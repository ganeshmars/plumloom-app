from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class Refund(Base):
    __tablename__ = "refunds"

    id = Column(String, primary_key=True)
    subscription_id = Column(String, ForeignKey("subscriptions.id"), nullable=False)
    user_id = Column(String, ForeignKey('customers.id'), nullable=False)
    amount = Column(Integer, nullable=False)  # Amount in cents
    currency = Column(String, nullable=False)
    status = Column(String, nullable=False)  # succeeded, pending, failed, canceled
    reason = Column(String, nullable=True)
    stripe_refund_id = Column(String, nullable=False)
    stripe_charge_id = Column(String, nullable=False)
    charge_id = Column(String, ForeignKey('charges.id'))
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=datetime.utcnow)
    stripe_event_data = Column(JSONB, nullable=True, default={})

    # Relationship with Subscription
    subscription = relationship("Subscription", backref="refunds")
    charge = relationship("Charge", back_populates="refunds")

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_refunds_stripe_refund_id', 'stripe_refund_id', unique=True),
        Index('ix_refunds_status', 'status'),
        Index('ix_refunds_user_id', 'user_id'),
        Index('ix_refunds_subscription_id', 'subscription_id'),
        Index('ix_refunds_charge_id', 'charge_id'),
        Index('ix_refunds_stripe_charge_id', 'stripe_charge_id')

    )

    def to_dict(self):
        return {
            "id": self.id,
            "subscription_id": self.subscription_id,
            "user_id": self.user_id,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "reason": self.reason,
            "stripe_refund_id": self.stripe_refund_id,
            "charge_id": self.charge_id,
            "stripe_charge_id": self.stripe_charge_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
