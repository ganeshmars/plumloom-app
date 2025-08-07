import sys
import time
import random
from functools import wraps
import stripe
from typing import Any, Callable, TypeVar, cast
from app.core.config import get_settings

T = TypeVar('T')

def with_stripe_retry(max_retries: int = 3, initial_wait: float = 0.5) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator for retrying Stripe API calls with exponential backoff."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            retries = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except stripe.error.RateLimitError as e:
                    if retries >= max_retries:
                        raise
                    wait_time = (initial_wait * (2 ** retries)) + (random.uniform(0, 0.1))
                    time.sleep(wait_time)
                    retries += 1
                except stripe.error.APIConnectionError as e:
                    if retries >= max_retries:
                        raise
                    wait_time = (initial_wait * (2 ** retries)) + (random.uniform(0, 0.1))
                    time.sleep(wait_time)
                    retries += 1
                except stripe.error.StripeError:
                    raise
        return cast(Callable[..., T], wrapper)
    return decorator

def initialize_stripe() -> stripe:
    """Initialize Stripe with settings from the main configuration."""
    settings = get_settings()
    try:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        # Configure automatic retries
        stripe.max_network_retries = 2
        # Configure idempotency key prefix
        stripe.idempotency_key_prefix = f'retry_{int(time.time())}_'
        return stripe
    except Exception as e:
        print(f"Failed to initialize Stripe: {str(e)}")
        print("Please check your environment variables for STRIPE_SECRET_KEY")
        raise