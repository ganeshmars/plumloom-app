from pydantic import BaseModel, HttpUrl, EmailStr, Field
from typing import Optional, List, Dict, Any


# OAuth Models
class OAuthStartResponse(BaseModel):
    """Response for initiating OAuth flow"""
    url: HttpUrl

class OAuthExchangeResponse(BaseModel):
    """Response for OAuth token exchange"""
    sessionToken: str
    refreshToken: str
    user: Dict[str, Any]
    roles: List[str]

# Password Authentication Models
class PasswordSignUpRequest(BaseModel):
    """Request model for password-based sign up"""
    email: EmailStr
    password: str

class SendPasswordResetEmailResponse(BaseModel):
    """Response model for sending password reset email"""
    email: EmailStr
    success: bool
    message: str

class UpdatePasswordRequest(BaseModel):
    """Request model for updating password of logged-in user"""
    new_password: str
    old_password: str

class UpdateDisplayNameRequest(BaseModel):
    """Request model for updating user's display name"""
    display_name: Optional[str] = None
    given_name: Optional[str] = None
    middle_name: Optional[str] = None
    family_name: Optional[str] = None
