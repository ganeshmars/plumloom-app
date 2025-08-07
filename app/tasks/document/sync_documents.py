# app/tasks/document/sync_documents.py
from datetime import datetime, timedelta
import time

from celery import shared_task
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import joinedload
import requests
from typing import Dict, List, Optional, Any, Callable, Union
from app.models.document import Document
from app.core.logging_config import logger
from app.core.database import SessionLocal
from app.core.config import get_settings
from app.services.sync_document_service import SyncDocumentService
from app.services.weaviate.page_service_sync import PageVectorServiceSync
from app.services.weaviate.repository_sync import WeaviateRepositorySync
from app.core.redis import get_sync_redis

settings = get_settings()

# Lazy initialization of Weaviate services to prevent startup failures
_repository = None
_page_vector_service = None

def get_vector_service(max_retries=3, retry_delay=2):
    """Get the vector service with lazy initialization and retry mechanism
    
    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        retry_delay: Delay between retries in seconds (default: 2)
    
    Returns:
        PageVectorServiceSync: The initialized vector service
        
    Raises:
        RuntimeError: If unable to initialize the vector service after all retries
    """
    global _repository, _page_vector_service
    
    if _page_vector_service is not None:
        return _page_vector_service
    
    # Implement retry logic
    retry_count = 0
    last_error = None
    
    while retry_count < max_retries:
        try:
            _repository = WeaviateRepositorySync()
            _page_vector_service = PageVectorServiceSync(repository=_repository)
            logger.info("Successfully initialized Weaviate vector service")
            return _page_vector_service
        except Exception as e:
            last_error = e
            retry_count += 1
            logger.warning(f"Weaviate connection attempt {retry_count}/{max_retries} failed: {str(e)}")
            
            if retry_count < max_retries:
                logger.info(f"Retrying Weaviate connection in {retry_delay} seconds...")
                time.sleep(retry_delay)
                # Increase delay for next retry (exponential backoff)
                retry_delay *= 1.5
    
    # If we get here, all retries have failed
    logger.error(f"Failed to initialize Weaviate vector service after {max_retries} attempts: {str(last_error)}")
    raise RuntimeError(f"Unable to connect to Weaviate after {max_retries} attempts: {str(last_error)}")


def get_tiptap_base_url() -> str:
    """Get the base URL for TipTap Cloud API"""
    return f"https://{settings.TIPTAP_CLOUD_APP_ID}.collab.tiptap.cloud/api"


def get_tiptap_headers() -> Dict[str, str]:
    """Get the headers required for TipTap Cloud API authentication"""
    return {
        "Content-Type": "application/json",
        "Authorization": f"{settings.TIPTAP_CLOUD_API_SECRET_KEY}"
    }


def fetch_document_list(take: int = 100, skip: int = 0) -> Dict[str, Any]:
    """Fetch a list of documents from TipTap Cloud API
    
    Args:
        take: The number of documents to fetch (default: 100)
        skip: The number of documents to skip (default: 0)
        
    Returns:
        Dictionary containing the documents list and pagination info
        
    Raises:
        Exception: If the API request fails
    """
    url = f"{get_tiptap_base_url()}/documents"
    params = {
        "take": take,
        "skip": skip
    }
    
    try:
        response = requests.get(url, headers=get_tiptap_headers(), params=params)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch document list from TipTap: {str(e)}", exc_info=True)
        raise


