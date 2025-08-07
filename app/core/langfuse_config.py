from langfuse import Langfuse
from app.core.config import get_settings
from app.core.logging_config import logger

settings = get_settings()

# Initialize Langfuse client singleton
langfuse_client = Langfuse(
    secret_key=settings.LANGFUSE_SECRET_KEY,
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    host=settings.LANGFUSE_HOST,
    # debug=True # Optional: Enable for detailed logs
)

# Dependency for FastAPI
def get_langfuse():
    if langfuse_client is None:
         # Log a warning if accessed when initialization failed
         logger.warning("Attempting to use Langfuse client, but it failed to initialize.")
         # Optionally raise an error or return a dummy object that does nothing
         # raise HTTPException(status_code=503, detail="Observability service (Langfuse) is unavailable.")
    return langfuse_client