from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Index, JSON, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base

class Customer(Base):
    __tablename__ = "customers"

    # Primary Fields
    id = Column(String, primary_key=True)
    stripe_customer_id = Column(String, unique=True, nullable=False)
    email = Column(String, nullable=False)
    name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    status = Column(String, nullable=False, default="active")  # active, suspended, deleted
    
    # Business Fields
    company_name = Column(String, nullable=True)
    tax_id = Column(String, nullable=True)
    tenant_id = Column(String, nullable=False)  # For multi-tenant support
    
    # Address Information
    billing_address = Column(JSONB, nullable=True, default={})
    shipping_address = Column(JSONB, nullable=True, default={})
    
    # Preferences
    currency = Column(String, nullable=False, default="usd")
    language = Column(String, nullable=False, default="en")
    notification_preferences = Column(JSONB, nullable=True, default={})
    
    # System Fields
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
    stripe_event_data = Column(JSONB, nullable=True, default={}, comment='Complete JSON data from Stripe events, including customer creation and metadata')

    # Relationships
    subscriptions = relationship("Subscription", back_populates="customer",
                               cascade="all, delete-orphan",
                               primaryjoin="and_(Customer.id==Subscription.user_id, Subscription.status!='deleted')")
    
    payment_methods = relationship("PaymentMethod", backref="customer",
                                 cascade="all, delete-orphan",
                                 primaryjoin="Customer.id==PaymentMethod.user_id")
    
    invoices = relationship("Invoice", backref="customer",
                          cascade="all, delete-orphan",
                          primaryjoin="Customer.id==Invoice.user_id")
    
    payments = relationship("Payment", backref="customer",
                          cascade="all, delete-orphan",
                          primaryjoin="Customer.id==Payment.user_id")
    
    refunds = relationship("Refund", backref="customer",
                         cascade="all, delete-orphan",
                         primaryjoin="Customer.id==Refund.user_id")

    # Indexes for better query performance
    __table_args__ = (
        Index('ix_customers_stripe_customer_id', 'stripe_customer_id'),
        Index('ix_customers_email', 'email'),
        Index('ix_customers_tenant_id', 'tenant_id'),
        Index('ix_customers_status', 'status'),
        # Composite index for tenant-specific email uniqueness
        Index('ix_customers_tenant_email_unique', 'tenant_id', 'email', unique=True)
    )

    def to_dict(self):
        """Convert customer object to dictionary."""
        return {
            "id": self.id,
            "stripe_customer_id": self.stripe_customer_id,
            "email": self.email,
            "name": self.name,
            "phone": self.phone,
            "status": self.status,
            "company_name": self.company_name,
            "tax_id": self.tax_id,
            "tenant_id": self.tenant_id,
            "billing_address": self.billing_address,
            "shipping_address": self.shipping_address,
            "currency": self.currency,
            "language": self.language,
            "notification_preferences": self.notification_preferences,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None
        }

    def __repr__(self):
        return f"<Customer(id={self.id}, email={self.email}, tenant_id={self.tenant_id})>"