def fetch_all_documents(max_retries: int = 3, retry_backoff: bool = True) -> List[Dict[str, Any]]:
    """Fetch all documents from TipTap Cloud API by handling pagination automatically
    while respecting rate limits (100 requests per 5 seconds, burst up to 200)
    
    Args:
        max_retries: Maximum number of retries for transient errors (default: 3)
        retry_backoff: Whether to use exponential backoff for retries (default: True)
    
    Returns:
        List of dictionaries containing all document data
        
    Raises:
        Exception: If the API request fails after all retries
    """
    all_documents = []
    batch_size = 100
    skip = 0
    
    # Rate limiting parameters
    # TipTap limits: 100 requests per 5 seconds, burst up to 200
    requests_count = 0
    rate_limit_window_start = time.time()
    max_requests_per_window = 80  # Using 80 instead of 100 as a safety margin
    rate_limit_window = 5  # 5 seconds
    
    logger.info("Starting to fetch all documents from TipTap Cloud API")
    
    while True:
        try:
            # Check if we need to pause for rate limiting
            current_time = time.time()
            elapsed_time = current_time - rate_limit_window_start
            
            if elapsed_time >= rate_limit_window:
                # Reset the window if 5 seconds have passed
                requests_count = 0
                rate_limit_window_start = current_time
            elif requests_count >= max_requests_per_window:
                # Sleep for the remaining time in the window
                sleep_time = rate_limit_window - elapsed_time
                logger.info(f"Rate limit approaching: Pausing for {sleep_time:.2f} seconds")
                time.sleep(sleep_time)
                requests_count = 0
                rate_limit_window_start = time.time()
            
            logger.info(f"Fetching documents batch: skip={skip}, take={batch_size}")
            response = fetch_document_list(take=batch_size, skip=skip)
            requests_count += 1
            
            # Based on the actual API response format, the documents are directly in the response as a list
            # rather than nested under a 'data' field
            batch_documents = response if isinstance(response, list) else []
            
            if not batch_documents:
                logger.info("No more documents to fetch")
                break
                
            all_documents.extend(batch_documents)
            logger.info(f"Fetched {len(batch_documents)} documents in this batch. Total so far: {len(all_documents)}")
            
            # If we got fewer documents than requested, we've reached the end
            if len(batch_documents) < batch_size:
                break
                
            # Move to the next batch
            skip += batch_size
            
        except requests.exceptions.HTTPError as http_err:
            if http_err.response.status_code == 429:  # Too Many Requests
                retry_after = int(http_err.response.headers.get('Retry-After', '10'))
                logger.warning(f"Rate limit exceeded. Waiting for {retry_after} seconds before retrying.")
                time.sleep(retry_after)
                # Don't increment skip, retry the same batch
                continue
            elif http_err.response.status_code >= 500:  # Server errors
                logger.error(f"Server error while fetching documents: {str(http_err)}", exc_info=True)
                # Let Celery retry this task
                raise http_err
            else:  # Client errors (4xx)
                logger.error(f"Client error while fetching documents: {str(http_err)}", exc_info=True)
                # For client errors, we don't want to retry as they're likely permanent
                # But we want to record the failure and terminate the task
                raise http_err
        except requests.exceptions.ConnectionError as conn_err:
            # Network errors should be retried
            logger.error(f"Connection error while fetching documents: {str(conn_err)}", exc_info=True)
            raise conn_err
        except requests.exceptions.Timeout as timeout_err:
            # Timeout errors should be retried
            logger.error(f"Timeout error while fetching documents: {str(timeout_err)}", exc_info=True)
            raise timeout_err
        except Exception as e:
            # For unexpected errors, log and let Celery handle retry
            logger.error(f"Unexpected error while fetching all documents: {str(e)}", exc_info=True)
            raise
    
    logger.info(f"Successfully fetched all {len(all_documents)} documents")
    return all_documents


