import uuid
from sqlalchemy import Boolean, Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email           = Column(String(255), unique=True, nullable=False)
    password_hash   = Column(String(255))
    full_name       = Column(String(200))
    subscription    = Column(String(20), default="free")
    sub_expires_at  = Column(DateTime(timezone=True))
    gdpr_consent_at = Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    deleted_at      = Column(DateTime(timezone=True))
