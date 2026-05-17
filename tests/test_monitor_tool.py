from unittest.mock import AsyncMock

import pytest

from computeedge.exceptions import DeploymentError
from computeedge.models import HealthStatus, ResourceUsage
from computeedge.tools.monitor import make_monitor_tool


@pytest.fixture
def mock_monitoring_service():
    svc = AsyncMock()
    svc.check_health = AsyncMock(return_value=HealthStatus(
        status="healthy",
        uptime="12 days",
        resources=ResourceUsage(
            cpu_usage_percent=15.0,
            ram_usage_percent=42.0,
            ram_used_mb=430,
            ram_total_mb=1024,
            disk_usage_percent=23.0,
            disk_used_gb=2.3,
            disk_total_gb=10.0,
        ),
        monthly_cost=3.99,
        assessment="Your app is healthy.",
        alerts=[],
    ))
    return svc


@pytest.fixture
def mock_state_manager():
    return AsyncMock()


@pytest.mark.asyncio
async def test_monitor_tool_success(mock_monitoring_service, mock_state_manager):
    monitor = make_monitor_tool(mock_monitoring_service, mock_state_manager)
    result = await monitor("ce-hetzner-abc123", user_id=1)
    assert result["status"] == "healthy"
    assert result["monthly_cost"] == 3.99
    assert result["resources"]["cpu_usage_percent"] == 15.0


@pytest.mark.asyncio
async def test_monitor_tool_deployment_not_found(mock_monitoring_service, mock_state_manager):
    mock_monitoring_service.check_health = AsyncMock(
        side_effect=DeploymentError("Deployment not found: ce-bad-id")
    )
    monitor = make_monitor_tool(mock_monitoring_service, mock_state_manager)
    result = await monitor("ce-bad-id", user_id=1)
    assert "error" in result
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_monitor_tool_invalid_id(mock_state_manager):
    svc = AsyncMock()
    monitor = make_monitor_tool(svc, mock_state_manager)
    result = await monitor("invalid_id", user_id=1)
    assert "error" in result
    assert "ce-" in result["error"]
