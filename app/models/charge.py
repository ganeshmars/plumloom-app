from datetime import datetime
from datetime import UTC
from sqlalchemy import Column, String, DateTime, Integer, Boolean, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class Charge(Base):
    __tablename__ = "charges"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('customers.id'), nullable=False)
    subscription_id = Column(String, ForeignKey("subscriptions.id"), nullable=True)
    invoice_id = Column(String, ForeignKey("invoices.id"), nullable=True)
    amount = Column(Integer, nullable=False)  # Amount in cents
    amount_refunded = Column(Integer, nullable=False, default=0)  # Amount refunded in cents
    currency = Column(String, nullable=False)
    status = Column(String, nullable=False)  # succeeded, pending, failed
    refunded = Column(Boolean, nullable=False, default=False)
    stripe_charge_id = Column(String, nullable=False)
    stripe_event_data = Column(JSONB, nullable=True, default={})
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=datetime.now(UTC))

    # Relationships
    subscription = relationship("Subscription", backref="charges")
    invoice = relationship("Invoice", backref="charges")
    refunds = relationship("Refund", back_populates="charge")

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_charges_stripe_charge_id', 'stripe_charge_id', unique=True),
        Index('ix_charges_status', 'status'),
        Index('ix_charges_user_id', 'user_id'),
        Index('ix_charges_subscription_id', 'subscription_id'),
        Index('ix_charges_invoice_id', 'invoice_id')
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "subscription_id": self.subscription_id,
            "invoice_id": self.invoice_id,
            "amount": self.amount,
            "amount_refunded": self.amount_refunded,
            "currency": self.currency,
            "status": self.status,
            "refunded": self.refunded,
            "stripe_charge_id": self.stripe_charge_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
