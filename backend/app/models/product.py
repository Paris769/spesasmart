import uuid
from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, DateTime, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.base import Base


class Product(Base):
    __tablename__ = "products"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    barcode      = Column(String(50), index=True)
    name         = Column(String(500), nullable=False)
    brand        = Column(String(200))
    category_id  = Column(Integer, ForeignKey("categories.id"))
    description  = Column(String)
    image_url    = Column(String)
    unit         = Column(String(10))
    unit_quantity = Column(Numeric(10, 3))
    is_verified  = Column(Boolean, default=False)
    source       = Column(String(30))
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())
