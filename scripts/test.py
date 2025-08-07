# app/services/chat_service.py
import logging
import uuid
from typing import Dict, Any, Optional, List, Tuple, TypedDict
from uuid import UUID as PyUUID

from fastapi import Depends
from langfuse import Langfuse  # type: ignore
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.future import select
from langgraph.graph import StateGraph, END

from app.core.config import get_settings
from app.core.langfuse_config import get_langfuse
from app.core.llm_clients import BaseLLMClient, get_primary_llm_client, LLMGenerationError
from app.schemas.chat import ChatKnowledgeScope, ContextType, Citation, CitationScopeType
from app.services.weaviate.page_service_async import PageVectorServiceAsync
from app.services.weaviate.document_service_async import DocumentVectorServiceAsync
from app.services.weaviate.exceptions import VectorStoreOperationError, VectorStoreTenantNotFoundError
from app.services.weaviate import get_page_vector_service_async, get_document_vector_service_async
from app.core.database import get_db
from app.models.chat_message import ChatMessage, SenderType
from app.models.chat_conversation import ChatConversation
from app.models.uploaded_document import UploadedDocument

settings = get_settings()
logger = logging.getLogger(__name__)

RAG_RETRIEVAL_LIMIT_DEFAULT = 3
RAG_RETRIEVAL_LIMIT_FOCUSED_DOCS = 5
RAG_RETRIEVAL_LIMIT_WORKSPACE = 5
RAG_RETRIEVAL_LIMIT_PAGE_PRIMARY = 2
RAG_RETRIEVAL_LIMIT_PAGE_AUGMENT = 2

MIN_CERTAINTY_THRESHOLD = 0.70
MAX_DISTANCE_THRESHOLD = 0.65
MIN_HYBRID_SCORE_THRESHOLD = 0.55


# --- LangGraph State Definition ---
class GraphState(TypedDict):
    # Inputs
    user_id: str
    tenant_id: str
    query: str
    chat_conversation_id: str
    selected_uploaded_document_ids: Optional[List[str]]
    knowledge_scope: ChatKnowledgeScope
    knowledge_scope_id: Optional[str]
    workspace_id_for_scope: Optional[str]

    # Langfuse & DB & Services (passed through for nodes to use)
    langfuse_trace_obj: Any  # Langfuse trace object
    db_session: AsyncSession
    llm_client: BaseLLMClient
    page_vector_service: PageVectorServiceAsync
    document_vector_service: DocumentVectorServiceAsync

    # Intermediate & Output values
    trace_id: str  # Langfuse trace ID string
    error_message: Optional[str]
    final_answer: str
    llm_used_provider: Optional[str]

    primary_search_results_filtered: List[Dict[str, Any]]
    augmentation_search_results_filtered: Optional[List[Dict[str, Any]]]
    context_type_used: ContextType

    retrieved_context_str: str
    citations: List[Dict[str, Any]]  # List of Citation.model_dump()

    all_retrieved_doc_ids: List[str]
    retrieved_page_ids_for_augmentation: Optional[List[str]]

    # For saving AI message
    ai_message_metadata: Optional[Dict[str, Any]]


