import json

import time
import hashlib
from typing import Dict, Any, List, Union
from uuid import UUID

from app.utils.extract_text import extract_text_from_json
from app.utils.chunk_text import chunk_text
from app.services.weaviate_base_service import WeaviateService
from weaviate.classes.query import Filter

from app.core.logging_config import logger

# Define the collection name based on your schema
WEAVIATE_PAGE_CLASS_NAME = "Page"
DEFAULT_BATCH_SIZE = 100 # Adjust as needed


class VectorService(WeaviateService): # Inherit from WeaviateService
    def __init__(self, chunk_size: int = 100, overlap: int = 20):
        super().__init__()

        self.chunk_size = chunk_size
        self.overlap = overlap
        
    def _generate_chunk_fingerprint(self, chunk_text: str) -> str:
        """Generate a unique fingerprint for a chunk of text."""
        # Normalize text before hashing
        normalized_text = " ".join(chunk_text.lower().split())
        return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()
        
    def _get_document_chunks(self, tenant_id: str, doc_id: str) -> List[Dict[str, Any]]:
        """Get all chunks for a document with their fingerprints."""
        collection = self.client.collections.get(WEAVIATE_PAGE_CLASS_NAME)
        collection = collection.with_tenant(tenant=tenant_id)
        
        # Use fetch_objects with Filter for v4 syntax
        results = collection.query.fetch_objects(
            filters=Filter.by_property("documentId").equal(str(doc_id))
        )
        
        # Convert Weaviate objects to dictionary format
        chunks = []
        for obj in results.objects:
            chunk_data = {
                "_additional": {"id": str(obj.uuid)},
                **obj.properties
            }
            chunks.append(chunk_data)
        
        # Sort by chunkOrder
        chunks.sort(key=lambda x: x.get('chunkOrder', 0))
        
        return chunks

    def create_vectors(
        self,
        tenant_id: str,
        doc_id: UUID,
        workspace_id: UUID,
        title: str,
        content: Union[Dict[str, Any], bytes, str]
    ) -> Dict[str, Any]:
        """Create vector embeddings for document content.
        
        Args:
            tenant_id: ID of the tenant
            doc_id: Document UUID
            workspace_id: Workspace UUID
            title: Document title
            content: TipTap JSON content
            
        Returns:
            Dict with status and message
        """
        try:
            # Parse content if it's bytes or string
            if isinstance(content, (bytes, str)):
                try:
                    if isinstance(content, bytes):
                        content = content.decode('utf-8')
                    content = json.loads(content)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON content: {str(e)}")
                    return {
                        "status": "error",
                        "message": f"Invalid JSON content: {str(e)}",
                        "document_id": str(doc_id)
                    }
            
            logger.info(f"Processing content type: {type(content)}")
            logger.info(f"Content structure: {json.dumps(content, indent=2)}")
            
            # Extract text from TipTap JSON
            text = extract_text_from_json(content)
            logger.info(f"Extracted text: '{text}'")
            logger.info(f"Extracted {len(text)} characters from document {doc_id}")
            
            # Split into chunks
            chunks = chunk_text(text, self.chunk_size, self.overlap)
            logger.info(f"Split text into {len(chunks)} chunks")
            
            # Get Page collection with tenant
            collection = self.client.collections.get("Page")
            collection = collection.with_tenant(tenant=tenant_id)
            
            # Process each chunk
            successful_chunks = 0
            for i, chunk in enumerate(chunks):
                try:
                    # Prepare properties for Weaviate
                    properties = {
                        "tenantId": tenant_id,
                        "documentId": str(doc_id),
                        "workspaceId": str(workspace_id),
                        "title": title,
                        "contentChunk": chunk,
                        "chunkOrder": i,
                        "chunkFingerprint": self._generate_chunk_fingerprint(chunk)
                    }
                    
                    # Insert into Weaviate
                    result = collection.data.insert(properties)
                    
                    if result:
                        successful_chunks += 1
                        logger.info(f"Successfully vectorized chunk {i+1}/{len(chunks)} for document {doc_id}")
                    else:
                        logger.error(f"Failed to vectorize chunk {i+1} for document {doc_id}")
                        
                except Exception as chunk_error:
                    logger.error(f"Error processing chunk {i+1}: {str(chunk_error)}")
                    continue
                
                # Add small delay between chunks to prevent memory issues
                if i < len(chunks) - 1:  # Don't sleep after last chunk
                    time.sleep(0.2)
            
            # Return success status with details
            return {
                "status": "success",
                "message": f"Successfully vectorized {successful_chunks} out of {len(chunks)} chunks",
                "total_chunks": len(chunks),
                "successful_chunks": successful_chunks,
                "document_id": str(doc_id)
            }
            
        except Exception as e:
            logger.error(f"Failed to vectorize document {doc_id}: {str(e)}")
            return {
                "status": "error",
                "message": f"Failed to vectorize document: {str(e)}",
                "document_id": str(doc_id)
            }
            
    def update_vectors(
        self,
        tenant_id: str,
        doc_id: UUID,
        workspace_id: UUID,
        title: str,
        content: Union[Dict[str, Any], bytes, str]
    ) -> Dict[str, Any]:
        """Update vector embeddings for document content.
        Only updates chunks that have changed, preserving existing ones.
        """
        try:
            # Parse content if needed
            if isinstance(content, (bytes, str)):
                try:
                    if isinstance(content, bytes):
                        content = content.decode('utf-8')
                    content = json.loads(content)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON content: {str(e)}")
                    return {
                        "status": "error",
                        "message": f"Invalid JSON content: {str(e)}",
                        "document_id": str(doc_id)
                    }
            
            # Extract text and create new chunks
            text = extract_text_from_json(content)
            new_chunks = chunk_text(text, self.chunk_size, self.overlap)
            
            # Get existing chunks
            existing_chunks = self._get_document_chunks(tenant_id, str(doc_id))
            
            # Generate fingerprints for new chunks
            new_chunk_data = [
                (chunk, self._generate_chunk_fingerprint(chunk))
                for chunk in new_chunks
            ]
            
            # Map existing chunks by fingerprint
            existing_by_fingerprint = {
                chunk['chunkFingerprint']: chunk
                for chunk in existing_chunks
            }
            
            # Prepare collections
            collection = self.client.collections.get(WEAVIATE_PAGE_CLASS_NAME)
            collection = collection.with_tenant(tenant=tenant_id)
            
            # Track statistics
            stats = {
                "unchanged": 0,
                "updated": 0,
                "added": 0,
                "removed": 0
            }
            
            # Process each new chunk
            for i, (chunk, fingerprint) in enumerate(new_chunk_data):
                try:
                    if fingerprint in existing_by_fingerprint:
                        # Chunk exists and hasn't changed
                        existing_chunk = existing_by_fingerprint[fingerprint]
                        if existing_chunk['chunkOrder'] != i:
                            # Update order if changed
                            collection.data.update(
                                uuid=existing_chunk['_additional']['id'],
                                properties={"chunkOrder": i}
                            )
                        stats["unchanged"] += 1
                        del existing_by_fingerprint[fingerprint]
                    else:
                        # New or modified chunk
                        properties = {
                            "tenantId": tenant_id,
                            "documentId": str(doc_id),
                            "workspaceId": str(workspace_id),
                            "title": title,
                            "contentChunk": chunk,
                            "chunkOrder": i,
                            "chunkFingerprint": fingerprint
                        }
                        collection.data.insert(properties)
                        stats["added"] += 1
                        
                except Exception as chunk_error:
                    logger.error(f"Error processing chunk {i}: {str(chunk_error)}")
                    continue
            
            # Remove chunks that no longer exist
            for old_chunk in existing_by_fingerprint.values():
                try:
                    collection.data.delete_by_id(
                        uuid=old_chunk['_additional']['id']
                    )
                    stats["removed"] += 1
                except Exception as del_error:
                    logger.error(f"Error deleting chunk: {str(del_error)}")
            
            return {
                "status": "success",
                "message": "Successfully updated document vectors",
                "document_id": str(doc_id),
                "stats": stats
            }
            
        except Exception as e:
            logger.error(f"Failed to update vectors for document {doc_id}: {str(e)}")
            return {
                "status": "error",
                "message": f"Failed to update vectors: {str(e)}",
                "document_id": str(doc_id)
            }
            
    async def search_documents(
        self,
        query: str,
        workspace_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search documents using vector similarity.
        
        Args:
            query: Search query
            workspace_id: Workspace to search in
            limit: Maximum number of results
            
        Returns:
            List of documents with their similarity scores
        """
        try:
            # Get collection
            collection = self.client.collections.get(WEAVIATE_PAGE_CLASS_NAME)
            
            # Build nearText query
            results = collection.query.near_text(
                query=query,
                filters=Filter.by_property("workspaceId").equal(workspace_id),
                limit=limit
            ).with_additional(["score"]).do()
            
            # Process results
            documents = []
            seen_doc_ids = set()
            
            for item in results.objects:
                doc_id = item.properties.get("documentId")
                if doc_id and doc_id not in seen_doc_ids:
                    documents.append({
                        "document_id": doc_id,
                        "title": item.properties.get("title"),
                        "_additional": {
                            "score": item.score
                        }
                    })
                    seen_doc_ids.add(doc_id)
            
            return documents
            
        except Exception as e:
            logger.error(f"Failed to search documents: {str(e)}")
            return []

    def delete_vectors(
        self,
        tenant_id: str,
        doc_id: str
    ) -> Dict[str, Any]:
        """Delete all vector chunks for a document.
        
        Args:
            tenant_id: ID of the tenant
            doc_id: Document UUID
            
        Returns:
            Dict with status and message
        """
        try:
            # Get collection with tenant
            page_collection = self.client.collections.get(WEAVIATE_PAGE_CLASS_NAME).with_tenant(tenant=tenant_id)
            
            # Delete all chunks for this document in one operation
            try:
                result = page_collection.data.delete_many(
                    where=Filter.by_property("documentId").equal(str(doc_id))
                )
                
                chunks_deleted = result.matches if result else 0
                
                return {
                    "status": "success",
                    "message": f"Successfully deleted {chunks_deleted} chunks",
                    "document_id": str(doc_id),
                    "chunks_deleted": chunks_deleted
                }
                
            except Exception as del_error:
                logger.error(f"Error deleting chunks: {str(del_error)}")
                return {
                    "status": "error",
                    "message": f"Failed to delete chunks: {str(del_error)}",
                    "document_id": str(doc_id),
                    "chunks_deleted": 0
                }
            
        except Exception as e:
            logger.error(f"Failed to delete vectors for document {doc_id}: {str(e)}")
            return {
                "status": "error",
                "message": f"Failed to delete vectors: {str(e)}",
                "document_id": str(doc_id),
                "chunks_deleted": 0
            }
