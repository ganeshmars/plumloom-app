from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel

class RecentItemResponse(BaseModel):
    item_id: UUID
    title: str
    workspace_id: UUID
    workspace_name: str
    updated_at: datetime
    item_type: str
    opened_at: Optional[datetime] = None
    parent_id: Optional[UUID] = None

class RecentItemsList(BaseModel):
    items: List[RecentItemResponse]
    total: int
    page: int
    size: int
    total_pages: int
