from typing import Dict
from fastapi import APIRouter, Depends, HTTPException
from app.core.auth import validate_session

router = APIRouter(tags=["subscription"])

@router.get("/subscription/status")
async def get_subscription_status(
    current_user: Dict = Depends(validate_session)
):
    """Get current user's subscription status"""
    # For testing, we'll consider users have a subscription
    return {
        "has_active_subscription": True,
        "subscription_details": {
            "user_id": current_user["id"],
            "plan": "test_plan",
            "status": "active",
            "product_id": "prod_RupgmRiJAAu0m6"  # Your Free Trial product
        }
    }

@router.get("/test-premium")
async def test_premium_feature(
    current_user: Dict = Depends(validate_session)
):
    """Test endpoint that requires an active subscription"""
    # For testing, we'll simulate subscription check
    return {
        "message": "You have access to premium features!",
        "user_id": current_user["id"],
        "subscription": "test_plan"
    }

@router.get("/test-basic")
async def test_basic_feature():
    """Test endpoint that's accessible to all users"""
    return {
        "message": "This is a basic feature available to all users!"
    }
