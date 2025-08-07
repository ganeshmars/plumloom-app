from datetime import datetime
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update

from app.models.invoice import Invoice
from app.models.subscription import Subscription
from app.core.stripe_config import initialize_stripe
from app.core.config import get_settings

settings = get_settings()

class InvoiceService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.stripe = initialize_stripe()

    async def create_or_update_invoice(self, stripe_invoice) -> Invoice:
        """Create or update an invoice record from Stripe invoice object"""
        # Get subscription details
        subscription = None
        
        # First try to find subscription by subscription ID
        if stripe_invoice.subscription:
            # Start a transaction to ensure subscription is committed
            async with self.db.begin():
                stmt = select(Subscription).where(
                    Subscription.stripe_subscription_id == stripe_invoice.subscription
                ).with_for_update()
                result = await self.db.execute(stmt)
                subscription = result.scalar_one_or_none()
                
                # If no subscription found, try to find one by customer ID
                if not subscription:
                    stmt = select(Subscription).where(
                        Subscription.stripe_customer_id == stripe_invoice.customer
                    ).order_by(Subscription.created_at.desc()).with_for_update()
                    result = await self.db.execute(stmt)
                    subscription = result.scalar_one_or_none()
                
                if not subscription:
                    raise ValueError(f"No subscription found for invoice {stripe_invoice.id} and customer {stripe_invoice.customer}")

        # Safely get period data from the first line item
        period_start = None
        period_end = None
        if (getattr(stripe_invoice, 'lines', None) and 
            getattr(stripe_invoice.lines, 'data', None) and 
            len(stripe_invoice.lines.data) > 0 and 
            getattr(stripe_invoice.lines.data[0], 'period', None)):
            period = stripe_invoice.lines.data[0].period
            if getattr(period, 'start', None):
                period_start = datetime.fromtimestamp(period.start)
            if getattr(period, 'end', None):
                period_end = datetime.fromtimestamp(period.end)

        # Safely get paid_at timestamp
        paid_at = None
        if (stripe_invoice.status == 'paid' and 
            getattr(stripe_invoice, 'status_transitions', None) and 
            getattr(stripe_invoice.status_transitions, 'paid_at', None)):
            paid_at = datetime.fromtimestamp(stripe_invoice.status_transitions.paid_at)

        invoice = {
            "id": str(uuid.uuid4()),
            "subscription_id": subscription.id,
            "user_id": subscription.user_id,
            "stripe_invoice_id": stripe_invoice.id,
            "amount": getattr(stripe_invoice, 'total', 0) or 0,  # Convert None to 0
            "currency": getattr(stripe_invoice, 'currency', 'usd'),  # Default to USD
            "status": getattr(stripe_invoice, 'status', 'unknown'),
            "invoice_pdf_url": getattr(stripe_invoice, 'invoice_pdf', None),
            "hosted_invoice_url": getattr(stripe_invoice, 'hosted_invoice_url', None),
            "due_date": datetime.fromtimestamp(stripe_invoice.due_date) if getattr(stripe_invoice, 'due_date', None) else None,
            "paid_at": paid_at,
            "period_start": period_start,
            "period_end": period_end,
            "is_paid": bool(getattr(stripe_invoice, 'paid', False)),
            "stripe_event_data": stripe_invoice
        }

        # Check if invoice exists
        stmt = select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice.id)
        result = await self.db.execute(stmt)
        existing_invoice = result.scalar_one_or_none()

        if existing_invoice:
            # Update existing invoice
            await self.db.execute(
                update(Invoice)
                .where(Invoice.stripe_invoice_id == stripe_invoice.id)
                .values(**invoice)
            )
            await self.db.commit()
            return await self.get_invoice(stripe_invoice.id)
        else:
            # Create new invoice
            new_invoice = Invoice(**invoice)
            self.db.add(new_invoice)
            await self.db.commit()
            await self.db.refresh(new_invoice)
            return new_invoice

    async def get_invoice(self, invoice_id: str) -> Invoice:
        """Get an invoice by ID"""
        stmt = select(Invoice).where(Invoice.stripe_invoice_id == invoice_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_invoices(self, user_id: str, limit: int = 10) -> list[Invoice]:
        """Get all invoices for a user"""
        stmt = select(Invoice).where(Invoice.user_id == user_id).order_by(Invoice.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_subscription_invoices(self, subscription_id: str) -> list[Invoice]:
        """Get all invoices for a subscription"""
        stmt = select(Invoice).where(Invoice.subscription_id == subscription_id).order_by(Invoice.created_at.desc())
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_upcoming_invoice(self, subscription_id: str) -> dict:
        """Get the upcoming invoice for a subscription"""
        subscription = await self.db.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        subscription = subscription.scalar_one_or_none()
        
        if not subscription:
            raise ValueError("Subscription not found")

        upcoming = self.stripe.Invoice.upcoming(
            customer=subscription.stripe_customer_id,
            subscription=subscription.stripe_subscription_id
        )

        return {
            "amount_due": upcoming.amount_due,
            "currency": upcoming.currency,
            "period_start": datetime.fromtimestamp(upcoming.period_start),
            "period_end": datetime.fromtimestamp(upcoming.period_end),
            "next_payment_attempt": datetime.fromtimestamp(upcoming.next_payment_attempt) if upcoming.next_payment_attempt else None
        }

    async def update_invoice_status(self, invoice_id: str, status_data: dict) -> Invoice:
        """Update invoice status and related fields
        
        Args:
            invoice_id: The Stripe invoice ID
            status_data: Dictionary containing status update data including:
                - status: New invoice status
                - paid_at: When the invoice was paid (for paid status)
                - amount_paid: Amount that was paid
                - amount_remaining: Amount still remaining to be paid
                - event_data: Complete Stripe event data
        
        Returns:
            Updated Invoice object
        """
        # Get existing invoice
        stmt = select(Invoice).where(Invoice.stripe_invoice_id == invoice_id)
        result = await self.db.execute(stmt)
        invoice = result.scalar_one_or_none()
        
        if not invoice:
            raise ValueError(f"Invoice not found: {invoice_id}")
            
        # Update invoice fields
        update_data = {
            "status": status_data.get("status"),
            "is_paid": status_data.get("status") == "paid",
            "paid_at": status_data.get("paid_at"),
            "stripe_event_data": status_data.get("event_data"),
            "updated_at": datetime.now(UTC)
        }
        
        # Update invoice record
        await self.db.execute(
            update(Invoice)
            .where(Invoice.stripe_invoice_id == invoice_id)
            .values(**update_data)
        )
        await self.db.commit()
        
        # Refresh and return updated invoice
        await self.db.refresh(invoice)
        return invoice

    async def send_invoice_notification(self, invoice: Invoice) -> None:
        """Send notification about invoice status"""
        # This is a placeholder for your notification system
        # You would typically integrate with your email service or notification system here
        if invoice.status == "open":
            # New invoice ready for payment
            notification_data = {
                "type": "invoice.created",
                "user_id": invoice.user_id,
                "amount": invoice.amount,
                "currency": invoice.currency,
                "due_date": invoice.due_date,
                "invoice_url": invoice.hosted_invoice_url,
                "pdf_url": invoice.invoice_pdf
            }
        elif invoice.status == "paid":
            # Payment received
            notification_data = {
                "type": "invoice.paid",
                "user_id": invoice.user_id,
                "amount": invoice.amount,
                "currency": invoice.currency,
                "paid_at": invoice.paid_at,
                "invoice_url": invoice.hosted_invoice_url
            }
        elif invoice.status == "payment_failed":
            # Payment failed
            notification_data = {
                "type": "invoice.payment_failed",
                "user_id": invoice.user_id,
                "amount": invoice.amount,
                "currency": invoice.currency,
                "due_date": invoice.due_date,
                "invoice_url": invoice.hosted_invoice_url
            }
        
        # Send notification (implement your notification logic here)
        # await notification_service.send(notification_data)
