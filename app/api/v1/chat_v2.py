# app/api/v1/chat_v2.py
from typing import Dict
from fastapi import APIRouter, Depends, HTTPException, Request, status, Body
from uuid import UUID

from app.core.auth import validate_session
from app.schemas.chat_v2 import AgenticChatRequestV2, AgenticChatResponseV2
from app.services.chat_service_v2 import ChatService, get_chat_service # ChatService will be updated
from app.core.logging_config import logger

router = APIRouter(prefix="/chat/v2", tags=["Agentic Chatbot"])


@router.post(
    "/agent",
    response_model=AgenticChatResponseV2,
    summary="Interact with the Agentic AI Chatbot",
)
async def ai_assistant(
        request_data: AgenticChatRequestV2 = Body(...),
        chat_service: ChatService = Depends(get_chat_service),
        current_user: Dict = Depends(validate_session),
        request: Request = None
):
    request_id = getattr(request.state, 'request_id', 'N/A') if request and hasattr(request, 'state') else 'N/A'
    log_details = (
        f"RID:{request_id} - Independent Agent - ChatConvID: {request_data.chat_conversation_id}, "
        f"Query: '{request_data.query[:50]}...'"
    )
    logger.info(f"Received independent agentic chat query: {log_details}")

    try:
        service_result = await chat_service.generate_response(
            request_data=request_data,
            user_data = current_user
        )
        return service_result

    except ValueError as ve:
        logger.warning(f"RID:{request_id} - Invalid input data for agentic chat: {ve}", exc_info=False)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid input data: {str(ve)}")
    except Exception as e:
        logger.exception(f"RID:{request_id} - Unexpected error processing agentic chat request: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="An internal server error occurred while processing your agentic chat request.")

