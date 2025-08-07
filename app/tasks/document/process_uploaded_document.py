import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

import httpx
from celery import shared_task
from google.cloud import storage
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging_config import logger
from app.models.uploaded_document import UploadedDocument
from app.services.vector_service_v3 import VectorService
from llama_cloud_services import LlamaParse
from llama_cloud_services.parse.utils import ResultType
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.schema import Document

settings = get_settings()

# Supported file extensions and their MIME types
SUPPORTED_FILE_TYPES = {
    'pdf': 'application/pdf',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'txt': 'text/plain',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'xls': 'application/vnd.ms-excel',
    'md': 'text/markdown',
}


@shared_task(
    name='app.tasks.document.process_uploaded_document',
    queue='doc_processing',
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=600,  # 10-minutes soft timeout
    time_limit=660,       # 11-minutes hard timeout
    acks_late=True        # Only acknowledge after a task completes
)
def process_uploaded_document(
    document_id: str,
    gcs_path: str,
    bucket_name: str,
    file_name: str,
    file_ext: str,
    file_size: int,
    workspace_id: str,
    user_id: str,
    user_tenant_id: str,
    chat_conversation_id: str,
    meta_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Process an uploaded document (PDF, DOCX, TXT, CSV, etc.) and store it in the vector database.

    Args:
        document_id (str): Document ID.
        gcs_path (str): Path to the file in GCS.
        bucket_name (str): GCS bucket name.
        file_name (str): Name of the file.
        file_ext (str): File extension (without the dot).
        file_size (int): Size of the file in bytes.
        workspace_id (str): Workspace ID.
        user_id (str): User ID.
        user_tenant_id (str): User tenant ID.
        chat_conversation_id (str): Chat conversation ID.
        meta_data (Optional[Dict[str, Any]]): Optional metadata for the file.

    Returns:
        Dict[str, Any]: Dictionary with processing results.
    """
    logger.info(f"Processing document: {file_name}")

    try:
        doc_id = UUID(document_id)
        temp_file_path = ""
        chunks = []
        try:
            # Create a temporary file and download the file from GCS
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as temp_file:
                storage_client = storage.Client()
                bucket = storage_client.bucket(bucket_name)
                blob = bucket.blob(gcs_path)
                blob.download_to_filename(temp_file.name)
                temp_file_path = temp_file.name
                
                logger.info(f"Downloaded file from GCS: {gcs_path} to {temp_file_path}")

            # Step 2: Extract a text and chunk document using LlamaParse and LlamaIndex
            # Process structured documents with LlamaParse (PDF, DOCX, Excel files)
            if file_ext in ['pdf', 'docx', 'xlsx', 'xls']:
                logger.info(f"Extracting text from {file_ext} document using LlamaParse")
                
                # Initialize LlamaParse client
                parser = LlamaParse(
                    api_key=settings.LLAMA_PARSE_KEY,
                    result_type=ResultType.MD,
                    num_workers=4,
                    verbose=True,
                    language="en"
                )
                try:
                    # Parse the document using load_data to get LlamaIndex Document objects
                    llama_documents = parser.load_data(temp_file_path)
                    logger.info(f"Successfully parsed document: {file_name}")
                    logger.info(f"Returned {len(llama_documents)} LlamaIndex Document object(s)")
                    
                    if not llama_documents:
                        raise ValueError("Parsing did not return any documents")
                    
                    # Use MarkdownNodeParser to chunk the document based on structure
                    node_parser = MarkdownNodeParser(
                        include_metadata=True,
                        include_prev_next_rel=True,
                    )
                    
                    # Generate nodes (chunks) from the parsed documents
                    nodes = node_parser.get_nodes_from_documents(llama_documents)
                    logger.info(f"Generated {len(nodes)} nodes (chunks) from the parsed document")
                    
                    # Extract text content from nodes
                    chunks = [node.get_content(metadata_mode='none') for node in nodes]
                    
                    # Log sample chunks for debugging
                    if chunks and len(chunks) > 0:
                        logger.info(f"Sample chunk: {chunks[0][:200]}...")
                    
                except Exception as e:
                    logger.error(f"Error during LlamaParse processing: {str(e)}")
                    raise
                    
            elif file_ext in ['txt', 'md']:
                # For text files, read content and use simple chunking
                with open(temp_file_path, 'r', encoding='utf-8') as f:
                    text_content = f.read()

                doc = Document(text=text_content)
                node_parser = MarkdownNodeParser()
                nodes = node_parser.get_nodes_from_documents([doc])
                chunks = [node.get_content(metadata_mode='none') for node in nodes]
                logger.info(f"Split text file into {len(chunks)} chunks")
            
            # Log the number of chunks
            logger.info(f"Total chunks generated: {len(chunks)}")
            
            # Update the existing document status from queued to processing
            with SessionLocal() as db:
                query = select(UploadedDocument).where(UploadedDocument.uploaded_document_id == doc_id)
                result_obj = db.execute(query)
                uploaded_doc = result_obj.scalar_one_or_none()
                
                if uploaded_doc:
                    uploaded_doc.processing_status = 'processing'
                    uploaded_doc.vector_status = 'processing'
                    # Update metadata with processing information
                    if uploaded_doc.meta_data:
                        uploaded_doc.meta_data.update({
                            "processing_started_at": datetime.now(timezone.utc).isoformat()
                        })
                    db.commit()
                    logger.info(f"Updated UploadedDocument processing status for ID: {doc_id}")
                else:
                    logger.error(f"UploadedDocument with ID {doc_id} not found in database")
                    return {
                        "status": "error",
                        "message": f"Document with ID {doc_id} not found in database"
                    }
            
            # Process chunks directly
            if chunks:
                
                # Process vectors directly
                logger.info(f"Processing vectors for document {doc_id} with {len(chunks)} chunks")
                
                # Initialize vector service
                vector_service = VectorService()
                
                # Store chunks in a vector database
                result = vector_service.create_vectors(
                    tenant_id=user_tenant_id or str(workspace_id),
                    doc_id=doc_id,
                    workspace_id=UUID(workspace_id),
                    title=file_name,
                    chunks=chunks,
                    chat_conversation_id=chat_conversation_id
                )
                
                # Update document status in database
                with SessionLocal() as db:
                    # Find the document again
                    query = select(UploadedDocument).where(UploadedDocument.uploaded_document_id == doc_id)
                    result_obj = db.execute(query)
                    uploaded_doc = result_obj.scalar_one_or_none()
                    
                    if uploaded_doc:
                        # Update document status based on vector processing result
                        if result.get("status") == "success":
                            uploaded_doc.vector_chunks_count = len(chunks)
                            uploaded_doc.vector_status = 'completed'
                            uploaded_doc.is_processed = True
                            uploaded_doc.processing_status = 'completed'
                        else:
                            uploaded_doc.vector_status = 'failed'
                            uploaded_doc.error_message = result.get("message")
                        
                        db.commit()
                        logger.info(f"Updated document status for {doc_id}: {uploaded_doc.processing_status}")
                    else:
                        logger.error(f"Document {doc_id} not found in database")
                
                logger.info(f"Vector processing result for document {doc_id}: {result}")
                
        finally:
            # Clean up the temporary file
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

        return {
            "status": "success",
            "message": "Document processed successfully",
            "document_id": document_id,  # Use the string document_id that was passed in
            "file_name": file_name,
            "file_type": file_ext,
            "file_size": file_size,
            "chunks_count": len(chunks) if chunks else 0,
            "gcs_path": gcs_path,
            "is_processed": True,
            "vector_result": result if 'result' in locals() else None,
            "vector_status": "completed" if chunks else "skipped",
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error while parsing document: {str(e)}")
        return {
            "status": "error",
            "message": f"Error from parsing service: {e.response.text}",
            "error_type": "http_status_error"
        }
    except httpx.RequestError as e:
        logger.error(f"Error connecting to parsing service: {str(e)}")
        return {
            "status": "error",
            "message": f"Could not connect to parsing service: {str(e)}",
            "error_type": "request_error"
        }
    except ValueError as e:
        logger.warning(f"Invalid input for document upload: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "error_type": "value_error"
        }
    except Exception as e:
        logger.error(f"Error uploading document: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "error_type": "general_error"
        }
