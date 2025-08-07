from abc import ABC, abstractmethod
from typing import Dict, Any, List, Union
from uuid import UUID
from sqlalchemy import text, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.extract_text import extract_text_from_json
from app.models.document import Document
from app.services.storage_service import StorageService

import logging
logger = logging.getLogger(__name__)

class BaseDocumentIndexingService(ABC):
    """Base class for document indexing with common functionality."""
    
    def __init__(self, db: Union[Session, AsyncSession]):
        self.db = db
        self.storage_service = StorageService()

    @abstractmethod
    def index_document(self, doc_id: UUID, content: Dict[str, Any]) -> None:
        """Index a document's content for search."""
        pass

    @abstractmethod
    def reindex_all_documents(self) -> None:
        """Reindex all documents in the database."""
        pass

    @abstractmethod
    def search_documents(
        self,
        query: str,
        workspace_id: UUID,
        limit: int = 10,
        offset: int = 0
    ) -> List[Document]:
        """Search documents using PostgreSQL full-text search."""
        pass


class SyncDocumentIndexingService(BaseDocumentIndexingService):
    """Synchronous implementation for Celery tasks."""
    
    def __init__(self, db: Session):
        super().__init__(db)

    def index_document(self, doc_id: UUID, content: Dict[str, Any]) -> None:
        try:
            # Extract text from content
            extracted_text = extract_text_from_json(content)

            # Update document with extracted text
            query = text("""
                UPDATE documents 
                SET content_text = :text
                WHERE document_id = :doc_id
            """)
            
            self.db.execute(
                query,
                {"text": extracted_text, "doc_id": doc_id}
            )
            
            self.db.commit()

        except Exception as e:
            self.db.rollback()
            raise Exception(f"Failed to index document {doc_id}: {str(e)}")

    def reindex_all_documents(self) -> None:
        try:
            documents = self.db.query(Document).filter(
                Document.content_file_path.isnot(None)
            ).all()

            for doc in documents:
                content = self.storage_service.get_json_sync(doc.content_file_path)
                if content:
                    self.index_document(doc.document_id, content)

        except Exception as e:
            self.db.rollback()
            raise Exception(f"Failed to reindex documents: {str(e)}")

    def search_documents(
        self,
        query: str,
        workspace_id: UUID,
        limit: int = 10,
        offset: int = 0
    ) -> List[Document]:
        try:
            search_query = text("""
                SELECT * FROM documents
                WHERE 
                    workspace_id = :workspace_id
                    AND search_vector @@ plainto_tsquery('english', :query)
                ORDER BY ts_rank(search_vector, plainto_tsquery('english', :query)) DESC
                LIMIT :limit OFFSET :offset
            """)

            result = self.db.execute(
                search_query,
                {
                    "workspace_id": workspace_id,
                    "query": query,
                    "limit": limit,
                    "offset": offset
                }
            )

            return result.scalars().all()

        except Exception as e:
            raise Exception(f"Failed to search documents: {str(e)}")


class AsyncDocumentIndexingService(BaseDocumentIndexingService):
    """Asynchronous implementation for API endpoints."""
    
    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def index_document(self, doc_id: UUID, content: Dict[str, Any]) -> None:
        try:
            # Extract text from content
            extracted_text = extract_text_from_json(content)

            # Update document with extracted text
            query = text("""
                UPDATE documents 
                SET content_text = :text
                WHERE document_id = :doc_id
            """)
            
            await self.db.execute(
                query,
                {"text": extracted_text, "doc_id": doc_id}
            )
            
            await self.db.commit()

        except Exception as e:
            await self.db.rollback()
            raise Exception(f"Failed to index document {doc_id}: {str(e)}")

    async def reindex_all_documents(self) -> None:
        try:
            result = await self.db.execute(
                select(Document).where(Document.content_file_path.isnot(None))
            )
            documents = result.scalars().all()

            for doc in documents:
                content = await self.storage_service.get_json(doc.content_file_path)
                if content:
                    await self.index_document(doc.document_id, content)

        except Exception as e:
            await self.db.rollback()
            raise Exception(f"Failed to reindex documents: {str(e)}")

    async def search_documents(
        self,
        query: str,
        workspace_id: UUID,
        limit: int = 10,
        offset: int = 0
    ) -> List[Document]:
        try:
            search_query = text("""
                SELECT * FROM documents
                WHERE 
                    workspace_id = :workspace_id
                    AND search_vector @@ plainto_tsquery('english', :query)
                ORDER BY ts_rank(search_vector, plainto_tsquery('english', :query)) DESC
                LIMIT :limit OFFSET :offset
            """)

            result = await self.db.execute(
                search_query,
                {
                    "workspace_id": workspace_id,
                    "query": query,
                    "limit": limit,
                    "offset": offset
                }
            )

            return result.scalars().all()

        except Exception as e:
            raise Exception(f"Failed to search documents: {str(e)}")

    def index_document(self, doc_id: UUID, content: Dict[str, Any]) -> None:
        """
        Index a document's content for search.
        This extracts text and updates both content_text and search_vector columns.
        """
        try:
            # Extract text from content
            extracted_text = extract_text_from_json(content)

            # Update document with extracted text
            # Note: search_vector will be updated automatically by the trigger
            query = text("""
                UPDATE documents 
                SET content_text = :text
                WHERE document_id = :doc_id
            """)
            
            self.db.execute(
                query,
                {"text": extracted_text, "doc_id": doc_id}
            )
            
            self.db.commit()

        except Exception as e:
            self.db.rollback()
            raise Exception(f"Failed to index document {doc_id}: {str(e)}")

    def reindex_all_documents(self) -> None:
        """
        Reindex all documents in the database.
        Useful when search schema changes or for bulk updates.
        """
        try:
            # Get all documents that have content
            documents = self.db.query(Document).filter(
                Document.content_file_path.isnot(None)
            ).all()

            for doc in documents:
                # Get content from storage and index it
                content = self._get_document_content(doc.content_file_path)
                if content:
                    self.index_document(doc.document_id, content)

        except Exception as e:
            self.db.rollback()
            raise Exception(f"Failed to reindex documents: {str(e)}")

    def search_documents(
        self, 
        query: str, 
        workspace_id: UUID,
        limit: int = 10,
        offset: int = 0
    ) -> list[Document]:
        """
        Search documents using PostgreSQL full-text search.
        """
        try:
            search_query = text("""
                SELECT * FROM documents
                WHERE 
                    workspace_id = :workspace_id
                    AND search_vector @@ plainto_tsquery('english', :query)
                ORDER BY ts_rank(search_vector, plainto_tsquery('english', :query)) DESC
                LIMIT :limit OFFSET :offset
            """)

            result = self.db.execute(
                search_query,
                {
                    "workspace_id": workspace_id,
                    "query": query,
                    "limit": limit,
                    "offset": offset
                }
            )

            return result.scalars().all()

        except Exception as e:
            raise Exception(f"Failed to search documents: {str(e)}")

    def _get_document_content(self, content_file_path: str) -> Dict[str, Any]:
        """
        Get document content from GCS storage.
        """
        try:
            return self.storage_service.get_json_sync(content_file_path)
        except Exception as e:
            logger.error(f"Failed to get document content from storage: {str(e)}")
            return None
