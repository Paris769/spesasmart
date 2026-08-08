from fastapi import APIRouter
from app.api.v1.endpoints import (
    auth, stores, products, lists, scan, receipts, go,
    watches, recurring, agent_ai, promo, offers,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(stores.router)
api_router.include_router(products.router)
api_router.include_router(lists.router)
api_router.include_router(scan.router)
api_router.include_router(receipts.router)
api_router.include_router(go.router)
api_router.include_router(watches.router)
api_router.include_router(recurring.router)
api_router.include_router(agent_ai.router)
api_router.include_router(promo.router)
api_router.include_router(offers.router)
