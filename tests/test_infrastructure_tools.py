from unittest.mock import AsyncMock

import pytest

from computeedge.exceptions import DeploymentError
from computeedge.services.infrastructure import (
    InfrastructureDestroyResult,
    InfrastructureInfo,
    InfrastructureRefreshResult,
)
from computeedge.services.infra.base import InfrastructurePlanResult
from computeedge.tools.infrastructure import (
    make_destroy_deployment_tool,
    make_plan_infra_tool,
    make_refresh_infra_tool,
    make_show_infra_tool,
)


@pytest.mark.asyncio
async def test_show_infra_tool_success():
    service = AsyncMock()
    service.show = AsyncMock(return_value=InfrastructureInfo(
        deployment_id="ce-hetzner-abc12345",
        provider="hetzner",
        backend="terraform",
        plan="cx23",
        server_id=12345,
    ))

    tool = make_show_infra_tool(service)
    result = await tool("ce-hetzner-abc12345", user_id=1)

    assert result["backend"] == "terraform"


@pytest.mark.asyncio
async def test_refresh_infra_tool_invalid_id():
    tool = make_refresh_infra_tool(AsyncMock())
    result = await tool("bad-id", user_id=1)
    assert "error" in result


@pytest.mark.asyncio
async def test_plan_infra_tool_handles_errors():
    service = AsyncMock()
    service.plan = AsyncMock(side_effect=DeploymentError("Deployment not found"))
    tool = make_plan_infra_tool(service)

    result = await tool("ce-hetzner-abc12345", user_id=1)

    assert "error" in result
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_destroy_deployment_tool_success():
    service = AsyncMock()
    service.destroy = AsyncMock(return_value=InfrastructureDestroyResult(
        deployment_id="ce-hetzner-abc12345",
        provider="hetzner",
        backend="terraform",
        status="destroyed",
        message="Destroyed infrastructure for ce-hetzner-abc12345.",
    ))
    tool = make_destroy_deployment_tool(service)

    result = await tool("ce-hetzner-abc12345", provider_token="test-token", user_id=1)

    assert result["status"] == "destroyed"
