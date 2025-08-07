from typing import Optional, Union
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, date

from app.core.database import get_db
from app.core.auth import validate_session, check_active_subscription
from app.services.workspace_service import WorkspaceService
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceUpdate,
    WorkspaceResponse,
    WorkspaceList,
    WorkspaceDeleteResponse,
    ParticularWorkspaceResponse,
    PanelStateUpdate
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

@router.post("/create", response_model=WorkspaceResponse, responses={
    200: {"model": WorkspaceResponse},
    403: {"description": "Subscription required"},
    404: {"description": "Not found"}
})
async def create_workspace(
    workspace_data: WorkspaceCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(validate_session)
):
    """Create a new workspace"""
    # Check for active subscription
    # subscription_checker = await check_active_subscription(db)
    # subscription_result = await subscription_checker(user)
    
    # # If subscription check returned an error response, return it
    # if isinstance(subscription_result, JSONResponse):
    #     return subscription_result
        
    # Otherwise proceed with workspace creation
    workspace_service = WorkspaceService(db)
    return await workspace_service.create_workspace(workspace_data, user["id"])

@router.get("/{workspace_id}", response_model=ParticularWorkspaceResponse)
async def get_workspace(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(validate_session)
) -> ParticularWorkspaceResponse:
    """Get a workspace by ID"""
    workspace_service = WorkspaceService(db)
    return await workspace_service.get_workspace(workspace_id, user["id"])

@router.put("/{workspace_id}", response_model=WorkspaceResponse, responses={
    200: {"model": WorkspaceResponse},
    403: {"description": "Subscription required"},
    404: {"description": "Not found"}
})
async def update_workspace(
    workspace_id: UUID,
    workspace_data: WorkspaceUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(validate_session)
):
    """Update a workspace"""
    # Check for active subscription
    # subscription_checker = await check_active_subscription(db)
    # subscription_result = await subscription_checker(user)
    
    # # If subscription check returned an error response, return it
    # if isinstance(subscription_result, JSONResponse):
    #     return subscription_result
    workspace_service = WorkspaceService(db)
    return await workspace_service.update_workspace(workspace_id, workspace_data, user["id"])

@router.delete("/{workspace_id}", response_model=WorkspaceDeleteResponse, responses={
    200: {"model": WorkspaceDeleteResponse},
    403: {"description": "Subscription required"},
    404: {"description": "Not found"}
})
async def delete_workspace(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(validate_session)
):
    """Delete a workspace"""
    # Check for active subscription
    subscription_checker = await check_active_subscription(db)
    # subscription_result = await subscription_checker(user)
    
    # If subscription check returned an error response, return it
    # if isinstance(subscription_result, JSONResponse):
    #     return subscription_result
    workspace_service = WorkspaceService(db)
    return await workspace_service.delete_workspace(workspace_id, user["id"])

@router.get("", response_model=WorkspaceList)
async def list_workspaces(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=100, description="Items per page"),
    name: Optional[str] = Query(None, description="Filter workspaces by name"),
    workspace_type: Optional[str] = Query(None, description="Filter workspaces by type"),
    startdate: Optional[str] = Query(None, description="Filter workspaces created after this date (format: YYYY-MM-DD)"),
    enddate: Optional[str] = Query(None, description="Filter workspaces created before this date (format: YYYY-MM-DD)"),
    sort_by: Optional[str] = Query("created_at", description="Field to sort by (name, created_at, updated_at)"),
    sort_order: Optional[str] = Query("desc", description="Sort order (asc, desc)"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(validate_session)
) -> WorkspaceList:
    """List all workspaces for the current user"""
    workspace_service = WorkspaceService(db)
    try:
        if startdate:   
            startdate = datetime.strptime(startdate, "%Y-%m-%d").date()
        if enddate:
            enddate = datetime.strptime(enddate, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Please use YYYY-MM-DD.")

    return await workspace_service.list_workspaces(
        user["id"], page, size, name, workspace_type, startdate, enddate, sort_by, sort_order
    )

@router.put("/{workspace_id}/panel-state", response_model=WorkspaceResponse, responses={
    200: {"model": WorkspaceResponse},
    403: {"description": "Subscription required"},
    404: {"description": "Not found"}
})
async def update_panel_state(
    workspace_id: UUID,
    panel_state_data: PanelStateUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(validate_session)
):
    """Update a workspace's panel state"""
    # Check for active subscription
    # subscription_checker = await check_active_subscription(db)
    # subscription_result = await subscription_checker(user)
    
    # If subscription check returned an error response, return it
    # if isinstance(subscription_result, JSONResponse):
    #     return subscription_result
    workspace_service = WorkspaceService(db)
    return await workspace_service.update_panel_state(workspace_id, panel_state_data, user["id"])