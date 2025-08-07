"""API endpoints for document search."""

from typing import List, Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.hybrid_search_service import HybridSearchService

router = APIRouter(prefix="/search", tags=["search"])

@router.get("/", response_model=List[Dict[str, Any]])
async def search_documents(
    query: str = Query(..., description="Search query"),
    workspace_id: UUID = Query(..., description="Workspace ID to search in"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    hybrid_weight: float = Query(
        0.5,
        ge=0.0,
        le=1.0,
        description="Weight between full-text (0.0) and vector search (1.0)"
    ),
    db: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """
    Search documents using hybrid search (PostgreSQL full-text + Weaviate vector search).
    
    - `query`: The search query
    - `workspace_id`: ID of the workspace to search in
    - `limit`: Maximum number of results (1-100)
    - `offset`: Number of results to skip for pagination
    - `hybrid_weight`: Balance between full-text (0.0) and vector search (1.0)
    
    Returns a list of documents with scores for each search method.
    """
    search_service = HybridSearchService(db)
    results = await search_service.search_documents(
        query=query,
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
        hybrid_weight=hybrid_weight
    )
    return results
