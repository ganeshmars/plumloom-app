from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import validate_session
from app.core.database import get_db
from app.schemas.user_preference import (
    UserPreferencesUpdate,
    UserPreferencesResponse
)
from app.services.user_preference_service import UserPreferenceService

router = APIRouter(prefix="/preferences", tags=["User Preferences"])

@router.get("/", response_model=UserPreferencesResponse)
async def get_user_preferences(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(validate_session)  # user['id'] contains the user_id
):
    """
    Retrieve the authenticated user's preferences.
    Returns the full preference object, including global and workspace-specific settings.
    """
    preference_service = UserPreferenceService(db_session=db)
    try:
        preferences = await preference_service.get_preferences(user_id=user["id"])
        return preferences
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve user preferences.")

@router.put("/", response_model=UserPreferencesResponse)
async def update_user_preferences(
    preference_data: UserPreferencesUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(validate_session)  # user['id'] contains the user_id
):
    """
    Update the authenticated user's preferences.
    Accepts a partial update. Only provided fields will be modified.
    """
    preference_service = UserPreferenceService(db_session=db)
    try:
        updated_preferences = await preference_service.update_preferences(user_id=user["id"], data=preference_data)
        return updated_preferences
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update user preferences: {str(e)}")
