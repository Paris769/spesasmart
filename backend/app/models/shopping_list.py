import uuid
from sqlalchemy import Boolean, Column, String, ForeignKey, DateTime, Numeric, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.models.base import Base


class ShoppingList(Base):
    __tablename__ = "shopping_lists"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    name                = Column(String(200), default="Lista spesa")
    optimization_result = Column(JSONB)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())


class ListItem(Base):
    __tablename__ = "list_items"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    list_id      = Column(UUID(as_uuid=True), ForeignKey("shopping_lists.id", ondelete="CASCADE"), nullable=False)
    product_id   = Column(UUID(as_uuid=True), ForeignKey("products.id"))
    product_name = Column(String(500))
    quantity     = Column(Numeric(6, 2), default=1)
    unit         = Column(String(20))
    is_checked   = Column(Boolean, default=False)
    sort_order   = Column(Integer, default=0)
