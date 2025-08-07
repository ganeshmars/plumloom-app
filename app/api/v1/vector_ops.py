# app/api/v1/vector_ops.py
from fastapi import APIRouter, Depends, HTTPException, Body, Path, status, Query
from uuid import UUID
from typing import Dict, Any, List

from app.api.v1.schemas import (
    VectorCreateRequest, VectorUpdateRequest, SearchRequest, UserInfo,
    VectorizeResponse, DeleteResponse, SearchResponse, SearchResultItem
)
# Import the specific service classes for type checking
from app.services.weaviate.page_service_async import PageVectorServiceAsync
from app.services.weaviate.document_service_async import DocumentVectorServiceAsync
# Keep BaseVectorService for dependency return type
from app.services.weaviate.base_vector_service import BaseVectorService
from app.api.v1.deps import get_vector_service, get_dummy_user
from app.services.weaviate.exceptions import VectorStoreOperationError, VectorStoreNotFoundError
from app.core.logging_config import logger


router = APIRouter(prefix="/vector-ops", tags=["Vector Operations"])

@router.post("/{collection_name}/{doc_id}/vectorize",
             response_model=VectorizeResponse,
             status_code=status.HTTP_201_CREATED)
async def vectorize_content(
    collection_name: str = Path(..., description="Target collection ('Page' or 'Document')"),
    doc_id: UUID = Path(..., description="Document UUID"),
    payload: VectorCreateRequest = Body(...),
    # service dependency still returns BaseVectorService
    service: BaseVectorService = Depends(get_vector_service),
    current_user: UserInfo = Depends(get_dummy_user),
):
    """
    Vectorize content and store it in the specified collection.
    Handles text extraction and chunking internally.
    """
    tenant_id = current_user.tenant_id
    logger.info(f"Vectorizing doc {doc_id} for tenant {tenant_id} in collection {collection_name}")

    try:
        # --- MODIFIED: Call service methods explicitly based on type ---
        if isinstance(service, PageVectorServiceAsync):
            result = await service.create_vectors_from_content(
                tenant_id=tenant_id,
                doc_id=doc_id,
                workspace_id=payload.workspace_id, # Get required args from payload
                title=payload.title,
                content=payload.content
            )
        elif isinstance(service, DocumentVectorServiceAsync):
            result = await service.create_vectors_from_content(
                tenant_id=tenant_id,
                doc_id=doc_id,
                workspace_id=payload.workspace_id, # Get required args from payload
                title=payload.title,
                content=payload.content,
                chat_session_id=payload.chat_session_id # Pass the specific arg
            )
        else:
            # This case should ideally not be reached if deps.py is correct
            logger.error(f"Invalid service type resolved for collection '{collection_name}': {type(service)}")
            raise HTTPException(status_code=500, detail="Internal server error: Could not resolve appropriate vector service.")
        # --- End Modification ---

        if result["status"] == "error":
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["message"])
        elif result["status"] == "partial_success":
            return VectorizeResponse(**result)

        return VectorizeResponse(**result)

    except ValueError as ve:
        logger.warning(f"Value error during vectorization for doc {doc_id}: {ve}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except VectorStoreOperationError as vs_err:
        logger.error(f"Weaviate operation error for doc {doc_id}: {vs_err}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Vector DB operation failed: {vs_err}")
    except Exception as e:
        logger.error(f"Unexpected error vectorizing doc {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during vectorization.")

# --- IMPORTANT ---
# You need to apply similar logic to the `update_vectorized_content` endpoint.
# Check the signature of the `update_vectors_from_content` method in each
# service and pass only the arguments it expects.

@router.put("/{collection_name}/{doc_id}/vectorize",
            response_model=VectorizeResponse, # Reuse or create specific UpdateResponse
            status_code=status.HTTP_200_OK)
async def update_vectorized_content(
    collection_name: str = Path(..., description="Target collection ('Page' or 'Document')"),
    doc_id: UUID = Path(..., description="Document UUID"),
    payload: VectorUpdateRequest = Body(...),
    service: BaseVectorService = Depends(get_vector_service),
    current_user: UserInfo = Depends(get_dummy_user),
):
    """
    Update vectorized content for a document. Handles re-chunking and diffing.
    """
    tenant_id = current_user.tenant_id
    logger.info(f"Updating vectors for doc {doc_id} for tenant {tenant_id} in collection {collection_name}")

    try:
        # --- MODIFIED: Call service methods explicitly based on type ---
        # Make sure workspace_id and title are handled correctly for update
        # Fetch if needed, or require in payload if service logic depends on them.
        # Assuming here they are needed and provided optionally in payload
        if isinstance(service, PageVectorServiceAsync):
            result = await service.update_vectors_from_content(
                tenant_id=tenant_id,
                doc_id=doc_id,
                workspace_id=payload.workspace_id, # Pass if provided
                title=payload.title,              # Pass if provided
                content=payload.content
            )
        elif isinstance(service, DocumentVectorServiceAsync):
             result = await service.update_vectors_from_content(
                tenant_id=tenant_id,
                doc_id=doc_id,
                workspace_id=payload.workspace_id, # Pass if provided
                title=payload.title,              # Pass if provided
                content=payload.content,
                chat_session_id=payload.chat_session_id # Pass the specific arg
            )
        else:
            logger.error(f"Invalid service type resolved for collection '{collection_name}': {type(service)}")
            raise HTTPException(status_code=500, detail="Internal server error: Could not resolve appropriate vector service.")
        # --- End Modification ---


        if result["status"] == "not_implemented":
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=result["message"])
        if result["status"] == "error":
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["message"])
        elif result["status"] == "partial_success":
            return VectorizeResponse(**result)

        return VectorizeResponse(**result)

    except VectorStoreNotFoundError:
        logger.warning(f"Document {doc_id} not found in vector store during update attempt.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No vectors found for document {doc_id} to update.")
    except VectorStoreOperationError as vs_err:
        logger.error(f"Weaviate operation error updating doc {doc_id}: {vs_err}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Vector DB operation failed: {vs_err}")
    except Exception as e:
        logger.error(f"Unexpected error updating doc {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during update.")


