# app/core/storage.py

from typing import Optional, Union

from google.cloud import storage
from google.cloud.exceptions import NotFound
from app.core.logging_config import logger
from app.core.config import get_settings
from app.core.constants import (
    # Assuming these constants might be used for default bucket names if needed,
    # though the functions take bucket_name as an argument.
    GCS_UPLOADED_DOCUMENTS_BUCKET,
    GCS_STORAGE_BUCKET,
    GCS_DOCUMENTS_BUCKET
)

# Initialize logger for this module

# Load application settings
settings = get_settings()

# --- Initialize Google Cloud Storage Client ---
# This client instance will be shared by all functions in this module.
gcs_client: Optional[storage.Client] = None
try:
    # Attempt to get credentials and initialize the client
    gcs_client = settings.get_gcp_credentials()

    # Validate if the client object is of the expected type
    if not isinstance(gcs_client, storage.Client):
        logger.error("Failed to initialize a valid GCS storage.Client. Type received: %s", type(gcs_client).__name__)
        # Set client to None to prevent further operations
        gcs_client = None
        raise RuntimeError("Invalid GCS client initialized.")
    else:
        logger.info("Successfully initialized Google Cloud Storage client.")

except Exception as e:
    logger.critical("CRITICAL: Error initializing Google Cloud Storage client: %s", e, exc_info=True)
    # Prevent the application from proceeding without a GCS client if it's essential
    gcs_client = None # Ensure client is None on failure
    # Re-raise the error to potentially halt application startup if GCS is critical
    raise RuntimeError(f"Could not initialize GCS client: {e}") from e
# --- End Client Initialization ---


# === Asynchronous Functions ===

async def upload_file_to_gcs(
    content: Union[bytes, str],
    file_path: str,
    bucket_name: str,
    content_type: Optional[str] = None
) -> str:
    """
    Asynchronously upload binary or string content to Google Cloud Storage.
    Uses blob.public_url. Access depends on GCS permissions.
    """
    if not gcs_client:
        raise RuntimeError("GCS client not available for async upload")

    try:
        logger.info(f"Async uploading file to GCS: gs://{bucket_name}/{file_path}")
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(file_path)
        logger.debug(f"Blob object created: {blob.name}")

        blob.upload_from_string(content, content_type=content_type)
        logger.info(f"Blob uploaded successfully: gs://{bucket_name}/{file_path}")

        # --- Use blob.public_url ---
        public_url = blob.public_url
        logger.info(f"Public URL from blob.public_url: {public_url}")
        # --- End change ---

        if hasattr(settings, 'CDN_BASE_URL') and settings.CDN_BASE_URL:
            cdn_url = f"{settings.CDN_BASE_URL.rstrip('/')}/{file_path}"
            logger.info(f"Returning CDN URL: {cdn_url}")
            return cdn_url

        logger.info(f"Returning GCS URL: {public_url}")
        return public_url

    except Exception as e:
        logger.error(f"Failed to async upload file to GCS (gs://{bucket_name}/{file_path}): {e}", exc_info=True)
        raise RuntimeError(f"Failed to async upload file to GCS: {str(e)}") from e


async def delete_file_from_gcs(file_path: str, bucket_name: str) -> bool:
    """
    Asynchronously delete a file from Google Cloud Storage.

    Args:
        file_path: The path to the object within the bucket.
        bucket_name: The name of the GCS bucket.

    Returns:
        True if the file was deleted successfully or was already gone, False on error.
    """
    if not gcs_client:
        logger.error("GCS client is not initialized. Cannot perform async delete.")
        raise RuntimeError("GCS client not available for async delete")

    try:
        logger.info(f"Async deleting file from GCS: gs://{bucket_name}/{file_path}")

        # Remove CDN prefix if present
        if hasattr(settings, 'CDN_BASE_URL') and settings.CDN_BASE_URL and file_path.startswith(settings.CDN_BASE_URL):
            original_path = file_path
            file_path = file_path.replace(settings.CDN_BASE_URL, "").lstrip('/')
            logger.debug(f"Removed CDN prefix from '{original_path}', adjusted path to: {file_path}")

        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(file_path)

        # Note: blob.delete() is also blocking. See comment in upload_file_to_gcs.
        # Check existence first to provide better logging/return value.
        if blob.exists():
             blob.delete()
             logger.info(f"Successfully async deleted file from GCS: gs://{bucket_name}/{file_path}")
             return True
        else:
             logger.warning(f"File not found during async delete attempt (maybe already deleted?): gs://{bucket_name}/{file_path}")
             return True # Treat not found as success from deletion perspective

    except Exception as e:
        logger.error(f"Failed to async delete file from GCS (gs://{bucket_name}/{file_path}): {e}", exc_info=True)
        # Return False on failure
        return False


# === Synchronous Functions ===

def upload_file_to_gcs_sync(
    content: Union[bytes, str],
    file_path: str,
    bucket_name: str,
    content_type: Optional[str] = None
) -> str:
    """
    Synchronously upload binary or string content to Google Cloud Storage.
    Uses blob.public_url. Access depends on GCS permissions.
    """
    if not gcs_client:
        raise RuntimeError("GCS client not available for sync upload")

    try:
        logger.info(f"Sync uploading file to GCS: gs://{bucket_name}/{file_path}")
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(file_path)
        logger.debug(f"Blob object created: {blob.name}")

        blob.upload_from_string(content, content_type=content_type)
        logger.info(f"Blob uploaded successfully: gs://{bucket_name}/{file_path}")

        # --- Use blob.public_url ---
        public_url = blob.public_url
        logger.info(f"Public URL from blob.public_url: {public_url}")
        # --- End change ---

        if hasattr(settings, 'CDN_BASE_URL') and settings.CDN_BASE_URL:
            cdn_url = f"{settings.CDN_BASE_URL.rstrip('/')}/{file_path}"
            logger.info(f"Returning CDN URL: {cdn_url}")
            return cdn_url

        logger.info(f"Returning GCS URL: {public_url}")
        return public_url

    except Exception as e:
        logger.error(f"Failed to sync upload file to GCS (gs://{bucket_name}/{file_path}): {e}", exc_info=True)
        raise RuntimeError(f"Failed to sync upload file to GCS: {str(e)}") from e


