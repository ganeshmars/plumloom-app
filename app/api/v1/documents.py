from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, File
import json
from typing import Optional, Dict, Any
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from app.core.redis import get_redis
from app.core.storage import upload_file_to_gcs, delete_file_from_gcs
from app.core.constants import GCS_STORAGE_BUCKET
from fastapi.responses import JSONResponse

from app.core.auth import validate_session
from app.core.database import get_db
from app.models import User, Workspace
from app.services.document_service import DocumentService
from app.schemas.document import (
    DocumentCreate,
    DocumentUpdate,
    DocumentResponse,
    DocumentList,
    DocumentSearch,
    DocumentSearchResponse,
    DocumentTreeResponse,
    CoverLetterResponse,
    DocumentUpdateRequest
)
from app.models.document import Document
from sqlalchemy import select, delete
from datetime import datetime, timezone, UTC
from app.tasks.document.update_hierarchy import process_hierarchy_update
from app.core.logging_config import logger
from app.schemas.document import MoveDocumentToWorkspaceRequest, MoveDocumentToWorkspaceResponse

router = APIRouter(prefix="/documents", tags=["documents"])

# # Test endpoints for Redis operations
# @router.post("/test/redis/store")
# async def test_store_in_redis(
#     content_file: UploadFile = File(...),
#     redis: Redis = Depends(get_redis)
# ) -> Dict[str, Any]:
#     """Test endpoint to store compressed document content in Redis"""
#     try:
#         # Read and decompress the content
#         compressed_content = await content_file.read()
        
#         # Decompress gzip content
#         with gzip.GzipFile(fileobj=io.BytesIO(compressed_content)) as f:
#             json_content = f.read()
        
#         # Generate a test key using timestamp
#         import time
#         timestamp = int(time.time())
#         test_key = f"test:doc:content:{timestamp}"
        
#         # Store JSON content in Redis with 5 minute TTL
#         await redis.setex(test_key, 300, json_content)
        
#         return {
#             "status": "success",
#             "message": "Content stored in Redis",
#             "key": test_key
#         }
#     except Exception as e:
#         logger.error(f"Failed to store content in Redis: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))

# @router.get("/test/redis/{key}")
# async def test_get_from_redis(
#     key: str,
#     redis: Redis = Depends(get_redis)
# ) -> Response:
#     """Test endpoint to retrieve content from Redis"""
#     try:
#         content = await redis.get(key)
#         if not content:
#             raise HTTPException(status_code=404, detail="Content not found in Redis")
            
#         return Response(
#             content=content,
#             media_type="application/json"
#         )
#     except Exception as e:
#         logger.error(f"Failed to retrieve content from Redis: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))

def get_document_service(db: AsyncSession = Depends(get_db)):
    return DocumentService(db)

@router.post("/create", response_model=DocumentResponse)
async def create_document(
    title: str,
    workspace_id: UUID,
    parent_page_id: Optional[UUID] = None,
    icon_url: Optional[str] = None,
    cover_url: Optional[str] = None,
    current_user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
) -> DocumentResponse:
    """Create a new document"""
    logger.info(f"Creating document with title: {title}")
    
    try:

        # Create new document
        # Create new document with null content path
        doc_id = uuid4()
        document = Document(
            document_id=doc_id,
            title=title,
            workspace_id=workspace_id,
            user_id=current_user['id'],
            parent_id=parent_page_id,
            icon_url=icon_url,
            cover_url=cover_url,
            content_file_path="null",
            meta_data={}
        )
        
        db.add(document)
        await db.commit()
        await db.refresh(document)
        
        return DocumentResponse.model_validate(document)
        
    except ValueError as e:
        logger.warning(f"Invalid input for document creation: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{target_doc_id}/update-hierarchy", response_model=DocumentResponse)
