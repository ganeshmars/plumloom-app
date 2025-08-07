# app/schemas/chat.py
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from typing import Optional, List, Dict, Any
from enum import Enum
from uuid import UUID
from datetime import datetime


class ChatKnowledgeScope(str, Enum):
    DEFAULT = "default"
    PAGE = "page"
    WORKSPACE = "workspace"
    TEMPLATE = "template"


class ContextType(str, Enum):
    USER_SELECTED_UPLOADED_DOCUMENTS = "user_selected_uploaded_documents"
    SCOPED_PAGE_CONTENT = "scoped_page_content"
    SCOPED_PAGE_WITH_WORKSPACE_AUGMENTATION = "scoped_page_with_workspace_augmentation"
    SCOPED_WORKSPACE_CONTENT = "scoped_workspace_content"
    SCOPED_DEFAULT_KNOWLEDGE_WORKSPACE_AWARE = "scoped_default_knowledge_workspace_aware"
    SCOPED_DEFAULT_KNOWLEDGE_TENANT_WIDE = "scoped_default_knowledge_tenant_wide"
    SCOPED_TEMPLATE_CONTENT = "scoped_template_content"
    ORIGINAL_TEMPLATE_CONTENT = "original_template_content" # Added for when using the direct markdown from a template's GCS URL
    NO_CONTEXT_USED = "no_context_used"
    CSV_DATA_INSIGHTS = "csv_data_insights"


class CitationScopeType(str, Enum):
    FOCUSED_DOCUMENT = "focused_document"
    KNOWLEDGE_BASE_PAGE = "knowledge_base_page"
    KNOWLEDGE_BASE_WORKSPACE = "knowledge_base_workspace"
    KNOWLEDGE_BASE_DEFAULT = "knowledge_base_default"
    KNOWLEDGE_BASE_AUGMENTATION = "knowledge_base_augmentation"

class ChatRequest(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra='forbid')

    query: str = Field(..., description="The user's query for the chatbot.")

    chat_conversation_id: str = Field(
        ...,
        description="The ID of the current chat conversation. Used as session_id for tracing."
    )

    selected_uploaded_document_ids: Optional[List[str]] = Field(
        default=None,
        description="List of specific uploaded document IDs (UUIDs from UploadedDocument table) to focus on. If provided, this takes precedence."
    )

    knowledge_scope: ChatKnowledgeScope = Field(
        default=ChatKnowledgeScope.DEFAULT,
        description="The general knowledge base scope if no specific uploaded documents are selected. This may be overridden if a CSV is detected in selected_uploaded_document_ids."
    )
    knowledge_scope_id: Optional[str] = Field(
        default=None,
        description="Identifier related to 'knowledge_scope' (e.g., page_id for PAGE, workspace_id for WORKSPACE/DEFAULT, template_id for TEMPLATE)."
    )

    workspace_id: Optional[str] = Field(
        default=None,
        description="The ID of the current workspace. Required for PAGE scope and used by DEFAULT scope for better context."
    )

    @field_validator('selected_uploaded_document_ids', 'chat_conversation_id', 'knowledge_scope_id', 'workspace_id',
                     mode='before')
    @classmethod
    def validate_uuids(cls, v: Any, info):
        if v is not None:
            if isinstance(v, list):
                for item_id in v:
                    try:
                        UUID(item_id)
                    except ValueError:
                        raise ValueError(f"Invalid UUID format in {info.field_name}: {item_id}")
            else:
                try:
                    UUID(str(v))
                except ValueError:
                    raise ValueError(f"Invalid UUID format for {info.field_name}: {v}")
        return v


    @model_validator(mode='after')
    def check_scope_requirements(self) -> 'ChatRequest':
        knowledge_scope = self.knowledge_scope
        knowledge_scope_id = self.knowledge_scope_id
        workspace_id = self.workspace_id
        selected_docs = self.selected_uploaded_document_ids

        if not selected_docs:
            if knowledge_scope == ChatKnowledgeScope.PAGE:
                if not knowledge_scope_id:
                    raise ValueError("knowledge_scope_id (as page_id) is required for PAGE scope.")
                if not workspace_id:
                    raise ValueError("workspace_id is required for PAGE scope to provide broader context.")
            elif knowledge_scope == ChatKnowledgeScope.WORKSPACE:
                if not knowledge_scope_id and not workspace_id:
                     raise ValueError("Either knowledge_scope_id or workspace_id must be provided for WORKSPACE scope.")
            elif knowledge_scope == ChatKnowledgeScope.DEFAULT:
                 pass
            elif knowledge_scope == ChatKnowledgeScope.TEMPLATE:
                if not knowledge_scope_id:
                    raise ValueError("knowledge_scope_id (as template_id) is required for TEMPLATE scope.")
        return self


