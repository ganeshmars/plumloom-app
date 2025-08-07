from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Index, text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('customers.id'), nullable=False)
    stripe_payment_method_id = Column(String, nullable=False)
    type = Column(String, nullable=False)  # card, bank_account, etc.
    is_default = Column(Boolean, default=False)
    
    # Store only last 4 digits and non-sensitive payment details
    payment_metadata = Column(JSONB, nullable=False, default={})
    
    # Indexes for better query performance
    __table_args__ = (
        Index('ix_payment_methods_user_id', 'user_id'),
        Index('ix_payment_methods_stripe_payment_method_id', 'stripe_payment_method_id', unique=True),
        Index('ix_payment_methods_is_default', 'is_default'),
        Index('ix_payment_methods_type', 'type')
    )
    # Example payment_metadata for card:
    # {
    #     "last4": "4242",
    #     "brand": "visa",
    #     "exp_month": 12,
    #     "exp_year": 2025,
    #     "country": "US"
    # }

    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=datetime.utcnow)
    stripe_event_data = Column(JSONB, nullable=True, default={})

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type,
            "is_default": self.is_default,
            "metadata": self.payment_metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
