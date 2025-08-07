from app.core.stripe_config import initialize_stripe
from app.core.logging_config import logger

class StripeService:
    def __init__(self):
        self.stripe = initialize_stripe()

    async def get_customer_by_email(self, email: str) -> dict:
        """Find a Stripe customer by email"""
        try:
            customers = self.stripe.Customer.list(email=email, limit=1)
            return customers.data[0] if customers.data else None
        except Exception as e:
            logger.error(f"Failed to search for Stripe customer by email {email}: {str(e)}")
            raise

    async def get_customer_by_metadata(self, key: str, value: str) -> dict:
        """Find a Stripe customer by metadata field"""
        try:
            customers = self.stripe.Customer.search(
                query=f'metadata[\'{key}\']:\'{value}\''
            )
            return customers.data[0] if customers.data else None
        except Exception as e:
            logger.error(f"Failed to search for Stripe customer by {key}={value}: {str(e)}")
            raise

    async def create_customer(self, email: str, user_id: str, tenant_id: str) -> dict:
        """
        Create a Stripe customer for a new user if they don't exist
        """
        try:
            # First check if customer exists by user_id in metadata
            existing_customer = await self.get_customer_by_metadata('user_id', user_id)
            if existing_customer:
                logger.info(f"Found existing Stripe customer {existing_customer['id']} for user {user_id}")
                return existing_customer

            # Then check by email as fallback
            existing_customer = await self.get_customer_by_email(email)
            if existing_customer:
                # Update existing customer with our metadata
                updated_customer = self.stripe.Customer.modify(
                    existing_customer['id'],
                    metadata={
                        "user_id": user_id,
                        "tenant_id": tenant_id
                    }
                )
                logger.info(f"Updated existing Stripe customer {updated_customer['id']} for user {user_id}")
                return updated_customer

            # Create new customer if none exists
            customer = self.stripe.Customer.create(
                email=email,
                metadata={
                    "user_id": user_id,
                    "tenant_id": tenant_id
                }
            )
            logger.info(f"Created new Stripe customer {customer['id']} for user {user_id}")
            return customer

        except Exception as e:
            logger.error(f"Failed to create/get Stripe customer for user {user_id}: {str(e)}")
            raise

    async def get_product_by_product_id(self, product_id: str) -> dict:
        """Get product details by product ID"""
        try:
            product = self.stripe.Product.retrieve(product_id)
            return product
        except Exception as e:
            logger.error(f"Failed to get product by product ID {product_id}: {str(e)}")