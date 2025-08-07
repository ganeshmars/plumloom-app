# app/core/weaviate_client.py
import weaviate
from weaviate.classes.init import Auth
from urllib.parse import urlparse
from app.core.logging_config import logger
# Import Weaviate exceptions for specific handling
from weaviate.exceptions import WeaviateConnectionError

from app.core.config import get_settings
# Assuming weaviate_schema.py is in the same directory (app/core)
from app.core.weaviate_schema import init_schema

settings = get_settings()

_client = None

def init_weaviate_sync():
    """
    Synchronous initialization of Weaviate client and schema using helper functions.
    """
    global _client
    if not _client:
        logger.info("Initializing Weaviate client using helper functions")
        try:
            headers = {
                "X-OpenAI-Api-Key": settings.OPENAI_API_KEY,
                "X-HuggingFace-Api-Key": settings.HUGGINGFACE_API_KEY,
                # Add other necessary headers
            }

            _client = weaviate.connect_to_weaviate_cloud(
                cluster_url=settings.WEAVIATE_URL,
                auth_credentials=Auth.api_key(settings.WEAVIATE_API_KEY),
                headers=headers,
                skip_init_checks=True
            )
        except Exception as e:
            logger.error(f"Failed to initialize Weaviate client using helper functions: {e}", exc_info=True)

        # try:
            # parsed_url = urlparse(settings.WEAVIATE_URL)
            # scheme = parsed_url.scheme.lower()
            # host = parsed_url.netloc or parsed_url.path # Host/domain part
            #
            # logger.info(f"Attempting connection. URL: {settings.WEAVIATE_URL}, Scheme: '{scheme}', Host: '{host}'")
            #
            # # --- Use Helper Functions ---
            # if scheme in ("https", "http") and ".weaviate.cloud" in host:
            #     logger.info(f"Connecting to Weaviate Cloud via helper...")
            #     _client = weaviate.connect_to_weaviate_cloud(
            #         cluster_url=settings.WEAVIATE_URL, # Use the full URL
            #         auth_credentials=Auth.api_key(settings.WEAVIATE_API_KEY),
            #         headers=headers
            #     )
            # elif scheme == "http" and host: # Example for local HTTP, adjust if needed
            #      logger.warning(f"Assuming local HTTP connection for WEAVIATE_URL: {settings.WEAVIATE_URL}")
            #      logger.info(f"Connecting to local Weaviate via helper...")
            #      local_host = host
            #      local_port = 8080 # Default local port
            #      if ':' in host:
            #          parts = host.split(':')
            #          local_host = parts[0]
            #          try:
            #              local_port = int(parts[1])
            #          except (ValueError, IndexError):
            #              logger.error(f"Could not parse port from host '{host}'. Using default {local_port}.")
            #      _client = weaviate.connect_to_local(
            #          host=local_host,
            #          port=local_port,
            #          headers=headers
            #          # Add grpc_port if needed for local gRPC
            #      )
            # # Add elif for connect_to_custom if you have a non-standard HTTP/S or local setup
            # # elif scheme in ("http", "https") and host:
            # #     logger.info(f"Connecting using connect_to_custom...")
            # #      _client = weaviate.connect_to_custom(...) # Fill parameters
            # else:
            #     # Raise error if URL format is unusable by known helpers
            #     raise ValueError(f"Could not determine connection method for WEAVIATE_URL: {settings.WEAVIATE_URL}")
            # --- End Helper Functions ---

            # --- Readiness Check ---
        #     logger.info("Checking Weaviate client readiness...")
        #     if not _client.is_ready():
        #          # If not ready, maybe try a simple operation to get a more specific error
        #          try:
        #              _ = _client.get_meta()
        #          except Exception as ready_err:
        #               logger.error(f"Readiness check failed with error: {ready_err}", exc_info=True)
        #               raise RuntimeError(f"Weaviate client failed readiness check: {ready_err}") from ready_err
        #          # If get_meta works but is_ready is False, log warning but proceed cautiously
        #          logger.warning("Weaviate client reported not ready, but get_meta succeeded.")
        #     else:
        #          logger.info("Weaviate client is ready.")
        #
        #     logger.info("Successfully connected to Weaviate")
        #
        #     # --- Schema Initialization ---
        #     init_schema(_client)
        #     logger.info("Successfully initialized/verified Weaviate schema")
        #
        # except ValueError as ve: # Catch configuration errors like invalid URL
        #      logger.critical(f"Configuration error during Weaviate client init: {ve}", exc_info=True)
        #      raise RuntimeError(f"Configuration error: {ve}") from ve
        # except WeaviateConnectionError as wce: # Catch specific Weaviate connection errors
        #      logger.critical(f"Weaviate connection failed: {wce}", exc_info=True)
        #      error_detail = f"Is Weaviate running and reachable ('{settings.WEAVIATE_URL}')? Check network, firewall, URL/API Key."
        #      raise RuntimeError(f"Failed to connect Weaviate: {wce}. {error_detail}") from wce
        # except Exception as e: # Catch other unexpected errors during init
        #     logger.critical(f"Unexpected error during Weaviate client initialization: {e}", exc_info=True)
        #     raise RuntimeError(f"Failed to connect/initialize Weaviate: {e}") from e
    else:
        logger.debug("Weaviate client already initialized.")
    return _client

# --- async init_weaviate(), close_weaviate(), get_client() remain the same ---
async def init_weaviate():
    global _client
    if not _client:
        logger.info("Async init_weaviate called, running sync initialization.")
        _client = init_weaviate_sync()

async def close_weaviate():
    global _client
    if _client:
        try:
            logger.info("Closing Weaviate connection")
            _client.close()
            logger.info("Successfully closed Weaviate connection")
        except Exception as e:
            logger.warning(f"Error during Weaviate connection closing: {e}")
        finally:
            _client = None

def get_client():
    if not _client:
        logger.warning("Attempted to get Weaviate client, but it was not initialized. Re-initializing lazily.")
        return init_weaviate_sync()
    return _client