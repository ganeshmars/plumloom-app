import logging
import uvicorn
from app.core.logging_config import AppLogger

if __name__ == "__main__":
    # Initialize our custom logger
    logger = AppLogger().get_logger()
    
    # Configure Uvicorn logging
    # log_config = uvicorn.config.LOGGING_CONFIG
    # log_config["formatters"]["default"]["fmt"] = "%(asctime)s - uvicorn - %(levelname)s - %(message)s"
    # log_config["formatters"]["access"]["fmt"] = "%(asctime)s - uvicorn - %(levelname)s - %(message)s"
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=5000,
        reload=True,
        reload_excludes=["**/node_modules/*", "frontend/*"],
        log_config=None
    )
