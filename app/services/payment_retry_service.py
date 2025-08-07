from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core.stripe_config import initialize_stripe, with_stripe_retry
from app.models.subscription import Subscription
from app.services.invoice_service import InvoiceService

class PaymentRetryService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.stripe = initialize_stripe()
        self.invoice_service = InvoiceService(db_session)

    @with_stripe_retry(max_retries=3)
    async def retry_failed_payment(self, invoice_id: str) -> bool:
        """
        Retry a failed payment with exponential backoff.
        Returns True if payment succeeded, False otherwise.
        """
        try:
            invoice = self.stripe.Invoice.retrieve(invoice_id)
            if invoice.status != 'open':
                return False

            # Attempt to pay the invoice
            paid_invoice = self.stripe.Invoice.pay(invoice_id)
            
            # Update invoice in our database
            await self.invoice_service.create_or_update_invoice({"invoice": paid_invoice})
            
            return paid_invoice.status == 'paid'
        except stripe.error.CardError:
            # Payment failed due to card issues, mark for retry
            return False

    @with_stripe_retry(max_retries=3)
    async def handle_payment_failure(self, subscription_id: str) -> None:
        """
        Handle payment failure by implementing a retry strategy:
        1. Immediate retry
        2. After 3 days
        3. After 7 days
        Then mark subscription as unpaid if all retries fail.
        """
        subscription = await self.get_subscription(subscription_id)
        if not subscription:
            return

        stripe_sub = self.stripe.Subscription.retrieve(subscription.stripe_subscription_id)
        latest_invoice = self.stripe.Invoice.retrieve(stripe_sub.latest_invoice)

        # Check if we should retry based on the number of attempts
        retry_count = latest_invoice.attempt_count
        if retry_count >= 3:
            # Mark subscription as unpaid after all retries
            self.stripe.Subscription.modify(
                subscription.stripe_subscription_id,
                metadata={"payment_retry_status": "failed_all_retries"}
            )
            return

        # Calculate next retry date based on attempt count
        retry_delays = [timedelta(days=0), timedelta(days=3), timedelta(days=7)]
        next_retry = datetime.now() + retry_delays[retry_count]

        # Schedule next retry
        self.stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            metadata={
                "payment_retry_status": "scheduled",
                "next_retry_date": next_retry.isoformat()
            }
        )

    async def get_subscription(self, subscription_id: str) -> Subscription:
        """Get subscription from database."""
        stmt = select(Subscription).where(Subscription.id == subscription_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
