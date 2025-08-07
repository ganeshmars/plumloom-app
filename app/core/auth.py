import string
import random
from typing import Optional, Union
from datetime import datetime
from fastapi import HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from descope import (
    AuthException,
    DescopeClient,
    SESSION_TOKEN_NAME,
    REFRESH_SESSION_TOKEN_NAME,
)
from app.core.logging_config import logger
from app.core.config import get_settings
from app.core.database import get_db
from app.models import Customer, Subscription
from app.models.users import User
import jwt
import bcrypt
import httpx
from urllib.parse import urlencode
import json

settings = get_settings()
security = HTTPBearer()

# Default and extended session durations
DEFAULT_SESSION_DURATION = 24 * 60 * 60  # 24 hours in seconds
EXTENDED_SESSION_DURATION = 30 * 24 * 60 * 60  # 30 days in seconds



# Initialize Descope client
try:
    descope_client = DescopeClient(
        project_id=settings.DESCOPE_PROJECT_ID,
        management_key=settings.DESCOPE_MANAGEMENT_KEY,
        jwt_validation_leeway=DEFAULT_SESSION_DURATION
    )
except Exception as e:
    raise Exception(f"Failed to initialize Descope client: {str(e)}")

class AuthError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=401, detail=detail)

def get_token_from_header(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    try:
        return credentials.credentials
    except Exception:
        raise AuthError("Invalid authorization header")

async def validate_session(
    token: str = Depends(get_token_from_header),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Validate session token and return user information"""
    try:
        logger.info(f"Validating session token: {token}")
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_email = payload.get("sub")
        logger.info(f"User email: {user_email}")
        
        if user_email is None:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = await db.execute(select(User).where(User.email == user_email))
        user_db = user.scalar_one_or_none()

        if user_db is None:
            raise HTTPException(status_code=401, detail="User not found or session expired")

        return {
            "session_token": token,
            "user_id": user_db.id,
            "email": user_db.email,
            "name": user_db.name,
            "picture": user_db.picture,
            "roles": user_db.roles,
            "tenants": user_db.tenants,
            "loginIds": user_db.login_ids,
            "id": user_db.id,
            "givenName": user_db.given_name,
            "familyName": user_db.family_name,
            "phone": user_db.phone,
            "userTenants": user_db.tenants,
            "userTenantId": user_db.tenants[0]
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

async def validate_reset_token(token: str):
    """Validate the reset token and return user data."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        logger.info(f"JWT Payload: {payload}")
        user_email = payload.get("sub")
        if payload.get("type") != "reset_token":
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_email
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

async def check_active_subscription(db: AsyncSession = Depends(get_db)):
    """Dependency to check if the authenticated user has an active subscription.
    
    Args:
        db (AsyncSession): Database session
        
    Returns:
        Callable: A function that takes a user dict and returns either:
            - dict: User info if subscription is active
            - JSONResponse: Error response if subscription check fails
    """
    async def subscription_checker(user: dict = Depends(validate_session)) -> Union[dict, JSONResponse]:
        try:
            # Get customer record
            stmt = select(Customer).where(Customer.id == user['id'])
            result = await db.execute(stmt)
            customer = result.scalar_one_or_none()
            
            if not customer:
                logger.error("Customer record not found for user")
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "customer_not_found",
                        "message": "Customer record not found"
                    }
                )
            
            # Get latest active subscription
            stmt = select(Subscription).where(
                and_(
                    Subscription.user_id == customer.id,
                    Subscription.status == 'active'
                )
            ).order_by(Subscription.created_at.desc())
            
            result = await db.execute(stmt)
            subscription = result.scalar_one_or_none()
            
            if not subscription:
                logger.error("No active subscription found for user")
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "no_active_subscription",
                        "message": "No active subscription found"
                    }
                )
                
            return user

        except Exception as e:
            logger.error(f"Failed to verify subscription status: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "failed_to_verify_subscription",
                    "message": "Failed to verify subscription status"
                }
            )
    
    return subscription_checker


