import uuid
from sqlalchemy import Boolean, Column, String, ForeignKey, DateTime, Numeric, Date
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.base import Base


class Price(Base):
    __tablename__ = "prices"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id     = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    store_id       = Column(UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False)
    price          = Column(Numeric(8, 2), nullable=False)
    original_price = Column(Numeric(8, 2))
    promo_label    = Column(String(200))
    promo_expires  = Column(Date)
    price_per_unit = Column(Numeric(10, 4))
    in_stock       = Column(Boolean, default=True)
    is_current     = Column(Boolean, default=True, index=True)
    # Prezzo messo in quarantena dall'audit anomalie (scripts/audit_price_anomalies.py):
    # escluso da TUTTE le query di serving (ricerca, confronti, ottimizzazione).
    quarantined    = Column(Boolean, nullable=False, default=False, server_default="false")
    source         = Column(String(30))
    product_url    = Column(String)  # deep-link al prodotto nello shop online
    scraped_at     = Column(DateTime(timezone=True), server_default=func.now())
