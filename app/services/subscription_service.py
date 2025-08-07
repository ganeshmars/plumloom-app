from datetime import datetime
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.models.subscription import Subscription
from app.core.stripe_config import initialize_stripe

class SubscriptionService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.stripe = initialize_stripe()

    async def create_or_update_subscription(self, subscription_data: dict, is_test: bool = False) -> Subscription:
        """Create or update a subscription record from Stripe webhook data"""
        logger = logging.getLogger(__name__)
        try:
            logger.info(f"Processing subscription data: {subscription_data.get('subscription', {}).get('id') or subscription_data.get('id')}")
            
            stripe_sub = subscription_data.get("subscription", subscription_data)
            if not stripe_sub.get("customer"):
                raise ValueError("No customer ID found in subscription data")
                
            customer = self.stripe.Customer.retrieve(stripe_sub["customer"])
            logger.info(f"Retrieved customer data: {customer.id}")
            
            # For test events, use a dummy user_id if not present
            user_id = customer.metadata.get("user_id")
            if not user_id:
                if is_test:
                    user_id = "test_user_123"
                    logger.info(f"Using test user_id for customer {customer.id}")
                else:
                    raise ValueError(f"No user_id found in customer metadata for customer {customer.id}")
            
            # Store complete event data for future reference
            subscription = {
                "stripe_event_data": stripe_sub,  # Store the complete event data
                "id": stripe_sub["id"],
                "user_id": user_id,
                "stripe_customer_id": stripe_sub["customer"],
                "stripe_subscription_id": stripe_sub["id"],
                "status": stripe_sub["status"],
                "current_period_start": datetime.fromtimestamp(stripe_sub["current_period_start"]),
                "current_period_end": datetime.fromtimestamp(stripe_sub["current_period_end"]),
                "cancel_at": datetime.fromtimestamp(stripe_sub["cancel_at"]) if stripe_sub.get("cancel_at") else None,
                "canceled_at": datetime.fromtimestamp(stripe_sub["canceled_at"]) if stripe_sub.get("canceled_at") else None,
                "ended_at": datetime.fromtimestamp(stripe_sub["ended_at"]) if stripe_sub.get("ended_at") else None,
                "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
                "cancellation_reason": stripe_sub.get("cancellation_details", {}).get("reason"),
                "cancellation_feedback": stripe_sub.get("cancellation_details", {}).get("feedback"),
                "cancellation_comment": stripe_sub.get("cancellation_details", {}).get("comment"),
                "trial_start": datetime.fromtimestamp(stripe_sub["trial_start"]) if stripe_sub.get("trial_start") else None,
                "trial_end": datetime.fromtimestamp(stripe_sub["trial_end"]) if stripe_sub.get("trial_end") else None,
                "is_trial": bool(stripe_sub.get("trial_end"))
            }

            # Only fetch price information for active subscriptions
            if stripe_sub["status"] not in ["canceled", "incomplete_expired"]:
                try:
                    price = self.stripe.Price.retrieve(stripe_sub["items"]["data"][0]["price"]["id"])
                    logger.info(f"Retrieved price data: {price.id}")
                    subscription.update({
                        "plan_id": price["product"],
                        "price_id": price["id"],
                        "price_amount": price["unit_amount"],
                        "currency": price["currency"],
                        "interval": price["recurring"]["interval"],
                        "features": json.dumps(self._get_subscription_features(price["product"]))
                    })
                except Exception as e:
                    logger.warning(f"Failed to retrieve price info for subscription {stripe_sub['id']}: {str(e)}")
            
            logger.info(f"Prepared subscription data for database: {subscription['id']}")

            # Check if subscription exists
            stmt = select(Subscription).where(Subscription.id == stripe_sub["id"])
            result = await self.db.execute(stmt)
            existing_sub = result.scalar_one_or_none()

            try:
                if existing_sub:
                    logger.info(f"Updating existing subscription: {existing_sub.id}")
                    # Update existing subscription
                    await self.db.execute(
                        update(Subscription)
                        .where(Subscription.id == stripe_sub["id"])
                        .values(**subscription)
                    )
                    updated_sub = await self.get_subscription(stripe_sub["id"])
                    logger.info(f"Successfully updated subscription: {updated_sub.id}")
                    return updated_sub
                else:
                    # Only create new subscription if it's not a deletion event
                    if stripe_sub["status"] != "canceled":
                        # For non-test subscriptions, ensure only one active subscription per user
                        if not is_test:
                            # Set all other subscriptions to canceled
                            await self.db.execute(
                                update(Subscription)
                                .where(
                                    Subscription.user_id == user_id,
                                    Subscription.status == "active"
                                )
                                .values(status="canceled")
                            )

                        logger.info(f"Creating new subscription: {subscription['id']}")
                        # Create new subscription
                        new_sub = Subscription(**subscription)
                        self.db.add(new_sub)
                        await self.db.flush()
                        logger.info(f"Successfully created new subscription: {new_sub.id}")
                        return new_sub
                    else:
                        logger.info(f"Ignoring deleted subscription that doesn't exist: {stripe_sub['id']}")
                        return None
            except SQLAlchemyError as e:
                logger.error(f"Database error while saving subscription: {str(e)}")
                raise

        except Exception as e:
            logger.error(f"Error processing subscription data: {str(e)}")
            raise

    async def get_subscription(self, subscription_id: str) -> Subscription:
        """Get a subscription by ID"""
        stmt = select(Subscription).where(Subscription.id == subscription_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_subscription(self, user_id: str) -> Subscription:
        """Get user's active subscription"""
        stmt = select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == "active"
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def cancel_subscription(self, subscription_id: str) -> Subscription:
        """Cancel a subscription"""
        stripe_sub = self.stripe.Subscription.delete(subscription_id)
        return await self.create_or_update_subscription(stripe_sub)

    def _get_subscription_features(self, product_id: str) -> dict:
        """Get features for a subscription plan"""
        product = self.stripe.Product.retrieve(product_id)
        features = {
            "api_calls_limit": int(product.metadata.get("api_calls_limit", 1000)),
            "storage_limit_gb": float(product.metadata.get("storage_limit_gb", 5)),
            "max_users": int(product.metadata.get("max_users", 1)),
            "support_level": product.metadata.get("support_level", "basic"),
            "custom_features": json.loads(product.metadata.get("custom_features", "{}"))
        }
        return features

    async def update_subscription_status(self, subscription_id: str, status_data: dict) -> Subscription:
        """Update subscription status and related fields
        
        Args:
            subscription_id: The Stripe subscription ID
            status_data: Dictionary containing status update data including:
                - status: New subscription status
                - current_period_start: Start of current period
                - current_period_end: End of current period
                - cancel_at: When subscription will be canceled
                - canceled_at: When subscription was canceled
                - ended_at: When subscription ended
                - cancel_at_period_end: Whether subscription will be canceled at period end
                - cancellation_reason: Reason for cancellation
                - event_data: Complete Stripe event data
        
        Returns:
            Updated Subscription object
        """
        logger = logging.getLogger(__name__)
        
        # Get existing subscription
        stmt = select(Subscription).where(Subscription.id == subscription_id)
        result = await self.db.execute(stmt)
        subscription = result.scalar_one_or_none()
        
        if not subscription:
            raise ValueError(f"Subscription not found: {subscription_id}")
            
        logger.info(f"Updating subscription {subscription_id} status to {status_data.get('status')}")
            
        # Update subscription fields
        update_data = {
            "status": status_data.get("status"),
            "current_period_start": status_data.get("current_period_start"),
            "current_period_end": status_data.get("current_period_end"),
            "cancel_at": status_data.get("cancel_at"),
            "canceled_at": status_data.get("canceled_at"),
            "ended_at": status_data.get("ended_at"),
            "cancel_at_period_end": status_data.get("cancel_at_period_end", False),
            "cancellation_reason": status_data.get("cancellation_reason"),
            "stripe_event_data": status_data.get("event_data"),
            "updated_at": datetime.now(UTC)
        }
        
        # Update subscription record
        try:
            await self.db.execute(
                update(Subscription)
                .where(Subscription.id == subscription_id)
                .values(**update_data)
            )
            await self.db.commit()
            
            # Refresh and return updated subscription
            await self.db.refresh(subscription)
            logger.info(f"Successfully updated subscription {subscription_id} status")
            return subscription
            
        except Exception as e:
            logger.error(f"Failed to update subscription {subscription_id} status: {str(e)}")
            raise

    async def check_subscription_access(self, user_id: str, feature: str, quantity: int = 1) -> bool:
        """Check if user has access to a specific feature with given quantity"""
        subscription = await self.get_active_subscription(user_id)
        if not subscription:
            return False

        features = json.loads(subscription.features)
        
        # Check specific feature limits
        if feature == "api_calls":
            return quantity <= features["api_calls_limit"]
        elif feature == "storage":
            return quantity <= features["storage_limit_gb"] * 1024  # Convert GB to MB
        elif feature == "users":
            return quantity <= features["max_users"]
        
        # Check custom features
        custom_features = features.get("custom_features", {})
        return feature in custom_features and quantity <= custom_features[feature]
