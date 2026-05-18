from .start import router as start_router
from .proxy import router as proxy_router
from .help import router as help_router
from .admin import router as admin_router
from .fallback import router as fallback_router
from .inline_handler import router as inline_router

__all__ = [
    "start_router",
    "proxy_router",
    "help_router",
    "admin_router",
    "fallback_router",
    "inline_router",
]
