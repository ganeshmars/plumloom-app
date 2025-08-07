"""Service for storing document content in Weaviate."""

import logging
import time
from typing import Dict, Any, List
from uuid import UUID
import weaviate
from weaviate.classes.query import Filter
from app.core.config import get_settings
from app.services.weaviate_base_service import WeaviateService

logger = logging.getLogger(__name__)
settings = get_settings()

class VectorService(WeaviateService):
    def __init__(self):
        super().__init__()
        self.chunk_size = 10  # Characters per chunk
        self.overlap = 2  # Characters of overlap between chunks
        self.batch_size = 2  # Process 2 chunks at a time

    def store_document_sync(
        self,
        tenant_id: str,
        doc_id: UUID,
        workspace_id: UUID,
        title: str,
        content: Dict[str, Any]
    ) -> None:
        """Synchronous version of store_document for Celery tasks."""
        try:
            logger.info(f"Starting vectorization for document {doc_id} with title {title}")
            
            # Extract text from TipTap JSON
            text = self._extract_text_from_tiptap(content)
            logger.info(f"Extracted {len(text)} characters of text from TipTap JSON")
            
            # Split into chunks
            chunks = self._chunk_text(text)
            logger.info(f"Split text into {len(chunks)} chunks")
            
            # Get Page collection with tenant
            page_collection = self.client.collections.get("Page")
            page_collection = page_collection.with_tenant(tenant=tenant_id)
            logger.info(f"Got Weaviate collection for tenant {tenant_id}")
            
            # Process chunks in very small batches
            for batch_start in range(0, len(chunks), self.batch_size):
                batch_end = min(batch_start + self.batch_size, len(chunks))
                batch = chunks[batch_start:batch_end]
                
                logger.info(f"Processing batch {batch_start//self.batch_size + 1} of {(len(chunks)-1)//self.batch_size + 1} ({len(batch)} chunks)")
                
                # Add a small delay between batches to prevent memory buildup
                if batch_start > 0:
                    time.sleep(0.5)
                
                for i, chunk in enumerate(batch, start=batch_start):
                    try:
                        # Store in Weaviate
                        properties = {
                            "tenantId": tenant_id,
                            "documentId": str(doc_id),
                            "workspaceId": str(workspace_id),
                            "title": title,
                            "contentChunk": chunk,
                            "chunkOrder": i
                        }
                        
                        # Create object in Weaviate (vectorization handled by Weaviate)
                        result = page_collection.data.insert(properties)
                        
                        if not result:
                            raise RuntimeError(f"Failed to store chunk {i} in Weaviate")
                            
                        logger.info(f"Successfully stored chunk {i+1} of {len(chunks)}")
                            
                    except Exception as chunk_error:
                        logger.error(f"Failed to process chunk {i} for document {doc_id}: {str(chunk_error)}")
                        raise  # Fail fast on chunk error
            
            logger.info(f"Successfully vectorized all chunks for document: {doc_id}")
            
        except Exception as e:
            logger.error(f"Failed to vectorize document: {str(e)}")
            raise RuntimeError(f"Failed to vectorize document: {str(e)}")

    def delete_document_sync(
        self,
        tenant_id: str,
        doc_id: UUID
    ) -> None:
        """Synchronous version of delete_document for Celery tasks."""
        try:
            # Get Page collection with tenant
            page_collection = self.client.collections.get("Page")
            page_collection = page_collection.with_tenant(tenant=tenant_id)
            
            # Delete all objects with matching documentId
            result = page_collection.data.delete_many(
                where=Filter.by_property("documentId").like(doc_id)
            )
            
            if result:
                logger.info(f"Successfully deleted document {doc_id} from Weaviate")
            else:
                logger.warning(f"No chunks found for document {doc_id} in Weaviate")
                
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id} from Weaviate: {str(e)}")
            raise RuntimeError(f"Failed to delete document from Weaviate: {str(e)}")

    def _extract_text_from_tiptap(self, content: Dict[str, Any]) -> str:
        """Extract plain text from TipTap JSON content."""
        text = []
        
        def process_node(node):
            if isinstance(node, dict):
                # Handle text nodes
                if "type" in node and "text" in node:
                    text.append(node["text"])
                
                # Handle image nodes
                elif "type" in node and node["type"] == "image" and "attrs" in node:
                    attrs = node["attrs"]
                    if "alt" in attrs:
                        text.append(attrs["alt"])
                    if "title" in attrs:
                        text.append(attrs["title"])
                
                # Recursively process content
                if "content" in node and isinstance(node["content"], list):
                    for child in node["content"]:
                        process_node(child)
        
        # Start processing from the root
        process_node(content)
        return " ".join(text)

    def _chunk_text(self, text: str) -> List[str]:
        """Split text into overlapping chunks."""
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + self.chunk_size
            
            # Adjust end to not break words
            if end < len(text):
                end = text.rfind(" ", start, end)
                if end == -1:  # No space found
                    end = start + self.chunk_size
            
            chunks.append(text[start:end])
            start = end - self.overlap
        
        return chunks

    async def store_document(
        self,
        tenant_id: str,
        doc_id: UUID,
        workspace_id: UUID,
        title: str,
        content: Dict[str, Any]
    ) -> None:
        """Process document content and store in Weaviate."""
        try:
            # Extract text from TipTap JSON
            text = self._extract_text_from_tiptap(content)
            
            # Split into chunks
            chunks = self._chunk_text(text)
            
            # Get Page collection with tenant
            page_collection = self.client.collections.get("Page")
            page_collection = page_collection.with_tenant(tenant=tenant_id)
            
            # Process each chunk
            for i, chunk in enumerate(chunks):
                try:
                    # Store in Weaviate
                    properties = {
                        "tenantId": tenant_id,
                        "documentId": str(doc_id),
                        "workspaceId": str(workspace_id),
                        "title": title,
                        "contentChunk": chunk,
                        "chunkOrder": i
                    }
                    
                    # Create object in Weaviate (vectorization handled by Weaviate)
                    result = page_collection.data.insert(properties)
                    
                    if not result:
                        raise RuntimeError(f"Failed to store chunk {i} in Weaviate")
                        
                except Exception as chunk_error:
                    logger.error(f"Failed to process chunk {i} for document {doc_id}: {str(chunk_error)}")
                    # Continue with other chunks
                    continue
            
            logger.info(f"Successfully vectorized document: {doc_id}")
            
        except Exception as e:
            logger.error(f"Failed to vectorize document: {str(e)}")
            raise RuntimeError(f"Failed to vectorize document: {str(e)}")
            
    async def delete_document(
        self,
        tenant_id: str,
        doc_id: UUID
    ) -> None:
        """Delete all chunks of a document from Weaviate."""
        try:
            # Get Page collection with tenant
            page_collection = self.client.collections.get("Page")
            page_collection = page_collection.with_tenant(tenant=tenant_id)
            
            # Delete all objects with matching documentId
            result = page_collection.data.delete_many(
                where=Filter.by_property("documentId").like(doc_id)
            )
            
            if result:
                logger.info(f"Successfully deleted document {doc_id} from Weaviate")
            else:
                logger.warning(f"No chunks found for document {doc_id} in Weaviate")
                
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id} from Weaviate: {str(e)}")
            raise RuntimeError(f"Failed to delete document from Weaviate: {str(e)}")

    async def delete_document_vectors(self, doc_id: UUID) -> None:
        """Delete all vector embeddings for a document."""
        try:
            # Delete all chunks for the document
            self.weaviate_client.collections.get("DocumentContent").data.delete_many(
                where={
                    "path": ["documentId"],
                    "operator": "Equal",
                    "valueString": str(doc_id)
                }
            )
            logger.info(f"Successfully deleted vector embeddings for document: {doc_id}")
            
        except Exception as e:
            logger.error(f"Failed to delete vector embeddings: {str(e)}")
            raise RuntimeError(f"Failed to delete vector embeddings: {str(e)}")

    async def search_similar_content(
        self,
        query: str,
        workspace_id: UUID,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Search for similar content within a workspace."""
        try:
            # Generate query embedding
            query_vector = await self._generate_embeddings(query)
            
            # Search in Weaviate
            result = (
                self.weaviate_client.collections.get("DocumentContent")
                .query.near_vector(
                    vector=query_vector,
                    limit=limit,
                    return_properties=["documentId", "contentChunk", "chunkOrder"],
                    where={
                        "path": ["workspaceId"],
                        "operator": "Equal",
                        "valueString": str(workspace_id)
                    }
                )
            )
            
            return result.objects
            
        except Exception as e:
            logger.error(f"Failed to search similar content: {str(e)}")
            raise RuntimeError(f"Failed to search similar content: {str(e)}")
