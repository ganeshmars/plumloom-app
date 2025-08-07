from app.core.celery_app import celery_app
from app.core.logging_config import logger

@celery_app.task(bind=True, name='example.task')
def example_task(self, *args, **kwargs):
    """Example task to verify Celery is working"""
    logger.info(f"Running example task with args: {args} kwargs: {kwargs}")
    return {"status": "success", "message": "Task completed successfully"}
