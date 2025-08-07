from abc import ABC
from app.core.weaviate_client import get_client, init_weaviate_sync
from weaviate.classes import config as wc
from app.core.logging_config import logger


class WeaviateService(ABC):
    def __init__(self):
        logger.info(f"Initializing {self.__class__.__name__}")
        self._client = init_weaviate_sync()

    def close(self):
        """Close the client connection"""
        self._client = None

    @property
    def client(self):
        """Get the Weaviate client"""
        return get_client()

    async def __aenter__(self):
        """Support for async context manager"""
        self._client = get_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup when exiting async context"""
        self._client = None
