# app/api/v1/chat.py
from typing import Dict
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status, Body
from app.core.auth import validate_session, AuthError
from app.schemas.chat import ChatRequest, ChatResponse, ChatKnowledgeScope, ContextType
from app.services.chat_service import ChatService, get_chat_service
from app.services.weaviate.exceptions import VectorStoreOperationError
from app.core.llm_clients import LLMGenerationError
from app.core.logging_config import logger

router = APIRouter(prefix="/chat", tags=["Chatbot"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Interact with the AI Chatbot (Advanced Context RAG)",
)
async def handle_chat_query(
        request_data: ChatRequest = Body(...),
        chat_service: ChatService = Depends(get_chat_service),
        current_user: Dict = Depends(validate_session),
        request: Request = None
):
    user_id = current_user.get("id")
    tenant_id = current_user.get("userTenantId")

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not identify user.")
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not identify user's tenant.")

    request_id = getattr(request.state, 'request_id', 'N/A') if request and hasattr(request, 'state') else 'N/A'
    log_details = (
        f"RID:{request_id} - User: {user_id}, Tenant: {tenant_id}, "
        f"ChatConvID: {request_data.chat_conversation_id}, " # chat_conversation_id is the session identifier
        f"KnowledgeScope: {request_data.knowledge_scope.value}, ScopeID: {request_data.knowledge_scope_id}, "
        f"WorkspaceID: {request_data.workspace_id}, "
        f"SelectedDocs: {len(request_data.selected_uploaded_document_ids) if request_data.selected_uploaded_document_ids else 0}"
    )
    logger.info(f"Received RAG chat query: {log_details}")

    try:
        service_result = await chat_service.generate_response(
            user_id=str(user_id),
            tenant_id=str(tenant_id),
            query=request_data.query,
            chat_conversation_id=request_data.chat_conversation_id, # Pass chat_conversation_id
            selected_uploaded_document_ids=request_data.selected_uploaded_document_ids,
            knowledge_scope=request_data.knowledge_scope,
            knowledge_scope_id=request_data.knowledge_scope_id,
            workspace_id_for_scope=request_data.workspace_id  # Pass workspace_id explicitly for scoping
        )

        if service_result.get("error"):
            logger.error(f"RID:{request_id} - RAG Chat generation failed. Error: {service_result['error']}")
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE # Default for service errors

            error_detail = service_result['error']
            if "Invalid input" in error_detail or "ValueError" in error_detail or "Invalid UUID format" in error_detail:
                status_code = status.HTTP_400_BAD_REQUEST
            elif "LLM service unavailable" in error_detail or "Knowledge base access issue" in error_detail:
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE


            raise HTTPException(
                status_code=status_code,
                detail=f"Chat generation failed: {service_result['error']}"
            )

        logger.info(f"RID:{request_id} - Successfully generated RAG chat response for user {user_id}")
        return ChatResponse(
            answer=service_result["answer"],
            session_id=service_result["session_id"],  # This will be the chat_conversation_id
            trace_id=service_result.get("trace_id"),
            llm_used=service_result.get("llm_used"),
            error=service_result.get("error"),
            context_type_used=service_result.get("context_type_used"),
            retrieved_document_ids=service_result.get("retrieved_document_ids"),
            retrieved_page_ids_for_augmentation=service_result.get("retrieved_page_ids_for_augmentation"),
            citations = service_result.get("citations")
        )

    except ValueError as ve:
        logger.warning(f"RID:{request_id} - Invalid input data: {ve}", exc_info=False)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid input data: {str(ve)}")
    # # Handle specific known exceptions from services if they are not caught and repackaged by the service itself
    # except AuthError as ae: # Example if validate_session could raise a specific AuthError
    #     logger.warning(f"RID:{request_id} - Authentication error: {ae}", exc_info=False)
    #     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(ae))
    # # Generic exception handler for unexpected errors
    # except Exception as e:
    #     logger.exception(f"RID:{request_id} - Unexpected error processing RAG chat request: {e}", exc_info=True)
    #     raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    #                         detail="An internal server error occurred.")