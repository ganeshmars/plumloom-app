import json
import httpx
import requests
import time
from uuid import UUID
from typing import List, Dict, Any, Optional, Tuple

from celery import shared_task
from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session

from app.core.database import DATABASE_URL, SessionLocal
from app.core.redis import get_sync_redis
from app.core.constants import GCS_DOCUMENTS_BUCKET, GCS_UPLOADED_DOCUMENTS_BUCKET
from app.core.storage import delete_file_from_gcs_sync
from app.core.config import get_settings
from app.core.logging_config import logger

from app.models.workspace import Workspace
from app.models.document import Document
from app.models.uploaded_document import UploadedDocument
from app.models.chat_conversation import ChatConversation
from app.models.users import User

from app.services.weaviate.page_service_sync import PageVectorServiceSync
from app.services.weaviate.document_service_sync import DocumentVectorServiceSync
from app.services.weaviate.repository_sync import WeaviateRepositorySync
from app.core.weaviate_client import get_client as get_weaviate_sdk_client


settings = get_settings()

# Get a synchronous Redis client
redis_client = get_sync_redis()

def cleanup_document_resources_sync(
    document_id: str,
    tenant_id: str,
    user_id: str,
    db: Session,
    page_vector_service: PageVectorServiceSync,
    delete_from_tiptap: bool = True,
    check_children: bool = True,
    content_file_path: str = None,
    is_deleted_from_db: bool = True
) -> Tuple[bool, str]:
    """
    Synchronous utility function to clean up all resources associated with a document.
    
    Args:
        document_id: The ID of the document to clean up
        tenant_id: The tenant ID for vector operations
        user_id: The ID of the user who owned the document
        db: Database session
        page_vector_service: Initialized PageVectorServiceSync instance
        delete_from_tiptap: Whether to delete the document from Tiptap Cloud
    
    Returns:
        Tuple of (success, message)
    """
    logger.info(f"TASK DEBUG: Starting cleanup_document_resources_sync for document {document_id}")
    try:
        # 1. Delete document vectors from Weaviate
        try:
            logger.info(f"TASK DEBUG: Deleting vectors for document {document_id} with tenant {tenant_id}")
            page_vector_service.delete_vectors(tenant_id=tenant_id, doc_id=UUID(document_id))
            logger.info(f"TASK DEBUG: Successfully deleted vector data for document {document_id}")
        except Exception as e:
            logger.error(f"TASK DEBUG: Error deleting vectors for document {document_id}: {str(e)}")
            logger.error(f"TASK DEBUG: Exception type: {type(e).__name__}")
            # Continue with cleanup despite vector deletion error
        
        # 2. Handle document details for GCS file deletion
        document = None
        if not is_deleted_from_db:
            # Only query the database if the document might still exist
            logger.info(f"TASK DEBUG: Querying database for document {document_id}")
            try:
                document = db.query(Document).filter(Document.document_id == UUID(document_id)).first()
                if document:
                    logger.info(f"TASK DEBUG: Found document in database: {document.title}")
                else:
                    logger.info(f"TASK DEBUG: Document {document_id} not found in database")
            except Exception as e:
                logger.error(f"TASK DEBUG: Error querying document from database: {str(e)}")
        else:
            logger.info(f"TASK DEBUG: Document {document_id} already deleted from database, skipping query")
        
        if document:
            # 3. Delete document content file from GCS if it exists
            if document.content_file_path:
                try:
                    logger.info(f"TASK DEBUG: Deleting file from GCS: {document.content_file_path}")
                    success = delete_file_from_gcs_sync(
                        file_path=document.content_file_path,
                        bucket_name=GCS_DOCUMENTS_BUCKET
                    )
                    if success:
                        logger.info(f"TASK DEBUG: Successfully deleted file from GCS: {document.content_file_path}")
                except Exception as e:
                    logger.error(f"TASK DEBUG: Error deleting file from GCS: {str(e)}")
                    logger.error(f"TASK DEBUG: Exception type: {type(e).__name__}")
            else:
                logger.info(f"TASK DEBUG: No file path found for document {document_id}")
            
            # 4. Clean up Redis cache
            # try:
            #     logger.info(f"TASK DEBUG: Removing document {document_id} from Redis cache for user {user_id}")
            #     document_key = f"recent_items:{user_id}:document"
            #     combined_key = f"recent_items:{user_id}"
            #
            #     # Find and remove all items related to this document
            #     try:
            #         # For documents
            #         document_items = redis_client.zrange(document_key, 0, -1)
            #         for item_data in document_items:
            #             item = json.loads(item_data)
            #             if item.get('item_id') == document_id:
            #                 redis_client.zrem(document_key, item_data)
            #                 redis_client.zrem(combined_key, item_data)
            #
            #         logger.info(f"TASK DEBUG: Successfully removed document from Redis cache")
            #     except Exception as e:
            #         logger.error(f"TASK DEBUG: Error cleaning up Redis cache: {str(e)}")
            #         logger.error(f"TASK DEBUG: Exception type: {type(e).__name__}")
            # except Exception as e:
            #     logger.error(f"TASK DEBUG: Error with Redis operations: {e}")
        
        # 4. Check for child documents and clean them up first if requested
        if check_children:
            try:
                # Query for child documents
                logger.info(f"TASK DEBUG: Checking for child documents of {document_id}")
                child_docs = db.query(Document).filter(Document.parent_id == UUID(document_id)).all()
                
                if child_docs:
                    logger.info(f"TASK DEBUG: Found {len(child_docs)} child documents to clean up")
                    for child_doc in child_docs:
                        child_doc_id = str(child_doc.document_id)
                        logger.info(f"TASK DEBUG: Cleaning up child document {child_doc_id}")
                        
                        # Recursively clean up each child document
                        # Set check_children=True to handle nested hierarchies
                        child_success, child_message = cleanup_document_resources_sync(
                            document_id=child_doc_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            db=db,
                            page_vector_service=page_vector_service,
                            delete_from_tiptap=delete_from_tiptap,
                            check_children=True  # Continue checking for nested children
                        )
                        
                        if not child_success:
                            logger.warning(f"TASK DEBUG: Issue cleaning up child document {child_doc_id}: {child_message}")
                            # Continue with other children even if one fails
                    
                    logger.info(f"TASK DEBUG: Completed cleanup of all child documents for {document_id}")
                else:
                    logger.info(f"TASK DEBUG: No child documents found for {document_id}")
                    
            except Exception as e:
                logger.error(f"TASK DEBUG: Error checking for child documents: {str(e)}")
                # Continue with parent document cleanup despite error with children
        
        # 5. Delete the document from Tiptap Cloud if requested
        if delete_from_tiptap:
            try:
                # Construct the Tiptap Cloud API URL
                tiptap_app_id = settings.TIPTAP_CLOUD_APP_ID
                tiptap_api_key = settings.TIPTAP_CLOUD_API_SECRET_KEY
                
                # Validate Tiptap Cloud configuration
                if not tiptap_app_id or not tiptap_api_key:
                    logger.warning(f"Tiptap Cloud configuration missing for document {document_id}. Skipping Tiptap deletion.")
                else:
                    # Call the Tiptap Cloud API to delete the document
                    tiptap_url = f"https://{tiptap_app_id}.collab.tiptap.cloud/api/documents/document_{document_id}"
                    headers = {"Authorization": tiptap_api_key}
                    
                    logger.info(f"TASK DEBUG: Attempting to delete document from Tiptap Cloud: {tiptap_url}")
                    
                    # Implement retry with exponential backoff
                    max_retries = 3
                    retry_delay = 0.5  # Start with 500ms delay
                    success = False
                    
                    for attempt in range(max_retries):
                        try:
                            # Add a delay before making the API call (increases with each retry)
                            if attempt > 0:
                                logger.info(f"Retry attempt {attempt} for Tiptap Cloud deletion of document {document_id}, waiting {retry_delay}s")
                                time.sleep(retry_delay)
                                # Exponential backoff: double the delay for next attempt
                                retry_delay *= 2
                            
                            # Set a reasonable timeout to avoid hanging
                            response = requests.delete(tiptap_url, headers=headers, timeout=10)
                            
                            if response.status_code == 204:
                                logger.info(f"Successfully deleted document {document_id} from Tiptap Cloud")
                                success = True
                                break  # Exit retry loop on success
                            elif response.status_code == 429:  # Rate limit exceeded
                                logger.warning(f"Rate limit exceeded for Tiptap Cloud API: {response.status_code}, {response.text}")
                                # Continue to next retry attempt
                            else:
                                logger.warning(f"Failed to delete document {document_id} from Tiptap Cloud: {response.status_code}, {response.text}")
                                # For non-rate-limit errors, we'll still retry but log the specific error
                            
                        except requests.exceptions.ConnectionError as conn_error:
                            logger.error(f"Connection error when deleting document {document_id} from Tiptap Cloud: {str(conn_error)}")
                        except requests.exceptions.Timeout:
                            logger.error(f"Timeout when deleting document {document_id} from Tiptap Cloud")
                        except requests.exceptions.RequestException as req_error:
                            logger.error(f"Request error when deleting document {document_id} from Tiptap Cloud: {str(req_error)}")
                    
                    if not success:
                        logger.error(f"Failed to delete document {document_id} from Tiptap Cloud after {max_retries} attempts")
            
            except Exception as tiptap_error:
                # Log the error but continue with cleanup
                logger.error(f"Error in Tiptap Cloud deletion process for document {document_id}: {str(tiptap_error)}")
                logger.error(f"Error type: {type(tiptap_error).__name__}")
                # Continue with the rest of the cleanup despite Tiptap error
        
        logger.info(f"TASK DEBUG: Document cleanup completed successfully for {document_id}")
        return True, f"Document {document_id} resources cleaned up successfully"
        
    except Exception as e:
        error_message = f"Error cleaning up document resources: {str(e)}"
        logger.error(f"TASK DEBUG: Unhandled exception in cleanup_document_resources_sync: {str(e)}")
        logger.error(f"TASK DEBUG: Exception type: {type(e).__name__}")
        return False, error_message


