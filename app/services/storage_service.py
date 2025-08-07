"""Google Cloud Storage service for document content storage."""

import json
from typing import Dict, Any, Optional
from google.cloud import storage
from google.cloud.storage import Blob
from app.core.config import get_settings
from app.core.constants import (
    GCS_STORAGE_BUCKET,
    GCS_UPLOADED_DOCUMENTS_BUCKET,
    GCS_DOCUMENTS_BUCKET
)
from app.core.logging_config import logger
settings = get_settings()

class StorageService:
    def __init__(self, bucket_name: str=GCS_DOCUMENTS_BUCKET):
        # Initialize client using settings
        self.client = settings.get_gcp_credentials()
        
        # Get bucket using constant
        self.bucket = self.client.bucket(bucket_name)
    
    def upload_json_sync(self, file_path: str, content: Dict[str, Any]) -> str:
        """Synchronous version of upload_json for Celery tasks."""
        try:
            blob = self.bucket.blob(file_path)
            blob.upload_from_string(
                json.dumps(content),
                content_type='application/json'
            )
            logger.info(f"Successfully uploaded content to GCS: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to upload content to GCS: {str(e)}")
            raise RuntimeError(f"Failed to upload content to GCS: {str(e)}")
    
    def get_json_sync(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Synchronous version of get_json for Celery tasks."""
        try:
            blob = self.bucket.blob(file_path)
            content = blob.download_as_bytes()
            return json.loads(content.decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to retrieve content from GCS: {str(e)}")
            raise RuntimeError(f"Failed to retrieve content from GCS: {str(e)}")

    def delete_file_sync(self, file_path: str) -> None:
        """Synchronous version of delete_file for Celery tasks."""
        try:
            blob = self.bucket.blob(file_path)
            if blob.exists():
                blob.delete()
                logger.info(f"Successfully deleted file from GCS: {file_path}")
            else:
                logger.warning(f"File not found in GCS: {file_path}")
        except Exception as e:
            logger.error(f"Failed to delete file from GCS: {str(e)}")
            raise RuntimeError(f"Failed to delete file from GCS: {str(e)}")

    async def upload_json(self, file_path: str, content: Dict[str, Any]) -> str:
        """Upload JSON content to Google Cloud Storage."""
        try:
            blob = self.bucket.blob(file_path)
            blob.upload_from_string(
                json.dumps(content),
                content_type='application/json'
            )
            logger.info(f"Successfully uploaded content to GCS: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to upload content to GCS: {str(e)}")
            raise RuntimeError(f"Failed to upload content to GCS: {str(e)}")

    async def get_json(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Retrieve JSON content from Google Cloud Storage."""
        try:
            blob = self.bucket.blob(file_path)
            content = blob.download_as_bytes()
            return json.loads(content.decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to retrieve content from GCS: {str(e)}")
            raise RuntimeError(f"Failed to retrieve content from GCS: {str(e)}")
            
    async def delete_file(self, file_path: str) -> None:
        """Delete a file from Google Cloud Storage."""
        try:
            blob = self.bucket.blob(file_path)
            if blob.exists():
                blob.delete()
                logger.info(f"Successfully deleted file from GCS: {file_path}")
            else:
                logger.warning(f"File not found in GCS: {file_path}")
        except Exception as e:
            logger.error(f"Failed to delete file from GCS: {str(e)}")
            raise RuntimeError(f"Failed to delete file from GCS: {str(e)}")

    async def delete_prefix(self, prefix: str) -> bool:
        """Delete all files under a prefix from Google Cloud Storage."""
        try:
            blobs = self.bucket.list_blobs(prefix=prefix)
            for blob in blobs:
                blob.delete()
            logger.info(f"Successfully deleted files with prefix from GCS: {prefix}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete files with prefix from GCS: {str(e)}")
            raise RuntimeError(f"Failed to delete files with prefix from GCS: {str(e)}")
