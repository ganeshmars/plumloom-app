from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update

from app.models.refund import Refund
from app.models.subscription import Subscription
from app.core.stripe_config import initialize_stripe
from app.models.charge import Charge

class RefundService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.stripe = initialize_stripe()

    async def create_or_update_refund(self, refund_data: dict) -> Refund:
        """Create or update a refund record from Stripe webhook data"""
        stripe_refund = refund_data.get("refund", refund_data)
        charge = self.stripe.Charge.retrieve(stripe_refund["charge"])
        
        # Get subscription ID from charge metadata or charge object
        subscription_id = (
            charge.metadata.get("subscription_id") or 
            charge.invoice.subscription if charge.get("invoice") else None
        )

        if subscription_id:
            # Get subscription details
            stmt = select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
            result = await self.db.execute(stmt)
            subscription = result.scalar_one_or_none()

            if not subscription:
                raise ValueError(f"Subscription not found for ID: {subscription_id}")

            # Get charge record from database
            stmt = select(Charge).where(Charge.stripe_charge_id == stripe_refund["charge"])
            result = await self.db.execute(stmt)
            charge_record = result.scalar_one_or_none()

            # Set charge_id to None if no charge record is found
            charge_id = charge_record.id if charge_record else None
            
            refund = {
                "id": stripe_refund["id"],
                "subscription_id": subscription.id,
                "user_id": subscription.user_id,
                "charge_id": charge_id,  # Add the charge_id from our database or None
                "amount": stripe_refund["amount"],
                "currency": stripe_refund["currency"],
                "status": stripe_refund["status"],
                "reason": stripe_refund.get("reason"),
                "stripe_refund_id": stripe_refund["id"],
                "stripe_charge_id": stripe_refund["charge"],
                "stripe_event_data": refund_data  # Store the complete event data
            }

            # Check if refund exists
            stmt = select(Refund).where(Refund.stripe_refund_id == stripe_refund["id"])
            result = await self.db.execute(stmt)
            existing_refund = result.scalar_one_or_none()

            if existing_refund:
                # Update existing refund
                await self.db.execute(
                    update(Refund)
                    .where(Refund.stripe_refund_id == stripe_refund["id"])
                    .values(**refund)
                )
                await self.db.commit()
                return await self.get_refund(stripe_refund["id"])
            else:
                # Create new refund
                new_refund = Refund(**refund)
                self.db.add(new_refund)
                await self.db.commit()
                await self.db.refresh(new_refund)
                return new_refund

    async def get_refund(self, refund_id: str) -> Refund:
        """Get a refund by ID"""
        stmt = select(Refund).where(Refund.stripe_refund_id == refund_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_subscription_refunds(self, subscription_id: str) -> list[Refund]:
        """Get all refunds for a subscription"""
        stmt = select(Refund).where(Refund.subscription_id == subscription_id)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def process_refund(self, charge_id: str, amount: int = None, reason: str = None) -> Refund:
        """Process a new refund"""
        try:
            # Create refund in Stripe
            refund_params = {
                "charge": charge_id,
                "reason": reason
            }
            if amount:
                refund_params["amount"] = amount

            stripe_refund = self.stripe.Refund.create(**refund_params)
            
            # Create refund record in our database
            return await self.create_or_update_refund({"refund": stripe_refund})
        except Exception as e:
            raise ValueError(f"Failed to process refund: {str(e)}")