def get_document(document_id: str) -> Dict[str, Any]:
    """Get a specific document from TipTap Cloud API
    
    Args:
        document_id: The ID of the document to fetch
        
    Returns:
        Dictionary containing the document data
        
    Raises:
        requests.HTTPError: For HTTP errors (4xx, 5xx)
        requests.ConnectionError: For network connection errors
        requests.Timeout: For request timeouts
        requests.RequestException: For other request-related errors
        ValueError: If document_id is invalid or document not found
        Exception: For unexpected errors
    """
    if not document_id:
        logger.error("Invalid document_id: document_id cannot be empty")
        raise ValueError("document_id cannot be empty")
        
    url = f"{get_tiptap_base_url()}/documents/{document_id}"
    
    try:
        logger.info(f"Fetching document {document_id} from TipTap")
        response = requests.get(url, headers=get_tiptap_headers())
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        if http_err.response.status_code == 404:
            logger.error(f"Document {document_id} not found in TipTap", exc_info=True)
            raise ValueError(f"Document {document_id} not found") from http_err
        elif http_err.response.status_code == 429:  # Too Many Requests
            retry_after = int(http_err.response.headers.get('Retry-After', '10'))
            logger.warning(f"Rate limit exceeded. Retry after {retry_after} seconds.")
            # Let Celery handle the retry with backoff
            raise http_err
        elif http_err.response.status_code >= 500:  # Server errors
            logger.error(f"Server error while fetching document {document_id}: {str(http_err)}", exc_info=True)
            # Let Celery retry this task
            raise http_err
        else:  # Other client errors
            logger.error(f"Client error while fetching document {document_id}: {str(http_err)}", exc_info=True)
            raise http_err
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as err:
        # Network errors should be retried by Celery
        logger.error(f"Network error while fetching document {document_id}: {str(err)}", exc_info=True)
        raise err
    except Exception as e:
        logger.error(f"Unexpected error while fetching document {document_id}: {str(e)}", exc_info=True)
        raise


@shared_task(
    bind=True,
    name='app.tasks.document.sync_documents',
    queue='doc_persistence',
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=300,
    time_limit=360,
    acks_late=True
)
def sync_documents(self, document_data):
    """Synchronize document from provided data to local database."""
    logger.info("Starting document synchronization")

    try:
        document = document_data.get("document")
        if not document:
            logger.error("Missing document in document_data")
            raise ValueError("Document is required")
            
        document_name = document.get("name")
        if not document_name:
            logger.error("Missing name in document")
            raise ValueError("Document name is required")

        try:
            document_id = document_name.split("_")[1]
        except (IndexError, ValueError):
            logger.error(f"Invalid document name format: {document_name}")
            raise ValueError(f"Invalid document name format: {document_name}")

        # Extract metadata from the original document item
        document_size = document.get("size")
        tiptap_created_at = document.get("created_at")
        tiptap_updated_at = document.get("updated_at")
        
        if not tiptap_updated_at:
            logger.error(f"Missing updated_at timestamp in TipTap document: {document_name}")
            raise ValueError(f"Missing updated_at timestamp in TipTap document: {document_name}")

        response = None

        with SessionLocal() as db:
            # Check if document exists by ID and load user + tenants
            query = select(Document).where(Document.document_id == document_id).options(
                joinedload(Document.user)  # Eagerly load the user
            )
            result = db.execute(query)
            existing_doc = result.scalar_one_or_none()
            if not existing_doc:
                logger.error(f"Document with ID {document_id} not found in the database. Cannot proceed with sync.")
                response = {"status": "skipped", "reason": "Document not found in the database"}
                return response

            logger.info(
                f"Found document {document_id} for sync. User ID: {existing_doc.user_id}, Title: {existing_doc.title}.")

            if not existing_doc.user:
                logger.error(
                    f"Document {document_id} (Title: {existing_doc.title}) has no associated user. Cannot determine tenant.")
                raise ValueError(f"Document {document_id} has no user, cannot determine tenant.")

            if not existing_doc.user.tenants or len(existing_doc.user.tenants) == 0:
                logger.error(
                    f"User {existing_doc.user_id} associated with document {document_id} (Title: {existing_doc.title}) has no tenants configured. Cannot proceed with sync.")
                raise ValueError(f"User {existing_doc.user_id} for document {document_id} has no tenants.")
            
            tenant_id = existing_doc.user.tenants[0]
            try:
                tiptap_dt = datetime.fromisoformat(tiptap_updated_at.replace('Z', '+00:00'))
                local_dt = existing_doc.updated_at
                logger.info(f"Comparing timestamps - TipTap: {tiptap_dt.isoformat()}, Local: {local_dt.isoformat()}")
                if tiptap_dt <= local_dt:
                    logger.info(f"Document {document_id} is already up to date. Skipping update.")
                    return {
                        "status": "skipped", 
                        "reason": "document_up_to_date",
                        "document_name": document_name,
                        "tiptap_updated_at": tiptap_updated_at,
                        "local_updated_at": local_dt.isoformat()
                    }
                logger.info(f"Fetching document {document_name} content from TipTap")
                document_details = get_document(document_name)

                logger.info(f"Updating document {document_id} with newer content from TipTap")
                try:
                    vector_service = get_vector_service(max_retries=3, retry_delay=2)
                    doc_service = SyncDocumentService(db=db, page_vector_service=vector_service)
                except RuntimeError as e:
                    logger.error(f"Cannot sync document {document_id} - Weaviate connection failed after retries: {str(e)}")
                    return {"status": "error", "message": f"Weaviate connection failed after retries: {str(e)}"}

                content = document_details
                if not content or len(content['content']) == 0:
                    logger.warning(f"Document {document_id} has no content: {content}. Skipping update.")
                    return {
                        "status": "skipped", 
                        "reason": "document_has_no_content",
                        "document_name": document_name,
                        "tiptap_updated_at": tiptap_updated_at,
                        "local_updated_at": local_dt.isoformat()
                    }
                response = doc_service.update_document(
                    doc_id=document_id,
                    user_id=existing_doc.user_id,
                    tenant_id=tenant_id,
                    content=content,
                    title=existing_doc.title,
                    doc_size=len(str(content))
                )
                logger.info(f"Successfully updated document: {document_id}")
            except ValueError as ve:
                logger.error(f"Error parsing timestamp for document {document_id}: {str(ve)}")
                raise ValueError(f"Error parsing timestamp: {str(ve)}")
            except Exception as e:
                logger.error(f"Error updating document {document_id}: {str(e)}", exc_info=True)
                raise RuntimeError(f"Error updating document: {str(e)}") from e
        
        return response
        
    except ValueError as ve:
        logger.error(f"Document synchronization failed due to a value error: {str(ve)}", exc_info=True)
        raise  # Re-raise to let Celery handle retry/failure
    except Exception as e:
        logger.error(f"Document synchronization failed: {str(e)}", exc_info=True)
        raise


