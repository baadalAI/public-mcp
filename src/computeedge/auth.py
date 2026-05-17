from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_USER_STATE_KEY = "computeedge_user"
LOCAL_USER_ID = -1


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Validates Authorization: Bearer <api_key> against the database."""

    def __init__(self, app, db):
        super().__init__(app)
        self.db = db

    _PUBLIC_PATHS = {"/health", "/register", "/api/register"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing or invalid Authorization header"},
                status_code=401,
            )

        token = auth_header[len("Bearer "):]
        user = await self.db.get_user_by_api_key(token)
        if user is None:
            return JSONResponse(
                {"error": "Invalid API key"},
                status_code=401,
            )

        request.state.computeedge_user = user
        return await call_next(request)


def get_current_user_id(request: Request) -> int:
    """Extract user_id from request state. Returns LOCAL_USER_ID for stdio mode."""
    user = getattr(request.state, "computeedge_user", None)
    if user is None:
        return LOCAL_USER_ID
    return user["id"]
