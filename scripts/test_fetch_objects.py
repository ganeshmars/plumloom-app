#!/usr/bin/env python
"""
Test script for WeaviateRepositorySync.fetch_objects method
Specifically tests the error handling for tenant not found scenarios
"""

import sys
import logging
import uuid
from weaviate.classes.query import Filter

# Add the app directory to the path so we can import from app
sys.path.append('/home/ganesh/plumloom-app')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('test_fetch_objects')

# Import the repository class
from app.services.weaviate.repository_sync import WeaviateRepositorySync
from app.services.weaviate.exceptions import VectorStoreOperationError

def test_fetch_objects():
    """Test the fetch_objects method with various scenarios"""
    
    # Create a repository instance
    repo = WeaviateRepositorySync()
    
    # Test cases to run
    test_cases = [
        {
            "name": "Existing tenant with valid filter",
            "collection": "Page",
            "tenant_id": "test_tenant",  # Replace with a known existing tenant
            "filters": Filter.by_property("documentId").equal(str(uuid.uuid4())),
            "expected_error": False
        },
        {
            "name": "Non-existent tenant",
            "collection": "Page",
            "tenant_id": f"nonexistent_tenant_{uuid.uuid4()}",  # Generate a random tenant ID that shouldn't exist
            "filters": Filter.by_property("documentId").equal(str(uuid.uuid4())),
            "expected_error": False  # Should not error, should return empty list
        },
        {
            "name": "Invalid collection name",
            "collection": "NonExistentCollection",
            "tenant_id": "test_tenant",
            "filters": Filter.by_property("documentId").equal(str(uuid.uuid4())),
            "expected_error": True
        }
    ]
    
    # Run the test cases
    for tc in test_cases:
        logger.info(f"Running test case: {tc['name']}")
        try:
            result = repo.fetch_objects(
                collection_name=tc['collection'],
                tenant_id=tc['tenant_id'],
                filters=tc['filters'],
                limit=10
            )
            
            logger.info(f"Test case '{tc['name']}' succeeded with result type: {type(result)}")
            logger.info(f"Result count: {len(result)}")
            
            if tc['expected_error']:
                logger.error(f"Test case '{tc['name']}' should have raised an error but didn't")
            
        except VectorStoreOperationError as e:
            if tc['expected_error']:
                logger.info(f"Test case '{tc['name']}' raised expected error: {str(e)}")
            else:
                logger.error(f"Test case '{tc['name']}' failed with unexpected error: {str(e)}")
        except Exception as e:
            logger.error(f"Test case '{tc['name']}' failed with unexpected exception: {str(e)}")

def test_specific_tenant_not_found():
    """Test specifically for the tenant not found scenario"""
    
    repo = WeaviateRepositorySync()
    
    # Generate a random tenant ID that shouldn't exist
    nonexistent_tenant = f"Ganesh_nonexistent_{uuid.uuid4()}"
    
    logger.info(f"Testing fetch_objects with nonexistent tenant: {nonexistent_tenant}")
    
    try:
        result = repo.fetch_objects(
            collection_name="Page",
            tenant_id=nonexistent_tenant,
            filters=Filter.by_property("documentId").equal(str(uuid.uuid4())),
            limit=10
        )
        
        logger.info(f"Successfully handled nonexistent tenant. Result type: {type(result)}")
        logger.info(f"Result count: {len(result)}")
        
    except Exception as e:
        logger.error(f"Failed to handle nonexistent tenant: {str(e)}")

if __name__ == "__main__":
    logger.info("Starting fetch_objects test script")
    
    # Run the general test cases
    test_fetch_objects()
    
    # Run the specific tenant not found test
    test_specific_tenant_not_found()
    
    logger.info("Test script completed")