# --- Delete and Search endpoints likely don't need this change ---
# --- as their arguments are usually less complex / different ---
# --- but double-check their service method signatures too! ---

@router.delete("/{collection_name}/{doc_id}",
               response_model=DeleteResponse,
               status_code=status.HTTP_200_OK)
async def delete_document_vectors(
    collection_name: str = Path(..., description="Target collection ('Page' or 'Document')"),
    doc_id: UUID = Path(..., description="Document UUID"),
    service: BaseVectorService = Depends(get_vector_service),
    current_user: UserInfo = Depends(get_dummy_user),
):
    # ... (implementation likely okay) ...
    tenant_id = current_user.tenant_id
    logger.info(f"Deleting vectors for doc {doc_id} for tenant {tenant_id} in collection {collection_name}")
    try:
        # Delete methods usually only need tenant_id and doc_id
        result = await service.delete_vectors(
            tenant_id=tenant_id,
            doc_id=doc_id
        )
        if result["status"] == "error":
             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["message"])
        return DeleteResponse(**result)
    except VectorStoreOperationError as vs_err:
         logger.error(f"Weaviate operation error deleting doc {doc_id}: {vs_err}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Vector DB operation failed: {vs_err}")
    except Exception as e:
        logger.error(f"Unexpected error deleting doc {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during deletion.")


@router.post("/{collection_name}/search", response_model=SearchResponse)
async def search_vectors(
    collection_name: str = Path(..., description="Target collection ('Page' or 'Document')"),
    payload: SearchRequest = Body(...),
    service: BaseVectorService = Depends(get_vector_service),
    current_user: UserInfo = Depends(get_dummy_user),
):
    tenant_id = current_user.tenant_id
    logger.info(f"Searching collection {collection_name} for tenant {tenant_id}. Query: '{payload.query[:50]}...'")

    try:
        results_list: List[Dict[str, Any]] = []

        # --- MODIFIED: Call search method explicitly based on type ---
        if isinstance(service, PageVectorServiceAsync):
            # Call Page service search - does NOT expect chat_session_id
            results_list = await service.search(
                tenant_id=tenant_id,
                query=payload.query,
                limit=payload.limit,
                workspace_id=payload.workspace_id,
                doc_id=payload.doc_id,
                use_hybrid=payload.use_hybrid,
                alpha=payload.alpha
                # No chat_session_id passed here
            )
        elif isinstance(service, DocumentVectorServiceAsync):
            # Call Document service search - DOES expect chat_session_id
            results_list = await service.search(
                tenant_id=tenant_id,
                query=payload.query,
                limit=payload.limit,
                workspace_id=payload.workspace_id,
                doc_id=payload.doc_id,
                chat_session_id=payload.chat_session_id, # Pass it here
                use_hybrid=payload.use_hybrid,
                alpha=payload.alpha
            )
        else:
            # This case should ideally not be reached if deps.py is correct
            logger.error(f"Invalid service type resolved for collection '{collection_name}' search: {type(service)}")
            raise HTTPException(status_code=500, detail="Internal server error: Could not resolve appropriate vector service for search.")
        # --- End Modification ---

        formatted_results = [SearchResultItem(**item) for item in results_list]
        return SearchResponse(results=formatted_results, count=len(formatted_results))

    except VectorStoreOperationError as vs_err:
         logger.error(f"Weaviate search error in {collection_name}: {vs_err}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Vector DB search failed: {vs_err}")
    except Exception as e:
        # Catch the TypeError here if it somehow still happens, or other unexpected errors
        logger.error(f"Unexpected error during search in {collection_name}: {e}", exc_info=True)
        # Provide a more specific error message if it's a TypeError during the call
        detail = f"An unexpected error occurred during search: {e}"
        if isinstance(e, TypeError):
             detail = f"Internal error processing search arguments for {collection_name}: {e}"
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)