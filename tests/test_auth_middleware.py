import pytest
from unittest.mock import AsyncMock
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from computeedge.auth import BearerTokenMiddleware, get_current_user_id


async def hello(request: Request):
    user_id = get_current_user_id(request)
    return JSONResponse({"user_id": user_id})


def make_app(db_mock):
    app = Starlette(routes=[Route("/test", hello)])
    app.add_middleware(BearerTokenMiddleware, db=db_mock)
    return app


def test_missing_auth_header_returns_401():
    db = AsyncMock()
    app = make_app(db)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/test")
    assert response.status_code == 401


def test_invalid_token_returns_401():
    db = AsyncMock()
    db.get_user_by_api_key = AsyncMock(return_value=None)
    app = make_app(db)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/test", headers={"Authorization": "Bearer bad-key"})
    assert response.status_code == 401


def test_valid_token_passes_through():
    db = AsyncMock()
    db.get_user_by_api_key = AsyncMock(return_value={"id": 42, "name": "alice"})
    app = make_app(db)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/test", headers={"Authorization": "Bearer good-key"})
    assert response.status_code == 200
    assert response.json()["user_id"] == 42
