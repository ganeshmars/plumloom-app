import json
from typing import Optional
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.users import User
from app.models.workspace import Workspace
from app.schemas.user_preference import (
    UserPreferences,
    UserPreferencesUpdate,
    WorkspacePreferenceState,
    WorkspaceFullState,
    ThemeMode
)

class UserPreferenceService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_user(self, user_id: str) -> User:
        query = select(User).where(User.id == user_id)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return user

    async def get_preferences(self, user_id: str) -> UserPreferences:
        """Retrieves user preferences directly using the nested UserPreferences schema."""
        user = await self.get_user(user_id)
        
        if user.user_metadata:
            try:
                preferences = UserPreferences.model_validate(user.user_metadata)
                return preferences
            except Exception as e:
                print(f"Error parsing user_metadata for user {user_id} into UserPreferences: {e}. Returning defaults.")
                return UserPreferences() 
        return UserPreferences()

    async def update_preferences(self, user_id: str, data: UserPreferencesUpdate) -> UserPreferences:
        """Updates user preferences based on nested API payload and saves in the same nested DB storage."""
        user = await self.get_user(user_id)

        current_prefs = await self.get_preferences(user_id)

        if data.view_mode is not None:
            current_prefs.view_mode = data.view_mode

        if data.workspace is not None:
            payload_ws_data = data.workspace
            payload_ws_id = payload_ws_data.id

            if payload_ws_id is not None:
                if current_prefs.workspace is None or payload_ws_id != current_prefs.workspace.id:
                    current_prefs.workspace = WorkspaceFullState(id=payload_ws_id, **WorkspacePreferenceState().model_dump())

                for field_name in WorkspacePreferenceState.model_fields:
                    payload_value = getattr(payload_ws_data, field_name, None)
                    if payload_value is not None:
                        setattr(current_prefs.workspace, field_name, payload_value)
            elif payload_ws_id is None:
                current_prefs.workspace = None

        user.user_metadata = current_prefs.model_dump(mode='json', exclude_none=False) # Store full object
        self.db.add(user)

        workspace_to_sync_state: Optional[WorkspacePreferenceState] = None
        workspace_to_sync_id_uuid: Optional[UUID] = None

        if current_prefs.workspace is not None:
            workspace_to_sync_id_uuid = current_prefs.workspace.id
            workspace_to_sync_state = WorkspacePreferenceState.model_validate(current_prefs.workspace.model_dump())

        if workspace_to_sync_id_uuid and workspace_to_sync_state:
            workspace_query = select(Workspace).where(Workspace.workspace_id == workspace_to_sync_id_uuid)
            workspace_result = await self.db.execute(workspace_query)
            workspace_to_update = workspace_result.scalar_one_or_none()

            if workspace_to_update:
                current_ws_meta = workspace_to_update.meta_data
                if current_ws_meta is None: current_ws_meta = {}
                elif isinstance(current_ws_meta, str):
                    try: current_ws_meta = json.loads(current_ws_meta)
                    except json.JSONDecodeError: current_ws_meta = {}
                if not isinstance(current_ws_meta, dict): current_ws_meta = {}

                new_meta_data_for_ws = dict(current_ws_meta)
                for key, value in workspace_to_sync_state.model_dump(mode='json', exclude_none=False).items(): 
                    new_meta_data_for_ws[key] = value
                
                workspace_to_update.meta_data = new_meta_data_for_ws
                self.db.add(workspace_to_update)
        
        await self.db.commit()
        await self.db.refresh(user)
        
        return current_prefs
