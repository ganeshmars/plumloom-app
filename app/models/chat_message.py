import enum
from uuid import uuid4
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, event, Integer, Enum as DBEnum, Boolean, ARRAY
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base

class SenderType(enum.Enum):
    """Enum to represent the sender of a message."""
    USER = "user"
    AI = "ai"
    SYSTEM = "system"

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    message_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey('chat_conversations.conversation_id', ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    sender_type = Column(
        DBEnum(SenderType, name="sender_type_enum", create_constraint=True),
        nullable=False,
        index=True
    )

    sender_user_id = Column(String, ForeignKey('users.id'), nullable=True, index=True)

    message_content = Column(Text, nullable=False)
    timestamp = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    meta_data = Column(JSONB, nullable=True, default={})
    message_type = Column(String, nullable=True)
    
    # Feedback fields
    is_liked = Column(Boolean, nullable=True, default=None)
    
    # Store multiple standard feedback options selected by the user
    feedback_types = Column(ARRAY(String), nullable=True, default=[])
    
    # Free-form text feedback
    feedback_comment = Column(Text, nullable=True)
    feedback_timestamp = Column(DateTime(timezone=True), nullable=True)

    sender = relationship("User")

    # --- Event Listener for updating Conversation timestamp ---
    # NOTE: SQLAlchemy's onupdate=func.now() on ChatConversation.updated_at is often
    # preferred and handles this automatically if any relationship change occurs within a session flush.
    # This manual listener is an alternative if specific logic is needed *after* insert.
    # Keep it if it serves a specific purpose not covered by standard onupdate.
    @staticmethod
    def after_insert_listener(mapper, connection, target):
        """Update the parent conversation's updated_at timestamp after message insert"""
        from app.models.chat_conversation import ChatConversation # Import locally to avoid circular deps at module level
    
        stmt = (
            ChatConversation.__table__.update()
            .where(ChatConversation.conversation_id == target.conversation_id)
            .values(updated_at=func.now()) # Use func.now() for consistency
        )
        connection.execute(stmt)

    __table_args__ = ()

    def __repr__(self):
        return f"<ChatMessage(message_id={self.message_id}, sender_type={self.sender_type.name if self.sender_type else None})>"

# Register the after_insert event listener
event.listen(ChatMessage, 'after_insert', ChatMessage.after_insert_listener)