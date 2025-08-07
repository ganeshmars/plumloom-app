from fastapi import APIRouter, HTTPException, File, UploadFile, Query, Depends
from fastapi.concurrency import run_in_threadpool
import json
import os
import tempfile
from app.utils.extract_text import extract_text_from_json
from app.utils.chunk_text import chunk_text
from typing import List, Optional, Dict, Any, Tuple
from app.core.auth import validate_session
from app.core.database import get_db
from app.core.config import get_settings
from app.schemas.recent_items import RecentItemsList
from app.services.recent_items_service import RecentItemsService
from sqlalchemy.ext.asyncio import AsyncSession
from llama_cloud_services import LlamaParse
from llama_cloud_services.parse.utils import ResultType
from pathlib import Path
import asyncio
from sqlalchemy import select, func
from app.models.document import Document
from app.models.workspace import Workspace
from app.models.chat_conversation import ChatConversation
from app.core.logging_config import logger

router = APIRouter(prefix="/utils", tags=["utils"])

settings = get_settings()

@router.post("/extract_text_from_json_api")
async def extract_text_from_json_api(
    content_file: UploadFile = File(...)
) -> str:
    """Extract plain text from JSON content."""
    content = await content_file.read()
    # logger.info(f"Received content: {content}")
    try:
        if isinstance(content, (bytes, str)):
            try:
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                    content = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON content: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Invalid JSON content: {str(e)}")
        result = extract_text_from_json(content)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/divide_text_into_chunks_api")
