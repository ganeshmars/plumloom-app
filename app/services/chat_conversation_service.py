from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from fastapi import Depends
from datetime import datetime, timezone

from app.models.chat_conversation import ChatConversation
from app.models.chat_message import ChatMessage
from app.core.database import get_db
from app.schemas.chat import (
    ChatConversationCreate, 
    ChatConversationUpdate,
    ChatConversationListResponse,
    ChatConversationResponse,
    ListChatConversationResponse,
    ChatConversationCreateResponse,
    ChatMessageResponse,
    # ChatMessageFeedbackUpdate
)
from app.core.logging_config import logger

class ChatConversationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_conversation(
        self, 
        user_id: str, 
        data: ChatConversationCreate
    ) :
        """Create a new chat conversation"""
        try:
            conversation = ChatConversation(
                conversation_id=uuid4(),
                user_id=user_id,
                workspace_id=data.workspace_id,
                conversation_title=data.conversation_title,
                icon=data.icon,
                meta_data=data.meta_data or {}
            )
            
            self.db.add(conversation)
            await self.db.commit()
            await self.db.refresh(conversation)
            
            logger.info(f"Successfully created chat conversation: {conversation.conversation_id}")
            
            # Convert to ChatConversationCreateResponse before returning
            response = ChatConversationCreateResponse(
                id=conversation.conversation_id,
                workspace_id=conversation.workspace_id,
                conversation_title=conversation.conversation_title,
                icon=conversation.icon,
                meta_data=conversation.meta_data,
                started_at=conversation.started_at,
                updated_at=conversation.updated_at,
                conversation_status=conversation.conversation_status
            )
            
            return response
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to create chat conversation: {str(e)}")
            raise RuntimeError(f"Failed to create chat conversation: {str(e)}")

    async def get_conversation(
        self, 
        conversation_id: UUID, 
        user_id: str,
        include_messages: bool = False
    ) -> Optional[ChatConversation]:
        """Get a conversation by ID with optional message loading"""
        try:
            query = select(ChatConversation).where(
                ChatConversation.conversation_id == conversation_id,
                ChatConversation.user_id == user_id
            )

            if include_messages:
                # Load messages along with the conversation
                query = query.options(selectinload(ChatConversation.messages))
                
            result = await self.db.execute(query)
            conversation = result.scalar_one_or_none()
            if conversation:
                conversation.opened_at = datetime.now(timezone.utc)
                self.db.add(conversation)
                await self.db.commit()
                await self.db.refresh(conversation)
            return conversation
        except Exception as e:
            logger.error(f"Error retrieving chat conversation {conversation_id}: {str(e)}")
            raise RuntimeError(f"Failed to retrieve chat conversation: {str(e)}")

    async def list_conversations(
        self,
        user_id: str,
        workspace_id: Optional[UUID] = None,
        page: int = 1,
        page_size: int = 10
    ) :
        """List conversations with pagination and optional workspace filtering"""
        try:
            # Base query
            query = select(ChatConversation).where(ChatConversation.user_id == user_id)
            
            # Apply workspace filter if provided
            if workspace_id:
                query = query.filter(ChatConversation.workspace_id == workspace_id)
                
            # Get total count
            count_query = select(func.count()).select_from(query.subquery())
            total = await self.db.scalar(count_query) or 0
            
            # Apply sorting by most recent first
            query = query.order_by(ChatConversation.updated_at.desc())
            
            # Apply pagination
            query = query.offset((page - 1) * page_size).limit(page_size)
            
            # Execute query
            result = await self.db.execute(query)
            conversation_models = result.scalars().all()
            
            # Convert model instances to ChatConversationCreateResponse objects
            conversations = [
                ChatConversationCreateResponse(
                    id=conv.conversation_id,
                    workspace_id=conv.workspace_id,
                    conversation_title=conv.conversation_title,
                    icon=conv.icon,
                    meta_data=conv.meta_data,
                    started_at=conv.started_at,
                    updated_at=conv.updated_at,
                    conversation_status=conv.conversation_status
                ) for conv in conversation_models
            ]
            
            return ChatConversationListResponse(
                items=conversations,
                total=total,
                page=page,
                page_size=page_size
            )
            
        except Exception as e:
            logger.error(f"Failed to list chat conversations: {str(e)}")
            raise RuntimeError(f"Failed to list chat conversations: {str(e)}")

    async def update_conversation(
        self, 
        conversation_id: UUID, 
        user_id: str, 
        data: ChatConversationUpdate
    ) :
        """Update a chat conversation"""
        try:
            # Check if conversation exists and belongs to user
            conversation = await self.get_conversation(conversation_id, user_id)
            if not conversation:
                return None
                
            # Prepare update data
            update_data = {}
            if data.conversation_title is not None:
                update_data["conversation_title"] = data.conversation_title
            if data.icon is not None:
                update_data["icon"] = data.icon
            if data.meta_data is not None:
                update_data["meta_data"] = data.meta_data
                
            if update_data:
                # Update conversation in database
                await self.db.execute(
                    update(ChatConversation)
                    .where(
                        ChatConversation.conversation_id == conversation_id,
                        ChatConversation.user_id == user_id
                    )
                    .values(**update_data)
                )
                await self.db.commit()
                
                # Refresh the in-memory object with the updated values from the database
                await self.db.refresh(conversation)
                
            # Convert to ChatConversationCreateResponse before returning
            response = ChatConversationCreateResponse(
                id=conversation.conversation_id,
                workspace_id=conversation.workspace_id,
                conversation_title=conversation.conversation_title,
                icon=conversation.icon,
                meta_data=conversation.meta_data,
                started_at=conversation.started_at,
                updated_at=conversation.updated_at,
                conversation_status=conversation.conversation_status
            )
                
            return response
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update chat conversation {conversation_id}: {str(e)}")
            raise RuntimeError(f"Failed to update chat conversation: {str(e)}")

    async def delete_conversation(self, conversation_id: UUID, user_id: str) -> bool:
        """Delete a chat conversation"""
        try:
            # Check if conversation exists and belongs to user
            conversation = await self.get_conversation(conversation_id, user_id)
            if not conversation:
                return False
                
            # Delete conversation (will cascade delete messages due to relationship)
            await self.db.delete(conversation)
            await self.db.commit()
            
            logger.info(f"Successfully deleted chat conversation: {conversation_id}")
            return True
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to delete chat conversation {conversation_id}: {str(e)}")
            raise RuntimeError(f"Failed to delete chat conversation: {str(e)}")

    # async def update_message_feedback(
    #     self,
    #     conversation_id: UUID,
    #     message_id: UUID,
    #     user_id: str,
    #     feedback_data: ChatMessageFeedbackUpdate
    # ) -> Optional[ChatMessageResponse]:
    #     """Update feedback for a specific message"""
    #     try:
    #         # First check if the conversation exists and belongs to the user
    #         conversation = await self.get_conversation(
    #             conversation_id=conversation_id,
    #             user_id=user_id
    #         )
            
    #         if not conversation:
    #             logger.warning(f"Conversation {conversation_id} not found or doesn't belong to user {user_id}")
    #             return None
                
    #         # Then find the specific message
    #         query = select(ChatMessage).where(
    #             ChatMessage.message_id == message_id,
    #             ChatMessage.conversation_id == conversation_id
    #         )
            
    #         result = await self.db.execute(query)
    #         message = result.scalar_one_or_none()
            
    #         if not message:
    #             logger.warning(f"Message {message_id} not found in conversation {conversation_id}")
    #             return None
                
    #         # Prepare update data
    #         update_data = {}
    #         current_time = datetime.now()
            
    #         if feedback_data.is_liked is not None:
    #             update_data["is_liked"] = feedback_data.is_liked
                
    #         if feedback_data.feedback_types is not None:
    #             update_data["feedback_types"] = feedback_data.feedback_types
                
    #         if feedback_data.feedback_comment is not None:
    #             update_data["feedback_comment"] = feedback_data.feedback_comment
                
    #         if update_data:
    #             # Add feedback timestamp when any feedback is updated
    #             update_data["feedback_timestamp"] = current_time
                
    #             # Update the message
    #             await self.db.execute(
    #                 update(ChatMessage)
    #                 .where(
    #                     ChatMessage.message_id == message_id,
    #                     ChatMessage.conversation_id == conversation_id
    #                 )
    #                 .values(**update_data)
    #             )
    #             await self.db.commit()
                
    #             # Refresh the message object with updated values
    #             await self.db.refresh(message)
                
    #         # Convert to response model and return
    #         return ChatMessageResponse.model_validate(message)
            
    #     except Exception as e:
    #         await self.db.rollback()
    #         logger.error(f"Error updating message feedback: {str(e)}")
    #         raise RuntimeError(f"Failed to update message feedback: {str(e)}")

# Dependency for FastAPI
async def get_chat_conversation_service(db: AsyncSession = Depends(get_db)) -> ChatConversationService:
    return ChatConversationService(db) 