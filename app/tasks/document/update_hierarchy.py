import json
from uuid import UUID
from datetime import datetime, UTC
from sqlalchemy import select
from app.core.celery_app import celery_app
from app.core.database import get_db
from app.models.document import Document
from app.core.redis import sync_redis
import asyncio
from app.services.document_service import DocumentService
from app.core.logging_config import logger


@celery_app.task(
    name="app.tasks.document.update_hierarchy.process_hierarchy_update",
    queue="doc_hierarchy",
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=60,
    time_limit=120,
    acks_late=True
)
def process_hierarchy_update(task_data):
    logger.info(f"Processing hierarchy update task")
    
    target_doc_id = UUID(task_data['data']['target_doc_id'])
    parent_doc_id = UUID(task_data['data']['parent_doc_id']) if task_data['data']['parent_doc_id'] else None
    
    try:
        result = asyncio.get_event_loop().run_until_complete(update_document_hierarchy(target_doc_id, parent_doc_id))
        
        logger.info(f"Successfully updated hierarchy for document {target_doc_id}")
        return {
            "status": True,
            "target_doc_id": str(target_doc_id),
            "parent_doc_id": str(parent_doc_id) if parent_doc_id else None
        }
        
    except Exception as e:
        logger.error(f"Failed to update hierarchy for document {target_doc_id}: {str(e)}")
        raise


async def update_document_hierarchy(target_doc_id, parent_doc_id):
    db_gen = get_db()
    db = await anext(db_gen)
    
    try:
        document_service = DocumentService(db)
        
        document = await document_service.get_document_object_by_id(target_doc_id)
        if not document:
            raise ValueError(f"Target document not found: {target_doc_id}")

        if parent_doc_id:
            parent_document = await document_service.get_document_object_by_id(parent_doc_id)
            
            if not parent_document:
                logger.info(f"Parent document not found: {parent_doc_id}")
                raise ValueError(f"Parent document not found: {parent_doc_id}")
            
            if parent_document.workspace_id != document.workspace_id:
                logger.info("Parent and target documents must be in the same workspace")
                raise ValueError("Parent and target documents must be in the same workspace")
        
        document.parent_id = parent_doc_id
        await db.commit()
        
        return {
            "document_id": str(document.document_id),
            "title": document.title,
            "parent_id": str(document.parent_id) if document.parent_id else None,
            "workspace_id": str(document.workspace_id),
            "updated_at": document.updated_at.isoformat()
        }
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Database error during hierarchy update: {str(e)}")
        raise
    finally:
        await db.close()
        try:
            await db_gen.aclose()
        except Exception:
            pass