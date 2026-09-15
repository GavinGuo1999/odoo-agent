from fastapi import APIRouter, Depends

from app.api.routes.auth import require_session, router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.database import router as database_router
from app.api.routes.health import router as health_router
from app.api.routes.quality import router as quality_router
from app.api.routes.settings import router as settings_router
from app.api.routes.sales import router as sales_router
from app.api.routes.semantic_audit import router as semantic_audit_router
from app.api.routes.wiki import router as wiki_router


api_router = APIRouter()

# 登录接口自身必须公开，否则没人登得进来。
api_router.include_router(auth_router)

# 其余全部挂门禁。**新增路由默认就是受保护的**——挂在这个 protected 上，
# 而不是逐个路由加 dependency，就不会出现"新加的接口忘了加保护"。
# /health 也在里面：它会回显模型名、库名和订单数。
protected = APIRouter(dependencies=[Depends(require_session)])
protected.include_router(health_router)
protected.include_router(chat_router)
protected.include_router(settings_router)
protected.include_router(database_router)
protected.include_router(sales_router)
protected.include_router(semantic_audit_router)
protected.include_router(wiki_router)
protected.include_router(quality_router)
api_router.include_router(protected)
