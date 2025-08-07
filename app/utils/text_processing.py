import hashlib

def generate_chunk_fingerprint(chunk_text: str) -> str:
    """Generate a unique fingerprint for a chunk of text."""
    # Normalize text (lowercase, strip whitespace) before hashing for consistency
    normalized_text = " ".join(chunk_text.strip().lower().split())
    return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()