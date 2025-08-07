# app/services/weaviate/base_vector_service.py

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Union, Optional
from uuid import UUID

from app.utils.extract_text import extract_text_from_json
from app.utils.chunk_text import chunk_text
from app.utils.text_processing import generate_chunk_fingerprint

from app.core.logging_config import logger

class BaseVectorService(ABC):
    """Abstract base class for collection-specific vector services."""

    # To be defined by subclasses
    COLLECTION_NAME: str
    DEFAULT_CHUNK_SIZE: int = 1000
    DEFAULT_CHUNK_OVERLAP: int = 200

    # Subclasses will hold instances of the appropriate repository (sync or async)
    # Example: self._repo: WeaviateRepositoryAsync | WeaviateRepositorySync

    @abstractmethod
    async def create_vectors_from_content(self, tenant_id: str, doc_id: UUID, workspace_id: UUID, title: str, content: Union[Dict[str, Any], str], **kwargs) -> Dict[str, Any]:
        """Extract, chunk, and create vectors for content."""
        pass

    @abstractmethod
    async def update_vectors_from_content(self, tenant_id: str, doc_id: UUID, workspace_id: UUID, title: str, content: Union[Dict[str, Any], str], **kwargs) -> Dict[str, Any]:
        """Extract, chunk, and update vectors, managing changes."""
        pass

    @abstractmethod
    async def delete_vectors(self, tenant_id: str, doc_id: UUID, **kwargs) -> Dict[str, Any]:
        """Delete all vectors associated with a document ID."""
        pass

    @abstractmethod
    async def search(self, tenant_id: str, query: str, limit: int = 10, **kwargs) -> List[Dict[str, Any]]:
        """Perform a search (e.g., near_text or hybrid) within the collection."""
        pass

    # --- Helper Methods (can be shared) ---

    def _prepare_content(self, content: Union[Dict[str, Any], str]) -> str:
        """Extracts text from various content formats."""
        if isinstance(content, str):
            # Assume plain text or pre-extracted
            return content
        elif isinstance(content, dict):
             # Assume TipTap JSON or similar structure
            try:
                return extract_text_from_json(content)
            except Exception as e:
                logger.error(f"Failed to extract text from dict content: {e}")
                raise ValueError("Invalid content structure for text extraction") from e
        else:
             raise TypeError(f"Unsupported content type: {type(content)}")

    def _chunk_content(self, text: str) -> List[str]:
        """Chunks the extracted text."""
        if not text:
            return []
        return chunk_text(text, self.DEFAULT_CHUNK_SIZE, self.DEFAULT_CHUNK_OVERLAP)

    def _generate_fingerprint(self, chunk_text: str) -> str:
        """Generates a fingerprint for a chunk."""
        return generate_chunk_fingerprint(chunk_text)