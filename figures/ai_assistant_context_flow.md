# AI Assistant Context Flow

```mermaid
flowchart TD
    APIRequest[API Request] --> HasUploadedDoc{Has Uploaded Document?}
    
    HasUploadedDoc -->|Yes| UploadedDoc[Uploaded Document Context]
    HasUploadedDoc -->|No| KnowledgeBaseSelection[Knowledge Base Selection]
    
    KnowledgeBaseSelection --> Default[Default Context]
    KnowledgeBaseSelection --> Template[Template Context]
    KnowledgeBaseSelection --> Page[Page Context]
    KnowledgeBaseSelection --> Workspace[Workspace Context]
    
    UploadedDoc --> DocTypeDecision{Document Type?}
    DocTypeDecision -->|CSV| CSVAnalysis[CSV Data Analysis]
    DocTypeDecision -->|PDF/TXT/etc.| DocAnalysis[Document Content Analysis]
    
    CSVAnalysis --> Visualizations[Data Visualizations & Insights]
    DocAnalysis --> DocResponses[Document-Specific Responses]
    
    Default --> DefaultResponses[General AI Responses]
    
    Template --> TemplateCompletion[Template Completion]
    WorkspaceContent[Workspace Content] --> |Enriches| TemplateCompletion
    
    Page --> PageFocus[Page Focus]
    WorkspaceContent --> |Enriches| PageFocus
    PageFocus --> EnhancedPageResponses[Enhanced Page-Specific Responses]
    
    Workspace --> WorkspaceAnalysis[Workspace Analysis]
    WorkspaceAnalysis --> WorkspaceResponses[Workspace-Aware Responses]
    
    subgraph "Context Hierarchy"
        PrimaryContext[Primary Context: Uploaded Document]
        KnowledgeBases[Knowledge Bases: Default/Workspace/Page/Template]
    end
    
    subgraph "Document Types"
        CSVType[CSV Files]
        OtherDocTypes[PDF/TXT/Other Documents]
    end
    
    subgraph "Secondary Context Components"
        WorkspacePages[Workspace Pages]
        WorkspaceSubpages[Workspace Sub-pages]
        TemplateStructures[Template Structures]
        PageContents[Page Contents]
    end
```

## Context Model Description

The AI Assistant operates with a hierarchical context model:

### Primary Context: Uploaded Document

When a document is uploaded, it becomes the primary context:

1. **Uploaded Document Context**
   - Decision point: System determines if the uploaded file is CSV or another document type (PDF, TXT, etc.)
   - For CSV files:
     - Primary: Uploaded CSV file data
     - Behavior: AI Assistant analyzes the CSV data for visualizations and insights
   - For other document types:
     - Primary: Uploaded document content (PDF, TXT, etc.)
     - Behavior: AI Assistant processes and responds to queries about the specific document

### Knowledge Bases (When No Document is Uploaded)

If no document is uploaded, one of these becomes the active context:

2. **Default Context**
   - Behavior: AI Assistant provides general responses without specific contextual information

3. **Template Context**
   - Context: Selected template (e.g., "Product Metrics Template")
   - Enriched by: Workspace content when available
   - Behavior: AI Assistant guides template completion

4. **Page Context**
   - Context: Selected page being viewed/edited
   - Enriched by: Broader workspace content when available
   - Behavior: AI Assistant focuses on the page while leveraging related workspace information

5. **Workspace Context**
   - Context: Entire workspace content (pages, sub-pages)
   - Behavior: AI Assistant provides responses with awareness of the complete workspace knowledge repository
