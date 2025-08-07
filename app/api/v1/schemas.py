# app/api/v1/schemas.py
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Union
from uuid import UUID

# --- Dummy User ---
class UserInfo(BaseModel):
    user_id: str = "test-user-123"
    tenant_id: str = "personal_77531"

# --- API Payloads ---
class VectorCreateRequest(BaseModel):
    workspace_id: UUID
    title: str
    content: Union[Dict[str, Any], str]
    chat_session_id: Optional[str] = None

class VectorUpdateRequest(BaseModel):
    workspace_id: Optional[UUID] = None
    title: Optional[str] = None
    content: Union[Dict[str, Any], str]
    chat_session_id: Optional[str] = None

class SearchRequest(BaseModel):
    query: str
    limit: int = Field(10, gt=0, le=100)
    workspace_id: Optional[UUID] = None
    doc_id: Optional[UUID] = None
    chat_session_id: Optional[str] = None
    use_hybrid: bool = False
    alpha: float = Field(0.5, ge=0.0, le=1.0)

# --- API Responses ---
class VectorizeResponse(BaseModel):
    status: str
    message: str
    document_id: str
    successful_chunks: Optional[int] = None
    failed_chunks: Optional[int] = None
    # Add other fields returned by service methods

class DeleteResponse(BaseModel):
    status: str
    message: str
    document_id: str
    chunks_deleted: Optional[int] = None

class SearchResultItem(BaseModel):
    uuid: str
    properties: Dict[str, Any]
    # *** CORRECTED: Made metadata optional ***
    metadata: Optional[Dict[str, Any]] = None # Set default to None or {} if preferred

class SearchResponse(BaseModel):
    results: List[SearchResultItem]
    count: int