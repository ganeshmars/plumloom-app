from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.document import DocumentResponse
from typing import Optional, List, Any, Union, Dict

class WorkspaceBase(BaseModel):
    name: str = Field(..., description="Name of the workspace")
    description: Optional[str] = Field(None, description="Optional description of the workspace")

class WorkspaceCreate(WorkspaceBase):
    
    icon_url: Optional[str] = Field(None, description="URL of the icon for the workspace")
    workspace_type : Optional[str] = Field(None, description="Type of the workspace")


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Name of the workspace")
    description: Optional[str] = Field(None, description="Description of the workspace")
    last_opened_page_id: Optional[UUID] = Field(None, description="ID of the last opened page in this workspace")
    is_nav_panel_opened: Optional[bool] = Field(None, description="State of the navigation panel")
    nav_panel_divider_position: Optional[int] = Field(None, description="Position of the navigation panel divider")
    is_chat_panel_opened: Optional[bool] = Field(None, description="State of the chat panel")

class WorkspaceInDB(WorkspaceBase):
    workspace_id: UUID = Field(..., description="Unique identifier for the workspace")
    user_id: str = Field(..., description="ID of the user who owns this workspace")
    created_at: datetime = Field(..., description="Timestamp when the workspace was created")
    meta_data: dict = Field(default={}, description="Additional metadata for the workspace")
    document_count: int = Field(default=0, description="Number of documents in the workspace")
    conversation_count: int = Field(default=0, description="Number of conversations in the workspace")
    updated_at: datetime = Field(..., description="Timestamp when the workspace was last updated")
    panel_state: Optional[Dict[str, Any]] = Field(
        default={"ai_assistant_panel": "closed", "context_menu": "open"},
        description="Panel state configuration"
    )
    opened_at: Optional[datetime] = Field(None, description="Timestamp when the workspace was opened")
    model_config = ConfigDict(from_attributes=True)

class WorkspaceResponse(WorkspaceInDB):
    workspace_type: Optional[str] = None
    last_opened_page_id: Optional[UUID] = None
    
    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        instance = super().model_validate(obj, *args, **kwargs)

        if obj.meta_data and 'last_opened_page_id' in obj.meta_data:
            try:
                instance.last_opened_page_id = UUID(obj.meta_data['last_opened_page_id'])
            except (ValueError, TypeError):
                pass
                
        return instance

class ParticularWorkspaceResponse(WorkspaceBase):
    workspace_id: UUID = Field(..., description="Unique identifier for the workspace")
    user_id: str = Field(..., description="ID of the user who owns this workspace")
    created_at: datetime = Field(..., description="Timestamp when the workspace was created")
    meta_data: dict = Field(default={}, description="Additional metadata for the workspace")
    updated_at: datetime = Field(..., description="Timestamp when the workspace was last updated")
    workspace_type: Optional[str] = None
    last_opened_page_id: Optional[UUID] = None
    
    model_config = ConfigDict(from_attributes=True)
    
    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        instance = super().model_validate(obj, *args, **kwargs)

        if obj.meta_data and 'last_opened_page_id' in obj.meta_data:
            try:
                instance.last_opened_page_id = UUID(obj.meta_data['last_opened_page_id'])
            except (ValueError, TypeError):
                pass
                
        return instance

class ListWorkspaceResponse(WorkspaceInDB):
    workspace_type: Optional[str] = None
    document_count: int = Field(default=0, description="Number of documents in the workspace")
    conversation_count: int = Field(default=0, description="Number of conversations in the workspace")    

class WorkspaceDeleteResponse(BaseModel):
    message: str = Field(..., description="Success message for the deletion")

class WorkspaceList(BaseModel):
    items: list[ListWorkspaceResponse]
    total: int = Field(..., description="Total number of workspaces")
    page: int = Field(..., description="Current page number")
    size: int = Field(..., description="Number of items per page")
    total_pages: int = Field(..., description="Total number of pages")

class PanelStateUpdate(BaseModel):
    panel_state: Dict[str, Any] = Field(
        default={"ai_assistant_panel": "closed", "context_menu": "open"},
        description="Panel state configuration"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "panel_state": {
                    "ai_assistant_panel": "open",
                    "context_menu": "closed"
                }
            }
        }