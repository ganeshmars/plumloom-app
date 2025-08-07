# app/services/chat_service.py
import uuid
import tempfile
import os
import json
import asyncio  # For asyncio.to_thread
from typing import Dict, Any, Optional, List, Tuple, TypedDict
from uuid import UUID as PyUUID
from urllib.parse import urlparse
import io  # For pd.read_csv from string

import pandas as pd
import redis.asyncio as aioredis
from fastapi import Depends
from langfuse import Langfuse
from sqlalchemy import update as sqlalchemy_update, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_experimental.agents.agent_toolkits import create_csv_agent
from langchain.agents.agent_types import AgentType
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage

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
from app.core.redis import get_redis
from app.core.storage import get_file_content_sync
from app.core.logging_config import logger, app_logger

settings = get_settings()

RAG_RETRIEVAL_LIMIT_DEFAULT = 3
RAG_RETRIEVAL_LIMIT_FOCUSED_DOCS = 5
RAG_RETRIEVAL_LIMIT_WORKSPACE = 5
RAG_RETRIEVAL_LIMIT_PAGE_PRIMARY = 2
RAG_RETRIEVAL_LIMIT_PAGE_AUGMENT = 2

MIN_CERTAINTY_THRESHOLD = 0.70
MAX_DISTANCE_THRESHOLD = 0.65
MIN_HYBRID_SCORE_THRESHOLD = 0.55

