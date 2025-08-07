from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, Boolean, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(String, primary_key=True)
    subscription_id = Column(String, ForeignKey("subscriptions.id"), nullable=False)
    user_id = Column(String, ForeignKey('customers.id'), nullable=False)
    stripe_invoice_id = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)  # Amount in cents
    currency = Column(String, nullable=False)
    status = Column(String, nullable=False)  # draft, open, paid, void, uncollectible
    invoice_pdf_url = Column(String, nullable=True)
    hosted_invoice_url = Column(String, nullable=True)
    due_date = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    is_paid = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=datetime.utcnow)
    stripe_event_data = Column(JSONB, nullable=True, default={})

    # Relationship with Subscription
    subscription = relationship("Subscription", backref="invoices")

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_invoices_stripe_invoice_id', 'stripe_invoice_id', unique=True),
        Index('ix_invoices_status', 'status'),
        Index('ix_invoices_user_id', 'user_id'),
        Index('ix_invoices_subscription_id', 'subscription_id')
    )

    def to_dict(self):
        return {
            "id": self.id,
            "subscription_id": self.subscription_id,
            "user_id": self.user_id,
            "stripe_invoice_id": self.stripe_invoice_id,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "invoice_pdf_url": self.invoice_pdf_url,
            "hosted_invoice_url": self.hosted_invoice_url,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "is_paid": self.is_paid,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
