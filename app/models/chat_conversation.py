from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import event, update
from app.models.workspace import Workspace
from app.models.base import Base

class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    conversation_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey('workspaces.workspace_id'), nullable=False)
    user_id = Column(String, ForeignKey('users.id'), nullable=False)

    conversation_title = Column(String, nullable=True)
    icon = Column(String, nullable=True)  # Store icon name or URL

    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    opened_at = Column(DateTime(timezone=True), nullable=True)

    meta_data = Column(JSONB, nullable=True, default={})
    conversation_status = Column(String, default='active', index=True) # active, archived, deleted

    user = relationship("User", backref="chat_conversations")

    messages = relationship(
        "ChatMessage",
        backref="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.timestamp"
    )

    __table_args__ = (
        Index('ix_chat_conversations_workspace_id', 'workspace_id'),
        Index('ix_chat_conversations_user_id', 'user_id'),
        Index('ix_chat_conversations_started_at', 'started_at'),
        Index('ix_chat_conversations_updated_at', 'updated_at'),
        # Index('ix_chat_conversations_status', 'conversation_status')
    )

    def __repr__(self):
        return f"<ChatConversation(conversation_id={self.conversation_id}, title={self.conversation_title})>"

# SQLAlchemy event listener to update Workspace timestamp
def update_workspace_timestamp_on_chat_update(mapper, connection, target):
    """
    After a ChatConversation is updated, update the 'updated_at' timestamp 
    of its parent Workspace.
    """
    # 'target' is the ChatConversation instance that was updated
    if target.workspace_id:
        # Ensure datetime and timezone are available (they should be from top imports)
        stmt = (
            update(Workspace)  # Workspace model should be imported
            .where(Workspace.workspace_id == target.workspace_id)
            .values(updated_at=datetime.now(timezone.utc))
        )
        connection.execute(stmt)

event.listen(ChatConversation, 'after_update', update_workspace_timestamp_on_chat_update)
