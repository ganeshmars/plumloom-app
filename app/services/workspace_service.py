import json
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import select, update, delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from sqlalchemy.orm import selectinload
from typing import List, Optional, Dict, Any
from app.models.workspace import Workspace
from app.models.document import Document
from app.models.chat_conversation import ChatConversation
from app.models.uploaded_document import UploadedDocument
from app.models.users import User
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceUpdate,
    WorkspaceResponse,
    WorkspaceList,
    WorkspaceDeleteResponse,
    PanelStateUpdate,
    ParticularWorkspaceResponse,
    ListWorkspaceResponse
)
from app.core.exceptions import NotFoundException, ForbiddenException
from app.core.logging_config import logger


class WorkspaceService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def create_workspace(self, workspace_data: WorkspaceCreate, user_id: str) -> WorkspaceResponse:
        """Create a new workspace"""
        workspace = Workspace(
            workspace_id=uuid4(),
            user_id=user_id,
            name=workspace_data.name,
            icon_url=workspace_data.icon_url,
            workspace_type=workspace_data.workspace_type,
            description=workspace_data.description
        )
        self.db.add(workspace)
        await self.db.commit()
        await self.db.refresh(workspace)
        return WorkspaceResponse.model_validate(workspace)

    async def get_workspace(self, workspace_id: UUID, user_id: str) -> ParticularWorkspaceResponse:
        """Get a workspace by ID"""
        workspace = await self._get_workspace_or_404(workspace_id, user_id)
        workspace.opened_at = datetime.now(timezone.utc)
        self.db.add(workspace)
        await self.db.commit()
        await self.db.refresh(workspace)
        return ParticularWorkspaceResponse.model_validate(workspace)
        
    
    
    async def update_workspace(
        self, workspace_id: UUID, workspace_data: WorkspaceUpdate, user_id: str
    ) -> WorkspaceResponse:
        """Update a workspace"""
        workspace = await self._get_workspace_or_404(workspace_id, user_id)
        
        # Check ownership
        if workspace.user_id != user_id:
            raise ForbiddenException("Only the workspace owner can update it")

        update_data = workspace_data.model_dump(exclude_unset=True)

        meta_data_config = {
            "last_opened_page_id": lambda x: str(x) if x is not None else None,
            "is_nav_panel_opened": None,
            "nav_panel_divider_position": None,
            "is_chat_panel_opened": None,
        }
        
        meta_data_values_to_set = {}
        meta_data_keys_to_remove = []
        processed_any_meta_data = False

        for key, transform_func in meta_data_config.items():
            if key in update_data:
                value = update_data.pop(key)
                processed_any_meta_data = True
                if value is not None:
                    processed_value = transform_func(value) if transform_func else value
                    meta_data_values_to_set[key] = processed_value
                else:
                    meta_data_keys_to_remove.append(key)

        if processed_any_meta_data:
            if workspace.meta_data is None:
                workspace.meta_data = {}
            elif isinstance(workspace.meta_data, str):
                try:
                    parsed_meta = json.loads(workspace.meta_data)
                    if isinstance(parsed_meta, dict):
                        workspace.meta_data = parsed_meta
                    else:
                        workspace.meta_data = {} 
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse existing meta_data string for workspace {workspace_id}. Resetting to empty dict.")
                    workspace.meta_data = {}
            
            if not isinstance(workspace.meta_data, dict): # Final check
                logger.warning(f"workspace.meta_data for {workspace_id} was not a dict after attempting to load. Resetting.")
                workspace.meta_data = {}

            if meta_data_values_to_set:
                workspace.meta_data.update(meta_data_values_to_set)
            
            for key_to_remove in meta_data_keys_to_remove:
                if key_to_remove in workspace.meta_data:
                    del workspace.meta_data[key_to_remove]

            workspace.meta_data = dict(workspace.meta_data)

            logger.info(f"Updated workspace {workspace_id} meta_data. Changes: set={meta_data_values_to_set}, removed/nulled={meta_data_keys_to_remove}")
            logger.info(f"New meta_data for workspace {workspace_id}: {workspace.meta_data}")

        for key, value in update_data.items():
            setattr(workspace, key, value)

        await self.db.commit()
        await self.db.refresh(workspace)
        return WorkspaceResponse.model_validate(workspace)

    async def delete_workspace(self, workspace_id: UUID, user_id: str) -> WorkspaceDeleteResponse:
        """Delete a workspace and all associated content
        
        This method deletes the workspace from the database and triggers a background task
        to clean up all associated resources, including:
        - Documents (handled by cascade)
        - Chat conversations (handled by cascade)
        - Uploaded documents (handled by cascade)
        - Vector database entries
        - Redis cache entries
        - Files stored in Google Cloud Storage
        """
        from app.tasks.tasks import delete_workspace_resources
        
        workspace = await self._get_workspace_or_404(workspace_id, user_id)
        
        # Check ownership
        if workspace.user_id != user_id:
            raise ForbiddenException("Only the workspace owner can delete it")
        
        # Get user to access tenant information
        user_query = await self.db.execute(select(User).where(User.id == user_id))
        user = user_query.scalar_one_or_none()
        if not user:
            raise NotFoundException(f"User with ID {user_id} not found")
        
        # Get tenant ID from user's tenants array
        tenant_id = user.tenants[0] if user.tenants else str(workspace_id)  # Fallback to workspace_id if no tenants
        logger.info(f"Using tenant ID {tenant_id} for vector operations")
        
        # Get all related data IDs before deleting the workspace
        document_query = await self.db.execute(select(Document.document_id).where(Document.workspace_id == workspace_id))
        document_ids = [str(doc_id) for doc_id, in document_query.all()]
        
        uploaded_doc_query = await self.db.execute(select(UploadedDocument.uploaded_document_id).where(UploadedDocument.workspace_id == workspace_id))
        uploaded_doc_ids = [str(doc_id) for doc_id, in uploaded_doc_query.all()]

        await self.db.delete(workspace)
        await self.db.commit()
        logger.info(f"Workspace {workspace_id} deleted from database with cascade")

        delete_workspace_resources.delay(
            workspace_id=str(workspace_id),
            user_id=user_id,
            tenant_id=tenant_id,
            document_ids=document_ids,
            uploaded_document_ids=uploaded_doc_ids
        )
        
        logger.info(f"Background cleanup task triggered for workspace {workspace_id}")
        return WorkspaceDeleteResponse(message="Workspace deleted successfully. Resource cleanup in progress.")

    async def list_workspaces(
        self, user_id: str, page: int = 1, size: int = 10, name: Optional[str] = None,
        workspace_type: Optional[str] = None, startdate: Optional[str] = None,
        enddate: Optional[str] = None,
        sort_by: str = "created_at", sort_order: str = "desc"
    ) -> WorkspaceList:
        """List all workspaces for a user with document and chat conversation counts"""
        # Calculate offset
        offset = (page - 1) * size

        # Base filter condition
        filter_conditions = [Workspace.user_id == user_id]
        
        # Add optional filters
        if name:
            filter_conditions.append(Workspace.name.ilike(f"%{name}%"))
        if workspace_type:
            filter_conditions.append(Workspace.workspace_type == workspace_type)
        if startdate:
            filter_conditions.append(Workspace.created_at >= startdate)
        if enddate:
            filter_conditions.append(Workspace.created_at <= enddate)

        # Determine sort field and direction
        valid_sort_fields = {
            "name": Workspace.name,
            "created_at": Workspace.created_at,
            "updated_at": Workspace.updated_at
        }
        sort_field = valid_sort_fields.get(sort_by, Workspace.created_at)
        order_by_clause = sort_field.asc() if sort_order.lower() == "asc" else sort_field.desc()

        # Create subqueries for counts
        doc_count_subq = (
            select(Document.workspace_id, func.count().label('doc_count'))
            .group_by(Document.workspace_id)
            .subquery()
        )

        conv_count_subq = (
            select(ChatConversation.workspace_id, func.count().label('conv_count'))
            .group_by(ChatConversation.workspace_id)
            .subquery()
        )

        # Main query with left joins to include workspaces with zero counts
        query = (
            select(
                Workspace,
                func.coalesce(doc_count_subq.c.doc_count, 0).label('document_count'),
                func.coalesce(conv_count_subq.c.conv_count, 0).label('conversation_count')
            )
            .outerjoin(doc_count_subq, Workspace.workspace_id == doc_count_subq.c.workspace_id)
            .outerjoin(conv_count_subq, Workspace.workspace_id == conv_count_subq.c.workspace_id)
            .filter(*filter_conditions)
            .order_by(order_by_clause)
        )

        # Get total count
        total = await self.db.scalar(
            select(func.count()).select_from(query.subquery())
        )

        # Get paginated results
        result = await self.db.execute(
            query.offset(offset).limit(size)
        )
        rows = result.all()

        # Create response
        workspace_responses = []
        for workspace, doc_count, conv_count in rows:
            workspace_data = ListWorkspaceResponse.model_validate(workspace).model_dump()
            workspace_data["document_count"] = doc_count
            workspace_data["conversation_count"] = conv_count
            workspace_responses.append(ListWorkspaceResponse.model_validate(workspace_data))

        return WorkspaceList(
            items=workspace_responses,
            total=total,
            page=page,
            size=size,
            total_pages=max(1, -(-total // size))  # Ensure at least 1 page
        )

    async def _get_workspace_or_404(self, workspace_id: UUID, user_id: str) -> Workspace:
        """Get a workspace by ID or raise 404"""
        query = select(Workspace).filter(
            Workspace.workspace_id == workspace_id,
            Workspace.user_id == user_id
        )
        result = await self.db.execute(query)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise NotFoundException(f"Workspace {workspace_id} not found")
        return workspace

    async def update_panel_state(self, workspace_id: UUID, panel_state_data: PanelStateUpdate, user_id: str):
        """Update a workspace's panel state"""
        workspace = await self._get_workspace_or_404(workspace_id, user_id)
        
        # Update the panel state
        workspace.panel_state = panel_state_data.panel_state
        workspace.updated_at = datetime.utcnow()
        
        # Ensure meta_data is a dictionary
        if workspace.meta_data is None:
            workspace.meta_data = {}
        
        await self.db.commit()
        await self.db.refresh(workspace)
        
        return workspace
