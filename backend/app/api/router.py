from fastapi import APIRouter

from app.api.routes.chat import router as chat_router
from app.api.routes.database import router as database_router
from app.api.routes.health import router as health_router
from app.api.routes.settings import router as settings_router
from app.api.routes.sales import router as sales_router
from app.api.routes.semantic_audit import router as semantic_audit_router


api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(chat_router)
api_router.include_router(settings_router)
api_router.include_router(database_router)
api_router.include_router(sales_router)
api_router.include_router(semantic_audit_router)
