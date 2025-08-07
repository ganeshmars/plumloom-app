# app/schemas/chat_v2.py
from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Any
from uuid import UUID
import enum

class ChatContextType(str, enum.Enum):
    """
    Defines the primary context selected by the user via UI dropdown.
    - default_chat: Standard chat/brainstorming. Workspace is secondary context.
    - page: Focused on a specific page. Workspace is secondary context.
    - workspace: Focused on the entire workspace content as primary context.
    - template: Focused on a specific template (identified by page_id). Workspace is secondary context.
    If uploaded_document_ids are provided in the request, they typically become the
    primary focus, and the backend determines specific handling (e.g., for CSVs vs PDFs).
    """
    DEFAULT_CHAT = "default_chat"
    PAGE = "page"
    WORKSPACE = "workspace"
    TEMPLATE = "template"
    DOCUMENT = "document"


class AgenticChatRequestV2(BaseModel):
    query: str = Field(..., description="The user's query for the agentic chatbot.")
    chat_conversation_id: UUID = Field(
        ...,
        description="The ID of the current chat conversation. Used as session_id for tracing."
    )
    workspace_id: UUID = Field(
        ...,
        description="The ID of the workspace associated with the chat. Often serves as secondary context, or primary if context_type is 'workspace'."
    )
    context_type: ChatContextType = Field(
        default=ChatContextType.DEFAULT_CHAT,
        description="The context type selected by the user (e.g., from a dropdown)."
    )
    page_id: Optional[UUID] = Field(
        default=None,
        description="The ID of the specific page if context_type is 'page', or the ID of the template's page if context_type is 'template'."
    )
    uploaded_document_ids: Optional[List[UUID]] = Field(
        default=None,
        description="A list of IDs for uploaded documents (e.g., PDF, TXT, CSV). If provided, these often become the primary context. The backend will differentiate document types (e.g., CSV vs PDF) for specialized processing and determine if secondary workspace context is muted."
    )

    @model_validator(mode='after')
    def check_page_id_for_context_type(cls, values):
        context_type, page_id = values.context_type, values.page_id
        if context_type == ChatContextType.PAGE and page_id is None:
            raise ValueError("page_id is required when context_type is 'page'")
        if context_type == ChatContextType.TEMPLATE and page_id is None:
            raise ValueError("page_id is required to identify the template when context_type is 'template'")
        return values


class AgenticChatResponseV2(BaseModel):
    answer: str = Field(..., description="The agent's final answer.")
    session_id: UUID = Field(...,
                             description="The session ID used for the conversation trace (typically chat_conversation_id).")
    error: Optional[str] = Field(default=None, description="Any error message if the process failed.")
    citations: Optional[List['Citation']] = Field(default=None, description="List of citations used in the answer.") # Forward reference


class Citation(BaseModel):
    source_label: str = Field(..., description="The label used in the text, e.g., '[1]'")
    document_id: Optional[UUID] = Field(None, description="UUID of the source document/page")
    title: Optional[str] = Field(None, description="Title of the source document/page")
    scope_type: Optional[str] = Field(None, description="Type of source, e.g., 'page', 'document', 'workspace_chunk'")
    source_url: Optional[str] = Field(None, description="Direct URL to the source, if available")
    text_content_chunk: Optional[str] = Field(None, description="The actual text chunk used for citation")

# Pydantic v2 typically handles string forward references for List['Citation'] automatically.
# If issues arise, AgenticChatResponseV2.model_rebuild() might be needed after Citation definition.
