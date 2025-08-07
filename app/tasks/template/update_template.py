import json
from uuid import UUID
from datetime import datetime, UTC
from typing import Dict, Any, List, Tuple, Optional
from app.core.celery_app import celery_app
from app.core.redis import sync_redis
from app.services.template_service import TemplateService
from app.core.database import get_db
from app.core.storage import upload_file_to_gcs_sync
from app.core.constants import GCS_STORAGE_BUCKET
import asyncio
from app.services.template_service import TemplateService
from app.core.database import get_db
from app.core.config import get_settings
from app.core.storage import upload_file_to_gcs_sync
from app.models.template import TemplateCategory

from app.core.logging_config import logger

settings = get_settings()

@celery_app.task(
    name="app.tasks.template.update_template.process_template_update",
    queue="template_updation",
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=60,
    time_limit=120,
    acks_late=True
)
def process_template_update(template_data):
    logger.info(f"Processing template update task for template: {template_data['id']}")
    
    template_id = UUID(template_data["id"])
    file_name = template_data["file_name"]
    file_content = template_data["file_content"]
    
    try:
        result = asyncio.get_event_loop().run_until_complete(update_template_content(template_id, file_name, file_content))
        
        logger.info(f"Successfully updated template {template_id}")
        return {
            "status": True,
            "template_id": str(template_id),
            "content_url": result["content_url"]
        }
        
    except Exception as e:
        logger.error(f"Failed to update template {template_id}: {str(e)}")
        raise


def extract_sections_from_tiptap(content: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract sections (headings and their descriptions) from Tiptap JSON content.
    
    A section consists of a heading and the text content that follows it until the next heading.
    Returns a list of dictionaries with 'heading' and 'description' keys.
    """
    sections = []
    current_heading = None
    current_description = []
    
    # Get the document content from the Tiptap JSON structure
    doc_content = None
    if isinstance(content, dict):
        if "data" in content and "content" in content["data"]:
            if "content" in content["data"]["content"]:
                doc_content = content["data"]["content"]["content"]
        elif "type" in content and content["type"] == "doc" and "content" in content:
            doc_content = content["content"]
    
    if not doc_content or not isinstance(doc_content, list):
        logger.warning("Could not find document content in Tiptap JSON")
        return sections
    
    # Process each node in the document
    for node in doc_content:
        if isinstance(node, dict) and "type" in node:
            # If we encounter a heading, save the previous section and start a new one
            if node["type"] == "heading" and "content" in node and "attrs" in node:
                # Save the previous section if it exists
                if current_heading is not None:
                    sections.append({
                        "heading": current_heading,
                        "description": " ".join(current_description).strip()
                    })
                
                # Extract heading text
                heading_text = ""
                for content_node in node["content"]:
                    if content_node.get("type") == "text" and "text" in content_node:
                        heading_text += content_node["text"]
                
                # Start a new section
                current_heading = heading_text
                current_description = []
            
            # If we have a current heading and encounter a paragraph, add its text to the description
            elif current_heading is not None and node["type"] in ["paragraph", "bulletList", "orderedList"] and "content" in node:
                # Extract text from paragraph or list
                paragraph_text = extract_text_from_node(node)
                if paragraph_text:
                    current_description.append(paragraph_text)
    
    # Add the last section if it exists
    if current_heading is not None:
        sections.append({
            "heading": current_heading,
            "description": " ".join(current_description).strip()
        })
    
    return sections


def extract_text_from_node(node: Dict[str, Any]) -> str:
    """
    Recursively extract text from a node and its children.
    """
    text = []
    
    if "type" in node and node["type"] == "text" and "text" in node:
        text.append(node["text"])
    
    if "content" in node and isinstance(node["content"], list):
        for child in node["content"]:
            if isinstance(child, dict):
                text.append(extract_text_from_node(child))
    
    return " ".join(text).strip()


async def update_template_content(template_id, file_name, file_content):
    db = await anext(get_db())
    try:
        template_service = TemplateService(db)
        template = await template_service.get_template(template_id)
        
        # Extract sections from the Tiptap JSON content
        sections = extract_sections_from_tiptap(file_content)
        
        # Update the template's meta_data with the extracted sections
        meta_data = template.meta_data or {}
        meta_data["sections"] = sections
        
        # Fix the syntax error in the f-string by using single quotes consistently
        file_path = f"templates/{'app' if template.category != TemplateCategory.MY_TEMPLATE else f'user/{template.user_id}'}/{file_name}.json"
        json_content = json.dumps(file_content, ensure_ascii=False)
        
        # Upload to GCS
        public_url = upload_file_to_gcs_sync(
            content=json_content,
            file_path=file_path,
            bucket_name=GCS_STORAGE_BUCKET,
            content_type="application/json"
        )
        
        # Update the template content URL and meta_data
        await template_service.update_template(template_id, {
            "content_url": public_url,
            "meta_data": meta_data
        })
        await db.commit()
        
        logger.info(f"Updated template {template_id} with {len(sections)} extracted sections")
        
        return {
            "template_id": str(template_id),
            "content_url": public_url,
            "sections_count": len(sections)
        }
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Database error during template update: {str(e)}")
        raise
    finally:
        await db.close()
