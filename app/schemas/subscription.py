from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel

class SubscriptionBase(BaseModel):
    user_id: str
    stripe_customer_id: str
    stripe_subscription_id: str
    plan_id: str
    status: str
    current_period_start: datetime
    current_period_end: datetime
    price_id: str
    price_amount: int
    currency: str
    interval: str
    is_trial: bool
    features: str

class SubscriptionCreate(SubscriptionBase):
    pass

class SubscriptionUpdate(BaseModel):
    status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    cancel_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    features: Optional[str] = None

class SubscriptionResponse(SubscriptionBase):
    id: str
    cancel_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class SubscriptionFeatures(BaseModel):
    api_calls_limit: int
    storage_limit_gb: float
    max_users: int
    support_level: str
    custom_features: Dict[str, Any]
