from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, HttpUrl

class ProductResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    images: List[str] = []
    active: bool
    metadata: Dict[str, str] = {}
    unit_label: Optional[str] = None
    url: Optional[str] = None

class CreateCheckoutRequest(BaseModel):
    product_id: str
    success_url: HttpUrl
    cancel_url: HttpUrl
    mode: str = "subscription"  # "subscription" or "payment"
    country_code: Optional[str] = None  # Two-letter country code (ISO 3166-1 alpha-2)

class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str

class CustomerResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    metadata: Dict[str, str] = {}
    subscription_status: Optional[str] = None

class CreateCustomerRequest(BaseModel):
    email: str
    name: Optional[str] = None
    metadata: Dict[str, str] = {}

class RefundRequest(BaseModel):
    subscription_id: str
    amount: Optional[int] = None  # Amount in cents, if None will refund full amount
    reason: Optional[str] = None  # duplicate, fraudulent, requested_by_customer

class RefundResponse(BaseModel):
    id: str
    amount: int
    currency: str
    status: str
    reason: Optional[str] = None

class UpdateSubscriptionRequest(BaseModel):
    subscription_id: str
    new_price_id: str
    prorate: bool = True  # Whether to prorate the subscription change
    preview_proration: bool = False  # If true, only calculate proration without making changes

class UpdateSubscriptionResponse(BaseModel):
    subscription_id: str
    new_price_id: str
    proration_date: datetime
    prorated_amount: int  # Amount in cents
    currency: str
    is_preview: bool  # Whether this is a preview or actual change

class InvoiceResponse(BaseModel):
    id: str
    subscription_id: str
    amount: int
    currency: str
    status: str
    invoice_pdf: Optional[str] = None
    hosted_invoice_url: Optional[str] = None
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    period_start: datetime
    period_end: datetime
    is_paid: bool

class UpcomingInvoiceResponse(BaseModel):
    amount_due: int
    currency: str
    period_start: datetime
    period_end: datetime
    next_payment_attempt: Optional[datetime] = None

class SetupIntentResponse(BaseModel):
    client_secret: str
    customer_id: str

class PaymentMethodMetadata(BaseModel):
    last4: str
    brand: Optional[str] = None  # For cards
    exp_month: Optional[int] = None  # For cards
    exp_year: Optional[int] = None  # For cards
    bank_name: Optional[str] = None  # For bank accounts
    country: Optional[str] = None

class PaymentMethodResponse(BaseModel):
    id: str
    type: str  # card, bank_account, etc.
    is_default: bool
    metadata: PaymentMethodMetadata
    created_at: datetime
    updated_at: datetime

class CountryResponse(BaseModel):
    code: str
    name: str

class CountrySpecResponse(BaseModel):
    id: str  # Two-letter country code
    default_currency: str
    supported_payment_currencies: List[str]
    supported_payment_methods: List[str]
    supported_transfer_countries: List[str]

class PaymentResponse(BaseModel):
    id: str
    user_id: str
    amount: int
    currency: str
    status: str
    payment_intent: Optional[str] = None
    created_at: datetime
    payment_metadata: Dict[str, str] = {}