@shared_task(
    name='app.tasks.workspace.delete_workspace_resources',
    queue='operations',
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=1800,  # 30-minutes soft timeout
    time_limit=1860,       # 31-minutes hard timeout
    acks_late=True         # Only acknowledge after a task completes
)
def delete_workspace_resources(
    workspace_id: str,
    user_id: str,
    tenant_id: str,
    document_ids: List[str],
    uploaded_document_ids: List[str]
) -> Dict[str, Any]:
    """
    Background task to clean up all resources associated with a deleted workspace.
    
    Args:
        workspace_id: The ID of the deleted workspace
        user_id: The ID of the user who owned the workspace
        tenant_id: The tenant ID for vector operations
        document_ids: List of document IDs that were in the workspace
        uploaded_document_ids: List of uploaded document IDs that were in the workspace
    
    Returns:
        Dict with status information about the cleanup operation
    """
    # Debug log at the very beginning of the task
    logger.info(f"TASK DEBUG: delete_workspace_resources task started with args: workspace_id={workspace_id}, user_id={user_id}, tenant_id={tenant_id}")
    logger.info(f"TASK DEBUG: document_ids count: {len(document_ids)}, uploaded_document_ids count: {len(uploaded_document_ids)}")
    
    try:
        # Test logging to verify task is running
        logger.info("TASK DEBUG: Task function is executing - initial checkpoint")
    except Exception as e:
        logger.error(f"TASK DEBUG: Error in initial logging: {str(e)}")
        # Continue with the task even if logging fails
    logger.info(f"Starting background cleanup for workspace {workspace_id}")
    
    try:
        # Initialize database session
        logger.info("TASK DEBUG: Creating database session")
        db_session = SessionLocal()
        logger.info("TASK DEBUG: Database session created successfully")
    except Exception as e:
        logger.error(f"TASK DEBUG: Error creating database session: {str(e)}")
        return {"status": "error", "message": f"Failed to create database session: {str(e)}"}
    
    try:
        # Initialize Weaviate repositories and services
        logger.info("TASK DEBUG: Initializing Weaviate client and services")
        weaviate_repo_sync = WeaviateRepositorySync(client=get_weaviate_sdk_client())
        page_vector_service = PageVectorServiceSync(repository=weaviate_repo_sync)
        document_vector_service = DocumentVectorServiceSync(repository=weaviate_repo_sync)
        logger.info("TASK DEBUG: Weaviate services initialized successfully")
        
        # 1. Clean up document-related data
        logger.info(f"TASK DEBUG: Starting cleanup of {len(document_ids)} documents")
        doc_cleanup_results = []
        
        for doc_id in document_ids:
            try:
                # Use the synchronous cleanup function for each document
                logger.info(f"TASK DEBUG: Cleaning up document {doc_id}")
                success, message = cleanup_document_resources_sync(
                    document_id=doc_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    db=db_session,
                    page_vector_service=page_vector_service,
                    delete_from_tiptap=True  # Set to True if you want to delete from Tiptap, False otherwise
                )
                
                doc_result = {"document_id": doc_id, "success": success, "message": message}
                doc_cleanup_results.append(doc_result)
                
                if success:
                    logger.info(f"TASK DEBUG: Successfully cleaned up document {doc_id} resources")
                else:
                    logger.warning(f"TASK DEBUG: Issue cleaning up document {doc_id}: {message}")
            except Exception as e:
                logger.error(f"TASK DEBUG: Error cleaning up document {doc_id}: {str(e)}")
                doc_cleanup_results.append({"document_id": doc_id, "success": False, "message": str(e)})
        
        # 2. Clean up uploaded documents
        logger.info(f"TASK DEBUG: Starting cleanup of {len(uploaded_document_ids)} uploaded documents")
        uploaded_doc_cleanup_results = []
        
        for upload_id in uploaded_document_ids:
            try:
                logger.info(f"TASK DEBUG: Cleaning up uploaded document {upload_id}")
                # Get uploaded document details if needed
                uploaded_doc = db_session.query(UploadedDocument).filter(UploadedDocument.uploaded_document_id == UUID(upload_id)).first()
                
                upload_result = {"uploaded_document_id": upload_id, "success": False, "message": ""}
                
                if uploaded_doc:
                    logger.info(f"TASK DEBUG: Found uploaded document in database: {uploaded_doc.filename}")
                    # Delete vectors for the uploaded document from Weaviate
                    if uploaded_doc.vector_status in ["completed", "processing"]:
                        try:
                            logger.info(f"TASK DEBUG: Deleting vectors for uploaded document {upload_id}")
                            delete_result = document_vector_service.delete_vectors(
                                tenant_id=tenant_id, 
                                doc_id=uploaded_doc.uploaded_document_id
                            )
                            logger.info(f"TASK DEBUG: Successfully deleted vector data for uploaded document {upload_id}")
                            upload_result["vectors_deleted"] = True
                        except Exception as e:
                            logger.error(f"TASK DEBUG: Error deleting vectors for uploaded document {upload_id}: {str(e)}")
                            upload_result["vectors_deleted"] = False
                            upload_result["vector_error"] = str(e)
                    
                    # Delete the file from Google Cloud Storage
                    if uploaded_doc.file_path:
                        logger.info(f"TASK DEBUG: Processing file path for deletion: {uploaded_doc.file_path}")
                        # Extract the file path from the full URL
                        file_path = uploaded_doc.file_path
                        
                        # If it's a full URL, extract just the path part after the bucket name
                        if file_path.startswith('https://storage.googleapis.com/'):
                            bucket_prefix = f'https://storage.googleapis.com/{GCS_UPLOADED_DOCUMENTS_BUCKET}/'
                            if file_path.startswith(bucket_prefix):
                                file_path = file_path[len(bucket_prefix):]
                                logger.info(f"TASK DEBUG: Extracted file path from URL: {file_path}")
                        
                        try:
                            logger.info(f"TASK DEBUG: Deleting file from GCS: {file_path}")
                            success = delete_file_from_gcs_sync(
                                file_path=file_path,
                                bucket_name=GCS_UPLOADED_DOCUMENTS_BUCKET
                            )
                            if success:
                                logger.info(f"TASK DEBUG: Successfully deleted file from GCS: {file_path}")
                                upload_result["file_deleted"] = True
                            else:
                                logger.warning(f"TASK DEBUG: Failed to delete file from GCS: {file_path}")
                                upload_result["file_deleted"] = False
                        except Exception as e:
                            logger.error(f"TASK DEBUG: Error deleting file from storage: {str(e)}")
                            upload_result["file_deleted"] = False
                            upload_result["file_error"] = str(e)
                    else:
                        logger.info(f"TASK DEBUG: No file path found for uploaded document {upload_id}")
                        
                    # Mark this upload as successfully processed
                    upload_result["success"] = True
                    upload_result["message"] = "Uploaded document resources cleaned up successfully"
                else:
                    logger.info(f"TASK DEBUG: Uploaded document {upload_id} not found in database")
                    upload_result["message"] = "Uploaded document not found in database"
                    
                uploaded_doc_cleanup_results.append(upload_result)
            except Exception as e:
                logger.error(f"TASK DEBUG: Error cleaning up uploaded document {upload_id}: {str(e)}")
                uploaded_doc_cleanup_results.append({
                    "uploaded_document_id": upload_id, 
                    "success": False, 
                    "message": f"Error: {str(e)}"
                })
        
        # 3. Clean up Redis cache entries
        logger.info(f"TASK DEBUG: Starting Redis cache cleanup for workspace {workspace_id}")
        redis_cleanup_result = {"success": False, "documents_removed": 0, "chats_removed": 0}
        
        # try:
        #     # Get all items for this user
        #     document_key = f"recent_items:{user_id}:document"
        #     chat_key = f"recent_items:{user_id}:chat"
        #     combined_key = f"recent_items:{user_id}"
        #
        #     logger.info(f"TASK DEBUG: Redis keys: document_key={document_key}, chat_key={chat_key}, combined_key={combined_key}")
        #
        #     # Find and remove all items related to this workspace
        #     try:
        #         # For documents
        #         logger.info("TASK DEBUG: Getting document items from Redis")
        #         document_items = redis_client.zrange(document_key, 0, -1)
        #         logger.info(f"TASK DEBUG: Found {len(document_items)} document items in Redis")
        #
        #         docs_removed = 0
        #         for item_data in document_items:
        #             try:
        #                 item = json.loads(item_data)
        #                 if item.get('workspace_id') == workspace_id:
        #                     logger.info(f"TASK DEBUG: Removing document item {item.get('item_id')} from Redis")
        #                     redis_client.zrem(document_key, item_data)
        #                     redis_client.zrem(combined_key, item_data)
        #                     docs_removed += 1
        #             except Exception as e:
        #                 logger.error(f"TASK DEBUG: Error processing document item in Redis: {str(e)}")
        #
        #         logger.info(f"TASK DEBUG: Removed {docs_removed} document items from Redis")
        #         redis_cleanup_result["documents_removed"] = docs_removed
        #
        #         # For chats
        #         logger.info("TASK DEBUG: Getting chat items from Redis")
        #         chat_items = redis_client.zrange(chat_key, 0, -1)
        #         logger.info(f"TASK DEBUG: Found {len(chat_items)} chat items in Redis")
        #
        #         chats_removed = 0
        #         for item_data in chat_items:
        #             try:
        #                 item = json.loads(item_data)
        #                 if item.get('workspace_id') == workspace_id:
        #                     logger.info(f"TASK DEBUG: Removing chat item {item.get('item_id')} from Redis")
        #                     redis_client.zrem(chat_key, item_data)
        #                     redis_client.zrem(combined_key, item_data)
        #                     chats_removed += 1
        #             except Exception as e:
        #                 logger.error(f"TASK DEBUG: Error processing chat item in Redis: {str(e)}")
        #
        #         logger.info(f"TASK DEBUG: Removed {chats_removed} chat items from Redis")
        #         redis_cleanup_result["chats_removed"] = chats_removed
        #         redis_cleanup_result["success"] = True
        #
        #         logger.info(f"TASK DEBUG: Successfully cleaned up Redis cache entries for workspace {workspace_id}")
        #     except Exception as e:
        #         logger.error(f"TASK DEBUG: Error removing items from Redis: {str(e)}")
        #         logger.error(f"TASK DEBUG: Exception type: {type(e).__name__}")
        #         redis_cleanup_result["error"] = str(e)
        # except Exception as e:
        #     logger.error(f"TASK DEBUG: Error with Redis operations: {str(e)}")
        #     logger.error(f"TASK DEBUG: Exception type: {type(e).__name__}")
        #     redis_cleanup_result["error"] = str(e)
        # 5. Return success status
        logger.info(f"TASK DEBUG: All cleanup operations completed for workspace {workspace_id}")
        result = {
            "status": "success",
            "message": f"Workspace {workspace_id} resources cleaned up successfully",
            "documents_cleaned": doc_cleanup_results,
            "uploaded_documents_cleaned": uploaded_doc_cleanup_results,
            "redis_cleanup": redis_cleanup_result
        }
        logger.info(f"TASK DEBUG: Task delete_workspace_resources completed with result: {result}")
        return result
        
    except Exception as e:
        error_msg = f"Error in background cleanup for workspace {workspace_id}: {str(e)}"
        logger.error(f"TASK DEBUG: Unhandled exception in delete_workspace_resources: {str(e)}")
        logger.error(f"TASK DEBUG: Exception type: {type(e).__name__}")
        result = {
            "status": "error",
            "message": error_msg,
            "documents_cleaned": doc_cleanup_results if 'doc_cleanup_results' in locals() else [],
            "uploaded_documents_cleaned": uploaded_doc_cleanup_results if 'uploaded_doc_cleanup_results' in locals() else []
        }
        logger.info(f"TASK DEBUG: Task delete_workspace_resources completed with error result: {result}")
        return result
    
    finally:
        # Close the database session
        try:
            logger.info("TASK DEBUG: Closing database session")
            db_session.close()
            logger.info("TASK DEBUG: Database session closed successfully")
        except Exception as e:
            logger.error(f"TASK DEBUG: Error closing database session: {str(e)}")
            # Don't re-raise the exception, just log it


