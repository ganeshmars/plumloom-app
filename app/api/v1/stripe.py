from sys import prefix
from typing import Dict, List
from datetime import datetime
import pycountry
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import stripe
from app.core.logging_config import logger
from app.core.stripe_config import initialize_stripe
from app.core.auth import validate_session, descope_client
from app.core.config import get_settings
from app.core.database import get_db
from app.core.email import send_email
from app.models.invoice import Invoice
from app.models.subscription import Subscription
from app.services.subscription_service import SubscriptionService
from app.services.refund_service import RefundService
from app.services.charge_service import ChargeService
from app.services.customer_service import CustomerService
from app.core.exceptions import CustomerError
from app.services.invoice_service import InvoiceService
from app.services.payment_method_service import PaymentMethodService
from app.services.payment_retry_service import PaymentRetryService
from app.services.payment_service import PaymentService
from app.schemas.stripe import (
    ProductResponse,
    CreateCheckoutRequest,
    CheckoutResponse,
    CustomerResponse,
    RefundRequest,
    RefundResponse,
    UpdateSubscriptionRequest,
    UpdateSubscriptionResponse,
    InvoiceResponse,
    UpcomingInvoiceResponse,
    SetupIntentResponse,
    PaymentMethodResponse, CreateCustomerRequest, CountrySpecResponse,
    CountryResponse
)
from app.core.email_templates import (
    payment_confirmation_template,
    subscription_welcome_template,
    subscription_updated_template,
    subscription_cancelled_template,
    invoice_created_template,
    invoice_payment_success_template
)


settings = get_settings()
router = APIRouter(prefix="/stripe", tags=["stripe"])

