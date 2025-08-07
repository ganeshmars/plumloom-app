from datetime import datetime
from datetime import UTC
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update

from app.models.charge import Charge
from app.core.stripe_config import initialize_stripe

class ChargeService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.stripe = initialize_stripe()

    async def update_charge_status(self, data: dict) -> Charge:
        """Update charge status and refund details"""
        charge = data.get("charge")
        event = data.get("event")
        
        if not charge:
            raise ValueError("No charge data provided")
            
        # Get existing charge record
        stmt = select(Charge).where(Charge.stripe_charge_id == charge["id"])
        result = await self.db.execute(stmt)
        db_charge = result.scalar_one_or_none()
        
        if not db_charge:
            raise ValueError(f"Charge not found: {charge['id']}")
            
        # Update charge details
        charge_data = {
            "status": charge["status"],
            "refunded": charge["refunded"],
            "amount_refunded": charge["amount_refunded"],
            "stripe_event_data": event,  # Store the complete event data
            "updated_at": datetime.now(UTC)
        }
        
        # Update charge record
        await self.db.execute(
            update(Charge)
            .where(Charge.stripe_charge_id == charge["id"])
            .values(**charge_data)
        )
        await self.db.commit()
        
        # Refresh and return updated charge
        await self.db.refresh(db_charge)
        return db_charge

    async def get_charge(self, charge_id: str) -> Charge:
        """Get a charge by ID"""
        stmt = select(Charge).where(Charge.stripe_charge_id == charge_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
