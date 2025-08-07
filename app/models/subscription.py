from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Integer, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('customers.id'), nullable=False)
    stripe_customer_id = Column(String, nullable=False)
    stripe_subscription_id = Column(String, nullable=False)
    plan_id = Column(String, nullable=False)  # Reference to Stripe product/price ID
    status = Column(String, nullable=False)  # active, cancelled, past_due, etc.
    current_period_start = Column(DateTime, nullable=False)
    current_period_end = Column(DateTime, nullable=False)
    cancel_at = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    cancellation_reason = Column(String, nullable=True)
    cancellation_feedback = Column(String, nullable=True)
    cancellation_comment = Column(String, nullable=True)
    trial_start = Column(DateTime, nullable=True)
    trial_end = Column(DateTime, nullable=True)
    price_id = Column(String, nullable=False)
    price_amount = Column(Integer, nullable=False)  # Amount in cents
    currency = Column(String, nullable=False)
    interval = Column(String, nullable=False)  # month, year, etc.
    is_trial = Column(Boolean, default=False)
    features = Column(String, nullable=True)  # JSON string of enabled features
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=datetime.utcnow)
    stripe_event_data = Column(JSONB, nullable=True, default={})

    # Relationships
    customer = relationship("Customer", back_populates="subscriptions")
    
    # Indexes for better query performance
    __table_args__ = (
        Index('ix_subscriptions_stripe_subscription_id', 'stripe_subscription_id', unique=True),
        Index('ix_subscriptions_status', 'status'),
        Index('ix_subscriptions_user_id', 'user_id')
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "stripe_customer_id": self.stripe_customer_id,
            "stripe_subscription_id": self.stripe_subscription_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "current_period_start": self.current_period_start.isoformat(),
            "current_period_end": self.current_period_end.isoformat(),
            "cancel_at": self.cancel_at.isoformat() if self.cancel_at else None,
            "canceled_at": self.canceled_at.isoformat() if self.canceled_at else None,
            "trial_start": self.trial_start.isoformat() if self.trial_start else None,
            "trial_end": self.trial_end.isoformat() if self.trial_end else None,
            "price_id": self.price_id,
            "price_amount": self.price_amount,
            "currency": self.currency,
            "interval": self.interval,
            "is_trial": self.is_trial,
            "features": self.features,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
