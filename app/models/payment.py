from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base

class Payment(Base):
    __tablename__ = "payments"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('customers.id'), nullable=False)
    stripe_customer_id = Column(String, nullable=True)
    amount = Column(Integer, nullable=False)  # Amount in cents
    currency = Column(String, nullable=False)
    status = Column(String, nullable=False)
    payment_intent = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=datetime.utcnow)
    payment_metadata = Column(JSON, nullable=True, default={})
    stripe_event_data = Column(JSONB, nullable=True, default={})

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_payments_payment_intent', 'payment_intent', unique=True),
        Index('ix_payments_status', 'status'),
        Index('ix_payments_user_id', 'user_id'),
        Index('ix_payments_stripe_customer_id', 'stripe_customer_id')
    )

    def __repr__(self):
        return f"<Payment(id={self.id}, user_id={self.user_id}, amount={self.amount}, status={self.status})>"
