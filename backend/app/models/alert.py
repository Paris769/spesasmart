import uuid
from sqlalchemy import Boolean, Column, String, ForeignKey, DateTime, Numeric, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.base import Base


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id      = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    threshold_price = Column(Numeric(8, 2), nullable=False)
    radius_km       = Column(Integer, default=5)
    is_active       = Column(Boolean, default=True)
    last_triggered  = Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
