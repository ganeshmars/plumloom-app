from datetime import datetime
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.core.stripe_config import initialize_stripe
from app.models.payment import Payment

class PaymentService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.stripe = initialize_stripe()

    async def create_or_update_payment(self, payment_data: dict) -> Payment:
        """Create or update a payment record from Stripe webhook data"""
        logger = logging.getLogger(__name__)
        try:
            logger.info(f"Processing payment data: {payment_data.get('id')}")
            
            try:
                # Initialize payment data
                payment = {
                    "id": payment_data["id"],
                    "amount": payment_data["amount_total"],
                    "currency": payment_data["currency"],
                    "status": payment_data["payment_status"],
                    "payment_intent": payment_data.get("payment_intent"),
                    "created_at": datetime.fromtimestamp(payment_data["created"]),
                    "payment_metadata": payment_data.get("metadata", {})
                }
                
                # For guest checkouts or test events, use customer_details if available
                customer_details = payment_data.get("customer_details", {})
                if customer_details:
                    payment["user_id"] = "guest_" + customer_details.get("email", "unknown").replace("@", "_at_")
                    payment["stripe_customer_id"] = None
                    logger.info(f"Processing guest payment for: {payment['user_id']}")
                else:
                    # Fallback for test events
                    payment["user_id"] = "test_user"
                    payment["stripe_customer_id"] = None
                    logger.info("Processing test payment")
                
                # If customer is available, try to get customer information
                if payment_data.get("customer"):
                    try:
                        customer = self.stripe.Customer.retrieve(payment_data["customer"])
                        logger.info(f"Retrieved customer data: {customer.id}")
                        
                        if customer.metadata.get("user_id"):
                            payment["user_id"] = customer.metadata.get("user_id")
                            payment["stripe_customer_id"] = payment_data["customer"]
                            logger.info(f"Using authenticated user: {payment['user_id']}")
                    except Exception as e:
                        logger.warning(f"Error retrieving customer data: {str(e)}")
            except KeyError as e:
                logger.error(f"Missing required field in payment data: {str(e)}")
                raise ValueError(f"Missing required field in payment data: {str(e)}")
            logger.info(f"Prepared payment data for database: {payment['id']}")

            # Check if payment exists
            stmt = select(Payment).where(Payment.id == payment_data["id"])
            result = await self.db.execute(stmt)
            existing_payment = result.scalar_one_or_none()

            try:
                if existing_payment:
                    logger.info(f"Updating existing payment: {existing_payment.id}")
                    await self.db.execute(
                        update(Payment)
                        .where(Payment.id == payment_data["id"])
                        .values(**payment)
                    )
                    await self.db.commit()
                    updated_payment = await self.get_payment(payment_data["id"])
                    logger.info(f"Successfully updated payment: {updated_payment.id}")
                    return updated_payment
                else:
                    logger.info(f"Creating new payment: {payment['id']}")
                    new_payment = Payment(**payment)
                    self.db.add(new_payment)
                    await self.db.commit()
                    await self.db.refresh(new_payment)
                    logger.info(f"Successfully created new payment: {new_payment.id}")
                    return new_payment
            except SQLAlchemyError as e:
                logger.error(f"Database error while saving payment: {str(e)}")
                await self.db.rollback()
                raise

        except Exception as e:
            logger.error(f"Error processing payment data: {str(e)}")
            raise

    async def get_payment(self, payment_id: str) -> Payment:
        """Get a payment by ID"""
        stmt = select(Payment).where(Payment.id == payment_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_payments(self, user_id: str) -> list[Payment]:
        """Get all payments for a user"""
        stmt = select(Payment).where(Payment.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalars().all()
