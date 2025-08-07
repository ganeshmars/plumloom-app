from fastapi import APIRouter, Depends, HTTPException, Query, Body
from typing import Optional, List
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select
from app.core.logging_config import logger
from app.core.auth import validate_session
from app.core.database import get_db
from app.services.template_service import TemplateService
from app.schemas.template import TemplateCreate, TemplateResponse, TemplateList, TemplateUpdate
from app.models.template import TemplateCategory, Template



router = APIRouter(prefix="/templates", tags=["templates"])

def get_template_service(db: AsyncSession = Depends(get_db)):
    return TemplateService(db)

@router.post("/create", response_model=TemplateResponse)
async def create_template(
    title: str,
    category: TemplateCategory,
    icon_url: Optional[str] = None,
    current_user: dict = Depends(validate_session),
    template_service: TemplateService = Depends(get_template_service)
) -> TemplateResponse:
    """Create a new template"""
    logger.info(f"Creating template with title: {title}")
    
    try:
        template = await template_service.create_template(
            title=title,
            user_id=current_user['id'] if category == TemplateCategory.MY_TEMPLATE else None,
            category=category,
            icon_url=icon_url,
            is_custom=True if category == TemplateCategory.MY_TEMPLATE else False
        )
        
        template_response = TemplateResponse.model_validate(template)
        return template_response
        
    except ValueError as e:
        logger.warning(f"Invalid input for template creation: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating template: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: UUID,
    template_update: TemplateUpdate,
    current_user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
) -> TemplateResponse:
    """Update a template's title"""
    try:
        # First check if the template exists and belongs to the user
        query = select(Template).where(
            Template.id == template_id,
            Template.user_id == current_user.get("user_id")
        )
        result = await db.execute(query)
        template = result.scalar_one_or_none()
        
        if not template:
            raise HTTPException(
                status_code=404, 
                detail="Template not found or you don't have permission to update it"
            )
        
        # Update the template title using SQLAlchemy update
        update_values = {}
        if template_update.title is not None:
            update_values["title"] = template_update.title
        if template_update.icon_url is not None:
            update_values["icon_url"] = template_update.icon_url
            
        if not update_values:
            # No changes to make
            return template
            
        update_query = update(Template).where(
            Template.id == template_id
        ).values(**update_values).returning(Template)
        
        result = await db.execute(update_query)
        updated_template = result.scalar_one()
        await db.commit()
        
        logger.info(f"Successfully updated template: {template_id}")
        return updated_template
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating template: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating template: {str(e)}")

@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: UUID,
    current_user: dict = Depends(validate_session),
    template_service: TemplateService = Depends(get_template_service)
) -> TemplateResponse:
    """Get a template by ID"""
    logger.info(f"Fetching template with id: {template_id}")
    
    try:
        template = await template_service.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        
        template_response = TemplateResponse.model_validate(template)
        return template_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving template {template_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/")
async def list_templates(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    category: Optional[TemplateCategory] = None,
    is_custom: Optional[bool] = None,
    current_user: dict = Depends(validate_session),
    template_service: TemplateService = Depends(get_template_service)
):
    """List templates with pagination and optional filtering
    
    Returns:
    - user : List of custom templates for that particular user
    - app : List of all admin(app) templates
    """
    try:
        templates_list = await template_service.list_templates(
            user_id=current_user['id'],
            page=page,
            page_size=page_size,
            category=category,
            is_custom=is_custom
        )
        
        return templates_list
        
    except Exception as e:
        logger.error(f"Error listing templates: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
