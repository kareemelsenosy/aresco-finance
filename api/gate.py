"""
The access gate.

One middleware in front of everything rather than a dependency on each route:
routers get added over time and a forgotten `Depends(current_user)` is an open
door. Default-deny with a small allow-list is the safer shape.

The finance figures are the same for everyone, so this gates access and stops
there — there is no per-user partitioning of the data.

Browser requests for a page get redirected to the login screen; anything else
gets a 401 so the front-end's fetch() calls can react without following a
redirect into HTML.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from api.auth import SESSION_COOKIE, _unsign

LOGIN_PAGE = "/app/login.html"

# Paths reachable without a session.
PUBLIC_EXACT = {
    "/health",
    "/auth/config",
    "/auth/request-code",
    "/auth/verify-code",
    "/auth/set-password",
    "/auth/login",
    "/auth/logout",
    "/favicon.ico",
}

# Static assets the login page itself needs.
PUBLIC_PREFIXES = (
    "/app/login.html",
    "/app/login.js",
    "/app/style.css",
    "/app/assets/",
    "/app/logo.png",
)


def _is_public(path: str) -> bool:
    if path in PUBLIC_EXACT:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def _wants_html(request) -> bool:
    return "text/html" in request.headers.get("accept", "")


class AuthGate(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path

        if request.method == "OPTIONS" or _is_public(path):
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE)
        payload = _unsign(token) if token else None
        if not payload or payload.get("kind") != "session":
            if _wants_html(request):
                return RedirectResponse(LOGIN_PAGE, status_code=303)
            return JSONResponse({"detail": "Sign in to continue."}, status_code=401)

        return await call_next(request)
