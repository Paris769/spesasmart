import uuid
from sqlalchemy import Boolean, Column, String, ForeignKey, DateTime, Numeric, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.base import Base


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # user_id ora nullable: i "price watch" anonimi usano solo l'email come chiave
    # (la tabella users e' vuota, niente login). Vedi endpoints/watches.py.
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    product_id      = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    email           = Column(String)
    threshold_price = Column(Numeric(8, 2))     # nullable: watch senza soglia = "avvisami di ogni calo"
    radius_km       = Column(Integer, default=5)
    is_active       = Column(Boolean, default=True)
    last_triggered  = Column(DateTime(timezone=True))
    last_notified_at = Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
