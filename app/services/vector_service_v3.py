import json
import time
import hashlib
from typing import Dict, Any, List, Union
from uuid import UUID

from app.utils.extract_text import extract_text_from_json
from app.utils.chunk_text import chunk_text
from app.services.weaviate_base_service import WeaviateService
from weaviate.classes.query import Filter
from app.core.logging_config import logger

# Define the collection name based on your schema
WEAVIATE_DOCUMENT_CLASS_NAME = "Document"
DEFAULT_BATCH_SIZE = 100 # Adjust as needed

def generate_chunk_fingerprint(chunk_text: str) -> str:
    """Generate a unique fingerprint for a chunk of text."""
    # Normalize text before hashing
    normalized_text = " ".join(chunk_text.lower().split())
    return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()


class VectorService(WeaviateService):
    def __init__(self):
        super().__init__()
    
    def _generate_chunk_fingerprint(self, chunk_text: str) -> str:
        """Generate a unique fingerprint for a chunk of text."""
        # Normalize text before hashing
        normalized_text = " ".join(chunk_text.lower().split())
        return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()
    
    def create_vectors(
        self,
        tenant_id: str,
        doc_id: UUID,
        workspace_id: UUID,
        chat_conversation_id: str,
        title: str,
        chunks: List[str]
    ) -> Dict[str, Any]:
        try:
            # Get Document collection with tenant
            collection = self.client.collections.get(WEAVIATE_DOCUMENT_CLASS_NAME).with_tenant(tenant=tenant_id)
            
            # Process each chunk
            successful_chunks = 0
            objects_to_insert = []
            for i, chunk in enumerate(chunks):
                try:
                    # Prepare properties for Weaviate
                    properties = {
                        "tenantId": tenant_id,
                        "documentId": str(doc_id),
                        "workspaceId": str(workspace_id),
                        "chatSessionId": str(chat_conversation_id),
                        "title": title,
                        "contentChunk": chunk,
                        "chunkOrder": i,
                        "chunkFingerprint": self._generate_chunk_fingerprint(chunk)
                    }
                    objects_to_insert.append(properties)
                        
                except Exception as chunk_error:
                    logger.error(f"Error processing chunk {i+1}: {str(chunk_error)}")
                    continue

            try:
                logger.debug(f"Starting batch insert for {len(objects_to_insert)}.")

                batch_return_summary = collection.data.insert_many(objects_to_insert)

                successful_count = 0
                failed_count = 0
                all_errors_dict = {}
                has_errors_flag = False

                if batch_return_summary.has_errors:
                    has_errors_flag = True
                    for i, res_obj in enumerate(batch_return_summary.objects):
                        if res_obj.errors:
                            failed_count += 1
                            error_messages = "; ".join(
                                res_obj.errors.messages) if res_obj.errors.messages else "Unknown batch error"
                            all_errors_dict[i] = f"UUID: {res_obj.uuid}, Error: {error_messages}"
                        else:
                            successful_count += 1
                else:
                    successful_count = len(objects_to_insert)

                logger.info(
                    f"Batch insert summary for '{WEAVIATE_DOCUMENT_CLASS_NAME}': Attempted: {len(objects_to_insert)}, Successful: {successful_count}, Failed: {failed_count}.")
                if has_errors_flag:
                    logger.error(f"Batch insert errors dictionary in '{WEAVIATE_DOCUMENT_CLASS_NAME}': {all_errors_dict}")
            except Exception as e:
                logger.error(f"Failed to insert chunks into Weaviate: {str(e)}")
                return {
                    "status": "error",
                    "message": f"Failed to insert chunks into Weaviate: {str(e)}",
                    "document_id": str(doc_id)
                }
            
            # Return success status with details
            return {
                "status": "success",
                "message": f"Successfully vectorized {successful_chunks} out of {len(chunks)} chunks",
                "total_chunks": len(chunks),
                "successful_chunks": successful_chunks,
                "document_id": str(doc_id)
            }
            
        except Exception as e:
            logger.error(f"Failed to vectorize document {doc_id}: {str(e)}")
            return {
                "status": "error",
                "message": f"Failed to vectorize document: {str(e)}",
                "document_id": str(doc_id)
            }