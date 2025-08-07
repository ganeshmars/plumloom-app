# app/services/weaviate/exceptions.py

class VectorStoreConfigError(Exception):
    """Error related to Weaviate configuration or schema issues."""
    pass

class VectorStoreOperationError(Exception):
    """Error during a Weaviate CRUD or search operation."""
    pass

class VectorStoreNotFoundError(VectorStoreOperationError):
    """Indicates an object was not found."""
    pass

class VectorStoreTenantNotFoundError(VectorStoreOperationError):
    """Indicates a tenant was not found in a specific collection during an operation."""
    pass