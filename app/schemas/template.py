from datetime import datetime
from typing import Dict, Any, Optional, List
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict

from app.models.template import TemplateCategory

class TemplateBase(BaseModel):
    """Base schema for template operations."""
    title: str = Field(..., description="Template title")
    category: TemplateCategory = Field(..., description="Template category")

class TemplateCreate(TemplateBase):
    """Schema for template creation."""
    icon_url: Optional[str] = Field(None, description="URL of the template's icon")
    is_custom: bool = Field(False, description="Whether this is a custom template")

class TemplateUpdate(BaseModel):
    """Schema for template update."""
    title: Optional[str] = Field(None, description="New template title")
    icon_url: Optional[str] = Field(None, description="New template icon URL")
    # category: Optional[TemplateCategory] = Field(None, description="New template category")

class TemplateResponse(TemplateBase):
    """Schema for template response."""
    id: UUID
    user_id: Optional[str]
    title: str
    icon_url: Optional[str] = None
    content_url: Optional[str] = None
    is_custom: bool
    category: TemplateCategory
    meta_data: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class TemplateList(BaseModel):
    """Schema for list of templates."""
    templates: List[TemplateResponse]
    total: int
    page: int
    page_size: int

    model_config = ConfigDict(from_attributes=True)
