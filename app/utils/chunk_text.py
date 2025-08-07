from typing import List
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.schema import Document

from app.core.logging_config import logger


def divide_text_into_chunks(text: str, chunk_size: int = 100, overlap: int = 20) -> List[str]:
    """Split text into overlapping chunks."""
    # Clean up the text first
    text = " ".join(text.split())  # Normalize whitespace
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        # If we're not at the end of text, adjust to not break words
        if end < len(text):
            # Try to find a sentence boundary first
            sentence_end = -1
            for punct in [".", "!", "?", "\n"]:
                pos = text.rfind(punct, start, end)
                if pos > sentence_end:
                    sentence_end = pos + 1
            
            if sentence_end > start:
                end = sentence_end
            else:
                # If no sentence boundary, try to break at word boundary
                word_end = text.rfind(" ", start, end)
                if word_end > start:
                    end = word_end
        
        # Get the chunk and clean it
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        # Move start position for next chunk
        start = max(start + 1, end - overlap)
    
    return chunks

def chunk_text(text: str, chunk_size: int = 100, overlap: int = 20) -> List[str]:
    # """
    # Split text into overlapping chunks based on character count.
    # Attempts to split at spaces near the target chunk size.
    # """
    # if not text:
    #     return []
    #
    # chunks = []
    # text_length = len(text)
    # start = 0
    #
    # while start < text_length:
    #     # Calculate potential end index
    #     end = start + chunk_size
    #
    #     # If end is beyond the text length, take the rest of the text
    #     if end >= text_length:
    #         chunks.append(text[start:])
    #         break # Exit loop
    #
    #     # Find the last space before or at the potential end index
    #     # Search window: [start, end]
    #     split_pos = text.rfind(" ", start, end + 1) # Include end index in search
    #
    #     # If no space found in the desired range, force split at chunk_size
    #     if split_pos == -1 or split_pos <= start:
    #             split_pos = end
    #
    #     # Add the chunk from start to split_pos
    #     chunks.append(text[start:split_pos].strip())
    #
    #     # Calculate the next start position with overlap
    #     # Move start forward, ensuring overlap doesn't push it beyond split_pos
    #     next_start = split_pos - overlap
    #     # Prevent start from going backward or staying put if overlap is large/no space found
    #     start = max(start + 1, next_start if next_start > start else split_pos)
    #
    #
    # # Filter out any potentially empty chunks resulting from splitting logic
    # return [chunk for chunk in chunks if chunk]

    doc = Document(text=text)
    node_parser = MarkdownNodeParser()
    nodes = node_parser.get_nodes_from_documents([doc])
    chunks = [node.get_content(metadata_mode='none') for node in nodes]
    return chunks