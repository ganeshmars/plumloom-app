# app/services/weaviate/document_service_async.py

from typing import Dict, Any, List, Optional, Union
from uuid import UUID as PyUUID

from .base_vector_service import BaseVectorService
from .repository_async import WeaviateRepositoryAsync
from .exceptions import VectorStoreOperationError, VectorStoreTenantNotFoundError
from weaviate.collections.classes.filters import Filter

from app.core.logging_config import logger


class DocumentVectorServiceAsync(BaseVectorService):
    COLLECTION_NAME = "Document"

    def __init__(self, repository: WeaviateRepositoryAsync):
        self._repo = repository
        logger.info(f"{self.__class__.__name__} initialized.")

    async def create_vectors_from_content(
            self,
            tenant_id: str,
            doc_id: PyUUID,
            workspace_id: PyUUID,
            title: str,
            content: Union[Dict[str, Any], str],
            chat_session_id: Optional[str] = None,
            **kwargs
    ) -> Dict[str, Any]:
        logger.info(f"Creating vectors for Document {doc_id} in workspace {workspace_id}")
        try:
            text = self._prepare_content(content)
            chunks = self._chunk_content(text)

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
                if chat_session_id:
                    properties["chatSessionId"] = str(chat_session_id)

                objects_to_insert.append(properties)

            batch_result_dict = await self._repo.insert_many(self.COLLECTION_NAME, objects_to_insert, tenant_id)

            successful_chunks = batch_result_dict.get("successful", 0)
            failed_chunks = batch_result_dict.get("failed", 0)
            has_errors = batch_result_dict.get("has_errors", False)
            errors = batch_result_dict.get("errors", {})

            status = "success" if not has_errors else "partial_success"
            message = f"Vectorized ~{successful_chunks}/{len(objects_to_insert)} chunks for Document."
            if has_errors:
                message += f" Encountered {failed_chunks} errors."
                logger.error(f"Create vector errors for Document {doc_id}: {errors}")

            return {
                "status": status, "message": message, "document_id": str(doc_id),
                "chunks_processed": len(objects_to_insert),
                "successful_chunks": successful_chunks,
                "failed_chunks": failed_chunks,
                "errors": errors if failed_chunks > 0 else None
            }
        except Exception as e:
            logger.error(f"Failed to create vectors for Document {doc_id}: {e}", exc_info=True)
            return {"status": "error", "message": f"Failed to create vectors: {e}", "document_id": str(doc_id)}

    async def update_vectors_from_content(
            self,
            tenant_id: str,
            doc_id: PyUUID,
            workspace_id: PyUUID,
            title: str,
            content: Union[Dict[str, Any], str],
            chat_session_id: Optional[str] = None,
            **kwargs
    ) -> Dict[str, Any]:
        logger.warning(
            f"Update operation for collection '{self.COLLECTION_NAME}' needs full implementation similar to Page service, considering 'chatSessionId'.")
        return {"status": "not_implemented", "message": "Update not implemented for Document collection yet.",
                "document_id": str(doc_id)}

    async def delete_vectors(
            self,
            tenant_id: str,
            doc_id: PyUUID,
            **kwargs
    ) -> Dict[str, Any]:
        logger.info(f"Deleting vectors for Document {doc_id} in tenant {tenant_id}")
        doc_id_str = str(doc_id)
        try:
            where_filter = Filter.by_property("documentId").equal(doc_id_str)
            deleted_count = await self._repo.delete_many(
                collection_name=self.COLLECTION_NAME,
                where_filter=where_filter,
                tenant_id=tenant_id
            )
            message = f"Successfully deleted {deleted_count} vector chunk(s) for Document."
            return {"status": "success", "message": message, "document_id": doc_id_str, "chunks_deleted": deleted_count}
        except Exception as e:
            logger.error(f"Failed to delete vectors for Document {doc_id_str}: {e}", exc_info=True)
            return {"status": "error", "message": f"Delete failed: {e}", "document_id": doc_id_str}


    async def search(
            self,
            tenant_id: str,
            query: str,
            limit: int = 10,
            workspace_id: Optional[PyUUID] = None,
            doc_id: Optional[PyUUID] = None,
            doc_ids: Optional[List[PyUUID]] = None,
            chat_session_id: Optional[str] = None,
            use_hybrid: bool = False,
            alpha: float = 0.5,
            **kwargs
    ) -> List[Dict[str, Any]]:
        logger.debug(
            f"Searching Document collection in tenant '{tenant_id}' for query: '{query[:50]}...' "
            f"ws_id: {workspace_id}, doc_id(s): {doc_ids or doc_id}, chat_session: {chat_session_id}, hybrid: {use_hybrid}"
        )
        try:
            active_filters: List[Filter] = []

            if workspace_id:
                active_filters.append(Filter.by_property("workspaceId").equal(str(workspace_id)))

            if doc_id:
                active_filters.append(Filter.by_property("documentId").equal(str(doc_id)))
            elif doc_ids:
                if len(doc_ids) == 1:
                    active_filters.append(Filter.by_property("documentId").equal(str(doc_ids[0])))
                elif len(doc_ids) > 1:
                    str_doc_ids = [str(d_id) for d_id in doc_ids]
                    active_filters.append(Filter.by_property("documentId").contains_any(str_doc_ids))

            if chat_session_id:
                active_filters.append(Filter.by_property("chatSessionId").equal(str(chat_session_id)))

            final_filter = None
            if len(active_filters) == 1:
                final_filter = active_filters[0]
            elif len(active_filters) > 1:
                final_filter = Filter.all_of(active_filters)

            search_method = self._repo.hybrid_search if use_hybrid else self._repo.near_text_search
            search_kwargs = {
                "collection_name": self.COLLECTION_NAME, "query": query, "limit": limit,
                "filters": final_filter, "tenant_id": tenant_id,
                "return_properties": ["documentId", "title", "contentChunk", "chunkOrder", "workspaceId",
                                      "chatSessionId"],
            }
            if use_hybrid:
                search_kwargs["alpha"] = alpha

            results = await search_method(**search_kwargs)
            logger.info(
                f"Document search returned {len(results)} results for tenant '{tenant_id}'. Filters: {final_filter}"
            )
            return results
        except VectorStoreTenantNotFoundError as e:
            logger.info(
                f"Search in Document collection for tenant '{tenant_id}' encountered a known missing tenant (returning empty list): {e}"
            )
            return [] # Return empty list instead of re-raising
        except VectorStoreOperationError as e:
            logger.error(
                f"VectorStoreOperationError during Document search for tenant '{tenant_id}': {e}", exc_info=True
            )
            raise # Re-raise other vector store operational errors
        except Exception as e:
            logger.error(
                f"Unexpected error during Document search for tenant '{tenant_id}': {e}", exc_info=True
            )
            raise VectorStoreOperationError(f"Unexpected error during Document search: {e}") from e