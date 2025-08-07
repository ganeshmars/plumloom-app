"""Pydantic schemas for document-related operations."""

from datetime import datetime
from typing import Dict, Any, Optional, List
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict, HttpUrl

class DocumentBase(BaseModel):
    """Base schema for document operations."""
    title: str = Field(..., description="Document title")

class DocumentCreate(DocumentBase):
    """Schema for document creation."""
    workspace_id: UUID = Field(..., description="ID of the workspace this document belongs to")
    parent_page_id: Optional[UUID] = Field(None, description="ID of the parent document if this is a child document")
    icon_url: Optional[str] = Field(None, description="URL of the document's icon")
    cover_url: Optional[str] = Field(None, description="URL of the document's cover image")

class DocumentUpdate(BaseModel):
    """Schema for document update."""
    title: Optional[str] = Field(None, description="New document title")
    content: Optional[Dict[str, Any]] = Field(None, description="New document content in TipTap JSON format")

class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = None
    icon_url: Optional[HttpUrl] = None
    cover_url: Optional[HttpUrl] = None
    delete_icon: bool = False

class DocumentVersion(BaseModel):
    """Schema for document version information."""
    version_id: UUID
    version_number: int
    content_file_path: str
    saved_at: datetime
    saved_by_user_id: str
    meta_data: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)



class DocumentResponse(DocumentBase):
    """Schema for document response."""
    title: str
    document_id: UUID
    workspace_id: UUID
    user_id: str = Field(..., description="ID of the user who created this document")
    created_at: datetime
    updated_at: datetime
    meta_data: Dict[str, Any]
    icon_url: Optional[str] = None
    cover_url: Optional[str] = None
    opened_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class DocumentTreeNode(DocumentResponse):
    """Schema for document tree node."""
    versions: List[Dict[str, Any]]=[]
    children: List["DocumentTreeNode"] = []

    model_config = ConfigDict(from_attributes=True)

# This is needed for the recursive type definition
DocumentTreeNode.model_rebuild()

class DocumentTreeResponse(BaseModel):
    """Schema for document tree response."""
    data: DocumentTreeNode

    model_config = ConfigDict(from_attributes=True)
    
class DocumentList(BaseModel):
    """Schema for list of documents."""
    documents: List[DocumentTreeNode]
    total: int
    page: int
    page_size: int

    model_config = ConfigDict(from_attributes=True)

class DocumentSearch(BaseModel):
    """Schema for document search parameters."""
    query: str = Field(..., description="Search query string")
    workspace_id: UUID = Field(..., description="Workspace to search in")
    page: int = Field(1, description="Page number for pagination")
    page_size: int = Field(10, description="Number of results per page")

class DocumentSearchResult(BaseModel):
    """Schema for document search results."""
    document_id: UUID
    title: str
    content_snippet: str
    relevance_score: float
    workspace_id: UUID
    created_at: datetime
    updated_at: datetime

class DocumentSearchResponse(BaseModel):
    """Schema for document search response."""
    results: List[DocumentSearchResult]
    total: int
    page: int
    page_size: int

class CoverLetterResponse(BaseModel):
    """Schema for cover letter upload response."""
    document_id: UUID
    cover_url: str
    meta_data: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)

class MoveDocumentToWorkspaceRequest(BaseModel):
    """Schema for move document to workspace request."""
    page_id: UUID
    new_workspace_id: UUID

class MoveDocumentToWorkspaceResponse(BaseModel):
    """Schema for move document to workspace response."""
    status: str
    message: str

    model_config = ConfigDict(from_attributes=True)
