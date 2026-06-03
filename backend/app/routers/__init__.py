from app.routers.auth import router as auth_router
from app.routers.chat import router as chat_router
from app.routers.documents import router as documents_router
from app.routers.admin import router as admin_router
from app.routers.billing import router as billing_router
from app.routers.integrations import router as integrations_router
from app.routers.email_agent import router as email_router
from app.routers.document_sync import router as document_sync_router
from app.routers.user_sync import router as user_sync_router
from app.routers.qbo import router as qbo_router
from app.routers.billing_extended import router as billing_extended_router
from app.routers.trust_accounting import router as trust_accounting_router

__all__ = [
    "auth_router",
    "chat_router",
    "documents_router",
    "admin_router",
    "billing_router",
    "integrations_router",
    "email_router",
    "document_sync_router",
    "user_sync_router",
    "qbo_router",
    "billing_extended_router",
    "trust_accounting_router",
]
