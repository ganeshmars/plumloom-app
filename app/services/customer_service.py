"""Service for managing customer data across Stripe and local database."""

import logging
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import stripe

from app.models.customer import Customer
from app.core.config import get_settings
from app.core.exceptions import CustomerError

logger = logging.getLogger(__name__)

class CustomerService:
    def __init__(self, db: AsyncSession):
        self.db = db
        stripe.api_key = get_settings().STRIPE_SECRET_KEY

    async def create_customer(self, user_data: Dict[str, Any]) -> Customer:
        """Create a new customer in database using combined Stripe and Descope data."""
        try:
            # Extract required fields and validate
            required_fields = ['id', 'stripe_customer_id', 'email', 'tenant_id']
            for field in required_fields:
                if not user_data.get(field):
                    raise CustomerError(f"Missing required field: {field}")
            
            # Create customer in database with all available fields
            customer = Customer(
                id=user_data['id'],
                stripe_customer_id=user_data['stripe_customer_id'],
                email=user_data['email'],
                name=user_data.get('name'),
                phone=user_data.get('phone'),
                tenant_id=user_data['tenant_id'],
                status=user_data.get('status', 'active'),
                company_name=user_data.get('company_name'),
                tax_id=user_data.get('tax_id'),
                billing_address=user_data.get('billing_address', {}),
                shipping_address=user_data.get('shipping_address', {}),
                currency=user_data.get('currency', 'usd'),
                language=user_data.get('language', 'en'),
                notification_preferences=user_data.get('notification_preferences', {}),
                stripe_event_data=user_data.get('stripe_event_data', {})
            )
            
            self.db.add(customer)
            await self.db.commit()
            await self.db.refresh(customer)
            
            logger.info(f"Successfully created customer: {customer.id}")
            return customer
            
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to create customer: {str(e)}")
            raise CustomerError(f"Failed to create customer: {str(e)}")

    async def get_customer(self, id: str, by_stripe_id: bool = False) -> Optional[Customer]:
        """Get customer by ID or Stripe customer ID.
        
        Args:
            id: The ID to look up
            by_stripe_id: If True, lookup by stripe_customer_id instead of id
        """
        try:
            if by_stripe_id:
                query = select(Customer).where(Customer.stripe_customer_id == id)
            else:
                query = select(Customer).where(Customer.id == id)
                
            result = await self.db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Failed to get customer: {str(e)}")
            raise CustomerError(f"Failed to get customer: {str(e)}")

    async def update_customer(self, id: str, user_data: Dict[str, Any]) -> Optional[Customer]:
        """Update customer in database.
        Note: id, stripe_customer_id, email, and tenant_id cannot be updated."""
        try:
            # Get existing customer
            customer = await self.get_customer(id)
            if not customer:
                return None

            # Fields that cannot be updated
            protected_fields = {'id', 'stripe_customer_id', 'email', 'tenant_id'}
            
            # Update all fields from user_data except protected ones
            for field, value in user_data.items():
                if field not in protected_fields:
                    setattr(customer, field, value)

            await self.db.commit()
            await self.db.refresh(customer)

            return customer

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update customer: {str(e)}")
            raise CustomerError(f"Failed to update customer: {str(e)}")

    async def delete_customer(self, id: str) -> bool:
        """Delete customer from local database."""
        try:
            customer = await self.get_customer(id)
            if not customer:
                return False

            # Delete from database
            await self.db.delete(customer)
            await self.db.commit()

            return True

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to delete customer: {str(e)}")
            raise CustomerError(f"Failed to delete customer: {str(e)}")
