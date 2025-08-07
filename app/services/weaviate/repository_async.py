# app/services/weaviate/repository_async.py

import asyncio
from typing import List, Dict, Any, Optional
from uuid import UUID

from weaviate.collections.classes.filters import Filter
from weaviate.collections.classes.grpc import Move
from weaviate.collections.classes.types import Properties
from weaviate.collections.classes.batch import BatchObjectReturn

from .repository_sync import WeaviateRepositorySync # Import the sync version
from app.core.weaviate_client import get_client # To pass to sync repo if needed

from app.core.logging_config import logger

class WeaviateRepositoryAsync:
    """
    Asynchronous wrapper for Weaviate interactions.
    Uses asyncio.to_thread to run synchronous Weaviate client calls
    in a separate thread, preventing blocking of the main FastAPI event loop.
    """

    def __init__(self, sync_repository: Optional[WeaviateRepositorySync] = None):
        # Inject sync repo or create a new one
        self._repo_sync = sync_repository or WeaviateRepositorySync(client=get_client())
        logger.info(f"{self.__class__.__name__} initialized.")

    async def insert(self, collection_name: str, properties: Properties, tenant_id: Optional[str] = None, vector: Optional[List[float]] = None) -> UUID:
        return await asyncio.to_thread(
            self._repo_sync.insert, collection_name, properties, tenant_id, vector
        )

    async def insert_many(self, collection_name: str, objects: List[Properties], tenant_id: Optional[str] = None) -> Dict[str, Any]:
         # Batch operations in the client might already be somewhat optimized,
         # but running the whole batch submission in a thread is safest for the event loop.
        return await asyncio.to_thread(
            self._repo_sync.insert_many, collection_name, objects, tenant_id
        )

    async def update(self, collection_name: str, uuid: UUID, properties: Properties, tenant_id: Optional[str] = None, vector: Optional[List[float]] = None) -> bool:
        return await asyncio.to_thread(
            self._repo_sync.update, collection_name, uuid, properties, tenant_id, vector
        )

    async def delete_by_id(self, collection_name: str, uuid: UUID, tenant_id: Optional[str] = None) -> bool:
        return await asyncio.to_thread(
            self._repo_sync.delete_by_id, collection_name, uuid, tenant_id
        )

    async def delete_many(self, collection_name: str, where_filter: Filter, tenant_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(
            self._repo_sync.delete_many, collection_name, where_filter, tenant_id
        )

    async def fetch_by_id(self, collection_name: str, uuid: UUID, tenant_id: Optional[str] = None, include_vector: bool = False) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._repo_sync.fetch_by_id, collection_name, uuid, tenant_id, include_vector
        )

    async def fetch_objects(self, collection_name: str, filters: Optional[Filter] = None, limit: Optional[int] = None, sort: Optional[Any] = None, tenant_id: Optional[str] = None, include_vector: bool = False, return_properties: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._repo_sync.fetch_objects, collection_name, filters, limit, sort, tenant_id, include_vector, return_properties
        )

    async def near_text_search(self, collection_name: str, query: str, filters: Optional[Filter] = None, limit: int = 10, tenant_id: Optional[str] = None, return_properties: Optional[List[str]] = None, include_vector: bool = False, certainty: Optional[float] = None, distance: Optional[float] = 0.75, move_to: Optional[Move] = None, move_away: Optional[Move] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._repo_sync.near_text_search, collection_name, query, filters, limit, tenant_id, return_properties, include_vector, certainty, distance, move_to, move_away
        )

    async def hybrid_search(self, collection_name: str, query: str, filters: Optional[Filter] = None, limit: int = 10, alpha: float = 0.5, tenant_id: Optional[str] = None, return_properties: Optional[List[str]] = None, include_vector: bool = False, query_properties: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._repo_sync.hybrid_search, collection_name, query, filters, limit, alpha, tenant_id, return_properties, include_vector, query_properties
        )