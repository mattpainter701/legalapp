from app.routers.auth import router as auth_router
from app.routers.chat import router as chat_router
from app.routers.documents import router as documents_router
from app.routers.admin import router as admin_router
from app.routers.billing import router as billing_router

__all__ = [
    "auth_router",
    "chat_router",
    "documents_router",
    "admin_router",
    "billing_router",
]
