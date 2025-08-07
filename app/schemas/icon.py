from typing import Optional, List, Dict, Any, Union
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field

from app.models.icon import IconCategory, IconType, IconMode, IconFormat


class IconBase(BaseModel):
    name: str
    type: IconType
    mode: IconMode = IconMode.LIGHT
    file_format: IconFormat
    
    class Config:
        use_enum_values = True


class IconCreate(IconBase):
    user_id: Optional[str] = None
    gcs_path: str
    url: str
    file_size: Optional[int] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class IconUpdate(BaseModel):
    name: Optional[str] = None
    mode: Optional[IconMode] = None
    meta_data: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    
    class Config:
        use_enum_values = True


class IconResponse(IconBase):
    id: UUID
    user_id: Optional[str] = None
    gcs_path: str
    url: str
    file_size: Optional[int] = None
    meta_data: Dict[str, Any] = {}
    tags: List[str] = []
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# Add a new response model for the grouped icons
class GroupedIconsResponse(BaseModel):
    user: List[IconResponse] = []
    app: List[IconResponse] = []