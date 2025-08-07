# app/services/chat_service_v2.py
import time
import uuid
import tempfile
import os
import json
import asyncio
from typing import Dict, Any, Optional, List, Tuple, TypedDict
from urllib.parse import urlparse
import io
import httpx
from sqlalchemy import select
from uuid import UUID as PyUUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import Document
from app.models.template import Template
import redis.asyncio as aioredis
from fastapi import Depends
from langfuse import Langfuse
from sqlalchemy import select, update as sqlalchemy_update, func
from typing import Dict, List, Optional, Any, TypedDict
import tiktoken
from sqlalchemy.sql import func
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langchain_openai import ChatOpenAI
from langchain_experimental.agents.agent_toolkits import create_csv_agent
from langchain.agents.agent_types import AgentType
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage, BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from pydantic import SecretStr
from langchain.agents.format_scratchpad.openai_tools import format_to_openai_tool_messages
from langchain.agents.output_parsers.openai_tools import OpenAIToolsAgentOutputParser
from langchain.schema import SystemMessage, HumanMessage, AIMessage
from app.core.config import get_settings
from app.core.langfuse_config import get_langfuse
from app.core.llm_clients import BaseLLMClient, get_primary_llm_client, LLMGenerationError
from app.schemas.chat_v2 import AgenticChatRequestV2, AgenticChatResponseV2, ChatContextType, Citation as CitationV2
from app.services.weaviate.page_service_async import PageVectorServiceAsync
from app.services.weaviate.document_service_async import DocumentVectorServiceAsync
from app.services.weaviate.exceptions import VectorStoreOperationError, VectorStoreTenantNotFoundError
from app.services.weaviate import get_page_vector_service_async, get_document_vector_service_async
from app.core.database import get_db
from app.models.chat_message import ChatMessage, SenderType
from app.models.chat_conversation import ChatConversation
from app.models.uploaded_document import UploadedDocument
from app.models.template import Template as TemplateModel
from app.core.redis import get_redis
from app.core.storage import get_file_content_sync
from app.core.logging_config import logger
from app.utils.extract_text import tiptap_json_to_markdown

settings = get_settings()

MAX_HISTORY_TOKENS = 6000


class GraphState(TypedDict):
    query: str
    chat_conversation_id: PyUUID
    messages: List[BaseMessage]
    intermediate_steps: List[Tuple[Any, Any]]
    final_answer: Optional[str]
    error: Optional[str]
    user_id: Optional[str]
    tenant_id: Optional[str]
    db_session: Optional[AsyncSession]
    llm_client: BaseLLMClient
    langfuse_client: Optional[Langfuse]
    workspace_id: Optional[PyUUID]
    context_type: Optional[ChatContextType]
    page_id: Optional[PyUUID]
    uploaded_document_ids: Optional[List[PyUUID]]
    page_specific_context_chunks: Optional[List[Dict[str, Any]]]
    workspace_context_chunks: Optional[List[Dict[str, Any]]]
    retrieved_document_context_chunks: Optional[List[Dict[str, Any]]]
    llm_response: Optional[str]
    llm_messages: Optional[List[str]]
    prompt_template: Optional[str]
    page_prompt: Optional[str]
    page_prompt_template: Optional[str]
    workspace_prompt: Optional[str]
    workspace_prompt_template: Optional[str]
    default_context_prompt: Optional[str]
    default_context_prompt_template: Optional[str]
    uploaded_document_prompt: Optional[str]
    uploaded_document_prompt_template: Optional[str]
    citations: Optional[List[Dict[str, Any]]]
    identified_langfuse_prompt_name: Optional[str]
    template_scope_langfuse_system_prompt: Optional[str]
    template_context_prompt: Optional[str]
    template_context_prompt_template: Optional[str]


