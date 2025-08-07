# app/services/weaviate/page_service_sync.py
import time
from typing import Dict, Any, List, Optional, Union
from uuid import UUID

from weaviate.collections.classes.filters import Filter

from app.core.logging_config import logger
from .base_vector_service import BaseVectorService
from .repository_sync import WeaviateRepositorySync
from .exceptions import VectorStoreOperationError, VectorStoreNotFoundError


class PageVectorServiceSync(BaseVectorService):
    """Synchronous service for managing vectors in the 'Page' collection."""

    COLLECTION_NAME = "Page"
    DEFAULT_CHUNK_SIZE = 1000
    DEFAULT_CHUNK_OVERLAP = 200

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
        **kwargs
    ) -> Dict[str, Any]:
        start_time = time.time()
        try:
            text = self._prepare_content(content)
            chunks = self._chunk_content(text)
            logger.info(f"Document {doc_id}: Extracted {len(text)} chars, created {len(chunks)} chunks.")

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
                objects_to_insert.append(properties)

            # Call sync insert_many (no await)
            batch_result_dict: Dict[str, Any] = self._repo.insert_many( # Removed await
                self.COLLECTION_NAME, objects_to_insert, tenant_id
            )

            # Process the returned dictionary (same as async)
            successful_chunks = batch_result_dict.get("successful", 0)
            failed_chunks = batch_result_dict.get("failed", 0)
            has_errors = batch_result_dict.get("has_errors", False)
            errors = batch_result_dict.get("errors", {})

            status = "success" if not has_errors else "partial_success"
            message = f"Vectorized ~{successful_chunks}/{len(objects_to_insert)} chunks."
            if has_errors:
                message += f" Encountered {failed_chunks} errors."

            elapsed = time.time() - start_time
            logger.info(f"Document {doc_id}: Vectorization took {elapsed:.2f}s. Status: {status}")

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
            logger.error(f"Failed to create vectors for document {doc_id}: {e}", exc_info=True)
            if isinstance(e, VectorStoreOperationError):
                 return {"status": "error", "message": f"Vector store error: {e}", "document_id": str(doc_id)}
            return {"status": "error", "message": f"Failed to create vectors: {e}", "document_id": str(doc_id)}

    def update_vectors_from_content( # Removed async
        self,
        tenant_id: str,
        doc_id: UUID,
        workspace_id: Optional[UUID],
        title: Optional[str],
        content: Union[Dict[str, Any], str],
        **kwargs
    ) -> Dict[str, Any]:
        """Extracts, chunks, and updates vectors (synchronously), managing changes."""
        start_time = time.time()
        logger.info(f"Starting synchronous update for document {doc_id} in tenant {tenant_id}...")
        doc_id_str = str(doc_id)

        added_count = 0
        deleted_count = 0
        updated_count = 0
        operation_errors = []

        try:
            # 1. Prepare new chunks (same as async)
            text = self._prepare_content(content)
            new_chunks = self._chunk_content(text)
            new_chunk_data = {
                self._generate_fingerprint(chunk): {"text": chunk, "order": i}
                for i, chunk in enumerate(new_chunks)
            }
            logger.debug(f"Document {doc_id_str}: Generated {len(new_chunk_data)} new chunks/fingerprints.")

            # 2. Get existing chunks (sync call, no await)
            existing_filter = Filter.by_property("documentId").equal(doc_id_str)
            logger.info(f"Document {doc_id_str}: Attempting to fetch existing objects with tenant_id='{tenant_id}', filter='{existing_filter}'.")
            existing_objects = self._repo.fetch_objects( # Removed await
                collection_name=self.COLLECTION_NAME,
                filters=existing_filter,
                tenant_id=tenant_id,
                return_properties=["chunkFingerprint", "chunkOrder", "title", "workspaceId"],
                limit=10000
            )

            if not existing_objects:
                logger.warning(f"Document {doc_id_str} not found in vector store for update. Treating as creation.")
                if not workspace_id or not title:
                     # This error was previously ValueError, making it consistent with create_vectors_from_content style
                     err_msg = "Cannot create/update document: workspace_id and title are required if the document doesn't exist in vector store."
                     logger.error(f"Document {doc_id_str}: {err_msg}")
                     raise ValueError(err_msg)
                # Call sync create method
                return self.create_vectors_from_content(tenant_id, doc_id, workspace_id, title, content)

            existing_by_fingerprint = {
                obj["properties"].get("chunkFingerprint"): {
                    "uuid": obj["uuid"],
                    "order": obj["properties"].get("chunkOrder"),
                    "title": obj["properties"].get("title"),
                    "workspaceId": obj["properties"].get("workspaceId")
                 }
                for obj in existing_objects if "properties" in obj and obj["properties"].get("chunkFingerprint")
            }
            logger.debug(f"Document {doc_id_str}: Found {len(existing_by_fingerprint)} existing chunks.")

            # Determine definitive title and workspace ID (same as async)
            current_title = title if title is not None else existing_objects[0]["properties"].get("title", "Unknown Title")
            # workspace_id is Optional UUID, but we need string for vector store properties
            current_workspace_id_obj = workspace_id if workspace_id is not None else existing_objects[0]["properties"].get("workspaceId")
            if isinstance(current_workspace_id_obj, UUID):
                current_workspace_id = str(current_workspace_id_obj)
            elif isinstance(current_workspace_id_obj, str): # Already a string (from existing_objects)
                current_workspace_id = current_workspace_id_obj
            else: # Should not happen if workspace_id is UUID or fetched as string
                logger.error(f"Document {doc_id_str}: Could not determine a valid string workspaceId. current_workspace_id_obj: {current_workspace_id_obj}")
                raise ValueError(f"Invalid workspaceId type for document {doc_id_str}")


            # 3. Determine changes (same as async)
            fingerprints_to_add = set(new_chunk_data.keys()) - set(existing_by_fingerprint.keys())
            fingerprints_to_delete = set(existing_by_fingerprint.keys()) - set(new_chunk_data.keys())
            fingerprints_common = set(new_chunk_data.keys()) & set(existing_by_fingerprint.keys())

            objects_to_insert = []
            ids_to_delete = []
            updates_to_perform = [] # List of (uuid, props_to_update) tuples

            # Prepare additions
            for fp in fingerprints_to_add:
                chunk_info = new_chunk_data[fp]
                properties = {
                    "tenantId": tenant_id, "documentId": doc_id_str, "workspaceId": current_workspace_id,
                    "title": current_title, "contentChunk": chunk_info["text"], "chunkOrder": chunk_info["order"],
                    "chunkFingerprint": fp
                }
                objects_to_insert.append(properties)

            # Prepare deletions
            for fp in fingerprints_to_delete:
                ids_to_delete.append(existing_by_fingerprint[fp]["uuid"])

            # Prepare updates
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

                if props_to_update:
                    updates_to_perform.append((existing_by_fingerprint[fp]["uuid"], props_to_update))

            logger.info(f"Document {doc_id_str}: Changes - Add: {len(objects_to_insert)}, Delete: {len(ids_to_delete)}, Update: {len(updates_to_perform)}")

            # 4. Execute changes sequentially
            # Insert
            if objects_to_insert:
                try:
                    insert_result_dict = self._repo.insert_many(self.COLLECTION_NAME, objects_to_insert, tenant_id) # Removed await
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
                    deleted_count = self._repo.delete_many(self.COLLECTION_NAME, where_filter=delete_filter, tenant_id=tenant_id) # Removed await
                except Exception as e:
                    logger.error(f"Delete many task failed for {doc_id_str}: {e}", exc_info=True)
                    operation_errors.append(e)
                    deleted_count = -1 # Indicate deletion failure

            # Update
            for update_uuid, update_props in updates_to_perform:
                try:
                    # UUID must be passed as UUID object or string to weaviate client
                    update_uuid_arg = update_uuid if isinstance(update_uuid, (UUID, str)) else str(update_uuid)
                    success = self._repo.update(self.COLLECTION_NAME, update_uuid_arg, update_props, tenant_id) # Removed await
                    if success:
                        updated_count += 1
                    # Note: repo.update raises exceptions on failure, caught below
                except Exception as e:
                     logger.error(f"Update task failed for chunk {update_uuid} in {doc_id_str}: {e}", exc_info=True)
                     operation_errors.append(e)

            # Final status determination
            status = "success" if not operation_errors else "partial_success" if (added_count > 0 or deleted_count > 0 or updated_count > 0) else "error" # Ensure partial_success only if some operation happened
            message = f"Sync update complete. Added: {added_count}, Deleted: {deleted_count if deleted_count >=0 else 'Error'}, Updated: {updated_count}."
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
                    "deleted": deleted_count if deleted_count >= 0 else "failed", # Adjust reporting
                    "updated": updated_count,
                    "unchanged": len(fingerprints_common) - updated_count, # Approx.
                    "errors": len(operation_errors)
                },
                "error_details": [str(e) for e in operation_errors] if operation_errors else None
            }
        except VectorStoreNotFoundError as e: # Catch specific not found from repo.update or repo.fetch_objects
             logger.warning(f"Sync update failed for doc {doc_id_str} as an object was not found: {e}") # Error from repo
             # This can happen if the object was deleted between fetch and update, or if fetch_objects returns empty but it's not the "no existing objects" path.
             # The original logic for "not existing_objects" (meaning doc not in vector store) leads to create_vectors.
             # This catch is more for unexpected "not found" during individual operations like repo.update.
             # Re-raise to be caught by the generic Exception handler below for consistent error response format.
             raise VectorStoreOperationError(f"Object not found during sync update for {doc_id_str}: {e}") from e
        except ValueError as e: # Catch ValueErrors (e.g., from missing workspace_id/title)
             logger.warning(f"Sync update failed for doc {doc_id_str} due to bad input/state: {e}")
             # Re-raise to be caught by the generic Exception handler for consistent error response.
             raise # Keep as ValueError or re-wrap if preferred
        except Exception as e: # Generic catch-all for unexpected errors, including those re-raised
            logger.error(f"Failed to sync update vectors for document {doc_id}: {e}", exc_info=True)
            # Ensure errors caught during sequential execution are included if main try block fails later
            final_errors = operation_errors + [e] if str(e) not in [str(oe) for oe in operation_errors] else operation_errors # Avoid duplicate error messages
            return {
                "status": "error",
                "message": f"Sync update failed: {e}", # Main error
                "document_id": str(doc_id),
                "stats": { # Provide stats captured so far
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
        """Deletes all vectors associated with a document ID for the Page collection (synchronously)."""
        doc_id_str = str(doc_id)
        logger.info(f"Sync deleting vectors for document {doc_id_str} in tenant {tenant_id}...")
        try:
            where_filter = Filter.by_property("documentId").equal(doc_id_str)
            deleted_count = self._repo.delete_many( # Removed await
                collection_name=self.COLLECTION_NAME,
                where_filter=where_filter,
                tenant_id=tenant_id
            )

            message = f"Successfully deleted {deleted_count} vector chunk(s)." if deleted_count >= 0 else "Deletion check failed."
            logger.info(f"Document {doc_id_str}: {message} (Count: {deleted_count})")
            return {
                "status": "success",
                "message": message,
                "document_id": doc_id_str,
                "chunks_deleted": deleted_count
            }
        except Exception as e:
             logger.error(f"Failed to sync delete vectors for document {doc_id_str}: {e}", exc_info=True)
             return {"status": "error", "message": f"Sync delete failed: {e}", "document_id": doc_id_str}

    def search( # Removed async
        self,
        tenant_id: str,
        query: str,
        limit: int = 10,
        workspace_id: Optional[UUID] = None,
        doc_id: Optional[UUID] = None,
        use_hybrid: bool = False,
        alpha: float = 0.5,
        **kwargs # Keep kwargs for potential future filters
    ) -> List[Dict[str, Any]]:
        """Performs near_text or hybrid search within the Page collection (synchronously)."""
        logger.debug(f"Sync searching collection '{self.COLLECTION_NAME}' in tenant '{tenant_id}' for query: '{query[:50]}...'")
        try:
            # Filter building logic remains the same
            filters = None
            filter_list = []
            if workspace_id:
                 filter_list.append(Filter.by_property("workspaceId").equal(str(workspace_id)))
            if doc_id:
                 filter_list.append(Filter.by_property("documentId").equal(str(doc_id)))
            if len(filter_list) == 1:
                filters = filter_list[0]
            elif len(filter_list) > 1:
                filters = Filter.all_of(filter_list)

            search_method = self._repo.hybrid_search if use_hybrid else self._repo.near_text_search
            search_kwargs = {
                "collection_name": self.COLLECTION_NAME,
                "query": query,
                "limit": limit,
                "filters": filters,
                "tenant_id": tenant_id,
                "return_properties": ["documentId", "title", "contentChunk", "chunkOrder", "workspaceId"],
            }
            if use_hybrid:
                 search_kwargs["alpha"] = alpha
            # else:
                 # Add certainty/distance if needed for near_text

            # Sync call (no await)
            results = search_method(**search_kwargs) # Removed await

            logger.info(f"Sync search returned {len(results)} results for tenant '{tenant_id}'.")
            return results

        except Exception as e:
            logger.error(f"Sync search failed in collection '{self.COLLECTION_NAME}' for tenant '{tenant_id}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Sync search operation failed: {e}") from e