from typing import Optional, List
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.sql import or_

from app.models.template import Template, TemplateCategory
from app.schemas.template import TemplateList

from app.core.logging_config import logger

class TemplateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_template(
        self, 
        title: str,
        user_id: str,
        category: TemplateCategory,
        icon_url: Optional[str] = None,
        is_custom: bool = False
    ) -> Template:
        """Create a new template"""
        try:
            template = Template(
                title=title,
                user_id=user_id,
                category=category,
                icon_url=icon_url,
                is_custom=is_custom,
                meta_data={}
            )
            
            self.db.add(template)
            await self.db.commit()
            await self.db.refresh(template)
            
            logger.info(f"Successfully created template: {template.id}")
            return template
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to create template: {str(e)}")
            raise RuntimeError(f"Failed to create template: {str(e)}")

    async def get_template(self, template_id: UUID) -> Optional[Template]:
        """Get a template by ID"""
        try:
            query = select(Template).where(Template.id == template_id)
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error retrieving template {template_id}: {str(e)}")
            raise RuntimeError(f"Failed to retrieve template: {str(e)}")

    async def list_templates(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 10,
        category: Optional[TemplateCategory] = None,
        is_custom: Optional[bool] = None
    ) -> dict:
        """List templates with pagination and optional filtering"""
        try:
            query = select(Template).filter(or_(Template.user_id.is_(None), Template.user_id == user_id))
            
            if category:
                query = query.filter(Template.category == category)
                
            if is_custom is not None:
                query = query.filter(Template.is_custom == is_custom)
            
            # Get total count
            count_query = select(func.count()).select_from(query.subquery())
            total = await self.db.scalar(count_query) or 0
            
            # Apply pagination
            query = query.offset((page - 1) * page_size).limit(page_size)
            
            # Execute query
            result = await self.db.execute(query)
            templates = result.scalars().all()
            
            # Separate templates into user and app categories
            user_templates = [template for template in templates if template.is_custom]
            app_templates = [template for template in templates if not template.is_custom]
            
            # Categorize app templates by category
            categorized_app_templates = {}
            for template in app_templates:
                if template.category not in categorized_app_templates:
                    categorized_app_templates[template.category] = []
                categorized_app_templates[template.category].append(template)
            
            return {
                "user": user_templates,
                "app": categorized_app_templates,
                "total": total,
                "page": page,
                "page_size": page_size
            }
            
        except Exception as e:
            logger.error(f"Failed to list templates: {str(e)}")
            raise RuntimeError(f"Failed to list templates: {str(e)}")

    async def update_template(self, template_id: UUID, update_data: dict) -> Optional[Template]:
        """Update template fields
        
        Args:
            template_id: UUID of the template to update
            update_data: Dictionary of fields to update
            
        Returns:
            Updated Template object or None if template not found
        """
        try:
            query = select(Template).where(Template.id == template_id)
            result = await self.db.execute(query)
            template = result.scalar_one_or_none()
            
            if not template:
                logger.warning(f"Template {template_id} not found for update")
                return None
            
            # Update template fields
            for field, value in update_data.items():
                if hasattr(template, field):
                    setattr(template, field, value)
            
            await self.db.commit()
            await self.db.refresh(template)
            
            logger.info(f"Updated template {template_id} with fields: {list(update_data.keys())}")
            return template
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Error updating template {template_id}: {str(e)}")
            raise RuntimeError(f"Failed to update template: {str(e)}")
    
    async def update_template_content_url(self, template_id: UUID, content_url: str) -> Optional[Template]:
        """Update the content_url for a template"""
        try:
            return await self.update_template(template_id, {"content_url": content_url})
        except Exception as e:
            logger.error(f"Error updating content_url for template {template_id}: {str(e)}")
            raise RuntimeError(f"Failed to update template content_url: {str(e)}")