class ChatService:
    def __init__(
            self,
            llm: BaseLLMClient = Depends(get_primary_llm_client),
            langfuse_client: Langfuse = Depends(get_langfuse),
            page_vector_service: PageVectorServiceAsync = Depends(get_page_vector_service_async),
            document_vector_service: DocumentVectorServiceAsync = Depends(get_document_vector_service_async),
            db: AsyncSession = Depends(get_db),
            redis: aioredis.Redis = Depends(get_redis),
    ):
        self.llm = llm
        self.langfuse = langfuse_client
        self.page_vector_service = page_vector_service
        self.document_vector_service = document_vector_service
        self.db = db
        self.redis = redis
        self.agentic_graph = self._build_graph()

    def _join_chunks(self, chunks):
        """Helper to join content chunks into a single string."""
        if not chunks:
            return ""
        return "\n---\n".join(
            chunk.get("content", str(chunk)) for chunk in chunks if chunk
        )

    def _filter_chunks_by_certainty(
        self,
        chunks: List[Dict[str, Any]], 
        threshold: float, 
        context_name: str
    ) -> List[Dict[str, Any]]:
        """Filters a list of search result chunks by a minimum certainty score."""
        if not chunks:
            return []
        
        filtered_chunks = [
            chunk for chunk in chunks if chunk.get('certainty', 0.0) >= threshold
        ]
        
        if not filtered_chunks:
            logger.info(f"No {context_name} chunks met the certainty threshold of {threshold}. Original count: {len(chunks)}")
        else:
            logger.info(f"Filtered {context_name} chunks from {len(chunks)} to {len(filtered_chunks)} based on certainty >= {threshold}")
        return filtered_chunks

    async def _retrieve_page_context_node(self, state: GraphState) -> GraphState:
        logger.info(f"Attempting to retrieve page/template context. ContextType: {state.get('context_type')}, PageID: {state.get('page_id')}, WorkspaceID: {state.get('workspace_id')}")
        state['page_specific_context_chunks'] = None
        state['workspace_context_chunks'] = None
        
        citations_list = state.get('citations') or []
        source_counter = len(citations_list) + 1
        
        formatted_page_chunks_for_prompt = []
        formatted_workspace_chunks_for_prompt = []

        prompt_template = (
            "Instructions:\n"
            "Answer the user's question using the provided page content and, if relevant, supplemental workspace content. "
            "Cite your sources using the provided labels (e.g., [1], [2]). "
            "Be specific to the page, but use workspace context if it helps. If the answer is not in the context, say so.\n\n"
            "Page Context:\n{page_context}\n\n"
            "Supplemental Workspace Context:\n{workspace_context}\n\n"
            "User Query:\n{query}"
        )
        context_type = state.get('context_type')
        page_id = state.get('page_id')
        query = state.get('query')
        tenant_id = state.get('tenant_id')
        workspace_id = state.get('workspace_id')

        page_specific_search_limit = 3
        supplemental_workspace_search_limit = 2

        if not query or not tenant_id:
            logger.info("Skipping page/template context retrieval: missing query or tenant_id.")
            state["page_prompt"] = prompt_template.format(page_context="Not available.", workspace_context="Not available.", query=query or "")
            state["page_prompt_template"] = prompt_template
            state['citations'] = citations_list
            return state

        if context_type is ChatContextType.PAGE and page_id and workspace_id:
            # 1. Fetch page-specific context
            page_results_raw = []
            try:
                logger.info(f"Searching page-specific context for PageID: {page_id}, TenantID: {tenant_id}, Query: '{query[:50]}...'" )
                page_results_raw = await self.page_vector_service.search(
                    tenant_id=tenant_id, query=query, doc_id=page_id, workspace_id=workspace_id,
                    limit=page_specific_search_limit, use_hybrid=False
                )
                MIN_PAGE_CONTEXT_CERTAINTY = 0.7
                if page_results_raw:
                    filtered_page_results = self._filter_chunks_by_certainty(
                        page_results_raw,
                        MIN_PAGE_CONTEXT_CERTAINTY,
                        "page-specific context"
                    )
                    state['page_specific_context_chunks'] = filtered_page_results
                    if filtered_page_results:
                        for chunk in filtered_page_results:
                            properties = chunk.get('properties', {})
                            source_label = f"[{source_counter}]"
                            
                            text_content = properties.get('contentChunk')
                            if not text_content:
                                logger.warning(f"Missing 'contentChunk' in page_results_raw properties for chunk: {chunk.get('uuid')}")
                                text_content = str(chunk)

                            chunk_doc_id_str = properties.get('documentId')
                            chunk_doc_id = None
                            if chunk_doc_id_str:
                                try:
                                    chunk_doc_id = PyUUID(str(chunk_doc_id_str))
                                except ValueError:
                                    logger.warning(f"Could not parse documentId '{chunk_doc_id_str}' from page_results_raw properties as UUID. Chunk UUID: {chunk.get('uuid')}")
                            if not chunk_doc_id:
                                fallback_doc_id_str = chunk.get('doc_id') or page_id
                                if fallback_doc_id_str:
                                    try:
                                        chunk_doc_id = PyUUID(str(fallback_doc_id_str))
                                    except ValueError:
                                         logger.warning(f"Could not parse fallback doc_id/page_id '{fallback_doc_id_str}' from page_results_raw chunk as UUID. Chunk UUID: {chunk.get('uuid')}")

                            title = properties.get('title', chunk.get('title', f"Page Context {source_label}"))
                            source_url = chunk.get('source_url')

                            formatted_page_chunks_for_prompt.append(f"{text_content} {source_label}")
                            citation = CitationV2(
                                source_label=source_label,
                                document_id=chunk_doc_id,
                                title=title,
                                scope_type=ChatContextType.PAGE.value,
                                text_content_chunk=text_content,
                                source_url=source_url
                            )
                            citations_list.append(citation.model_dump())
                            source_counter += 1
                    logger.info(f"Successfully retrieved and processed {len(page_results_raw)} page-specific context chunks for PageID: {page_id}.")
                else:
                    logger.info(f"No page-specific context found for PageID: {page_id}.")
            except Exception as e:
                logger.error(f"Error retrieving page-specific context for PageID: {page_id}: {e}", exc_info=True)
            
            # 2. Fetch supplemental workspace-level context
            workspace_results_raw = []
            try:
                logger.info(f"Searching supplemental workspace context for WorkspaceID: {workspace_id}, TenantID: {tenant_id}, Query: '{query[:50]}...'" )
                workspace_results_raw = await self.page_vector_service.search(
                    tenant_id=tenant_id, query=query, doc_id=None, workspace_id=workspace_id,
                    limit=supplemental_workspace_search_limit, use_hybrid=False
                )
                MIN_SUPPLEMENTAL_WORKSPACE_CERTAINTY = 0.7
                if workspace_results_raw:
                    # First, filter by certainty
                    certainty_filtered_workspace_results = self._filter_chunks_by_certainty(
                        workspace_results_raw,
                        MIN_SUPPLEMENTAL_WORKSPACE_CERTAINTY,
                        "supplemental workspace context"
                    )
                    page_specific_chunks_for_dedupe = state.get('page_specific_context_chunks') or []
                    existing_chunk_ids = {c.get('uuid') for c in page_specific_chunks_for_dedupe if c.get('uuid')}
                    
                    final_filtered_workspace_results = [
                        chunk for chunk in certainty_filtered_workspace_results 
                        if chunk.get('uuid') not in existing_chunk_ids
                    ]
                    
                    state['workspace_context_chunks'] = final_filtered_workspace_results
                    if final_filtered_workspace_results:
                        for chunk in final_filtered_workspace_results:
                            properties = chunk.get('properties', {})
                            source_label = f"[{source_counter}]"

                            text_content = properties.get('contentChunk')
                            if not text_content:
                                logger.warning(f"Missing 'contentChunk' in filtered_workspace_results properties for chunk: {chunk.get('uuid')}")
                                text_content = str(chunk)

                            chunk_doc_id_str = properties.get('documentId')
                            chunk_doc_id = None
                            if chunk_doc_id_str:
                                try:
                                    chunk_doc_id = PyUUID(str(chunk_doc_id_str))
                                except ValueError:
                                    logger.warning(f"Could not parse documentId '{chunk_doc_id_str}' from filtered_workspace_results properties as UUID. Chunk UUID: {chunk.get('uuid')}")
                            if not chunk_doc_id:
                                fallback_doc_id_str = chunk.get('doc_id')
                                if fallback_doc_id_str:
                                    try:
                                        chunk_doc_id = PyUUID(str(fallback_doc_id_str))
                                    except ValueError:
                                        logger.warning(f"Could not parse fallback doc_id '{fallback_doc_id_str}' from filtered_workspace_results chunk as UUID. Chunk UUID: {chunk.get('uuid')}")

                            title = properties.get('title', chunk.get('title', f"Workspace Context {source_label}"))
                            source_url = chunk.get('source_url')

                            formatted_workspace_chunks_for_prompt.append(f"{text_content} {source_label}")
                            citation = CitationV2(
                                source_label=source_label,
                                document_id=chunk_doc_id,
                                title=title,
                                scope_type=ChatContextType.WORKSPACE.value,
                                text_content_chunk=text_content,
                                source_url=source_url
                            )
                            citations_list.append(citation.model_dump())
                            source_counter += 1
                    logger.info(f"Successfully retrieved and processed {len(final_filtered_workspace_results)} supplemental workspace context chunks for WorkspaceID: {workspace_id}.")
                else:
                    logger.info(f"No supplemental workspace context found for WorkspaceID: {workspace_id}.")
            except Exception as e:
                logger.error(f"Error retrieving supplemental workspace context for WorkspaceID: {workspace_id}: {e}", exc_info=True)
        else:
            logger.info(f"Skipping page/template context retrieval. ContextType: {context_type}, PageID: {page_id}, WorkspaceID: {workspace_id}. Conditions not met.")

        page_context_str = "\n---\n".join(formatted_page_chunks_for_prompt) if formatted_page_chunks_for_prompt else "No specific page context found or applicable."
        workspace_context_str = "\n---\n".join(formatted_workspace_chunks_for_prompt) if formatted_workspace_chunks_for_prompt else "No supplemental workspace context found or applicable."
        
        formatted_prompt = prompt_template.format(
            page_context=page_context_str,
            workspace_context=workspace_context_str,
            query=state.get("query") or ""
        )
        state["page_prompt"] = formatted_prompt
        state["page_prompt_template"] = prompt_template
        state['citations'] = citations_list
        
        return state

    async def _retrieve_document_context_node(self, state: GraphState) -> GraphState:
        logger.info(f"Attempting to retrieve uploaded document context. DocumentIDs: {state.get('uploaded_document_ids')}")
        state['retrieved_document_context_chunks'] = None
        
        citations_list = state.get('citations') or []
        source_counter = len(citations_list) + 1
        formatted_doc_chunks_for_prompt = []
        doc_titles_map = {}

        prompt_template = (
            "Instructions:\n"
            "Answer the user's question using the provided uploaded document(s). "
            "Cite your sources using the provided labels (e.g., [1], [2]). "
            "If the answer is not in the documents, say so.\n\n"
            "Document Context:\n{document_context}\n\n"
            "User Query:\n{query}"
        )
        
        uploaded_doc_ids = state.get('uploaded_document_ids')
        query = state.get('query')
        tenant_id = state.get('tenant_id')
        db_session = state.get('db_session')

        DOCUMENT_SEARCH_LIMIT_PER_DOC = 2

        if not query or not tenant_id or not db_session:
            logger.warning("Skipping document context retrieval: missing query, tenant_id, or db_session.")
            state["uploaded_document_prompt"] = prompt_template.format(document_context="Context not available due to missing parameters.", query=query or "")
            state["uploaded_document_prompt_template"] = prompt_template
            state['citations'] = citations_list
            return state

        if not uploaded_doc_ids or not isinstance(uploaded_doc_ids, list) or len(uploaded_doc_ids) == 0:
            logger.info("Skipping document context retrieval: no uploaded_document_ids provided.")
            state["uploaded_document_prompt"] = prompt_template.format(document_context="No documents provided for context.", query=query or "")
            state["uploaded_document_prompt_template"] = prompt_template
            state['citations'] = citations_list
            return state

        try:
            stmt = select(UploadedDocument).where(UploadedDocument.id.in_([PyUUID(str(doc_id)) for doc_id in uploaded_doc_ids]))
            results = await db_session.execute(stmt)
            for doc in results.scalars().all():
                doc_titles_map[doc.id] = doc.filename
        except Exception as e:
            logger.error(f"Error fetching uploaded document metadata: {e}", exc_info=True)
        try:
            logger.info(f"Searching document context for DocumentIDs: {uploaded_doc_ids}, TenantID: {tenant_id}, Query: '{query[:50]}...'" )
            doc_results_raw = await self.document_vector_service.search(
                tenant_id=tenant_id,
                query=query,
                doc_ids=[PyUUID(str(doc_id)) for doc_id in uploaded_doc_ids],
                limit=DOCUMENT_SEARCH_LIMIT_PER_DOC * len(uploaded_doc_ids),
                use_hybrid=False
            )

            MIN_DOCUMENT_CONTEXT_CERTAINTY = 0.7
            if doc_results_raw:
                filtered_doc_results = self._filter_chunks_by_certainty(
                    doc_results_raw,
                    MIN_DOCUMENT_CONTEXT_CERTAINTY,
                    "uploaded document context"
                )
                state['retrieved_document_context_chunks'] = filtered_doc_results
                if filtered_doc_results:
                    for chunk in filtered_doc_results:
                        properties = chunk.get('properties', {})
                        source_label = f"[{source_counter}]"
                        
                        text_content = properties.get('contentChunk')
                        if not text_content:
                            text_content = chunk.get('content', str(chunk))
                            if text_content == str(chunk):
                                logger.warning(f"Missing 'contentChunk' in properties for document chunk: {chunk.get('uuid', 'N/A')}. Using full chunk string as fallback.")
                        
                        chunk_doc_id_str = chunk.get('documentId')
                        chunk_doc_id = None
                        if chunk_doc_id_str:
                            try:
                                chunk_doc_id = PyUUID(str(chunk_doc_id_str))
                            except ValueError:
                                logger.warning(f"Could not parse documentId '{chunk_doc_id_str}' from document chunk as UUID. Chunk UUID: {chunk.get('uuid', 'N/A')}")
                        
                        title = doc_titles_map.get(chunk_doc_id, f"Document {source_label}")
                        
                        citation = CitationV2(
                            source_label=source_label,
                            document_id=chunk_doc_id,
                            title=title,
                            scope_type=ChatContextType.DOCUMENT.value,
                            text_content_chunk=text_content,
                            source_url=doc_titles_map.get(chunk_doc_id)
                        )
                        citations_list.append(citation.model_dump())
                        formatted_doc_chunks_for_prompt.append(f"{text_content} {source_label}")
                        source_counter += 1
                if filtered_doc_results:
                    logger.info(f"Successfully retrieved and processed {len(filtered_doc_results)} context chunks for DocumentIDs: {uploaded_doc_ids}.")
                elif doc_results_raw:
                    logger.info(f"No document context chunks met certainty threshold. Original count: {len(doc_results_raw)} for DocumentIDs: {uploaded_doc_ids}.")
            else:
                logger.info(f"No context found for DocumentIDs: {uploaded_doc_ids}.")
        except Exception as e:
            logger.error(f"Error retrieving document context for DocumentIDs: {uploaded_doc_ids}: {e}", exc_info=True)
        
        document_context_str = "\n---\n".join(formatted_doc_chunks_for_prompt) if formatted_doc_chunks_for_prompt else "No relevant context found in the uploaded documents."
        
        formatted_prompt = prompt_template.format(
            document_context=document_context_str,
            query=state.get("query") or ""
        )
        state["uploaded_document_prompt"] = formatted_prompt
        state["uploaded_document_prompt_template"] = prompt_template
        state['citations'] = citations_list
        
        return state

    async def _retrieve_workspace_context_node(self, state: GraphState) -> GraphState:
        logger.info(f"Attempting to retrieve workspace context. ContextType: {state.get('context_type')}, WorkspaceID: {state.get('workspace_id')}")
        state['workspace_context_chunks'] = None
        
        citations_list = state.get('citations') or []
        source_counter = len(citations_list) + 1
        formatted_workspace_chunks_for_prompt = []

        prompt_template = (
            "Instructions:\n"
            "Answer the user's question using the workspace-wide content. "
            "Cite your sources using the provided labels (e.g., [1], [2]). "
            "If the answer is not in the context, say so.\n\n"
            "Workspace Context:\n{workspace_context}\n\n"
            "User Query:\n{query}"
        )
        
        context_type = state.get('context_type')
        workspace_id = state.get('workspace_id')
        query = state.get('query')
        tenant_id = state.get('tenant_id')
        WORKSPACE_CONTEXT_LIMIT = 4

        if not query or not tenant_id or not workspace_id:
            logger.info("Skipping workspace context retrieval: missing query, tenant_id, or workspace_id.")
            state["workspace_prompt"] = prompt_template.format(workspace_context="Context not available due to missing parameters.", query=query or "")
            state["workspace_prompt_template"] = prompt_template
            state['citations'] = citations_list
            return state

        if context_type is ChatContextType.WORKSPACE:
            try:
                logger.info(f"Searching workspace context for WorkspaceID: {workspace_id}, TenantID: {tenant_id}, Query: '{query[:50]}...'" )
                results_raw = await self.page_vector_service.search(
                    tenant_id=tenant_id,
                    query=query,
                    workspace_id=workspace_id,
                    doc_id=None,
                    limit=WORKSPACE_CONTEXT_LIMIT,
                    use_hybrid=False
                )
                MIN_WORKSPACE_CONTEXT_CERTAINTY = 0.7
                filtered_results = self._filter_chunks_by_certainty(
                    results_raw, 
                    MIN_WORKSPACE_CONTEXT_CERTAINTY, 
                    "workspace context"
                )
                state['workspace_context_chunks'] = filtered_results
                if filtered_results:
                    for chunk in filtered_results:
                        properties = chunk.get('properties', {})
                        source_label = f"[{source_counter}]"
                        
                        text_content = properties.get('contentChunk')
                        if not text_content:
                            logger.warning(f"Missing 'contentChunk' in properties for chunk: {chunk.get('uuid')}")
                            text_content = str(chunk)

                        chunk_doc_id_str = properties.get('documentId')
                        chunk_doc_id = None
                        if chunk_doc_id_str:
                            try:
                                chunk_doc_id = PyUUID(str(chunk_doc_id_str))
                            except ValueError:
                                logger.warning(f"Could not parse documentId '{chunk_doc_id_str}' from chunk properties as UUID. Chunk UUID: {chunk.get('uuid')}")
                        else:
                            fallback_doc_id_str = chunk.get('doc_id') or chunk.get('page_id')
                            if fallback_doc_id_str:
                                try:
                                    chunk_doc_id = PyUUID(str(fallback_doc_id_str))
                                except ValueError:
                                    logger.warning(f"Could not parse fallback doc_id/page_id '{fallback_doc_id_str}' from workspace chunk as UUID. Chunk UUID: {chunk.get('uuid')}")

                        title = properties.get('title', f"Workspace Content {source_label}")
                        source_url = chunk.get('source_url') 

                        citation = CitationV2(
                            source_label=source_label,
                            document_id=chunk_doc_id, 
                            title=title,
                            scope_type=ChatContextType.WORKSPACE.value,
                            text_content_chunk=text_content,
                            source_url=source_url
                        )
                        citations_list.append(citation.model_dump())
                        formatted_workspace_chunks_for_prompt.append(f"{text_content} {source_label}")
                        source_counter += 1
                    logger.info(f"Successfully retrieved and processed {len(results_raw)} workspace context chunks for WorkspaceID: {workspace_id}.")
                else:
                    logger.info(f"No workspace context found for WorkspaceID: {workspace_id}.")
            except Exception as e:
                logger.error(f"Error retrieving workspace context for WorkspaceID: {workspace_id}: {e}", exc_info=True)
        
        workspace_context_str = "\n---\n".join(formatted_workspace_chunks_for_prompt) if formatted_workspace_chunks_for_prompt else "No relevant context found in the workspace."
        
        formatted_prompt = prompt_template.format(
            workspace_context=workspace_context_str,
            query=state.get("query") or ""
        )
        state["workspace_prompt"] = formatted_prompt
        state["workspace_prompt_template"] = prompt_template
        state['citations'] = citations_list

        return state

    async def _retrieve_default_context_node(self, state: GraphState) -> GraphState:
        logger.info("Retrieving workspace context for DEFAULT context type (workspace-aware RAG mode with citations).")
        
        citations_list = state.get('citations') or []
        source_counter = len(citations_list) + 1
        formatted_workspace_chunks_for_prompt = []
        raw_workspace_chunks = []
        prompt_template = (
            "Instructions:\n"
            "Answer the user's question using the workspace context. "
            "Cite your sources using the [number] format provided with each context chunk. "
            "If you do not know the answer, say so.\n\n"
            "Workspace Context:\n{workspace_context}\n\n"
            "User Query:\n{query}"
        )

        workspace_id = state.get('workspace_id')
        query = state.get('query')
        tenant_id = state.get('tenant_id')
        WORKSPACE_CONTEXT_LIMIT = 4

        if not query or not tenant_id or not workspace_id:
            logger.info("Skipping workspace context retrieval for DEFAULT: missing query, tenant_id, or workspace_id.")
            empty_context_prompt = prompt_template.format(workspace_context="No context available.", query=query or "")
            state["default_context_prompt"] = empty_context_prompt
            state["default_context_prompt_template"] = prompt_template
            state['citations'] = citations_list
            return state

        try:
            logger.info(f"Searching workspace context for DEFAULT. WorkspaceID: {workspace_id}, TenantID: {tenant_id}, Query: '{query[:50]}...'" )
            results = await self.page_vector_service.search(
                tenant_id=tenant_id,
                query=query,
                workspace_id=workspace_id,
                doc_id=None,
                limit=WORKSPACE_CONTEXT_LIMIT,
                use_hybrid=False
            )
            MIN_DEFAULT_CONTEXT_CERTAINTY = 0.7
            if results:
                filtered_results = self._filter_chunks_by_certainty(
                    results, 
                    MIN_DEFAULT_CONTEXT_CERTAINTY, 
                    "default context"
                )
                raw_workspace_chunks = filtered_results
                if filtered_results:
                    for chunk in filtered_results:
                        properties = chunk.get('properties', {})
                        source_label = f"[{source_counter}]"
                        
                        text_content = properties.get('contentChunk')
                        if not text_content:
                            logger.warning(f"Missing 'contentChunk' in properties for default context chunk: {chunk.get('uuid')}")
                            text_content = str(chunk)

                        chunk_doc_id_str = properties.get('documentId')
                        chunk_doc_id = None
                        if chunk_doc_id_str:
                            try:
                                chunk_doc_id = PyUUID(str(chunk_doc_id_str))
                            except ValueError:
                                logger.warning(f"Could not parse documentId '{chunk_doc_id_str}' from default context chunk properties as UUID. Chunk UUID: {chunk.get('uuid')}")
                        if not chunk_doc_id:
                            fallback_doc_id_str = chunk.get('doc_id') or chunk.get('page_id')
                            if fallback_doc_id_str:
                                try:
                                    chunk_doc_id = PyUUID(str(fallback_doc_id_str))
                                except ValueError:
                                    logger.warning(f"Could not parse fallback doc_id/page_id '{fallback_doc_id_str}' from default context chunk as UUID. Chunk UUID: {chunk.get('uuid')}")

                        title_for_citation = properties.get('title', chunk.get('title', f"Workspace Context Chunk {source_label}"))
                        source_url_for_citation = chunk.get('source_url')

                        formatted_workspace_chunks_for_prompt.append(f"{text_content} {source_label}")
                        
                        citation = CitationV2(
                            source_label=source_label,
                            document_id=chunk_doc_id,
                            title=title_for_citation,
                            scope_type=ChatContextType.WORKSPACE.value,
                            text_content_chunk=text_content,
                            source_url=source_url_for_citation
                        )
                        citations_list.append(citation.model_dump())
                        source_counter += 1
                logger.info(f"Successfully retrieved and processed {len(results)} workspace context chunks for DEFAULT context type.")
            else:
                logger.info(f"No workspace context found for DEFAULT context type.")
        except Exception as e:
            logger.error(f"Error retrieving or processing workspace context for DEFAULT context type: {e}", exc_info=True)
        
        state['workspace_context_chunks'] = raw_workspace_chunks
        state['citations'] = citations_list

        workspace_context_str = "\n\n".join(formatted_workspace_chunks_for_prompt) if formatted_workspace_chunks_for_prompt else "No relevant workspace context found."
        
        final_formatted_prompt = prompt_template.format(
            workspace_context=workspace_context_str,
            query=query or ""
        )
        state["default_context_prompt"] = final_formatted_prompt
        state["default_context_prompt_template"] = prompt_template
        
        return state


    async def _retrieve_template_context_node(self, state: GraphState) -> GraphState:
        logger.info("Attempting to retrieve context from the original template of the page.")
        
        page_id_str = state.get("page_id")
        query = state.get("query")
        workspace_id = state.get('workspace_id')
        tenant_id = state.get('tenant_id')
        WORKSPACE_CONTEXT_LIMIT = 4
        
        citations_list = state.get('citations') or []
        source_counter = len(citations_list) + 1
        formatted_template_chunks_for_prompt = []
        formatted_workspace_chunks_for_prompt = []
        template_context_str = "No specific template context found for this page or an error occurred."

        page_id = PyUUID(str(page_id_str))
        session: AsyncSession = state.get("db_session")

        try:
            result = await session.execute(
                select(Document).options(selectinload(Document.template)).where(Document.document_id == page_id) #type: ignore
            )
            document = result.scalar_one_or_none()

            if document and document.template_id and document.template:
                template_obj = document.template
                
                template_title = getattr(template_obj, 'title', f"Template ID: {template_obj.id}")
                prompt_identification = template_obj.meta_data.get('prompt_identification', None)

                logger.info(f"Found template '{template_title}' (ID: {template_obj.id}) for page ID: {page_id}.")

                # LLM call to identify specific Langfuse prompt name
                identified_prompt_name_for_state = None
                if prompt_identification and isinstance(prompt_identification, dict) and query:
                    llm_client_instance = state.get("llm_client")
                    if llm_client_instance:
                        system_message_content = (
                            "You are an expert assistant that categorizes a user's query based on a provided list of sections and their corresponding prompt identifiers.\n"
                            "Your task is to determine which section the user's query is most relevant to.\n"
                            "You MUST strictly return ONLY the prompt identifier string associated with the matched section.\n"
                            "If the query does not clearly match any of the provided sections, you MUST strictly return the string \"None\".\n"
                            "Do not add any explanations or conversational text. Only return the prompt identifier or \"None\"."
                        )
                        user_message_content = (
                            f"User Query: \"{query}\"\n\n"
                            f"Available Sections and Prompt Identifiers:\n{json.dumps(prompt_identification, indent=2)}\n\n"
                            "Based on the User Query and the Available Sections, which prompt identifier should be used?"
                        )
                        try:
                            logger.info(f"Attempting to identify specific prompt for query: '{query[:50]}...' using template sections.")
                            logger.info(f"System Prompt: {system_message_content}")
                            logger.info(f"User Prompt: {user_message_content}")
                            llm_response = await llm_client_instance.generate(prompt=user_message_content, system_prompt=system_message_content, temperature=0.1)
                            
                            # Validate response
                            if llm_response and llm_response.strip() in prompt_identification.values():
                                identified_prompt_name_for_state = llm_response.strip()
                                logger.info(f"LLM identified specific prompt: {identified_prompt_name_for_state}")
                            elif llm_response and llm_response.strip().lower() == "none":
                                logger.info(f"LLM indicated no specific prompt matches the query.{llm_response}")
                                identified_prompt_name_for_state = None
                            else:
                                logger.warning(f"LLM returned an unexpected value for prompt identification: '{llm_response}'. Treating as no match.")
                                identified_prompt_name_for_state = None
                        except LLMGenerationError as e:
                            logger.error(f"LLM generation error during prompt identification: {e}", exc_info=True)
                        except Exception as e:
                            logger.error(f"Unexpected error during prompt identification LLM call: {e}", exc_info=True)
                    else:
                        logger.warning("LLM client not found in state, cannot identify specific prompt name.")
                else:
                    if not query:
                        logger.info("No user query provided, skipping specific prompt identification.")
                    if not prompt_identification or not isinstance(prompt_identification, dict):
                        logger.info("No valid 'prompt_identification' metadata in template, skipping specific prompt identification.")
                
                state['identified_langfuse_prompt_name'] = identified_prompt_name_for_state
                identified_prompt_name = state.get('identified_langfuse_prompt_name')

                if identified_prompt_name:
                    logger.info(f"Attempting to use identified Langfuse prompt: {identified_prompt_name}")
                    try:
                        langfuse_client = state.get("langfuse_client")
                        if not langfuse_client:
                            raise ValueError("Langfuse client missing")

                        # 1. Fetch prompt from Langfuse
                        langfuse_system_prompt = ""
                        try:
                            langfuse_prompt_object = langfuse_client.get_prompt(name=identified_prompt_name)
                            if hasattr(langfuse_prompt_object, 'prompt') and isinstance(langfuse_prompt_object.prompt, list):
                                for message_part in langfuse_prompt_object.prompt:
                                    if isinstance(message_part, dict) and message_part.get("role") == 'system' and isinstance(message_part.get('content'), str):
                                        langfuse_system_prompt = message_part.get('content')
                                        state['template_scope_langfuse_system_prompt'] = langfuse_system_prompt
                                        break
                                if not langfuse_system_prompt:
                                    logger.warning(f"Langfuse prompt '{identified_prompt_name}' has a 'prompt' list but no system message string found directly.")
                            else:
                                logger.warning(f"Langfuse prompt object for '{identified_prompt_name}' does not have a recognized structure for instructions.")

                            if not langfuse_system_prompt:
                                raise ValueError(f"System instructions not found or empty in Langfuse prompt '{identified_prompt_name}'.")
                            logger.info(f"Successfully fetched Langfuse prompt '{identified_prompt_name}' with instructions.")

                        except Exception as e:
                            logger.error(f"Failed to fetch or parse Langfuse prompt '{identified_prompt_name}': {e}", exc_info=True)
                            raise # Re-raise to be caught by the outer try-except for this flow

                        # 2. Get workspace RAG context
                        raw_workspace_chunks = []
                        try:
                            logger.info(f"Searching workspace context for DEFAULT. WorkspaceID: {workspace_id}, TenantID: {tenant_id}, Query: '{query[:50]}...'" )
                            results = await self.page_vector_service.search(
                                tenant_id=tenant_id,
                                query=query,
                                workspace_id=workspace_id,
                                doc_id=None,
                                limit=WORKSPACE_CONTEXT_LIMIT,
                                use_hybrid=False
                            )
                            MIN_DEFAULT_CONTEXT_CERTAINTY = 0.7
                            if results:
                                filtered_results = self._filter_chunks_by_certainty(
                                    results, 
                                    MIN_DEFAULT_CONTEXT_CERTAINTY, 
                                    "default context"
                                    )
                                raw_workspace_chunks = filtered_results
                                if filtered_results:
                                    for chunk in filtered_results:
                                        properties = chunk.get('properties', {})
                                        source_label = f"[{source_counter}]"
                                            
                                        text_content = properties.get('contentChunk')
                                        if not text_content:
                                            logger.warning(f"Missing 'contentChunk' in properties for default context chunk: {chunk.get('uuid')}")
                                            text_content = str(chunk) # Fallback

                                        chunk_doc_id_str = properties.get('documentId')
                                        chunk_doc_id = None
                                        if chunk_doc_id_str:
                                            try:
                                                chunk_doc_id = PyUUID(str(chunk_doc_id_str))
                                            except ValueError:
                                                    logger.warning(f"Could not parse documentId '{chunk_doc_id_str}' from default context chunk properties as UUID. Chunk UUID: {chunk.get('uuid')}")
                                        if not chunk_doc_id:
                                            fallback_doc_id_str = chunk.get('doc_id') or chunk.get('page_id')
                                            if fallback_doc_id_str:
                                                try:
                                                    chunk_doc_id = PyUUID(str(fallback_doc_id_str))
                                                except ValueError:
                                                    logger.warning(f"Could not parse fallback doc_id/page_id '{fallback_doc_id_str}' from default context chunk as UUID. Chunk UUID: {chunk.get('uuid')}")

                                        title_for_citation = properties.get('title', chunk.get('title', f"Workspace Context Chunk {source_label}"))
                                        source_url_for_citation = chunk.get('source_url')

                                        formatted_workspace_chunks_for_prompt.append(f"{text_content} {source_label}")
                                        
                                        citation = CitationV2(
                                            source_label=source_label,
                                            document_id=chunk_doc_id,
                                            title=title_for_citation,
                                            scope_type=ChatContextType.WORKSPACE.value,
                                            text_content_chunk=text_content,
                                            source_url=source_url_for_citation
                                        )
                                        citations_list.append(citation.model_dump())
                                        source_counter += 1
                                logger.info(f"Successfully retrieved and processed {len(results)} workspace context chunks for TEMPLATE context type.")
                            else:
                                logger.info(f"No workspace context found for TEMPLATE context type.")
                        except Exception as e:
                            logger.error(f"Error retrieving or processing workspace context for TEMPLATE context type: {e}", exc_info=True)

                        state['workspace_context_chunks'] = raw_workspace_chunks
                        state['citations'] = citations_list

                        workspace_context_str = "\n\n".join(formatted_workspace_chunks_for_prompt) if formatted_workspace_chunks_for_prompt else "No relevant workspace context found."
                        
                        langfuse_flow_specific_prompt_template_str = (
                            "Use the following relevant workspace context to help answer the user's query.\n"
                            "Relevant Workspace Context:\n{workspace_context}\n\n"
                            "User Query:\n{query}"
                        )
                        final_formatted_prompt = langfuse_flow_specific_prompt_template_str.format(
                            workspace_context=workspace_context_str,
                            query=query or ""
                        )
                        state["template_context_prompt"] = final_formatted_prompt
                        state["template_context_prompt_template"] = langfuse_flow_specific_prompt_template_str
                            
                    except Exception as e: # This except should catch errors specific to the Langfuse flow
                        logger.error(f"Error in template-specific Langfuse prompt flow for '{identified_prompt_name}': {e}", exc_info=True)
                        state['identified_langfuse_prompt_name'] = None
                        state['template_scope_langfuse_system_prompt'] = None # Clear if Langfuse system prompt was set
                else:
                    logger.info("prompt identification failed for document ID: {page_id}.")
            elif document and document.template_id and not document.template:
                logger.warning(f"Document {page_id} has template_id {document.template_id}, but template object could not be loaded. Check relationship or data integrity.")
            elif document and not document.template_id:
                logger.info(f"Page ID: {page_id} was not created from a template. Delegating to page context retrieval.")
                state['context_type'] = ChatContextType.PAGE
                return await self._retrieve_page_context_node(state)
            else:
                logger.warning(f"Page (Document) with ID: {page_id} not found.")
                template_context_str = "The current page could not be found."

        except Exception as e:
            logger.error(f"Error retrieving template context for page ID {page_id}: {e}", exc_info=True)

        # Fallback: Ensure 'template_context_prompt' is a valid string before returning from the node.
        # This handles cases where neither Langfuse-specific flow nor default template content processing set the prompt.
        if state.get("template_context_prompt") is None:
            current_query = state.get("query") # Get query from state
            logger.warning(
                f"'template_context_prompt' is None for page_id '{page_id}' before returning from _retrieve_template_context_node. "
                f"Using query ('{current_query[:50] if current_query else ''}...') or empty string as fallback."
            )
            state["template_context_prompt"] = current_query or ""
            if not state.get("template_context_prompt_template"):
                state["template_context_prompt_template"] = "User Query:\n{query}"

        logger.info(f"Template context node completed. Citations: {len(citations_list)}. Prompt set.")
        return state


    async def _get_token_count_for_messages(self, messages: List[BaseMessage], model_name: str) -> int:
        """Helper to count tokens for a list of messages using tiktoken."""
        try:
            encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            logger.warning(f"Warning: model {model_name} not found for tiktoken. Using cl100k_base encoding.")
            encoding = tiktoken.get_encoding("cl100k_base")
        
        num_tokens = 0
        for message in messages:
            num_tokens += 4 
            if isinstance(message.content, str):
                num_tokens += len(encoding.encode(message.content))
            elif isinstance(message.content, list):
                for item in message.content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        num_tokens += len(encoding.encode(item.get("text", "")))
        num_tokens += 2
        return num_tokens

    async def _save_conversation_turn_node(self, state: GraphState) -> GraphState:
        logger.debug(f"Saving conversation turn for conversation_id: {state['chat_conversation_id']}")
        db: Optional[AsyncSession] = state.get("db_session")
        if not db:
            logger.error("No DB session found in state for _save_conversation_turn_node")
            state["error"] = state.get("error") or "Internal error: DB session missing."
            return state

        try:
            retrieved_citations_from_state = state.get('citations')
            logger.info(f"_save_conversation_turn_node: Citations received in state: {retrieved_citations_from_state}")

            # 1. Save User Message
            user_message = ChatMessage(
                conversation_id=state["chat_conversation_id"],
                sender_type=SenderType.USER,
                sender_user_id=state.get("user_id"),
                message_content=state["query"]
            )
            db.add(user_message)
            logger.info(f"User message prepared for conversation_id: {state['chat_conversation_id']}")

            # 2. Save AI Message (which might be an error message if LLM failed)
            if not state.get("final_answer"):
                logger.error(f"Critical: final_answer is missing in _save_conversation_turn_node for {state['chat_conversation_id']}. Setting placeholder.")
                ai_content = "An unexpected error occurred, and no response was generated."
                state["error"] = state.get("error") or "AI response was unexpectedly missing."
            else:
                ai_content = state["final_answer"]

            ai_meta_data = {}
            citations_list_raw = state.get('citations')
            if citations_list_raw:
                processed_citations = []
                for citation_item in citations_list_raw:
                    processed_item = citation_item.copy()  # Create a copy
                    if 'document_id' in processed_item and isinstance(processed_item.get('document_id'), PyUUID):
                        processed_item['document_id'] = str(processed_item['document_id'])
                    if 'source_url' in processed_item and isinstance(processed_item.get('source_url'), PyUUID):
                        # Though source_url was None in logs, handle if it could be UUID
                        processed_item['source_url'] = str(processed_item['source_url'])
                    processed_citations.append(processed_item)
                ai_meta_data['citations'] = processed_citations
            logger.info(f"_save_conversation_turn_node: Prepared ai_meta_data for AI message: {ai_meta_data}")

            ai_message = ChatMessage(
                conversation_id=state["chat_conversation_id"],
                sender_type=SenderType.AI,
                message_content=ai_content,
                meta_data=ai_meta_data if ai_meta_data else None # Pass None if empty to use DB default {}
            )
            db.add(ai_message)
            logger.info(f"AI message prepared for conversation_id: {state['chat_conversation_id']}")
            
            # 3. Update conversation's updated_at timestamp and commit
            stmt = (
                sqlalchemy_update(ChatConversation)
                .where(ChatConversation.conversation_id == state["chat_conversation_id"]) # type: ignore
                .values(updated_at=func.now())
            )
            await db.execute(stmt)
            await db.commit()
            logger.info(f"User and AI messages saved for conversation_id: {state['chat_conversation_id']}")
        except Exception as e:
            logger.error(f"Error saving conversation turn for conversation_id {state['chat_conversation_id']}: {e}")
            state["error"] = state.get("error") or f"Failed to save conversation turn: {str(e)}"
            await db.rollback() # Rollback on error
        return state

    async def _generate_llm_response_node(self, state: GraphState) -> GraphState:
        """Node for the agent to process messages and generate a response (no tools)."""

        context_type = state.get("context_type")
        prompt = ""
        if context_type is ChatContextType.PAGE:
            prompt = state.get("page_prompt")
        elif context_type is ChatContextType.DOCUMENT:
            prompt = state.get("uploaded_document_prompt")
        elif context_type is ChatContextType.DEFAULT_CHAT:
            prompt = state.get("default_context_prompt")
        elif context_type is ChatContextType.WORKSPACE:
            prompt = state.get("workspace_prompt")
        elif context_type is ChatContextType.TEMPLATE:
            prompt = state.get("template_context_prompt")

        
        if context_type is ChatContextType.TEMPLATE:
            # Use the specific Langfuse system prompt if available and identified
            langfuse_system_prompt_content = state.get('template_scope_langfuse_system_prompt')
            if langfuse_system_prompt_content:
                messages = [SystemMessage(content=langfuse_system_prompt_content), HumanMessage(content=prompt)]
            else:
                # Fallback to default system prompt if no specific Langfuse prompt was used
                logger.info("No specific Langfuse system prompt for TEMPLATE context, using default system prompt.")
                messages = [
                    SystemMessage(content="You are PlumLoom, an intelligent, helpful, and friendly conversational AI assistant. You answer user questions, provide explanations, and help users work with their workspace, documents, and pages. Always be concise, clear, and context-aware. If you do not know the answer, say so honestly."),
                    HumanMessage(content=prompt)
                ]
        else:
            db_session = state.get("db_session")
            chat_conversation_id = state.get("chat_conversation_id")
            old_messages = []
            if db_session is not None and chat_conversation_id is not None:
                result = await db_session.execute(
                    ChatMessage.__table__.select()
                    .where(ChatMessage.conversation_id == chat_conversation_id)
                    .order_by(ChatMessage.timestamp.asc())
                )
                db_messages = result.fetchall()
                for db_msg in db_messages:
                    sender = db_msg.sender_type if hasattr(db_msg, 'sender_type') else db_msg["sender_type"]
                    content = db_msg.message_content if hasattr(db_msg, 'message_content') else db_msg["message_content"]
                    if sender == SenderType.AI:
                        old_messages.append(AIMessage(content=content))
                    else:
                        old_messages.append(HumanMessage(content=content))
            messages = [SystemMessage(content="You are PlumLoom, an intelligent, helpful, and friendly conversational AI assistant. You answer user questions, provide explanations, and help users work with their workspace, documents, and pages. Always be concise, clear, and context-aware. If you do not know the answer, say so honestly.")]
            messages.extend(old_messages)
            messages.append(HumanMessage(content=prompt))
        try:
            # Convert messages to OpenAI format
            openai_messages = []
            for msg in messages:
                if isinstance(msg, SystemMessage):
                    openai_messages.append({"role": "system", "content": msg.content})
                elif isinstance(msg, AIMessage):
                    openai_messages.append({"role": "assistant", "content": msg.content})
                elif isinstance(msg, HumanMessage):
                    openai_messages.append({"role": "user", "content": msg.content})
        
            # Generate response using the LLM client
            final_answer = await self.llm.generate(
                prompt="",
                system_prompt=None,
                temperature=0.5,
                messages=openai_messages
            )
            current_error_message = None
        except LLMGenerationError as e:
            final_answer = "I apologize, but I'm currently unable to generate a response due to a problem with the AI service."
            current_error_message = f"LLM service error: {e}"
            logger.error(f"LLM generation failed: {e}", exc_info=True)
        except Exception as e:
            final_answer = "I apologize, but I'm currently unable to generate a response due to an unexpected error."
            current_error_message = f"Unexpected error in LLM generation: {e}"
            logger.error(f"Unexpected error in LLM generation: {e}", exc_info=True)
        state["llm_response"] = final_answer
        state["llm_messages"] = [m.content for m in messages]
        state["error"] = current_error_message
        state["final_answer"] = final_answer
        return state

    def _build_graph(self) -> CompiledStateGraph:
        """
        Builds the LangGraph for the V2 agentic chat.
        Branches:
        - If context_type is WORKSPACE: retrieve_workspace_context -> generate_llm_response
        - If context_type is PAGE or TEMPLATE: retrieve_page_context -> retrieve_document_context -> generate_llm_response
        - If context_type is DEFAULT or unknown: retrieve_default_context -> generate_llm_response
        Then: generate_llm_response -> save_conversation_turn -> END
        """
        workflow = StateGraph(GraphState)

        # Add all context retrieval and core nodes
        workflow.add_node("retrieve_page_context", self._retrieve_page_context_node) # type: ignore
        workflow.add_node("retrieve_document_context", self._retrieve_document_context_node) # type: ignore
        workflow.add_node("retrieve_workspace_context", self._retrieve_workspace_context_node) # type: ignore
        workflow.add_node("retrieve_default_context", self._retrieve_default_context_node) # type: ignore
        workflow.add_node("retrieve_template_context", self._retrieve_template_context_node) # type: ignore
        workflow.add_node("generate_llm_response", self._generate_llm_response_node)
        workflow.add_node("save_conversation_turn", self._save_conversation_turn_node) # type: ignore

        # Router to pick the correct entry point based on context_type
        def context_router(state: GraphState) -> Dict[str, Any]:
            context_type = state.get("context_type")

            next_node = "retrieve_default_context"
            
            if context_type is ChatContextType.WORKSPACE:
                next_node = "retrieve_workspace_context"
            elif context_type is ChatContextType.PAGE:
                next_node = "retrieve_page_context"
            elif context_type is ChatContextType.DOCUMENT:
                next_node = "retrieve_document_context"
            elif context_type is ChatContextType.TEMPLATE:
                next_node = "retrieve_template_context"
            elif context_type is ChatContextType.DEFAULT_CHAT:
                next_node = "retrieve_default_context"
            
            logger.info(f"Context router determined next node: '{next_node}' for context_type: {context_type}")
            return {"next": next_node}

        # Add the router as a node and set it as the entry point
        workflow.add_node("context_router", context_router)
        workflow.set_entry_point("context_router")
        
        conditional_edges_map = {
            "retrieve_workspace_context": "retrieve_workspace_context",
            "retrieve_page_context": "retrieve_page_context",
            "retrieve_document_context": "retrieve_document_context",
            "retrieve_template_context": "retrieve_template_context",
            "retrieve_default_context": "retrieve_default_context",
        }
        workflow.add_conditional_edges(
            "context_router",
            lambda state: state["next"],
            conditional_edges_map
        )
        
        # Context to LLM response edges
        workflow.add_edge("retrieve_workspace_context", "generate_llm_response")
        workflow.add_edge("retrieve_page_context", "generate_llm_response")
        workflow.add_edge("retrieve_document_context", "generate_llm_response")
        workflow.add_edge("retrieve_template_context", "generate_llm_response")
        workflow.add_edge("retrieve_default_context", "generate_llm_response")
        
        # Common flow to end
        workflow.add_edge("generate_llm_response", "save_conversation_turn")
        workflow.add_edge("save_conversation_turn", END)
        
        logger.info("ChatService V2 graph built: context_router -> (retrieve_workspace_context | retrieve_page_context | retrieve_document_context | retrieve_template_context | retrieve_default_context) -> generate_llm_response -> save_conversation_turn -> END.")
        return workflow.compile()


    async def generate_response(
        self,
        request_data: AgenticChatRequestV2,
        user_data: Dict
) -> AgenticChatResponseV2:
        """Generates a response using the agentic AI graph."""
        session_id = str(request_data.chat_conversation_id)
        final_state = {}
        error_message = None
        answer_to_return = None

        try:
            initial_state: GraphState = {
                "query": request_data.query,
                "chat_conversation_id": request_data.chat_conversation_id,
                "intermediate_steps": [],
                "final_answer": None,
                "error": None,
                "user_id": user_data.get("id"),
                "tenant_id": user_data.get("userTenantId"),
                "db_session": self.db,
                "llm_client": self.llm,
                "langfuse_client": self.langfuse,
                "workspace_id": request_data.workspace_id,
                "context_type": request_data.context_type,
                "page_id": request_data.page_id,
                "uploaded_document_ids": request_data.uploaded_document_ids,
                "page_specific_context_chunks": None, 
                "workspace_context_chunks": None, 
                "retrieved_document_context_chunks": None,
                "template_scope_langfuse_system_prompt": None,
                "template_context_prompt": None,
                "template_context_prompt_template": None,
                "llm_response": None,
                "llm_messages": None,
                "prompt_template": None,
                "page_prompt": None,
                "page_prompt_template": None,
                "workspace_prompt": None,
                "workspace_prompt_template": None,
                "default_context_prompt": None,
                "default_context_prompt_template": None,
                "uploaded_document_prompt": None,
                "uploaded_document_prompt_template": None,
                "citations": [],
                "messages":[],
                "identified_langfuse_prompt_name":None
            }
        
            logger.info(f"Invoking agentic graph with initial state for session: {session_id}")
            config: RunnableConfig = {"recursion_limit": 10}
            final_state = await self.agentic_graph.ainvoke(
                initial_state,
                config=config
            )

            if not final_state or not isinstance(final_state, dict):
                logger.error(f"SessionID: {session_id} - Graph execution with ainvoke yielded invalid or empty final state: {final_state}")
                final_state = initial_state
                final_state["error"] = final_state.get("error") or "Internal error: Graph execution (ainvoke) failed to return a valid state."
                final_state["final_answer"] = final_state.get("final_answer") or "An unexpected internal error occurred during processing."

            answer_to_return = final_state.get("final_answer")
            
            error_message = final_state.get("error")
            intermediate_steps_to_return = final_state.get("intermediate_steps", [])
            citations_to_return = final_state.get("citations", [])

            if not answer_to_return and not error_message:
                answer_to_return = "Agent processing complete. No specific answer was generated."
            elif not answer_to_return and error_message:
                # If there's an error, the answer should reflect that, or be None if the error message is the primary info
                answer_to_return = f"Agent encountered an issue: {error_message}" 

            return AgenticChatResponseV2(
                answer=answer_to_return,
                citations=citations_to_return,
                intermediate_steps=intermediate_steps_to_return,
                error=error_message,
                session_id=request_data.chat_conversation_id
            )

        except Exception as e:
            logger.exception(f"Error during agentic graph execution for session {session_id}: {e}")
            # Ensure final_state is a dictionary for safe access, using initial_state as a fallback structure
            current_intermediate_steps = []
            current_citations = []
            if isinstance(final_state, dict):
                current_intermediate_steps = final_state.get("intermediate_steps", [])
                current_citations = final_state.get("citations", [])
            elif isinstance(initial_state, dict): # initial_state is defined at the start of the try block
                current_intermediate_steps = initial_state.get("intermediate_steps", [])
                # Citations might not be in initial_state, so default to empty

            return AgenticChatResponseV2(
                answer="An unexpected server error occurred while processing your request.",
                citations=current_citations,
                intermediate_steps=current_intermediate_steps,
                error=f"An unexpected server error occurred: {str(e)}",
                session_id=request_data.chat_conversation_id
            )


def get_chat_service(
        llm: BaseLLMClient = Depends(get_primary_llm_client),
        langfuse_client: Langfuse = Depends(get_langfuse),
        page_vector_service: PageVectorServiceAsync = Depends(get_page_vector_service_async),
        document_vector_service: DocumentVectorServiceAsync = Depends(get_document_vector_service_async),
        db: AsyncSession = Depends(get_db),
        redis: aioredis.Redis = Depends(get_redis),
) -> ChatService:
    return ChatService(
        llm=llm,
        langfuse_client=langfuse_client,
        page_vector_service=page_vector_service,
        document_vector_service=document_vector_service,
        db=db,
        redis=redis
    )