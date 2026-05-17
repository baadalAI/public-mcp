from unittest.mock import AsyncMock

import pytest

from computeedge.exceptions import WebhookError
from computeedge.tools.webhook import make_connect_github_tool, make_disconnect_github_tool


@pytest.mark.asyncio
async def test_connect_github_tool_success():
    mock_service = AsyncMock()
    mock_service.connect = AsyncMock(return_value={
        "status": "connected",
        "deployment_id": "ce-hetzner-abc12345",
        "webhook_url": "http://1.2.3.4:9867/webhook",
    })
    fn = make_connect_github_tool(mock_service)
    result = await fn("ce-hetzner-abc12345")
    assert result["status"] == "connected"
    mock_service.connect.assert_called_once()


@pytest.mark.asyncio
async def test_connect_github_tool_no_service():
    fn = make_connect_github_tool(None)
    result = await fn("ce-hetzner-abc12345")
    assert "error" in result
    assert "not available" in result["error"]


@pytest.mark.asyncio
async def test_connect_github_tool_invalid_id():
    mock_service = AsyncMock()
    fn = make_connect_github_tool(mock_service)
    result = await fn("invalid-id")
    assert "error" in result
    assert "Invalid deployment ID" in result["error"]


@pytest.mark.asyncio
async def test_disconnect_github_tool_success():
    mock_service = AsyncMock()
    mock_service.disconnect = AsyncMock(return_value={
        "status": "disconnected",
        "deployment_id": "ce-hetzner-abc12345",
    })
    fn = make_disconnect_github_tool(mock_service)
    result = await fn("ce-hetzner-abc12345")
    assert result["status"] == "disconnected"
    mock_service.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_disconnect_github_tool_catches_errors():
    mock_service = AsyncMock()
    mock_service.disconnect = AsyncMock(side_effect=WebhookError("test error"))
    fn = make_disconnect_github_tool(mock_service)
    result = await fn("ce-hetzner-abc12345")
    assert "error" in result
    assert "test error" in result["error"]
