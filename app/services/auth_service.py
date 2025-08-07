from app.models.users import User
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from app.core.config import get_settings
import redis.asyncio as aioredis
from app.core.redis import get_redis  # Import the Redis connection function
import jwt  # Import the JWT library
from datetime import datetime, timedelta
from sqlalchemy import select, update
from fastapi import HTTPException
from app.core.logging_config import logger
from app.core.auth import hash_password
import uuid

from app.schemas.workspace import WorkspaceCreate
from app.services.workspace_service import WorkspaceService

settings = get_settings()

async def get_user_by_email(db: AsyncSession, email: str) -> User:
    """Fetch user by email from the database."""
    user_query = await db.execute(select(User).where(User.email == email))
    return user_query.scalar_one_or_none()

def generate_tenant_id(name: str) -> str:
    current_time = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"{name}_{current_time}"


# Function to generate JWT tokens
def create_access_token(data: dict, expires_delta: timedelta = None):
    """Generate an access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(days=30))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict):
    """Generate a refresh token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=30)  # Default expiration time for refresh token
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def sign_up(db: AsyncSession, user_data: dict) -> User:
    """Create a new user in the database."""
    try:
        existing_user = await db.execute(select(User).where(User.email == user_data['email']))
        if existing_user.scalar():
            raise HTTPException(status_code=400, detail="User already exists")
        user = User(
            id=user_data['id'],  # Generate a unique ID (e.g., UUID)
            email=user_data['email'],
            is_email_verified=True,
            is_phone_verified=False,
            status="active",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user 
    except Exception as e:
        print(f"Error creating user: {str(e)}")
        raise e

async def store_otp_in_redis(email: str, otp: str):
    """Store OTP in Redis with a 5-minute expiration."""
    redis_gen = get_redis()
    redis = await anext(redis_gen)
    await redis.set(email, otp, ex=300)  # Store OTP for 5 minutes

async def verify_otp_in_redis(email: str, otp: str) -> bool:
    """Verify OTP from Redis."""
    logger.info(f"Verifying OTP for email: {email}, OTP: {otp}")
    
    redis_gen = get_redis()
    redis = await anext(redis_gen)
    stored_otp = await redis.get(email)
    if stored_otp == otp:
        await redis.delete(email)
        return True
    else:
        return False

def send_mail(to_email: str, subject: str, html_content: str):
    """Send email using SendGrid."""
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=to_email,
        subject=subject,
        html_content=html_content
    )
    try:
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        response = sg.send(message)
        return response
    except Exception as e:
        print(f"Error sending email: {str(e)}")

def send_otp_email(to_email: str, otp: str):
    """Send OTP email using SendGrid."""
    try:
        response = send_mail(to_email, 'Your OTP Code', f'<strong>Your OTP code is: {otp}</strong>')
        return response
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        raise e

async def send_password_reset_email(to_email: str, redirect_url: str = ""):
    """Send password reset email using SendGrid."""
    try:
        token = create_access_token(data={"sub": to_email, "type": "reset_token"}, expires_delta=timedelta(minutes=5))
        response = send_mail(to_email, 'Password Reset Request', f'<strong>Click <a href="{redirect_url}?token={token}">here</a> to reset your password</strong>')
        return response
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        raise e
    
async def expire_token(token: str):
    """Expire a token by storing it in a blacklist."""
    logger.info(f"Expiring token: {token}")
    

async def logout_user(db: AsyncSession, user: dict):
    """Logout user by revoking their session."""
    try:
        logger.info(f"Logging out user with ID: {user}")
        token = user.get("session_token")

        return {"message": "User logged out successfully"}
    except Exception as e:
        print(f"Error logging out user: {str(e)}")
        raise HTTPException(status_code=500, detail="Error logging out user")
    
async def create_user_profile(db: AsyncSession, user_email: str) -> User:
    """Create a user profile in the database."""
    try:
        existing_user = await get_user_by_email(db, user_email)
        if existing_user:
            raise HTTPException(status_code=400, detail="User already exists")

        #  Create new user profile
        new_user = User(
            id=str(uuid.uuid4()),
            email=user_email,
            is_email_verified=True,
            is_phone_verified=False
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        try:
            workspace_service = WorkspaceService(db)
            workspace_data = WorkspaceCreate(
                name="My Workspace",
                description="Default workspace",
                workspace_type="personal",
                icon_url=None
            )
            await workspace_service.create_workspace(workspace_data, new_user.id)
            logger.info(f"Created default workspace for new user {new_user.id}")
        except Exception as e:
            logger.error(f"Error creating default workspace for user {new_user.id}: {str(e)}")

        return new_user
    except Exception as e:
        logger.error(f"Error creating user profile: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating user profile")

async def update_user_profile(db: AsyncSession, update_data: dict) -> User:
    """Update user profile details in the database."""
    try:
        update_data = {key: value for key, value in update_data.items() if value}
        existing_user = await get_user_by_email(db, update_data['email'])
        logger.info(f"Existing user: {existing_user}")
        query = (
            update(User)
            .where(User.email == update_data['email'])
            .values(**update_data)
            .returning(User)  # Return the updated user
        )
        updated_user = await db.execute(query)
        await db.commit()
        user =  updated_user.scalar_one_or_none()  # Return the updated user object
        logger.info(f"Updated user profile: {user}")
        return user

    except Exception as e:
        logger.error(f"Error updating user profile: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating user profile")

