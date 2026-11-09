"""
SolarSync - Authentication routes: login, register, logout.
Server-side rendered with Jinja2 templates.
"""

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import UserRole
from app.services.auth_service import (
    authenticate_user,
    create_user,
    create_access_token,
    get_user_by_email,
    get_user_by_phone,
)
from app.schemas.user import UserRegister

router = APIRouter(prefix="/auth", tags=["Authentication"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login page."""
    return templates.TemplateResponse(
        request=request,
        name="pages/login.html",
        context={"title": "Login — SolarSync"}
    )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Process login form submission."""
    user = await authenticate_user(db, email, password)

    if not user:
        return templates.TemplateResponse(
            request=request,
            name="pages/login.html",
            context={
                "title": "Login — SolarSync",
                "error": "Invalid email or password.",
            },
            status_code=400,
        )

    # Create JWT token
    token = create_access_token(data={
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
    })

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=86400,  # 24 hours
        samesite="lax",
    )
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Render the registration page."""
    return templates.TemplateResponse(
        request=request,
        name="pages/register.html",
        context={"title": "Register — SolarSync"}
    )


@router.post("/register")
async def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Process registration form submission."""
    errors = []

    # Validate passwords match
    if password != confirm_password:
        errors.append("Passwords do not match.")

    # Validate password length
    if len(password) < 6:
        errors.append("Password must be at least 6 characters.")

    # Validate phone
    if not phone.isdigit() or len(phone) != 10:
        errors.append("Phone number must be 10 digits.")

    # Check if email already exists
    if await get_user_by_email(db, email):
        errors.append("An account with this email already exists.")

    # Check if phone already exists
    if await get_user_by_phone(db, phone):
        errors.append("An account with this phone number already exists.")

    if errors:
        return templates.TemplateResponse(
            request=request,
            name="pages/register.html",
            context={
                "title": "Register — SolarSync",
                "errors": errors,
                "form_data": {"name": name, "email": email, "phone": phone},
            },
            status_code=400,
        )

    # Create user (homeowners register themselves)
    user_data = UserRegister(
        name=name,
        email=email,
        phone=phone,
        password=password,
        role=UserRole.HOMEOWNER,
    )
    user = await create_user(db, user_data)

    # Auto-login after registration
    token = create_access_token(data={
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
    })

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=86400,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout():
    """Clear the auth cookie and redirect to login."""
    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie("access_token")
    return response
