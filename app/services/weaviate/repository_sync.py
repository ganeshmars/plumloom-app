# app/services/weaviate/repository_sync.py
import logging
from typing import List, Dict, Any, Optional, Union
from uuid import UUID

from weaviate import WeaviateClient
from weaviate.collections import Collection
from weaviate.collections.classes.filters import Filter
from weaviate.collections.classes.grpc import Move
from weaviate.collections.classes.types import Properties
from weaviate.exceptions import WeaviateQueryError, UnexpectedStatusCodeError

from app.core.logging_config import logger
from app.core.weaviate_client import get_client
from .exceptions import VectorStoreOperationError, VectorStoreNotFoundError, VectorStoreTenantNotFoundError
import httpx
import httpcore


class WeaviateRepositorySync:
    """Synchronous low-level repository for Weaviate interactions."""

    def __init__(self, client: Optional[WeaviateClient] = None):
        self._client = client or get_client()
        if not self._client:
            logger.critical("Weaviate client is None after attempting to get/initialize it.")
            raise RuntimeError("Weaviate client not initialized. Ensure Weaviate is configured and reachable.")
        logger.info(f"{self.__class__.__name__} initialized with client: {type(self._client)}")

    def _get_collection(self, collection_name: str, tenant_id: Optional[str] = None) -> Collection:
        try:
            collection = self._client.collections.get(collection_name)
            if tenant_id:
                return collection.with_tenant(tenant=tenant_id)
            return collection
        except Exception as e:
            logger.error(f"Failed to get collection '{collection_name}' (tenant: {tenant_id}): {e}", exc_info=True)
            raise VectorStoreOperationError(f"Could not access collection '{collection_name}'") from e

    def insert(self, collection_name: str, properties: Properties, tenant_id: Optional[str] = None,
               vector: Optional[List[float]] = None) -> UUID:
        collection = self._get_collection(collection_name, tenant_id)
        try:
            uuid_inserted = collection.data.insert(properties=properties, vector=vector)
            logger.debug(f"Inserted object into '{collection_name}' with UUID: {uuid_inserted}")
            return uuid_inserted
        except Exception as e:
            logger.error(f"Failed to insert object into '{collection_name}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Insert failed for '{collection_name}'") from e

    def insert_many(self, collection_name: str, objects: List[Properties], tenant_id: Optional[str] = None) -> Dict[
        str, Any]:
        collection = self._get_collection(collection_name, tenant_id)
        try:
            logger.debug(
                f"Starting batch insert for {len(objects)} objects into '{collection_name}' (tenant: {tenant_id}).")

            batch_return_summary = collection.data.insert_many(objects)

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
                successful_count = len(objects)

            logger.info(
                f"Batch insert summary for '{collection_name}': Attempted: {len(objects)}, Successful: {successful_count}, Failed: {failed_count}.")
            if has_errors_flag:
                logger.error(f"Batch insert errors dictionary in '{collection_name}': {all_errors_dict}")

            return {
                "successful": successful_count,
                "failed": failed_count,
                "errors": all_errors_dict,
                "has_errors": has_errors_flag
            }

        except (httpx.TimeoutException, httpcore.TimeoutException) as timeout_err:
            logger.error(f"Timeout occurred during batch operation for '{collection_name}': {timeout_err}",
                         exc_info=True)
            raise VectorStoreOperationError(f"Timeout during batch operation for '{collection_name}'.") from timeout_err
        except Exception as e:
            if not (isinstance(e, VectorStoreOperationError) and "Timeout during batch operation" in str(e)):
                logger.error(f"Failed batch insert into '{collection_name}': {e}", exc_info=True)
                raise VectorStoreOperationError(f"Batch insert failed for '{collection_name}'") from e
            else:
                raise

    def update(self, collection_name: str, uuid: Union[UUID, str], properties: Properties,
               tenant_id: Optional[str] = None, vector: Optional[List[float]] = None) -> bool:
        collection = self._get_collection(collection_name, tenant_id)
        try:
            collection.data.update(uuid=uuid, properties=properties, vector=vector)
            logger.debug(f"Updated object {uuid} in '{collection_name}'.")
            return True
        except UnexpectedStatusCodeError as e:
            if e.status_code == 404:
                logger.warning(f"Object {uuid} not found in '{collection_name}' for update.")
                raise VectorStoreNotFoundError(f"Object {uuid} not found in '{collection_name}'") from e
            logger.error(f"Failed to update object {uuid} in '{collection_name}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Update failed for {uuid} in '{collection_name}'") from e
        except Exception as e:
            logger.error(f"Failed to update object {uuid} in '{collection_name}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Update failed for {uuid} in '{collection_name}'") from e

    def delete_by_id(self, collection_name: str, uuid: Union[UUID, str], tenant_id: Optional[str] = None) -> bool:
        collection = self._get_collection(collection_name, tenant_id)
        try:
            collection.data.delete_by_id(uuid=uuid)
            logger.debug(f"Deleted object {uuid} from '{collection_name}'.")
            return True
        except UnexpectedStatusCodeError as e:
            if e.status_code == 404:
                logger.warning(f"Object {uuid} not found in '{collection_name}' for deletion, or already deleted.")
                return False
            logger.error(f"Failed to delete object {uuid} from '{collection_name}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Delete failed for {uuid} in '{collection_name}'") from e
        except Exception as e:
            logger.error(f"Unexpected error deleting object {uuid} from '{collection_name}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Delete failed for {uuid} in '{collection_name}'") from e

    def delete_many(self, collection_name: str, where_filter: Filter, tenant_id: Optional[str] = None) -> int:
        collection = self._get_collection(collection_name, tenant_id)
        try:
            result = collection.data.delete_many(where=where_filter)
            deleted_count = result.successful
            failed_count = result.failed
            logger.info(
                f"Delete many from '{collection_name}': Matched filter. Successful: {deleted_count}, Failed: {failed_count}")
            if failed_count > 0:
                failure_details = [f"UUID: {obj.uuid}, Error: {obj.errors.messages if obj.errors else 'Unknown'}" for
                                   obj in result.objects if obj.errors]
                logger.error(
                    f"Some deletions failed in '{collection_name}' for filter {where_filter}. Failures: {failed_count}. Details: {failure_details}")
            return deleted_count
        except Exception as e:
            logger.error(f"Failed to delete many objects from '{collection_name}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Delete many failed for '{collection_name}'") from e

    def fetch_by_id(self, collection_name: str, uuid: Union[UUID, str], tenant_id: Optional[str] = None,
                    include_vector: bool = False) -> Optional[Dict[str, Any]]:
        collection = self._get_collection(collection_name, tenant_id)
        try:
            obj = collection.query.fetch_object_by_id(
                uuid=uuid,
                include_vector=include_vector
            )
            if obj:
                logger.debug(f"Fetched object {obj.uuid} from '{collection_name}'.")
                vector_data = None
                if include_vector and obj.vector:
                    vector_data = obj.vector.get('default') if isinstance(obj.vector, dict) else obj.vector

                return {
                    "uuid": str(obj.uuid),
                    "properties": obj.properties,
                    "vector": vector_data
                }
            else:
                logger.warning(f"Object {uuid} not found in '{collection_name}'.")
                return None
        except UnexpectedStatusCodeError as e:
            if e.status_code == 404:
                logger.warning(f"Object {uuid} not found in '{collection_name}' (status code 404).")
                return None
            logger.error(f"Failed to fetch object {uuid} from '{collection_name}' due to unexpected status: {e}",
                         exc_info=True)
            raise VectorStoreOperationError(
                f"Fetch by ID failed for {uuid} in '{collection_name}' due to status code {e.status_code}") from e
        except Exception as e:
            logger.error(f"Failed to fetch object {uuid} from '{collection_name}': {e}", exc_info=True)
            raise VectorStoreOperationError(f"Fetch by ID failed for {uuid} in '{collection_name}'") from e

    def fetch_objects(self, collection_name: str, filters: Optional[Filter] = None, limit: Optional[int] = None,
                      sort: Optional[Any] = None, tenant_id: Optional[str] = None, include_vector: bool = False,
                      return_properties: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        collection = self._get_collection(collection_name, tenant_id)
        response = None  # Initialize response
        try:
            logger.debug(
                f"Fetching objects from '{collection_name}' with tenant_id='{tenant_id}'. "
                f"Filters: {str(filters)}. Limit: {limit}. Sort: {str(sort)}. "
                f"Return_properties: {return_properties}. Include_vector: {include_vector}."
            )
            response = collection.query.fetch_objects(
                filters=filters,
                limit=limit,
                sort=sort,
                include_vector=include_vector,
                return_properties=return_properties
            )

            # This block will only be reached if collection.query.fetch_objects succeeds
            if response and response.objects is not None:
                logger.debug(f"Successfully fetched {len(response.objects)} objects from '{collection_name}'.")
            elif response:
                logger.warning(
                    f"Fetch_objects call for '{collection_name}' (tenant: {tenant_id}) returned a response, but response.objects is None. Raw response: {response}")
                return []  # Should ideally not happen if call succeeded but no objects
            else:
                logger.warning(
                    f"Fetch_objects call for '{collection_name}' (tenant: {tenant_id}) returned a None response object.")
                return []

            results_list = []
            if response.objects:
                for obj in response.objects:
                    vector_data = None
                    if include_vector and obj.vector:
                        vector_data = obj.vector.get('default') if isinstance(obj.vector, dict) else obj.vector

                    results_list.append({
                        "uuid": str(obj.uuid),
                        "properties": obj.properties,
                        "vector": vector_data
                    })
            return results_list

        except WeaviateQueryError as e:
            err_msg = str(e)
            err_msg_lower = err_msg.lower()
            if tenant_id and "tenant not found" in err_msg_lower:
                logger.warning(
                    f"Tenant '{tenant_id}' reported as not found by Weaviate during fetch_objects for collection '{collection_name}'. "
                    f"This is normal for new users or tenants. Returning empty list. Filters: {str(filters)}"
                )
                return []
            else:
                logger.error(
                    f"Weaviate query error during fetch_objects for '{collection_name}' (tenant: {tenant_id}, filters: {str(filters)}, limit: {limit}). Error: {e}",
                    exc_info=True
                )
                # Re-raise as a more generic VectorStoreOperationError, including the original Weaviate error message
                raise VectorStoreOperationError(f"Fetch objects query failed for '{collection_name}': {e}") from e
        except UnexpectedStatusCodeError as e:
            if e.status_code == 404 and tenant_id:
                logger.warning(
                    f"Fetch objects encountered 404 for collection '{collection_name}' (tenant: {tenant_id}, filters: {str(filters)}). This might indicate the tenant does not exist or has no data. Returning empty list."
                )
                return []
            logger.error(
                f"Failed to fetch objects from '{collection_name}' (tenant: {tenant_id}, filters: {str(filters)}, limit: {limit}) due to unexpected status: {e}",
                exc_info=True
            )
            raise VectorStoreOperationError(
                f"Fetch objects failed for '{collection_name}' due to status code {e.status_code}") from e
        except Exception as e:  # Catch any other unexpected exceptions
            logger.error(
                f"Unexpected generic error during fetch_objects from '{collection_name}' (tenant: {tenant_id}, filters: {str(filters)}, limit: {limit}). Error: {e}",
                exc_info=True
            )
            raise VectorStoreOperationError(
                f"Fetch objects failed for '{collection_name}' with an unexpected error: {e}") from e

    def near_text_search(self, collection_name: str, query: str, filters: Optional[Filter] = None, limit: int = 10,
                         tenant_id: Optional[str] = None, return_properties: Optional[List[str]] = None,
                         include_vector: bool = False, certainty: Optional[float] = None,
                         distance: Optional[float] = None, move_to: Optional[Move] = None,
                         move_away: Optional[Move] = None) -> List[Dict[str, Any]]:
        collection = self._get_collection(collection_name, tenant_id)
        logger.debug(
            f"Executing near_text_search in '{collection_name}' for tenant '{tenant_id}'. Query: '{query[:100]}...', Limit: {limit}, Certainty: {certainty}, Distance: {distance}")
        try:
            response = collection.query.near_text(
                query=query,
                filters=filters,
                limit=limit,
                return_properties=return_properties,
                include_vector=include_vector,
                certainty=certainty,
                distance=distance,
                move_to=move_to,
                move_away=move_away,
                return_metadata=["certainty", "distance"]
            )
            logger.debug(
                f"Near text search in '{collection_name}' (tenant: {tenant_id}) returned {len(response.objects)} raw results.")
            results = []
            for i, obj in enumerate(response.objects):
                logger.debug(f"NearText - Object {i} UUID {obj.uuid} metadata: {obj.metadata}")

                result_item = {
                    "uuid": str(obj.uuid),
                    "properties": obj.properties,
                }
                vector_data = None
                if include_vector and obj.vector:
                    vector_data = obj.vector.get('default') if isinstance(obj.vector, dict) else obj.vector
                result_item["vector"] = vector_data

                if obj.metadata:
                    if obj.metadata.certainty is not None:
                        result_item["certainty"] = obj.metadata.certainty
                    if obj.metadata.distance is not None:
                        result_item["distance"] = obj.metadata.distance
                results.append(result_item)
            if results:
                logger.debug(
                    f"NearText - First result preview from '{collection_name}' (tenant: {tenant_id}): {str(results[0])[:200]}")
            else:
                logger.debug(f"NearText - No results to preview from '{collection_name}' (tenant: {tenant_id})")
            return results
        except WeaviateQueryError as e:
            err_msg_lower = str(e).lower()
            # More specific check for gRPC tenant not found in near_text as well
            if tenant_id and "explorer: get class" in err_msg_lower and "tenant not found" in err_msg_lower and f'"{tenant_id}"' in err_msg_lower:
                logger.warning(
                    f"Tenant '{tenant_id}' reported as not found by Weaviate (gRPC detail) during near_text_search for collection '{collection_name}'. Query: '{query[:50]}...'. Returning empty list. Original error: {e}"
                )
                return []  # Or raise VectorStoreTenantNotFoundError if that's preferred for search
            elif tenant_id and ("tenant" in err_msg_lower and (
                    "not found" in err_msg_lower or "does not exist" in err_msg_lower or f"tenant {tenant_id} not found" in err_msg_lower)):  # General tenant not found
                logger.warning(
                    f"Tenant not found during near_text_search in '{collection_name}' (Tenant: {tenant_id}). Query: '{query[:50]}...'. Details: {e}"
                )
                raise VectorStoreTenantNotFoundError(  # This is a specific error we want to propagate for search
                    f"Tenant '{tenant_id}' not found in '{collection_name}' for near_text_search."
                ) from e
            else:
                logger.error(
                    f"Weaviate query error during near_text_search in '{collection_name}' (Tenant: {tenant_id}). Query: '{query[:50]}...'. Error: {e}",
                    exc_info=True
                )
                raise VectorStoreOperationError(
                    f"Near text search query failed for '{collection_name}': {e}"
                ) from e
        except Exception as e:
            logger.error(
                f"Unexpected error during near_text_search in '{collection_name}' (Tenant: {tenant_id}). Query: '{query[:50]}...'. Error: {e}",
                exc_info=True
            )
            raise VectorStoreOperationError(
                f"Near text search operation failed for '{collection_name}': {e}"
            ) from e

    def hybrid_search(self, collection_name: str, query: str, filters: Optional[Filter] = None, limit: int = 10,
                      alpha: float = 0.5, tenant_id: Optional[str] = None,
                      return_properties: Optional[List[str]] = None, include_vector: bool = False,
                      query_properties: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        collection = self._get_collection(collection_name, tenant_id)
        logger.debug(
            f"Executing hybrid_search in '{collection_name}' for tenant '{tenant_id}'. Query: '{query[:100]}...', Alpha: {alpha}, Limit: {limit}")
        try:
            response = collection.query.hybrid(
                query=query,
                filters=filters,
                limit=limit,
                alpha=alpha,
                return_properties=return_properties,
                include_vector=include_vector,
                query_properties=query_properties,
                return_metadata=["score", "explain_score"]
            )
            logger.debug(
                f"Hybrid search in '{collection_name}' (tenant: {tenant_id}) returned {len(response.objects)} raw results.")
            results = []
            for i, obj in enumerate(response.objects):
                logger.debug(f"Hybrid - Object {i} UUID {obj.uuid} metadata: {obj.metadata}")

                result_item = {
                    "uuid": str(obj.uuid),
                    "properties": obj.properties,
                }
                vector_data = None
                if include_vector and obj.vector:
                    vector_data = obj.vector.get('default') if isinstance(obj.vector, dict) else obj.vector
                result_item["vector"] = vector_data

                if obj.metadata and obj.metadata.score is not None:
                    result_item["score"] = obj.metadata.score
                results.append(result_item)

            if results:
                logger.debug(
                    f"Hybrid - First result preview from '{collection_name}' (tenant: {tenant_id}): {str(results[0])[:200]}")
            else:
                logger.debug(f"Hybrid - No results to preview from '{collection_name}' (tenant: {tenant_id})")
            return results
        except WeaviateQueryError as e:
            err_msg_lower = str(e).lower()
            # More specific check for gRPC tenant not found in hybrid_search as well
            if tenant_id and "explorer: get class" in err_msg_lower and "tenant not found" in err_msg_lower and f'"{tenant_id}"' in err_msg_lower:
                logger.warning(
                    f"Tenant '{tenant_id}' reported as not found by Weaviate (gRPC detail) during hybrid_search for collection '{collection_name}'. Query: '{query[:50]}...'. Returning empty list. Original error: {e}"
                )
                return []  # Or raise VectorStoreTenantNotFoundError
            elif tenant_id and ("tenant" in err_msg_lower and (
                    "not found" in err_msg_lower or "does not exist" in err_msg_lower or f"tenant {tenant_id} not found" in err_msg_lower)):  # General tenant not found
                logger.warning(
                    f"Tenant not found during hybrid_search in '{collection_name}' (Tenant: {tenant_id}). Query: '{query[:50]}...'. Details: {e}"
                )
                raise VectorStoreTenantNotFoundError(  # Propagate for search
                    f"Tenant '{tenant_id}' not found in '{collection_name}' for hybrid_search."
                ) from e
            else:
                logger.error(
                    f"Weaviate query error during hybrid_search in '{collection_name}' (Tenant: {tenant_id}). Query: '{query[:50]}...'. Error: {e}",
                    exc_info=True
                )
                raise VectorStoreOperationError(
                    f"Hybrid search query failed for '{collection_name}': {e}"
                ) from e
        except Exception as e:
            logger.error(
                f"Unexpected error during hybrid_search in '{collection_name}' (Tenant: {tenant_id}). Query: '{query[:50]}...'. Error: {e}",
                exc_info=True
            )
            raise VectorStoreOperationError(
                f"Hybrid search operation failed for '{collection_name}': {e}"
            ) from e