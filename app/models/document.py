from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, backref

from sqlalchemy import event, update
from app.models.base import Base
from app.models.workspace import Workspace

class Document(Base):
    __tablename__ = "documents"

    document_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey('workspaces.workspace_id'), nullable=False)
    user_id = Column(String, ForeignKey('users.id'), nullable=False)
    title = Column(String, nullable=False)
    content_file_path = Column(String)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    opened_at = Column(DateTime(timezone=True), nullable=True)
    meta_data = Column(JSONB, nullable=True, default={})
    template_id = Column(UUID(as_uuid=True), ForeignKey('templates.id'), nullable=True)

    # Parent-child relationship
    parent_id = Column(UUID(as_uuid=True), ForeignKey('documents.document_id'), nullable=True)
    icon_url = Column(String, nullable=True)
    cover_url = Column(String, nullable=True)
    last_viewed_at = Column(DateTime, nullable=True)
    # Relationships
    user = relationship("User", backref="documents")
    template = relationship("Template", backref="documents")
    versions = relationship("DocumentVersion", backref="document", cascade="all, delete-orphan")

    # Define the children relationship (one-to-many)
    children = relationship("Document",
                          foreign_keys=[parent_id],
                          backref=backref("parent", remote_side=[document_id]),
                          cascade="all, delete-orphan")

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_documents_workspace_id', 'workspace_id'),
        Index('ix_documents_user_id', 'user_id'),
        Index('ix_documents_title', 'title'),
        Index('ix_documents_created_at', 'created_at'),
        Index('ix_documents_updated_at', 'updated_at'),
        Index('ix_documents_parent_id', 'parent_id'),
        Index('ix_documents_last_viewed_at', 'last_viewed_at'),
        Index('ix_documents_template_id', 'template_id')
    )

    def __repr__(self):
        return f"<Document(document_id={self.document_id}, title={self.title})>"

    def get_document_tree(self):
        """
        Returns a nested dictionary representing this document and all its descendants.
        
        Returns:
            dict: A dictionary with document data and a 'children' key containing
                  a list of child document trees.
        """
        tree = {
            "document_id": self.document_id,
            "title": self.title,
            "children": []
        }
        
        # Recursively add all children
        for child in self.children:
            tree["children"].append(child.get_document_tree())
            
        return tree    


# SQLAlchemy event listener to update Workspace timestamp
def update_workspace_timestamp_on_document_update(mapper, connection, target):
    """
    After a Document is updated, update the 'updated_at' timestamp 
    of its parent Workspace.
    """
    if target.workspace_id:
        stmt = (
            update(Workspace)  # Workspace model should be imported
            .where(Workspace.workspace_id == target.workspace_id)
            .values(updated_at=datetime.now(timezone.utc))
        )
        connection.execute(stmt)

event.listen(Document, 'after_update', update_workspace_timestamp_on_document_update)
