# app/services/weaviate/page_service_async.py
import time
import asyncio
from typing import Dict, Any, List, Optional, Union
from uuid import UUID

from weaviate.collections.classes.filters import Filter

from app.core.logging_config import logger
from .base_vector_service import BaseVectorService
from .repository_async import WeaviateRepositoryAsync
from .exceptions import VectorStoreOperationError, VectorStoreNotFoundError, VectorStoreTenantNotFoundError


class PageVectorServiceAsync(BaseVectorService):
    COLLECTION_NAME = "Page"
    DEFAULT_CHUNK_SIZE = 1000
    DEFAULT_CHUNK_OVERLAP = 200

    def __init__(self, repository: WeaviateRepositoryAsync):
        self._repo = repository
        logger.info(f"{self.__class__.__name__} initialized.")

    async def create_vectors_from_content(
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
                return {"status": "success", "message": "No content to vectorize.", "document_id": str(doc_id),
                        "chunks_processed": 0}

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

            batch_result_dict: Dict[str, Any] = await self._repo.insert_many(
                self.COLLECTION_NAME, objects_to_insert, tenant_id
            )

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

    async def update_vectors_from_content(
            self,
            tenant_id: str,
            doc_id: UUID,
            workspace_id: Optional[UUID],
            title: Optional[str],
            content: Union[Dict[str, Any], str],
            **kwargs
    ) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Starting update for document {doc_id} in tenant {tenant_id}...")
        doc_id_str = str(doc_id)

        try:
            text = self._prepare_content(content)
            new_chunks = self._chunk_content(text)
            new_chunk_data = {
                self._generate_fingerprint(chunk): {"text": chunk, "order": i}
                for i, chunk in enumerate(new_chunks)
            }
            logger.debug(f"Document {doc_id_str}: Generated {len(new_chunk_data)} new chunks/fingerprints.")

            existing_filter = Filter.by_property("documentId").equal(doc_id_str)
            existing_objects = await self._repo.fetch_objects(
                collection_name=self.COLLECTION_NAME,
                filters=existing_filter,
                tenant_id=tenant_id,
                return_properties=["chunkFingerprint", "chunkOrder", "title", "workspaceId"],
                limit=10000
            )

            if not existing_objects:
                logger.warning(f"Document {doc_id_str} not found in vector store for update. Treating as creation.")
                if not workspace_id or not title:
                    raise ValueError(
                        "Cannot create/update document: workspace_id and title are required if the document doesn't exist.")
                return await self.create_vectors_from_content(tenant_id, doc_id, workspace_id, title, content)

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

            current_title = title if title is not None else existing_objects[0]["properties"].get("title",
                                                                                                  "Unknown Title")
            current_workspace_id = str(workspace_id) if workspace_id is not None else existing_objects[0][
                "properties"].get("workspaceId", "Unknown Workspace")

            fingerprints_to_add = set(new_chunk_data.keys()) - set(existing_by_fingerprint.keys())
            fingerprints_to_delete = set(existing_by_fingerprint.keys()) - set(new_chunk_data.keys())
            fingerprints_common = set(new_chunk_data.keys()) & set(existing_by_fingerprint.keys())

            objects_to_insert = []
            ids_to_delete = []
            update_tasks = []

            for fp in fingerprints_to_add:
                chunk_info = new_chunk_data[fp]
                properties = {
                    "tenantId": tenant_id, "documentId": doc_id_str, "workspaceId": current_workspace_id,
                    "title": current_title, "contentChunk": chunk_info["text"], "chunkOrder": chunk_info["order"],
                    "chunkFingerprint": fp
                }
                objects_to_insert.append(properties)

            for fp in fingerprints_to_delete:
                ids_to_delete.append(existing_by_fingerprint[fp]["uuid"])

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
                    update_tasks.append(
                        self._repo.update(self.COLLECTION_NAME, existing_info["uuid"], props_to_update, tenant_id)
                    )

            logger.info(
                f"Document {doc_id_str}: Changes - Add: {len(objects_to_insert)}, Delete: {len(ids_to_delete)}, Update: {len(update_tasks)}")

            insert_result_dict = None
            delete_count_result = 0
            update_results = []

            tasks_to_run = []
            if objects_to_insert:
                tasks_to_run.append(self._repo.insert_many(self.COLLECTION_NAME, objects_to_insert, tenant_id))
            else:
                tasks_to_run.append(asyncio.sleep(0, result=None))

            if ids_to_delete:
                delete_filter = Filter.by_id().contains_any([str(uid) for uid in ids_to_delete])
                tasks_to_run.append(
                    self._repo.delete_many(self.COLLECTION_NAME, where_filter=delete_filter, tenant_id=tenant_id))
            else:
                tasks_to_run.append(asyncio.sleep(0, result=0))

            if update_tasks:
                tasks_to_run.extend(update_tasks)

            all_results = await asyncio.gather(*tasks_to_run, return_exceptions=True)

            insert_result_dict = all_results[0]
            delete_count_result = all_results[1]
            update_results = all_results[2:]

            added_count = 0
            insert_errors = {}
            has_insert_errors = False
            if isinstance(insert_result_dict, dict):
                added_count = insert_result_dict.get("successful", 0)
                insert_errors = insert_result_dict.get("errors", {})
                has_insert_errors = insert_result_dict.get("has_errors", False)
            elif isinstance(insert_result_dict, Exception):
                logger.error(f"Insert many task failed: {insert_result_dict}")
                has_insert_errors = True

            delete_count = delete_count_result if isinstance(delete_count_result, int) else 0
            updated_count = sum(1 for res in update_results if res is True and not isinstance(res, Exception))

            errors = []
            if has_insert_errors:
                if isinstance(insert_result_dict, dict):
                    errors.append(VectorStoreOperationError(f"Insert batch errors: {insert_errors}"))
                elif isinstance(insert_result_dict, Exception):
                    errors.append(insert_result_dict)
            if isinstance(delete_count_result, Exception):
                errors.append(delete_count_result)
            update_errors = [res for res in update_results if isinstance(res, Exception)]
            errors.extend(update_errors)

            status = "success" if not errors else "partial_success" if (
                                                                                   added_count + delete_count + updated_count) > 0 else "error"
            message = f"Update complete. Added: {added_count}, Deleted: {delete_count}, Updated: {updated_count}."
            if errors:
                message += f" Encountered {len(errors)} errors during operations."
                logger.error(f"Document {doc_id_str}: Update operation errors: {[str(e) for e in errors]}")

            elapsed = time.time() - start_time
            logger.info(f"Document {doc_id_str}: Update took {elapsed:.2f}s. Status: {status}")

            return {
                "status": status,
                "message": message,
                "document_id": doc_id_str,
                "stats": {
                    "added": added_count,
                    "deleted": delete_count,
                    "updated": updated_count,
                    "unchanged": len(fingerprints_common) - updated_count,
                    "errors": len(errors)
                },
                "error_details": [str(e) for e in errors] if errors else None
            }
        except VectorStoreNotFoundError as e:
            logger.warning(f"Update failed for doc {doc_id_str}: {e}")
            raise e
        except ValueError as e:
            logger.warning(f"Update failed for doc {doc_id_str} due to bad input: {e}")
            raise e
        except Exception as e:
            logger.error(f"Failed to update vectors for document {doc_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Update failed: {e}", "document_id": str(doc_id)}

    async def delete_vectors(
            self,
            tenant_id: str,
            doc_id: UUID,
            **kwargs
    ) -> Dict[str, Any]:
        doc_id_str = str(doc_id)
        logger.info(f"Deleting vectors for document {doc_id_str} in tenant {tenant_id}...")
        try:
            where_filter = Filter.by_property("documentId").equal(doc_id_str)
            deleted_count = await self._repo.delete_many(
                collection_name=self.COLLECTION_NAME,
                where_filter=where_filter,
                tenant_id=tenant_id
            )
            message = f"Successfully deleted {deleted_count} vector chunk(s)."
            logger.info(f"Document {doc_id_str}: {message} (Count: {deleted_count})")
            return {
                "status": "success",
                "message": message,
                "document_id": doc_id_str,
                "chunks_deleted": deleted_count
            }
        except Exception as e:
            logger.error(f"Failed to delete vectors for document {doc_id_str}: {e}", exc_info=True)
            return {"status": "error", "message": f"Delete failed: {e}", "document_id": doc_id_str}

    async def search(
            self,
            tenant_id: str,
            query: str,
            limit: int = 10,
            workspace_id: Optional[UUID] = None,
            doc_id: Optional[UUID] = None,
            use_hybrid: bool = False,
            alpha: float = 0.5,
    ) -> List[Dict[str, Any]]:
        logger.debug(
            f"Searching Page collection in tenant '{tenant_id}' for query: '{query[:50]}...' "
            f"ws_id: {workspace_id}, doc_id: {doc_id}, hybrid: {use_hybrid}"
        )
        try:
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

            results = await search_method(**search_kwargs)

            logger.info(
                f"Page search returned {len(results)} results for tenant '{tenant_id}'. Filters: {filters}"
            )
            return results
        except VectorStoreTenantNotFoundError as e:
            logger.info(
                f"Search in Page collection for tenant '{tenant_id}' encountered a known missing tenant (returning empty list): {e}"
            )
            return []  # Return empty list instead of re-raising
        except VectorStoreOperationError as e:
            logger.error(
                f"VectorStoreOperationError during Page search for tenant '{tenant_id}': {e}", exc_info=True
            )
            raise  # Re-raise other vector store operational errors
        except Exception as e:
            logger.error(
                f"Unexpected error during Page search for tenant '{tenant_id}': {e}", exc_info=True
            )
            raise VectorStoreOperationError(f"Unexpected error during Page search: {e}") from e