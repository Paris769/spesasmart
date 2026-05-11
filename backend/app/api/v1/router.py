from fastapi import APIRouter
from app.api.v1.endpoints import auth, stores, products, lists, scan, receipts

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(stores.router)
api_router.include_router(products.router)
api_router.include_router(lists.router)
api_router.include_router(scan.router)
api_router.include_router(receipts.router)