# Redis distributed lock implementation for Celery tasks
class RedisLock:
    """A distributed lock implementation using Redis.
    
    This class provides a simple distributed lock mechanism to prevent 
    multiple instances of the same task from running concurrently.
    """
    
    def __init__(self, lock_name: str, expire_time: int = 3600, retry_interval: float = 0.2, max_retries: int = 5):
        """Initialize the Redis lock.
        
        Args:
            lock_name: A unique name for the lock
            expire_time: Time in seconds after which the lock expires automatically (default: 3600)
            retry_interval: Time in seconds to wait between retries (default: 0.2)
            max_retries: Maximum number of times to retry acquiring the lock (default: 5)
        """
        self.redis = get_sync_redis()
        self.lock_name = f"lock:{lock_name}"
        self.expire_time = expire_time
        self.retry_interval = retry_interval
        self.max_retries = max_retries
        self._locked = False
    
    def acquire(self) -> bool:
        """Acquire the lock.
        
        Returns:
            bool: True if the lock was acquired, False otherwise
        """
        retries = 0
        
        while retries < self.max_retries:
            # Try to set the lock key with NX option (only if it doesn't exist)
            acquired = self.redis.set(
                self.lock_name, 
                str(time.time()), 
                ex=self.expire_time, 
                nx=True
            )
            
            if acquired:
                self._locked = True
                logger.info(f"Acquired lock: {self.lock_name}")
                return True
            
            # If we couldn't acquire the lock, check if it's expired
            lock_value = self.redis.get(self.lock_name)
            if lock_value:
                # If the lock exists but is older than expire_time, try to release it
                try:
                    lock_time = float(lock_value)
                    if time.time() - lock_time > self.expire_time:
                        # Lock is stale, try to delete it
                        self.redis.delete(self.lock_name)
                        logger.warning(f"Deleted stale lock: {self.lock_name}")
                        continue
                except (ValueError, TypeError):
                    # If we can't parse the lock value, just continue
                    pass
            
            # Wait before retrying
            time.sleep(self.retry_interval)
            retries += 1
        
        logger.warning(f"Failed to acquire lock: {self.lock_name} after {self.max_retries} retries")
        return False
    
    def release(self) -> bool:
        """Release the lock.
        
        Returns:
            bool: True if the lock was released, False otherwise
        """
        if self._locked:
            result = self.redis.delete(self.lock_name)
            self._locked = False
            logger.info(f"Released lock: {self.lock_name}")
            return result > 0
        return False
    
    def __enter__(self):
        self.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def with_distributed_lock(lock_name: str, expire_time: int = 3600):
    """Decorator to run a function with a distributed lock.
    
    Args:
        lock_name: A unique name for the lock
        expire_time: Time in seconds after which the lock expires automatically (default: 3600)
        
    Returns:
        The decorated function
    """
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            lock = RedisLock(lock_name, expire_time=expire_time)
            if lock.acquire():
                try:
                    return func(*args, **kwargs)
                finally:
                    lock.release()
            else:
                logger.info(f"Task {lock_name} is already running, skipping this execution")
                return {"status": "skipped", "reason": "Task already running"}
        return wrapper
    return decorator


