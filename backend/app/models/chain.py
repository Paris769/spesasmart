from sqlalchemy import Boolean, Column, Integer, String
from app.models.base import Base


class Chain(Base):
    __tablename__ = "chains"

    id               = Column(Integer, primary_key=True)
    name             = Column(String(100), nullable=False)
    slug             = Column(String(50), unique=True, nullable=False)
    logo_url         = Column(String)
    has_online_shop  = Column(Boolean, default=False)
    shop_url         = Column(String)
    integration_type = Column(String(20), default="redirect")
    is_active        = Column(Boolean, default=True)
