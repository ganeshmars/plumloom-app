import logging
import sys
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from decouple import config

# Create logs directory if it doesn't exist
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

# Log file configuration
LOG_FILENAME = f"app_{datetime.now().strftime('%Y%m%d')}.log"
LOG_FILE = LOGS_DIR / LOG_FILENAME

class AppLogger:
    _instance = None
    _logger = None
    _handlers = []

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AppLogger, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if self._logger is None:
            self._setup_logging()

    def _setup_logging(self):
        # Get log level from environment variable, default to INFO
        log_level = config("LOG_LEVEL", default="INFO").upper()
        
        # Create formatter
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        # Configure root logger
        self._logger = logging.getLogger()
        self._logger.setLevel(log_level)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self._logger.addHandler(console_handler)
        self._handlers.append(console_handler)

        # File handler with rotation
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=10485760,  # 10MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        self._logger.addHandler(file_handler)
        self._handlers.append(file_handler)

        uvicorn_logger = logging.getLogger("uvicorn")
        uvicorn_logger.setLevel(logging.INFO)
        uvicorn_logger.propagate = True

        uvicorn_access_logger = logging.getLogger("uvicorn.access")
        uvicorn_access_logger.setLevel(logging.INFO)
        uvicorn_access_logger.propagate = True

        uvicorn_error_logger = logging.getLogger("uvicorn.error")
        uvicorn_error_logger.setLevel(logging.INFO)
        uvicorn_error_logger.propagate = True

    def get_logger(self):
        return self._logger

    def cleanup(self):
        """Clean up logging handlers"""
        for handler in self._handlers:
            handler.close()
            self._logger.removeHandler(handler)
        self._handlers.clear()

# Create logger instance
app_logger = AppLogger()
logger = app_logger.get_logger()
