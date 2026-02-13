from .start import router as start_router
from .proxy import router as proxy_router
from .help import router as help_router
from .admin import router as admin_router
from .donate import router as donate_router

__all__ = [
    "start_router",
    "proxy_router",
    "help_router",
    "admin_router",
    "donate_router",
]
