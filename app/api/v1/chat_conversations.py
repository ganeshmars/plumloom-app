# app/api/v1/chat_conversations.py
from typing import Dict, Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logging_config import logger
from app.core.auth import validate_session, AuthError
from app.core.database import get_db
from app.core.constants import FeedbackOptions
from app.schemas.chat import (
    ChatConversationCreate,
    ChatConversationUpdate,
    ChatConversationResponse,
    ChatConversationListResponse,
    ChatMessageResponse,
    ChatConversationCreateResponse,
    # ChatMessageFeedbackUpdate
)
from app.services.chat_conversation_service import ChatConversationService, get_chat_conversation_service

router = APIRouter(prefix="/conversations", tags=["Chat Conversations"])

@router.post(
    "/",
    response_model=ChatConversationCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new chat conversation",
    description="Create a new chat conversation for the authenticated user.",
)
async def create_conversation(
    request_data: ChatConversationCreate,
    chat_service: ChatConversationService = Depends(get_chat_conversation_service),
    current_user: Dict = Depends(validate_session),
):
    """
    Create a new chat conversation.
    """
    logger.critical(f"Creating new chat conversation for user_id: {current_user.get('id')}")
    user_id = current_user.get("id")
    if not user_id:
        logger.error("User ID not found in validated session data.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not identify user from session."
        )

    logger.info(f"Creating new chat conversation for user_id: {user_id}")

    try:
        conversation = await chat_service.create_conversation(user_id, request_data)
        return conversation
    except Exception as e:
        logger.exception(f"Error creating chat conversation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create chat conversation: {str(e)}"
        )

@router.get(
    "/",
    response_model=ChatConversationListResponse,
    summary="List user's chat conversations",
    description="List all chat conversations for the authenticated user with pagination.",
)
async def list_conversations(
    workspace_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    chat_service: ChatConversationService = Depends(get_chat_conversation_service),
    current_user: Dict = Depends(validate_session),
):
    """
    List all chat conversations for the user.
    """
    user_id = current_user.get("id")
    if not user_id:
        logger.error("User ID not found in validated session data.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not identify user from session."
        )

    logger.info(f"Listing chat conversations for user_id: {user_id}")

    try:
        conversations = await chat_service.list_conversations(
            user_id=user_id,
            workspace_id=workspace_id,
            page=page,
            page_size=page_size
        )
        return conversations
    except Exception as e:
        logger.exception(f"Error listing chat conversations: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list chat conversations: {str(e)}"
        )

@router.get(
    "/{conversation_id}",
    response_model=ChatConversationResponse,
    summary="Get a chat conversation",
    description="Get details of a specific chat conversation by ID.",
)
async def get_conversation(
    conversation_id: UUID,
    chat_service: ChatConversationService = Depends(get_chat_conversation_service),
    current_user: Dict = Depends(validate_session),
):
    """
    Get a specific chat conversation by ID.
    """
    user_id = current_user.get("id")
    if not user_id:
        logger.error("User ID not found in validated session data.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not identify user from session."
        )

    logger.info(f"Getting chat conversation {conversation_id} for user_id: {user_id}")

    try:
        conversation = await chat_service.get_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            include_messages=True  # Always include messages
        )
        
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chat conversation {conversation_id} not found."
            )
        
        # Create a new response object instead of returning the model directly
        messages = []
        if hasattr(conversation, 'messages') and conversation.messages:
            # Convert each message model to a response schema
            messages = []
            for msg in conversation.messages:
                # Create a dictionary of message fields, handling None values
                msg_data = {
                    "message_id": msg.message_id,
                    "conversation_id": msg.conversation_id,
                    "sender_type": msg.sender_type,
                    "message_content": msg.message_content,
                    "timestamp": msg.timestamp,
                    "meta_data": msg.meta_data or {},
                    "message_type": msg.message_type,
                    # sender_user_id is required in the schema, provide empty string if None
                    "sender_user_id": msg.sender_user_id or ""
                }
                if msg.is_liked is not None:
                    msg_data["is_liked"] = msg.is_liked
                if msg.feedback_types is not None:
                    msg_data["feedback_types"] = msg.feedback_types
                if msg.feedback_comment is not None:
                    msg_data["feedback_comment"] = msg.feedback_comment
                if msg.feedback_timestamp is not None:
                    msg_data["feedback_timestamp"] = msg.feedback_timestamp
                
                # Create response object with validated data
                messages.append(ChatMessageResponse(**msg_data))
            
        response = ChatConversationResponse(
            conversation_id=conversation.conversation_id,
            user_id=conversation.user_id,
            workspace_id=conversation.workspace_id,
            conversation_title=conversation.conversation_title,
            icon=conversation.icon,
            started_at=conversation.started_at,
            updated_at=conversation.updated_at,
            opened_at=conversation.opened_at,
            meta_data=conversation.meta_data,
            messages=messages
        )
            
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting chat conversation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get chat conversation: {str(e)}"
        )