@router.get("/products", response_model=List[ProductResponse])
async def list_products(current_user: Dict = Depends(validate_session)):
    """
    List all active products from Stripe.
    """
    try:
        stripe_client = initialize_stripe()
        products = stripe_client.Product.list(active=True)

        return [
            ProductResponse(
                id=product.id,
                name=product.name,
                description=product.description,
                images=product.images,
                active=product.active,
                metadata=product.metadata,
                unit_label=product.unit_label if hasattr(product, 'unit_label') else None,
                url=product.url if hasattr(product, 'url') else None
            )
            for product in products.data
        ]
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/customer/create", response_model=CustomerResponse)
async def create_customer(request: CreateCustomerRequest, current_user: Dict = Depends(validate_session)):
    """
    Create a new customer in Stripe.
    """
    try:
        stripe_client = initialize_stripe()

        # Create customer in Stripe
        customer = stripe_client.Customer.create(
            email=request.email,
            name=request.name,
            metadata={
                **request.metadata,
                'user_id': current_user['user_id']  # Link Stripe customer to our user
            }
        )

        return CustomerResponse(
            id=customer.id,
            email=customer.email,
            name=customer.name,
            metadata=customer.metadata,
            subscription_status=None  # New customer has no subscription yet
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/countries", response_model=List[CountryResponse])
async def list_countries(current_user: Dict = Depends(validate_session)):
    """
    Get a list of all countries supported by Stripe with their full names.
    """
    try:
        stripe_client = initialize_stripe()
        countries = []
        
        # Get all countries using pagination
        has_more = True
        starting_after = None
        
        while has_more:
            country_specs = stripe_client.CountrySpec.list(
                limit=100,
                starting_after=starting_after
            )
            
            for country_spec in country_specs.data:
                try:
                    country = pycountry.countries.get(alpha_2=country_spec.id)
                    if country:
                        countries.append(CountryResponse(
                            code=country_spec.id,
                            name=country.name
                        ))
                except LookupError:
                    continue
                    
            # Update pagination
            has_more = country_specs.has_more
            if has_more and country_specs.data:
                starting_after = country_specs.data[-1].id
        
        # Sort countries by name
        return sorted(countries, key=lambda x: x.code)
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/countries/{country_code}", response_model=CountrySpecResponse)
async def get_country_spec(country_code: str, current_user: Dict = Depends(validate_session)):
    """
    Get detailed specifications for a specific country.
    """
    try:
        stripe_client = initialize_stripe()
        country_spec = stripe_client.CountrySpec.retrieve(country_code)
        return CountrySpecResponse(
            id=country_spec.id,
            default_currency=country_spec.default_currency,
            supported_payment_currencies=country_spec.supported_payment_currencies,
            supported_payment_methods=country_spec.supported_payment_methods,
            supported_transfer_countries=country_spec.supported_transfer_countries
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/create-checkout", response_model=CheckoutResponse)
async def create_checkout_session(request: CreateCheckoutRequest, current_user: Dict = Depends(validate_session)):
    """
    Create a Stripe Checkout Session for subscription or one-time payment.
    """
    try:
        stripe_client = initialize_stripe()

        # Check if user already has a Stripe customer ID
        existing_customers = stripe_client.Customer.list(email=current_user["email"])
        
        if existing_customers.data:
            if len(existing_customers.data) > 1:
                # Multiple customers found with same email - this shouldn't happen
                logger.error(f"Multiple Stripe customers found with email: {current_user['email']}")
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "duplicate_customer",
                        "message": "Multiple customers found with this email. Please contact support."
                    }
                )
            
            customer = existing_customers.data[0]
            # Verify the user_id in metadata matches
            if customer.metadata.get('user_id') and customer.metadata.get('user_id') != current_user["id"]:
                logger.error(f"Email {current_user['email']} already associated with different user")
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "email_taken",
                        "message": "This email is already associated with another customer"
                    }
                )
                
            # Update metadata if it doesn't exist
            if not customer.metadata.get('user_id'):
                customer = stripe_client.Customer.modify(
                    customer.id,
                    metadata={'user_id': current_user["id"]}
                )
        else:
            # Create new customer with country if provided
            customer_data = {
                "email": current_user["email"],
                "name": current_user.get("name"),
                "metadata": {"user_id": current_user["id"]}
            }
            
            if request.country_code:
                # Validate country code exists in Stripe
                try:
                    stripe_client.CountrySpec.retrieve(request.country_code)
                    customer_data["address"] = {"country": request.country_code}
                except stripe.error.StripeError:
                    # If country is invalid, we'll create customer without it
                    pass
                    
            customer = stripe_client.Customer.create(**customer_data)

        # Get the default price for the product
        product = stripe_client.Product.retrieve(request.product_id)
        if not product.active:
            raise HTTPException(status_code=400, detail="Product is not active")
            
        # Get prices for the product
        prices = stripe_client.Price.list(product=request.product_id, active=True, limit=1)
        if not prices.data:
            raise HTTPException(status_code=400, detail="No active price found for this product")
            
        default_price = prices.data[0]

        # Common checkout session parameters
        session_params = {
            "customer": customer.id,
            "payment_method_types": ["card"],
            "success_url": str(request.success_url),
            "cancel_url": str(request.cancel_url),
            "metadata": {
                "user_id": current_user["id"]
            }
        }

        # Configure mode-specific parameters
        if request.mode == "payment":
            session_params.update({
                "mode": "payment",
                "line_items": [{
                    "price": default_price.id,
                    "quantity": 1,
                }],
                "payment_intent_data": {
                    "metadata": {
                        "user_id": current_user["id"]
                    }
                }
            })
        else:  # subscription mode
            session_params.update({
                "mode": "subscription",
                "line_items": [{
                    "price": default_price.id,
                    "quantity": 1,
                }]
            })

        # Create checkout session
        checkout_session = stripe_client.checkout.Session.create(**session_params)

        return CheckoutResponse(
            checkout_url=checkout_session.url,
            session_id=checkout_session.id
        )

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/customer/webhook")
async def customer_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Handle Stripe webhook events for customer-related updates.
    """
    try:
        stripe_client = initialize_stripe()
        payload = await request.body()
        signature = request.headers.get('stripe-signature')
        
        if not signature:
            logger.error("No Stripe signature found in request headers")
            raise HTTPException(status_code=400, detail="No signature provided")
            
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                settings.STRIPE_CUSTOMER_WEBHOOK_SECRET
            )
            logger.info(f"Processing customer webhook event: {event.type}")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Webhook signature verification failed: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as e:
            logger.error(f"Error constructing webhook event: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid webhook payload")

        customer_service = CustomerService(db)

        # Handle customer-specific events
        if event.type == "customer.created":
            customer = event.data.object
            logger.info(f"Processing new customer creation: {customer}")
            try:
                user_data = customer.get('metadata',{})
                logger.info(f"User data: {user_data}")
                
                if not user_data:
                    logger.error(f"No user data found for email: {customer.email}")
                    return JSONResponse(status_code=404, content={"status": "error", "message": "User not found."})
                
                # Get tenant information
                user_tenants = user_data.get('tenants', '')
                if not user_tenants:
                    logger.error(f"No tenant information found for user: {user_data.get('user_id')}")
                    return JSONResponse(status_code=400, content={"status": "error", "message": "No tenant information found"})
                
                # Prepare user data combining Stripe and customer information
                user_data = {
                    'id': user_data['user_id'],
                    'stripe_customer_id': customer.id,
                    'email': user_data['email'],
                    'name': customer.name,
                    'phone': user_data.get('phone') or customer.phone,
                    'tenant_id': user_tenants,  # Use first tenant as primary
                    'status': 'active',
                    'company_name': user_tenants,  # Can be updated later
                    'tax_id': None,  # Can be updated later
                    'billing_address': customer.address or {},
                    'shipping_address': customer.shipping or {},
                    'currency': customer.currency or 'usd',
                    'language': 'en',  # Default
                    'notification_preferences': {},  # Default empty
                    'stripe_event_data': dict(customer),  # Store full Stripe customer data
                }
                
                # Create customer in our database
                db_customer = await customer_service.create_customer(user_data)
                logger.info(f"Successfully created customer in database: {db_customer.id}")
                
            except CustomerError as e:
                logger.error(f"Customer creation failed: {str(e)}")
                # Don't raise HTTP exception as we want to acknowledge the webhook
                # The customer can be created later through other flows
            
        elif event.type == "customer.updated":
            customer = event.data.object
            logger.info(f"Processing customer update: {customer}")
            
            try:
                # Get user data from customer metadata
                user_data = customer.get('metadata', {})
                logger.info(f"User data: {user_data}")
                
                if not user_data:
                    logger.error(f"No user data found for email: {customer.email}")
                    return JSONResponse(status_code=404, content={"status": "error", "message": "User not found"})
                
                # Get tenant information
                user_tenants = user_data.get('tenants', '')
                if not user_tenants:
                    logger.error(f"No tenant information found for user: {user_data.get('user_id')}")
                    return JSONResponse(status_code=400, content={"status": "error", "message": "No tenant information found"})
                
                # Prepare user data combining Stripe information
                user_data = {
                    'id': user_data['user_id'],
                    'stripe_customer_id': customer.id,
                    'email': user_data['email'],
                    'name': customer.name,
                    'phone': user_data.get('phone') or customer.phone,
                    'tenant_id': user_tenants,  # Use first tenant as primary
                    'status': 'active',
                    'company_name': user_tenants,  # Can be updated later
                    'tax_id': None,  # Can be updated later
                    'billing_address': customer.address or {},
                    'shipping_address': customer.shipping or {},
                    'currency': customer.currency or 'usd',
                    'language': 'en',
                    'notification_preferences': {},
                    'stripe_event_data': dict(customer)  # Update with latest Stripe data
                }
                
                # Update customer in our database
                db_customer = await customer_service.update_customer(user_data['id'], user_data)
                if db_customer:
                    logger.info(f"Successfully updated customer in database: {db_customer.id}")
                    return JSONResponse(status_code=200, content={"status": "success"})
                else:
                    logger.error(f"Customer not found in database: {user_data['id']}")
                    return JSONResponse(status_code=404, content={"status": "error", "message": "Customer not found"})
                    
            except Exception as e:
                logger.error(f"Error updating customer: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
            
        elif event.type == "customer.deleted":
            customer = event.data.object
            logger.info(f"Processing customer deletion: {customer}")
            
            try:
                # Get customer by Stripe ID
                db_customer = await customer_service.get_customer(customer.id, by_stripe_id=True)
                if not db_customer:
                    logger.warning(f"Customer not found in database for Stripe ID: {customer.id}")
                    return JSONResponse(status_code=404, content={"status": "error", "message": "Customer not found"})
                
                # Delete customer from database
                deleted = await customer_service.delete_customer(db_customer.id)
                if deleted:
                    logger.info(f"Successfully deleted customer: {db_customer.id}")
                    return JSONResponse(status_code=200, content={"status": "success"})
                else:
                    logger.error(f"Failed to delete customer: {db_customer.id}")
                    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete customer"})
                    
            except Exception as e:
                logger.error(f"Error processing customer deletion: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
            
        elif event.type == "customer.source.created":
            source = event.data.object
            logger.info(f"Payment source added for customer: {source.customer}")
            # Add source creation logic here
            
        elif event.type == "customer.source.updated":
            source = event.data.object
            logger.info(f"Payment source updated for customer: {source.customer}")
            # Add source update logic here
            
        elif event.type == "customer.source.deleted":
            source = event.data.object
            logger.info(f"Payment source deleted for customer: {source.customer}")
            # Add source deletion logic here

        return JSONResponse(status_code=200, content={"status": "success"})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing customer webhook: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/customer/subscription/webhook")
async def subscription_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Handle Stripe webhook events for subscription-related updates.
    """
    try:
        stripe_client = initialize_stripe()
        payload = await request.body()
        signature = request.headers.get('stripe-signature')
        if not signature:
            logger.error("No Stripe signature found in request headers")
            raise HTTPException(status_code=400, detail="No signature provided")
            
        # Verify webhook signature
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                settings.STRIPE_CUSTOMER_SUBSCRIPTION_WEBHOOK_SECRET
            )
            logger.info(f"Processing subscription webhook event: {event.type}")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Webhook signature verification failed: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as e:
            logger.error(f"Error constructing webhook event: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid webhook payload")

        # Get customer email for notifications
        customer_email = None
        try:
            if event.data.object.get('customer'):
                customer = stripe_client.Customer.retrieve(event.data.object.customer)
                customer_email = customer.email
                logger.info(f"Found customer email for notifications: {customer_email}")
        except Exception as e:
            logger.error(f"Could not retrieve customer email: {str(e)}")

        subscription_service = SubscriptionService(db)

        # Handle subscription-specific events
        if event.type == "customer.subscription.created":
            subscription = event.data.object
            logger.info(f"Processing new subscription creation: {subscription.id}")
            try:
                # Create subscription record
                db_subscription = await subscription_service.create_or_update_subscription({
                    "subscription": subscription
                })
                logger.info(f"Successfully created subscription - ID: {db_subscription.id}")
                
                # Send welcome email
                if customer_email:
                    product_name = "Subscription"
                    if subscription['items']['data'][0]['price']['product']:
                        product = stripe_client.Product.retrieve(subscription['items']['data'][0]['price']['product'])
                        product_name = product.name
                            
                    await send_email(
                        to_email=customer_email,
                        subject="Your Subscription Has Been Activated",
                        content=subscription_welcome_template(
                            product_name=product_name,
                            price_info=f"<p>Plan: {subscription['items']['data'][0]['price']['unit_amount'] / 100} {subscription['items']['data'][0]['price']['currency'].upper()}/{subscription['items']['data'][0]['price']['recurring']['interval']}</p>",
                            status=subscription['status'].capitalize(),
                            current_period_end=datetime.fromtimestamp(subscription['current_period_end']).strftime('%B %d, %Y'),
                            trial_info=""
                        )
                    )
                    logger.info(f"Sent subscription welcome email to {customer_email}")
            except Exception as e:
                logger.error(f"Error processing subscription creation: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
                
        elif event.type == "customer.subscription.updated":
            subscription = event.data.object
            logger.info(f"Processing subscription update: {subscription.id}")
            try:
                # Update subscription record
                db_subscription = await subscription_service.create_or_update_subscription({
                    "subscription": subscription
                })
                logger.info(f"Successfully updated subscription - ID: {db_subscription.id}")
            except Exception as e:
                logger.error(f"Error processing subscription update: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
                
        elif event.type == "customer.subscription.deleted":
            subscription = event.data.object
            logger.info(f"Processing subscription deletion: {subscription.id}")
            try:
                # Mark subscription as deleted
                await subscription_service.cancel_subscription(subscription.id)
                logger.info(f"Successfully marked subscription as deleted - ID: {subscription.id}")
                
                # Send cancellation email
                if customer_email:
                    await send_email(
                        to_email=customer_email,
                        subject="Your Subscription Has Been Cancelled",
                        content=subscription_cancelled_template(
                            end_date=datetime.fromtimestamp(subscription.ended_at or subscription.current_period_end).strftime('%B %d, %Y')
                        )
                    )
                    logger.info(f"Sent subscription cancellation email to {customer_email}")
            except Exception as e:
                logger.error(f"Error processing subscription deletion: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
                
        elif event.type == "customer.subscription.trial_will_end":
            subscription = event.data.object
            logger.info(f"Processing trial ending notification: {subscription.id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing subscription webhook: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Handle Stripe webhook events for subscription status updates.
    """
    try:
        stripe_client = initialize_stripe()
        payload = await request.body()
        signature = request.headers.get('stripe-signature')
        
        if not signature:
            logger.error("No Stripe signature found in request headers")
            raise HTTPException(status_code=400, detail="No signature provided")
            
        # Verify webhook signature
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                settings.STRIPE_WEBHOOK_SECRET
            )
            
            # Log event details
            logger.info("=== Webhook Event Details ===")
            logger.info(f"Event Type: {event.type}")
            logger.info(f"Webhook event constructed successfully - Type: {event.type}")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Webhook signature verification failed: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as e:
            logger.error(f"Error constructing webhook event: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid webhook payload")

        logger.info(f"Processing webhook event: {event.type}")
        subscription_service = SubscriptionService(db)
        refund_service = RefundService(db)
        customer_service = CustomerService(db)

        # Get customer email for notifications
        customer_email = None
        try:
            if event.data.object.get('customer'):
                customer = stripe_client.Customer.retrieve(event.data.object.customer)
                customer_email = customer.email
                logger.info(f"Found customer email for notifications: {customer_email}")
        except Exception as e:
            logger.error(f"Could not retrieve customer email: {str(e)}")
            # Continue processing even if we can't get the email
        
        # Handle customer-specific events
        if event.type == "customer.created":
            customer = event.data.object
            logger.info(f"Processing new customer creation: {customer}")
            try:
                user_data = customer.get('metadata',{})
                
                if not user_data:
                    logger.error(f"No user data found for email: {customer.email}")
                    return JSONResponse(status_code=404, content={"status": "error", "message": "User not found."})
                
                # Get tenant information
                user_tenants = user_data.get('tenants', '')
                if not user_tenants:
                    logger.error(f"No tenant information found for user: {user_data.get('user_id')}")
                    return JSONResponse(status_code=400, content={"status": "error", "message": "No tenant information found"})
                
                # Prepare user data combining Stripe and customer information
                user_data = {
                    'id': user_data['user_id'],
                    'stripe_customer_id': customer.id,
                    'email': user_data['email'],
                    'name': customer.name,
                    'phone': user_data.get('phone') or customer.phone,
                    'tenant_id': user_tenants,  # Use first tenant as primary
                    'status': 'active',
                    'company_name': user_tenants,  # Can be updated later
                    'tax_id': None,  # Can be updated later
                    'billing_address': customer.address or {},
                    'shipping_address': customer.shipping or {},
                    'currency': customer.currency or 'usd',
                    'language': 'en',  # Default
                    'notification_preferences': {},  # Default empty
                    'stripe_event_data': dict(customer),  # Store full Stripe customer data
                }
                
                # Create customer in our database
                db_customer = await customer_service.create_customer(user_data)
                logger.info(f"Successfully created customer in database: {db_customer.id}")
                
            except CustomerError as e:
                logger.error(f"Customer creation failed: {str(e)}")
                # Don't raise HTTP exception as we want to acknowledge the webhook
                # The customer can be created later through other flows
            
        elif event.type == "customer.updated":
            customer = event.data.object
            logger.info(f"Processing customer update: {customer}")
            
            try:
                # Get user data from customer metadata
                user_data = customer.get('metadata', {})
                
                if not user_data:
                    logger.error(f"No user data found for email: {customer.email}")
                    return JSONResponse(status_code=404, content={"status": "error", "message": "User not found"})
                
                # Get tenant information
                user_tenants = user_data.get('tenants', '')
                if not user_tenants:
                    logger.error(f"No tenant information found for user: {user_data.get('user_id')}")
                    return JSONResponse(status_code=400, content={"status": "error", "message": "No tenant information found"})
                
                # Prepare user data combining Stripe information
                user_data = {
                    'id': user_data['user_id'],
                    'stripe_customer_id': customer.id,
                    'email': user_data['email'],
                    'name': customer.name,
                    'phone': user_data.get('phone') or customer.phone,
                    'tenant_id': user_tenants,  # Use first tenant as primary
                    'status': 'active',
                    'company_name': user_tenants,  # Can be updated later
                    'tax_id': None,  # Can be updated later
                    'billing_address': customer.address or {},
                    'shipping_address': customer.shipping or {},
                    'currency': customer.currency or 'usd',
                    'language': 'en',
                    'notification_preferences': {},
                    'stripe_event_data': dict(customer)  # Update with latest Stripe data
                }
                
                # Update customer in our database
                db_customer = await customer_service.update_customer(user_data['id'], user_data)
                if db_customer:
                    logger.info(f"Successfully updated customer in database: {db_customer.id}")
                    return JSONResponse(status_code=200, content={"status": "success"})
                else:
                    logger.error(f"Customer not found in database: {user_data['id']}")
                    return JSONResponse(status_code=404, content={"status": "error", "message": "Customer not found"})
                    
            except Exception as e:
                logger.error(f"Error updating customer: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
            
        elif event.type == "customer.deleted":
            customer = event.data.object
            logger.info(f"Processing customer deletion: {customer}")
            
            try:
                # Get customer by Stripe ID
                db_customer = await customer_service.get_customer(customer.id, by_stripe_id=True)
                if not db_customer:
                    logger.warning(f"Customer not found in database for Stripe ID: {customer.id}")
                    return JSONResponse(status_code=404, content={"status": "error", "message": "Customer not found"})
                
                # Delete customer from database
                deleted = await customer_service.delete_customer(db_customer.id)
                if deleted:
                    logger.info(f"Successfully deleted customer: {db_customer.id}")
                    return JSONResponse(status_code=200, content={"status": "success"})
                else:
                    logger.error(f"Failed to delete customer: {db_customer.id}")
                    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to delete customer"})
                    
            except Exception as e:
                logger.error(f"Error processing customer deletion: {str(e)}")
                return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
            
        elif event.type == "customer.source.created":
            source = event.data.object
            logger.info(f"Payment source added for customer: {source.customer}")
            # Add source creation logic here
            
        elif event.type == "customer.source.updated":
            source = event.data.object
            logger.info(f"Payment source updated for customer: {source.customer}")
            # Add source update logic here
            
        elif event.type == "customer.source.deleted":
            source = event.data.object
            logger.info(f"Payment source deleted for customer: {source.customer}")
            # Add source deletion logic here
        
        # elif event.type == "customer.subscription.created":
        #     subscription = event.data.object
        #     logger.info(f"Processing new subscription creation: {subscription.id}")
        #     try:
        #         # Create subscription record
        #         db_subscription = await subscription_service.create_or_update_subscription({
        #             "subscription": subscription
        #         })
        #         logger.info(f"Successfully created subscription - ID: {db_subscription.id}")
                
        #         # Send welcome email
        #         if customer_email:
        #             product_name = "Subscription"
        #             if subscription.items.data and subscription.items.data[0].price.product:
        #                 product = stripe_client.Product.retrieve(subscription.items.data[0].price.product)
        #                 product_name = product.name
                            
        #             await send_email(
        #                 to_email=customer_email,
        #                 subject="Your Subscription Has Been Activated",
        #                 content=subscription_welcome_template(
        #                     product_name=product_name,
        #                     price_info=f"<p>Plan: {subscription.items.data[0].price.unit_amount / 100} {subscription.items.data[0].price.currency.upper()}/{subscription.items.data[0].price.recurring.interval}</p>",
        #                     status=subscription.status.capitalize(),
        #                     current_period_end=datetime.fromtimestamp(subscription.current_period_end).strftime('%B %d, %Y'),
        #                     trial_info=""
        #                 )
        #             )
        #             logger.info(f"Sent subscription welcome email to {customer_email}")
        #     except Exception as e:
        #         logger.error(f"Error processing subscription creation: {str(e)}")
        #         return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
                
        # elif event.type == "customer.subscription.updated":
        #     subscription = event.data.object
        #     logger.info(f"Processing subscription update: {subscription.id}")
        #     try:
        #         # Update subscription record
        #         db_subscription = await subscription_service.create_or_update_subscription({
        #             "subscription": subscription
        #         })
        #         logger.info(f"Successfully updated subscription - ID: {db_subscription.id}")
        #     except Exception as e:
        #         logger.error(f"Error processing subscription update: {str(e)}")
        #         return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
                
        # elif event.type == "customer.subscription.deleted":
        #     subscription = event.data.object
        #     logger.info(f"Processing subscription deletion: {subscription.id}")
        #     try:
        #         # Mark subscription as deleted
        #         await subscription_service.delete_subscription(subscription.id)
        #         logger.info(f"Successfully marked subscription as deleted - ID: {subscription.id}")
                
        #         # Send cancellation email
        #         if customer_email:
        #             await send_email(
        #                 to_email=customer_email,
        #                 subject="Your Subscription Has Been Cancelled",
        #                 content=subscription_cancelled_template(
        #                     end_date=datetime.fromtimestamp(subscription.ended_at or subscription.current_period_end).strftime('%B %d, %Y')
        #                 )
        #             )
        #             logger.info(f"Sent subscription cancellation email to {customer_email}")
        #     except Exception as e:
        #         logger.error(f"Error processing subscription deletion: {str(e)}")
        #         return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
                
        # elif event.type == "customer.subscription.trial_will_end":
        #     subscription = event.data.object
        #     logger.info(f"Processing trial ending notification: {subscription.id}")
        
        # Handle specific webhook events
        elif event.type == "checkout.session.completed":
            session = event.data.object
            logger.info("=== Processing Checkout Session ===")
            logger.info(f"Session ID: {session.id}")
            logger.info(f"Customer ID: {session.get('customer')}")
            logger.info(f"Mode: {session.get('mode')}")
            logger.info(f"Payment Status: {session.get('payment_status')}")
            
            # For test events, we'll still process them but log more details
            if not event.livemode:
                logger.info("=== Test Event Details ===")
                logger.info("This is a test event from Stripe CLI")
                logger.info(f"Full session data: {session}")
            
            if session.mode == "subscription":
                # Handle subscription mode
                try:
                    # Retrieve the subscription details
                    subscription = stripe_client.Subscription.retrieve(session.subscription)
                    logger.info(f"Retrieved subscription details - ID: {subscription.id}")
                    # Create or update subscription record

                    db_subscription = await subscription_service.create_or_update_subscription({
                        "subscription": subscription
                    })
                    logger.info(f"Successfully processed subscription - ID: {db_subscription.id}")
                    logger.info(f"Sending email notification to {customer_email}")
                    # Send email notification
                    if customer_email:
                        product_name = "Subscription"
                        # Safely access subscription items data
                        try:
                            items_data = subscription.get('items', {})
                            if hasattr(items_data, 'data') and items_data.data:
                                item = items_data.data[0]
                                if hasattr(item, 'price') and hasattr(item.price, 'product'):
                                    product = stripe_client.Product.retrieve(item.price.product)
                                    product_name = product.name
                                    logger.info(f"Found product name: {product_name}")
                            else:
                                logger.info(f"No items data found in subscription: {subscription.id}")
                        except Exception as e:
                            logger.error(f"Error accessing subscription items: {str(e)}")
                            # Continue with default product name
                            
                        await send_email(
                            to_email=customer_email,
                            subject="Your Subscription Has Been Activated",
                            content=subscription_welcome_template(
                                product_name=product_name,
                                price_info="<p>Plan details unavailable</p>",
                                status=subscription.get('status', 'active').capitalize(),
                                current_period_end=datetime.fromtimestamp(subscription.get('current_period_end', time.time())).strftime('%B %d, %Y'),
                                trial_info=""
                            )
                        )
                        logger.info(f"Sent subscription confirmation email to {customer_email}")
                except stripe.error.StripeError as e:
                    logger.error(f"Stripe API error while processing subscription: {str(e)}")
                    raise HTTPException(status_code=400, detail=str(e))
                except Exception as e:
                    logger.error(f"Error processing subscription: {str(e)}")
                    raise HTTPException(status_code=500, detail="Error processing subscription")
            #mail send completed
            elif session.mode == "payment":
                # Handle payment mode
                try:
                    # Create or update payment record
                    payment_service = PaymentService(db)
                    db_payment = await payment_service.create_or_update_payment(session)
                    logger.info(f"Successfully processed payment - ID: {db_payment.id}")
                    
                    # Send email notification for successful payment
                    if customer_email:
                        # Try to get product details if available
                        product_name = "Product"
                        try:
                            if session.line_items and session.line_items.data and session.line_items.data[0].price.product:
                                product = stripe_client.Product.retrieve(session.line_items.data[0].price.product)
                                product_name = product.name
                        except Exception as e:
                            logger.error(f"Error retrieving product details: {str(e)}")
                            
                        # Format amount with currency
                        amount_str = f"{session.amount_total / 100:.2f} {session.currency.upper()}"
                        
                        await send_email(
                            to_email=customer_email,
                            subject="Payment Confirmation",
                            content=payment_confirmation_template(
                                product_name=product_name,
                                amount_str=amount_str,
                                date=datetime.now().strftime('%B %d, %Y'),
                                transaction_id=session.payment_intent
                            )
                        )
                        logger.info(f"Sent payment confirmation email to {customer_email}")
                except stripe.error.StripeError as e:
                    logger.error(f"Stripe API error while processing payment: {str(e)}")
                    raise HTTPException(status_code=400, detail=str(e))
                except Exception as e:
                    logger.error(f"Error processing payment: {str(e)}")
                    raise HTTPException(status_code=500, detail="Error processing payment")
                    
            elif session.mode == "setup":
                # Handle setup mode
                logger.info("Setup mode detected - storing payment method")
                try:
                    payment_method_service = PaymentMethodService(db)
                    setup_intent = stripe_client.SetupIntent.retrieve(session.setup_intent)
                    await payment_method_service.add_payment_method(setup_intent.payment_method)
                    logger.info(f"Successfully stored payment method from setup intent: {setup_intent.id}")
                except Exception as e:
                    logger.error(f"Error processing setup intent: {str(e)}")
                    raise HTTPException(status_code=500, detail="Error processing setup intent")
                try:
                    # Retrieve the subscription details
                    subscription = stripe_client.Subscription.retrieve(session.subscription)
                    logger.info(f"Retrieved subscription details - ID: {subscription.id}")
                    # Create or update subscription record
                    db_subscription = await subscription_service.create_or_update_subscription({
                        "subscription": subscription
                    })
                    logger.info(f"Successfully processed subscription - ID: {db_subscription.id}")
                except stripe.error.StripeError as e:
                    logger.error(f"Stripe API error while processing subscription: {str(e)}")
                    raise HTTPException(status_code=400, detail=str(e))
                except Exception as e:
                    logger.error(f"Error processing subscription: {str(e)}")
                    raise HTTPException(status_code=500, detail="Error processing subscription")

        # elif event.type == "invoice.payment_succeeded":
        #     # For testing purposes, use the test data instead of actual event data
        #     from app.tests.data.stripe_test_data import INVOICE_PAYMENT_SUCCEEDED_EVENT
            
        #     # Use test event data
        #     # event = INVOICE_PAYMENT_SUCCEEDED_EVENT
        #     invoice = event.data.object
        #     logger.info(f"Processing invoice payment succeeded event for invoice: {invoice.id}")
            
        #     try:
        #         # First update subscription if it exists
        #         subscription = None
        #         if hasattr(invoice, 'subscription') and invoice.subscription:
        #             logger.info(f"Found subscription: {invoice.subscription}")
        #             subscription_service = SubscriptionService(db)
        #             try:
        #                 subscription = stripe_client.Subscription.retrieve(invoice.subscription)
        #                 logger.info(f"Successfully retrieved subscription from Stripe: {subscription.id}")
        #                 await subscription_service.create_or_update_subscription(subscription)
        #                 await db.commit()
        #                 logger.info(f"Successfully updated subscription in database")
        #             except Exception as e:
        #                 logger.error(f"Error retrieving or updating subscription: {str(e)}")
        #                 logger.exception("Subscription retrieval error details:")
        #                 # Continue processing even if subscription update fails
                    
        #         # Then create/update invoice
        #         invoice_service = InvoiceService(db)
        #         try:
        #             db_invoice = await invoice_service.create_or_update_invoice(invoice)
        #             await db.commit()
        #             logger.info(f"Successfully updated invoice in database: {db_invoice.id}")
        #         except Exception as e:
        #             logger.error(f"Error updating invoice in database: {str(e)}")
        #             logger.exception("Invoice update error details:")
        #             await db.rollback()
        #             # Continue to try sending email even if database update fails
                
        #         # Send email notification to customer
        #         logger.info("Attempting to send email notification to customer")
        #         try:
        #             # Check if customer exists
        #             if not hasattr(invoice, 'customer') or not invoice.customer:
        #                 logger.error("No customer found in invoice data")
        #                 raise ValueError("No customer found in invoice data")
                        
        #             logger.info(f"Retrieving customer details for: {invoice.customer}")
        #             customer = stripe_client.Customer.retrieve(invoice.customer)
                    
        #             if not customer:
        #                 logger.error(f"Could not retrieve customer: {invoice.customer}")
        #                 raise ValueError(f"Could not retrieve customer: {invoice.customer}")
                        
        #             logger.info(f"Customer retrieved: {customer.id}")
                    
        #             if not hasattr(customer, 'email') or not customer.email:
        #                 logger.error("Customer has no email address")
        #                 raise ValueError("Customer has no email address")
                        
        #             customer_email = customer.email
        #             logger.info(f"Customer email found: {customer_email}")
                    
        #             # Format amount with currency
        #             amount_str = f"{invoice.amount_paid / 100:.2f} {invoice.currency.upper()}"
        #             logger.info(f"Formatted amount: {amount_str}")
                    
        #             # Get product details if available
        #             product_name = "Subscription"
        #             product_id = None
                    
        #             # Try to get product ID from invoice lines
        #             logger.info("Attempting to extract product details from invoice")
        #             if hasattr(invoice, 'lines') and hasattr(invoice.lines, 'data') and len(invoice.lines.data) > 0:
        #                 line_item = invoice.lines.data[0]
        #                 logger.info(f"Found line item: {line_item.id}")
                        
        #                 if hasattr(line_item, 'price') and hasattr(line_item.price, 'product'):
        #                     product_id = line_item.price.product
        #                     logger.info(f"Found product ID in price: {product_id}")
        #                 elif hasattr(line_item, 'plan') and hasattr(line_item.plan, 'product'):
        #                     product_id = line_item.plan.product
        #                     logger.info(f"Found product ID in plan: {product_id}")
                    
        #             # If we found a product ID, get the product details
        #             if product_id:
        #                 try:
        #                     logger.info(f"Retrieving product details for: {product_id}")
        #                     product = stripe_client.Product.retrieve(product_id)
        #                     if hasattr(product, 'name'):
        #                         product_name = product.name
        #                         logger.info(f"Found product name: {product_name}")
        #                 except Exception as e:
        #                     logger.error(f"Error retrieving product details: {str(e)}")
        #                     logger.exception("Product retrieval error details:")
        #                     # Continue with default product name
                    
        #             # Format payment date
        #             logger.info("Formatting payment date")
        #             payment_date = None
        #             if hasattr(invoice, 'status_transitions') and hasattr(invoice.status_transitions, 'paid_at') and invoice.status_transitions.paid_at:
        #                 payment_date = datetime.fromtimestamp(invoice.status_transitions.paid_at)
        #                 logger.info(f"Using paid_at timestamp: {invoice.status_transitions.paid_at}")
        #             else:
        #                 payment_date = datetime.fromtimestamp(invoice.created) if hasattr(invoice, 'created') else datetime.now()
        #                 logger.info(f"Using created timestamp or current time: {payment_date}")
                    
        #             payment_date_str = payment_date.strftime('%B %d, %Y')
        #             logger.info(f"Formatted payment date: {payment_date_str}")
                    
        #             # Get invoice number and URL
        #             invoice_number = invoice.number if hasattr(invoice, 'number') else "N/A"
        #             invoice_url = invoice.hosted_invoice_url if hasattr(invoice, 'hosted_invoice_url') else "#"
        #             logger.info(f"Invoice number: {invoice_number}, URL available: {'Yes' if invoice_url != '#' else 'No'}")
                    
        #             # Generate email content
        #             logger.info("Generating email content")
        #             email_content = invoice_payment_success_template(
        #                 product_name=product_name,
        #                 amount_str=amount_str,
        #                 payment_date=payment_date_str,
        #                 invoice_number=invoice_number,
        #                 invoice_url=invoice_url
        #             )
        #             logger.info(f"Email content generated, length: {len(email_content)}")
                    
        #             # Send email with payment confirmation
        #             logger.info(f"Sending email to: {customer_email}")
        #             await send_email(
        #                 to_email=customer_email,
        #                 subject="Payment Successful",
        #                 content=email_content
        #             )
        #             logger.info(f"Email sent successfully to {customer_email}")
                    
        #         except Exception as e:
        #             logger.error(f"Error sending payment success email: {str(e)}")
        #             logger.exception("Detailed email sending error:")
        #             # Continue processing even if email fails
                
        #     except Exception as e:
        #         logger.error(f"Unexpected error processing payment succeeded webhook: {str(e)}")
        #         logger.exception("Webhook processing error details:")
        #         await db.rollback()
        #         raise HTTPException(status_code=500, detail=str(e))

        # elif event.type == "invoice.payment_failed":
        #     from app.tests.data.stripe_test_data import INVOICE_PAYMENT_FAILED_EVENT
        #     # invoice = event.data.object
        #     invoice = INVOICE_PAYMENT_FAILED_EVENT
        #     logger.info(f"Failed invoice: {invoice}")
            
        #     try:
        #         logger.info(f"Processing failed payment for invoice: {invoice.id}")
                
        #         if not invoice.subscription:
        #             logger.warning(f"No subscription found for failed invoice: {invoice.id}")
        #             return
                
        #         logger.info(f"Found subscription: {invoice.subscription}")
        #         # Initialize payment retry service
        #         payment_retry_service = PaymentRetryService(db)
                
        #         try:
        #             # Update subscription status
        #             logger.info(f"Retrieving subscription details from Stripe")
        #             subscription = stripe_client.Subscription.retrieve(
        #                 invoice.subscription,
        #                 expand=['latest_invoice.payment_intent']
        #             )
        #         except stripe.error.StripeError as e:
        #             logger.error(f"Failed to retrieve subscription from Stripe: {str(e)}")
        #             raise HTTPException(
        #                 status_code=500,
        #                 detail=f"Failed to retrieve subscription: {str(e)}"
        #             )
                
        #         logger.info(f"Updating subscription in database: {subscription.id}")
        #         await subscription_service.create_or_update_subscription({
        #             "subscription": subscription
        #         })
                
        #         # Handle payment failure with retry logic
        #         logger.info(f"Initiating payment retry process for subscription: {subscription.id}")
        #         await payment_retry_service.handle_payment_failure(subscription.id)
                
        #         # Update invoice record and send notification
        #         logger.info(f"Updating invoice record in database: {invoice.id}")
        #         invoice_service = InvoiceService(db)
        #         db_invoice = await invoice_service.create_or_update_invoice({"invoice": invoice})
                
        #         logger.info(f"Sending payment failure notification for invoice: {invoice.id}")
        #         await invoice_service.send_invoice_notification(db_invoice)
        #         logger.info(f"Completed processing payment failure for invoice: {invoice.id}")
                
        #     except Exception as e:
        #         logger.error(f"Error processing invoice payment failure: {str(e)}")
        #         await db.rollback()
        #         raise HTTPException(status_code=500, detail=f"Failed to process payment failure: {str(e)}")

        # # Invoice event handlers
        # elif event.type == "invoice.created":
        #     invoice = event.data.object
        #     logger.info(f"Processing new invoice creation: {invoice.id}")
            
        #     try:
        #         if not invoice.customer:
        #             logger.error(f"No customer found for invoice: {invoice.id}")
        #             raise ValueError(f"No customer found for invoice: {invoice.id}")
                
        #         logger.info(f"Creating/updating invoice for customer: {invoice.customer}")
        #         invoice_service = InvoiceService(db)
                
        #         try:
        #             # If this invoice is related to a subscription, update subscription first
        #             if invoice.subscription:
        #                 logger.info(f"Updating subscription details for: {invoice.subscription}")
        #                 try:
        #                     subscription = stripe_client.Subscription.retrieve(
        #                         invoice.subscription,
        #                         expand=['latest_invoice']
        #                     )
        #                     await subscription_service.create_or_update_subscription({
        #                         "subscription": subscription
        #                     })
        #                     logger.info(f"Successfully updated subscription: {subscription.id}")
        #                 except stripe.error.StripeError as e:
        #                     logger.error(f"Failed to retrieve subscription from Stripe: {str(e)}")
        #                     raise HTTPException(
        #                         status_code=500,
        #                         detail=f"Failed to retrieve subscription: {str(e)}"
        #                     )
                    
        #             # Create or update invoice
        #             db_invoice = await invoice_service.create_or_update_invoice({"invoice": invoice})
        #             logger.info(f"Successfully created/updated invoice {db_invoice.id} in database")
                    
        #             # Send notification
        #             logger.info(f"Sending invoice notification for invoice: {invoice.id}")
                    
        #             # Get customer email for notification
        #             try:
        #                 customer = stripe_client.Customer.retrieve(invoice.customer)
        #                 if customer and customer.email:
        #                     # Format amount with currency
        #                     amount_str = f"{invoice.amount_due / 100:.2f} {invoice.currency.upper()}"
                            
        #                     # Get product details if available
        #                     product_name = "Subscription"
        #                     if invoice.subscription:
        #                         try:
        #                             subscription = stripe_client.Subscription.retrieve(invoice.subscription)
        #                             if subscription.items.data and subscription.items.data[0].price.product:
        #                                 product = stripe_client.Product.retrieve(subscription.items.data[0].price.product)
        #                                 product_name = product.name
        #                         except Exception as e:
        #                             logger.error(f"Error retrieving product details: {str(e)}")
                            
        #                     # Format due date
        #                     due_date = datetime.fromtimestamp(invoice.due_date) if invoice.due_date else datetime.now()
        #                     due_date_str = due_date.strftime('%B %d, %Y')
                            
        #                     # Send email with invoice details
        #                     await send_email(
        #                         to_email=customer.email,
        #                         subject="New Invoice Available",
        #                         content=invoice_created_template(
        #                             product_name=product_name,
        #                             amount_str=amount_str,
        #                             due_date_str=due_date_str,
        #                             status=invoice.status,
        #                             hosted_invoice_url=invoice.hosted_invoice_url
        #                         )
        #                     )
        #                     logger.info(f"Sent invoice email notification to {customer.email}")
        #             except Exception as e:
        #                 logger.error(f"Error sending invoice email notification: {str(e)}")
        #                 # Continue processing even if email fails
                    
        #             await invoice_service.send_invoice_notification(db_invoice)
        #             logger.info(f"Successfully sent notification for invoice: {invoice.id}")
                    
        #         except ValueError as e:
        #             logger.error(f"Failed to process invoice {invoice.id}: {str(e)}")
        #             await db.rollback()
        #             raise HTTPException(status_code=400, detail=str(e))
                    
        #     except Exception as e:
        #         logger.error(f"Unexpected error processing invoice {invoice.id}: {str(e)}")
        #         await db.rollback()
        #         raise HTTPException(status_code=500, detail=f"Failed to process invoice: {str(e)}")

        # elif event.type == "invoice.paid":
        #     invoice = event.data.object
        #     logger.info(f"Processing paid invoice: {invoice.id}")
            
        #     try:
        #         if not invoice.customer:
        #             logger.error(f"No customer found for invoice: {invoice.id}")
        #             raise ValueError(f"No customer found for invoice: {invoice.id}")
                
        #         logger.info(f"Processing payment for customer: {invoice.customer}")
                
        #         # If this is a subscription invoice, update subscription status
        #         if invoice.subscription:
        #             logger.info(f"Updating subscription status for: {invoice.subscription}")
        #             try:
        #                 # Retrieve subscription with expanded details
        #                 subscription = stripe_client.Subscription.retrieve(
        #                     invoice.subscription,
        #                     expand=['latest_invoice', 'latest_invoice.payment_intent']
        #                 )
                        
        #                 # Update subscription in our database
        #                 await subscription_service.create_or_update_subscription({
        #                     "subscription": subscription
        #                 })
        #                 logger.info(f"Successfully updated subscription status: {subscription.id}")
                        
        #                 # Check if this payment affects subscription status
        #                 if subscription.status in ['active', 'trialing']:
        #                     logger.info(f"Subscription {subscription.id} is now {subscription.status}")
                        
        #             except stripe.error.StripeError as e:
        #                 logger.error(f"Failed to retrieve subscription from Stripe: {str(e)}")
        #                 raise HTTPException(
        #                     status_code=500,
        #                     detail=f"Failed to retrieve subscription: {str(e)}"
        #                 )
                
        #         # Update invoice in database
        #         try:
        #             invoice_service = InvoiceService(db)
        #             db_invoice = await invoice_service.create_or_update_invoice({"invoice": invoice})
        #             logger.info(f"Successfully recorded payment for invoice: {invoice.id}")
                    
        #             # Send email notification to customer
        #             try:
        #                 customer = stripe_client.Customer.retrieve(invoice.customer)
        #                 if customer and customer.email:
        #                     # Format amount with currency
        #                     amount_str = f"{invoice.amount_paid / 100:.2f} {invoice.currency.upper()}"
                            
        #                     # Get product details if available
        #                     product_name = "Subscription"
        #                     if invoice.subscription:
        #                         try:
        #                             subscription = stripe_client.Subscription.retrieve(invoice.subscription)
        #                             if subscription.items.data and subscription.items.data[0].price.product:
        #                                 product = stripe_client.Product.retrieve(subscription.items.data[0].price.product)
        #                                 product_name = product.name
        #                         except Exception as e:
        #                             logger.error(f"Error retrieving product details: {str(e)}")
                            
        #                     # Format payment date
        #                     payment_date = datetime.fromtimestamp(invoice.status_transitions.paid_at) if hasattr(invoice.status_transitions, 'paid_at') and invoice.status_transitions.paid_at else datetime.now()
        #                     payment_date_str = payment_date.strftime('%B %d, %Y')
                            
        #                     # Send email with payment confirmation
        #                     await send_email(
        #                         to_email=customer.email,
        #                         subject="Payment Confirmation",
        #                         content=invoice_payment_success_template(
        #                             product_name=product_name,
        #                             amount_str=amount_str,
        #                             payment_date=payment_date_str,
        #                             invoice_number=invoice.number,
        #                             invoice_url=invoice.hosted_invoice_url
        #                         )
        #                     )
        #                     logger.info(f"Sent payment confirmation email to {customer.email}")
        #             except Exception as e:
        #                 logger.error(f"Error sending payment confirmation email: {str(e)}")
        #                 # Continue processing even if email fails
                    
        #             # Send success notification
        #             logger.info(f"Sending payment success notification for invoice: {invoice.id}")
        #             await invoice_service.send_invoice_notification(db_invoice)
        #             logger.info(f"Successfully sent payment notification for invoice: {invoice.id}")
                    
        #         except ValueError as e:
        #             logger.error(f"Failed to process invoice payment {invoice.id}: {str(e)}")
        #             await db.rollback()
        #             raise HTTPException(status_code=400, detail=str(e))
                
        #     except Exception as e:
        #         logger.error(f"Unexpected error processing invoice payment {invoice.id}: {str(e)}")
        #         await db.rollback()
        #         raise HTTPException(status_code=500, detail=f"Failed to process invoice payment: {str(e)}")

        # elif event.type == "invoice.payment_action_required":
        #     invoice = event.data.object
        #     logger.info(f"Payment action required for invoice: {invoice.id}")
            
        #     try:
        #         if not invoice.payment_intent:
        #             logger.error(f"No payment intent found for invoice: {invoice.id}")
        #             raise ValueError(f"No payment intent found for invoice: {invoice.id}")
                
        #         # Retrieve payment intent with expanded details
        #         try:
        #             payment_intent = stripe_client.PaymentIntent.retrieve(
        #                 invoice.payment_intent,
        #                 expand=['payment_method']
        #             )
        #             logger.info(f"Retrieved payment intent: {payment_intent.id}, status: {payment_intent.status}")
                    
        #             # Get the specific action required
        #             if payment_intent.status == 'requires_action':
        #                 action_type = payment_intent.next_action.type if payment_intent.next_action else 'unknown'
        #                 logger.info(f"Action required: {action_type} for payment: {payment_intent.id}")
                    
        #         except stripe.error.StripeError as e:
        #             logger.error(f"Failed to retrieve payment intent: {str(e)}")
        #             raise HTTPException(
        #                 status_code=500,
        #                 detail=f"Failed to retrieve payment details: {str(e)}"
        #             )
                
        #         # Update invoice in database
        #         try:
        #             invoice_service = InvoiceService(db)
        #             db_invoice = await invoice_service.create_or_update_invoice({
        #                 "invoice": invoice,
        #                 "payment_intent": payment_intent
        #             })
        #             logger.info(f"Updated invoice status in database: {invoice.id}")
                    
        #             # Send notification to customer about required action
        #             logger.info(f"Sending action required notification for invoice: {invoice.id}")
        #             await invoice_service.send_invoice_notification(db_invoice)
        #             logger.info(f"Successfully sent action required notification")
                    
        #         except ValueError as e:
        #             logger.error(f"Failed to process invoice update: {str(e)}")
        #             await db.rollback()
        #             raise HTTPException(status_code=400, detail=str(e))
                
        #     except Exception as e:
        #         logger.error(f"Unexpected error processing payment action required: {str(e)}")
        #         await db.rollback()
        #         raise HTTPException(
        #             status_code=500,
        #             detail=f"Failed to process payment action requirement: {str(e)}"
        #         )

        # elif event.type == "invoice.upcoming":
        #     invoice = event.data.object
        #     logger.info(f"Processing upcoming invoice: {invoice.id}")
            
        #     try:
        #         # First, get the subscription details
        #         if invoice.subscription:
        #             try:
        #                 logger.info(f"Retrieving subscription details for invoice: {invoice.id}")
        #                 subscription = stripe.Subscription.retrieve(
        #                     invoice.subscription,
        #                     expand=['latest_invoice', 'pending_setup_intent']
        #                 )
                        
        #                 # Update subscription in database
        #                 subscription_service = SubscriptionService(db)
        #                 await subscription_service.create_or_update_subscription({
        #                     "subscription": subscription
        #                 })
        #                 logger.info(f"Updated subscription: {subscription.id}")
                        
        #                 # Check for upcoming changes
        #                 if subscription.cancel_at:
        #                     logger.info(f"Subscription {subscription.id} is scheduled to cancel at {subscription.cancel_at}")
        #                 if subscription.trial_end:
        #                     logger.info(f"Subscription {subscription.id} trial ends at {subscription.trial_end}")
                            
        #             except stripe.error.StripeError as e:
        #                 logger.error(f"Failed to retrieve subscription: {str(e)}")
        #                 raise HTTPException(
        #                     status_code=500,
        #                     detail=f"Failed to retrieve subscription details: {str(e)}"
        #                 )
                
        #         # Create/update the invoice record
        #         try:
        #             invoice_service = InvoiceService(db)
        #             db_invoice = await invoice_service.create_or_update_invoice({
        #                 "invoice": invoice,
        #                 "subscription": subscription if invoice.subscription else None
        #             })
        #             logger.info(f"Created/updated upcoming invoice record: {db_invoice.id}")
                    
        #             # Send notification about upcoming invoice
        #             await invoice_service.send_invoice_notification(db_invoice)
        #             logger.info(f"Sent upcoming invoice notification for: {db_invoice.id}")
                    
        #         except ValueError as e:
        #             logger.error(f"Failed to process upcoming invoice: {str(e)}")
        #             await db.rollback()
        #             raise HTTPException(status_code=400, detail=str(e))
                    
        #     except Exception as e:
        #         logger.error(f"Unexpected error processing upcoming invoice: {str(e)}")
        #         await db.rollback()
        #         raise HTTPException(
        #             status_code=500,
        #             detail=f"Failed to process upcoming invoice: {str(e)}"
        #         )

        # # Refund event handlers
        # elif event.type == "charge.refunded":
        #     charge = event.data.object
        #     logger.info(f"Processing refund for charge: {charge.id}")
            
        #     try:
        #         # Get associated invoice if it exists
        #         invoice = None
        #         if charge.invoice:
        #             try:
        #                 logger.info(f"Retrieving invoice details for charge: {charge.id}")
        #                 invoice = stripe_client.Invoice.retrieve(
        #                     charge.invoice,
        #                     expand=['subscription']
        #                 )
        #                 logger.info(f"Retrieved invoice: {invoice.id}")
        #             except stripe.error.StripeError as e:
        #                 logger.error(f"Failed to retrieve invoice for charge {charge.id}: {str(e)}")
        #                 # Don't fail the whole process if invoice retrieval fails
                
        #         # Initialize services
        #         refund_service = RefundService(db)
        #         charge_service = ChargeService(db)
        #         invoice_service = InvoiceService(db)
        #         subscription_service = SubscriptionService(db)
                
        #         # 1. Update charge record first
        #         logger.info(f"Updating charge {charge.id} with refund amount: {charge.amount_refunded}, status: {charge.status}")
        #         db_charge = await charge_service.update_charge_status({
        #             "charge": charge,
        #             "event": event.to_dict()
        #         })
        #         logger.info(f"Successfully updated charge record: {charge.id}, refunded amount: {db_charge.amount_refunded}")
                
        #         # 2. Update invoice and subscription if they exist
        #         if invoice:
        #             # Update subscription if it exists
        #             if invoice.subscription:
        #                 logger.info(f"Found related subscription {invoice.subscription.id} for invoice {invoice.id}")
        #                 status_data = {
        #                     "status": invoice.subscription.status,
        #                     "current_period_start": datetime.fromtimestamp(invoice.subscription.current_period_start),
        #                     "current_period_end": datetime.fromtimestamp(invoice.subscription.current_period_end),
        #                     "cancel_at": datetime.fromtimestamp(invoice.subscription.cancel_at) if invoice.subscription.get("cancel_at") else None,
        #                     "canceled_at": datetime.fromtimestamp(invoice.subscription.canceled_at) if invoice.subscription.get("canceled_at") else None,
        #                     "ended_at": datetime.fromtimestamp(invoice.subscription.ended_at) if invoice.subscription.get("ended_at") else None,
        #                     "cancel_at_period_end": invoice.subscription.get("cancel_at_period_end", False),
        #                     "cancellation_reason": invoice.subscription.get("cancellation_details", {}).get("reason"),
        #                     "event_data": event.to_dict()
        #                 }
        #                 db_subscription = await subscription_service.update_subscription_status(invoice.subscription.id, status_data)
        #                 logger.info(f"Successfully updated subscription record: {invoice.subscription.id}, status: {db_subscription.status}")
                    
        #             # Update invoice status
        #             logger.info(f"Updating invoice {invoice.id} status")
        #             status_data = {
        #                 "status": invoice.status,
        #                 "paid_at": datetime.fromtimestamp(invoice.status_transitions.paid_at) if invoice.status == "paid" and getattr(invoice.status_transitions, "paid_at", None) else None,
        #                 "amount_paid": invoice.amount_paid,
        #                 "amount_remaining": invoice.amount_remaining,
        #                 "event_data": event.to_dict()
        #             }
        #             db_invoice = await invoice_service.update_invoice_status(invoice.id, status_data)
        #             logger.info(f"Successfully updated invoice record: {invoice.id}, status: {db_invoice.status}")
                
        #         # 3. Process each refund in the charge
        #         for refund_obj in charge.refunds.data:
        #             try:
        #                 logger.info(f"Processing refund: {refund_obj.id}")
        #                 db_refund = await refund_service.create_or_update_refund({
        #                     "refund": refund_obj,
        #                     "event": event.to_dict()
        #                 })
        #                 logger.info(f"Successfully processed refund: {refund_obj.id}, status: {db_refund.status}")
                            
        #             except ValueError as e:
        #                 logger.error(f"Failed to process refund {refund_obj.id}: {str(e)}")
        #                 await db.rollback()
        #                 raise HTTPException(status_code=400, detail=str(e))
                        
        #     except Exception as e:
        #         logger.error(f"Unexpected error processing charge refund: {str(e)}")
        #         await db.rollback()
        #         raise HTTPException(
        #             status_code=500,
        #             detail=f"Failed to process charge refund: {str(e)}"
        #         )

        # elif event.type == "charge.refund.updated":
        #     refund = event.data.object
        #     logger.info(f"Processing refund update: {refund.id}, charge: {refund.charge}")
            
        #     try:
        #         # Get associated charge and invoice
        #         charge = stripe_client.Charge.retrieve(refund.charge)
        #         invoice = None
        #         if charge.invoice:
        #             try:
        #                 logger.info(f"Retrieving invoice details for charge: {charge.id}")
        #                 invoice = stripe_client.Invoice.retrieve(
        #                     charge.invoice,
        #                     expand=['subscription']
        #                 )
        #                 logger.info(f"Retrieved invoice: {invoice.id}")
        #             except stripe.error.StripeError as e:
        #                 logger.error(f"Failed to retrieve invoice for charge {charge.id}: {str(e)}")
        #                 # Don't fail the whole process if invoice retrieval fails
                
        #         # Initialize services
        #         refund_service = RefundService(db)
        #         charge_service = ChargeService(db)
        #         invoice_service = InvoiceService(db)
        #         subscription_service = SubscriptionService(db)
                
        #         # 1. Update refund record
        #         logger.info(f"Attempting to update refund {refund.id} in database")
        #         db_refund = await refund_service.create_or_update_refund({
        #             "refund": refund,
        #             "event": event.to_dict()
        #         })
        #         logger.info(f"Successfully updated refund record: {refund.id}, status: {db_refund.status}")

        #         # 2. Update charge record
        #         logger.info(f"Updating charge {charge.id} with refund amount: {charge.amount_refunded}, status: {charge.status}")
        #         db_charge = await charge_service.update_charge_status({
        #             "charge": charge,
        #             "event": event.to_dict()
        #         })
        #         logger.info(f"Successfully updated charge record: {charge.id}, refunded amount: {db_charge.amount_refunded}")

        #         # 3. Update invoice and subscription if they exist
        #         if invoice:
        #             # Update subscription if it exists
        #             if invoice.subscription:
        #                 logger.info(f"Found related subscription {invoice.subscription.id} for invoice {invoice.id}")
        #                 status_data = {
        #                     "status": invoice.subscription.status,
        #                     "current_period_start": datetime.fromtimestamp(invoice.subscription.current_period_start),
        #                     "current_period_end": datetime.fromtimestamp(invoice.subscription.current_period_end),
        #                     "cancel_at": datetime.fromtimestamp(invoice.subscription.cancel_at) if invoice.subscription.get("cancel_at") else None,
        #                     "canceled_at": datetime.fromtimestamp(invoice.subscription.canceled_at) if invoice.subscription.get("canceled_at") else None,
        #                     "ended_at": datetime.fromtimestamp(invoice.subscription.ended_at) if invoice.subscription.get("ended_at") else None,
        #                     "cancel_at_period_end": invoice.subscription.get("cancel_at_period_end", False),
        #                     "cancellation_reason": invoice.subscription.get("cancellation_details", {}).get("reason"),
        #                     "event_data": event.to_dict()
        #                 }
        #                 db_subscription = await subscription_service.update_subscription_status(invoice.subscription.id, status_data)
        #                 logger.info(f"Successfully updated subscription record: {invoice.subscription.id}, status: {db_subscription.status}")
                    
        #             # Update invoice status
        #             logger.info(f"Updating invoice {invoice.id} status")
        #             status_data = {
        #                 "status": invoice.status,
        #                 "paid_at": datetime.fromtimestamp(invoice.status_transitions.paid_at) if invoice.status == "paid" and getattr(invoice.status_transitions, "paid_at", None) else None,
        #                 "amount_paid": invoice.amount_paid,
        #                 "amount_remaining": invoice.amount_remaining,
        #                 "event_data": event.to_dict()
        #             }
        #             db_invoice = await invoice_service.update_invoice_status(invoice.id, status_data)
        #             logger.info(f"Successfully updated invoice record: {invoice.id}, status: {db_invoice.status}")
                    
        #             # Send invoice notification if needed
        #             await invoice_service.send_invoice_notification(db_invoice)
        #             logger.info(f"Sent invoice notification for {invoice.id}")
        #         else:
        #             logger.info(f"No invoice found for charge {charge.id}, skipping invoice update")
                    
        #     except ValueError as e:
        #         logger.error(f"Failed to update refund {refund.id}: {str(e)}")
        #         raise HTTPException(status_code=400, detail=str(e))
                
        #     except Exception as e:
        #         logger.error(f"Unexpected error updating refund {refund.id} for charge {refund.charge}: {str(e)}")
        #         raise HTTPException(
        #             status_code=500,
        #             detail=f"Failed to update refund: {str(e)}"
        #         )

        return JSONResponse({"status": "success"})

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/subscription/update", response_model=UpdateSubscriptionResponse)
async def update_subscription(
    request: UpdateSubscriptionRequest,
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """Update a subscription plan with proration handling"""
    try:
        stripe_client = initialize_stripe()
        subscription_service = SubscriptionService(db)

        # Verify subscription belongs to user
        subscription = await subscription_service.get_subscription(request.subscription_id)
        if not subscription or subscription.user_id != current_user["id"]:
            raise HTTPException(status_code=404, detail="Subscription not found")

        # Calculate proration details if requested
        if request.preview_proration:
            # Get upcoming invoice to preview changes
            upcoming_invoice = stripe_client.Invoice.upcoming(
                customer=subscription.stripe_customer_id,
                subscription=subscription.stripe_subscription_id,
                subscription_items=[{
                    'id': subscription.stripe_subscription_id,
                    'price': request.new_price_id,
                }]
            )

            return UpdateSubscriptionResponse(
                subscription_id=subscription.id,
                new_price_id=request.new_price_id,
                proration_date=datetime.fromtimestamp(upcoming_invoice.created),
                prorated_amount=upcoming_invoice.amount_due,
                currency=upcoming_invoice.currency,
                is_preview=True
            )

        # Update the subscription with proration
        updated_subscription = stripe_client.Subscription.modify(
            subscription.stripe_subscription_id,
            items=[{
                'id': subscription.stripe_subscription_id,
                'price': request.new_price_id,
            }],
            proration_behavior='always_invoice' if request.prorate else 'none',
        )

        # Update our database record
        db_subscription = await subscription_service.create_or_update_subscription({
            "subscription": updated_subscription
        })

        return UpdateSubscriptionResponse(
            subscription_id=db_subscription.id,
            new_price_id=request.new_price_id,
            proration_date=datetime.fromtimestamp(updated_subscription.current_period_start),
            prorated_amount=updated_subscription.items.data[0].price.unit_amount,
            currency=db_subscription.currency,
            is_preview=False
        )

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/refund", response_model=RefundResponse)
async def create_refund(
    request: RefundRequest,
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """Create a refund for a subscription payment"""
    try:
        stripe_client = initialize_stripe()
        subscription_service = SubscriptionService(db)
        refund_service = RefundService(db)

        # Verify subscription belongs to user
        subscription = await subscription_service.get_subscription(request.subscription_id)
        if not subscription or subscription.user_id != current_user["id"]:
            raise HTTPException(status_code=404, detail="Subscription not found")

        # Get latest charge for the subscription
        charges = stripe_client.Charge.list(
            customer=subscription.stripe_customer_id,
            limit=1
        )

        if not charges.data:
            raise HTTPException(status_code=404, detail="No charges found for this subscription")

        # Process the refund
        refund = await refund_service.process_refund(
            charge_id=charges.data[0].id,
            amount=request.amount,
            reason=request.reason
        )

        return RefundResponse(
            id=refund.id,
            amount=refund.amount,
            currency=refund.currency,
            status=refund.status,
            reason=refund.reason
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/invoices", response_model=List[InvoiceResponse])
async def list_invoices(
    limit: int = 10,
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """List invoices for the current user"""
    try:
        invoice_service = InvoiceService(db)
        invoices = await invoice_service.get_user_invoices(current_user["id"], limit)
        
        return [
            InvoiceResponse(
                id=invoice.id,
                subscription_id=invoice.subscription_id,
                amount=invoice.amount,
                currency=invoice.currency,
                status=invoice.status,
                invoice_pdf=invoice.invoice_pdf,
                hosted_invoice_url=invoice.hosted_invoice_url,
                due_date=invoice.due_date,
                paid_at=invoice.paid_at,
                period_start=invoice.period_start,
                period_end=invoice.period_end,
                is_paid=invoice.is_paid
            ) for invoice in invoices
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/subscription/{subscription_id}/invoices", response_model=List[InvoiceResponse])
async def list_subscription_invoices(
    subscription_id: str,
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """List all invoices for a specific subscription"""
    try:
        subscription_service = SubscriptionService(db)
        invoice_service = InvoiceService(db)

        # Verify subscription belongs to user
        subscription = await subscription_service.get_subscription(subscription_id)
        if not subscription or subscription.user_id != current_user["id"]:
            raise HTTPException(status_code=404, detail="Subscription not found")

        invoices = await invoice_service.get_subscription_invoices(subscription_id)
        
        return [
            InvoiceResponse(
                id=invoice.id,
                subscription_id=invoice.subscription_id,
                amount=invoice.amount,
                currency=invoice.currency,
                status=invoice.status,
                invoice_pdf=invoice.invoice_pdf,
                hosted_invoice_url=invoice.hosted_invoice_url,
                due_date=invoice.due_date,
                paid_at=invoice.paid_at,
                period_start=invoice.period_start,
                period_end=invoice.period_end,
                is_paid=invoice.is_paid
            ) for invoice in invoices
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/subscription/{subscription_id}/upcoming-invoice", response_model=UpcomingInvoiceResponse)
async def get_upcoming_invoice(
    subscription_id: str,
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """Get the upcoming invoice for a subscription"""
    try:
        subscription_service = SubscriptionService(db)
        invoice_service = InvoiceService(db)

        # Verify subscription belongs to user
        subscription = await subscription_service.get_subscription(subscription_id)
        if not subscription or subscription.user_id != current_user["id"]:
            raise HTTPException(status_code=404, detail="Subscription not found")

        upcoming = await invoice_service.get_upcoming_invoice(subscription_id)
        return UpcomingInvoiceResponse(**upcoming)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/payment-methods/setup", response_model=SetupIntentResponse)
async def create_setup_intent(
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """Create a SetupIntent for securely collecting payment method details"""
    try:
        payment_method_service = PaymentMethodService(db)
        setup_intent = await payment_method_service.create_setup_intent(current_user["id"])
        return SetupIntentResponse(**setup_intent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/payment-methods", response_model=PaymentMethodResponse)
async def add_payment_method(
    payment_method_id: str,
    is_default: bool = False,
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """Store a payment method after successful setup"""
    try:
        payment_method_service = PaymentMethodService(db)
        payment_method = await payment_method_service.add_payment_method({
            "user_id": current_user["id"],
            "stripe_payment_method_id": payment_method_id,
            "is_default": is_default
        })
        return PaymentMethodResponse(**payment_method.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/payment-methods", response_model=List[PaymentMethodResponse])
async def list_payment_methods(
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """List all payment methods for the current user"""
    try:
        payment_method_service = PaymentMethodService(db)
        payment_methods = await payment_method_service.list_payment_methods(current_user["id"])
        return [PaymentMethodResponse(**pm.to_dict()) for pm in payment_methods]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/payment-methods/{payment_method_id}/default", response_model=PaymentMethodResponse)
async def set_default_payment_method(
    payment_method_id: str,
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """Set a payment method as default"""
    try:
        payment_method_service = PaymentMethodService(db)
        payment_method = await payment_method_service.set_default_payment_method(
            payment_method_id,
            current_user["id"]
        )
        return PaymentMethodResponse(**payment_method.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/payment-methods/{payment_method_id}")
async def delete_payment_method(
    payment_method_id: str,
    current_user: Dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """Delete a payment method"""
    try:
        payment_method_service = PaymentMethodService(db)
        await payment_method_service.delete_payment_method(
            payment_method_id,
            current_user["id"]
        )
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/subscription", response_model=CustomerResponse)
async def get_subscription_status(current_user: Dict = Depends(validate_session)):
    """
    Get the current user's subscription status.
    """
    try:
        stripe_client = initialize_stripe()
        customers = stripe_client.Customer.list(email=current_user["email"], limit=1)

        if not customers.data:
            return CustomerResponse(
                id="",
                email=current_user["email"],
                name=current_user.get("name"),
                subscription_status="none"
            )

        customer = customers.data[0]
        return CustomerResponse(
            id=customer.id,
            email=customer.email,
            name=customer.name,
            metadata=customer.metadata,
            subscription_status=customer.metadata.get("subscription_status", "none")
        )

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")