@shared_task(
    bind=True,
    name='app.tasks.document.sync_all_tiptap_documents',
    queue='doc_persistence',
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=1800,  # 30 minutes
    time_limit=2100,       # 35 minutes
    acks_late=True
)
def sync_all_tiptap_documents(self):
    """Fetch all documents from TipTap and synchronize them with the local database.
    This task is scheduled to run periodically.
    """
    logger.info("Starting synchronization of all TipTap documents")
    
    # Use distributed lock to prevent multiple instances from running concurrently
    # The lock will expire after 45 minutes (2700 seconds) to prevent deadlocks
    lock = RedisLock("sync_all_tiptap_documents", expire_time=2700)
    
    if not lock.acquire():
        logger.info("Another sync_all_tiptap_documents task is already running, skipping this execution")
        return {"status": "skipped", "reason": "Task already running"}
    
    try:
        # Fetch all documents from TipTap
        all_documents = fetch_all_documents()
        logger.info(f"Fetched {len(all_documents)} documents from TipTap")
        
        # Process each document
        documents_queued = 0
        for doc in all_documents:
            try:
                # Get the document name from the response for logging
                doc_name = doc.get("name")
                if not doc_name:
                    logger.warning("Skipping document with missing name")
                    continue
                
                # Check document name format and process accordingly
                if doc_name.startswith("document"):
                    # Only queue sync_documents task for documents starting with 'document'
                    logger.info(f"Queuing sync task for document {doc_name}")
                    sync_documents.delay({"document": doc})
                    documents_queued += 1
                    continue
                elif doc_name.startswith("template"):
                    # For templates, just log but don't process
                    logger.info(f"Found template: {doc_name} - not processing")
                    continue
                else:
                    # Skip documents that don't match expected naming patterns
                    logger.info(f"Skipping document with name format not starting with 'document' or 'template': {doc_name}")
                    continue
                
            except Exception as e:
                # Log error but continue with other documents
                logger.error(f"Error queuing document {doc.get('name', 'unknown')}: {str(e)}", exc_info=True)
                continue
        
        return {"status": "success", "documents_queued": documents_queued}
        
    except Exception as e:
        logger.error(f"Failed to synchronize all TipTap documents: {str(e)}", exc_info=True)
        raise
    finally:
        # Always release the lock, even if an exception occurs
        lock.release()

