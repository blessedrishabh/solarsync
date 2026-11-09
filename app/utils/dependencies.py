"""
SolarSync - Authentication dependencies for FastAPI route protection.
"""

from typing import List
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.services.auth_service import decode_access_token, get_user_by_id


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """
    Extract and validate the current user from the JWT token in cookies or Authorization header.
    Used as a dependency in protected routes.
    """
    token = request.cookies.get("access_token")

    # Also check Authorization header if no cookie
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/auth/login"},
        )

    # Clean up any surrounding quotes or Bearer prefix
    token = token.strip('"').strip("'")
    if token.startswith("Bearer "):
        token = token[7:]

    token_data = decode_access_token(token)
    if token_data is None or token_data.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/auth/login"},
        )

    user = await get_user_by_id(db, token_data.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/auth/login"},
        )

    return user


def require_roles(allowed_roles: List[UserRole]):
    """
    Dependency factory that restricts access to specific roles.
    Usage: Depends(require_roles([UserRole.ADMIN, UserRole.SALES]))
    """
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to access this resource.",
            )
        return current_user
    return role_checker
