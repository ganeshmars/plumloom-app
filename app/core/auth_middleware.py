from fastapi import Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime
from app.core.logging_config import logger
from app.core.auth import validate_session
from app.models.customer import Customer
from app.services.customer_service import CustomerService

async def ensure_customer_exists(user_data: dict, db: AsyncSession) -> Optional[Customer]:
    """
    Ensure a customer exists in both Stripe and our database for the authenticated user.
    
    Args:
        user_data: Validated session data from Descope
        db: Database session
    """
    try:
        # Extract user info
        user_id = user_data.get('id')
        email = user_data.get('email')
        tenant_id = user_data.get('userTenants', [{}])[0].get('tenantId') if user_data.get('userTenants') else None
        
        logger.info(f"Ensuring customer exists - ID: {user_id}, Email: {email}, Tenant: {tenant_id}")

        if not user_id or not email:
            logger.error(f"Missing required user data - ID: {user_id}, Email: {email}")
            raise HTTPException(status_code=400, detail="Missing required user data (ID or email)")
            
        if not tenant_id:
            logger.warning(f"No tenant ID found for user {user_id}")
            tenant_id = None

        # Check if customer exists in our database
        customer_service = CustomerService(db)
        customer = await customer_service.get_customer(user_id)

        # If customer exists in our database, return it
        if customer:
            logger.info(f"Found existing customer in database: {customer.id}")
            return customer

        # Create new customer using CustomerService with user data
        customer = await customer_service.create_customer(user_data)
        
        logger.info(f"Successfully created new customer: {customer.id}")
        return customer

    except Exception as e:
        logger.error(f"Failed to ensure customer exists: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to ensure customer exists: {str(e)}"
        )
