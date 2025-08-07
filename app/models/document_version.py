from datetime import datetime
from uuid import uuid4
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class DocumentVersion(Base):
    __tablename__ = "document_versions"

    version_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey('documents.document_id'), nullable=False)
    version_number = Column(Integer, nullable=False)
    content_file_path = Column(String)
    saved_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    meta_data = Column(JSONB, nullable=True, default={})


    # Indexes and constraints
    __table_args__ = (
        UniqueConstraint('document_id', 'version_number', name='uix_document_version'),
        Index('ix_document_versions_document_id', 'document_id'),
        Index('ix_document_versions_saved_at', 'saved_at')
    )

    def __repr__(self):
        return f"<DocumentVersion(version_id={self.version_id}, document_id={self.document_id}, version_number={self.version_number})>"
