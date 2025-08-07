# app/api/v1/deps.py
from fastapi import Depends, HTTPException, status

from app.services.weaviate.repository_async import WeaviateRepositoryAsync
from app.services.weaviate.page_service_async import PageVectorServiceAsync
from app.services.weaviate.document_service_async import DocumentVectorServiceAsync
from app.services.weaviate.base_vector_service import BaseVectorService
from app.api.v1.schemas import UserInfo # Import the dummy UserInfo
from app.core.logging_config import logger

# --- Repository Dependency ---
# This could be enhanced for true singleton management if needed
_repo_async_instance = None

def get_weaviate_repo_async() -> WeaviateRepositoryAsync:
    global _repo_async_instance
    if _repo_async_instance is None:
        logger.info("Creating WeaviateRepositoryAsync instance")
        # Ensures the client is ready via get_client() inside the repo init
        _repo_async_instance = WeaviateRepositoryAsync()
    return _repo_async_instance

# --- Service Dependencies ---
def get_page_vector_service(
    repo: WeaviateRepositoryAsync = Depends(get_weaviate_repo_async)
) -> PageVectorServiceAsync:
    return PageVectorServiceAsync(repository=repo)

def get_document_vector_service(
    repo: WeaviateRepositoryAsync = Depends(get_weaviate_repo_async)
) -> DocumentVectorServiceAsync:
    return DocumentVectorServiceAsync(repository=repo)

# --- Collection Service Factory Dependency ---
# Selects the correct service based on path parameter
def get_vector_service(
    collection_name: str,
    page_service: PageVectorServiceAsync = Depends(get_page_vector_service),
    doc_service: DocumentVectorServiceAsync = Depends(get_document_vector_service)
) -> BaseVectorService:
    if collection_name.lower() == "page":
        return page_service
    elif collection_name.lower() == "document":
        return doc_service
    else:
        logger.error(f"Invalid collection name requested: {collection_name}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_name}' not supported."
        )

# --- Dummy User Dependency ---
# Replace this with your actual authentication dependency
async def get_dummy_user() -> UserInfo:
    """Provides a hardcoded user/tenant for testing purposes."""
    return UserInfo()