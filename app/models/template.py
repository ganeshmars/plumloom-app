from datetime import datetime, timezone
from uuid import uuid4
from enum import Enum
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Index, Enum as SQLAlchemyEnum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base

class TemplateCategory(str, Enum):
    BUSINESS_PLANNING = "business_planning"
    PRODUCT_MANAGEMENT = "product_management"
    HEALTHCARE = "healthcare"
    SOFTWARE_IT = "software_it"
    CONTENT_CREATION_MARKETING = "content_creation_marketing"
    PERSONAL_PRODUCTIVITY = "personal_productivity"
    FINANCE_OPERATIONS = "finance_operations"
    PRODUCT_OPERATIONS = "product_operations"
    CUSTOMER_SUPPORT = "customer_support"
    MY_TEMPLATE = "my_template"

class Template(Base):
    __tablename__ = "templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(String, ForeignKey('users.id'), nullable=True)
    title = Column(String, nullable=False)
    icon_url = Column(String, nullable=True)
    content_url = Column(String, nullable=True)
    is_custom = Column(Boolean, nullable=False, default=False)
    category = Column(SQLAlchemyEnum(TemplateCategory, name='template_category'), nullable=False)
    meta_data = Column(JSONB, nullable=True, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", backref="templates")
    
    __table_args__ = (
        Index('ix_templates_user_id', 'user_id'),
        Index('ix_templates_title', 'title'),
        Index('ix_templates_created_at', 'created_at'),
        Index('ix_templates_updated_at', 'updated_at'),
    )

    def __repr__(self):
        return f"<Template(template_id={self.id}, title={self.title})>"

    def get_template_tree(self):
        """
        Returns a nested dictionary representing this template and all its descendants.
        
        Returns:
            dict: A dictionary with template data and a 'children' key containing
                  a list of child template trees.
        """
        tree = {
            "id": self.id,
            "title": self.title,
            "icon_url": self.icon_url,
            "category": self.category
        }
        
        # Recursively add all children
        for child in self.children:
            tree["children"].append(child.get_template_tree())
            
        return tree
