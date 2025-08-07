"""Service for hybrid search combining PostgreSQL and Weaviate."""

import logging
from typing import Dict, Any, List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.services.document_indexing_service import AsyncDocumentIndexingService
from app.services.vector_services_v2 import VectorService

logger = logging.getLogger(__name__)

class HybridSearchService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.indexing_service = AsyncDocumentIndexingService(db)
        self.vector_service = VectorService()

    async def search_documents(
        self,
        query: str,
        workspace_id: UUID,
        limit: int = 10,
        offset: int = 0,
        hybrid_weight: float = 0.5  # 0.0 = full-text only, 1.0 = vector only
    ) -> List[Dict[str, Any]]:
        """
        Perform hybrid search using both PostgreSQL full-text and Weaviate vector search.
        
        Args:
            query: Search query
            workspace_id: Workspace to search in
            limit: Maximum number of results
            offset: Number of results to skip
            hybrid_weight: Weight between full-text (0.0) and vector search (1.0)
        """
        try:
            # Get PostgreSQL full-text search results
            pg_results = self.indexing_service.search_documents(
                query=query,
                workspace_id=workspace_id,
                # Get more results for merging
                limit=limit * 2,
                offset=offset
            )
            pg_docs = {str(doc.document_id): doc for doc in pg_results}

            # Get Weaviate vector search results
            vector_results = await self.vector_service.search_documents(
                query=query,
                workspace_id=str(workspace_id),
                # Get more results for merging
                limit=limit * 2
            )
            vector_docs = {
                result["document_id"]: {"score": result["_additional"]["score"]}
                for result in vector_results
            }

            # Combine and rank results
            combined_results = []
            seen_docs = set()

            # Process PostgreSQL results first
            for doc_id, doc in pg_docs.items():
                if doc_id in seen_docs:
                    continue
                
                result = {
                    "document_id": doc.document_id,
                    "title": doc.title,
                    "created_at": doc.created_at,
                    "updated_at": doc.updated_at,
                    "score": {
                        "full_text": 1.0,  # Normalized score
                        "vector": vector_docs.get(doc_id, {}).get("score", 0.0),
                    }
                }
                
                # Calculate hybrid score
                result["score"]["hybrid"] = (
                    (1 - hybrid_weight) * result["score"]["full_text"] +
                    hybrid_weight * result["score"]["vector"]
                )
                
                combined_results.append(result)
                seen_docs.add(doc_id)

            # Add remaining vector results
            for doc_id, vector_data in vector_docs.items():
                if doc_id in seen_docs:
                    continue
                
                doc = pg_docs.get(doc_id)
                if not doc:
                    continue

                result = {
                    "document_id": doc.document_id,
                    "title": doc.title,
                    "created_at": doc.created_at,
                    "updated_at": doc.updated_at,
                    "score": {
                        "full_text": 0.0,
                        "vector": vector_data["score"],
                    }
                }
                
                # Calculate hybrid score
                result["score"]["hybrid"] = (
                    (1 - hybrid_weight) * result["score"]["full_text"] +
                    hybrid_weight * result["score"]["vector"]
                )
                
                combined_results.append(result)

            # Sort by hybrid score and apply pagination
            combined_results.sort(key=lambda x: x["score"]["hybrid"], reverse=True)
            paginated_results = combined_results[offset:offset + limit]

            return paginated_results

        except Exception as e:
            logger.error(f"Error in hybrid search: {str(e)}")
            raise
