"""
SolarSync - Main FastAPI application.
Rooftop solar lead qualification, quotation, and project tracker.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db, AsyncSessionLocal
from app.services.auth_service import seed_admin_user
from app.services.lead_service import seed_sample_leads
from app.services.quote_service import seed_default_rules
from app.services.project_service import seed_sample_projects

# Import routers
from app.routers import auth, dashboard, leads, admin, quotes, projects

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup: create tables and seed demo data
    await init_db()

    async with AsyncSessionLocal() as session:
        await seed_admin_user(session)
        await seed_sample_leads(session)
        await seed_default_rules(session)
        await seed_sample_projects(session)

    print("SolarSync is ready!")
    yield
    print("SolarSync shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    description="Rooftop solar lead qualification, quotation, and project tracker",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static and uploads
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Include routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(leads.router)
app.include_router(quotes.router)
app.include_router(projects.router)
app.include_router(admin.router)


@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint for AWS ALB, App Runner, ECS, and container probes."""
    return {
        "status": "healthy",
        "service": "SolarSync",
        "version": "1.0.0",
        "environment": "production" if not settings.DEBUG else "development",
    }


@app.get("/")
async def root():
    """Redirect root to dashboard (or login if not authenticated)."""
    return RedirectResponse(url="/dashboard")
