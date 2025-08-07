from datetime import datetime
from typing import Optional
import random
import string
from descope import (
    DeliveryMethod,
    SESSION_TOKEN_NAME,
    REFRESH_SESSION_TOKEN_NAME,
    AssociatedTenant,
    AuthException
)
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Header, Query
from pydantic import BaseModel
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
# Query database for user by email
from sqlalchemy import select, update
from app.core.logging_config import logger
from app.models.users import User
from app.models.customer import Customer
from app.models.subscription import Subscription
from app.services.stripe_service import StripeService
        

from app.core.auth import (
    validate_session,
    verify_social_login,
    check_roles,
    AuthError,
    descope_client,
    get_token_from_header,
    DEFAULT_SESSION_DURATION,
    EXTENDED_SESSION_DURATION,
    validate_reset_token,
    hash_password,
    verify_password,
    get_google_oauth_url,
    exchange_google_code,
    get_google_user_info
)
from app.core.auth_middleware import ensure_customer_exists
from app.services.workspace_service import WorkspaceService
from app.schemas.workspace import WorkspaceCreate
from app.core.database import get_db
from app.core.config import get_settings
from app.core.stripe_config import initialize_stripe
from app.services.customer_service import CustomerService
from app.users.schema import (
    SocialLoginResponse,
    UserResponse,
    SignUpRequest,
    SignInRequest,
    PasswordSignInRequest,
    OTPVerifyRequest,
    AuthResponse,
    SetPasswordRequest,
    UpdateUserRequest,
    UpdateEmailRequest,
    CreateUserProfileRequest,
    ResetPasswordResponse
)
from app.schemas.auth import (
    OAuthStartResponse,
    PasswordSignUpRequest,
    UpdatePasswordRequest,
    UpdateDisplayNameRequest,
    SendPasswordResetEmailResponse
)
from app.core.storage import upload_file_to_gcs, delete_file_from_gcs
from app.core.constants import GCS_STORAGE_BUCKET
from app.services.auth_service import ( 
    sign_up, 
    send_otp_email, 
    store_otp_in_redis, 
    verify_otp_in_redis, 
    create_access_token, 
    create_refresh_token, 
    logout_user, 
    update_user_profile,
    create_user_profile,
    get_user_by_email,
    send_password_reset_email,
    generate_tenant_id
)
import json

settings = get_settings()
security = HTTPBearer()
router = APIRouter(prefix="/auth", tags=["auth"])


class PasswordResetEmailRequest(BaseModel):
    email: str
    redirect_url: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class AuthResponse(BaseModel):
    session_token: str
    refresh_token: str
    user: UserResponse
    subscription_type: Optional[str] = None
    first_login: bool

# @router.get("/social/start/{provider}", response_model=OAuthStartResponse)
# async def start_social_auth(provider: str, return_url: str = ""):
#     """
#     Start OAuth flow for social login
#     Returns URL to redirect user to provider's login page
#     """
#     try:
#         response = descope_client.oauth.start(provider=provider, return_url=return_url)
#         return OAuthStartResponse(url=response["url"])
#     except AuthException as e:
#         raise HTTPException(status_code=400, detail=str(e))
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#
# @router.get("/social/exchange")
# async def exchange_social_token(code: str):
#     """
#     Exchange OAuth code for session tokens
#     Called by frontend after OAuth provider redirects back with code
#     """
#     try:
#         response = descope_client.oauth.exchange_token(code)
#         return response
#     except AuthException as e:
#         raise HTTPException(status_code=401, detail=str(e))
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#
# @router.post("/social/verify", response_model=SocialLoginResponse)
# async def verify_social_auth(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
#     """
#     Verify social authentication token and return user session
#     This endpoint is called after successful social login from frontend
#     """
#     try:
#         return await verify_social_login(credentials.credentials)
#     except AuthError as e:
#         raise HTTPException(status_code=401, detail=str(e))
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

# Google OAuth endpoints
@router.get("/google/login")
async def google_login(is_login: bool = Query(False)):
    """
    Start Google OAuth flow and redirect user to Google login page
    
    Parameters:
    - redirect_uri: URI to redirect after authentication
    - is_login: Flag to distinguish between login (True) and signup (False) flows
    """
    try:
        # Pass the is_login parameter as part of the state
        oauth_url = await get_google_oauth_url(is_login=is_login)
        return JSONResponse(status_code=200, content={"url": oauth_url})
    except Exception as e:
        logger.error(f"Error starting Google OAuth flow: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start Google OAuth: {str(e)}")

