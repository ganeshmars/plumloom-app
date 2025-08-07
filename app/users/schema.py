from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from typing import Optional, List, Any, Dict

from app.schemas.user_preference import ThemeMode, WorkspaceFullState


class SocialLoginResponse(BaseModel):
    session_token: str
    refresh_token: str
    user: 'UserResponse'

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: Optional[str]
    given_name: Optional[str]
    middle_name: Optional[str]
    roles: List[str]
    picture: Optional[str]
    phone: Optional[str]
    company_name: Optional[str]
    company_website: Optional[str]
    country: Optional[str]
    state: Optional[str]
    timezone: Optional[str]
    language: Optional[str]
    tenants: List[str]
    user_metadata: Optional['FlattenedUserPreferences'] = None

    @field_validator('user_metadata', mode='before')
    @classmethod
    def validate_user_metadata(cls, v: Any) -> Optional[Dict[str, Any]]:
        if not v:
            return None

        if isinstance(v, dict):
            flattened_data: Dict[str, Any] = {}
            workspace_data = v.get('workspace')

            if isinstance(workspace_data, dict):
                flattened_data.update(workspace_data)

            for key, value in v.items():
                if key != 'workspace':
                    flattened_data[key] = value
            
            return flattened_data
        return v


class FlattenedUserPreferences(WorkspaceFullState):
    view_mode: ThemeMode = ThemeMode.LIGHT

    model_config = ConfigDict(from_attributes=True)



class SignInRequest(BaseModel):
    email: str

class PasswordSignInRequest(BaseModel):
    email: str
    password: str
    remember_me: bool = False

class SignUpRequest(BaseModel):
    email: str

class OTPVerifyRequest(BaseModel):
    email: str
    code: str

class AuthResponse(BaseModel):
    session_token: str
    subscription_type: Optional[str]
    user: UserResponse
    first_login: bool = False

class SetPasswordRequest(BaseModel):
    password: str

class RefreshToken(BaseModel):
    refresh_token: str

class UpdateEmailRequest(RefreshToken):
    new_email: str

class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    phone: Optional[str] = None
    company_name: Optional[str] = None
    company_website: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    timezone: Optional[str] = None
    language: Optional[str] = None

class CustomerResponse(BaseModel):
    id: str
    stripe_customer_id: str
    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    status: str
    tenant_id: str
    currency: str
    language: str

class CreateUserProfileRequest(BaseModel):
    email: str
    first_name: str
    last_name: str
    password: str

class ResetPasswordResponse(BaseModel):
    success: bool
    email: str
    message: str