CSV_AGENT_MODEL_NAME = "gpt-3.5-turbo-0125"

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

    # Langfuse & DB & Services
    langfuse_trace_obj: Any
    db_session: AsyncSession
    llm_client: BaseLLMClient
    page_vector_service: PageVectorServiceAsync
    document_vector_service: DocumentVectorServiceAsync
    redis_client: aioredis.Redis

    # Intermediate & Output values
    trace_id: str
    error_message: Optional[str]
    final_answer: str
    llm_used_provider: Optional[str]

    # RAG Specific
    primary_search_results_filtered: List[Dict[str, Any]]
    augmentation_search_results_filtered: Optional[List[Dict[str, Any]]]
    context_type_used: ContextType
    retrieved_context_str: str
    citations: List[Dict[str, Any]]
    all_retrieved_doc_ids: List[str]
    retrieved_page_ids_for_augmentation: Optional[List[str]]

    # For saving AI message
    ai_message_metadata: Optional[Dict[str, Any]]

    # CSV Processing State
    is_csv_mode: bool
    csv_document_id: Optional[str]
    csv_file_name: Optional[str]
    csv_content_str: Optional[str]
    csv_temp_file_path: Optional[str]
    csv_classification_result: Optional[Dict[str, Any]]
    csv_text_insight: Optional[str]
    csv_plot_json_data: Optional[Dict[str, Any]]
    csv_agent_llm_provider: Optional[str]


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
        self.graph = self._build_graph()

    def _get_csv_agent_llm(self) -> ChatOpenAI:  # Explicitly ChatOpenAI for create_csv_agent
        # Ensure OPENAI_API_KEY is available in settings if creating new
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY must be set in environment/settings for CSV agent.")

        # Check if the main LLM is already ChatOpenAI or a compatible type
        # This part is heuristic; adapt if your BaseLLMClient has type info
        if isinstance(self.llm, ChatOpenAI) and self.llm.model_name == CSV_AGENT_MODEL_NAME:
            return self.llm  # type: ignore

        logger.info(
            f"Main LLM client type {type(self.llm)} not directly used for CSV agent. Creating new ChatOpenAI for CSV agent with model {CSV_AGENT_MODEL_NAME}.")
        return ChatOpenAI(model=CSV_AGENT_MODEL_NAME, temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)

    async def _parse_gcs_url(self, gcs_url: str) -> Tuple[Optional[str], Optional[str]]:
        if not gcs_url:
            return None, None
        try:
            parsed_url = urlparse(gcs_url)
            if parsed_url.scheme == "gs":
                bucket_name = parsed_url.netloc
                object_name = parsed_url.path.lstrip('/')
                return bucket_name, object_name
            elif parsed_url.scheme in ["http", "https"] and "storage.googleapis.com" in parsed_url.netloc:
                # Handles URLs like:
                # https://storage.googleapis.com/BUCKET_NAME/OBJECT_NAME
                # https://BUCKET_NAME.storage.googleapis.com/OBJECT_NAME (less common for direct client usage but possible)
                path_parts = parsed_url.path.lstrip('/').split('/', 1)
                if len(path_parts) == 2:
                    bucket_name = path_parts[0]
                    object_name = path_parts[1]
                    return bucket_name, object_name
                elif parsed_url.netloc != "storage.googleapis.com":  # e.g. BUCKET_NAME.storage.googleapis.com
                    bucket_name = parsed_url.netloc.split(".storage.googleapis.com")[0]
                    object_name = parsed_url.path.lstrip('/')
                    return bucket_name, object_name

            logger.warning(f"Could not parse GCS URL: {gcs_url} into bucket/object using common patterns.")
            return None, None
        except Exception as e:
            logger.error(f"Error parsing GCS URL {gcs_url}: {e}")
            return None, None

    async def _get_document_details_from_db(self, doc_ids: List[str]) -> Dict[str, Dict[str, str]]:
        if not doc_ids: return {}
        pyuuid_doc_ids = [PyUUID(doc_id) for doc_id in doc_ids]
        stmt = select(
            UploadedDocument.uploaded_document_id,
            UploadedDocument.file_type,
            UploadedDocument.file_path,
            UploadedDocument.file_name
        ).where(UploadedDocument.uploaded_document_id.in_(pyuuid_doc_ids))

        result = await self.db.execute(stmt)
        db_rows = result.all()

        doc_details = {}
        for row in db_rows:
            doc_details[str(row.uploaded_document_id)] = {
                "file_type": row.file_type,
                "file_path": row.file_path,
                "file_name": row.file_name
            }
        return doc_details

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(GraphState)

        workflow.add_node("save_user_message_node", self._save_user_message_node)
        workflow.add_node("initial_document_analysis_node", self._initial_document_analysis_node)

        workflow.add_node("csv_classify_query_node", self._csv_classify_query_node)
        workflow.add_node("csv_generate_text_output_node", self._csv_generate_text_output_node)
        workflow.add_node("csv_generate_plot_json_node", self._csv_generate_plot_json_node)
        workflow.add_node("csv_prepare_response_node", self._csv_prepare_response_node)

        workflow.add_node("retrieve_focused_docs_node", self._retrieve_focused_docs_node)
        workflow.add_node("retrieve_scoped_knowledge_node", self._retrieve_scoped_knowledge_node)
        workflow.add_node("format_context_node", self._format_context_node)
        workflow.add_node("generate_llm_response_node", self._generate_llm_response_node)

        workflow.add_node("save_ai_message_node", self._save_ai_message_node)
        workflow.add_node("prepare_error_response_node", self._prepare_error_response_node)
        workflow.add_node("cleanup_temp_files_node", self._cleanup_temp_files_node)

        workflow.set_entry_point("save_user_message_node")
        workflow.add_edge("save_user_message_node", "initial_document_analysis_node")

        workflow.add_conditional_edges(
            "initial_document_analysis_node",
            self._route_after_doc_analysis,
            {
                "csv_processing": "csv_classify_query_node",
                "focused_rag": "retrieve_focused_docs_node",
                "scoped_rag": "retrieve_scoped_knowledge_node",
                "error": "prepare_error_response_node"
            }
        )

        workflow.add_conditional_edges(
            "csv_classify_query_node",
            self._route_csv_after_classification,
            {
                "generate_text": "csv_generate_text_output_node",
                "generate_plot": "csv_generate_plot_json_node",
                "generate_both_text_first": "csv_generate_text_output_node",
                "compile_csv": "csv_prepare_response_node",
                "error": "prepare_error_response_node"
            }
        )
        workflow.add_conditional_edges(
            "csv_generate_text_output_node",
            self._route_csv_after_text_output,
            {
                "generate_plot": "csv_generate_plot_json_node",
                "compile_csv": "csv_prepare_response_node",
                "error": "prepare_error_response_node"
            }
        )
        workflow.add_edge("csv_generate_plot_json_node", "csv_prepare_response_node")
        workflow.add_edge("csv_prepare_response_node", "save_ai_message_node")

        workflow.add_edge("retrieve_focused_docs_node", "format_context_node")
        workflow.add_edge("retrieve_scoped_knowledge_node", "format_context_node")

        workflow.add_conditional_edges(
            "format_context_node",
            self._check_retrieval_success,
            {
                "success": "generate_llm_response_node",
                "retrieval_failed_or_empty": "generate_llm_response_node",
                "critical_error": "prepare_error_response_node"
            }
        )
        workflow.add_conditional_edges(
            "generate_llm_response_node",
            self._check_llm_success,
            {
                "success": "save_ai_message_node",
                "llm_error": "prepare_error_response_node",
            }
        )

        workflow.add_edge("save_ai_message_node", "cleanup_temp_files_node")
        workflow.add_edge("prepare_error_response_node", "cleanup_temp_files_node")
        workflow.add_edge("cleanup_temp_files_node", END)

        return workflow.compile()

    async def _route_after_doc_analysis(self, state: GraphState) -> str:
        trace_id = state.get("trace_id", "N/A")
        if state.get("error_message"):
            logger.error(f"TraceID: {trace_id} - Routing to error due to: {state['error_message']}")
            return "error"
        if state.get("is_csv_mode"):
            if state.get("csv_content_str"):
                logger.info(f"TraceID: {trace_id} - Routing to CSV processing.")
                return "csv_processing"
            else:
                logger.error(f"TraceID: {trace_id} - CSV mode active but no CSV content loaded. Critical error.")
                state["error_message"] = state.get("error_message") or "Failed to load CSV content for processing."
                return "error"
        if state.get("selected_uploaded_document_ids"):
            logger.info(f"TraceID: {trace_id} - Routing to focused RAG (non-CSV documents).")
            return "focused_rag"

        logger.info(f"TraceID: {trace_id} - Routing to scoped RAG (no specific documents selected or no CSVs).")
        return "scoped_rag"

    async def _route_csv_after_classification(self, state: GraphState) -> str:
        trace_id = state.get("trace_id", "N/A")
        if state.get("error_message"): return "error"

        classification = state.get("csv_classification_result")
        if not classification or classification.get("type") == "ERROR" or not classification.get("type"):
            logger.warning(f"TraceID: {trace_id} - CSV classification failed or type is missing. Defaulting to error.")
            state["error_message"] = state.get("error_message") or "CSV query classification failed."
            return "error"

        query_type = classification["type"]
        text_task = classification.get("text_task")
        plot_task = classification.get("plot_task")

        if query_type == "BOTH":
            if text_task:
                logger.info(f"TraceID: {trace_id} - CSV BOTH: Routing to text generation first.")
                return "generate_both_text_first"
            elif plot_task:
                logger.info(f"TraceID: {trace_id} - CSV BOTH (no text_task): Routing to plot generation.")
                return "generate_plot"
            else:
                logger.warning(f"TraceID: {trace_id} - CSV BOTH specified but no text or plot task. Compiling.")
                return "compile_csv"
        elif query_type == "TEXT_INSIGHT":
            if text_task:
                logger.info(f"TraceID: {trace_id} - CSV TEXT_INSIGHT: Routing to text generation.")
                return "generate_text"
            else:
                logger.warning(f"TraceID: {trace_id} - CSV TEXT_INSIGHT specified but no text task. Compiling.")
                return "compile_csv"
        elif query_type == "PLOT":
            if plot_task:
                logger.info(f"TraceID: {trace_id} - CSV PLOT: Routing to plot generation.")
                return "generate_plot"
            else:
                logger.warning(f"TraceID: {trace_id} - CSV PLOT specified but no plot task. Compiling.")
                return "compile_csv"

        logger.warning(f"TraceID: {trace_id} - Unknown CSV classification type: {query_type}. Compiling.")
        return "compile_csv"

    async def _route_csv_after_text_output(self, state: GraphState) -> str:
        trace_id = state.get("trace_id", "N/A")
        if state.get("error_message"): return "error"

        classification = state.get("csv_classification_result", {})
        if classification.get("type") == "BOTH" and classification.get("plot_task"):
            logger.info(f"TraceID: {trace_id} - CSV Text output done for BOTH. Routing to plot generation.")
            return "generate_plot"

        logger.info(f"TraceID: {trace_id} - CSV Text output done (or not BOTH/plot). Routing to compile CSV response.")
        return "compile_csv"

    async def _check_retrieval_success(self, state: GraphState) -> str:
        trace_id = state.get("trace_id", "N/A")
        if state.get("error_message") and "Knowledge base access or input issue during retrieval" in state[
            "error_message"]:
            logger.warning(f"TraceID: {trace_id} - RAG retrieval critical error: {state['error_message']}")
            return "critical_error"
        logger.info(f"TraceID: {trace_id} - RAG retrieval check passed or non-critical.")
        return "success"

    async def _check_llm_success(self, state: GraphState) -> str:
        trace_id = state.get("trace_id", "N/A")
        if state.get("error_message") and ("LLM service unavailable" in state[
            "error_message"] or "An unexpected error occurred during AI response generation" in state["error_message"]):
            logger.error(f"TraceID: {trace_id} - RAG LLM error: {state['error_message']}")
            return "llm_error"
        logger.info(f"TraceID: {trace_id} - RAG LLM generation check passed.")
        return "success"

    async def _save_user_message_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _save_user_message_node")
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
            logger.error(f"TraceID: {trace_id} - Error in _save_user_message_node: {e}", exc_info=True)
            return {"error_message": f"Failed to save user message: {e}"}

    async def _initial_document_analysis_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _initial_document_analysis_node")
        patch: Dict[str, Any] = {"is_csv_mode": False, "csv_content_str": None, "csv_document_id": None,
                                 "csv_file_name": None}
        selected_doc_ids = state.get("selected_uploaded_document_ids")

        if selected_doc_ids:
            try:
                doc_infos = await self._get_document_details_from_db(selected_doc_ids)

                for doc_id_str, info in doc_infos.items():
                    if info and info.get('file_type', '').lower() == 'csv':
                        patch["csv_document_id"] = doc_id_str
                        patch["csv_file_name"] = info.get('file_name', 'Unknown CSV')
                        gcs_public_url = info.get('file_path')
                        logger.info(
                            f"TraceID: {trace_id} - Found CSV: {doc_id_str} ('{patch['csv_file_name']}') with GCS URL: {gcs_public_url}")

                        redis_key = f"csv_cache:{state['tenant_id']}:{doc_id_str}"
                        csv_content_str = await state["redis_client"].get(redis_key)

                        if csv_content_str:
                            logger.info(f"TraceID: {trace_id} - CSV {doc_id_str} found in Redis cache.")
                        else:
                            logger.info(f"TraceID: {trace_id} - CSV {doc_id_str} not in Redis. Fetching from GCS.")
                            if not gcs_public_url:
                                msg = f"CSV file {doc_id_str} has no GCS file_path."
                                logger.error(f"TraceID: {trace_id} - {msg}")
                                patch["error_message"] = msg
                                return patch

                            bucket_name, object_name = await self._parse_gcs_url(gcs_public_url)
                            if not bucket_name or not object_name:
                                msg = f"Could not parse GCS URL for CSV {doc_id_str}: {gcs_public_url}"
                                logger.error(f"TraceID: {trace_id} - {msg}")
                                patch["error_message"] = msg
                                return patch

                            try:
                                content_bytes = await asyncio.to_thread(get_file_content_sync, object_name, bucket_name)
                                csv_content_str = content_bytes.decode('utf-8', errors='replace')
                                logger.info(
                                    f"TraceID: {trace_id} - Fetched CSV {doc_id_str} from GCS. Size: {len(csv_content_str)} chars.")
                                await state["redis_client"].set(redis_key, csv_content_str, ex=3600)  # Cache for 1 hour
                                logger.info(f"TraceID: {trace_id} - Cached CSV {doc_id_str} in Redis.")
                            except FileNotFoundError:
                                msg = f"CSV file not found at GCS path for {doc_id_str}: gs://{bucket_name}/{object_name}"
                                logger.error(f"TraceID: {trace_id} - {msg}")
                                patch["error_message"] = msg
                                return patch
                            except Exception as e_gcs:
                                msg = f"Failed to fetch CSV {doc_id_str} from GCS: {e_gcs}"
                                logger.error(f"TraceID: {trace_id} - {msg}", exc_info=True)
                                patch["error_message"] = msg
                                return patch

                        patch["csv_content_str"] = csv_content_str
                        patch["is_csv_mode"] = True
                        break

                if patch["is_csv_mode"] and not patch.get("csv_content_str") and not patch.get("error_message"):
                    patch["error_message"] = f"Failed to load content for CSV file {patch.get('csv_document_id')}."
                    patch["is_csv_mode"] = False

            except Exception as e_main:
                logger.error(f"TraceID: {trace_id} - Error during initial document analysis: {e_main}", exc_info=True)
                patch["error_message"] = f"Error analyzing documents: {e_main}"
                patch["is_csv_mode"] = False

        if patch["is_csv_mode"]:
            logger.info(f"TraceID: {trace_id} - CSV mode activated for document: {patch['csv_document_id']}")
            patch["context_type_used"] = ContextType.CSV_DATA_INSIGHTS
        elif not selected_doc_ids:
            logger.info(f"TraceID: {trace_id} - No documents selected. Standard scoped RAG will apply.")
        else:
            logger.info(
                f"TraceID: {trace_id} - Selected documents are not CSVs or CSV loading failed. Focused RAG will apply.")
        return patch

    async def _cleanup_temp_files_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state.get("trace_id", "N/A")
        temp_file_path = state.get("csv_temp_file_path")
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"TraceID: {trace_id} - Successfully cleaned up temp CSV file: {temp_file_path}")
            except Exception as e:
                logger.error(f"TraceID: {trace_id} - Error cleaning up temp CSV file {temp_file_path}: {e}",
                             exc_info=True)
        return {}

    async def _csv_classify_query_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _csv_classify_query_node for doc ID {state.get('csv_document_id')}")
        query = state["query"]
        csv_file_name = state.get("csv_file_name", "the uploaded CSV")

        df_preview_cols_info = ""
        if state.get("csv_content_str"):
            try:
                # Use io.StringIO to read from string
                temp_df = pd.read_csv(io.StringIO(state["csv_content_str"]), nrows=5)
                df_preview_cols_info = f"The CSV file ('{csv_file_name}') has columns such as: {', '.join(temp_df.columns.tolist()[:5])}..."  # Preview first 5 columns
            except Exception as e_preview:
                logger.warning(
                    f"TraceID: {trace_id} - Could not get column names for CSV classifier preview: {e_preview}")

        classification_prompt_messages = [
            SystemMessage(content=f"""You are a query classification expert for a CSV data analysis assistant.
The user is asking about data in a CSV file named '{csv_file_name}'. {df_preview_cols_info}
Your task is to analyze the user's query and determine the type of response and specific sub-tasks.
The dataframe `df` (derived from '{csv_file_name}') will be available for later processing by other components.

Possible output types:
1. 'TEXT_INSIGHT': For textual answers, definitions, or information that may require calculations or aggregations over the ENTIRE dataset (e.g., total sum, average, "how are sales performed by state?").
2. 'PLOT': For generating a Plotly graph visualization (output as Plotly.js compatible JSON).
3. 'BOTH': If the query requires both a plot AND a 'TEXT_INSIGHT'.

Output a JSON object with the following structure:
{{
  "type": "TEXT_INSIGHT" | "PLOT" | "BOTH",
  "text_task": "Specific instruction for the textual part (if 'TEXT_INSIGHT' or 'BOTH'), or null if 'PLOT' only. This task will be given to an agent that can analyze the full CSV.",
  "plot_task": "Specific instruction for generating Plotly.js JSON data (if 'PLOT' or 'BOTH'), or null if text-only. This task will be given to an agent that can analyze the full CSV and generate Plotly.js compatible JSON."
}}

Examples:
Query: "What are the column names?"
Output: {{"type": "TEXT_INSIGHT", "text_task": "List all the column names from the dataset.", "plot_task": null}}

Query: "What is the total sales?"
Output: {{"type": "TEXT_INSIGHT", "text_task": "Calculate the total sales by summing the relevant sales/amount column from the entire dataset and provide the result.", "plot_task": null}}

Query: "Plot a histogram of ages."
Output: {{"type": "PLOT", "text_task": null, "plot_task": "Generate Plotly.js JSON data for a histogram of the 'age' column."}}

Query: "Show me the average price per category and also plot it as a bar chart."
Output: {{"type": "BOTH", "text_task": "Calculate the average price for each category from the entire dataset and list them.", "plot_task": "Generate Plotly.js JSON data for a bar chart showing average price by category."}}
"""),
            ("user", f"User Query about '{csv_file_name}': \"{query}\"")
        ]
        classification_prompt = ChatPromptTemplate.from_messages(classification_prompt_messages)

        csv_classifier_llm = self._get_csv_agent_llm()
        chain = classification_prompt | csv_classifier_llm

        span = state["langfuse_trace_obj"].span(name="csv-query-classification",
                                                input={"query": query, "csv_file_name": csv_file_name,
                                                       "columns_preview": df_preview_cols_info})
        response_content_str = ""  # Initialize for error case
        try:
            response = await chain.ainvoke({"query": query})
            response_content_str = response.content
            if not isinstance(response_content_str, str): response_content_str = str(response_content_str)

            response_content_str = response_content_str.strip()
            if response_content_str.startswith("```json"):
                response_content_str = response_content_str[7:-3].strip()
            elif response_content_str.startswith("```"):
                response_content_str = response_content_str[3:-3].strip()

            classification_result = json.loads(response_content_str)
            logger.info(f"TraceID: {trace_id} - CSV Classification result: {classification_result}")
            span.end(output=classification_result)
            return {"csv_classification_result": classification_result}
        except json.JSONDecodeError as je:
            msg = f"CSV Classification JSON decoding failed: {je}. Response: {response_content_str}"
            logger.error(f"TraceID: {trace_id} - {msg}")
            span.end(level="ERROR", status_message=msg, output={"raw_response": response_content_str})
            return {"error_message": msg, "csv_classification_result": {"type": "ERROR"}}
        except Exception as e:
            msg = f"Error in CSV query classification: {e}"
            logger.error(f"TraceID: {trace_id} - {msg}", exc_info=True)
            span.end(level="ERROR", status_message=msg, output={"raw_response": response_content_str})
            return {"error_message": msg, "csv_classification_result": {"type": "ERROR"}}

    def _write_csv_to_temp_file(self, csv_content: str, trace_id: str) -> str:
        try:
            # Ensure trace_id is filesystem-safe or use a portion of it
            safe_prefix_part = trace_id.split('-')[-1]  # Use last part of UUID-like trace_id
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv", encoding="utf-8",
                                             prefix=f"chatcsv_{safe_prefix_part}_") as tmp_file:
                tmp_file.write(csv_content)
                tmp_file_path = tmp_file.name
            logger.info(f"TraceID: {trace_id} - CSV content written to temp file: {tmp_file_path}")
            return tmp_file_path
        except Exception as e:
            logger.error(f"TraceID: {trace_id} - Failed to write CSV to temp file: {e}", exc_info=True)
            raise RuntimeError(f"Failed to create temp CSV file: {e}") from e

    async def _csv_generate_text_output_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        classification = state.get("csv_classification_result", {})
        text_task = classification.get("text_task")

        if not text_task:
            logger.info(f"TraceID: {trace_id} - No text task for CSV. Skipping text output.")
            return {"csv_text_insight": None}

        logger.info(f"TraceID: {trace_id} - Node: _csv_generate_text_output_node. Task: {text_task}")
        csv_content = state.get("csv_content_str")
        if not csv_content:
            return {"error_message": "CSV content not available for text generation.",
                    "csv_text_insight": "Error: CSV content missing."}

        patch: Dict[str, Any] = {}
        # Use existing temp file if available (e.g. from a previous step if graph was structured differently)
        temp_csv_path = state.get("csv_temp_file_path")
        newly_created_temp_path_text = False

        span = state["langfuse_trace_obj"].generation(name="csv-text-insight-generation", input={"task": text_task,
                                                                                                 "csv_file_name": state.get(
                                                                                                     "csv_file_name")})
        try:
            if not temp_csv_path or not os.path.exists(temp_csv_path):
                temp_csv_path = await asyncio.to_thread(self._write_csv_to_temp_file, csv_content, trace_id)
                patch["csv_temp_file_path"] = temp_csv_path  # Store for cleanup if newly created
                newly_created_temp_path_text = True

            agent_llm = self._get_csv_agent_llm()
            patch["csv_agent_llm_provider"] = agent_llm.model_name  # Store provider info

            csv_agent = create_csv_agent(
                agent_llm, temp_csv_path, verbose=settings.DEBUG,  # Use settings for verbose
                agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
                allow_dangerous_code=True, handle_parsing_errors=True, max_iterations=15,
                agent_executor_kwargs={"handle_parsing_errors": True}
            )

            agent_prompt = f"""
            You are a data analyst agent with access to a pandas DataFrame ('df') from CSV '{state.get("csv_file_name", "the CSV")}'.
            Your assigned task is: {text_task}
            To fulfill this task, use your tools to perform necessary analysis.
            Your final response MUST be ONLY the insightful text. Do NOT include "Thought:", "Action:", "Final Answer:" prefixes. Just the answer.
            Current Task: "{text_task}"
            Based on your analysis, what are your findings?
            """
            response = await csv_agent.ainvoke({"input": agent_prompt})
            insight = response.get("output", "Could not generate text insight from CSV.")
            if isinstance(insight, str) and insight.strip().upper().startswith("FINAL ANSWER:"):
                insight = insight.strip()[len("FINAL ANSWER:"):].strip()

            logger.info(f"TraceID: {trace_id} - CSV Text Insight: {insight[:200]}...")
            span.end(output=insight, metadata={"temp_file_used": temp_csv_path})
            patch["csv_text_insight"] = insight
            return patch
        except Exception as e:
            msg = f"Error generating CSV text insight: {e}"
            logger.error(f"TraceID: {trace_id} - {msg}", exc_info=True)
            span.end(level="ERROR", status_message=msg, metadata={"temp_file_used": temp_csv_path})
            patch["error_message"] = (state.get("error_message") or "") + " " + msg
            patch["csv_text_insight"] = f"Error generating text insight: {e}"
            return patch
        # No explicit cleanup here, handled by _cleanup_temp_files_node

    async def _csv_generate_plot_json_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        classification = state.get("csv_classification_result", {})
        plot_task = classification.get("plot_task")

        if not plot_task:
            logger.info(f"TraceID: {trace_id} - No plot task for CSV. Skipping plot generation.")
            return {"csv_plot_json_data": None}

        logger.info(f"TraceID: {trace_id} - Node: _csv_generate_plot_json_node. Task: {plot_task}")
        csv_content = state.get("csv_content_str")
        if not csv_content:
            return {"error_message": "CSV content not available for plot generation.",
                    "csv_plot_json_data": {"error": "CSV content missing."}}

        patch: Dict[str, Any] = {}
        temp_csv_path = state.get("csv_temp_file_path")
        newly_created_temp_path_plot = False

        span = state["langfuse_trace_obj"].generation(name="csv-plot-json-generation", input={"task": plot_task,
                                                                                              "csv_file_name": state.get(
                                                                                                  "csv_file_name")})
        try:
            if not temp_csv_path or not os.path.exists(temp_csv_path):
                temp_csv_path = await asyncio.to_thread(self._write_csv_to_temp_file, csv_content, trace_id)
                patch["csv_temp_file_path"] = temp_csv_path
                newly_created_temp_path_plot = True

            agent_llm = self._get_csv_agent_llm()
            patch["csv_agent_llm_provider"] = patch.get(
                "csv_agent_llm_provider") or agent_llm.model_name  # Store if not already set

            csv_agent = create_csv_agent(
                agent_llm, temp_csv_path, verbose=settings.DEBUG,
                agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
                allow_dangerous_code=True, handle_parsing_errors=True, max_iterations=15,
                agent_executor_kwargs={"handle_parsing_errors": True}
            )

            agent_prompt = f"""
            You are a Python data visualization expert. Given the dataframe `df` from CSV '{state.get("csv_file_name", "the CSV")}':
            Task: {plot_task}
            Generate ONLY a JSON object that can be used by Plotly.js to render this chart.
            The JSON MUST have 'data' and 'layout' keys at the top level.
            The pandas DataFrame is available as `df`.
            Ensure the JSON is complete, valid, and directly usable by Plotly.js.
            Do NOT include any explanations, comments, or surrounding text like ```json ... ```. Just the raw JSON object.
            Example for a bar chart of sales by category:
            {{
              "data": [
                {{
                  "type": "bar",
                  "x": ["Category A", "Category B"],
                  "y": [100, 150],
                  "name": "Sales"
                }}
              ],
              "layout": {{
                "title": "Sales by Category",
                "xaxis": {{"title": "Category"}},
                "yaxis": {{"title": "Sales"}}
              }}
            }}
            """
            response = await csv_agent.ainvoke({"input": agent_prompt})
            raw_json_output = response.get("output", "")

            raw_json_output = raw_json_output.strip()
            if raw_json_output.startswith("```json"):
                raw_json_output = raw_json_output[7:-3].strip()
            elif raw_json_output.startswith("```"):
                raw_json_output = raw_json_output[3:-3].strip()

            try:
                plot_json = json.loads(raw_json_output)
                if not isinstance(plot_json, dict) or "data" not in plot_json or "layout" not in plot_json:
                    raise ValueError("Plot JSON from agent missing 'data' or 'layout' keys.")
                logger.info(f"TraceID: {trace_id} - CSV Plot JSON generated successfully.")
                span.end(output=plot_json, metadata={"temp_file_used": temp_csv_path})
                patch["csv_plot_json_data"] = plot_json
            except json.JSONDecodeError as je:
                msg = f"Failed to decode Plotly JSON from agent: {je}. Raw output was: '{raw_json_output[:500]}...'"
                logger.error(f"TraceID: {trace_id} - {msg}")
                span.end(level="ERROR", status_message=msg,
                         metadata={"temp_file_used": temp_csv_path, "raw_output": raw_json_output})
                patch["error_message"] = (state.get("error_message") or "") + " " + msg
                patch["csv_plot_json_data"] = {"error": msg, "raw_output": raw_json_output}
            except ValueError as ve:
                msg = f"Invalid Plotly JSON structure from agent: {ve}. Raw output was: '{raw_json_output[:500]}...'"
                logger.error(f"TraceID: {trace_id} - {msg}")
                span.end(level="ERROR", status_message=msg,
                         metadata={"temp_file_used": temp_csv_path, "raw_output": raw_json_output})
                patch["error_message"] = (state.get("error_message") or "") + " " + msg
                patch["csv_plot_json_data"] = {"error": msg, "raw_output": raw_json_output}
            return patch
        except Exception as e:
            msg = f"Error generating CSV plot JSON: {e}"
            logger.error(f"TraceID: {trace_id} - {msg}", exc_info=True)
            span.end(level="ERROR", status_message=msg, metadata={"temp_file_used": temp_csv_path})
            patch["error_message"] = (state.get("error_message") or "") + " " + msg
            patch["csv_plot_json_data"] = {"error": f"Plot generation failed: {e}"}
            return patch

    async def _csv_prepare_response_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _csv_prepare_response_node")

        final_answer_parts = []
        if state.get("csv_text_insight") and "Error generating text insight:" not in state[
            "csv_text_insight"]:  # Check for error string
            final_answer_parts.append(state["csv_text_insight"])
        elif state.get("csv_text_insight"):  # Contains error
            final_answer_parts.append(
                f"(Could not generate text insight for '{state.get('csv_file_name')}' due to an error.)")

        plot_data = state.get("csv_plot_json_data")
        is_plot_valid = plot_data and not plot_data.get("error")

        if is_plot_valid:
            final_answer_parts.append(
                f"(A plot related to your query on '{state.get('csv_file_name', 'the CSV')}' has been generated and should be visible.)")
        elif plot_data and plot_data.get("error"):
            final_answer_parts.append(
                f"(Could not generate a plot for '{state.get('csv_file_name', 'the CSV')}' due to an error: {plot_data.get('error')})")

        final_answer_str = " ".join(final_answer_parts).strip()
        if not final_answer_str and not state.get("error_message"):
            # Check if any task was actually performed
            csv_class_type = state.get("csv_classification_result", {}).get("type")
            if csv_class_type == "TEXT_INSIGHT" and not state.get("csv_text_insight"):
                final_answer_str = f"I attempted to get insights from {state.get('csv_file_name', 'the CSV')}, but no specific information was returned."
            elif csv_class_type == "PLOT" and not is_plot_valid:
                final_answer_str = f"I attempted to generate a plot for {state.get('csv_file_name', 'the CSV')}, but it was not successful."
            elif csv_class_type == "BOTH" and not state.get("csv_text_insight") and not is_plot_valid:
                final_answer_str = f"I attempted to get insights and a plot for {state.get('csv_file_name', 'the CSV')}, but neither was successful."
            else:  # General case if no parts but no obvious error yet.
                final_answer_str = f"I've processed your query regarding {state.get('csv_file_name', 'the CSV')}."

        elif not final_answer_str and state.get("error_message"):
            final_answer_str = f"Sorry, I encountered an issue processing your query for {state.get('csv_file_name', 'the CSV')}. Error: {state['error_message']}"

        # If error_message exists, it should take precedence or be appended.
        if state.get("error_message") and state["error_message"] not in final_answer_str:
            final_answer_str = f"{final_answer_str} (Note: {state['error_message']})".strip()

        if not final_answer_str:  # Ultimate fallback
            final_answer_str = "I have received your query for the CSV file."

        ai_message_meta = {
            "langfuse_trace_id": trace_id,
            "llm_provider": state.get("csv_agent_llm_provider") or self._get_csv_agent_llm().model_name,
            "llm_model": self._get_csv_agent_llm().model_name,
            "context_type_used": ContextType.CSV_DATA_INSIGHTS.value,
            "retrieved_all_doc_ids": [state["csv_document_id"]] if state.get("csv_document_id") else [],
            "csv_document_id": state.get("csv_document_id"),
            "csv_file_name": state.get("csv_file_name"),
            "is_plot_available": is_plot_valid,
            "csv_classification_result_type": state.get("csv_classification_result", {}).get("type")
        }
        if state.get("error_message"):
            ai_message_meta["error"] = state["error_message"]

        return {
            "final_answer": final_answer_str,
            "context_type_used": ContextType.CSV_DATA_INSIGHTS,  # Explicitly set for this path
            "llm_used_provider": ai_message_meta["llm_provider"],
            "all_retrieved_doc_ids": ai_message_meta["retrieved_all_doc_ids"],
            "citations": [],  # No RAG citations for CSV path
            "retrieved_page_ids_for_augmentation": None,
            "ai_message_metadata": ai_message_meta,
            "csv_plot_json_data": plot_data if is_plot_valid else None,  # Pass only valid plot data
        }

    async def _retrieve_focused_docs_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _retrieve_focused_docs_node (RAG Path)")

        pyuuid_selected_document_ids = [PyUUID(doc_id) for doc_id in
                                        state["selected_uploaded_document_ids"]] if state.get(
            "selected_uploaded_document_ids") else []

        retrieval_orchestration_span = state["langfuse_trace_obj"].span(
            name="context-retrieval-orchestration",
            input={"strategy": "focused_documents_rag"}
        )
        primary_results: List[Dict[str, Any]] = []
        error_msg: Optional[str] = None
        context_type = ContextType.USER_SELECTED_UPLOADED_DOCUMENTS  # Default RAG context

        try:
            primary_results = await self._perform_retrieval_for_focused_documents(
                retrieval_orchestration_span, state["tenant_id"], state["query"],
                state["chat_conversation_id"], pyuuid_selected_document_ids
            )
            if not primary_results:
                context_type = ContextType.NO_CONTEXT_USED
                logger.info(f"TraceID: {trace_id} - RAG: No relevant chunks from selected documents after filtering.")
            retrieval_orchestration_span.end(output={
                "final_context_type_selected": context_type.value,
                "primary_results_count": len(primary_results)
            })
        except (ValueError, VectorStoreOperationError, VectorStoreTenantNotFoundError) as retrieval_err:
            error_msg = f"RAG: Knowledge base access or input issue during retrieval: {retrieval_err}"
            logger.error(f"TraceID: {trace_id} - {error_msg}", exc_info=False)
            retrieval_orchestration_span.end(level="ERROR", status_message=str(retrieval_err),
                                             output={"error": str(retrieval_err)})
            context_type = ContextType.NO_CONTEXT_USED
        except Exception as e:
            error_msg = f"RAG: Unexpected error during focused document retrieval: {e}"
            logger.error(f"TraceID: {trace_id} - {error_msg}", exc_info=True)
            retrieval_orchestration_span.end(level="ERROR", status_message=str(e), output={"error": str(e)})
            context_type = ContextType.NO_CONTEXT_USED

        return {
            "primary_search_results_filtered": primary_results,
            "augmentation_search_results_filtered": None,
            "context_type_used": context_type,  # RAG context type
            "error_message": state.get("error_message") or error_msg
        }

    async def _retrieve_scoped_knowledge_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _retrieve_scoped_knowledge_node (RAG Path)")
        retrieval_orchestration_span = state["langfuse_trace_obj"].span(
            name="context-retrieval-orchestration",
            input={"strategy": f"scoped_knowledge_rag: {state['knowledge_scope'].value}"}
        )
        primary_results: List[Dict[str, Any]] = []
        aug_results: Optional[List[Dict[str, Any]]] = None
        error_msg: Optional[str] = None
        context_type = ContextType.NO_CONTEXT_USED  # Default RAG context

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
            error_msg = f"RAG: Knowledge base access or input issue during retrieval: {retrieval_err}"
            logger.error(f"TraceID: {trace_id} - {error_msg}", exc_info=False)
            retrieval_orchestration_span.end(level="ERROR", status_message=str(retrieval_err),
                                             output={"error": str(retrieval_err)})
            context_type = ContextType.NO_CONTEXT_USED
        except Exception as e:
            error_msg = f"RAG: Unexpected error during scoped knowledge retrieval: {e}"
            logger.error(f"TraceID: {trace_id} - {error_msg}", exc_info=True)
            retrieval_orchestration_span.end(level="ERROR", status_message=str(e), output={"error": str(e)})
            context_type = ContextType.NO_CONTEXT_USED

        return {
            "primary_search_results_filtered": primary_results,
            "augmentation_search_results_filtered": aug_results,
            "context_type_used": context_type,  # RAG context type
            "error_message": state.get("error_message") or error_msg
        }

    async def _format_context_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _format_context_node (RAG Path)")

        if state.get("error_message") and "Knowledge base access or input issue during retrieval" in state[
            "error_message"]:
            logger.warning(
                f"TraceID: {trace_id} - RAG: Skipping context formatting due to prior retrieval error: {state['error_message']}")
            return {
                "retrieved_context_str": "Error during RAG context retrieval.",
                "citations": [], "all_retrieved_doc_ids": [], "retrieved_page_ids_for_augmentation": None,
                "context_type_used": ContextType.NO_CONTEXT_USED  # Ensure this for RAG error
            }

        primary_results = state.get("primary_search_results_filtered", [])
        aug_results = state.get("augmentation_search_results_filtered")
        rag_context_type = state.get("context_type_used", ContextType.NO_CONTEXT_USED)

        final_rag_context_type = rag_context_type
        if not primary_results and not (aug_results and len(aug_results) > 0):
            final_rag_context_type = ContextType.NO_CONTEXT_USED
            logger.info(
                f"TraceID: {trace_id} - RAG: No relevant primary or augmentation chunks. Context type set to NO_CONTEXT_USED.")

        all_doc_ids: List[str] = []
        aug_page_ids: Optional[List[str]] = None
        context_str = "No relevant context was found for RAG."
        citations_list: List[Dict[str, Any]] = []

        if final_rag_context_type != ContextType.NO_CONTEXT_USED:
            all_effective_chunks = primary_results + (aug_results if aug_results else [])
            if all_effective_chunks:
                context_str, citations_list = await self._format_context(
                    primary_results, final_rag_context_type, aug_results, state["langfuse_trace_obj"]
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
                final_rag_context_type = ContextType.NO_CONTEXT_USED
                context_str = "No relevant context was found or used for RAG."
                citations_list = []

        state["langfuse_trace_obj"].event(
            name="rag-final-context-for-llm-check",
            output={
                "context_type": final_rag_context_type.value,
                "primary_chunks_count": len(primary_results),
                "augmentation_chunks_count": len(aug_results or []),
                "context_str_preview": context_str[:500] + "...",
                "citations_prepared_count": len(citations_list),
            }
        )
        return {
            "retrieved_context_str": context_str, "citations": citations_list,
            "all_retrieved_doc_ids": all_doc_ids, "retrieved_page_ids_for_augmentation": aug_page_ids,
            "context_type_used": final_rag_context_type  # RAG context type
        }

    async def _generate_llm_response_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _generate_llm_response_node (RAG Path)")

        query = state["query"]
        context_str = state["retrieved_context_str"]  # RAG context string
        rag_context_type = state["context_type_used"]  # RAG context type
        llm_client = state["llm_client"]

        final_answer = "Sorry, I encountered an issue and couldn't generate a RAG response."
        llm_provider: Optional[str] = None
        current_error_message = state.get("error_message")

        # Determine if context is effectively available for RAG
        is_context_effectively_available = (
                context_str and
                "Error during RAG context retrieval." not in context_str and
                "No relevant context was found for RAG." not in context_str and
                "No relevant context was found or used for RAG." not in context_str and
                rag_context_type != ContextType.NO_CONTEXT_USED
        )

        # Scenario 1: Default scope (workspace-aware or tenant-wide) with effectively available context
        is_default_scope_with_context_scenario = (
                is_context_effectively_available and
                rag_context_type in [
                    ContextType.SCOPED_DEFAULT_KNOWLEDGE_WORKSPACE_AWARE,
                    ContextType.SCOPED_DEFAULT_KNOWLEDGE_TENANT_WIDE
                ]
        )

        # Scenario 2: Other RAG scopes (focused docs, page, workspace) with effectively available context
        is_strict_rag_scope_with_context_scenario = (
                is_context_effectively_available and
                rag_context_type not in [  # All other RAG types that ARE NOT default scopes
                    ContextType.SCOPED_DEFAULT_KNOWLEDGE_WORKSPACE_AWARE,
                    ContextType.SCOPED_DEFAULT_KNOWLEDGE_TENANT_WIDE
                    # NO_CONTEXT_USED is already handled by is_context_effectively_available being false
                    # CSV_DATA_INSIGHTS is handled by a different graph path
                ]
        )

        if is_default_scope_with_context_scenario:
            system_prompt_key = "with_context_default_scope_rag"
            system_prompt = (
                "You are a helpful AI assistant. Context from the knowledge base or workspace may be provided below, labeled with sources like '[1]', '[2]', etc. "
                "First, try to answer the user's question using this provided context if it is relevant. If you use information from a source, you **MUST** cite it using its label (e.g., '[1]'). "
                "If the provided context is not relevant, not sufficient, or if no context is provided for the question, answer using your general knowledge. "
                "When using only general knowledge, do not invent citations or refer to specific documents you haven't been shown. "
                "If the question is highly specific and requires information not in the context or your general knowledge, clearly state that you cannot provide a specific answer. "
                "Be concise and accurate."
            )
            user_prompt = f"Context (if relevant for the question):\n{context_str}\n\nQuestion: {query}\n\nAnswer:"
            # Citations are kept as LLM might use the context. If it uses general knowledge, it's instructed not to cite.

        elif is_strict_rag_scope_with_context_scenario:
            system_prompt_key = "with_context_strict_rag"
            system_prompt = (
                "You are a helpful AI assistant. Answer the user's question based *strictly* on the provided context below. "
                "The context consists of several numbered sources, labeled like '[1]', '[2]', etc., each potentially indicating its Type (e.g., focused_document, knowledge_base_page). "
                "When you use information from one or more of these sources in your answer, you **MUST** cite the source(s) immediately after the information, using the exact source label (e.g., '[1]', '[2]'). For example: 'Information X comes from the first source [1]. Information Y is detailed in the second source [2].' "
                "If a single sentence synthesizes information from multiple sources, cite all relevant sources at the end of the sentence, like: 'This concept combines ideas from several places [1] [2].' "
                "Cite every piece of information you use from the context. Do not add citations for information not present in the context. "
                "If the context does not contain the information needed to answer the question, clearly state that you cannot answer based on the provided information and do **not** invent an answer or citations. "
                "Do not use any external knowledge. Be concise and accurate."
            )
            user_prompt = f"Context:\n{context_str}\n\nQuestion: {query}\n\nAnswer:"

        else:  # No effective context available (includes ContextType.NO_CONTEXT_USED or errors in context retrieval)
            system_prompt_key = "no_context_rag"
            system_prompt = (
                "You are a helpful AI assistant. No specific context was found from the knowledge base "
                "that meets the relevance criteria for the user's query, or no specific documents were provided. "
                "Try to answer generally if the question allows for it using your internal knowledge. "
                "If the question seems to require specific information you likely don't have access to (e.g., details about specific user documents or pages you weren't given context for), "
                "state clearly that you lack the specific information needed to provide a detailed answer. Do not invent information or documents."
            )
            user_prompt = query
            # Ensure citations are empty if no context was effectively used or should be ignored by general knowledge path
            if system_prompt_key == "no_context_rag" or (is_default_scope_with_context_scenario and not state.get(
                    "citations")):  # Clear if no context or default scenario decided not to use context (harder to detect this part accurately)
                state["citations"] = []

        llm_input_for_trace = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        generation_metadata = {
            "actual_llm_provider": llm_client.provider_name,
            "actual_llm_model": llm_client.get_model_name(),
            "final_context_type_used_for_prompt_logic": rag_context_type.value,  # The type that led to this prompt path
            "is_context_effectively_available_flag": is_context_effectively_available,
            "retrieved_total_doc_ids_count": len(state.get("all_retrieved_doc_ids", [])),
            "system_prompt_template_key": system_prompt_key,
            "context_string_length": len(context_str) if is_context_effectively_available else 0
        }

        generation_span = state["langfuse_trace_obj"].generation(
            name="rag-llm-generation", model=llm_client.get_model_name(),
            input=llm_input_for_trace, metadata=generation_metadata
        )

        try:
            logger.info(
                f"TraceID: {trace_id} - RAG LLM generation (Context: {rag_context_type.value}, Effective Context Available: {is_context_effectively_available}). System Prompt Key: '{system_prompt_key}'")
            final_answer = await llm_client.generate(prompt=user_prompt, system_prompt=system_prompt)
            generation_span.end(output=final_answer)
            llm_provider = llm_client.provider_name
            logger.info(f"TraceID: {trace_id} - Successfully generated RAG LLM response.")
        except LLMGenerationError as e:
            logger.error(f"TraceID: {trace_id} - RAG LLM generation failed: {e}", exc_info=True)
            generation_span.end(level="ERROR", status_message=str(e), output={"error": str(e)})
            current_error_message = f"LLM service unavailable for RAG: {e}"
            final_answer = "I apologize, but I'm currently unable to generate a RAG response due to a problem with the AI service."
        except Exception as e:
            logger.error(f"TraceID: {trace_id} - Unexpected error during RAG LLM call: {e}", exc_info=True)
            generation_span.end(level="ERROR", status_message=f"Unexpected RAG generation error: {e}",
                                output={"error": str(e)})
            current_error_message = f"An unexpected error occurred during RAG AI response generation: {e}"
            final_answer = "I apologize, but an unexpected error occurred while trying to generate a RAG response."

        ai_message_meta = {
            "langfuse_trace_id": state["trace_id"],
            "llm_provider": llm_provider,
            "llm_model": llm_client.get_model_name(),
            "context_type_used": rag_context_type.value,  # This is the original RAG context type
            "retrieved_all_doc_ids": state.get("all_retrieved_doc_ids", []),
            "retrieved_page_ids_for_augmentation": state.get("retrieved_page_ids_for_augmentation"),
            "potential_citations_data": state.get("citations", []),  # Use potentially updated citations
            "retrieved_total_doc_count": len(state.get("all_retrieved_doc_ids", []))
        }
        if current_error_message:
            ai_message_meta["error"] = current_error_message

        return {
            "final_answer": final_answer,
            "llm_used_provider": llm_provider,
            "error_message": current_error_message,
            "ai_message_metadata": ai_message_meta
        }

    async def _save_ai_message_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _save_ai_message_node (Common)")

        ai_message_meta = state.get("ai_message_metadata")
        if not ai_message_meta:
            logger.error(f"TraceID: {trace_id} - AI message metadata missing in _save_ai_message_node.")
            ai_message_meta = {"error": "Internal: AI metadata missing for save", "langfuse_trace_id": trace_id}

        logger.debug(f"TraceID: {trace_id} - AI message metadata for save: {ai_message_meta}")

        final_answer_to_save = state.get("final_answer", "Error: No final answer in state for saving message.")
        # If error_message is set and final_answer is the default init one, prefer error_message.
        if state.get("error_message") and final_answer_to_save == "Sorry, an initialization error occurred.":
            final_answer_to_save = state.get("error_message", "An unspecified error occurred.")

        await self._save_chat_message(
            conversation_id=state["chat_conversation_id"],
            sender_type=SenderType.AI,
            content=final_answer_to_save,
            metadata=ai_message_meta,
            trace_span=state["langfuse_trace_obj"]
        )
        return {}

    async def _prepare_error_response_node(self, state: GraphState) -> Dict[str, Any]:
        trace_id = state['trace_id']
        logger.info(f"TraceID: {trace_id} - Node: _prepare_error_response_node (Common)")
        error_message = state.get("error_message", "An unspecified error occurred.")

        final_answer = "Sorry, I encountered an issue and couldn't generate a response."
        if "Invalid input provided" in error_message:
            final_answer = f"There was an issue with the input: {error_message.split(': ', 1)[-1]}"
        elif "LLM service unavailable" in error_message:
            final_answer = "I apologize, but I'm currently unable to generate a response due to a problem with the AI service."
        elif "Knowledge base access or input issue" in error_message:  # This is for RAG
            final_answer = "I'm having trouble accessing the necessary information for RAG. Please try again later."
        elif "CSV" in error_message and (
                "fetch" in error_message or "load" in error_message or "parse" in error_message):
            final_answer = f"I'm having trouble processing the CSV file: {error_message.split(': ', 1)[-1]}"
        elif "classification failed" in error_message:
            final_answer = f"I couldn't understand how to process your query for the CSV: {error_message.split(': ', 1)[-1]}"

        # General fallback if final_answer is still the default and error_message is specific
        if final_answer == "Sorry, I encountered an issue and couldn't generate a response." and error_message != "An unspecified error occurred.":
            final_answer = error_message

        ai_message_meta = state.get("ai_message_metadata", {})  # Preserve if already set
        ai_message_meta.update({
            "langfuse_trace_id": trace_id,
            "error": error_message,
            "context_type_used": ContextType.NO_CONTEXT_USED.value,
            "llm_provider": state.get("llm_used_provider") or state.get(
                "csv_agent_llm_provider") or self.llm.provider_name,
            "llm_model": state.get("csv_agent_llm_provider") or self.llm.get_model_name(),  # Best guess for model
        })

        return {
            "final_answer": final_answer,
            "error_message": error_message,  # This is for the API response error field
            "llm_used_provider": ai_message_meta.get("llm_provider"),
            "context_type_used": ContextType.NO_CONTEXT_USED,
            "citations": [],
            "all_retrieved_doc_ids": [],
            "retrieved_page_ids_for_augmentation": None,
            "csv_plot_json_data": None,  # Clear plot data on error
            "ai_message_metadata": ai_message_meta  # For _save_ai_message_node if it's called after this
        }

    def _format_chunk_for_trace(self, chunk_item: Dict[str, Any]) -> Dict[str, Any]:
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

    def _filter_results_by_relevance(self, results: List[Dict[str, Any]], trace_span: Optional[Any] = None,
                                     trace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not results: return []
        log_trace_id = trace_id or (getattr(trace_span, 'id', 'N/A') if trace_span else 'N/A')
        original_count = len(results)
        filtered_results: List[Dict[str, Any]] = []
        filtered_out_details: List[Dict[str, Any]] = []

        for res_item in results:
            passes_threshold = False
            score_type_used = "none"
            score_value = None
            if res_item.get("distance") is not None:
                score_type_used = "distance"
                score_value = res_item["distance"]
                if score_value <= MAX_DISTANCE_THRESHOLD: passes_threshold = True
            elif res_item.get("certainty") is not None:
                score_type_used = "certainty"
                score_value = res_item["certainty"]
                if score_value >= MIN_CERTAINTY_THRESHOLD: passes_threshold = True
            elif res_item.get("score") is not None:  # Hybrid score
                score_type_used = "hybrid_score"
                score_value = res_item["score"]
                if score_value >= MIN_HYBRID_SCORE_THRESHOLD: passes_threshold = True  # Check if this threshold is correct
            else:  # No score, pass through (or define behavior)
                passes_threshold = True
                score_type_used = "no_score_present"

            logger.info(f"score_type_used: {score_type_used}, score_value: {score_value}")

            if passes_threshold:
                filtered_results.append(res_item)
            else:
                props = res_item.get("properties", {})
                doc_id_prop = props.get("documentId", "Unknown_ID")
                logger.debug(
                    f"TraceID: {log_trace_id} - Filtering out chunk for doc_id: {doc_id_prop} due to relevance {score_type_used}: {score_value}")
                filtered_out_details.append(self._format_chunk_for_trace(res_item))

        filtered_count = len(filtered_results)
        if trace_span and hasattr(trace_span, 'event') and callable(getattr(trace_span, 'event', None)):
            trace_span.event(
                name="relevance-filtering",
                input={"original_count": original_count},
                output={"filtered_count": filtered_count, "filtered_out_details_count": len(filtered_out_details)},
                level="DEBUG" if original_count == filtered_count else "DEFAULT"
            )
        logger.info(f"TraceID: {log_trace_id} - Relevance filtering: {original_count} -> {filtered_count} chunks.")
        return filtered_results

    async def _perform_retrieval_for_focused_documents(
            self, trace_span: Any, tenant_id: str, query: str,
            chat_conversation_id: str, selected_document_uuids: List[PyUUID]
    ) -> List[Dict[str, Any]]:
        log_trace_id = getattr(trace_span, 'id', 'N/A')
        retrieval_span_name = "weaviate-retrieval-focused-docs"
        raw_limit = RAG_RETRIEVAL_LIMIT_FOCUSED_DOCS * 2

        current_sub_span = trace_span.span(
            name=retrieval_span_name,
            input={
                "query": query, "tenant_id": tenant_id,
                "intended_limit": RAG_RETRIEVAL_LIMIT_FOCUSED_DOCS, "raw_retrieval_limit": raw_limit,
                "chat_conversation_id": chat_conversation_id,
                "selected_document_ids_count": len(selected_document_uuids),
                "selected_document_ids_str": [str(uid) for uid in selected_document_uuids]
            },
            metadata={
                "collection": self.document_vector_service.COLLECTION_NAME,
                "filter_by": "selected_document_ids_and_chatSessionId",
                "retrieval_strategy": "focused_documents_rag"
            }
        )
        search_results_filtered: List[Dict[str, Any]] = []
        try:
            search_results_raw = await self.document_vector_service.search(
                tenant_id=tenant_id, query=query, limit=raw_limit,
                doc_ids=selected_document_uuids, chat_session_id=str(chat_conversation_id),
                use_hybrid=True, alpha=0.5
            )
            search_results_filtered = self._filter_results_by_relevance(search_results_raw, current_sub_span,
                                                                        log_trace_id)
            current_sub_span.end(output={
                "retrieved_raw_count": len(search_results_raw),
                "retrieved_filtered_count": len(search_results_filtered),
                # "raw_chunks_preview": [self._format_chunk_for_trace(item) for item in search_results_raw[:3]],
            })
            logger.info(
                f"TraceID: {log_trace_id} - Focused Document RAG retrieval: {len(search_results_raw)} raw, {len(search_results_filtered)} filtered.")
            return search_results_filtered
        except ValueError as ve:
            msg = f"Invalid UUID format or input for focused RAG retrieval. Error: {ve}"
            logger.error(f"TraceID: {log_trace_id} - {msg}", exc_info=False)
            current_sub_span.end(level="ERROR", status_message=msg, output={"error": msg})
            raise ValueError(msg) from ve
        except (VectorStoreOperationError, VectorStoreTenantNotFoundError) as e:
            log_message = f"TraceID: {log_trace_id} - Weaviate RAG search failed (focused): {e}."
            logger.error(log_message, exc_info=True)
            current_sub_span.end(level="ERROR", status_message=str(e), output={"error": str(e)})
            raise
        except Exception as e:
            logger.error(f"TraceID: {log_trace_id} - Unexpected error during focused RAG retrieval: {e}", exc_info=True)
            current_sub_span.end(level="ERROR", status_message=f"Unexpected retrieval error: {e}",
                                 output={"error": str(e)})
            raise

    async def _perform_retrieval_for_knowledge_scope(
            self, trace_span: Any, tenant_id: str, query: str,
            knowledge_scope: ChatKnowledgeScope, knowledge_scope_id: Optional[str] = None,
            workspace_id_for_augmentation: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], ContextType, Optional[List[Dict[str, Any]]]]:
        log_trace_id = getattr(trace_span, 'id', 'N/A')
        retrieval_span_name = f"weaviate-retrieval-scope-rag-{knowledge_scope.value}"

        primary_results_raw: List[Dict[str, Any]] = []
        primary_results_filtered: List[Dict[str, Any]] = []
        augmentation_results_filtered: Optional[List[Dict[str, Any]]] = None  # Already filtered
        context_type = ContextType.NO_CONTEXT_USED  # Default for RAG

        actual_workspace_id_str: Optional[str] = workspace_id_for_augmentation
        if knowledge_scope == ChatKnowledgeScope.WORKSPACE and knowledge_scope_id and not workspace_id_for_augmentation:
            actual_workspace_id_str = knowledge_scope_id
        elif knowledge_scope == ChatKnowledgeScope.DEFAULT and knowledge_scope_id and not workspace_id_for_augmentation:
            actual_workspace_id_str = knowledge_scope_id

        current_sub_span = trace_span.span(name=retrieval_span_name, input={
            "query": query, "tenant_id": tenant_id, "knowledge_scope": knowledge_scope.value,
            "knowledge_scope_id": knowledge_scope_id, "workspace_id_for_augmentation": workspace_id_for_augmentation,
            "effective_workspace_id": actual_workspace_id_str
        })
        try:
            if knowledge_scope == ChatKnowledgeScope.PAGE and knowledge_scope_id and workspace_id_for_augmentation:
                context_type = ContextType.SCOPED_PAGE_WITH_WORKSPACE_AUGMENTATION
                page_uuid = PyUUID(knowledge_scope_id)
                workspace_uuid_aug = PyUUID(workspace_id_for_augmentation)
                primary_raw_limit = RAG_RETRIEVAL_LIMIT_PAGE_PRIMARY * 2

                primary_ret_sub_span = current_sub_span.span(name="primary-page-rag-retrieval",
                                                             input={"doc_id": knowledge_scope_id})
                try:
                    primary_results_raw = await self.page_vector_service.search(
                        tenant_id=tenant_id, query=query, limit=primary_raw_limit,
                        doc_id=page_uuid, use_hybrid=True, alpha=0.5
                    )
                    primary_results_filtered = self._filter_results_by_relevance(primary_results_raw,
                                                                                 primary_ret_sub_span, log_trace_id)
                    primary_ret_sub_span.end(output={"retrieved_raw_count": len(primary_results_raw),
                                                     "filtered_count": len(primary_results_filtered)})
                except Exception as e_prim:
                    primary_ret_sub_span.end(level="ERROR", status_message=str(e_prim))
                    logger.error(f"TraceID: {log_trace_id} - Primary page RAG retrieval failed: {e_prim}",
                                 exc_info=True)

                aug_needed = RAG_RETRIEVAL_LIMIT_PAGE_AUGMENT
                aug_raw_limit = (aug_needed + len(primary_results_filtered) + 1) * 2  # Heuristic for enough raw results
                aug_ret_sub_span = current_sub_span.span(name="augmentation-workspace-rag-retrieval",
                                                         input={"workspace_id": workspace_id_for_augmentation})
                try:
                    all_workspace_pages_raw = await self.page_vector_service.search(
                        tenant_id=tenant_id, query=query, limit=aug_raw_limit,
                        workspace_id=workspace_uuid_aug, use_hybrid=True, alpha=0.5
                    )
                    all_workspace_pages_relevance_filtered = self._filter_results_by_relevance(all_workspace_pages_raw,
                                                                                               aug_ret_sub_span,
                                                                                               log_trace_id)

                    primary_result_uuids = {res.get("uuid") for res in primary_results_filtered if res.get("uuid")}
                    focused_page_doc_id = str(page_uuid)  # documentId of the primary page
                    temp_augmentation_results = []
                    added_fingerprints = set()  # For deduplication of augmentation chunks

                    for res in all_workspace_pages_relevance_filtered:
                        chunk_uuid = res.get("uuid");
                        props = res.get("properties", {});
                        doc_id = props.get("documentId")
                        chunk_fingerprint = props.get("chunkFingerprint")
                        if chunk_uuid and chunk_uuid in primary_result_uuids: continue  # Skip if already in primary results
                        if doc_id and doc_id == focused_page_doc_id: continue  # Skip if from the same primary page document
                        if chunk_fingerprint and chunk_fingerprint in added_fingerprints: continue  # Skip if already added via fingerprint

                        temp_augmentation_results.append(res)
                        if chunk_fingerprint: added_fingerprints.add(chunk_fingerprint)
                        if len(temp_augmentation_results) >= aug_needed: break
                    augmentation_results_filtered = temp_augmentation_results
                    aug_ret_sub_span.end(output={"retrieved_raw_count": len(all_workspace_pages_raw),
                                                 "final_aug_count": len(augmentation_results_filtered or [])})
                except Exception as e_aug:
                    aug_ret_sub_span.end(level="ERROR", status_message=str(e_aug))
                    logger.error(f"TraceID: {log_trace_id} - Augmentation RAG retrieval failed: {e_aug}", exc_info=True)
                    augmentation_results_filtered = None

            elif knowledge_scope == ChatKnowledgeScope.WORKSPACE and actual_workspace_id_str:
                context_type = ContextType.SCOPED_WORKSPACE_CONTENT
                raw_limit = RAG_RETRIEVAL_LIMIT_WORKSPACE * 2
                workspace_uuid = PyUUID(actual_workspace_id_str)
                primary_results_raw = await self.page_vector_service.search(
                    tenant_id=tenant_id, query=query, limit=raw_limit,
                    workspace_id=workspace_uuid, use_hybrid=True, alpha=0.6
                )
                primary_results_filtered = self._filter_results_by_relevance(primary_results_raw, current_sub_span,
                                                                             log_trace_id)

            elif knowledge_scope == ChatKnowledgeScope.DEFAULT:
                raw_limit = RAG_RETRIEVAL_LIMIT_DEFAULT * 2
                if actual_workspace_id_str:
                    context_type = ContextType.SCOPED_DEFAULT_KNOWLEDGE_WORKSPACE_AWARE
                    workspace_uuid = PyUUID(actual_workspace_id_str)
                    primary_results_raw = await self.page_vector_service.search(
                        tenant_id=tenant_id, query=query, limit=raw_limit,
                        workspace_id=workspace_uuid, use_hybrid=False, alpha=0.5
                    )
                else:
                    context_type = ContextType.SCOPED_DEFAULT_KNOWLEDGE_TENANT_WIDE
                    primary_results_raw = await self.page_vector_service.search(
                        tenant_id=tenant_id, query=query, limit=raw_limit, use_hybrid=False, alpha=0.5
                    )
                primary_results_filtered = self._filter_results_by_relevance(primary_results_raw, current_sub_span,
                                                                             log_trace_id)

            elif knowledge_scope == ChatKnowledgeScope.TEMPLATE:
                context_type = ContextType.SCOPED_TEMPLATE_CONTENT  # Or specific if implemented
                logger.warning(
                    f"TraceID: {log_trace_id} - TEMPLATE scope RAG is not fully implemented, skipping retrieval.")
                primary_results_filtered = []
                primary_results_raw = []
                augmentation_results_filtered = None

            current_sub_span.end(output={
                "retrieved_primary_raw_count": len(primary_results_raw),
                "retrieved_primary_filtered_count": len(primary_results_filtered),
                "retrieved_augmentation_final_count": len(augmentation_results_filtered or []),
                "final_context_type_determined": context_type.value,
            })

            if not primary_results_filtered and not (
                    augmentation_results_filtered and len(augmentation_results_filtered) > 0):
                if context_type != ContextType.NO_CONTEXT_USED:
                    context_type = ContextType.NO_CONTEXT_USED
                    logger.info(
                        f"TraceID: {log_trace_id} - RAG: No relevant chunks for '{knowledge_scope.value}', falling back to NO_CONTEXT_USED.")
            return primary_results_filtered, context_type, augmentation_results_filtered

        except ValueError as ve:  # For PyUUID conversion
            msg = f"Invalid UUID format for RAG scope. Scope: {knowledge_scope.value}, ScopeID: {knowledge_scope_id}, WsID: {workspace_id_for_augmentation}. Error: {ve}"
            logger.error(f"TraceID: {log_trace_id} - {msg}", exc_info=False)
            current_sub_span.end(level="ERROR", status_message=msg, output={"error": msg})
            raise ValueError(msg) from ve
        except (VectorStoreOperationError, VectorStoreTenantNotFoundError) as e:
            log_message = f"TraceID: {log_trace_id} - Weaviate RAG search failed (scope: '{knowledge_scope.value}'): {e}."
            logger.error(log_message, exc_info=True)
            current_sub_span.end(level="ERROR", status_message=str(e), output={"error": str(e)})
            raise
        except Exception as e:
            logger.error(
                f"TraceID: {log_trace_id} - Unexpected error during RAG knowledge scope retrieval for '{knowledge_scope.value}': {e}",
                exc_info=True)
            current_sub_span.end(level="ERROR", status_message=f"Unexpected RAG retrieval error: {e}",
                                 output={"error": str(e)})
            raise

    async def _format_context(
            self, primary_results: List[Dict[str, Any]], context_type: ContextType,
            augmentation_results: Optional[List[Dict[str, Any]]] = None, trace_span: Optional[Any] = None
    ) -> Tuple[str, List[Dict[str, Any]]]:
        all_effective_results_with_scope: List[Tuple[Dict[str, Any], CitationScopeType]] = []
        added_fingerprints = set()  # Deduplicate chunks by fingerprint
        citations_list: List[Dict[str, Any]] = []
        trace_id_str = getattr(trace_span, 'id', 'N/A') if trace_span else 'N/A'
        focused_doc_ids_to_fetch_url: List[str] = []  # UploadedDocument UUIDs (as strings)

        def add_unique_result_with_scope(result_item: Dict[str, Any], scope_type: CitationScopeType):
            props = result_item.get("properties", {})
            chunk_fingerprint = props.get("chunkFingerprint")
            if chunk_fingerprint and chunk_fingerprint in added_fingerprints: return
            all_effective_results_with_scope.append((result_item, scope_type))
            if chunk_fingerprint: added_fingerprints.add(chunk_fingerprint)

            # If it's from USER_SELECTED_UPLOADED_DOCUMENTS (focused), try to get its GCS URL for citation
            if context_type == ContextType.USER_SELECTED_UPLOADED_DOCUMENTS and scope_type == CitationScopeType.FOCUSED_DOCUMENT:
                doc_id = props.get("documentId")  # This should be UploadedDocument.uploaded_document_id
                if doc_id and doc_id not in focused_doc_ids_to_fetch_url:
                    focused_doc_ids_to_fetch_url.append(doc_id)

        primary_scope_type: CitationScopeType
        if context_type == ContextType.USER_SELECTED_UPLOADED_DOCUMENTS:
            primary_scope_type = CitationScopeType.FOCUSED_DOCUMENT
        elif context_type in [ContextType.SCOPED_PAGE_CONTENT, ContextType.SCOPED_PAGE_WITH_WORKSPACE_AUGMENTATION]:
            primary_scope_type = CitationScopeType.KNOWLEDGE_BASE_PAGE
        elif context_type == ContextType.SCOPED_WORKSPACE_CONTENT:
            primary_scope_type = CitationScopeType.KNOWLEDGE_BASE_WORKSPACE
        elif context_type in [ContextType.SCOPED_DEFAULT_KNOWLEDGE_WORKSPACE_AWARE,
                              ContextType.SCOPED_DEFAULT_KNOWLEDGE_TENANT_WIDE]:
            primary_scope_type = CitationScopeType.KNOWLEDGE_BASE_DEFAULT
        else:  # Fallback, e.g. template or unknown
            primary_scope_type = CitationScopeType.KNOWLEDGE_BASE_DEFAULT  # Or handle more specifically

        for res in primary_results: add_unique_result_with_scope(res, primary_scope_type)
        if augmentation_results:  # Augmentation always from general knowledge base
            for aug_res in augmentation_results: add_unique_result_with_scope(aug_res,
                                                                              CitationScopeType.KNOWLEDGE_BASE_AUGMENTATION)

        uploaded_doc_urls: Dict[str, str] = {}  # Maps UploadedDocument.uploaded_document_id (str) to file_path (URL)
        if focused_doc_ids_to_fetch_url:
            url_fetch_sub_span = trace_span.span(name="fetch-uploaded-doc-urls-for-citation", input={
                "doc_ids_count": len(focused_doc_ids_to_fetch_url)}) if trace_span else None
            try:
                # Fetch UploadedDocument.file_path for these document IDs
                stmt = select(UploadedDocument.uploaded_document_id, UploadedDocument.file_path).where(
                    UploadedDocument.uploaded_document_id.in_(
                        [PyUUID(uid_str) for uid_str in focused_doc_ids_to_fetch_url])
                )
                result = await self.db.execute(stmt)
                rows = result.all()
                uploaded_doc_urls = {str(row.uploaded_document_id): row.file_path for row in rows if row.file_path}
                if url_fetch_sub_span: url_fetch_sub_span.end(output={"urls_fetched_count": len(uploaded_doc_urls)})
            except Exception as db_err:
                logger.error(
                    f"TraceID: {trace_id_str} - Failed to fetch uploaded document URLs for citations: {db_err}",
                    exc_info=True)
                if url_fetch_sub_span: url_fetch_sub_span.end(level="ERROR", status_message=str(db_err))

        if not all_effective_results_with_scope:
            empty_message = "No relevant RAG context was found for your query based on the current scope and relevance filtering."
            return empty_message, []

        context_parts = []
        source_counter = 1
        for idx, (res_item, item_scope_type) in enumerate(all_effective_results_with_scope):
            props = res_item.get("properties", {})
            title = props.get("title", "Untitled Content")
            chunk_content = props.get("contentChunk", "")
            doc_id_prop = props.get("documentId", "Unknown ID")
            chunk_order_prop = props.get("chunkOrder", -1)
            current_source_label = f"[{source_counter}]"

            score_info_str = ""
            score_value, score_display_type = None, "none"  # Simplified score display
            if res_item.get("distance") is not None:
                score_value, score_display_type = res_item["distance"], "Dist"
            elif res_item.get("certainty") is not None:
                score_value, score_display_type = res_item["certainty"], "Cert"
            elif res_item.get("score") is not None:
                score_value, score_display_type = res_item["score"], "Score"
            if score_value is not None: score_info_str = f" ({score_display_type}: {score_value:.3f})"

            formatted_source_part = (f"{current_source_label}{score_info_str} "
                                     f"(Type: {item_scope_type.value}, DocID: {doc_id_prop}, Title: \"{title}\"):\n{chunk_content}")  # Removed Chunk Order from context string for brevity
            context_parts.append(formatted_source_part)

            source_url_for_citation = None
            if item_scope_type == CitationScopeType.FOCUSED_DOCUMENT:
                source_url_for_citation = uploaded_doc_urls.get(doc_id_prop)

            citation_obj = Citation(
                source_label=current_source_label, document_id=doc_id_prop, title=title,
                preview=chunk_content,
                # preview=chunk_content[:200] + "..." if len(chunk_content) > 200 else chunk_content,
                scope_type=item_scope_type, source_url=source_url_for_citation
            )
            citations_list.append(citation_obj.model_dump())
            source_counter += 1

        final_context_string = "\n\n---\n\n".join(context_parts)
        if trace_span and hasattr(trace_span, 'event') and callable(getattr(trace_span, 'event', None)):
            trace_span.event(name="rag-context-formatting",
                             output={"effective_chunks_count": len(all_effective_results_with_scope),
                                     "final_context_string_length": len(final_context_string)})
        return final_context_string, citations_list

    async def _save_chat_message(
            self, conversation_id: str, sender_type: SenderType, content: str,
            user_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
            trace_span: Optional[Any] = None
    ):
        trace_id_str = getattr(trace_span, 'id', 'N/A') if trace_span else 'N/A'
        try:
            try:
                conv_uuid = PyUUID(conversation_id)
            except ValueError:
                logger.error(
                    f"TraceID: {trace_id_str} - Invalid conversation_id format: {conversation_id}. Cannot save message.")
                if trace_span and hasattr(trace_span, 'event'): trace_span.event(
                    name="save-message-failed-invalid-conv-id", level="ERROR")
                return

            chat_message = ChatMessage(
                conversation_id=conv_uuid, sender_type=sender_type, message_content=content,
                sender_user_id=user_id if sender_type == SenderType.USER else None, meta_data=metadata or {}
            )
            self.db.add(chat_message)
            stmt = sqlalchemy_update(ChatConversation).where(ChatConversation.conversation_id == conv_uuid).values(
                updated_at=func.now())  # .execution_options(synchronize_session=False) is default in SA 2.0
            await self.db.execute(stmt)
            await self.db.commit()
            await self.db.refresh(chat_message)

            if trace_span and hasattr(trace_span, 'event'):
                trace_span.event(
                    name=f"save-{sender_type.value}-message-db",
                    output={"message_saved": True, "chat_message_id": str(chat_message.message_id)}, level="DEBUG"
                )
        except Exception as e:
            await self.db.rollback()
            logger.error(
                f"TraceID: {trace_id_str} - Failed to save {sender_type.value} message for conv {conversation_id}: {e}",
                exc_info=True)
            if trace_span and hasattr(trace_span, 'event'):
                trace_span.event(name=f"save-{sender_type.value}-message-db-failed", output={"error": str(e)},
                                 level="ERROR")

    async def generate_response(
            self,
            user_id: str, tenant_id: str, query: str, chat_conversation_id: str,
            selected_uploaded_document_ids: Optional[List[str]] = None,
            knowledge_scope: ChatKnowledgeScope = ChatKnowledgeScope.DEFAULT,
            knowledge_scope_id: Optional[str] = None, workspace_id_for_scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        trace_id_val = f"trace-{uuid.uuid4()}"

        log_params = {
            "user_id": user_id, "tenant_id": tenant_id,
            "query_preview": query[:100], "chat_conversation_id": chat_conversation_id,
            "selected_doc_ids_count": len(selected_uploaded_document_ids) if selected_uploaded_document_ids else 0,
            "selected_doc_ids_preview": selected_uploaded_document_ids[:3] if selected_uploaded_document_ids else None,
            "knowledge_scope": knowledge_scope.value, "knowledge_scope_id": knowledge_scope_id,
            "workspace_id_for_scope": workspace_id_for_scope
        }
        logger.info(f"ChatService generate_response invoked with trace_id {trace_id_val}: {log_params}")

        langfuse_trace_obj: Any = self.langfuse.trace(
            id=trace_id_val,
            user_id=str(user_id),
            session_id=chat_conversation_id,
            name="chat-pipeline-langgraph",  # Simplified name
            input=log_params,
            metadata={
                "environment": settings.ENVIRONMENT,
                "llm_model_configured": self.llm.get_model_name(),
                "llm_provider_configured": self.llm.provider_name,
                "tenant_id": tenant_id
            }
        )
        final_trace_id_for_response = getattr(langfuse_trace_obj, 'id', trace_id_val)

        initial_state: GraphState = {
            "user_id": user_id, "tenant_id": tenant_id, "query": query,
            "chat_conversation_id": chat_conversation_id,
            "selected_uploaded_document_ids": selected_uploaded_document_ids,
            "knowledge_scope": knowledge_scope, "knowledge_scope_id": knowledge_scope_id,
            "workspace_id_for_scope": workspace_id_for_scope,
            "langfuse_trace_obj": langfuse_trace_obj,
            "trace_id": final_trace_id_for_response,
            "db_session": self.db, "llm_client": self.llm,
            "page_vector_service": self.page_vector_service,
            "document_vector_service": self.document_vector_service,
            "redis_client": self.redis,

            "error_message": None, "final_answer": "Sorry, an initialization error occurred.",
            "llm_used_provider": None,
            "primary_search_results_filtered": [], "augmentation_search_results_filtered": None,
            "context_type_used": ContextType.NO_CONTEXT_USED,
            "retrieved_context_str": "No context processed.", "citations": [],
            "all_retrieved_doc_ids": [], "retrieved_page_ids_for_augmentation": None,
            "ai_message_metadata": None,

            "is_csv_mode": False, "csv_document_id": None, "csv_file_name": None,
            "csv_content_str": None, "csv_temp_file_path": None,
            "csv_classification_result": None, "csv_text_insight": None,
            "csv_plot_json_data": None, "csv_agent_llm_provider": None,
        }

        final_state: GraphState = initial_state
        try:
            graph_output = await self.graph.ainvoke(initial_state, {"recursion_limit": 25})
            if graph_output:
                final_state = graph_output
            else:
                logger.error(f"TraceID: {final_trace_id_for_response} - LangGraph ainvoke returned None or empty.")
                final_state["error_message"] = (
                            final_state.get("error_message") or "Internal error: Graph execution yielded no state.")
                final_state["final_answer"] = final_state.get(
                    "final_answer") or "An unexpected internal error occurred."
        except ValueError as ve:
            logger.warning(
                f"TraceID: {final_trace_id_for_response} - Invalid input for chat generation (ValueError): {ve}",
                exc_info=False)
            final_state["error_message"] = f"Invalid input provided: {str(ve)}"
            final_state["final_answer"] = final_state.get(
                "final_answer") or f"There was an issue with the input: {str(ve)}"
        except Exception as e:
            logger.error(
                f"TraceID: {final_trace_id_for_response} - Unhandled exception during LangGraph execution: {e}",
                exc_info=True)
            final_state["error_message"] = (
                        final_state.get("error_message") or f"An unexpected server error occurred: {e}")
            final_state["final_answer"] = final_state.get(
                "final_answer") or "An unexpected server error occurred. Please try again later."

        finally:
            if langfuse_trace_obj and hasattr(langfuse_trace_obj, 'update'):
                status_message = final_state.get("error_message") or "Chat generation successful"

                # Determine final context_type for trace output
                final_trace_context_type = final_state.get("context_type_used", ContextType.NO_CONTEXT_USED)
                # If CSV mode was successful and error_message is not set, it should be CSV_DATA_INSIGHTS
                if final_state.get("is_csv_mode") and not final_state.get("error_message"):
                    final_trace_context_type = ContextType.CSV_DATA_INSIGHTS

                is_plot_available_final = bool(final_state.get("is_csv_mode") and \
                                               final_state.get("csv_plot_json_data") and \
                                               not final_state.get("csv_plot_json_data", {}).get("error") and \
                                               not final_state.get("error_message")  # Ensure no overarching error
                                               )

                trace_output_final = {
                    "final_answer_preview": final_state.get("final_answer", "")[:200] if not final_state.get(
                        "error_message") else None,
                    "error_message": final_state.get("error_message"),
                    "final_context_type_used": final_trace_context_type.value,
                    "llm_provider_used": final_state.get("llm_used_provider") or final_state.get(
                        "csv_agent_llm_provider"),
                    "citations_data_count": len(final_state.get("citations", [])),
                    "is_csv_mode_active": final_state.get("is_csv_mode"),
                    "is_plot_available": is_plot_available_final,
                    "csv_doc_id": final_state.get("csv_document_id") if final_state.get("is_csv_mode") else None,
                }
                langfuse_trace_obj.update(
                    output=trace_output_final,
                    level="ERROR" if final_state.get("error_message") else "DEFAULT",
                    status_message=status_message
                )

        # Final response assembly
        response_context_type = final_state.get("context_type_used", ContextType.NO_CONTEXT_USED)
        if final_state.get("is_csv_mode") and not final_state.get("error_message"):
            response_context_type = ContextType.CSV_DATA_INSIGHTS

        retrieved_ids_for_response = final_state.get("all_retrieved_doc_ids", [])
        if final_state.get("is_csv_mode") and final_state.get("csv_document_id") and not final_state.get(
                "error_message"):
            retrieved_ids_for_response = [final_state["csv_document_id"]]

        final_plot_data = None
        is_plot_available_response = False
        if final_state.get("is_csv_mode") and not final_state.get("error_message"):
            plot_data_candidate = final_state.get("csv_plot_json_data")
            if plot_data_candidate and not plot_data_candidate.get("error"):
                final_plot_data = plot_data_candidate
                is_plot_available_response = True

        return {
            "answer": final_state.get("final_answer", "Error processing request."),
            "session_id": chat_conversation_id,
            "trace_id": final_trace_id_for_response,
            "llm_used": final_state.get("llm_used_provider") or final_state.get("csv_agent_llm_provider"),
            "error": final_state.get("error_message"),
            "context_type_used": response_context_type,
            "retrieved_document_ids": list(set(retrieved_ids_for_response)),
            "retrieved_page_ids_for_augmentation": final_state.get(
                "retrieved_page_ids_for_augmentation") if not final_state.get("is_csv_mode") else None,
            "citations": final_state.get("citations", []) if not final_state.get("is_csv_mode") else [],
            "plot_data": final_plot_data,
            "is_plot_available": is_plot_available_response
        }


def get_chat_service(
        llm: BaseLLMClient = Depends(get_primary_llm_client),
        langfuse_client: Langfuse = Depends(get_langfuse),
        page_vector_service: PageVectorServiceAsync = Depends(get_page_vector_service_async),
        document_vector_service: DocumentVectorServiceAsync = Depends(get_document_vector_service_async),
        db: AsyncSession = Depends(get_db),
        redis: aioredis.Redis = Depends(get_redis),
) -> ChatService:
    return ChatService(
        llm=llm, langfuse_client=langfuse_client,
        page_vector_service=page_vector_service, document_vector_service=document_vector_service,
        db=db, redis=redis
    )