class Citation(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra='forbid')
    source_label: str = Field(..., description="The label used in the LLM context (e.g., '[1]', '[2]').")
    document_id: str = Field(..., description="The ID of the source document (either UploadedDocument.uploaded_document_id or Document.document_id).")
    title: Optional[str] = Field(default=None, description="The title of the source document.")
    preview: str = Field(..., description="A short preview of the cited content chunk.")
    scope_type: CitationScopeType = Field(..., description="Indicates whether the source was a focused document or from the general knowledge base.")
    source_url: Optional[str] = Field(default=None, description="The GCS URL (file_path) if the source is a focused document (UploadedDocument), otherwise null.")
    # chunk_order: Optional[int] = Field(default=None, description="The order of the chunk within the document.") # Optional, can be added if needed by frontend


class ChatResponse(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra='forbid')

    answer: str = Field(..., description="The chatbot's generated answer.")
    session_id: str = Field(...,
                            description="The session ID used for the conversation trace (typically chat_conversation_id).")
    trace_id: Optional[str] = Field(default=None, description="The Langfuse trace ID for observability.")
    llm_used: Optional[str] = Field(default=None, description="The LLM provider.")
    error: Optional[str] = Field(default=None, description="Any error message if the process failed.")
    context_type_used: Optional[ContextType] = Field(default=None, description="The type of context primarily used.")
    retrieved_document_ids: Optional[List[str]] = Field(default=None,
                                                        description="List of document/page IDs used for context. For CSV mode, this will be the ID of the processed CSV.")
    retrieved_page_ids_for_augmentation: Optional[List[str]] = Field(default=None,
                                                                     description="List of Page IDs retrieved for workspace augmentation if PAGE scope was used.")
    citations: Optional[List[Citation]] = Field(default=None, description="List of citations for the answer, linking parts of the answer to specific sources.")
    plot_data: Optional[Dict[str, Any]] = Field(default=None, description="JSON data for rendering a plot (e.g., for Plotly.js).") # New
    is_plot_available: bool = Field(default=False, description="Indicates if plot data is available in the response.") # New


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: UUID
    conversation_id: UUID
    sender_type: str
    sender_user_id: str
    message_content: str
    timestamp: datetime
    meta_data: Dict[str, Any]


class ChatConversationCreate(BaseModel):
    workspace_id: UUID = Field(..., description="ID of the workspace this conversation belongs to")
    conversation_title: Optional[str] = Field(None, description="Title of the conversation")
    meta_data: Optional[Dict[str, Any]] = Field(default={}, description="Additional metadata for the conversation")


class ChatConversationCreateResponse(BaseModel):
    id: UUID = Field(..., description="ID of the conversation")
    workspace_id: UUID = Field(..., description="ID of the workspace this conversation belongs to")
    conversation_title: Optional[str]
    meta_data: Optional[Dict[str, Any]]
    started_at: Optional[datetime]
    updated_at: Optional[datetime]
    conversation_status: Optional[str]


class ChatConversationUpdate(BaseModel):
    conversation_title: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None


class ChatConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conversation_id: UUID
    user_id: str
    workspace_id: UUID
    conversation_title: Optional[str]
    started_at: datetime
    updated_at: datetime
    opened_at: Optional[datetime] = None
    meta_data: Dict[str, Any]
    messages: Optional[List[ChatMessageResponse]] = None


class ListChatConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conversation_id: UUID
    user_id: str
    workspace_id: UUID
    conversation_title: Optional[str]
    started_at: datetime
    updated_at: datetime
    meta_data: Dict[str, Any]


class ChatConversationListResponse(BaseModel):
    items: List[ChatConversationCreateResponse]
    total: int
    page: int
    page_size: int