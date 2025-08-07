# app/core/database.py
import asyncio
import sys
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.engine import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import asynccontextmanager # Import asynccontextmanager

from app.core.config import get_settings
from app.core.logging_config import logger
from app.models.base import Base

settings = get_settings()

# Format: postgresql+asyncpg://user:password@host:port/dbname
ASYNC_DATABASE_URL = f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"

# Synchronous database URL for Celery tasks
DATABASE_URL = f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"

# Async engine for FastAPI
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_pre_ping=True,  # Enable connection health checks
    pool_size=10,        # Increased pool size for potential concurrent tasks + requests
    max_overflow=20      # Allow more overflow
)

# Sync engine for Celery
sync_engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10
)

# Async session factory for FastAPI
async_session_factory = sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Sync session factory for Celery
SessionLocal = sessionmaker(
    sync_engine,
    class_=Session,
    expire_on_commit=False
)

# FastAPI Dependency (Handles commit/rollback automatically for requests)
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database sessions within FastAPI requests."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit() # Commit successful request operations
        except Exception as e:
            logger.error(f"Error in FastAPI request DB session: {e}", exc_info=True)
            await session.rollback() # Rollback on error
            raise # Re-raise the exception for FastAPI to handle
        finally:
            # Closing is handled by the context manager `async with async_session_factory()`
            pass # No explicit close needed here

# --- NEW: Context Manager for Background Tasks (Celery) ---
@asynccontextmanager
async def db_session_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Provides an async database session for use outside FastAPI requests (e.g., Celery tasks).
    Caller is responsible for commit and rollback. Session is automatically closed.
    """
    session: AsyncSession = async_session_factory()
    try:
        yield session
        # Note: Commit/Rollback MUST be handled by the caller within the task logic
    except Exception as e:
        logger.error(f"Error within db_session_context: {e}", exc_info=True)
        await session.rollback() # Ensure rollback on unexpected errors within the context itself
        raise # Re-raise the exception
    finally:
        await session.close() # Ensure the session is always closed

# --- Existing functions ---

async def wait_for_db(max_retries=5, retry_interval=5):
    """Wait for database to become available"""
    for retry in range(max_retries):
        try:
            conn = await async_engine.connect()
            await conn.close()
            logger.info("Successfully connected to the database")
            return True
        except Exception as e:
            logger.warning(f"Database connection attempt {retry + 1}/{max_retries} failed: {str(e)}")
            if retry < max_retries - 1:
                logger.info(f"Retrying in {retry_interval} seconds...")
                await asyncio.sleep(retry_interval)
            else:
                logger.error("Max retries reached. Could not connect to the database.")
                return False
    return False

async def init_db():
    """
    Initialize database connections.
    Ensures the database is reachable but does NOT create tables.
    Tables should only be created through Alembic migrations.
    """
    if not await wait_for_db():
        logger.error("Could not establish database connection after multiple retries. Exiting.")
        sys.exit(1) # Exit the application if DB is unavailable

    logger.info("Database connection established.")
    # We no longer automatically create tables here.
    # Tables should be created only through migrations using Alembic.
    # The following code has been commented out:
    # try:
    #     async with async_engine.begin() as conn:
    #         await conn.run_sync(Base.metadata.create_all)
    #     logger.info("Database tables checked/created successfully.")
    # except Exception as e:
    #     logger.error(f"Failed during database table initialization: {str(e)}", exc_info=True)
    #     sys.exit(1)

async def close_db():
    """Close database connections"""
    try:
        await async_engine.dispose()
        sync_engine.dispose()
        logger.info("Database connection pool disposed successfully")
    except Exception as e:
        logger.error(f"Error during database shutdown: {str(e)}", exc_info=True)