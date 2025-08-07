from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Dict, Any, Optional
from uuid import UUID
from enum import Enum

class ThemeMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"

class WorkspacePreferenceState(BaseModel):
    """Preferences specific to a single workspace."""
    last_opened_page_id: Optional[UUID] = None
    is_nav_panel_opened: bool = True
    nav_panel_divider_position: Optional[int] = 280  # Default width in pixels
    is_chat_panel_opened: bool = False

    model_config = ConfigDict(from_attributes=True)

class WorkspaceFullState(WorkspacePreferenceState):
    """Represents the full state of a workspace, including its ID."""
    id: UUID

class UserPreferences(BaseModel):
    """Defines the structure of user preferences for API responses and internal representation.
    Uses a nested structure for the active workspace.
    """
    view_mode: ThemeMode = ThemeMode.LIGHT
    workspace: Optional[WorkspaceFullState] = None

    model_config = ConfigDict(from_attributes=True)

class WorkspacePartialUpdate(BaseModel):
    """Schema for providing updates related to a workspace, including its ID and partial state."""
    id: Optional[UUID] = None
    last_opened_page_id: Optional[UUID] = None
    is_nav_panel_opened: Optional[bool] = None
    nav_panel_divider_position: Optional[int] = None
    is_chat_panel_opened: Optional[bool] = None

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='after')
    def check_id_if_state_is_updated(cls, data):
        has_state_updates = any(
            getattr(data, field_name) is not None 
            for field_name in data.model_fields 
            if field_name != 'id'
        )
        
        if has_state_updates and data.id is None:
            raise ValueError(
                "'id' must be provided within the 'workspace' object when also providing "
                "other workspace state fields (e.g., 'last_opened_page_id')."
            )
        return data

class UserPreferencesUpdate(BaseModel):
    """Schema for partially updating user preferences using a nested workspace structure."""
    view_mode: Optional[ThemeMode] = None
    workspace: Optional[WorkspacePartialUpdate] = None

    model_config = ConfigDict(extra='forbid')

class UserPreferencesResponse(UserPreferences):
    """Response model for user preferences, identical to the new UserPreferences structure."""
    pass
