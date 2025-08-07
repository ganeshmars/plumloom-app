"""
Constants used throughout the application.
"""

# Google Cloud Storage constants
GCS_UPLOADED_DOCUMENTS_BUCKET = "plumloom-uploaded-documents"
GCS_STORAGE_BUCKET = "plumloom-storage"
GCS_DOCUMENTS_BUCKET = "plumloom-documents"

# Chat message feedback options
class FeedbackOptions:
    # Standard feedback types that match FeedbackTypeEnum in the schema
    PERFECT_DETAILS = "perfect_details"
    PERFECT_CITATIONS = "perfect_citations"
    RELEVANT_CITATIONS = "relevant_citations"
    RELEVANT_DETAILS = "relevant_details"
    EASY_TO_READ = "easy_to_read"
    ACCURATE = "accurate"
    
    # Negative feedback types
    DIFFICULT_TO_READ = "difficult_to_read"
    MISSING_DETAILS = "missing_details"
    IRRELEVANT_CITATIONS = "irrelevant_citations"
    IRRELEVANT_DETAILS = "irrelevant_details"
    INACCURATE = "inaccurate"
    MISSING_CITATIONS = "missing_citations"
    
    # Display names for the feedback types (for frontend display)
    DISPLAY_NAMES = {
        PERFECT_DETAILS: "Perfect details",
        PERFECT_CITATIONS: "Perfect citations",
        RELEVANT_CITATIONS: "Relevant citations",
        RELEVANT_DETAILS: "Relevant details",
        EASY_TO_READ: "Easy to read",
        ACCURATE: "Accurate based on document",
        
        # Negative feedback display names
        DIFFICULT_TO_READ: "Difficult to read",
        MISSING_DETAILS: "Missing details",
        IRRELEVANT_CITATIONS: "Irrelevant citations",
        IRRELEVANT_DETAILS: "Irrelevant details",
        INACCURATE: "Isn't accurate based on document",
        MISSING_CITATIONS: "Missing citations"
    }
    
    # List of all available feedback types
    ALL_TYPES = [
        PERFECT_DETAILS,
        PERFECT_CITATIONS,
        RELEVANT_CITATIONS,
        RELEVANT_DETAILS,
        EASY_TO_READ,
        ACCURATE,
        
        # Add negative feedback types
        DIFFICULT_TO_READ,
        MISSING_DETAILS,
        IRRELEVANT_CITATIONS,
        IRRELEVANT_DETAILS,
        INACCURATE,
        MISSING_CITATIONS
    ]