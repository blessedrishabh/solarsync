"""
SolarSync - User model with role-based access control.
"""

import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Enum, DateTime, Boolean
)
from app.database import Base


class UserRole(str, enum.Enum):
    """User roles for RBAC."""
    HOMEOWNER = "homeowner"
    SALES = "sales"
    ADMIN = "admin"


class User(Base):
    """User account model."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    phone = Column(String(15), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.HOMEOWNER)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}', role='{self.role}')>"