@shared_task(
    name='app.tasks.document.delete_document_resources',
    queue='operations',
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=600,  # 10-minutes soft timeout
    time_limit=660,       # 11-minutes hard timeout
    acks_late=True         # Only acknowledge after a task completes
)
def delete_document_resources(
    document_id: str,
    user_id: str,
    tenant_id: str,
    deleted_child_ids: List[str] = None,
) -> Dict[str, Any]:
    """
    Background task to clean up all resources associated with a deleted document.
    
    Args:
        document_id: The ID of the deleted document
        user_id: The ID of the user who owned the document
        tenant_id: The tenant ID for vector operations
        deleted_child_ids: Optional list of child document IDs that were already deleted from the database
    
    Returns:
        Dict with status information about the cleanup operation
    """
    # Debug log at the very beginning of the task
    logger.info(f"TASK DEBUG: delete_document_resources task started with args: document_id={document_id}, user_id={user_id}, tenant_id={tenant_id}")
    
    try:
        # Test logging to verify task is running
        logger.info("TASK DEBUG: Task function is executing - initial checkpoint")
    except Exception as e:
        logger.error(f"TASK DEBUG: Error in initial logging: {str(e)}")
        # Continue with the task even if logging fails
        
    logger.info(f"Starting background cleanup for document {document_id}")
    
    try:
        # Initialize database session
        logger.info("TASK DEBUG: Creating database session")
        db_session = SessionLocal()
        logger.info("TASK DEBUG: Database session created successfully")
    except Exception as e:
        logger.error(f"TASK DEBUG: Error creating database session: {str(e)}")
        return {"status": "error", "message": f"Failed to create database session: {str(e)}"}
    
    try:
        # Initialize Weaviate repositories and services
        logger.info("TASK DEBUG: Initializing Weaviate client and services")
        weaviate_repo_sync = WeaviateRepositorySync(client=get_weaviate_sdk_client())
        page_vector_service = PageVectorServiceSync(repository=weaviate_repo_sync)
        logger.info("TASK DEBUG: Weaviate services initialized successfully")
        
        # Use the synchronous cleanup function
        logger.info("TASK DEBUG: Calling cleanup_document_resources_sync function")
        
        # First, clean up any child documents that were already deleted from the database
        child_cleanup_results = []
        if deleted_child_ids:
            logger.info(f"TASK DEBUG: Processing {len(deleted_child_ids)} already deleted child documents")
            for child_id in deleted_child_ids:
                logger.info(f"TASK DEBUG: Cleaning up resources for already deleted child document {child_id}")
                child_success, child_message = cleanup_document_resources_sync(
                    document_id=child_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    db=db_session,
                    page_vector_service=page_vector_service,
                    check_children=False,  # Don't check for children since we're handling them explicitly
                    is_deleted_from_db=True  # Document is already deleted from the database
                )
                child_cleanup_results.append({"id": child_id, "success": child_success, "message": child_message})
                logger.info(f"TASK DEBUG: Child document {child_id} cleanup result: {child_success}")
        
        # Now clean up the parent document
        logger.info(f"TASK DEBUG: Cleaning up parent document {document_id}")
        success, message = cleanup_document_resources_sync(
            document_id=document_id,
            tenant_id=tenant_id,
            user_id=user_id,
            db=db_session,
            page_vector_service=page_vector_service,
            check_children=False,  # Don't check for children since we've already handled them
            is_deleted_from_db=True  # Document is already deleted from the database
        )
        
        if success:
            logger.info(f"TASK DEBUG: Successfully cleaned up resources for document {document_id}")
            result = {
                "status": "success", 
                "message": message,
                "child_documents_cleaned": child_cleanup_results if deleted_child_ids else []
            }
            logger.info(f"TASK DEBUG: Task delete_document_resources completed with result: {result}")
            return result
        else:
            logger.error(f"TASK DEBUG: Failed to clean up resources for document {document_id}: {message}")
            result = {
                "status": "error", 
                "message": message,
                "child_documents_cleaned": child_cleanup_results if deleted_child_ids else []
            }
            logger.info(f"TASK DEBUG: Task delete_document_resources completed with error result: {result}")
            return result
    
    except Exception as e:
        error_msg = f"Error in background cleanup for document {document_id}: {str(e)}"
        logger.error(f"TASK DEBUG: Unhandled exception in delete_document_resources: {str(e)}")
        logger.error(f"TASK DEBUG: Exception type: {type(e).__name__}")
        result = {"status": "error", "message": error_msg}
        logger.info(f"TASK DEBUG: Task delete_document_resources completed with error result: {result}")
        return result
    
    finally:
        # Close the database session
        try:
            logger.info("TASK DEBUG: Closing database session")
            db_session.close()
            logger.info("TASK DEBUG: Database session closed successfully")
        except Exception as e:
            logger.error(f"TASK DEBUG: Error closing database session: {str(e)}")
            # Don't re-raise the exception, just log it