@router.get("/google/callback", response_model=AuthResponse)
async def google_callback(
    code: str = Query(...),
    state: str = Query(None),
    error: str = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Handle the callback from Google OAuth with authorization code
    Process the code, get user info, and create/update user in the database
    """
    logger.info(f"Received Google callback with code: {code}, state: {state}, error: {error}")
    if error:
        logger.error(f"Google OAuth error: {error}")
        raise HTTPException(status_code=400, detail=f"Google authentication error: {error}")
    
    try:
        # Extract is_login from state
        is_login = False
        if state:
            try:
                state_data = json.loads(state)
                is_login = state_data.get("is_login", False)
            except:
                # If state is not valid JSON, continue with default is_login=False
                pass
        
        # Exchange the code for tokens
        token_data = await exchange_google_code(code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to get access token from Google")
        
        # Get user info from Google
        user_info = await get_google_user_info(access_token)
        
        # Check if user exists in our database
        email = user_info.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="Email not provided by Google")
        
        # Check if user is verified by Google
        if not user_info.get("verified_email", False):
            raise HTTPException(status_code=400, detail="Email not verified by Google")
        
        # Try to find the user in our database
        user_query = select(User).where(User.email == email)
        user_result = await db.execute(user_query)
        user = user_result.scalar_one_or_none()
        
        # Handle login vs signup logic
        if is_login:
            # Login flow - user must exist
            if not user:
                return JSONResponse(
                    status_code=404,
                    content={
                        "success": False,
                        "error": "User does not exist. Please sign up instead.",
                        "error_code": "user_not_found"
                    }
                )
        else:
            # Signup flow - check if user already exists
            if user:
                return JSONResponse(
                    status_code=409,  # Conflict status code
                    content={
                        "success": False,
                        "error": "User already exists. Please login instead.",
                        "error_code": "user_already_exists"
                    }
                )
            
            # Create a new user for signup
            tenant_id = generate_tenant_id(user_info.get("given_name", "user"))
            
            # Create user in database
            new_user = User(
                id=str(uuid.uuid4()),  # Add a unique UUID as the user ID
                email=email,
                name=user_info.get("name", ""),
                display_name=user_info.get("name", ""),
                given_name=user_info.get("given_name", ""),
                family_name=user_info.get("family_name", ""),
                picture=user_info.get("picture", ""),
                tenants=[tenant_id],
                status="active",
                is_email_verified=True,
                login_ids=[email],
                # No password for OAuth users
            )
            
            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)
            
            logger.info(f"New user created: {new_user}")
            user = new_user

            stripe_client = initialize_stripe()
            # new_user = user
            customer = stripe_client.Customer.create(
                email=user.email,
                name=f"{user.name} {user.family_name}",
                metadata={
                    'user_id': user.id,
                    'email': user.email,
                    'name': user.name,
                    'given_name': user.given_name,
                    'middle_name': user.middle_name,
                    'phone': user.phone,
                    'created_at': user.created_at,
                    'tenants': tenant_id,
                    'roles': user.roles,
                    'status': user.status
                }
            )

            subscription = stripe_client.Subscription.create(
                customer=customer.id,
                items=[
                    {"price": "price_1RBbaIIKbeOzAcByNrc6Xorw"},
                ],
                payment_behavior='default_incomplete',
                expand=["latest_invoice.payment_intent"],
            )
            
            # Create default workspace for new user
            try:
                workspace_service = WorkspaceService(db)
                workspace_data = WorkspaceCreate(
                    name="My Workspace",
                    description="Default workspace",
                    workspace_type="personal",
                    icon_url=None
                )
                await workspace_service.create_workspace(workspace_data, user.id)
                logger.info(f"Created default workspace for new Google user {user.id}")
            except Exception as e:
                logger.error(f"Error creating default workspace for Google user {user.id}: {str(e)}")
        
        # Create access token for the user
        session_token = create_access_token(data={"sub": user.email})
        refresh_token = create_refresh_token(data={"sub": user.email})
        
        # Convert ORM user to UserResponse schema
        user_response = UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            given_name=user.given_name,
            middle_name=user.middle_name,
            roles=user.roles or [],
            picture=user.picture,
            phone=user.phone,
            company_name=getattr(user, 'company_name', None),
            company_website=getattr(user, 'company_website', None),
            country=getattr(user, 'country', None),
            state=getattr(user, 'state', None),
            timezone=getattr(user, 'timezone', None),
            language=getattr(user, 'language', None),
            tenants=user.tenants or []
        )
        first_login = user.logout_time is None

        return AuthResponse(
            session_token=session_token,
            refresh_token=refresh_token,
            user=user_response,
            subscription_type=None,
            first_login=first_login
        )
        
    except Exception as e:
        logger.error(f"Google callback error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process Google callback: {str(e)}")

@router.post("/signup")
async def signup_user(
    request: SignUpRequest,
    db: AsyncSession = Depends(get_db)
):
    """Sign up a new user and send OTP to their email"""
    try:
        email = request.email
        userObj = select(User).where(User.email == email)
        userObj = await db.execute(userObj)
        userObj = userObj.scalar_one_or_none()
        if userObj:
            raise HTTPException(status_code=400, detail="User already exists")
        
        otp = ''.join(random.choices(string.digits, k=6))  # 6-digit OTP
        await store_otp_in_redis(email, otp)
        send_otp_email(email, otp)
        return {
            "message": "User created successfully, OTP sent to email.",
            "masked_address": email
        }
    except Exception as e:
        error_detail = {
            'status_code': getattr(e, 'status_code', 400),
            'error_type': getattr(e, 'error_type', 'unknown'),
            'error_message': str(e)
        }
        raise HTTPException(status_code=400, detail=str(error_detail))

# @router.post("/signin", response_model=dict)
# async def signin_with_otp(request: SignInRequest, db: AsyncSession = Depends(get_db)):
#     """Sign in an existing user by sending OTP to their email"""
#     try:
#         userObj = select(User).where(User.email == request.email)
#         userObj = await db.execute(userObj)
#         userObj = userObj.scalar_one_or_none()
#         if not userObj:
#             raise HTTPException(status_code=400, detail="User not found")
        
#         otp = ''.join(random.choices(string.digits, k=6))  # 6-digit OTP
#         await store_otp_in_redis(request.email, otp)
#         send_otp_email(request.email, otp)
        
#         return {
#             "message": "OTP sent successfully",
#             "masked_address": request.email
#         }
#     except Exception as e:
#         error_detail = {
#             'status_code': getattr(e, 'status_code', 400),
#             'error_type': getattr(e, 'error_type', 'unknown'),
#             'error_message': str(e)
#         }
#         raise HTTPException(status_code=400, detail=str(error_detail))


@router.post("/signin/password", response_model=AuthResponse)
async def signin_with_password(request: PasswordSignInRequest, db: AsyncSession = Depends(get_db)):
    """Sign in an existing user with email and password"""
    try:
        user = await get_user_by_email(db, request.email)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not user.password_hash:
            raise HTTPException(
                status_code=400,
                detail="Password is not set for this account. Please use a different sign-in method or reset your password.",
            )

        if not verify_password(request.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        subscription_type = None
        customer = select(Customer).where(Customer.email == user.email)
        customer = await db.execute(customer)
        customer = customer.scalar_one_or_none()
        if customer:
            logger.info(f"Customer: {customer}")
            subscription = select(Subscription).where(Subscription.stripe_customer_id == customer.stripe_customer_id)
            subscription = await db.execute(subscription)
            subscription = subscription.scalar_one_or_none()
            if subscription:
                stripe_service = StripeService()
                product = await stripe_service.get_product_by_product_id(subscription.plan_id)
                subscription_type = product.name
                logger.info(f"Subscription type: {subscription_type}")
            else:
                subscription_type = None
        
        logger.info(f"User: {user}")
        first_login = user.logout_time is None
        user_response = UserResponse.model_validate(user)
        session_token = create_access_token(data={"sub": user.email})
        refresh_token = create_refresh_token(data={"sub": user.email})

        return AuthResponse(
            session_token=session_token,
            refresh_token=refresh_token,
            user=user_response,
            subscription_type=subscription_type,
            first_login=first_login,
        )
    except Exception as e:
        error_detail = {
            'status_code': getattr(e, 'status_code', 400),
            'error_type': getattr(e, 'error_type', 'unknown'),
            'error_message': str(e)
        }
        raise HTTPException(status_code=400, detail=str(error_detail))

@router.post("/logout")
async def logout_user(user: dict = Depends(validate_session), db: AsyncSession = Depends(get_db)):
    """Log out a user by updating their logout_time in the database"""
    try:
        db_user = await get_user_by_email(db, user["email"])
        logger.info(f"User: {db_user}")
        if not db_user:
            raise HTTPException(
                status_code=404, 
                detail={"message": "User not found. Please try logging in again."}
            )
        
        db_user.logout_time = datetime.utcnow()
        await db.commit()
        
        return {"status": "success", "message": "User logged out successfully"}
    except Exception as e:
        logger.error(f"Error during logout: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail={"message": "Failed to log out. Please try again."}
        )

@router.post("/forgot/password", response_model=SendPasswordResetEmailResponse)
async def send_reset_password_email(reset_request: PasswordResetEmailRequest, db: AsyncSession = Depends(get_db)):
    try:
        user = await get_user_by_email(db, reset_request.email)
        if not user:
            raise HTTPException(status_code=400, detail="User not found")
        
        redirect_url = reset_request.redirect_url
        if redirect_url[-1] != "/":
            redirect_url += "/"

        await send_password_reset_email(reset_request.email, redirect_url)
        return SendPasswordResetEmailResponse(
            success=True,
            email=reset_request.email,
            message="Password reset email sent successfully"
        )
    except Exception as e:
        error_detail = {
            'status_code': 500,
            'error_type': 'unknown',
            'error_message': str(e)
        }
        raise HTTPException(status_code=500, detail=str(error_detail))

@router.post("/reset/password", response_model=ResetPasswordResponse)
async def reset_password(reset_request: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    try:
        user_email = await validate_reset_token(reset_request.token)
        user = await get_user_by_email(db, user_email)
        if not user:
            raise HTTPException(status_code=400, detail="User not found")
        
        user.password_hash = hash_password(reset_request.new_password)
        await db.commit()
        return ResetPasswordResponse(
            success=True,
            email=user_email,
            message="Password reset successfully"
        )
    except Exception as e:
        error_detail = {
            'status_code': 500,
            'error_type': 'unknown',
            'error_message': str(e)
        }
        return HTTPException(status_code=500, detail=str(error_detail))

@router.post("/password/update")
async def update_password(
    request: UpdatePasswordRequest,
    user: dict = Depends(validate_session),
    refresh_token: str = Depends(get_token_from_header),
    db: AsyncSession = Depends(get_db)
):
    """Update password for logged-in user"""
    try:
        user = await get_user_by_email(db, user["email"])
        if not user or not verify_password(request.old_password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid old password")
        
        user.password_hash = hash_password(request.new_password)
        await db.commit()

        return {"message": "Password updated successfully"}
    except Exception as e:
        error_detail = {
            'status_code': 500,
            'error_type': 'unknown',
            'error_message': str(e)
        }
        raise HTTPException(status_code=500, detail=str(error_detail))

@router.post("/verify/otp", response_model=dict)
async def verify_otp(
    verify: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    """Verify OTP code and create user entry if valid"""
    try:
        # Verify OTP code from Redis
        is_valid_otp = await verify_otp_in_redis(verify.email, verify.code)
        if not is_valid_otp:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        # userObj = await get_user_by_email(db, verify.email)
        await create_user_profile(db, verify.email)

        return {
            "email": verify.email,
        }
    except Exception as e:
        error_detail = {
            'status_code': getattr(e, 'status_code', 400),
            'error_type': getattr(e, 'error_type', 'unknown'),
            'error_message': str(e)
        }
        raise HTTPException(status_code=400, detail=str(error_detail))


# @router.post("/set-password")
# async def set_password(
#     password_req: SetPasswordRequest,
#     user: dict = Depends(validate_session)
# ):
#     """Set active password for logged in user"""
#     try:
#         descope_client.mgmt.user.set_active_password(
#             login_id=user["email"],
#             password=password_req.password
#         )
#         return {"message": "Password set successfully"}
#     except Exception as e:
#         raise HTTPException(status_code=400, detail=str(e))

# @router.put("/display-name")
# async def update_display_name(
#     request: UpdateDisplayNameRequest,
#     user: dict = Depends(validate_session)
# ):
#     """Update user's display name and related fields"""
#     try:
#         response = descope_client.mgmt.user.update_display_name(
#             login_id=user["email"],
#             display_name=request.display_name,
#             given_name=request.given_name,
#             middle_name=request.middle_name,
#             family_name=request.family_name
#         )
#         return {"message": "Display name updated successfully", "user": response["user"]}
#     except AuthException as e:
#         error_detail = {
#             'status_code': 400,
#             'error_type': 'update_display_name_failed',
#             'error_message': str(e)
#         }
#         raise HTTPException(status_code=400, detail=str(error_detail))
#     except Exception as e:
#         error_detail = {
#             'status_code': 500,
#             'error_type': 'unknown',
#             'error_message': str(e)
#         }
#         raise HTTPException(status_code=500, detail=str(error_detail))

@router.post("/create-profile", response_model=dict)
async def create_profile(
    request: CreateUserProfileRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create a new user profile with first name, last name, and password"""
    try:
        logger.info(f"Creating user profile for user: {request}")
        user = await get_user_by_email(db, request.email)
        # if not (user or user.is_email_verified):
        #     raise HTTPException(status_code=400, detail="User email not verified")
        # if user.status == 'active':
        #     raise HTTPException(status_code=400, detail="User already exists")
        
        tenant_id = generate_tenant_id(request.first_name)
        logger.info(f"Tenant ID: {tenant_id}")
        user_data = {
            'email': request.email,
            'name': f"{request.first_name} {request.last_name}",
            'given_name': request.first_name,
            'middle_name': request.last_name,
            'tenants': [str(tenant_id)],
            'password_hash': hash_password(request.password),
            'status': 'active'
        }

        # Create user in the database
        new_user = await update_user_profile(db, user_data)
        stripe_client = initialize_stripe()
        # new_user = user
        customer = stripe_client.Customer.create(
            email=request.email,
            name=f"{request.first_name} {request.last_name}",
            metadata={
                'user_id': new_user.id,
                'email': new_user.email,
                'name': new_user.name,
                'given_name': new_user.given_name,
                'middle_name': new_user.middle_name,
                'phone': new_user.phone,
                'created_at': new_user.created_at,
                'tenants': tenant_id,
                'roles': new_user.roles,
                'status': new_user.status
            }
        )

        free_tier_price_id = settings.STRIPE_FREE_TIER_PRICE_ID
        subscription = stripe_client.Subscription.create(
            customer=customer.id,
            items=[
                {"price": free_tier_price_id},
            ],
            collection_method='charge_automatically',
            trial_from_plan=True,
            expand=["latest_invoice.payment_intent"],
            metadata={
                'user_id': new_user.id,
                'tenant_id': tenant_id,
                'plan_type': 'free'
            }
        )
        logger.info(f"Created free tier subscription {subscription.id} for user {new_user.id}")

        if new_user:
            return {
                "id": new_user.id,
                "email": new_user.email,
                "name": new_user.name,
                "given_name": new_user.given_name,
                "middle_name": new_user.middle_name,
                "session_token": create_access_token(data={"sub": new_user.email})
            }
        else:
            raise HTTPException(status_code=400, detail="Failed to create user profile")
    except Exception as e:
        error_detail = {
            'status_code': getattr(e, 'status_code', 400),
            'error_type': getattr(e, 'error_type', 'unknown'),
            'error_message': str(e)
        }
        raise HTTPException(status_code=400, detail=str(error_detail))
@router.put("/update-profile")
async def update_profile(
    user_update: UpdateUserRequest,
    user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    """Update user profile details"""
    try:
        # Prepare the update data
        update_data = {key: value for key, value in user_update.model_dump(exclude_unset=True).items() if value}
        # Include the user's email in the update data
        update_data['email'] = user['email']
        # Call the service function to update the user profile
        updated_user = await update_user_profile(db, update_data)

        return {
            "message": "Profile updated successfully",
            "user": {
                "id": updated_user.id,
                "email": updated_user.email,
                "name": updated_user.name,
                "display_name": updated_user.display_name,
                "given_name": updated_user.given_name,
                "family_name": updated_user.family_name,
                "phone": updated_user.phone,
                "picture": updated_user.picture,
                "status": updated_user.status,
                "roles": updated_user.roles,
                "tenants": updated_user.tenants
            }
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/profile-picture/update")
async def update_profile_picture(
    user: dict = Depends(validate_session),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    try:
        # Validate file format
        allowed_formats = ["image/jpeg", "image/jpg", "image/png"]
        if file.content_type not in allowed_formats:
            return JSONResponse(
                status_code=400, 
                content={
                    "message": "Invalid file format. Only JPG and PNG formats are allowed."
                }
            )
            
        logger.info(f"Updating profile picture for user: {user}")
        file_dir = f"profile_pictures/{user['user_id']}"
        if user.get("picture"):
            await delete_file_from_gcs(f"{file_dir}/{user['picture'].split('/')[-1]}", GCS_STORAGE_BUCKET)

        # Upload new image to GCS
        image_content = await file.read()
        file_path = f"{file_dir}/{file.filename}"
        image_url = await upload_file_to_gcs(image_content, file_path, GCS_STORAGE_BUCKET, content_type=file.content_type)

        # Update user picture in database
        query = update(User).where(User.email == user["email"]).values(picture=image_url)
        await db.execute(query)        
        await db.commit()

        return {"status": "success", "image_url": image_url}

    except Exception as e:
        logger.error(f"Failed to update profile: {str(e)}")  # Log the error
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")


@router.delete("/profile-picture/remove")
async def remove_profile_picture(
    user: dict = Depends(validate_session),
    db: AsyncSession = Depends(get_db)
):
    try:
        logger.info(f"Removing profile picture for user: {user}")
        if user.get("picture"):
            await delete_file_from_gcs(f"profile_pictures/{user['user_id']}/{user['picture'].split('/')[-1]}", GCS_STORAGE_BUCKET)

        # Update user picture in database
        query = update(User).where(User.email == user["email"]).values(picture=None)
        await db.execute(query)        
        await db.commit()

        return {"status": "success", "image_url": ""}

    except Exception as e:
        logger.error(f"Failed to update profile: {str(e)}")  # Log the error
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")


# @router.put("/update-email")
# async def update_email(
#     user_email: UpdateEmailRequest,
#     user: dict = Depends(validate_session),
# ):
#     """Update email and send verification OTP"""
#     try:
#         response = descope_client.otp.update_user_email(
#             login_id=user["email"],
#             email=user_email.new_email,
#             refresh_token=user_email.refresh_token,
#             add_to_login_ids=True,
#             on_merge_use_existing=True
#         )
#         return {
#             "message": response,
#         }
#     except Exception as e:
#         raise HTTPException(status_code=400, detail=str(e))


# @router.get("/admin")
# async def platform_admin_route(user: dict = Depends(check_roles(["admin"]))):
#     """Route that requires admin role in any tenant (platform-wide admin access)"""
#     # Get all tenants where user is admin
#     admin_tenants = [
#         tenant for tenant in user.get("userTenants", [])
#         if "admin" in tenant.get("roleNames", [])
#     ]
    
#     return {
#         "message": "Platform admin access granted",
#         "user": {
#             "email": user.get("email"),
#             "id": user.get("id"),
#             "name": user.get("name")
#         },
#         "admin_access": [{
#             "tenant_id": t.get("tenantId"),
#             "tenant_name": t.get("tenantName"),
#             "tenant_type": "personal" if t.get("tenantId", "").startswith("personal_") else "organization"
#         } for t in admin_tenants]
#     }

# @router.get("/tenant/{tenant_id}/admin")
# async def tenant_admin_route(
#     tenant_id: str,
#     current_user: dict = Depends(validate_session)
# ):
#     """Route that requires admin role for a specific tenant (org or personal)"""
#     # Validate admin role for this specific tenant
#     await check_roles(["admin"], tenant_id=tenant_id).role_checker(current_user)
    
#     # Get tenant information
#     tenant = next((t for t in current_user.get("userTenants", []) if t.get("tenantId") == tenant_id), None)
#     tenant_type = "personal" if tenant_id.startswith("personal_") else "organization"
    
#     return {
#         "message": f"Admin access granted for {tenant_type} tenant {tenant_id}",
#         "user": {
#             "email": current_user.get("email"),
#             "id": current_user.get("id"),
#             "name": current_user.get("name")
#         },
#         "tenant": {
#             "id": tenant_id,
#             "name": tenant.get("tenantName"),
#             "type": tenant_type,
#             "roles": tenant.get("roleNames", [])
#         }
#     }

# @router.get("/manager")
# async def manager_route(user: dict = Depends(check_roles(["manager"]))):
#     """Route that requires manager role"""
#     return {
#         "message": "Manager access granted",
#         "user": user
#     }

# @router.get("/multi-role")
# async def multi_role_route(user: dict = Depends(check_roles(["admin", "manager"]))):
#     """Route that requires either admin OR manager role"""
#     return {
#         "message": "Access granted for admin or manager",
#         "user": user
#     }


# @router.post("/initialize")
# async def initialize_user(
#     request: Request,
#     db: AsyncSession = Depends(get_db),
#     user: dict = Depends(validate_session)
# ):
#     """Initialize user after successful authentication and ensure customer record exists"""
#     try:
#         # logger.info(f"Initializing user {user.get('email')} with ID {user.get('id')}")
#         logger.info(f"User data {user}")

#         # Ensure customer exists in both Stripe and our database
#         # customer = await ensure_customer_exists(user, db)
        
#         response = {
#             "user": {
#                 "email": user.get('email'),
#                 "name": user.get('name'),
#                 "givenName": user.get('givenName'),
#                 "familyName": user.get('familyName'),
#                 "phone": user.get('phone'),
#                 "picture": user.get('picture'),
#                 "userTenants": user.get('userTenants'),
#                 "userTenantId": user.get('userTenants', [{}])[0].get('tenantId') if user.get('userTenants') else None
#             },
#             # "customer": customer.to_dict() if customer else None
#         }
        
#         logger.info(f"Successfully initialized user {user.get('email')}")
#         return response
        
#     except Exception as e:
#         logger.error(f"Failed to initialize user: {str(e)}")
#         raise HTTPException(
#             status_code=500,
#             detail=f"Failed to initialize user: {str(e)}"
#         )

@router.get("/me", response_model=UserResponse)
async def get_current_user(user: dict = Depends(validate_session), db: AsyncSession = Depends(get_db)):
    """Get current authenticated user information"""
    userObj = select(User).where(User.email == user["email"])
    userObj = await db.execute(userObj)
    userObj = userObj.scalar_one_or_none()
    return userObj


# @router.get("/protected")
# async def protected_route(user: dict = Depends(validate_session)):
#     """Example of a protected route that requires authentication"""
#     return {"message": "This is a protected route", "user": user}

# @router.post("/logout")
# async def logout(
#     user: dict = Depends(validate_session)
# ):
#     """Logout user by revoking their session"""
#     try:
#         response = descope_client.mgmt.user.logout_user_by_user_id(user_id=user["id"])
#         return {"message": f"{response}"}
#     except Exception as e:
#         raise HTTPException(status_code=400, detail=str(e))

@router.get("/user/by-email/{email}")
async def get_current_user_by_email(
    email: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(validate_session)
):
    """Get user data by email address from the database (requires authentication)"""
    try:
        # Check if the user has admin role to access other user data
        is_admin = False
        for tenant in current_user.get("userTenants", []):
            if "admin" in tenant.get("roleNames", []):
                is_admin = True
                break
                
        # Non-admin users can only access their own data
        if not is_admin and current_user.get("email") != email:
            raise HTTPException(
                status_code=403,
                detail="Access denied: You can only access your own user data"
            )
            
        # Query database for user by email
        query = select(User).where(User.email == email)
        result = await db.execute(query)
        user_db = result.scalar_one_or_none()
        
        if not user_db:
            # User not found in our database
            raise HTTPException(status_code=404, detail=f"User with email {email} not found in database")
            
        # Return user data from database
        return {
            "id": user_db.id,
            # "descope_user_id": user_db.descope_user_id,
            "email": user_db.email,
            "name": user_db.name,
            "display_name": user_db.display_name,
            "given_name": user_db.given_name,
            "middle_name": user_db.middle_name,
            "family_name": user_db.family_name,
            "phone": user_db.phone,
            "picture": user_db.picture,
            "status": user_db.status,
            "roles": user_db.roles,
            "tenants": user_db.tenants,
            "login_ids": user_db.login_ids,
            "is_email_verified": user_db.is_email_verified,
            "is_phone_verified": user_db.is_phone_verified,
            "created_at": user_db.created_at.isoformat() if user_db.created_at else None,
            "user_metadata": user_db.user_metadata
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving user data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve user data: {str(e)}")
