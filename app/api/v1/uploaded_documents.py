# app/api/v1/uploaded_documents.py
import json
from uuid import UUID, uuid4
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio

from app.core.config import get_settings
from app.core.auth import validate_session
from app.core.database import get_db
from app.core.logging_config import logger
from app.models.uploaded_document import UploadedDocument
from app.models.workspace import Workspace
from app.models.chat_conversation import ChatConversation
from app.core.storage import upload_file_to_gcs, delete_file_from_gcs
from app.core.constants import GCS_UPLOADED_DOCUMENTS_BUCKET
from app.tasks.document.process_uploaded_document import process_uploaded_document, SUPPORTED_FILE_TYPES
from sqlalchemy import select
from celery.result import AsyncResult
from app.core.celery_app import celery_app
from app.utils.csv_data_processor import validate_csv, detect_data_types, check_data_quality,process_data
from app.services.weaviate import get_document_vector_service_async
from app.services.weaviate.document_service_async import DocumentVectorServiceAsync

settings = get_settings()

router = APIRouter(
    prefix="/uploaded-documents",
    tags=["uploaded-documents"],
)


@router.get("", response_model=List[Dict[str, Any]])
async def list_uploaded_documents(
        chat_conversation_id: UUID = Query(..., description="Chat conversation ID to fetch documents for"),
        current_user: dict = Depends(validate_session),
        db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """Return a list of uploaded documents for a specific chat conversation."""
    logger.info(f"Fetching uploaded documents for chat conversation ID: {chat_conversation_id}")

    # Query documents by chat conversation ID only
    query = select(UploadedDocument).where(UploadedDocument.chat_conversation_id == chat_conversation_id)
    
    result = await db.execute(query)
    uploaded_docs = result.scalars().all()

    return [
        {
            "id": str(doc.uploaded_document_id),
            "file_name": doc.file_name,
            "file_type": doc.file_type,
            # "file_size_bytes": doc.file_size_bytes,
            "file_path": doc.file_path,  # This is GCS URL
            "uploaded_at": doc.uploaded_at.isoformat(),
            "processing_status": doc.processing_status,
            "vector_status": doc.vector_status,
            "is_processed": doc.is_processed,
            # "meta_data": doc.meta_data
        }
        for doc in uploaded_docs
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def uploaded_document(
    files: List[UploadFile] = File(...),
    workspace_id: UUID = Form(...),
    chat_conversation_id: UUID = Form(...),
    current_user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    logger.info(f"Upload request for workspace: {workspace_id}, conversation: {chat_conversation_id}")

    all_allowed_extensions = list(SUPPORTED_FILE_TYPES.keys()) + ['csv']
    all_allowed_extensions = sorted(list(set(all_allowed_extensions)))
    supported_extensions_message = f"Supported file types are: {', '.join(all_allowed_extensions)}."

    # Preliminary checks
    for file_item in files:
        file_item_content_for_check = await file_item.read()
        if len(file_item_content_for_check) > 10 * 1024 * 1024:  # 10MB limit
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File {file_item.filename} exceeds 10MB limit."
            )
        await file_item.seek(0)

    if not await db.get(ChatConversation, chat_conversation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat conversation not found.")
    if not await db.get(Workspace, workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")

    processed_files_info = []
    failed_files_info = []

    user_id = current_user.get("id")
    tenant_id = current_user.get("userTenantId", str(user_id))

    for file_to_upload in files:
        doc_db_id = uuid4()
        original_filename = file_to_upload.filename
        file_ext = Path(original_filename).suffix.lower().lstrip('.')

        file_content_bytes = await file_to_upload.read()
        file_size = len(file_content_bytes)

        # db_meta_data will now only contain system-generated metadata
        db_meta_data: Dict[str, Any] = {
            "upload_timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        # Removed parsing of user-provided meta_data form field

        celery_task_id_str: Optional[str] = None
        doc_is_processed_in_db: bool = False
        doc_processing_status_in_db: str = "pending"
        doc_vector_status_in_db: str = "pending"
        gcs_url: Optional[str] = None
        gcs_object_key: Optional[str] = None

        if file_ext == 'csv':
            logger.info(f"Attempting CSV pre-processing for {original_filename}")
            validation_result = await asyncio.to_thread(
                validate_csv, file_content_bytes, original_filename
            )

            if not validation_result["success"]:
                error_msg = validation_result.get('error', 'CSV validation failed.')
                logger.error(f"CSV validation failed for {original_filename}: {error_msg}")
                failed_files_info.append({
                    "file_name": original_filename,
                    "error": error_msg,
                    "warnings": validation_result.get("warnings", [])
                })
                continue

            gcs_object_key = f"{user_id}/{workspace_id}/documents/{original_filename.replace(' ', '_')}"
            gcs_bucket = GCS_UPLOADED_DOCUMENTS_BUCKET
            gcs_url = await upload_file_to_gcs(
                content=file_content_bytes, file_path=gcs_object_key, bucket_name=gcs_bucket,
                content_type='text/csv'
            )
            logger.info(f"Validated CSV {original_filename} uploaded to GCS: {gcs_url}")
            db_meta_data["gcs_object_key"] = gcs_object_key
            db_meta_data["gcs_bucket_name"] = gcs_bucket
            db_meta_data["csv_validation_warnings"] = validation_result.get("warnings", [])

            df = validation_result["data"]
            if df is not None:
                basic_stats = await asyncio.to_thread(process_data, df)
                detected_types = await asyncio.to_thread(detect_data_types, df)
                quality_report = await asyncio.to_thread(check_data_quality, df)

                db_meta_data["csv_basic_stats"] = basic_stats.get("stats")
                db_meta_data["csv_detected_data_types"] = detected_types
                db_meta_data["csv_data_quality_report"] = quality_report

                doc_is_processed_in_db = True
                doc_processing_status_in_db = "metadata_extracted"
                doc_vector_status_in_db = "not_applicable"
            else:
                doc_is_processed_in_db = True
                doc_processing_status_in_db = "processing_error_post_validation"
                doc_vector_status_in_db = "not_applicable"
                db_meta_data["csv_error"] = "Internal error: No DataFrame post-validation."
            celery_task_id_str = None

        elif file_ext in SUPPORTED_FILE_TYPES:
            gcs_object_key = f"{user_id}/{workspace_id}/documents/{original_filename.replace(' ', '_')}"
            gcs_bucket = GCS_UPLOADED_DOCUMENTS_BUCKET
            gcs_url = await upload_file_to_gcs(
                content=file_content_bytes, file_path=gcs_object_key, bucket_name=gcs_bucket,
                content_type=file_to_upload.content_type or SUPPORTED_FILE_TYPES.get(file_ext, 'application/octet-stream')
            )
            logger.info(f"File {original_filename} uploaded to GCS: {gcs_url}")
            db_meta_data["gcs_object_key"] = gcs_object_key
            db_meta_data["gcs_bucket_name"] = gcs_bucket

            if not settings.LLAMA_PARSE_KEY:
                logger.warning(f"LLAMA_PARSE_KEY not set for RAG processing of {original_filename}.")

            task = process_uploaded_document.delay(
                document_id=str(doc_db_id), gcs_path=gcs_object_key, bucket_name=gcs_bucket,
                file_name=original_filename, file_ext=file_ext, file_size=file_size,
                workspace_id=str(workspace_id), user_id=user_id, user_tenant_id=tenant_id,
                chat_conversation_id=str(chat_conversation_id),
                meta_data={} # <--- Pass an empty dict or None if task handles it
            )
            celery_task_id_str = task.id
            db_meta_data["celery_task_id"] = celery_task_id_str
            doc_is_processed_in_db = False
            doc_processing_status_in_db = "queued"
            doc_vector_status_in_db = "pending"

        else:
            error_msg = f"Unsupported file type: '{file_ext}'. {supported_extensions_message}"
            logger.warning(f"File '{original_filename}' has an unsupported type: {file_ext}.")
            failed_files_info.append({
                "file_name": original_filename,
                "error": error_msg
            })
            continue

        if gcs_url:
            new_doc = UploadedDocument(
                uploaded_document_id=doc_db_id, user_id=user_id, workspace_id=workspace_id,
                chat_conversation_id=chat_conversation_id, file_name=original_filename,
                file_type=file_ext, file_size_bytes=file_size, file_path=gcs_url,
                is_processed=doc_is_processed_in_db, processing_status=doc_processing_status_in_db,
                vector_chunks_count=0, vector_status=doc_vector_status_in_db, meta_data=db_meta_data
            )
            db.add(new_doc)

            processed_files_info.append({
                "document_id": str(doc_db_id),
                "task_id": celery_task_id_str,
                "file_name": original_filename,
                "status": doc_processing_status_in_db,
                "gcs_url": gcs_url
            })

    if not processed_files_info and not failed_files_info and files:
        logger.warning("No files were categorized as processed or failed, despite input files being present.")

    if processed_files_info:
        try:
            await db.commit()
            logger.info(f"Successfully committed {len(processed_files_info)} document records to DB.")
        except Exception as e:
            await db.rollback()
            logger.error(f"Database commit failed after processing files: {e}", exc_info=True)
            error_detail = {
                "message": "Server error during database commit. Some files may be in an inconsistent state.",
                "processed_before_commit_failure": processed_files_info,
                "failed_validation_or_unsupported": failed_files_info,
                "error_details": str(e)
            }
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_detail)

    response_message = "File upload request processed."

    if failed_files_info and not processed_files_info:
        response_message = "All uploaded files failed processing (unsupported type or validation error)."
    elif failed_files_info:
        response_message = "Some files were processed successfully, while others failed (unsupported type or validation error)."
    elif not processed_files_info and not failed_files_info and not files:
        response_message = "No files provided in the request."
    elif not processed_files_info and not files:
        response_message = "No files were processed (no input files detected)."


    if files and not processed_files_info and failed_files_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": response_message,
                "uploaded_files": [],
                "failed_files": failed_files_info
            }
        )

    return {
        "message": response_message,
        "uploaded_files": processed_files_info,
        "failed_files": failed_files_info
    }


@router.delete(
    "/{document_id}",
    summary="Delete an uploaded document",
    description="Deletes the specified document from GCS, its vector embeddings from Weaviate (if applicable), and its record from the database.",
    status_code=status.HTTP_200_OK
)
async def delete_uploaded_document(
        document_id: str,
        current_user: dict = Depends(validate_session),
        db: AsyncSession = Depends(get_db),
        vector_service: DocumentVectorServiceAsync = Depends(get_document_vector_service_async)
) -> Dict[str, Any]:
    """
    Delete an uploaded document and its associated data from GCS and the vector database.
    """
    user_id_from_token = current_user.get("id")
    logger.info(f"User {user_id_from_token} attempting to delete document with DB ID: {document_id}")

    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        logger.warning(f"Invalid document_id format: {document_id}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document ID format.")

    # Fetch the document record from the database
    uploaded_doc = await db.get(UploadedDocument, doc_uuid)
    if not uploaded_doc:
        logger.warning(f"Document with ID {document_id} not found for deletion by user {user_id_from_token}.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    # Authorization check (ensure the user owns the document or has rights)
    # Example:
    # if uploaded_doc.user_id != user_id_from_token:
    #     logger.error(f"User {user_id_from_token} not authorized to delete document {document_id} owned by {uploaded_doc.user_id}.")
    #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this document.")

    # 1. Revoke Celery task if it's pending or running
    celery_task_id = uploaded_doc.meta_data.get("celery_task_id")
    if celery_task_id:
        task_result = AsyncResult(celery_task_id, app=celery_app)
        if task_result.state not in ['SUCCESS', 'FAILURE', 'REVOKED']:
            logger.info(f"Revoking Celery task {celery_task_id} for document {document_id}")
            try:
                task_result.revoke(terminate=True)  # Terminate if running
            except Exception as e_revoke:  # Catch potential errors during revoke
                logger.error(f"Error revoking Celery task {celery_task_id}: {e_revoke}", exc_info=True)

    # 2. Delete the file from GCS
    gcs_object_key_to_delete = uploaded_doc.meta_data.get("gcs_object_key")
    gcs_bucket_name_to_delete = uploaded_doc.meta_data.get("gcs_bucket_name")

    gcs_deleted_successfully = False
    if gcs_object_key_to_delete and gcs_bucket_name_to_delete:
        logger.info(f"Deleting from GCS: gs://{gcs_bucket_name_to_delete}/{gcs_object_key_to_delete}")
        try:
            if await delete_file_from_gcs(gcs_object_key_to_delete, gcs_bucket_name_to_delete):
                gcs_deleted_successfully = True
            else:  # delete_file_from_gcs might return False on logical failure (e.g. permissions)
                logger.warning(
                    f"delete_file_from_gcs returned false for gs://{gcs_bucket_name_to_delete}/{gcs_object_key_to_delete}.")
        except Exception as e_gcs:  # Catch exceptions during GCS call
            logger.error(
                f"Error deleting from GCS (gs://{gcs_bucket_name_to_delete}/{gcs_object_key_to_delete}): {e_gcs}",
                exc_info=True)
            # Decide if this is a critical failure. For now, we'll log and proceed.
    else:
        logger.warning(
            f"GCS object key or bucket name not found in metadata for document {document_id}. Cannot delete from GCS reliably.")
        # Fallback using file_path is fragile and removed for clarity. Rely on stored metadata.

    if not gcs_deleted_successfully and gcs_object_key_to_delete:  # Log if explicit GCS deletion path failed
        logger.warning(
            f"File gs://{gcs_bucket_name_to_delete}/{gcs_object_key_to_delete} might not have been deleted from GCS.")

    # 3. Delete vectors from the vector database (Weaviate) if applicable
    # Vectors are stored under 'Document' collection by DocumentVectorServiceAsync
    vectors_deleted_successfully = True  # Assume success if not applicable
    if uploaded_doc.vector_status not in ["not_applicable", "pending_no_task_csv", "failed", "validation_failed"]:
        logger.info(
            f"Attempting to delete vectors for document {document_id} (DB ID: {doc_uuid}, vector_status: {uploaded_doc.vector_status})")
        try:
            # tenant_id for Weaviate should be determined correctly (e.g., from user or workspace)
            tenant_id_for_vectors = current_user.get("userTenantId", str(uploaded_doc.workspace_id))
            delete_result = await vector_service.delete_vectors(
                tenant_id=tenant_id_for_vectors,
                doc_id=doc_uuid  # This is the UploadedDocument.uploaded_document_id
            )
            if delete_result.get("status") != "success":
                vectors_deleted_successfully = False
                logger.error(f"Failed to delete vectors for document {document_id}: {delete_result.get('message')}")
            else:
                logger.info(f"Vector deletion for document {document_id} reported: {delete_result.get('message')}")
        except Exception as e_vec:
            vectors_deleted_successfully = False
            logger.error(f"Error during vector deletion for document {document_id}: {e_vec}", exc_info=True)
    else:
        logger.info(
            f"Skipping vector deletion for document {document_id} (type: {uploaded_doc.file_type}, vector_status: {uploaded_doc.vector_status})")

    if not vectors_deleted_successfully and uploaded_doc.vector_status not in ["not_applicable", "pending_no_task_csv"]:
        logger.warning(f"Vectors for document {document_id} might not have been fully deleted from Weaviate.")

    # 4. Delete the document record from the database
    try:
        await db.delete(uploaded_doc)
        await db.commit()
        logger.info(f"Successfully deleted document record {document_id} from database.")
    except Exception as e_db:
        await db.rollback()
        logger.error(f"Failed to delete document record {document_id} from database: {e_db}", exc_info=True)
        # This is a critical failure, as other resources might be orphaned or deletion incomplete.
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to delete document record from database.")

    # Construct response message
    response_message = f"Document {document_id} deleted."
    details = []
    if not gcs_deleted_successfully and gcs_object_key_to_delete:
        details.append("GCS file deletion may have failed or was incomplete.")
    if not vectors_deleted_successfully and uploaded_doc.vector_status not in ["not_applicable", "pending_no_task_csv"]:
        details.append("Vector deletion may have failed or was incomplete.")

    if details:
        response_message += " Issues: " + " ".join(details)

    return {
        "status": "success" if gcs_deleted_successfully and vectors_deleted_successfully else "partial_success",
        "message": response_message,
        "document_id": document_id,
        "gcs_deleted": gcs_deleted_successfully,
        "vectors_deleted": vectors_deleted_successfully
    }
