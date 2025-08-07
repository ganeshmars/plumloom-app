# app/services/weaviate/document_service_sync.py

import time # Import time if using update logic similar to Page sync
from typing import Dict, Any, List, Optional, Union
from uuid import UUID

from .base_vector_service import BaseVectorService
from .repository_sync import WeaviateRepositorySync # Changed Import
from .exceptions import VectorStoreOperationError, VectorStoreNotFoundError # Added for potential update logic
from weaviate.collections.classes.filters import Filter

from app.core.logging_config import logger

class DocumentVectorServiceSync(BaseVectorService):
    """Synchronous service for managing vectors in the 'Document' collection."""

    COLLECTION_NAME = "Document"
    # Override chunk size/overlap if needed
    # DEFAULT_CHUNK_SIZE = 500
    # DEFAULT_CHUNK_OVERLAP = 100

    def __init__(self, repository: WeaviateRepositorySync): # Changed Type Hint
        self._repo = repository
        logger.info(f"{self.__class__.__name__} initialized.")

    def create_vectors_from_content( # Removed async
        self,
        tenant_id: str,
        doc_id: UUID,
        workspace_id: UUID,
        title: str,
        content: Union[Dict[str, Any], str],
        chat_session_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        logger.info(f"Sync creating vectors for Document {doc_id} in workspace {workspace_id}")
        try:
            text = self._prepare_content(content)
            chunks = self._chunk_content(text)

            if not chunks:
                 return {"status": "success", "message": "No content to vectorize.", "document_id": str(doc_id), "chunks_processed": 0}

            objects_to_insert = []
            for i, chunk in enumerate(chunks):
                properties = {
                    "tenantId": tenant_id,
                    "documentId": str(doc_id),
                    "workspaceId": str(workspace_id),
                    "title": title,
                    "contentChunk": chunk,
                    "chunkOrder": i,
                    "chunkFingerprint": self._generate_fingerprint(chunk)
                }
                if chat_session_id:
                     properties["chatSessionId"] = str(chat_session_id)

                objects_to_insert.append(properties)

            # Sync call (no await)
            batch_result_dict: Dict[str, Any] = self._repo.insert_many( # Removed await
                self.COLLECTION_NAME, objects_to_insert, tenant_id
            )

            # Process result dictionary
            successful_chunks = batch_result_dict.get("successful", 0)
            failed_chunks = batch_result_dict.get("failed", 0)
            has_errors = batch_result_dict.get("has_errors", False)
            errors = batch_result_dict.get("errors", {})

            status = "success" if not has_errors else "partial_success"
            message = f"Vectorized ~{successful_chunks}/{len(objects_to_insert)} chunks for Document."
            if has_errors:
                 message += f" Encountered {failed_chunks} errors."
                 logger.error(f"Sync create vector errors for Document {doc_id}: {errors}")

            return {
                "status": status,
                "message": message,
                "document_id": str(doc_id),
                "chunks_processed": len(objects_to_insert),
                "successful_chunks": successful_chunks,
                "failed_chunks": failed_chunks,
                 "errors": errors if failed_chunks > 0 else None
            }
        except Exception as e:
            logger.error(f"Failed to sync create vectors for Document {doc_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Failed to create vectors: {e}", "document_id": str(doc_id)}


    def update_vectors_from_content( # Removed async
        self,
        tenant_id: str,
        doc_id: UUID,
        workspace_id: Optional[UUID], # Allow optional for update
        title: Optional[str],        # Allow optional for update
        content: Union[Dict[str, Any], str],
        chat_session_id: Optional[str] = None, # Specific to Document collection
        **kwargs
    ) -> Dict[str, Any]:
        """Extracts, chunks, and updates vectors (synchronously), managing changes for Documents."""
        # Implementation Note: This follows the sequential pattern from PageVectorServiceSync.
        # You might adapt it further based on specific Document needs.

        start_time = time.time()
        logger.info(f"Starting synchronous update for Document {doc_id} in tenant {tenant_id}...")
        doc_id_str = str(doc_id)

        added_count = 0
        deleted_count = 0
        updated_count = 0
        operation_errors = []

        try:
            # 1. Prepare new chunks
            text = self._prepare_content(content)
            new_chunks = self._chunk_content(text)
            new_chunk_data = {
                self._generate_fingerprint(chunk): {"text": chunk, "order": i}
                for i, chunk in enumerate(new_chunks)
            }
            logger.debug(f"Document {doc_id_str}: Generated {len(new_chunk_data)} new chunks/fingerprints.")

            # 2. Get existing chunks
            existing_filter = Filter.by_property("documentId").equal(doc_id_str)
            existing_objects = self._repo.fetch_objects( # Removed await
                collection_name=self.COLLECTION_NAME,
                filters=existing_filter,
                tenant_id=tenant_id,
                # Fetch all relevant fields for comparison/update
                return_properties=["chunkFingerprint", "chunkOrder", "title", "workspaceId", "chatSessionId"],
                limit=10000
            )

            if not existing_objects:
                logger.warning(f"Document {doc_id_str} not found in vector store for update. Treating as creation.")
                # Document requires workspace_id and title for creation.
                if not workspace_id or not title:
                    raise ValueError("Cannot create/update document: workspace_id and title are required if the document doesn't exist.")
                # Call sync create method, passing chat_session_id
                return self.create_vectors_from_content(tenant_id, doc_id, workspace_id, title, content, chat_session_id)


            existing_by_fingerprint = {
                obj["properties"].get("chunkFingerprint"): {
                    "uuid": obj["uuid"],
                    "order": obj["properties"].get("chunkOrder"),
                    "title": obj["properties"].get("title"),
                    "workspaceId": obj["properties"].get("workspaceId"),
                    "chatSessionId": obj["properties"].get("chatSessionId") # Include chat session ID
                 }
                for obj in existing_objects if "properties" in obj and obj["properties"].get("chunkFingerprint")
            }
            logger.debug(f"Document {doc_id_str}: Found {len(existing_by_fingerprint)} existing chunks.")

            # Determine definitive title, workspace ID, and chat session ID
            # Use payload values if provided, otherwise fallback to existing values
            current_title = title if title is not None else existing_objects[0]["properties"].get("title", "Unknown Title")
            current_workspace_id = str(workspace_id) if workspace_id is not None else existing_objects[0]["properties"].get("workspaceId", "Unknown Workspace")
            # For chat_session_id, decide if update should overwrite or preserve existing if not provided in payload.
            # Let's assume payload value takes precedence, or keep existing if payload is None.
            current_chat_session_id = str(chat_session_id) if chat_session_id is not None else existing_objects[0]["properties"].get("chatSessionId")


            # 3. Determine changes
            fingerprints_to_add = set(new_chunk_data.keys()) - set(existing_by_fingerprint.keys())
            fingerprints_to_delete = set(existing_by_fingerprint.keys()) - set(new_chunk_data.keys())
            fingerprints_common = set(new_chunk_data.keys()) & set(existing_by_fingerprint.keys())

            objects_to_insert = []
            ids_to_delete = []
            updates_to_perform = []

            # Prepare additions (include chatSessionId)
            for fp in fingerprints_to_add:
                chunk_info = new_chunk_data[fp]
                properties = {
                    "tenantId": tenant_id, "documentId": doc_id_str, "workspaceId": current_workspace_id,
                    "title": current_title, "contentChunk": chunk_info["text"], "chunkOrder": chunk_info["order"],
                    "chunkFingerprint": fp
                }
                if current_chat_session_id:
                    properties["chatSessionId"] = current_chat_session_id # Add chat session ID
                objects_to_insert.append(properties)

            # Prepare deletions
            for fp in fingerprints_to_delete:
                ids_to_delete.append(existing_by_fingerprint[fp]["uuid"])

            # Prepare updates (check chatSessionId too)
            for fp in fingerprints_common:
                new_info = new_chunk_data[fp]
                existing_info = existing_by_fingerprint[fp]
                props_to_update = {}
                if existing_info["order"] != new_info["order"]:
                    props_to_update["chunkOrder"] = new_info["order"]
                if existing_info["title"] != current_title:
                     props_to_update["title"] = current_title
                if existing_info["workspaceId"] != current_workspace_id:
                     props_to_update["workspaceId"] = current_workspace_id
                # Compare chatSessionId (handle None vs string comparison carefully)
                existing_chat_id = existing_info.get("chatSessionId")
                if existing_chat_id != current_chat_session_id:
                     props_to_update["chatSessionId"] = current_chat_session_id # Can be None to remove it

                if props_to_update:
                    updates_to_perform.append((existing_info["uuid"], props_to_update))

            logger.info(f"Document {doc_id_str}: Sync Changes - Add: {len(objects_to_insert)}, Delete: {len(ids_to_delete)}, Update: {len(updates_to_perform)}")

            # 4. Execute changes sequentially (similar to Page sync)
            # Insert
            if objects_to_insert:
                try:
                    insert_result_dict = self._repo.insert_many(self.COLLECTION_NAME, objects_to_insert, tenant_id) # No await
                    added_count = insert_result_dict.get("successful", 0)
                    if insert_result_dict.get("has_errors", False):
                         err_msg = f"Insert batch errors: {insert_result_dict.get('errors', {})}"
                         operation_errors.append(VectorStoreOperationError(err_msg))
                         logger.error(f"Document {doc_id_str}: {err_msg}")
                except Exception as e:
                     logger.error(f"Insert many task failed for {doc_id_str}: {e}", exc_info=True)
                     operation_errors.append(e)

            # Delete
            if ids_to_delete:
                try:
                    delete_filter = Filter.by_id().contains_any([str(uid) for uid in ids_to_delete])
                    deleted_count = self._repo.delete_many(self.COLLECTION_NAME, where_filter=delete_filter, tenant_id=tenant_id) # No await
                except Exception as e:
                    logger.error(f"Delete many task failed for {doc_id_str}: {e}", exc_info=True)
                    operation_errors.append(e)
                    deleted_count = -1

            # Update
            for update_uuid, update_props in updates_to_perform:
                try:
                    success = self._repo.update(self.COLLECTION_NAME, update_uuid, update_props, tenant_id) # No await
                    if success:
                        updated_count += 1
                except Exception as e:
                     logger.error(f"Update task failed for chunk {update_uuid} in {doc_id_str}: {e}", exc_info=True)
                     operation_errors.append(e)

            # Final status determination
            status = "success" if not operation_errors else "partial_success" if (added_count + deleted_count + updated_count) > 0 else "error"
            message = f"Sync Document update complete. Added: {added_count}, Deleted: {deleted_count}, Updated: {updated_count}."
            if operation_errors:
                message += f" Encountered {len(operation_errors)} errors during operations."
                logger.error(f"Document {doc_id_str}: Sync update operation errors: {[str(e) for e in operation_errors]}")

            elapsed = time.time() - start_time
            logger.info(f"Document {doc_id_str}: Sync update took {elapsed:.2f}s. Status: {status}")

            return {
                "status": status,
                "message": message,
                "document_id": doc_id_str,
                "stats": {
                    "added": added_count,
                    "deleted": deleted_count if deleted_count >= 0 else "failed",
                    "updated": updated_count,
                    "unchanged": len(fingerprints_common) - updated_count,
                    "errors": len(operation_errors)
                },
                "error_details": [str(e) for e in operation_errors] if operation_errors else None
            }
        except VectorStoreNotFoundError as e:
             logger.warning(f"Sync Document update failed for doc {doc_id_str}: {e}")
             raise e
        except ValueError as e:
             logger.warning(f"Sync Document update failed for doc {doc_id_str} due to bad input: {e}")
             raise e
        except Exception as e:
            logger.error(f"Failed to sync update vectors for Document {doc_id}: {e}", exc_info=True)
            final_errors = operation_errors + [e] if e not in operation_errors else operation_errors
            return {
                "status": "error",
                "message": f"Sync Document update failed: {e}",
                "document_id": str(doc_id),
                 "stats": {
                    "added": added_count,
                    "deleted": deleted_count if deleted_count >= 0 else "failed",
                    "updated": updated_count,
                    "errors": len(final_errors)
                },
                "error_details": [str(err) for err in final_errors] if final_errors else None
            }

    def delete_vectors( # Removed async
        self,
        tenant_id: str,
        doc_id: UUID,
         **kwargs
    ) -> Dict[str, Any]:
        logger.info(f"Sync deleting vectors for Document {doc_id} in tenant {tenant_id}")
        doc_id_str = str(doc_id)
        try:
            where_filter = Filter.by_property("documentId").equal(doc_id_str)
            deleted_count = self._repo.delete_many( # Removed await
                collection_name=self.COLLECTION_NAME,
                where_filter=where_filter,
                tenant_id=tenant_id
            )
            message = f"Successfully deleted {deleted_count} vector chunk(s) for Document."
            return {"status": "success", "message": message, "document_id": doc_id_str, "chunks_deleted": deleted_count}
        except Exception as e:
            logger.error(f"Failed to sync delete vectors for Document {doc_id_str}: {e}", exc_info=True)
            return {"status": "error", "message": f"Sync delete failed: {e}", "document_id": doc_id_str}

    def search( # Removed async
        self,
        tenant_id: str,
        query: str,
        limit: int = 10,
        workspace_id: Optional[UUID] = None,
        doc_id: Optional[UUID] = None,
        chat_session_id: Optional[str] = None,
        use_hybrid: bool = False,
        alpha: float = 0.5,
        **kwargs
    ) -> List[Dict[str, Any]]:
        logger.debug(f"Sync searching Document collection in tenant '{tenant_id}' for query: '{query[:50]}...'")
        try:
            # Filter logic remains the same
            filters = None
            filter_list = []
            if workspace_id:
                filter_list.append(Filter.by_property("workspaceId").equal(str(workspace_id)))
            if doc_id:
                filter_list.append(Filter.by_property("documentId").equal(str(doc_id)))
            if chat_session_id:
                filter_list.append(Filter.by_property("chatSessionId").equal(str(chat_session_id)))

            if len(filter_list) == 1:
                filters = filter_list[0]
            elif len(filter_list) > 1:
                filters = Filter.all_of(filter_list) # Use all_of for AND

            search_method = self._repo.hybrid_search if use_hybrid else self._repo.near_text_search
            search_kwargs = {
                "collection_name": self.COLLECTION_NAME, "query": query, "limit": limit,
                "filters": filters, "tenant_id": tenant_id,
                # Ensure chatSessionId is returned if it exists
                "return_properties": ["documentId", "title", "contentChunk", "chunkOrder", "workspaceId", "chatSessionId"],
            }
            if use_hybrid:
                search_kwargs["alpha"] = alpha

            # Sync call (no await)
            results = search_method(**search_kwargs) # Removed await

            logger.info(f"Sync Document search returned {len(results)} results for tenant '{tenant_id}'.")
            return results
        except Exception as e:
            logger.error(f"Sync Document search failed for tenant '{tenant_id}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Sync search operation failed: {e}") from e