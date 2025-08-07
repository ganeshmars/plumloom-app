# app/core/celery_app.py
from celery import Celery
from celery.schedules import crontab
from kombu import Queue, Exchange
import os
from datetime import timedelta
from app.core.logging_config import logger

# Redis connection URL
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = os.getenv('REDIS_PORT', '6379')
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"

# Initialize Celery
celery_app = Celery(
    'plumloom',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        # Include modules containing task definitions based on new file names
        'app.tasks.document.sync_documents', # Contains the coordinator task
        'app.tasks.document.update_hierarchy', # Contains hierarchy update tasks
        'app.tasks.template.update_template', # Contains template update tasks
        'app.tasks.document.process_uploaded_document', # Contains document upload processing tasks
        'app.tasks.tasks', # Contains workspace and document deletion tasks
    ]
)

# --- Queue Definitions ---
TASK_QUEUES = {
    'doc_persistence': {
        'exchange': 'doc_persistence',
        'routing_key': 'doc_persistence',
        'queue_arguments': {'x-max-priority': 10}
    },
    'doc_hierarchy': {
        'exchange': 'doc_hierarchy',
        'routing_key': 'doc_hierarchy',
        'queue_arguments': {'x-max-priority': 10}
    },
    'template_updation': {
        'exchange': 'template_updation',
        'routing_key': 'template_updation',
        'queue_arguments': {'x-max-priority': 10}
    },
    'doc_processing': {
        'exchange': 'doc_processing',
        'routing_key': 'doc_processing',
        'queue_arguments': {'x-max-priority': 5}
    },
    'operations': {
        'exchange': 'operations',
        'routing_key': 'operations',
        'queue_arguments': {'x-max-priority': 7}
    }
}

# --- Celery Configurations ---
celery_app.conf.update(
    # Task settings
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=45 * 60,  # 45 minutes overall limit (adjust)
    task_soft_time_limit=40 * 60, # 40 minutes warning (adjust)
    task_acks_late=True,      # Acknowledge after task runs (ensure tasks are idempotent)

    # Queue settings (dynamically created from the simplified TASK_QUEUES)
    task_queues=[Queue(name,
                       Exchange(config['exchange'], type='direct'),
                       routing_key=config['routing_key'],
                       queue_arguments=config.get('queue_arguments', {}))
                 for name, config in TASK_QUEUES.items()],
    task_default_queue='operations',
    task_default_exchange='operations',
    task_default_routing_key='operations',

    # Routing settings (Updated for new file/task names and simplified queues)
    task_routes={
        'app.tasks.document.sync_documents.sync_documents': {'queue': 'doc_persistence'},
        'app.tasks.document.sync_documents.sync_all_tiptap_documents': {'queue': 'doc_persistence'},
        'app.tasks.document.update_hierarchy.process_hierarchy_update': {'queue': 'doc_hierarchy'},
        'app.tasks.template.update_template.process_template_update': {'queue': 'template_updation'},
        'app.tasks.document.process_uploaded_document.process_uploaded_document': {'queue': 'doc_processing'},
        'app.tasks.tasks.delete_workspace_resources': {'queue': 'operations'},
        'app.tasks.tasks.delete_document_resources': {'queue': 'operations'},
    },

    # Task result settings
    result_expires=timedelta(days=2),
    task_store_errors_even_if_ignored=True, # Keep storing errors

    # Broker settings
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=None,

    # Monitoring (useful for Flower)
    worker_send_task_events=True,
    task_send_sent_event=True,

    # Beat schedule (Only include relevant scheduled tasks)
    beat_schedule={
        'sync-all-tiptap-documents': {
            'task': 'app.tasks.document.sync_all_tiptap_documents',
            'schedule': timedelta(minutes=90),  # Run every 90 minutes
            'options': {
                'queue': 'doc_persistence',
                'priority': 10
            }
        },
    }
)

# Log configuration summary
logger.info(f"Celery ({celery_app.main}) configured with:")
logger.info(f"  Broker: {celery_app.conf.broker_url}")
logger.info(f"  Backend: {celery_app.conf.result_backend}")
logger.info(f"  Include: {celery_app.conf.include}")
logger.info(f"  Queues: {list(TASK_QUEUES.keys())}") # Should show ['doc_persistence', 'doc_processing']
logger.info(f"  Task Routes Defined: {len(celery_app.conf.task_routes)} routes")
logger.info(f"  Beat Schedule Enabled: {len(celery_app.conf.beat_schedule)} schedules") # Should show 1 schedule now
