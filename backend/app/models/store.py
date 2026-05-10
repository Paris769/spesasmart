import uuid
from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geometry
from app.models.base import Base


class Store(Base):
    __tablename__ = "stores"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chain_id          = Column(Integer, ForeignKey("chains.id"), nullable=False)
    external_id       = Column(String(100))
    name              = Column(String(200))
    address           = Column(String)
    city              = Column(String(100))
    province          = Column(String(2))
    postal_code       = Column(String(10))
    coordinates       = Column(Geometry("POINT", srid=4326), nullable=False)
    phone             = Column(String(20))
    opening_hours     = Column(JSONB)
    has_delivery      = Column(Boolean, default=False)
    has_click_collect = Column(Boolean, default=False)
    is_active         = Column(Boolean, default=True)
    last_verified     = Column(DateTime(timezone=True))