def check_roles(required_roles: list[str], tenant_id: str = None):
    """Role-based access control decorator with optional tenant-specific checks
    
    Args:
        required_roles (list[str]): List of required roles
        tenant_id (str, optional): Specific tenant to check roles against. If None, checks across all tenants.
    """
    async def role_checker(user: dict = Depends(validate_session)):
        user_tenants = user.get("userTenants", [])
        
        if tenant_id:
            # Check roles only in the specified tenant
            tenant = next((t for t in user_tenants if t.get("tenantId") == tenant_id), None)
            if not tenant:
                raise HTTPException(
                    status_code=403,
                    detail=f"User does not belong to tenant {tenant_id}"
                )
            user_roles = tenant.get("roleNames", [])
        else:
            # Check roles across all tenants
            user_roles = []
            for tenant in user_tenants:
                user_roles.extend(tenant.get("roleNames", []))
            
        if not any(role in user_roles for role in required_roles):
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        return user
    return role_checker

def has_any_role(roles: list[str], user_data: dict, tenant_id: str = None) -> bool:
    """Utility function to check if user has any of the specified roles
    
    Args:
        roles (list[str]): List of roles to check
        user_data (dict): User data from validate_session
        tenant_id (str, optional): Specific tenant to check roles against
    """
    user_tenants = user_data.get("userTenants", [])
    
    if tenant_id:
        tenant = next((t for t in user_tenants if t.get("tenantId") == tenant_id), None)
        if not tenant:
            return False
        user_roles = tenant.get("roleNames", [])
    else:
        user_roles = []
        for tenant in user_tenants:
            user_roles.extend(tenant.get("roleNames", []))
            
    return any(role in user_roles for role in roles)

# Social login verification
async def verify_social_login(token: str) -> dict:
    """Verify social login token and return user information"""
    try:
        jwt_response = descope_client.validate_session(session_token=token)
        return {
            "session_token": token,
            "refresh_token": jwt_response.refresh_token,
            "user": {
                "id": jwt_response.user.user_id,
                "email": jwt_response.user.email,
                "name": jwt_response.user.name,
                "picture": jwt_response.user.picture,
                "roles": jwt_response.roles
            }
        }
    except AuthException as e:
        raise AuthError(f"Invalid social login: {str(e)}")

# Google OAuth Implementation
async def get_google_oauth_url(is_login: bool = False) -> str:
    """
    Generate the Google OAuth URL for user authorization
    
    Parameters:
    - redirect_uri: URI to redirect after authentication
    - is_login: Flag to distinguish between login (True) and signup (False) flows
    """
    google_auth_url = "https://accounts.google.com/o/oauth2/auth"
    
    redirect_uri = settings.GOOGLE_REDIRECT_URI
    
    # Include is_login flag in the state parameter as JSON
    state = json.dumps({"is_login": is_login})
        
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "email profile",
        "access_type": "offline",
        "prompt": "consent",
        "state": state
    }
    return f"{google_auth_url}?{urlencode(params)}"

async def exchange_google_code(code: str, redirect_uri: str = None) -> dict:
    """
    Exchange authorization code for access and refresh tokens from Google
    """
    if not redirect_uri:
        redirect_uri = settings.GOOGLE_REDIRECT_URI
        
    token_url = "https://oauth2.googleapis.com/token"
    
    payload = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(token_url, data=payload)
        if response.status_code != 200:
            logger.error(f"Google token exchange failed: {response.text}")
            raise AuthError(f"Failed to exchange Google code: {response.text}")
            
        token_data = response.json()
        logger.info(f"Google token data: {token_data}")
        return token_data

async def get_google_user_info(access_token: str) -> dict:
    """
    Fetch user information from Google using the access token
    """
    user_info_url = "https://www.googleapis.com/oauth2/v2/userinfo"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            user_info_url, 
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to get Google user info: {response.text}")
            raise AuthError(f"Failed to get Google user info: {response.text}")
            
        user_info = response.json()
        logger.info(f"Google user info: {user_info}")
        return user_info

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
