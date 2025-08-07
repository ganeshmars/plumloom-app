# """API endpoints for testing vector service."""
#
# from uuid import UUID
# from fastapi import APIRouter, HTTPException, File, UploadFile
# from pydantic import BaseModel
# from typing import Dict, Any
# import logging
# from app.services.vector_services_v2 import VectorService
#
# logger = logging.getLogger(__name__)
# router = APIRouter(prefix="/test/vector-service", tags=["test-vector-service"])
# vector_service = VectorService()
#
# class VectorizeRequest(BaseModel):
#     """Request model for vectorization test."""
#     tenant_id: str
#     doc_id: UUID
#     workspace_id: UUID
#     title: str
#
# @router.post("/create")
# async def test_vectorize(
#     tenant_id: str,
#     doc_id: str,
#     workspace_id: str,
#     title: str,
#     content_file: UploadFile = File(...)
# ):
#     """Test endpoint for document vectorization."""
#     content = await content_file.read()
#     try:
#         result = vector_service.create_vectors(
#             tenant_id=tenant_id,
#             doc_id=doc_id,
#             workspace_id=workspace_id,
#             title=title,
#             content=content
#         )
#         return result
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#
# @router.post("/update")
# async def test_update(
#     tenant_id: str,
#     doc_id: str,
#     workspace_id: str,
#     title: str,
#     content_file: UploadFile = File(...)
# ):
#     """Test endpoint for updating document vectorization."""
#     content = await content_file.read()
#     try:
#         result = vector_service.update_vectors(
#             tenant_id=tenant_id,
#             doc_id=doc_id,
#             workspace_id=workspace_id,
#             title=title,
#             content=content
#         )
#         return result
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#
# @router.get("/health")
# async def test_health():
#     """Test endpoint to check if vector service is healthy."""
#     try:
#         # Try to connect to Weaviate
#         vector_service.client.schema.get()
#         return {"status": "healthy", "message": "Successfully connected to Weaviate"}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Vector service unhealthy: {str(e)}")
