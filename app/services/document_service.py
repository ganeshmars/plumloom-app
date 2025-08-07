from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from uuid import UUID, uuid4
import json

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.core.auth import validate_session
from app.core.config import get_settings
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.services.storage_service import StorageService
from app.services.vector_service import VectorService
from app.core.database import get_db
from sqlalchemy import text  

from app.core.logging_config import logger

class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage_service = StorageService()
        self.vector_service = VectorService()

    async def create_document(
        self, 
        title: str, 
        content: Dict[str, Any], 
        user_id: str,
        tenant_id: str,
        workspace_id: UUID,
        parent_page_id: Optional[UUID] = None,
    ) -> UUID:
        """Create a new document with content stored in GCS and vectorized in Weaviate"""
        doc_id = uuid4()
        content_file_path = f"{doc_id}/content.json"
        gcs_uploaded = False
        vector_stored = False
        db_committed = False

        try:
            # Step 1: Store content in GCS
            try:
                await self.storage_service.upload_json(content_file_path, content)
                gcs_uploaded = True
                logger.info(f"Content uploaded to GCS: {content_file_path}")
            except Exception as e:
                logger.error(f"Failed to upload content to GCS: {str(e)}")
                raise

            # Step 2: Store in Weaviate
            try:
                async with self.vector_service as vector_service:
                    await vector_service.store_document(
                        tenant_id=tenant_id,
                        doc_id=doc_id,
                        workspace_id=workspace_id,
                        title=title,
                        content=content
                    )
                vector_stored = True
                logger.info(f"Content vectorized in Weaviate: {doc_id}")
            except Exception as e:
                logger.error(f"Failed to vectorize content: {str(e)}")
                raise

            # Step 3: Create database records
            try:
                # Create document record
                document = Document(
                    document_id=doc_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    title=title,
                    content_file_path=content_file_path,
                    parent_id=parent_page_id,
                    meta_data={
                        "version": 1,
                        "vectorization_status": "completed"
                    }
                )
                
                # Create initial version
                version = DocumentVersion(
                    document_id=doc_id,
                    version_number=1,
                    content_file_path=content_file_path,
                    meta_data={"initial_version": True}
                )
                
                # Save to database
                self.db.add(document)
                self.db.add(version)
                await self.db.commit()
                db_committed = True
                logger.info(f"Database records created for document: {doc_id}")
            except Exception as e:
                logger.error(f"Failed to create database records: {str(e)}")
                raise
            
            logger.info(f"Successfully created document: {doc_id}")
            return doc_id


        except Exception as e:
            # Rollback all operations in reverse order
            if db_committed:
                try:
                    await self.db.rollback()
                    logger.info("Database changes rolled back")
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback database changes: {str(rollback_error)}")

            if vector_stored:
                try:
                    async with self.vector_service as vector_service:
                        # Implement delete_document in VectorService if not exists
                        await vector_service.delete_document(tenant_id=tenant_id, doc_id=doc_id)
                    logger.info("Weaviate vectors deleted")
                except Exception as vector_error:
                    logger.error(f"Failed to delete vectors from Weaviate: {str(vector_error)}")

            if gcs_uploaded:
                try:
                    await self.storage_service.delete_file(content_file_path)
                    logger.info("GCS content deleted")
                except Exception as storage_error:
                    logger.error(f"Failed to delete content from GCS: {str(storage_error)}")

            # Re-raise the original error
            raise RuntimeError(f"Failed to create document: {str(e)}")

    async def get_document(self, doc_id: UUID) -> Optional[Dict[str, Any]]:
        """Retrieve a document by ID"""
        try:
            # Query document with relationships
            query = select(Document).options(
                selectinload(Document.versions)
            ).where(Document.document_id == doc_id)
            
            result = await self.db.execute(query)
            document = result.scalar_one_or_none()
            
            if not document:
                logger.warning(f"Document not found: {doc_id}")
                return None
            
            # Get content from GCS
            content = await self.storage_service.get_json(document.content_file_path)
            
            # Convert versions to list of dicts
            versions_list = [
                {
                    "version_id": version.version_id,
                    "version_number": version.version_number,
                    "content_file_path": version.content_file_path,
                    "saved_at": version.saved_at,
                    "saved_by_user_id": version.saved_by_user_id,
                    "meta_data": version.meta_data
                } for version in document.versions
            ]
            
            # Return document with all required fields
            return {
                "document_id": document.document_id,
                "workspace_id": document.workspace_id,
                "title": document.title,
                "content": content,
                "content_file_path": document.content_file_path,
                "user_id": document.user_id,
                "icon_url": document.icon_url,
                "cover_url": document.cover_url,
                "created_at": document.created_at,
                "updated_at": document.updated_at,
                "meta_data": document.meta_data,
                "versions": versions_list
            }
            
        except Exception as e:
            logger.error(f"Failed to retrieve document: {str(e)}")
            raise RuntimeError(f"Failed to retrieve document: {str(e)}")
    
    async def get_document_tree(self, doc_id: UUID) -> Optional[Dict[str, Any]]:
        """Retrieve a document's hierarchy tree by ID"""
        try:
            # Load the entire document hierarchy in one go
            # This is a recursive CTE query that gets all descendants
            stmt = """
            WITH RECURSIVE document_tree AS (
                SELECT d.*
                FROM documents d
                WHERE d.document_id = :doc_id
                
            UNION ALL
            
            SELECT d.*
            FROM documents d
            JOIN document_tree dt ON d.parent_id = dt.document_id
            )
            SELECT * FROM document_tree
            """
        
            # Execute the raw SQL query
            result = await self.db.execute(text(stmt), {"doc_id": doc_id})
            rows = result.fetchall()
            
            if not rows:
                logger.warning(f"Document not found: {doc_id}")
                return None
                
            # Build the tree from flat results
            nodes = {}
            root = None
            mock_content = {"text": "Mock content for document"}
            
            # First pass: create all nodes with all required fields
            for row in rows:
                # Create a node with all required fields from DocumentResponse
                node = {
                    "document_id": row.document_id,
                    "title": row.title,
                    "content": mock_content,
                    "workspace_id": row.workspace_id,
                    "user_id": row.user_id,
                    "content_file_path": row.content_file_path,
                    "icon_url": row.icon_url,
                    "cover_url": row.cover_url,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "meta_data": row.meta_data or {},
                    "versions": [],  # Empty list for versions
                    "children": []
                }
                nodes[row.document_id] = node
                
                # The first row is our root document
                if row.document_id == doc_id:
                    root = node
            
            # Second pass: build the tree structure
            for row in rows:
                # Skip the root
                if row.document_id == doc_id:
                    continue
                    
                # Add this node to its parent's children
                if row.parent_id in nodes:
                    nodes[row.parent_id]["children"].append(nodes[row.document_id])
            
            return {"data": root}
            
        except Exception as e:
                logger.error(f"Failed to retrieve document tree: {str(e)}")
                raise RuntimeError(f"Failed to retrieve document tree: {str(e)}")

    async def list_documents(
        self,
        workspace_id: UUID,
        page: int = 1,
        page_size: int = 10
    ) -> Dict[str, Any]:
        """List documents in a workspace with pagination"""
        try:
            # Calculate offset
            offset = (page - 1) * page_size
            
            # Query documents with count and pagination
            query = (
                select(
                    Document,
                    func.count(Document.document_id).over().label('total_count')
                )
                .options(selectinload(Document.versions))
                .filter(Document.workspace_id == workspace_id)
                .filter(Document.parent_id.is_(None))  # Only get root documents
                .offset(offset)
                .limit(page_size)
            )
            
            result = await self.db.execute(query)
            rows = result.unique().all()
            documents = [row[0] for row in rows]
            total = rows[0][1] if rows else 0
            
            # Get all document IDs to fetch their hierarchies
            doc_ids = [doc.document_id for doc in documents]
            
            # Query to get all descendants for each document
            stmt = """
            WITH RECURSIVE document_tree AS (
                SELECT d.*
                FROM documents d
                WHERE d.document_id = ANY(:doc_ids)
                
                UNION ALL
                
                SELECT d.*
                FROM documents d
                JOIN document_tree dt ON d.parent_id = dt.document_id
            )
            SELECT * FROM document_tree
            """
            
            # Execute the raw SQL query
            result = await self.db.execute(text(stmt), {"doc_ids": doc_ids})
            hierarchy_rows = result.fetchall()
            
            # Build the complete hierarchy
            nodes = {}
            root_nodes = []
            
            # First pass: create all nodes
            for row in hierarchy_rows:
                node = {
                    "document_id": str(row.document_id),
                    "title": row.title,
                    "content": {"text": "Mock content for document"},
                    "workspace_id": str(row.workspace_id),
                    "user_id": row.user_id,
                    "content_file_path": row.content_file_path,
                    "icon_url": row.icon_url,
                    "cover_url": row.cover_url,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "meta_data": row.meta_data or {},
                    "versions": [],
                    "children": []
                }
                nodes[row.document_id] = node
                
                # If it's a root document, add to root_nodes
                if row.parent_id is None:
                    root_nodes.append(node)
            
            # Second pass: build the tree structure
            for row in hierarchy_rows:
                if row.parent_id is not None and row.parent_id in nodes:
                    parent_node = nodes[row.parent_id]
                    child_node = nodes[row.document_id]
                    parent_node["children"].append(child_node)
            
            return {
                "documents": root_nodes,
                "total": total,
                "page": page,
                "page_size": page_size
            }
            
        except Exception as e:
            logger.error(f"Failed to list documents: {str(e)}")
            raise RuntimeError(f"Failed to list documents: {str(e)}")

    async def update_document(
        self, 
        doc_id: UUID, 
        user_id: str,
        tenant_id: str,
        title: Optional[str] = None,
        content: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update a document and create a new version"""
        try:
            # Get existing document
            query = select(Document).where(Document.document_id == doc_id)
            result = await self.db.execute(query)
            document = result.scalar_one_or_none()
            
            if not document:
                logger.warning(f"Document not found for update: {doc_id}")
                return False
            
            # Update document fields
            if title is not None:
                document.title = title
                
            gcs_uploaded = False
            vector_stored = False
            db_committed = False
            content_file_path = None
            
            if content is not None:
                try:
                    # Step 1: Store new content version in GCS
                    new_version = document.meta_data.get("version", 1) + 1
                    content_file_path = f"{doc_id}/content_v{new_version}.json"
                    await self.storage_service.upload_json(content_file_path, content)
                    gcs_uploaded = True
                    logger.info(f"New content version uploaded to GCS: {content_file_path}")
                    
                    # Step 2: Store in Weaviate
                    async with self.vector_service as vector_service:
                        # First delete old vectors
                        await vector_service.delete_document(
                            tenant_id=tenant_id,
                            doc_id=doc_id
                        )
                        # Then store new vectors
                        await vector_service.store_document(
                            tenant_id=tenant_id,
                            doc_id=doc_id,
                            workspace_id=document.workspace_id,
                            title=document.title,
                            content=content
                        )
                    vector_stored = True
                    logger.info(f"Content vectorized in Weaviate: {doc_id}")
                    
                    # Step 3: Update database records
                    # Create new version record
                    version = DocumentVersion(
                        document_id=doc_id,
                        version_number=new_version,
                        content_file_path=content_file_path,
                        saved_by_user_id=user_id
                    )
                    self.db.add(version)
                    
                    # Update document metadata
                    document.content_file_path = content_file_path
                    document.meta_data = {
                        **document.meta_data,
                        "version": new_version,
                        "vectorization_status": "completed"
                    }
                    
                    await self.db.commit()
                    db_committed = True
                    logger.info(f"Database records updated for document: {doc_id}")
                    
                except Exception as e:
                    # Rollback all operations in reverse order
                    if db_committed:
                        try:
                            await self.db.rollback()
                            logger.info("Database changes rolled back")
                        except Exception as rollback_error:
                            logger.error(f"Failed to rollback database changes: {str(rollback_error)}")

                    if vector_stored:
                        try:
                            async with self.vector_service as vector_service:
                                await vector_service.delete_document(tenant_id=user_id, doc_id=doc_id)
                            logger.info("Weaviate vectors deleted")
                        except Exception as vector_error:
                            logger.error(f"Failed to delete vectors from Weaviate: {str(vector_error)}")

                    if gcs_uploaded and content_file_path:
                        try:
                            await self.storage_service.delete_file(content_file_path)
                            logger.info("GCS content deleted")
                        except Exception as storage_error:
                            logger.error(f"Failed to delete content from GCS: {str(storage_error)}")

                    raise RuntimeError(f"Failed to update document content: {str(e)}")
            
            # If only title is being updated, just commit the change
            if not content and title is not None:
                await self.db.commit()
                db_committed = True
            logger.info(f"Successfully updated document: {doc_id}")
            return True
            
        except Exception as e:
            if not db_committed:
                await self.db.rollback()
            logger.error(f"Failed to update document: {str(e)}")
            raise RuntimeError(f"Failed to update document: {str(e)}")

    async def delete_document(self, doc_id: UUID) -> bool:
        """Delete a document and all its versions"""
        try:
            # Get document
            query = select(Document).options(
                selectinload(Document.versions)
            ).where(Document.document_id == doc_id)
            
            result = await self.db.execute(query)
            document = result.scalar_one_or_none()
            
            if not document:
                logger.warning(f"Document not found for deletion: {doc_id}")
                return False
            
            # Delete content files from GCS
            await self.storage_service.delete_prefix(f"{doc_id}/")
            
            # Delete vector embeddings
            # await self.vector_service.delete_document_vectors(doc_id)
            
            # Delete document and its versions (cascade)
            await self.db.delete(document)
            await self.db.commit()
            
            logger.info(f"Successfully deleted document: {doc_id}")
            return True
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to delete document: {str(e)}")
            raise RuntimeError(f"Failed to delete document: {str(e)}")

    async def get_document_object_by_id(self, doc_id: UUID) -> Optional[Document]:
        """Get a document object by ID without loading content"""
        try:
            query = select(Document).where(Document.document_id == doc_id)
            result = await self.db.execute(query)
            document = result.scalar_one_or_none()
            return document
        except Exception as e:
            logger.error(f"Error retrieving document {doc_id}: {str(e)}")
            raise RuntimeError(f"Failed to retrieve document: {str(e)}")

    async def update_document_cover(
        self, 
        doc_id: UUID, 
        cover_url: str,
        meta_data: Optional[Dict[str, Any]] = None
    ) -> Optional[Document]:
        """Update a document's cover URL and metadata"""
        try:
            # Get existing document
            query = select(Document).where(Document.document_id == doc_id)
            result = await self.db.execute(query)
            document = result.scalar_one_or_none()
            
            if not document:
                logger.warning(f"Document not found for cover update: {doc_id}")
                return None
            
            # Update document fields
            document.cover_url = cover_url
            document.meta_data = meta_data
            # Commit changes
            self.db.commit()
            self.db.refresh(document)
            
            logger.info(f"Successfully updated document cover: {doc_id}")
            return document
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update document cover: {str(e)}")
            raise RuntimeError(f"Failed to update document cover: {str(e)}")

    async def move_document_to_workspace(self, page_id: UUID, new_workspace_id: UUID) -> bool:
        """
        Move a document (page) and its descendants to a new workspace using SQLAlchemy ORM updates.
        - The root page gets the new workspace_id and parent_id=None.
        - All descendants get their workspace_id updated.
        - If a child page is shifted, it becomes a root in the new workspace, and its children are transferred as-is.
        """
        try:
            # Get the full tree of the document
            stmt = """
            WITH RECURSIVE document_tree AS (
                SELECT * FROM documents WHERE document_id = :doc_id
                UNION ALL
                SELECT d.* FROM documents d JOIN document_tree dt ON d.parent_id = dt.document_id
            )
            SELECT * FROM document_tree
            """
            result = await self.db.execute(text(stmt), {"doc_id": page_id})
            rows = result.fetchall()
            if not rows:
                logger.warning(f"Document not found for move: {page_id}")
                return False

            # Build a map of document_id to row
            doc_ids = [row.document_id for row in rows]
            # Fetch all Document ORM objects in one go
            query = select(Document).where(Document.document_id.in_(doc_ids))
            result = await self.db.execute(query)
            documents = result.scalars().all()
            doc_map = {doc.document_id: doc for doc in documents}

            # Build a map of parent_id to children
            children_map = {}
            for doc in documents:
                children_map.setdefault(doc.parent_id, []).append(doc.document_id)

            # BFS to update all descendants using ORM
            queue = [(page_id, None)]  # (current_id, new_parent_id)
            while queue:
                current_id, new_parent_id = queue.pop(0)
                doc = doc_map[current_id]
                doc.workspace_id = new_workspace_id
                doc.parent_id = new_parent_id
                for child_id in children_map.get(current_id, []):
                    queue.append((child_id, current_id))
            await self.db.commit()
            logger.info(f"Moved document {page_id} and its descendants to workspace {new_workspace_id} (ORM)")
            return True
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to move document (ORM): {str(e)}")
            raise RuntimeError(f"Failed to move document: {str(e)}")