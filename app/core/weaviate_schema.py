# app/core/weaviate_schema.py

from weaviate import WeaviateClient
from weaviate.classes import config as wc
from weaviate.exceptions import UnexpectedStatusCodeError
from app.core.logging_config import logger

# --- Schema Definition ---
# Define all your collections and their properties here.
# This makes it easier to manage and add new collections.
DEFINED_SCHEMAS = {
    "Page": {
        "properties": [
            wc.Property(name="tenantId", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=True, skip_vectorization=True, tokenization=wc.Tokenization.WORD),
            wc.Property(name="workspaceId", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=False, skip_vectorization=True),
            wc.Property(name="documentId", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=True, skip_vectorization=True),
            wc.Property(name="title", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=True, skip_vectorization=False),
            wc.Property(name="contentChunk", data_type=wc.DataType.TEXT, index_filterable=False, index_searchable=True, skip_vectorization=False),
            wc.Property(name="chunkOrder", data_type=wc.DataType.INT, index_filterable=True, index_searchable=False, skip_vectorization=True),
            wc.Property(name="chunkFingerprint", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=False, skip_vectorization=True), # Consider filtering if needed for updates
        ],
        "vectorizer_config": wc.Configure.Vectorizer.text2vec_weaviate(model="Snowflake/snowflake-arctic-embed-m-v1.5", vectorize_collection_name=True),
        "multi_tenancy_config": wc.Configure.multi_tenancy(enabled=True, auto_tenant_creation=True, auto_tenant_activation=True),
        "vector_index_config": wc.Configure.VectorIndex.hnsw(),
    },
    "Document": {
         "properties": [
            wc.Property(name="tenantId", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=True, skip_vectorization=True, tokenization=wc.Tokenization.WORD),
            wc.Property(name="workspaceId", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=False, skip_vectorization=True),
            wc.Property(name="documentId", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=True, skip_vectorization=True),
            wc.Property(name="title", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=True, skip_vectorization=False),
            wc.Property(name="contentChunk", data_type=wc.DataType.TEXT, index_filterable=False, index_searchable=True, skip_vectorization=False),
            wc.Property(name="chunkOrder", data_type=wc.DataType.INT, index_filterable=True, index_searchable=False, skip_vectorization=True),
            wc.Property(name="chunkFingerprint", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=False, skip_vectorization=True), # Consider filtering if needed for updates
            wc.Property(name="chatSessionId", data_type=wc.DataType.TEXT, index_filterable=True, index_searchable=False, skip_vectorization=True), # Added property
        ],
        "vectorizer_config": wc.Configure.Vectorizer.text2vec_weaviate(model="Snowflake/snowflake-arctic-embed-m-v1.5", vectorize_collection_name=True),
        "multi_tenancy_config": wc.Configure.multi_tenancy(enabled=True, auto_tenant_creation=True, auto_tenant_activation=True),
        "vector_index_config": wc.Configure.VectorIndex.hnsw(),
    }
    # Add new collections here in the future
}

def check_property_exists(collection_schema, property_name: str) -> bool:
    """Checks if a property exists in the fetched schema config."""
    if not collection_schema or not collection_schema.properties:
        return False
    return any(prop.name == property_name for prop in collection_schema.properties)

def init_schema(client: WeaviateClient):
    """
    Initializes or updates Weaviate schemas based on DEFINED_SCHEMAS.
    Creates collections if they don't exist.
    Adds missing properties to existing collections.
    """
    logger.info("Starting Weaviate schema initialization/update...")
    existing_collections = {col.name for col in client.collections.list_all().values()}
    logger.info(f"Existing collections: {existing_collections}")

    for name, schema_config in DEFINED_SCHEMAS.items():
        try:
            if name not in existing_collections:
                logger.info(f"Collection '{name}' does not exist. Creating...")
                client.collections.create(
                    name=name,
                    properties=schema_config.get("properties", []),
                    vectorizer_config=schema_config.get("vectorizer_config"),
                    multi_tenancy_config=schema_config.get("multi_tenancy_config"),
                    vector_index_config=schema_config.get("vector_index_config"),
                    # Add other configurations like replication, sharding if needed
                )
                logger.info(f"Collection '{name}' created successfully.")
            else:
                logger.debug(f"Collection '{name}' already exists. Checking for property updates...")
                collection = client.collections.get(name)
                current_schema = collection.config.get()

                # Check for and add missing properties
                defined_properties = schema_config.get("properties", [])
                for prop_to_add in defined_properties:
                    if not check_property_exists(current_schema, prop_to_add.name):
                        logger.info(f"Adding missing property '{prop_to_add.name}' to collection '{name}'...")
                        try:
                            collection.config.add_property(prop_to_add)
                            logger.info(f"Successfully added property '{prop_to_add.name}' to '{name}'.")
                        except UnexpectedStatusCodeError as e:
                            # Handle potential conflicts (e.g., property exists with different config)
                            logger.error(f"Failed to add property '{prop_to_add.name}' to '{name}': {e}. Status code: {e.status_code}. Message: {e.message}")
                        except Exception as e:
                            logger.error(f"Error adding property '{prop_to_add.name}' to '{name}': {e}")
                    else:
                        logger.debug(f"Property '{prop_to_add.name}' already exists in '{name}'.")

                # Note: Updating existing property configurations or vectorizer settings
                # often requires recreating the collection. This logic only handles *adding* properties.
                # More complex migrations would need a dedicated strategy.

        except Exception as e:
            logger.error(f"Failed to process schema for collection '{name}': {e}")
            # Decide if you want to raise an error and stop startup or just log and continue
            # raise RuntimeError(f"Schema initialization failed for '{name}': {e}") from e

    logger.info("Weaviate schema initialization/update complete.")
    return True