"""Dashboard route for the built React system dashboard.

The dashboard is part of the frontend application. This compatibility route
exists so that opening or refreshing ``/dashboard`` on the API server serves
the same SPA shell as the other frontend routes instead of a separate,
outdated HTML page.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

dashboard_router = APIRouter()

_STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


@dashboard_router.get("/dashboard", response_class=FileResponse)
async def dashboard() -> FileResponse:
    """Serve the React app shell for the system dashboard route."""
    return FileResponse(_STATIC_INDEX, headers={"Cache-Control": "no-cache"})


__all__ = ["dashboard_router"]
