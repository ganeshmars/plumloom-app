from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
import uuid
import time
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from decouple import config

from app.core.logging_config import logger, app_logger
from app.core.auth import AuthError, validate_session
from app.core.database import init_db, close_db
from app.core.weaviate_client import init_weaviate, close_weaviate
from app.core.langfuse_config import langfuse_client
from app.api.v1 import auth as auth_routes
from app.api.v1 import stripe as stripe_routes
from app.api.v1 import subscription as subscription_routes
from app.api.v1 import documents as document_routes
from app.api.v1 import workspaces as workspace_routes
from app.api.v1 import uploaded_documents as uploaded_document_routes
# from app.api.v1.test import test_vector_service as test_vector_service_routes
from app.api.v1 import utils as utils_routes
from app.api.v1 import user_preference as user_preference_routes
from app.api.v1 import icons as icon_routes
from app.api.v1 import tiptap as tiptap_routes
from app.api.v1 import templates as template_routes
from app.api.v1 import chat as chat_router
from app.api.v1 import chat_v2 as chat_v2_router
from app.api.v1 import vector_ops as vector_ops_router
from app.api.v1 import chat_conversations as chat_conversations_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up the application")
    await init_db()
    await init_weaviate()
    yield
    # Shutdown
    logger.info("Shutting down the application")
    await close_db()
    await close_weaviate()
    # Flush Langfuse data on shutdown
    try:
        logger.info("Flushing Langfuse client before shutdown...")
        # Note: flush() is synchronous. If shutdown time is critical, consider
        # using langfuse.shutdown() in a separate thread or background task
        # initiated earlier, but flush() is generally sufficient.
        langfuse_client.flush()
        logger.info("Langfuse client flushed.")
    except ImportError:
         logger.warning("Langfuse client not found during shutdown (ImportError).")
    except AttributeError:
         logger.warning("Langfuse client object not found or doesn't have flush method.")
    except Exception as e:
        logger.error(f"Error flushing Langfuse client during shutdown: {e}", exc_info=True)
    app_logger.cleanup()


app = FastAPI(
    title=config("APP_NAME", default="AI Chat Application"),
    debug=config("DEBUG", default=False, cast=bool),
    version="1.0.0",
    lifespan=lifespan  # Register the lifespan handler
)

@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError):
    logger.warning(f"Authentication error: {exc.detail} for request: {request.url}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# Handler for input validation errors (e.g., Pydantic model validation)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    processed_errors = jsonable_encoder(exc.errors())
    logger.warning(
        f"Validation error (processed): {processed_errors} for request: {request.url.path}"
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": processed_errors},
    )

# Generic handler for standard HTTP exceptions (e.g., 404 Not Found, raised manually)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error(f"HTTP Exception: {exc.status_code} - {exc.detail} for request: {request.url}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )

# Catch-all handler for any unhandled exceptions (Internal Server Errors)
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, 'request_id', 'N/A') # Get request ID if set by middleware
    logger.exception(f"Unhandled exception - ID: {request_id} - Error: {exc} for request: {request.url}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred."},
    )

# --- CORS Middleware ---
def parse_cors_origins(v):
    try:
        # First try to parse as JSON array
        return eval(v)
    except (SyntaxError, ValueError):
        # If that fails, try comma-separated format
        return [origin.strip() for origin in v.split(',')]

origins = config("CORS_ORIGINS", default='["http://localhost:3000"]', cast=parse_cors_origins)
logger.info(f"Configured CORS with origins: {origins}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Middleware for request logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start_time = time.time()

    # Log request start more concisely
    logger.info(f"RID:{request_id} --> {request.method} {request.url.path}")
    if request.query_params:
        logger.debug(f"RID:{request_id} Query Params: {request.query_params}")

    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000 # Use milliseconds
        # Log response status and duration
        logger.info(
            f"RID:{request_id} <-- {response.status_code} ({process_time:.2f}ms)"
        )
        # Add request ID to response headers for client-side correlation
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as e:
        # This catch block is less critical now with the generic_exception_handler,
        # but can remain as a last resort safety net specifically within the middleware.
        # The generic_exception_handler will provide the 500 response.
        process_time = (time.time() - start_time) * 1000
        logger.error(f"RID:{request_id} !!! Exception during request processing ({process_time:.2f}ms): {e}", exc_info=True)
        # Re-raise the exception so the generic handler catches it and returns the 500 response
        raise e


# --- API Router Inclusion ---
app.include_router(auth_routes.router, prefix="/api/v1")
app.include_router(chat_router.router, prefix="/api/v1")
app.include_router(chat_v2_router.router, prefix="/api/v1")
app.include_router(chat_conversations_router.router, prefix="/api/v1")
app.include_router(stripe_routes.router, prefix="/api/v1")
app.include_router(document_routes.router, prefix="/api/v1")
app.include_router(subscription_routes.router, prefix="/api/v1")
app.include_router(icon_routes.router, prefix="/api/v1")
# app.include_router(test_vector_service_routes.router, prefix="/api/v1")
app.include_router(template_routes.router, prefix="/api/v1")
app.include_router(tiptap_routes.router, prefix="/api/v1")
app.include_router(vector_ops_router.router, prefix="/api/v1")
app.include_router(user_preference_routes.router, prefix="/api/v1")
app.include_router(utils_routes.router, prefix="/api/v1")
app.include_router(uploaded_document_routes.router, prefix="/api/v1")
app.include_router(workspace_routes.router, prefix="/api/v1")



# --- Root Endpoint ---
@app.get("/", tags=["Health Check"])
async def root(user: dict = Depends(validate_session)):
    # Optional: Add more details like service status (DB, Weaviate, LLM?)
    logger.info(f"Health check endpoint called by user: {user.get('email', 'Unknown')}")
    return {
        "message": "Welcome to AI Chat Application API",
        "version": config("APP_VERSION", default="1.0.0"), # Use config for version
        "status": "healthy",
        "user_email": user.get('email') # Return only necessary user info
    }