async def divide_text_into_chunks_api(
    text: str = Query(...)
) -> List[str]:
    """Split text into overlapping chunks."""
    try:
        return chunk_text(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def fetch_recent_documents(
    db: AsyncSession,
    user_id: str,
    limit: int = None,
    offset: int = None
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Fetch recent documents for a user from the database.
    """
    # Build base query
    query = (
    select(
        Document,
        Workspace.name.label('workspace_name'),
        func.count().over().label('total_count')  # window function for total count
        )
        .join(Workspace, Document.workspace_id == Workspace.workspace_id)
        .where(Document.user_id == user_id, Document.opened_at.isnot(None))
        .order_by(Document.opened_at.desc())
    )

    if limit is not None:
        query = query.limit(limit)
    if offset is not None:
        query = query.offset(offset)

    result = await db.execute(query)
    rows = result.all()

    # Extract total count from first row (if exists), else 0
    total = rows[0].total_count if rows else 0

    items = [
        {
            'item_id': str(row.Document.document_id),
            'title': row.Document.title or 'Untitled Document',
            'workspace_id': str(row.Document.workspace_id),
            'workspace_name': row.workspace_name or 'Unknown Workspace',
            'parent_id': str(row.Document.parent_id) if row.Document.parent_id else None,
            'updated_at': row.Document.updated_at,
            'opened_at': row.Document.opened_at,
            'item_type': 'Page'
        }
        for row in rows
    ]

    return items, total


async def fetch_recent_chats(
    db: AsyncSession,
    user_id: str,
    limit: int = None,
    offset: int = None
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Fetch recent chat conversations for a user from the database,
    optimized to get total count and paginated results in one query.
    """
    # Build query with window function for total count
    query = (
        select(
            ChatConversation,
            Workspace.name.label('workspace_name'),
            func.count().over().label('total_count')  # Window function for total count
        )
        .join(Workspace, ChatConversation.workspace_id == Workspace.workspace_id)
        .where(ChatConversation.user_id == user_id, ChatConversation.opened_at.isnot(None))
        .order_by(ChatConversation.opened_at.desc())
    )
    
    if limit is not None:
        query = query.limit(limit)
    if offset is not None:
        query = query.offset(offset)
    
    # Execute the single optimized query
    result = await db.execute(query)
    rows = result.all()
    
    # Extract total count from first row or zero if no rows
    total = rows[0].total_count if rows else 0
    
    # Format results
    items = [
        {
            'item_id': str(row.ChatConversation.conversation_id),  # Convert UUID to string
            'title': row.ChatConversation.conversation_title or 'Untitled Chat',
            'workspace_id': str(row.ChatConversation.workspace_id),  # Convert UUID to string
            'workspace_name': row.workspace_name or 'Unknown Workspace',
            'parent_id': None,
            'updated_at': row.ChatConversation.updated_at,
            'opened_at': row.ChatConversation.opened_at,
            'item_type': 'Conversation'  # Consistent item_type
        }
        for row in rows
    ]
    
    return items, total

@router.get("/recent-items", response_model=RecentItemsList)
async def list_recent_items(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    item_type: Optional[str] = Query(None, pattern="^(document|chat)$"),
    current_user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """
    Get recent items (documents and chats) for the current user directly from database.
    """
    try:
        user_id = current_user.get("id")
        # service = RecentItemsService(db)
        # result = await service.list_recent_items(
        #     user_id=user_id,
        #     page=page,
        #     size=size,
        #     item_type=item_type
        # )
        # return result
        # If specific item type is requested, use type-specific query
        if item_type:
            offset = (page - 1) * size
            if item_type == 'document':
                items, total = await fetch_recent_documents(db, user_id, limit=size, offset=offset)
            else:  # chat
                items, total = await fetch_recent_chats(db, user_id, limit=size, offset=offset)
        else:
            # For combined results, fetch both types
            doc_items, doc_total = await fetch_recent_documents(db, user_id, limit=5)
            chat_items, chat_total = await fetch_recent_chats(db, user_id, limit=5)
            
            # Combine and sort all items
            all_items = doc_items + chat_items
            items = sorted(all_items, key=lambda x: x['updated_at'], reverse=True)
            total = doc_total + chat_total
        
        # Convert items to RecentItemResponse format
        from app.schemas.recent_items import RecentItemResponse
        response_items = [RecentItemResponse(**item) for item in items]
        
        return RecentItemsList(
            items=response_items,
            total=total,
            page=page,
            size=size,
            total_pages=max(1, -(-total // size))  # Ceiling division for total pages
        )
    except Exception as e:
        logger.error(f"Error fetching recent items: {str(e)}")
        return RecentItemsList(
            items=[],
            total=0,
            page=page,
            size=size,
            total_pages=1  # At least one page even when empty
        )


@router.post("/process_document_with_llamaparse")
async def process_document_with_llamaparse(
        file: UploadFile = File(...),
) -> Dict[str, Any]:
    """
    Process a document using LlamaParse API and return the parsed content.

    LlamaParse is a document parsing service that can extract structured content from
    various document formats (PDF, DOCX, PPTX, etc.) and convert it to text, markdown, or JSON.

    Parameters:
    - file: The document file to process
    - disable_ocr: Whether to disable OCR for images
    - language: Language(s) for OCR processing

    Returns:
    - A dictionary containing the parsed content and metadata
    """
    logger.info(f"Processing document with LlamaParse: {file.filename}")

    # Check file extension and size
    file_ext = Path(file.filename).suffix.lower().lstrip('.')
    supported_extensions = ["pdf", "docx", "doc", "pptx", "ppt", "xlsx", "xls", "txt", "md", "html", "csv"]

    if file_ext not in supported_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Supported types: {', '.join(supported_extensions)}"
        )

    # Save an uploaded file to a temporary location
    file_content = await file.read()

    # Create a temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as temp_file:
        temp_file.write(file_content)
        temp_file_path = temp_file.name

    try:
        # Initialize LlamaParse
        # API key should be set as an environment variable: LLAMA_CLOUD_API_KEY
        api_key = settings.LLAMA_PARSE_KEY
        if not api_key:
            logger.warning("LLAMA_PARSE_KEY environment variable not set")

        # Initialize the parser with options
        parser = LlamaParse(
            api_key=api_key,
            result_type=ResultType.TXT,
            language="en"
        )

        # Process the document
        try:

            # Parse the document
            # Use the asynchronous method if available

            # Use run_in_threadpool to prevent blocking the event loop
            result = await run_in_threadpool(lambda: parser.parse(temp_file_path))
            result_markdown = result.get_markdown_documents()[0].text
            
            # # Save the markdown content to a file in the output directory
            # output_dir = "/app/output"
            # os.makedirs(output_dir, exist_ok=True)
            #
            # # Create a filename based on the original file
            # output_filename = f"{os.path.splitext(file.filename)[0]}.md"
            # output_path = os.path.join(output_dir, output_filename)
            #
            # # Write the markdown content to the file
            # with open(output_path, "w", encoding="utf-8") as md_file:
            #     md_file.write(result_markdown)
            #
            # logger.info(f"Saved parsed content to {output_path}")
            
            # Return the parsed content and file path in the response
            return {
                "content": result_markdown
            }

        except Exception as e:
            logger.error(f"LlamaParse error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error parsing document with LlamaParse: {str(e)}"
            )

    finally:
        # Clean up the temporary file
        if os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