def get_file_content_sync(file_path: str, bucket_name: str) -> bytes:
    """
    Synchronously retrieves the content of a file from GCS as bytes.

    Args:
        file_path: The path to the object within the bucket (e.g., "tenant/ws/doc/v1.json").
        bucket_name: The name of the GCS bucket.

    Returns:
        The content of the file as bytes.

    Raises:
        FileNotFoundError: If the specified file_path does not exist in the bucket.
        RuntimeError: For other GCS errors (permissions, network, etc.).
        ValueError: If the GCS client is not initialized (consistency with other sync funcs).
    """
    if not gcs_client:
        logger.error("GCS client is not initialized. Cannot get file content.")
        # Use RuntimeError consistent with other sync functions on client failure
        raise RuntimeError("GCS client not available")

    logger.info(f"Attempting to sync get content for GCS file: gs://{bucket_name}/{file_path}")
    try:
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(file_path)

        # Attempt to download the content as bytes
        content_bytes = blob.download_as_bytes()

        logger.info(f"Successfully retrieved {len(content_bytes)} bytes from GCS file: gs://{bucket_name}/{file_path}")
        return content_bytes

    except NotFound:
        # Specific exception for file not found
        logger.warning(f"File not found in GCS: gs://{bucket_name}/{file_path}")
        # Raise standard Python FileNotFoundError for easier handling by the caller
        raise FileNotFoundError(f"File not found in GCS: gs://{bucket_name}/{file_path}")

    except Exception as e:
        # Catch any other GCS API errors
        logger.error(f"Failed to retrieve content from GCS (gs://{bucket_name}/{file_path}): {e}", exc_info=True)
        # Wrap in RuntimeError to indicate a GCS operation failure
        raise RuntimeError(f"Failed to retrieve content from GCS: {e}") from e


def delete_file_from_gcs_sync(file_path: str, bucket_name: str) -> bool:
    """
    Synchronously deletes a file from Google Cloud Storage.

    Args:
        file_path: The path to the object within the bucket.
        bucket_name: The name of the GCS bucket.

    Returns:
        True if the file was deleted successfully or was already gone, False on error.

    Raises:
        RuntimeError: If the GCS client is not available.
    """
    if not gcs_client:
        logger.error("GCS client is not initialized. Cannot perform sync delete.")
        raise RuntimeError("GCS client not available for sync delete")

    try:
        logger.info(f"Sync deleting file from GCS: gs://{bucket_name}/{file_path}")

        # Remove CDN prefix if present
        if hasattr(settings, 'CDN_BASE_URL') and settings.CDN_BASE_URL and file_path.startswith(settings.CDN_BASE_URL):
            original_path = file_path
            file_path = file_path.replace(settings.CDN_BASE_URL, "").lstrip('/')
            logger.debug(f"Removed CDN prefix from '{original_path}', adjusted path to: {file_path}")

        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(file_path)

        # Check existence first
        if blob.exists():
            blob.delete()
            logger.info(f"Successfully sync deleted file from GCS: gs://{bucket_name}/{file_path}")
            return True
        else:
            logger.warning(f"File not found during sync delete attempt (maybe already deleted?): gs://{bucket_name}/{file_path}")
            return True # Treat not found as success from deletion perspective

    except Exception as e:
        logger.error(f"Failed to sync delete file from GCS (gs://{bucket_name}/{file_path}): {e}", exc_info=True)
        # Return False on failure
        return False

# Example of how to potentially delete by prefix (if needed)
# def delete_prefix_sync(prefix: str, bucket_name: str) -> bool:
#     """Synchronously deletes all blobs with a given prefix."""
#     if not gcs_client:
#         logger.error("GCS client is not initialized. Cannot perform sync prefix delete.")
#         raise RuntimeError("GCS client not available for sync prefix delete")
#
#     try:
#         logger.info(f"Sync deleting files with prefix from GCS: gs://{bucket_name}/{prefix}")
#         bucket = gcs_client.bucket(bucket_name)
#         blobs_to_delete = list(bucket.list_blobs(prefix=prefix)) # Get list upfront
#
#         if not blobs_to_delete:
#             logger.info(f"No files found with prefix '{prefix}' in bucket '{bucket_name}'. Nothing to delete.")
#             return True
#
#         # Note: Deleting many files individually can be slow.
#         # For very large numbers, consider Batch operations if available or lifecycle policies.
#         deleted_count = 0
#         failed_count = 0
#         for blob in blobs_to_delete:
#             try:
#                 blob.delete()
#                 deleted_count += 1
#                 logger.debug(f"Deleted blob: {blob.name}")
#             except Exception as del_err:
#                 logger.error(f"Failed to delete blob {blob.name} during prefix deletion: {del_err}")
#                 failed_count += 1
#
#         logger.info(f"Prefix deletion summary for gs://{bucket_name}/{prefix}: Deleted={deleted_count}, Failed={failed_count}")
#         return failed_count == 0 # Return True only if all deletions succeeded
#
#     except Exception as e:
#         logger.error(f"Failed to delete files with prefix 'gs://{bucket_name}/{prefix}' from GCS: {e}", exc_info=True)
#         return False