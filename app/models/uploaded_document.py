from datetime import datetime
from uuid import uuid4
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"

    uploaded_document_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(String, ForeignKey('users.id'), nullable=False)  # Foreign key to users table
    workspace_id = Column(UUID(as_uuid=True), ForeignKey('workspaces.workspace_id'), nullable=False)
    chat_conversation_id = Column(UUID(as_uuid=True), nullable=True)  # Chat conversation ID this document belongs to

    # Document metadata
    file_name = Column(String, nullable=False)
    file_type = Column(String, nullable=False)  # pdf, docx, txt
    file_size_bytes = Column(Integer, nullable=False)  # Size in bytes

    # Storage information
    file_path = Column(String, nullable=False)  # Path in Google Cloud Storage

    # Processing status
    is_processed = Column(Boolean, default=False)
    processing_status = Column(String, nullable=False, default='pending')  # pending, processing, completed, failed
    error_message = Column(String, nullable=True)

    # Vector DB information
    vector_chunks_count = Column(Integer, default=0)
    vector_status = Column(String, default='pending')  # pending, processing, completed, failed

    # Timestamps
    uploaded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Additional metadata
    meta_data = Column(JSONB, nullable=True, default={})
    
    # Relationships
    user = relationship("User", backref="uploaded_documents")

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_uploaded_documents_user_id', 'user_id'),
        Index('ix_uploaded_documents_workspace_id', 'workspace_id'),
        Index('ix_uploaded_documents_chat_conversation_id', 'chat_conversation_id'),
        Index('ix_uploaded_documents_file_name', 'file_name'),
        Index('ix_uploaded_documents_file_type', 'file_type'),
        Index('ix_uploaded_documents_is_processed', 'is_processed'),
        Index('ix_uploaded_documents_processing_status', 'processing_status'),
        Index('ix_uploaded_documents_vector_status', 'vector_status'),
        Index('ix_uploaded_documents_uploaded_at', 'uploaded_at')
    )

    def __repr__(self):
        return f"<UploadedDocument(uploaded_document_id={self.uploaded_document_id}, file_name={self.file_name})>"