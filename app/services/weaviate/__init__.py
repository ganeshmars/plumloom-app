# app/services/weaviate/__init__.py

from fastapi import Depends

from app.core.weaviate_client import get_client as get_weaviate_sdk_client # Alias for clarity
from .repository_sync import WeaviateRepositorySync
from .repository_async import WeaviateRepositoryAsync
from .page_service_async import PageVectorServiceAsync
from .document_service_async import DocumentVectorServiceAsync
# from .page_service_sync import PageVectorServiceSync # If you have/need sync versions
# from .document_service_sync import DocumentVectorServiceSync

from app.core.logging_config import logger

# --- Repository Dependencies ---
def get_weaviate_repository_sync() -> WeaviateRepositorySync:
    # Ensures the SDK client is initialized via get_client() which calls init_weaviate_sync() lazily
    return WeaviateRepositorySync(client=get_weaviate_sdk_client())

def get_weaviate_repository_async(
    sync_repo: WeaviateRepositorySync = Depends(get_weaviate_repository_sync)
) -> WeaviateRepositoryAsync:
    return WeaviateRepositoryAsync(sync_repository=sync_repo)


# --- Async Service Dependencies ---
def get_page_vector_service_async(
    repo_async: WeaviateRepositoryAsync = Depends(get_weaviate_repository_async),
) -> PageVectorServiceAsync:
    return PageVectorServiceAsync(repository=repo_async)

def get_document_vector_service_async(
    repo_async: WeaviateRepositoryAsync = Depends(get_weaviate_repository_async),
) -> DocumentVectorServiceAsync:
    return DocumentVectorServiceAsync(repository=repo_async)


# --- Optional: Sync Service Dependencies (if you use them elsewhere) ---
# def get_page_vector_service_sync(
#     repo_sync: WeaviateRepositorySync = Depends(get_weaviate_repository_sync),
# ) -> PageVectorServiceSync:
#     return PageVectorServiceSync(repository=repo_sync)

# def get_document_vector_service_sync(
#     repo_sync: WeaviateRepositorySync = Depends(get_weaviate_repository_sync),
# ) -> DocumentVectorServiceSync:
#     return DocumentVectorServiceSync(repository=repo_sync)

logger.info("Weaviate service dependencies configured.")