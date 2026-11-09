"""
SolarSync - Pydantic schemas for user authentication and registration.
"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from app.models.user import UserRole


# --- Auth Schemas ---

class UserRegister(BaseModel):
    """Schema for user registration."""
    name: str = Field(..., min_length=2, max_length=100, examples=["Prashant Wankhede"])
    email: EmailStr = Field(..., examples=["prashant@example.com"])
    phone: str = Field(..., pattern=r"^\d{10}$", examples=["9876543210"])
    password: str = Field(..., min_length=6, max_length=128)
    role: UserRole = UserRole.HOMEOWNER


class UserLogin(BaseModel):
    """Schema for user login."""
    email: EmailStr
    password: str


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Data extracted from JWT token."""
    user_id: Optional[int] = None
    email: Optional[str] = None
    role: Optional[str] = None


# --- User Response Schemas ---

class UserResponse(BaseModel):
    """Schema for user data in API responses."""
    id: int
    name: str
    email: str
    phone: str
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}
