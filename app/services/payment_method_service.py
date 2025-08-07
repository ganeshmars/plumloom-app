from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, and_

import stripe
from app.models.payment_method import PaymentMethod
from app.core.stripe_config import initialize_stripe
from app.core.config import get_settings

settings = get_settings()

class PaymentMethodService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.stripe = initialize_stripe()

    async def create_setup_intent(self, user_id: str) -> dict:
        """Create a SetupIntent for securely collecting payment method details"""
        try:
            # Get or create customer
            customers = self.stripe.Customer.list(limit=1, email=user_id)
            if customers.data:
                customer = customers.data[0]
                # Update metadata if it doesn't exist
                if not customer.metadata.get('user_id'):
                    customer = self.stripe.Customer.modify(
                        customer.id,
                        metadata={'user_id': user_id}
                    )
            else:
                customer = self.stripe.Customer.create(
                    email=user_id,
                    metadata={'user_id': user_id}
                )

            # Create SetupIntent
            setup_intent = self.stripe.SetupIntent.create(
                customer=customer.id,
                usage='off_session'  # Allow using this payment method for recurring payments
            )

            return {
                "client_secret": setup_intent.client_secret,
                "customer_id": customer.id
            }
        except stripe.error.StripeError as e:
            raise ValueError(f"Error creating setup intent: {str(e)}")

    async def add_payment_method(self, payment_method_data: dict) -> PaymentMethod:
        """Store payment method details after successful setup"""
        stripe_pm = self.stripe.PaymentMethod.retrieve(payment_method_data["stripe_payment_method_id"])
        
        # Extract safe payment metadata based on payment method type
        payment_metadata = {}
        if stripe_pm.type == "card":
            payment_metadata = {
                "last4": stripe_pm.card.last4,
                "brand": stripe_pm.card.brand,
                "exp_month": stripe_pm.card.exp_month,
                "exp_year": stripe_pm.card.exp_year,
                "country": stripe_pm.card.country
            }
        elif stripe_pm.type == "bank_account":
            payment_metadata = {
                "last4": stripe_pm.bank_account.last4,
                "bank_name": stripe_pm.bank_account.bank_name,
                "country": stripe_pm.bank_account.country
            }

        payment_method = PaymentMethod(
            id=stripe_pm.id,
            user_id=payment_method_data["user_id"],
            stripe_payment_method_id=stripe_pm.id,
            type=stripe_pm.type,
            is_default=payment_method_data.get("is_default", False),
            payment_metadata=payment_metadata
        )

        # If this is the default payment method, unset other defaults
        if payment_method.is_default:
            await self.db.execute(
                update(PaymentMethod)
                .where(
                    and_(
                        PaymentMethod.user_id == payment_method_data["user_id"],
                        PaymentMethod.is_default == True
                    )
                )
                .values(is_default=False)
            )

        self.db.add(payment_method)
        await self.db.commit()
        await self.db.refresh(payment_method)
        return payment_method

    async def list_payment_methods(self, user_id: str) -> list[PaymentMethod]:
        """Get all payment methods for a user"""
        stmt = select(PaymentMethod).where(PaymentMethod.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_payment_method(self, payment_method_id: str, user_id: str) -> PaymentMethod:
        """Get a specific payment method"""
        stmt = select(PaymentMethod).where(
            and_(
                PaymentMethod.id == payment_method_id,
                PaymentMethod.user_id == user_id
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def set_default_payment_method(self, payment_method_id: str, user_id: str) -> PaymentMethod:
        """Set a payment method as default"""
        # First, unset all default payment methods for the user
        await self.db.execute(
            update(PaymentMethod)
            .where(
                and_(
                    PaymentMethod.user_id == user_id,
                    PaymentMethod.is_default == True
                )
            )
            .values(is_default=False)
        )

        # Set the new default
        await self.db.execute(
            update(PaymentMethod)
            .where(
                and_(
                    PaymentMethod.id == payment_method_id,
                    PaymentMethod.user_id == user_id
                )
            )
            .values(is_default=True)
        )
        await self.db.commit()

        return await self.get_payment_method(payment_method_id, user_id)

    async def delete_payment_method(self, payment_method_id: str, user_id: str) -> None:
        """Delete a payment method"""
        payment_method = await self.get_payment_method(payment_method_id, user_id)
        if not payment_method:
            raise ValueError("Payment method not found")

        # Delete from Stripe
        try:
            self.stripe.PaymentMethod.detach(payment_method.stripe_payment_method_id)
        except stripe.error.StripeError as e:
            raise ValueError(f"Error deleting payment method from Stripe: {str(e)}")

        # Delete from our database
        await self.db.delete(payment_method)
        await self.db.commit()