@router.patch(
    "/{conversation_id}",
    response_model=ChatConversationCreateResponse,
    summary="Update a chat conversation",
    description="Update a specific chat conversation by ID.",
)
async def update_conversation(
    conversation_id: UUID,
    update_data: ChatConversationUpdate,
    chat_service: ChatConversationService = Depends(get_chat_conversation_service),
    current_user: Dict = Depends(validate_session),
):
    """
    Update a specific chat conversation by ID.
    """
    user_id = current_user.get("id")
    if not user_id:
        logger.error("User ID not found in validated session data.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not identify user from session."
        )

    logger.info(f"Updating chat conversation {conversation_id} for user_id: {user_id}")

    try:
        conversation = await chat_service.update_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            data=update_data
        )
        
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chat conversation {conversation_id} not found."
            )
            
        return conversation
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating chat conversation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update chat conversation: {str(e)}"
        )

@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a chat conversation",
    description="Delete a specific chat conversation by ID.",
)
async def delete_conversation(
    conversation_id: UUID,
    chat_service: ChatConversationService = Depends(get_chat_conversation_service),
    current_user: Dict = Depends(validate_session),
):
    """
    Delete a specific chat conversation by ID.
    """
    user_id = current_user.get("id")
    if not user_id:
        logger.error("User ID not found in validated session data.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not identify user from session."
        )

    logger.info(f"Deleting chat conversation {conversation_id} for user_id: {user_id}")

    try:
        success = await chat_service.delete_conversation(
            conversation_id=conversation_id,
            user_id=user_id
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chat conversation {conversation_id} not found."
            )
            
        return None  # 204 No Content
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error deleting chat conversation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete chat conversation: {str(e)}"
        )

# @router.patch(
#     "/{conversation_id}/messages/{message_id}/feedback",
#     response_model=ChatMessageResponse,
#     summary="Update message feedback",
#     description="Update a message with like/dislike status and user feedback."
# )
# async def update_message_feedback(
#     conversation_id: UUID,
#     message_id: UUID,
#     feedback_data: ChatMessageFeedbackUpdate,
#     chat_service: ChatConversationService = Depends(get_chat_conversation_service),
#     current_user: Dict = Depends(validate_session),
# ):
#     """
#     Update feedback for a specific message in a conversation.
#     """
#     user_id = current_user.get("id")
#     if not user_id:
#         logger.error("User ID not found in validated session data.")
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Could not identify user from session."
#         )

#     logger.info(f"Updating feedback for message {message_id} in conversation {conversation_id}")

#     try:
#         message = await chat_service.update_message_feedback(
#             conversation_id=conversation_id,
#             message_id=message_id,
#             user_id=user_id,
#             feedback_data=feedback_data
#         )
        
#         if not message:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND,
#                 detail=f"Message {message_id} not found in conversation {conversation_id}."
#             )
            
#         return message
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.exception(f"Error updating message feedback: {str(e)}")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to update message feedback: {str(e)}"
#         )

# @router.get(
#     "/feedback-types",
#     response_model=Dict[str, str],
#     summary="Get feedback type options",
#     description="Get all available feedback type options for chat messages."
# )
# async def get_feedback_types():
#     """
#     Returns all available feedback types for chat messages with their display names.
#     """
#     return FeedbackOptions.DISPLAY_NAMES