async def update_document_hierarchy(
    target_doc_id: UUID,
    parent_doc_id: Optional[UUID] = None,
    current_user: dict = Depends(validate_session),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentResponse:
    """
    Update document hierarchy by assigning a new parent to the target document.
    
    This endpoint takes:
    - target_doc_id: The target document that will get the new parent
    - parent_doc_id: The new parent document ID (or None to make it a root document)
    """
    logger.info(f"Updating hierarchy: assigning parent {parent_doc_id} to document {target_doc_id}")
    
    try:
        doc = await document_service.get_document_object_by_id(target_doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Target document not found")
            
        if parent_doc_id:
            parent_doc = await document_service.get_document_object_by_id(parent_doc_id)
            if not parent_doc:
                raise HTTPException(status_code=404, detail="Parent document not found")
                
            if doc.workspace_id != parent_doc.workspace_id:
                raise HTTPException(status_code=400, detail="Documents must be in the same workspace")
        
        task_data = {
            "data": {
                "target_doc_id": str(target_doc_id),
                "parent_doc_id": str(parent_doc_id) if parent_doc_id else None,
                "user_id": current_user['id'],
                "timestamp": str(datetime.now(UTC))
            }
        }
        
        process_hierarchy_update.delay(task_data)
        
        return DocumentResponse.model_validate(doc)
            
    except ValueError as e:
        logger.warning(f"Invalid input for hierarchy update: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error queueing document hierarchy update: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: UUID,
    current_user: str = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
) -> DocumentResponse:
    """Get a document by ID"""
    logger.info(f"Fetching document with id: {doc_id}")
    
    try:
        result = await db.execute(select(Document).where(Document.document_id == doc_id))
        doc = result.scalar_one_or_none()

        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        doc.opened_at = datetime.now(timezone.utc)
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
            
        return DocumentResponse.model_validate(doc)
        
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error retrieving document {doc_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{doc_id}/tree", response_model=DocumentTreeResponse)
async def get_document_tree(
    doc_id: UUID,
    current_user: str = Depends(validate_session),
    document_service: DocumentService = Depends(get_document_service)
) -> DocumentTreeResponse:
    """Get a document's hierarchy tree by ID"""
    logger.info(f"Fetching document tree for document with id: {doc_id}")
    
    try:
        doc_tree = await document_service.get_document_tree(doc_id)
        if not doc_tree:
            raise HTTPException(status_code=404, detail="Document not found")
            
        return doc_tree
        
    except Exception as e:
        logger.error(f"Error retrieving document tree for {doc_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{doc_id}", response_model=DocumentResponse)
async def update_document(
    doc_id: UUID,
    update_data: DocumentUpdateRequest,
    current_user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
) -> DocumentResponse:
    """Update a document"""
    logger.info(f"Updating document {doc_id}")
    
    try:
        # Get the document
        query = select(Document).where(Document.document_id == doc_id)
        result = await db.execute(query)
        document = result.scalar_one_or_none()
        
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Update fields
        if update_data.title is not None:
            document.title = update_data.title
        
        if update_data.delete_icon:
            document.icon_url = None
        elif update_data.icon_url is not None:
            document.icon_url = str(update_data.icon_url)
        if update_data.cover_url is not None:
            document.cover_url = str(update_data.cover_url)
        
        await db.commit()
        await db.refresh(document)
        
        return DocumentResponse.model_validate(document)
        
    except ValueError as e:
        logger.warning(f"Invalid input for document update {doc_id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating document {doc_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: UUID,
    current_user: str = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
    ) -> dict:
    """Delete a document
    
    This endpoint deletes a document from the database and triggers a background task
    to clean up associated resources, including:
    - Vector database entries
    - Files stored in Google Cloud Storage
    - Tiptap Cloud document
    - Redis cache entries
    """
    logger.info(f"Deleting document: {doc_id}")
    
    try:
        # First, retrieve the document to get its details before deletion
        document_query = await db.execute(select(Document).where(Document.document_id == doc_id))
        document = document_query.scalar_one_or_none()
        
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Get user to access tenant information
        # current_user is a dictionary returned by validate_session
        user_id = current_user.get('user_id')
        user_query = await db.execute(select(User).where(User.id == user_id))
        user = user_query.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found")
        
        # Get tenant ID from user's tenants array
        tenant_id = user.tenants[0] if user.tenants else str(document.workspace_id)  # Fallback to workspace_id if no tenants
        logger.info(f"Using tenant ID {tenant_id} for vector operations")
        
        # Check if this document has child documents that reference it as a parent
        child_docs_query = await db.execute(select(Document).where(Document.parent_id == doc_id))
        child_docs = child_docs_query.scalars().all()
        
        # Explicitly delete child documents first
        if child_docs:
            logger.info(f"Document {doc_id} has {len(child_docs)} child documents that will be deleted explicitly.")
            for child_doc in child_docs:
                # Check if the child has its own children
                child_doc_id = child_doc.document_id
                nested_child_query = await db.execute(select(Document).where(Document.parent_id == child_doc_id))
                nested_children = nested_child_query.scalars().all()
                
                # Delete any nested children first
                if nested_children:
                    logger.info(f"Child document {child_doc_id} has {len(nested_children)} nested children to delete.")
                    for nested_child in nested_children:
                        await db.delete(nested_child)
                
                # Now delete the child document
                await db.delete(child_doc)
            
            # Commit the deletion of all children
            await db.commit()
            logger.info(f"All child documents of {doc_id} have been deleted.")
        
        # Now delete the parent document
        # Fetch the document instance to ensure ORM cascades (like deleting versions) are triggered.
        stmt = select(Document).where(Document.document_id == doc_id)
        result = await db.execute(stmt)
        parent_doc_to_delete = result.scalar_one_or_none()

        if parent_doc_to_delete:
            await db.delete(parent_doc_to_delete)
            await db.commit()
        else:
            logger.warning(f"Document {doc_id} was not found for deletion during the final step. It may have already been deleted.")
        
        # Collect child document IDs that were deleted for cleanup
        deleted_child_ids = []
        if child_docs:
            for child_doc in child_docs:
                deleted_child_ids.append(str(child_doc.document_id))
                # Query for nested children
                nested_children_query = await db.execute(select(Document).where(Document.parent_id == child_doc.document_id))
                nested_children_list = nested_children_query.scalars().all()
                if nested_children_list:
                    for nested_child in nested_children_list:
                        deleted_child_ids.append(str(nested_child.document_id))
        
        # Trigger background task to clean up all associated resources
        # This allows the API to respond quickly while resource cleanup happens asynchronously
        from app.tasks.tasks import delete_document_resources
        delete_document_resources.delay(
            document_id=str(doc_id),
            user_id=user_id,  # Pass the extracted user_id instead of the current_user dict
            tenant_id=tenant_id,
            deleted_child_ids=deleted_child_ids  # Pass the IDs of child documents that were already deleted
        )
        
        logger.info(f"Document {doc_id} deleted from database. Background cleanup task triggered.")
        return {"message": "Document deleted successfully. Resource cleanup in progress."}
        
    except Exception as e:
        logger.error(f"Error deleting document {doc_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", response_model=DocumentList)
async def list_documents(
    workspace_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    current_user: str = Depends(validate_session),
    document_service: DocumentService = Depends(get_document_service)
) -> DocumentList:
    """List documents in a workspace with pagination"""
    try:
        documents = await document_service.list_documents(
            workspace_id=workspace_id,
            page=page,
            page_size=page_size
        )
        return documents
        
    except Exception as e:
        logger.error(f"Error listing documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", response_model=DocumentSearchResponse)
async def search_documents(
    search_params: DocumentSearch,
    current_user: str = Depends(validate_session),
    document_service: DocumentService = Depends(get_document_service)
) -> DocumentSearchResponse:
    """Search documents using semantic search"""
    try:
        results = await document_service.search_documents(
            query=search_params.query,
            workspace_id=search_params.workspace_id,
            page=search_params.page,
            page_size=search_params.page_size
        )
        return results
        
    except Exception as e:
        logger.error(f"Error searching documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{doc_id}/cover-image", response_model=CoverLetterResponse)
async def upload_cover_letter(
    doc_id: UUID,
    cover_file: UploadFile = File(None),
    x_position: Optional[int] = Query(None, description="X coordinate for cover letter positioning"),
    y_position: Optional[int] = Query(None, description="Y coordinate for cover letter positioning"),
    current_user: dict = Depends(validate_session),
    document_service: DocumentService = Depends(get_document_service)
) -> CoverLetterResponse:
    """Upload a cover letter for a document."""
    logger.info(f"Processing cover letter for document {doc_id}")
    
    try:
        document = await document_service.get_document_object_by_id(doc_id)
        if not document:
            logger.warning(f"Document not found: {doc_id}")
            raise HTTPException(status_code=404, detail="Document not found")
        cover_url = document.cover_url
        if cover_file:
            max_file_size = 25 * 1024 * 1024
            await cover_file.seek(0)
            content = await cover_file.read()
            file_size = len(content)
            # Reset to beginning
            await cover_file.seek(0)
            
            if file_size > max_file_size:
                logger.warning(f"File size exceeds limit: {file_size} bytes")
                return JSONResponse(
                    status_code=400,
                    content={"error": "File size exceeds the maximum limit of 25MB"}
                )
            
            # Validate file extension
            file_extension = cover_file.filename.split('.')[-1].lower() if '.' in cover_file.filename else ''
            allowed_extensions = ['jpg', 'jpeg', 'png', 'svg']
            if file_extension not in allowed_extensions:
                logger.warning(f"Invalid file extension: {file_extension}")
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Only jpg, jpeg, png, and svg files are allowed. Received: {file_extension}"}
                )
                
            if document.cover_url:
                logger.info(f"Removing existing cover letter: {document.cover_url}")
                old_file_path = document.cover_url.split("/")[-1]
                if old_file_path.startswith(f"{doc_id}_"):
                    old_file_path = f"documents/cover_letters/{old_file_path}"
                    await delete_file_from_gcs(old_file_path, GCS_STORAGE_BUCKET)

            file_content = await cover_file.read()
            timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            file_path = f"documents/cover_letters/{doc_id}_{timestamp}.{file_extension}"
            
            content_type = cover_file.content_type or "application/octet-stream"
            cover_url = await upload_file_to_gcs(file_content, file_path, GCS_STORAGE_BUCKET, content_type)

        logger.info(f"Cover letter uploaded for document {doc_id}")
        updated_document = await document_service.update_document_cover(
            doc_id=doc_id,
            cover_url=cover_url,
            meta_data={**document.meta_data, 'cover_letter_position': {
                'x': x_position if x_position is not None else document.meta_data.get('cover_letter_position', {}).get('x'),
                'y': y_position if y_position is not None else document.meta_data.get('cover_letter_position', {}).get('y')
            }}
        )
        
        return CoverLetterResponse(
            document_id=updated_document.document_id,
            cover_url=updated_document.cover_url or "",
            meta_data=updated_document.meta_data
        )
        
    except ValueError as e:
        logger.warning(f"Invalid input for cover letter operation: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing cover letter: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{doc_id}/cover-image", response_model=CoverLetterResponse)
async def delete_cover_letter(
    doc_id: UUID,
    current_user: dict = Depends(validate_session),
    document_service: DocumentService = Depends(get_document_service)
) -> CoverLetterResponse:
    """Delete a cover letter from a document."""
    logger.info(f"Deleting cover letter for document {doc_id}")
    
    try:
        document = await document_service.get_document_object_by_id(doc_id)
        if not document:
            logger.warning(f"Document not found: {doc_id}")
            raise HTTPException(status_code=404, detail="Document not found")
        
        if not document.cover_url:
            logger.warning(f"No cover letter found for document {doc_id}")
            raise HTTPException(status_code=404, detail="No cover letter found for this document")
        
        logger.info(f"Removing existing cover letter: {document.cover_url}")
        old_file_path = document.cover_url.split("/")[-1]
        if old_file_path.startswith(f"{doc_id}_"):
            old_file_path = f"documents/cover_letters/{old_file_path}"
            await delete_file_from_gcs(old_file_path, GCS_STORAGE_BUCKET)
        
        logger.info(f"Removed cover letter from document {doc_id}")
        updated_document = await document_service.update_document_cover(
            doc_id=doc_id,
            cover_url=None,
            meta_data={**document.meta_data, "cover_letter_position": {}}
        )

        return CoverLetterResponse(
            document_id=updated_document.document_id,
            cover_url="",
            meta_data=updated_document.meta_data
        )
        
    except ValueError as e:
        logger.warning(f"Invalid input for cover letter deletion: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting cover letter: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/move-to-workspace", response_model=MoveDocumentToWorkspaceResponse)
async def move_document_to_workspace(
    move_request: MoveDocumentToWorkspaceRequest,
    current_user: dict = Depends(validate_session),
    document_service: DocumentService = Depends(get_document_service),
    db: AsyncSession = Depends(get_db),
):
    """
    Move a document (page) and its descendants to a new workspace.
    - The root page gets the new workspace_id and parent_id=None.
    - All descendants get their workspace_id updated.
    - If a child page is shifted, it becomes a root in the new workspace, and its children are transferred as-is.
    """
    try:
        moved = await document_service.move_document_to_workspace(move_request.page_id, move_request.new_workspace_id)
        if not moved:
            # raise HTTPException(status_code=404, detail="Document not found or move failed")
            return JSONResponse(status_code=404, content={"error": "Document not found or move failed"})
        # Fetch the workspace name for the response
        result = await db.execute(select(Workspace).where(Workspace.workspace_id == move_request.new_workspace_id))
        workspace = result.scalar_one_or_none()
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found")
        workspace_name = workspace.name
        return MoveDocumentToWorkspaceResponse(
            status="success",
            message=f"Page moved to {workspace_name} workspace"
        )
    except Exception as e:
        logger.error(f"Error moving document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
