from datetime import datetime
from uuid import uuid4
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class Workspace(Base):
    __tablename__ = "workspaces"

    workspace_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(String, ForeignKey('users.id'), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    opened_at = Column(DateTime(timezone=True), nullable=True)
    meta_data = Column(JSONB, nullable=True, default={})
    icon_url = Column(String, nullable=True)
    cover_image_url = Column(String, nullable=True)
    workspace_type = Column(String, nullable=True)
    panel_state = Column(JSONB, nullable=True, default={"ai_assistant_panel": "closed", "context_menu": "open"})

    # Relationships
    user = relationship("User", back_populates="workspaces")
    documents = relationship("Document", backref="workspace", cascade="all, delete-orphan")
    chat_conversations = relationship("ChatConversation", backref="workspace", cascade="all, delete-orphan")
    uploaded_documents = relationship("UploadedDocument", backref="workspace", cascade="all, delete-orphan")

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_workspaces_user_id', 'user_id'),
        Index('ix_workspaces_name', 'name')
    )

    def __repr__(self):
        return f"<Workspace(workspace_id={self.workspace_id}, name={self.name})>"