class ChatService:
    def __init__(
            self,
            llm: BaseLLMClient = Depends(get_primary_llm_client),
            langfuse_client: Langfuse = Depends(get_langfuse),
            page_vector_service: PageVectorServiceAsync = Depends(get_page_vector_service_async),
            document_vector_service: DocumentVectorServiceAsync = Depends(get_document_vector_service_async),
            db: AsyncSession = Depends(get_db),
    ):
        self.llm = llm
        self.langfuse = langfuse_client
        self.page_vector_service = page_vector_service
        self.document_vector_service = document_vector_service
        self.db = db
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(GraphState)

        # Define nodes
        workflow.add_node("save_user_message_node", self._save_user_message_node)
        workflow.add_node("retrieve_focused_docs_node", self._retrieve_focused_docs_node)
        workflow.add_node("retrieve_scoped_knowledge_node", self._retrieve_scoped_knowledge_node)
        workflow.add_node("format_context_node", self._format_context_node)
        workflow.add_node("generate_llm_response_node", self._generate_llm_response_node)
        workflow.add_node("save_ai_message_node", self._save_ai_message_node)
        workflow.add_node("prepare_error_response_node", self._prepare_error_response_node)

        # Define edges
        workflow.set_entry_point("save_user_message_node")

        workflow.add_conditional_edges(
            "save_user_message_node",
            self._should_retrieve_focused_or_scoped,
            {
                "focused": "retrieve_focused_docs_node",
                "scoped": "retrieve_scoped_knowledge_node",
                "error": "prepare_error_response_node"  # If initial validation fails
            }
        )

        workflow.add_edge("retrieve_focused_docs_node", "format_context_node")
        workflow.add_edge("retrieve_scoped_knowledge_node", "format_context_node")

        workflow.add_conditional_edges(
            "format_context_node",
            self._check_retrieval_success,  # New conditional router
            {
                "success": "generate_llm_response_node",
                "retrieval_failed_or_empty": "generate_llm_response_node",
                # Still go to LLM, but it will use no_context_prompt
                "critical_error": "prepare_error_response_node"
            }
        )

        workflow.add_conditional_edges(
            "generate_llm_response_node",
            self._check_llm_success,
            {
                "success": "save_ai_message_node",
                "llm_error": "prepare_error_response_node",  # Update state with LLM error, then end
            }
        )

        workflow.add_edge("save_ai_message_node", END)
        workflow.add_edge("prepare_error_response_node", END)  # Errors also lead to end

        return workflow.compile()

    # --- Conditional Routers for LangGraph ---
    async def _should_retrieve_focused_or_scoped(self, state: GraphState) -> str:
        if state.get("error_message"):  # If error occurred during user message save or initial validation
            return "error"
        if state.get("selected_uploaded_document_ids") and state.get("chat_conversation_id"):
            return "focused"
        return "scoped"

    async def _check_retrieval_success(self, state: GraphState) -> str:
        if state.get("error_message") and "Knowledge base access or input issue during retrieval" in state[
            "error_message"]:
            # This is a critical error that should stop further processing and just return the error.
            return "critical_error"  # Route to prepare error response

        # If no primary and no augmentation results, it's not necessarily an error,
        # LLM will handle it with no_context_prompt.
        # This path also handles cases where retrieval was okay but simply found nothing relevant.
        return "success"  # or "retrieval_failed_or_empty" if you want a distinct path, then merge back to LLM

    async def _check_llm_success(self, state: GraphState) -> str:
        if state.get("error_message") and ("LLM service unavailable" in state[
            "error_message"] or "An unexpected error occurred during AI response generation" in state["error_message"]):
            return "llm_error"  # Signal LLM specific error to prepare_error_response
        return "success"

    # --- LangGraph Nodes ---
    async def _save_user_message_node(self, state: GraphState) -> Dict[str, Any]:
        logger.info(f"TraceID: {state['trace_id']} - Node: _save_user_message_node")
        try:
            await self._save_chat_message(
                conversation_id=state["chat_conversation_id"],
                sender_type=SenderType.USER,
                content=state["query"],
                user_id=state["user_id"],
                trace_span=state["langfuse_trace_obj"]
            )
            return {}
        except Exception as e:
            logger.error(f"TraceID: {state['trace_id']} - Error in _save_user_message_node: {e}", exc_info=True)
            return {"error_message": f"Failed to save user message: {e}"}

    async def _retrieve_focused_docs_node(self, state: GraphState) -> Dict[str, Any]:
        logger.info(f"TraceID: {state['trace_id']} - Node: _retrieve_focused_docs_node")
        retrieval_orchestration_span = state["langfuse_trace_obj"].span(
            name="context-retrieval-orchestration",
            input={"strategy": "focused_documents"}
        )
        primary_results: List[Dict[str, Any]] = []
        error_msg: Optional[str] = None
        context_type = ContextType.USER_SELECTED_UPLOADED_DOCUMENTS

        try:
            primary_results = await self._perform_retrieval_for_focused_documents(
                retrieval_orchestration_span, state["tenant_id"], state["query"],
                state["chat_conversation_id"], state["selected_uploaded_document_ids"]
            )
            if not primary_results:
                context_type = ContextType.NO_CONTEXT_USED
                logger.info(
                    f"TraceID: {state['trace_id']} - No relevant chunks from selected documents after filtering.")
            retrieval_orchestration_span.end(output={
                "final_context_type_selected": context_type.value,
                "primary_results_count": len(primary_results)
            })
        except (ValueError, VectorStoreOperationError, VectorStoreTenantNotFoundError) as retrieval_err:
            error_msg = f"Knowledge base access or input issue during retrieval: {retrieval_err}"
            logger.error(f"TraceID: {state['trace_id']} - Focused docs retrieval failed: {retrieval_err}",
                         exc_info=False)  # No full stack for known errors
            retrieval_orchestration_span.end(level="ERROR", status_message=str(retrieval_err),
                                             output={"error": str(retrieval_err)})
            context_type = ContextType.NO_CONTEXT_USED
        except Exception as e:
            error_msg = f"Unexpected error during focused document retrieval: {e}"
            logger.error(f"TraceID: {state['trace_id']} - Unexpected error in focused docs retrieval: {e}",
                         exc_info=True)
            retrieval_orchestration_span.end(level="ERROR", status_message=str(e), output={"error": str(e)})
            context_type = ContextType.NO_CONTEXT_USED

        return {
            "primary_search_results_filtered": primary_results,
            "augmentation_search_results_filtered": None,  # No augmentation for focused docs
            "context_type_used": context_type,
            "error_message": state.get("error_message") or error_msg  # Preserve existing error or set new one
        }

    async def _retrieve_scoped_knowledge_node(self, state: GraphState) -> Dict[str, Any]:
        logger.info(f"TraceID: {state['trace_id']} - Node: _retrieve_scoped_knowledge_node")
        retrieval_orchestration_span = state["langfuse_trace_obj"].span(
            name="context-retrieval-orchestration",
            input={"strategy": f"scoped_knowledge: {state['knowledge_scope'].value}"}
        )
        primary_results: List[Dict[str, Any]] = []
        aug_results: Optional[List[Dict[str, Any]]] = None
        error_msg: Optional[str] = None
        context_type = ContextType.NO_CONTEXT_USED  # Default

        try:
            primary_results, context_type, aug_results = await self._perform_retrieval_for_knowledge_scope(
                retrieval_orchestration_span, state["tenant_id"], state["query"],
                state["knowledge_scope"], state["knowledge_scope_id"],
                state["workspace_id_for_scope"]
            )
            retrieval_orchestration_span.end(output={
                "final_context_type_selected": context_type.value,
                "primary_results_count": len(primary_results),
                "augmentation_results_count": len(aug_results or [])
            })
        except (ValueError, VectorStoreOperationError, VectorStoreTenantNotFoundError) as retrieval_err:
            error_msg = f"Knowledge base access or input issue during retrieval: {retrieval_err}"
            logger.error(f"TraceID: {state['trace_id']} - Scoped knowledge retrieval failed: {retrieval_err}",
                         exc_info=False)
            retrieval_orchestration_span.end(level="ERROR", status_message=str(retrieval_err),
                                             output={"error": str(retrieval_err)})
            context_type = ContextType.NO_CONTEXT_USED  # Ensure it's NO_CONTEXT if retrieval fails critically
        except Exception as e:
            error_msg = f"Unexpected error during scoped knowledge retrieval: {e}"
            logger.error(f"TraceID: {state['trace_id']} - Unexpected error in scoped knowledge retrieval: {e}",
                         exc_info=True)
            retrieval_orchestration_span.end(level="ERROR", status_message=str(e), output={"error": str(e)})
            context_type = ContextType.NO_CONTEXT_USED

        return {
            "primary_search_results_filtered": primary_results,
            "augmentation_search_results_filtered": aug_results,
            "context_type_used": context_type,
            "error_message": state.get("error_message") or error_msg
        }

    async def _format_context_node(self, state: GraphState) -> Dict[str, Any]:
        logger.info(f"TraceID: {state['trace_id']} - Node: _format_context_node")
        # If a retrieval error occurred, this node might still be called.
        # It should gracefully handle empty results or pass through the error.
        if state.get("error_message") and "Knowledge base access or input issue during retrieval" in state[
            "error_message"]:
            # Critical retrieval error already logged, just pass through
            # The _check_retrieval_success router should prevent LLM call in this case.
            logger.warning(
                f"TraceID: {state['trace_id']} - Skipping context formatting due to prior retrieval error: {state['error_message']}")
            return {
                "retrieved_context_str": "Error during context retrieval.",
                "citations": [],
                "all_retrieved_doc_ids": [],
                "retrieved_page_ids_for_augmentation": None,
                "context_type_used": ContextType.NO_CONTEXT_USED  # Ensure this
            }

        primary_results = state.get("primary_search_results_filtered", [])
        aug_results = state.get("augmentation_search_results_filtered")
        context_type = state.get("context_type_used", ContextType.NO_CONTEXT_USED)

        # Recalculate context_type_used if results are empty, ensuring it's NO_CONTEXT_USED
        if not primary_results and not (aug_results and len(aug_results) > 0):
            final_context_type = ContextType.NO_CONTEXT_USED
            logger.info(
                f"TraceID: {state['trace_id']} - No relevant primary or augmentation chunks. Context type set to NO_CONTEXT_USED.")
        else:
            final_context_type = context_type

        all_doc_ids: List[str] = []
        aug_page_ids: Optional[List[str]] = None
        context_str = "No relevant context was found."
        citations_list: List[Dict[str, Any]] = []

        if final_context_type != ContextType.NO_CONTEXT_USED:
            all_effective_chunks = primary_results + (aug_results if aug_results else [])
            if all_effective_chunks:  # Ensure there's something to format
                context_str, citations_list = await self._format_context(  # Added await
                    primary_results,
                    final_context_type,
                    aug_results,
                    state["langfuse_trace_obj"]
                )
                all_doc_ids = list(set([
                    r.get("properties", {}).get("documentId") for r in all_effective_chunks if
                    r.get("properties", {}).get("documentId")
                ]))
                if aug_results:
                    aug_page_ids = list(set([
                        r.get("properties", {}).get("documentId") for r in aug_results if
                        r.get("properties", {}).get("documentId")
                    ]))
            else:  # Should be caught by NO_CONTEXT_USED, but defensive
                final_context_type = ContextType.NO_CONTEXT_USED
                context_str = "No relevant context was found or used."  # More specific
                citations_list = []

        state["langfuse_trace_obj"].event(
            name="final-context-for-llm-check",  # Renamed for clarity
            output={
                "context_type": final_context_type.value,
                "primary_chunks_count": len(primary_results),
                "augmentation_chunks_count": len(aug_results or []),
                "context_str_preview": context_str[:500] + "...",
                "citations_prepared_count": len(citations_list),
            }
        )

        return {
            "retrieved_context_str": context_str,
            "citations": citations_list,
            "all_retrieved_doc_ids": all_doc_ids,
            "retrieved_page_ids_for_augmentation": aug_page_ids,
            "context_type_used": final_context_type  # Update with potentially recalculated type
        }

    async def _generate_llm_response_node(self, state: GraphState) -> Dict[str, Any]:
        logger.info(f"TraceID: {state['trace_id']} - Node: _generate_llm_response_node")

        query = state["query"]
        context_str = state["retrieved_context_str"]
        context_type = state["context_type_used"]
        llm_client = state["llm_client"]

        final_answer = "Sorry, I encountered an issue and couldn't generate a response."
        llm_provider: Optional[str] = None
        current_error_message = state.get("error_message")  # Preserve previous errors if any

        # Determine prompts based on context availability
        if context_type != ContextType.NO_CONTEXT_USED and "Error during context retrieval." not in context_str:
            system_prompt_key = "with_context"
            system_prompt = (
                "You are a helpful AI assistant. Answer the user's question based *strictly* on the provided context below. "
                "The context consists of several numbered sources, labeled like '[1]', '[2]', etc., each potentially indicating its Type (e.g., focused_document, knowledge_base_page). "
                "When you use information from one or more of these sources in your answer, you **MUST** cite the source(s) immediately after the information, using the exact source label (e.g., '[1]', '[2]'). For example: 'Information X comes from the first source [1]. Information Y is detailed in the second source [2].' "
                "If a single sentence synthesizes information from multiple sources, cite all relevant sources at the end of the sentence, like: 'This concept combines ideas from several places [1] [2].' "
                "Cite every piece of information you use from the context. Do not add citations for information not present in the context. "
                "If the context does not contain the information needed to answer the question, clearly state that you cannot answer based on the provided information and do **not** invent an answer or citations. "
                "Do not use any external knowledge. Be concise and accurate."
            )
            user_prompt = f"""Context:
            {context_str}

            Question: {query}

            Answer:"""
        else:
            system_prompt_key = "no_context"
            system_prompt = (
                "You are a helpful AI assistant. No specific context was found from the knowledge base "
                "that meets the relevance criteria for the user's query, or no specific documents were provided. "
                "Try to answer generally if the question allows for it using your internal knowledge. "
                "If the question seems to require specific information you likely don't have access to (e.g., details about specific user documents or pages you weren't given context for), "
                "state clearly that you lack the specific information needed to provide a detailed answer. Do not invent information or documents."
            )
            user_prompt = query
            # Ensure citations are empty if no context was effectively used
            state["citations"] = []

        llm_input_for_trace = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        generation_metadata = {
            "actual_llm_provider": llm_client.provider_name,
            "actual_llm_model": llm_client.get_model_name(),
            "final_context_type_used": context_type.value,
            "retrieved_total_doc_ids_count": len(state.get("all_retrieved_doc_ids", [])),
            "system_prompt_template_key": system_prompt_key,
            "context_string_length": len(context_str) if context_type != ContextType.NO_CONTEXT_USED else 0
        }

        generation_span = state["langfuse_trace_obj"].generation(
            name="rag-llm-generation", model=llm_client.get_model_name(),
            input=llm_input_for_trace, metadata=generation_metadata
        )

        try:
            logger.info(
                f"TraceID: {state['trace_id']} - Attempting LLM generation (Context: {context_type.value}). System Prompt Key: '{system_prompt_key}'")
            final_answer = await llm_client.generate(prompt=user_prompt, system_prompt=system_prompt)
            generation_span.end(output=final_answer)
            llm_provider = llm_client.provider_name
            logger.info(f"TraceID: {state['trace_id']} - Successfully generated LLM response.")
        except LLMGenerationError as e:
            logger.error(f"TraceID: {state['trace_id']} - LLM generation failed: {e}", exc_info=True)
            generation_span.end(level="ERROR", status_message=str(e), output={"error": str(e)})
            current_error_message = f"LLM service unavailable: {e}"
            final_answer = "I apologize, but I'm currently unable to generate a response due to a problem with the AI service."
        except Exception as e:
            logger.error(f"TraceID: {state['trace_id']} - Unexpected error during LLM call: {e}", exc_info=True)
            generation_span.end(level="ERROR", status_message=f"Unexpected generation error: {e}",
                                output={"error": str(e)})
            current_error_message = f"An unexpected error occurred during AI response generation: {e}"
            final_answer = "I apologize, but an unexpected error occurred while trying to generate a response."

        # Prepare metadata for saving AI message (even if LLM failed, to save the error message)
        ai_message_metadata = {
            "langfuse_trace_id": state["trace_id"],
            "llm_provider": llm_provider,  # Could be None if error before provider set
            "llm_model": llm_client.get_model_name(),
            "context_type_used": context_type.value,
            "retrieved_all_doc_ids": state.get("all_retrieved_doc_ids", []),
            "retrieved_page_ids_for_augmentation": state.get("retrieved_page_ids_for_augmentation"),
            "potential_citations_data": state.get("citations", []),  # Use potentially updated citations
            "retrieved_total_doc_count": len(state.get("all_retrieved_doc_ids", []))
        }
        if current_error_message:  # Add error to metadata if one occurred
            ai_message_metadata["error"] = current_error_message

        return {
            "final_answer": final_answer,
            "llm_used_provider": llm_provider,
            "error_message": current_error_message,  # Propagate error
            "ai_message_metadata": ai_message_metadata
        }

    async def _save_ai_message_node(self, state: GraphState) -> Dict[str, Any]:
        logger.info(f"TraceID: {state['trace_id']} - Node: _save_ai_message_node")

        ai_message_meta = state.get("ai_message_metadata")
        if not ai_message_meta:
            logger.error(f"TraceID: {state['trace_id']} - AI message metadata missing in _save_ai_message_node.")
            ai_message_meta = {"error": "Internal: AI metadata missing"}

        logger.debug(f"TraceID: {state['trace_id']} - AI message metadata for save: {ai_message_meta}")
        await self._save_chat_message(
            conversation_id=state["chat_conversation_id"],
            sender_type=SenderType.AI,
            content=state["final_answer"],
            metadata=ai_message_meta,
            trace_span=state["langfuse_trace_obj"]  # CORRECTED: trace -> trace_span
        )
        return {}

    async def _prepare_error_response_node(self, state: GraphState) -> Dict[str, Any]:
        logger.info(f"TraceID: {state['trace_id']} - Node: _prepare_error_response_node")
        error_message = state.get("error_message", "An unspecified error occurred.")

        # Default final answer if an error path is taken
        final_answer = "Sorry, I encountered an issue and couldn't generate a response."
        if "Invalid input provided" in error_message:
            final_answer = f"There was an issue with the input: {error_message.split(': ', 1)[-1]}"
        elif "LLM service unavailable" in error_message:
            final_answer = "I apologize, but I'm currently unable to generate a response due to a problem with the AI service."
        elif "Knowledge base access or input issue" in error_message:
            final_answer = "I'm having trouble accessing the necessary information. Please try again later."
        # (Add more specific error messages if needed)

        # Ensure other relevant fields are set for the final response structure
        return {
            "final_answer": final_answer,  # This is what the user sees
            "error_message": error_message,  # This is for the API response error field
            "llm_used_provider": state.get("llm_used_provider"),  # May or may not be set
            "context_type_used": ContextType.NO_CONTEXT_USED,  # Error implies no valid context used
            "citations": [],
            "all_retrieved_doc_ids": [],
            "retrieved_page_ids_for_augmentation": None
        }

    # --- Helper methods (Existing ones from ChatService, slightly adapted if needed) ---
    # These are called by the nodes. Ensure they use parameters passed or from state.

    def _format_chunk_for_trace(self, chunk_item: Dict[str, Any]) -> Dict[str, Any]:
        # (Existing code - no changes needed)
        props = chunk_item.get("properties", {})
        score_value = None
        score_type = "none"
        if chunk_item.get("distance") is not None:
            score_value = chunk_item.get("distance")
            score_type = "distance"
        elif chunk_item.get("certainty") is not None:
            score_value = chunk_item.get("certainty")
            score_type = "certainty"
        elif chunk_item.get("score") is not None:
            score_value = chunk_item.get("score")
            score_type = "hybrid"
        formatted = {
            "uuid": chunk_item.get("uuid"), "doc_id": props.get("documentId"),
            "title": props.get("title"), "chunk_order": props.get("chunkOrder"),
            "score": round(score_value, 4) if isinstance(score_value, (int, float)) else None,
            "score_type": score_type,
            "content_preview": props.get("contentChunk", "")[:100] + "..." if props.get("contentChunk") else None
        }
        return {k: v for k, v in formatted.items() if v is not None}

    def _filter_results_by_relevance(self, results: List[Dict[str, Any]], trace_span: Optional[Any] = None) -> List[Dict[str, Any]]:
        # (Existing code, ensure trace_span is correctly passed if called by nodes)
        # Renamed 'trace' param to 'trace_span' for clarity
        if not results: return []
        original_count = len(results)
        filtered_results: List[Dict[str, Any]] = []
        filtered_out_details: List[Dict[str, Any]] = []

        for res_item in results:
            passes_threshold = False;
            score_type_used = "none";
            score_value = None
            if res_item.get("distance") is not None:
                score_type_used = "distance";
                score_value = res_item["distance"]
                if score_value <= MAX_DISTANCE_THRESHOLD: passes_threshold = True
            elif res_item.get("certainty") is not None:
                score_type_used = "certainty";
                score_value = res_item["certainty"]
                if score_value >= MIN_CERTAINTY_THRESHOLD: passes_threshold = True
            elif res_item.get("score") is not None:
                score_type_used = "hybrid_score";
                score_value = res_item["score"]
                if score_value >= MIN_HYBRID_SCORE_THRESHOLD: passes_threshold = True
            else:
                passes_threshold = True; score_type_used = "no_score_present"

            if passes_threshold:
                filtered_results.append(res_item)
            else:
                props = res_item.get("properties", {});
                doc_id_prop = props.get("documentId", "Unknown_ID")
                chunk_order = props.get("chunkOrder", -1)
                logger.debug(
                    f"TraceID: {getattr(trace_span, 'id', 'N/A')} - Filtering out chunk for doc_id: {doc_id_prop}, order: {chunk_order} "
                    f"due to relevance {score_type_used}: {score_value}"
                )
                filtered_out_details.append(self._format_chunk_for_trace(res_item))

        filtered_count = len(filtered_results)
        if trace_span and hasattr(trace_span, 'event') and callable(getattr(trace_span, 'event', None)):
            trace_span.event(
                name="relevance-filtering",
                input={"original_count": original_count,
                       "input_chunks_preview": [self._format_chunk_for_trace(item) for item in results[:10]]},
                output={"filtered_count": filtered_count,
                        "thresholds": {"min_certainty": MIN_CERTAINTY_THRESHOLD, "max_distance": MAX_DISTANCE_THRESHOLD,
                                       "min_hybrid_score": MIN_HYBRID_SCORE_THRESHOLD},
                        "filtered_out_chunks": filtered_out_details,
                        "passed_chunks": [self._format_chunk_for_trace(item) for item in filtered_results]},
                level="DEBUG" if original_count == filtered_count else "DEFAULT"
            )
        logger.info(
            f"TraceID: {getattr(trace_span, 'id', 'N/A')} - Relevance filtering: {original_count} -> {filtered_count} chunks.")
        return filtered_results

    async def _perform_retrieval_for_focused_documents(
            self, trace_span: Any, tenant_id: str, query: str,
            chat_conversation_id: str, selected_document_ids: List[str]
    ) -> List[Dict[str, Any]]:
        # (Existing code - ensure trace_span is the Langfuse span object)
        # Renamed 'trace' param to 'trace_span' for clarity
        retrieval_span_name = "weaviate-retrieval-focused-docs"
        raw_limit = RAG_RETRIEVAL_LIMIT_FOCUSED_DOCS * 2
        # ... (rest of the existing method, ensuring it uses trace_span correctly for its sub-spans or events) ...
        # Make sure to use self.document_vector_service from the instance
        pyuuid_selected_document_ids = [PyUUID(doc_id) for doc_id in
                                        selected_document_ids]  # Moved up for early validation potential

        current_sub_span = trace_span.span(  # Changed from trace.span to trace_span.span
            name=retrieval_span_name,
            input={
                "query": query, "tenant_id": tenant_id,
                "intended_limit": RAG_RETRIEVAL_LIMIT_FOCUSED_DOCS,
                "raw_retrieval_limit": raw_limit,
                "chat_conversation_id": chat_conversation_id,
                "selected_document_ids_count": len(selected_document_ids),
                "selected_document_ids": selected_document_ids
            },
            metadata={"collection": self.document_vector_service.COLLECTION_NAME,  # Use self.
                      "filter_by": "selected_document_ids_and_chatSessionId",
                      "retrieval_strategy": "focused_documents"}
        )
        search_results_filtered: List[Dict[str, Any]] = []
        try:
            search_results_raw = await self.document_vector_service.search(  # Use self.
                tenant_id=tenant_id, query=query, limit=raw_limit,
                doc_ids=pyuuid_selected_document_ids, chat_session_id=str(chat_conversation_id),
                use_hybrid=True, alpha=0.5
            )
            search_results_filtered = self._filter_results_by_relevance(search_results_raw,
                                                                        current_sub_span)  # Pass current_sub_span
            current_sub_span.end(output={
                "retrieved_raw_count": len(search_results_raw),
                "retrieved_filtered_count": len(search_results_filtered),
                "raw_chunks": [self._format_chunk_for_trace(item) for item in search_results_raw],
                "filtered_chunks": [self._format_chunk_for_trace(item) for item in search_results_filtered]
            })
            logger.info(
                f"TraceID: {trace_span.id} - Focused Document retrieval found {len(search_results_raw)} raw, {len(search_results_filtered)} filtered for tenant {tenant_id}.")
            return search_results_filtered
        except ValueError as ve:  # This should catch PyUUID conversion error too
            msg = f"Invalid UUID format in selected_document_ids or other input. Error: {ve}"
            logger.error(f"TraceID: {trace_span.id} - {msg}", exc_info=False)
            current_sub_span.end(level="ERROR", status_message=msg, output={"error": msg})
            raise ValueError(msg) from ve  # Re-raise to be caught by the node
        except (VectorStoreOperationError, VectorStoreTenantNotFoundError) as e:
            log_message = f"TraceID: {trace_span.id} - Weaviate search failed for focused documents, tenant {tenant_id}: {e}."
            logger.error(log_message, exc_info=True)  # Full stack for service errors
            current_sub_span.end(level="ERROR", status_message=f"Weaviate search (focused docs) failed: {e}.",
                                 output={"error": str(e)})
            # Let node handle return value, but can raise here too if it's always critical
            raise  # Re-raise to be caught by the node
        except Exception as e:
            logger.error(
                f"TraceID: {trace_span.id} - Unexpected error during focused document retrieval for tenant {tenant_id}: {e}",
                exc_info=True)
            current_sub_span.end(level="ERROR", status_message=f"Unexpected retrieval error: {e}",
                                 output={"error": str(e)})
            raise  # Re-raise

    async def _perform_retrieval_for_knowledge_scope(
            self, trace_span: Any, tenant_id: str, query: str,
            knowledge_scope: ChatKnowledgeScope, knowledge_scope_id: Optional[str] = None,
            workspace_id_for_augmentation: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], ContextType, Optional[List[Dict[str, Any]]]]:
        # (Existing code - ensure trace_span is the Langfuse span object and self.page_vector_service is used)
        # Renamed 'trace' param to 'trace_span' for clarity
        retrieval_span_name = f"weaviate-retrieval-scope-{knowledge_scope.value}"
        # ... (rest of the existing method, ensuring it uses trace_span correctly and services via self.) ...
        primary_results_raw: List[Dict[str, Any]] = []
        primary_results_filtered: List[Dict[str, Any]] = []
        augmentation_results_raw: Optional[List[Dict[str, Any]]] = None
        augmentation_results_filtered: Optional[List[Dict[str, Any]]] = None
        context_type = ContextType.NO_CONTEXT_USED

        actual_workspace_id_str: Optional[str] = workspace_id_for_augmentation
        if knowledge_scope == ChatKnowledgeScope.WORKSPACE and knowledge_scope_id and not workspace_id_for_augmentation:
            # If WORKSPACE scope and knowledge_scope_id is given, it's the workspace_id
            actual_workspace_id_str = knowledge_scope_id
        elif knowledge_scope == ChatKnowledgeScope.DEFAULT and knowledge_scope_id and not workspace_id_for_augmentation:
            # If DEFAULT scope and knowledge_scope_id is given, it implies workspace context for default
            actual_workspace_id_str = knowledge_scope_id

        current_sub_span = trace_span.span(name=retrieval_span_name,
                                           input={  # Changed from trace.span to trace_span.span
                                               "query": query, "tenant_id": tenant_id,
                                               "knowledge_scope": knowledge_scope.value,
                                               "knowledge_scope_id": knowledge_scope_id,
                                               "workspace_id_for_augmentation": workspace_id_for_augmentation,
                                               "effective_workspace_id": actual_workspace_id_str
                                           })
        try:
            # ... (The entire logic of this method)
            # Make sure to use self.page_vector_service and call self._filter_results_by_relevance
            # Example for one branch (PAGE scope):
            if knowledge_scope == ChatKnowledgeScope.PAGE and knowledge_scope_id and workspace_id_for_augmentation:
                context_type = ContextType.SCOPED_PAGE_WITH_WORKSPACE_AUGMENTATION
                page_uuid = PyUUID(knowledge_scope_id)
                workspace_uuid_aug = PyUUID(workspace_id_for_augmentation)
                primary_raw_limit = RAG_RETRIEVAL_LIMIT_PAGE_PRIMARY * 2
                aug_raw_limit_base = RAG_RETRIEVAL_LIMIT_PAGE_AUGMENT

                current_sub_span.update(metadata={"collection": self.page_vector_service.COLLECTION_NAME,
                                                  "filter_by": "page_documentId_and_workspace_augmentation",
                                                  "retrieval_strategy": context_type.value})
                # ... rest of PAGE scope logic ...
                # Primary Retrieval
                primary_ret_sub_span = current_sub_span.span(
                    name="primary-page-retrieval",
                    input={"doc_id": knowledge_scope_id, "raw_limit": primary_raw_limit,
                           "intended_limit": RAG_RETRIEVAL_LIMIT_PAGE_PRIMARY}
                )
                try:
                    primary_results_raw = await self.page_vector_service.search(
                        tenant_id=tenant_id, query=query, limit=primary_raw_limit,
                        doc_id=page_uuid, use_hybrid=True, alpha=0.5
                    )
                    primary_results_filtered = self._filter_results_by_relevance(primary_results_raw,
                                                                                 primary_ret_sub_span)
                    primary_ret_sub_span.end(output={
                        "retrieved_raw_count": len(primary_results_raw),
                        "retrieved_filtered_count": len(primary_results_filtered),
                        "raw_chunks": [self._format_chunk_for_trace(item) for item in primary_results_raw],
                        "filtered_chunks": [self._format_chunk_for_trace(item) for item in primary_results_filtered]
                    })
                except Exception as e:
                    primary_ret_sub_span.end(level="ERROR", status_message=f"Primary page retrieval failed: {e}",
                                             output={"error": str(e)})
                    logger.error(f"TraceID: {trace_span.id} - Primary page retrieval failed: {e}", exc_info=True)

                # Augmentation Workspace Retrieval
                aug_needed = max(0, RAG_RETRIEVAL_LIMIT_PAGE_AUGMENT)
                aug_raw_limit = (aug_needed + len(primary_results_filtered) + 1) * 2

                aug_ret_sub_span = current_sub_span.span(
                    name="augmentation-workspace-retrieval",
                    input={"workspace_id": workspace_id_for_augmentation, "raw_limit": aug_raw_limit,
                           "intended_limit": aug_needed}
                )
                try:
                    all_workspace_pages_raw = await self.page_vector_service.search(
                        tenant_id=tenant_id, query=query, limit=aug_raw_limit,
                        workspace_id=workspace_uuid_aug, use_hybrid=True, alpha=0.5
                    )
                    all_workspace_pages_relevance_filtered = self._filter_results_by_relevance(all_workspace_pages_raw,
                                                                                               aug_ret_sub_span)

                    primary_result_uuids = {res.get("uuid") for res in primary_results_filtered if res.get("uuid")}
                    focused_page_doc_id = str(page_uuid)
                    temp_augmentation_results_filtered = []
                    added_fingerprints = set()

                    for res in all_workspace_pages_relevance_filtered:
                        chunk_uuid = res.get("uuid");
                        props = res.get("properties", {});
                        doc_id = props.get("documentId");
                        chunk_fingerprint = props.get("chunkFingerprint")
                        if chunk_uuid and chunk_uuid in primary_result_uuids: continue
                        if doc_id and doc_id == focused_page_doc_id: continue
                        if chunk_fingerprint and chunk_fingerprint in added_fingerprints: continue
                        temp_augmentation_results_filtered.append(res)
                        if chunk_fingerprint: added_fingerprints.add(chunk_fingerprint)
                        if len(temp_augmentation_results_filtered) >= aug_needed: break
                    augmentation_results_filtered = temp_augmentation_results_filtered
                    aug_ret_sub_span.end(output={
                        "retrieved_raw_count": len(all_workspace_pages_raw),
                        "relevance_filtered_count": len(all_workspace_pages_relevance_filtered),
                        "final_deduplicated_augmentation_count": len(augmentation_results_filtered or []),
                        # ... (add chunk previews if needed)
                    })
                except Exception as e:
                    aug_ret_sub_span.end(level="ERROR", status_message=f"Augmentation retrieval failed: {e}",
                                         output={"error": str(e)})
                    logger.error(f"TraceID: {trace_span.id} - Augmentation retrieval failed: {e}", exc_info=True)
                    augmentation_results_filtered = None

            elif knowledge_scope == ChatKnowledgeScope.WORKSPACE and actual_workspace_id_str:
                context_type = ContextType.SCOPED_WORKSPACE_CONTENT
                raw_limit = RAG_RETRIEVAL_LIMIT_WORKSPACE * 2
                current_sub_span.update(
                    input={"raw_limit": raw_limit, "intended_limit": RAG_RETRIEVAL_LIMIT_WORKSPACE},
                    metadata={"collection": self.page_vector_service.COLLECTION_NAME, "filter_by": "workspaceId",
                              "retrieval_strategy": context_type.value}
                )
                workspace_uuid = PyUUID(actual_workspace_id_str)
                primary_results_raw = await self.page_vector_service.search(
                    tenant_id=tenant_id, query=query, limit=raw_limit,
                    workspace_id=workspace_uuid, use_hybrid=True, alpha=0.6
                )
                primary_results_filtered = self._filter_results_by_relevance(primary_results_raw, current_sub_span)

            elif knowledge_scope == ChatKnowledgeScope.DEFAULT:
                raw_limit = RAG_RETRIEVAL_LIMIT_DEFAULT * 2
                if actual_workspace_id_str:  # Workspace-aware default
                    context_type = ContextType.SCOPED_DEFAULT_KNOWLEDGE_WORKSPACE_AWARE
                    current_sub_span.update(
                        input={"raw_limit": raw_limit, "intended_limit": RAG_RETRIEVAL_LIMIT_DEFAULT,
                               "workspace_id_used": actual_workspace_id_str},
                        metadata={"collection": self.page_vector_service.COLLECTION_NAME,
                                  "filter_by": "workspaceId_for_default", "retrieval_strategy": context_type.value}
                    )
                    workspace_uuid = PyUUID(actual_workspace_id_str)
                    primary_results_raw = await self.page_vector_service.search(
                        tenant_id=tenant_id, query=query, limit=raw_limit,
                        workspace_id=workspace_uuid, use_hybrid=True, alpha=0.5
                    )
                else:  # Tenant-wide default
                    context_type = ContextType.SCOPED_DEFAULT_KNOWLEDGE_TENANT_WIDE
                    current_sub_span.update(
                        input={"raw_limit": raw_limit, "intended_limit": RAG_RETRIEVAL_LIMIT_DEFAULT},
                        metadata={"collection": self.page_vector_service.COLLECTION_NAME,
                                  "filter_by": "tenant_wide_default", "retrieval_strategy": context_type.value}
                    )
                    primary_results_raw = await self.page_vector_service.search(
                        tenant_id=tenant_id, query=query, limit=raw_limit,
                        use_hybrid=True, alpha=0.5
                    )
                primary_results_filtered = self._filter_results_by_relevance(primary_results_raw, current_sub_span)

            elif knowledge_scope == ChatKnowledgeScope.TEMPLATE:  # Placeholder
                context_type = ContextType.SCOPED_TEMPLATE_CONTENT
                current_sub_span.update(metadata={"retrieval_strategy": context_type.value})
                logger.warning(
                    f"TraceID: {trace_span.id} - TEMPLATE scope RAG is not fully implemented, skipping retrieval.")
                primary_results_filtered = [];
                primary_results_raw = [];
                augmentation_results_filtered = None

            current_sub_span.end(output={
                "retrieved_primary_raw_count": len(primary_results_raw),
                "retrieved_primary_filtered_count": len(primary_results_filtered),
                "retrieved_augmentation_final_count": len(augmentation_results_filtered or []),
                "final_context_type_determined": context_type.value,
                # ... (add chunk previews if needed)
            })

            if not primary_results_filtered and not (
                    augmentation_results_filtered and len(augmentation_results_filtered) > 0):
                if context_type != ContextType.NO_CONTEXT_USED:  # Avoid re-logging if already set to this due to error
                    context_type = ContextType.NO_CONTEXT_USED
                    logger.info(
                        f"TraceID: {trace_span.id} - No relevant chunks for '{knowledge_scope.value}', falling back to NO_CONTEXT_USED.")
            return primary_results_filtered, context_type, augmentation_results_filtered

        except ValueError as ve:
            msg = f"Invalid UUID format for scope/workspace ID. Scope: {knowledge_scope.value}, ScopeID: {knowledge_scope_id}, WsID: {workspace_id_for_augmentation}. Error: {ve}"
            logger.error(f"TraceID: {trace_span.id} - {msg}", exc_info=False)
            current_sub_span.end(level="ERROR", status_message=msg, output={"error": msg})
            raise ValueError(msg) from ve  # Re-raise for node
        except VectorStoreOperationError as e:
            log_message = f"TraceID: {trace_span.id} - Weaviate search failed for scope '{knowledge_scope.value}', tenant {tenant_id}: {e}."
            logger.error(log_message, exc_info=True)
            current_sub_span.end(level="ERROR",
                                 status_message=f"Weaviate search (scope: {knowledge_scope.value}) failed: {e}.",
                                 output={"error": str(e)})
            raise  # Re-raise for node
        except Exception as e:
            logger.error(
                f"TraceID: {trace_span.id} - Unexpected error during knowledge scope retrieval for '{knowledge_scope.value}': {e}",
                exc_info=True)
            current_sub_span.end(level="ERROR", status_message=f"Unexpected retrieval error: {e}",
                                 output={"error": str(e)})
            raise  # Re-raise

    async def _format_context(
            self, primary_results: List[Dict[str, Any]], context_type: ContextType,
            augmentation_results: Optional[List[Dict[str, Any]]] = None, trace_span: Optional[Any] = None
    ) -> Tuple[str, List[Dict[str, Any]]]:
        # (Existing code - ensure trace_span is the Langfuse span object and self.db is used)
        # Renamed 'trace' param to 'trace_span' for clarity
        # ... (rest of the existing method, using trace_span and self.db) ...
        all_effective_results_with_scope: List[Tuple[Dict[str, Any], CitationScopeType]] = []
        added_fingerprints = set()
        citations_list: List[Dict[str, Any]] = []
        trace_id_str = getattr(trace_span, 'id', 'N/A')
        focused_doc_ids_to_fetch_url: List[str] = []

        def add_unique_result_with_scope(result_item: Dict[str, Any], scope_type: CitationScopeType):
            # (Existing inner function logic)
            props = result_item.get("properties", {});
            chunk_fingerprint = props.get("chunkFingerprint")
            if chunk_fingerprint and chunk_fingerprint in added_fingerprints: return
            all_effective_results_with_scope.append((result_item, scope_type))
            if chunk_fingerprint: added_fingerprints.add(chunk_fingerprint)
            if scope_type == CitationScopeType.FOCUSED_DOCUMENT:
                doc_id = props.get("documentId")
                if doc_id and doc_id not in focused_doc_ids_to_fetch_url:
                    focused_doc_ids_to_fetch_url.append(doc_id)

        primary_scope_type: CitationScopeType
        if context_type == ContextType.USER_SELECTED_UPLOADED_DOCUMENTS:
            primary_scope_type = CitationScopeType.FOCUSED_DOCUMENT
        elif context_type == ContextType.SCOPED_PAGE_WITH_WORKSPACE_AUGMENTATION:
            primary_scope_type = CitationScopeType.KNOWLEDGE_BASE_PAGE
        elif context_type == ContextType.SCOPED_WORKSPACE_CONTENT:
            primary_scope_type = CitationScopeType.KNOWLEDGE_BASE_WORKSPACE
        elif context_type in [ContextType.SCOPED_DEFAULT_KNOWLEDGE_WORKSPACE_AWARE,
                              ContextType.SCOPED_DEFAULT_KNOWLEDGE_TENANT_WIDE]:
            primary_scope_type = CitationScopeType.KNOWLEDGE_BASE_DEFAULT
        else:
            primary_scope_type = CitationScopeType.KNOWLEDGE_BASE_DEFAULT

        for res in primary_results: add_unique_result_with_scope(res, primary_scope_type)
        if augmentation_results:
            for aug_res in augmentation_results: add_unique_result_with_scope(aug_res,
                                                                              CitationScopeType.KNOWLEDGE_BASE_AUGMENTATION)

        logger.debug(
            f"TraceID: {trace_id_str} - [_format_context] Total effective chunks to format: {len(all_effective_results_with_scope)}")

        uploaded_doc_urls: Dict[str, str] = {}
        if focused_doc_ids_to_fetch_url:
            url_fetch_sub_span = trace_span.span(name="fetch-uploaded-doc-urls", input={
                "doc_ids_count": len(focused_doc_ids_to_fetch_url)}) if trace_span else None
            try:
                # Use self.db for database operations
                stmt = select(UploadedDocument.uploaded_document_id, UploadedDocument.file_path).where(
                    UploadedDocument.uploaded_document_id.in_([PyUUID(uid) for uid in focused_doc_ids_to_fetch_url])
                )
                result = await self.db.execute(stmt)  # Use self.db
                rows = result.all()
                uploaded_doc_urls = {str(row.uploaded_document_id): row.file_path for row in rows if row.file_path}
                logger.info(
                    f"TraceID: {trace_id_str} - Fetched {len(uploaded_doc_urls)} URLs for {len(focused_doc_ids_to_fetch_url)} focused document IDs.")
                if url_fetch_sub_span: url_fetch_sub_span.end(output={"urls_fetched_count": len(uploaded_doc_urls)})
            except Exception as db_err:
                logger.error(f"TraceID: {trace_id_str} - Failed to fetch uploaded document URLs: {db_err}",
                             exc_info=True)
                if url_fetch_sub_span: url_fetch_sub_span.end(level="ERROR",
                                                              status_message=f"DB query failed: {db_err}",
                                                              output={"error": str(db_err)})

        if not all_effective_results_with_scope:
            logger.debug(
                f"TraceID: {trace_id_str} - [_format_context] No effective chunks, returning empty context/citations.")
            empty_message = "No relevant context was found for your query based on the current scope and relevance filtering."
            if context_type == ContextType.USER_SELECTED_UPLOADED_DOCUMENTS:
                empty_message = "No relevant information was found in the selected uploaded documents for your query after filtering by relevance."
            return empty_message, []

        context_parts = [];
        source_counter = 1
        for idx, (res_item, item_scope_type) in enumerate(all_effective_results_with_scope):
            props = res_item.get("properties", {})
            title = props.get("title", "Untitled Content");
            chunk_content = props.get("contentChunk", "")
            doc_id_prop = props.get("documentId", "Unknown ID");
            chunk_order_prop = props.get("chunkOrder", -1)
            current_source_label = f"[{source_counter}]"
            score_info_str = "";
            score_value, score_display_type = None, "none"
            if res_item.get("distance") is not None:
                score_value, score_display_type = res_item["distance"], "Distance"
            elif res_item.get("certainty") is not None:
                score_value, score_display_type = res_item["certainty"], "Certainty"
            elif res_item.get("score") is not None:
                score_value, score_display_type = res_item["score"], "Score"
            if score_value is not None: score_info_str = f" ({score_display_type}: {score_value:.4f})"

            formatted_source_part = (f"{current_source_label}{score_info_str} "
                                     f"(Type: {item_scope_type.value}, DocID: {doc_id_prop}, Chunk: {chunk_order_prop}, Title: \"{title}\"):\n{chunk_content}")
            context_parts.append(formatted_source_part)
            source_url = uploaded_doc_urls.get(
                doc_id_prop) if item_scope_type == CitationScopeType.FOCUSED_DOCUMENT else None
            citation_obj = Citation(
                source_label=current_source_label, document_id=doc_id_prop, title=title,
                preview=chunk_content[:200] + "..." if len(chunk_content) > 200 else chunk_content,
                scope_type=item_scope_type, source_url=source_url
            )
            citations_list.append(citation_obj.model_dump())
            source_counter += 1

        final_context_string = "\n\n---\n\n".join(context_parts)
        logger.debug(
            f"TraceID: {trace_id_str} - [_format_context] Context length: {len(final_context_string)}, Citations: {len(citations_list)}")
        if trace_span and hasattr(trace_span, 'event') and callable(getattr(trace_span, 'event', None)):
            trace_citations_preview = [
                {"label": c.get("source_label"), "doc_id": c.get("document_id"), "scope": c.get("scope_type"),
                 "has_url": bool(c.get("source_url"))} for c in citations_list[:5]]
            trace_span.event(
                name="context-formatting",
                input={"primary_results_input_count": len(primary_results),
                       "augmentation_results_input_count": len(augmentation_results or []),
                       "input_primary_chunks": [self._format_chunk_for_trace(item) for item in primary_results[:5]],
                       "input_augmentation_chunks": [self._format_chunk_for_trace(item) for item in
                                                     (augmentation_results or [])[:5]]},
                output={"effective_chunks_count": len(all_effective_results_with_scope),
                        "final_context_string_length": len(final_context_string),
                        "final_context_string_preview": final_context_string[:1000] + (
                            "..." if len(final_context_string) > 1000 else ""),
                        "effective_chunks_formatted_for_trace": [self._format_chunk_for_trace(item[0]) for item in
                                                                 all_effective_results_with_scope[:5]],
                        "citations_generated_count": len(citations_list), "citations_preview": trace_citations_preview},
                metadata={"context_type": context_type.value}
            )
        return final_context_string, citations_list

    async def _save_chat_message(
            self, conversation_id: str, sender_type: SenderType, content: str,
            user_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
            trace_span: Optional[Any] = None
    ):
        # (Existing code - ensure trace_span is the Langfuse span object and self.db is used)
        # Renamed 'trace' param to 'trace_span' for clarity
        trace_id_str = getattr(trace_span, 'id', 'N/A')
        try:
            try:
                conv_uuid = PyUUID(conversation_id)
            except ValueError:
                logger.error(
                    f"TraceID: {trace_id_str} - Invalid conversation_id format: {conversation_id}. Cannot save message.")
                if trace_span and hasattr(trace_span, 'event'): trace_span.event(
                    name="save-message-failed-invalid-conv-id", input={"conversation_id": conversation_id},
                    level="ERROR", metadata={"error": "Invalid conversation_id format"})
                return

            chat_message = ChatMessage(
                conversation_id=conv_uuid, sender_type=sender_type, message_content=content,
                sender_user_id=user_id if sender_type == SenderType.USER else None, meta_data=metadata or {}
            )
            self.db.add(chat_message)  # Use self.db
            stmt = sqlalchemy_update(ChatConversation).where(ChatConversation.conversation_id == conv_uuid).values(
                updated_at=func.now()).execution_options(synchronize_session=False)
            await self.db.execute(stmt)  # Use self.db
            await self.db.commit()  # Use self.db
            await self.db.refresh(chat_message)  # Use self.db

            logger.debug(
                f"TraceID: {trace_id_str} - Saved {sender_type.value} message {chat_message.message_id} for conversation {conversation_id}.")
            if trace_span and hasattr(trace_span, 'event'):
                trace_span.event(
                    name=f"save-{sender_type.value}-message-db",
                    input={"conversation_id": conversation_id, "sender": sender_type.value,
                           "content_length": len(content), "metadata_keys": list(metadata.keys()) if metadata else []},
                    output={"message_saved": True, "conversation_updated_at_updated": True,
                            "chat_message_id": str(chat_message.message_id)}, level="DEBUG"
                )
        except Exception as e:
            await self.db.rollback()  # Use self.db
            logger.error(
                f"TraceID: {trace_id_str} - Failed to add/process {sender_type.value} message for conversation {conversation_id}: {e}",
                exc_info=True)
            if trace_span and hasattr(trace_span, 'event'):
                trace_span.event(
                    name=f"save-{sender_type.value}-message-db-failed",
                    input={"conversation_id": conversation_id, "sender": sender_type.value,
                           "content_length": len(content)},
                    output={"error": str(e)}, metadata={"error_details": str(e)}, level="ERROR"
                )

    # --- Main public method ---
    async def generate_response(
            self,
            user_id: str,
            tenant_id: str,
            query: str,
            chat_conversation_id: str,
            selected_uploaded_document_ids: Optional[List[str]] = None,
            knowledge_scope: ChatKnowledgeScope = ChatKnowledgeScope.DEFAULT,
            knowledge_scope_id: Optional[str] = None,
            workspace_id_for_scope: Optional[str] = None,
    ) -> Dict[str, Any]:

        # 1. Initialize Langfuse Trace
        # Use a new unique ID for each trace, rather than one based on conversation_id,
        # as a conversation can have multiple interactions (traces).
        trace_id = f"trace-{uuid.uuid4()}"
        log_params = {  # For Langfuse trace input
            "user_id": user_id, "tenant_id": tenant_id,
            "query_preview": query[:100], "chat_conversation_id": chat_conversation_id,
            "selected_doc_ids_count": len(selected_uploaded_document_ids) if selected_uploaded_document_ids else 0,
            "selected_doc_ids_preview": selected_uploaded_document_ids[:3] if selected_uploaded_document_ids else None,
            "knowledge_scope": knowledge_scope.value, "knowledge_scope_id": knowledge_scope_id,
            "workspace_id_for_scope": workspace_id_for_scope
        }
        logger.info(f"ChatService generate_response invoked with trace_id {trace_id}: {log_params}")

        langfuse_trace_obj: Any = self.langfuse.trace(
            id=trace_id,  # Use the newly generated trace_id
            user_id=str(user_id),
            session_id=chat_conversation_id,  # session_id remains conversation_id
            name="rag-chat-pipeline-langgraph",  # Updated name
            input=log_params,
            metadata={
                "environment": settings.ENVIRONMENT,
                "llm_model_configured": self.llm.get_model_name(),
                "llm_provider_configured": self.llm.provider_name,
                "tenant_id": tenant_id
            }
        )
        # Ensure trace_id is from the created object
        final_trace_id_for_response = getattr(langfuse_trace_obj, 'id', trace_id)

        # 2. Prepare Initial State for LangGraph
        initial_state: GraphState = {
            "user_id": user_id, "tenant_id": tenant_id, "query": query,
            "chat_conversation_id": chat_conversation_id,
            "selected_uploaded_document_ids": selected_uploaded_document_ids,
            "knowledge_scope": knowledge_scope, "knowledge_scope_id": knowledge_scope_id,
            "workspace_id_for_scope": workspace_id_for_scope,
            "langfuse_trace_obj": langfuse_trace_obj,
            "trace_id": final_trace_id_for_response,  # Pass the actual trace_id
            "db_session": self.db,  # Pass AsyncSession
            "llm_client": self.llm,
            "page_vector_service": self.page_vector_service,
            "document_vector_service": self.document_vector_service,
            # Initialize other fields to default/empty states
            "error_message": None,
            "final_answer": "Sorry, an initialization error occurred.",  # Default before graph runs
            "llm_used_provider": None,
            "primary_search_results_filtered": [],
            "augmentation_search_results_filtered": None,
            "context_type_used": ContextType.NO_CONTEXT_USED,
            "retrieved_context_str": "No context processed.",
            "citations": [],
            "all_retrieved_doc_ids": [],
            "retrieved_page_ids_for_augmentation": None,
            "ai_message_metadata": None,
        }

        final_state: GraphState = initial_state
        try:
            # 3. Invoke the Graph
            # Provide config for recursion limit if complex graphs are expected, though not critical here yet.
            # config = {"recursion_limit": 25}
            graph_output = await self.graph.ainvoke(initial_state)  # , config=config)

            # The graph_output will be the final state of GraphState
            if graph_output:  # Should always return a state
                final_state = graph_output
            else:  # Should not happen with StateGraph
                logger.error(f"TraceID: {final_trace_id_for_response} - LangGraph ainvoke returned None or empty.")
                final_state["error_message"] = (final_state.get("error_message") or
                                                "Internal error: Graph execution yielded no state.")
                final_state["final_answer"] = "An unexpected internal error occurred."

        except ValueError as ve:  # Catch Pydantic-like validation errors from ChatRequest if they bypass FastAPI
            logger.warning(
                f"TraceID: {final_trace_id_for_response} - Invalid input for chat generation (ValueError): {ve}",
                exc_info=False)
            final_state["error_message"] = f"Invalid input provided: {str(ve)}"
            final_state["final_answer"] = f"There was an issue with the input: {str(ve)}"
            final_state["context_type_used"] = ContextType.NO_CONTEXT_USED
            final_state["citations"] = []
            if hasattr(langfuse_trace_obj, 'update'): langfuse_trace_obj.update(level="ERROR",
                                                                                status_message=final_state[
                                                                                    "error_message"], output={
                    "error": final_state["error_message"]})
        except Exception as e:
            logger.error(
                f"TraceID: {final_trace_id_for_response} - Unhandled exception during LangGraph execution: {e}",
                exc_info=True)
            final_state["error_message"] = (final_state.get("error_message") or
                                            f"An unexpected server error occurred processing your request: {e}")
            final_state["final_answer"] = "An unexpected server error occurred. Please try again later."
            final_state["context_type_used"] = ContextType.NO_CONTEXT_USED
            final_state["citations"] = []
            if hasattr(langfuse_trace_obj, 'update'): langfuse_trace_obj.update(level="ERROR", status_message=str(e),
                                                                                output={"error": str(e)})
        finally:
            # 4. Finalize Langfuse Trace
            if langfuse_trace_obj:
                try:
                    status_message = final_state.get("error_message") or "Chat generation successful"
                    trace_output = {
                        "final_answer_preview": final_state.get("final_answer", "")[:500] if not final_state.get(
                            "error_message") else None,
                        "error_message": final_state.get("error_message"),
                        "final_context_type_used": final_state.get("context_type_used",
                                                                   ContextType.NO_CONTEXT_USED).value,
                        "llm_provider_used": final_state.get("llm_used_provider"),
                        "citations_data_count": len(final_state.get("citations", []))
                    }
                    if hasattr(langfuse_trace_obj, 'update'):  # Use update to set final status
                        langfuse_trace_obj.update(
                            # Changed from .end() to .update() as .end() is for spans/generations
                            output=trace_output,
                            level="ERROR" if final_state.get("error_message") else "DEFAULT",
                            # Langfuse uses "DEFAULT" for success
                            status_message=status_message
                        )
                    # Note: langfuse_trace_obj.trace() itself does not have an .end() method in the same way spans do.
                    # The trace is considered "ended" when the program finishes or explicitly via shutdown.
                    # Updates are sufficient for marking its completion status.
                except Exception as tr_final_err:
                    logger.error(f"TraceID: {final_trace_id_for_response} - Final trace update failed: {tr_final_err}")

        # 5. Construct and Return Result Dictionary (Matches original format)
        # Ensure all keys exist in final_state, providing defaults if necessary.
        return {
            "answer": final_state.get("final_answer", "Error processing request."),
            "session_id": chat_conversation_id,  # from input, not state
            "trace_id": final_trace_id_for_response,  # The actual ID used for the trace
            "llm_used": final_state.get("llm_used_provider"),
            "error": final_state.get("error_message"),
            "context_type_used": final_state.get("context_type_used", ContextType.NO_CONTEXT_USED),
            "retrieved_document_ids": list(set(final_state.get("all_retrieved_doc_ids", []))),
            "retrieved_page_ids_for_augmentation": list(
                set(final_state.get("retrieved_page_ids_for_augmentation", []) if final_state.get(
                    "retrieved_page_ids_for_augmentation") is not None else [])),
            "citations": final_state.get("citations", [])
        }


def get_chat_service(
        llm: BaseLLMClient = Depends(get_primary_llm_client),
        langfuse_client: Langfuse = Depends(get_langfuse),
        page_vector_service: PageVectorServiceAsync = Depends(get_page_vector_service_async),
        document_vector_service: DocumentVectorServiceAsync = Depends(get_document_vector_service_async),
        db: AsyncSession = Depends(get_db),
) -> ChatService:
    # This will now return an instance with a compiled graph
    return ChatService(
        llm=llm,
        langfuse_client=langfuse_client,
        page_vector_service=page_vector_service,
        document_vector_service=document_vector_service,
        db=db
    )