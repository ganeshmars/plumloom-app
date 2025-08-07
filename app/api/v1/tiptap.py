import jwt
from fastapi import APIRouter, Request, HTTPException, Depends
from typing import Dict, Any
import logging
from uuid import UUID, uuid4
from datetime import datetime, UTC
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import validate_session
from app.core.logging_config import logger
from app.core.config import get_settings
from app.core.database import get_db
from app.services.template_service import TemplateService
from app.core.redis import get_redis
from app.tasks.template.update_template import process_template_update
from app.tasks.document.sync_documents import sync_documents

settings = get_settings()

router = APIRouter(prefix="/tiptap", tags=["tiptap"])

def get_template_service(db: AsyncSession = Depends(get_db)):
    return TemplateService(db)

@router.post("/webhook")
async def tiptap_webhook(
    request: Request,
    template_service: TemplateService = Depends(get_template_service),
    redis = Depends(get_redis)
):
    try:
        payload = await request.json()
        logger.info(f"Received Tiptap webhook: {payload}")
        if payload.get("clientsCount") != 0:
            return None
        
        file_name = payload.get("name")
        file_content = payload.get("tiptapJson", {}).get("default", {})
        
        try:
            parts = file_name.split('_')
            object_prefix = parts[0] 
            object_id = UUID(parts[1])
            object_data = {
                "id": str(object_id),
                "file_name": file_name,
                "file_content": file_content,
            }
            if object_prefix.lower() == "template" and len(parts) > 1: 
                logger.info(f"Processing template: {object_data}")
                process_template_update.delay(object_data)
            elif object_prefix.lower() == "document" and len(parts) > 1:
                logger.info(f"Processing document: {object_data}")
                sync_documents.delay(object_data)
        except (ValueError, IndexError) as e:
            logger.error(f"Error processing Tiptap webhook: {str(e)}", exc_info=True)
            pass

        return {
            "status": "success", 
            "message": "Document creation event received and queued for processing"
        }
    except Exception as e:
        logger.error(f"Error processing Tiptap webhook: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process webhook: {str(e)}")


async def generate_token(
    user_id: str,
    secret_key: str,
    token_type: str,
    db: AsyncSession,
 ) -> Dict[str, Any]:
    """Generate a JWT token with the given secret key"""
    logger.info(f"Generating {token_type} token for user: {user_id}")
    
    try:
        # Check if user exists using SQLAlchemy
        from app.models.users import User
        from sqlalchemy import select
        
        query = select(User).where(User.id == user_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()
        
        if not user:
             logger.warning(f"User not found: {user_id}")
             raise HTTPException(status_code=404, detail="User not found")
         
        # Generate token
        token = jwt.encode({"user_id": user_id}, secret_key, algorithm="HS256")
        return {"token": token}
         
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating {token_type} token: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
 
@router.post("/ai")
async def get_ai_token(
    current_user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
 ) -> Dict[str, Any]:
    user_id = current_user.get("id")
    """Generate a JWT token for AI services"""
    return await generate_token(user_id, settings.JWT_AI_SECRET, "AI", db)
 
@router.post("/collab")
async def get_collab_token(
    current_user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
 ) -> Dict[str, Any]:
    user_id = current_user.get("id")
    """Generate a JWT token for collaboration services"""
    return await generate_token(user_id, settings.JWT_COLLAB_SECRET, "collaboration", db)