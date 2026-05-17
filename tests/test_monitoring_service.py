from unittest.mock import AsyncMock, patch

import pytest

from computeedge.exceptions import DeploymentError
from computeedge.services.monitoring import MonitoringService
from computeedge.state.manager import StateManager


DOCKER_STATS_OUTPUT = """app-backend-1\t2.50%\t120MiB / 4GiB
app-db-1\t0.80%\t85MiB / 4GiB
app-nginx-1\t0.10%\t15MiB / 4GiB"""

DOCKER_STATS_HIGH_CPU = """app-backend-1\t88.50%\t3200MiB / 4GiB
app-db-1\t5.80%\t500MiB / 4GiB"""

DOCKER_STATS_EMPTY = ""

DF_OUTPUT = """Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        40G   12G   26G  32% /"""

DF_HIGH_USAGE = """Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        40G   36G   2G  92% /"""

UPTIME_OUTPUT = "2026-03-08 10:30:00"


@pytest.fixture
def mock_ssh():
    ssh = AsyncMock()
    mock_conn = AsyncMock()
    ssh.connect = AsyncMock(return_value=mock_conn)
    return ssh


@pytest.fixture
def monitoring_service(state_manager, mock_ssh):
    svc = MonitoringService(state=state_manager)
    svc._ssh = mock_ssh
    return svc


async def setup_state(state_manager, user_id):
    await state_manager.add("ce_hetzner_abc123", user_id, {
        "provider": "hetzner",
        "server_id": 12345,
        "ip": "1.2.3.4",
        "plan": "cx22",
        "monthly_cost": 3.99,
        "ssh_key_path": "/fake/key",
    })


@pytest.mark.asyncio
async def test_check_health_healthy(monitoring_service, state_manager, mock_ssh, test_user_id):
    await setup_state(state_manager, test_user_id)
    mock_ssh.run = AsyncMock(side_effect=[
        DOCKER_STATS_OUTPUT,
        DF_OUTPUT,
        UPTIME_OUTPUT,
    ])
    result = await monitoring_service.check_health("ce_hetzner_abc123", test_user_id)
    assert result.status == "healthy"
    assert result.monthly_cost == 3.99
    assert len(result.alerts) == 0
    assert result.resources.cpu_usage_percent > 0
    assert result.resources.disk_usage_percent == 32.0


@pytest.mark.asyncio
async def test_check_health_degraded(monitoring_service, state_manager, mock_ssh, test_user_id):
    await setup_state(state_manager, test_user_id)
    mock_ssh.run = AsyncMock(side_effect=[
        DOCKER_STATS_HIGH_CPU,
        DF_OUTPUT,
        UPTIME_OUTPUT,
    ])
    result = await monitoring_service.check_health("ce_hetzner_abc123", test_user_id)
    assert result.status == "degraded"
    assert any("CPU" in a or "cpu" in a.lower() for a in result.alerts)


@pytest.mark.asyncio
async def test_check_health_disk_alert(monitoring_service, state_manager, mock_ssh, test_user_id):
    await setup_state(state_manager, test_user_id)
    mock_ssh.run = AsyncMock(side_effect=[
        DOCKER_STATS_OUTPUT,
        DF_HIGH_USAGE,
        UPTIME_OUTPUT,
    ])
    result = await monitoring_service.check_health("ce_hetzner_abc123", test_user_id)
    assert result.status == "degraded"
    assert any("Disk" in a or "disk" in a.lower() for a in result.alerts)


@pytest.mark.asyncio
async def test_check_health_down_ssh_failure(monitoring_service, state_manager, mock_ssh, test_user_id):
    await setup_state(state_manager, test_user_id)
    mock_ssh.connect = AsyncMock(side_effect=Exception("Connection refused"))
    result = await monitoring_service.check_health("ce_hetzner_abc123", test_user_id)
    assert result.status == "down"
    assert any("Cannot connect" in a for a in result.alerts)


@pytest.mark.asyncio
async def test_check_health_down_no_containers(monitoring_service, state_manager, mock_ssh, test_user_id):
    await setup_state(state_manager, test_user_id)
    mock_ssh.run = AsyncMock(side_effect=[
        DOCKER_STATS_EMPTY,
        DF_OUTPUT,
        UPTIME_OUTPUT,
    ])
    result = await monitoring_service.check_health("ce_hetzner_abc123", test_user_id)
    assert result.status == "down"


@pytest.mark.asyncio
async def test_check_health_deployment_not_found(monitoring_service, test_user_id):
    with pytest.raises(DeploymentError, match="not found"):
        await monitoring_service.check_health("ce_nonexistent_123", test_user_id)


@pytest.mark.asyncio
async def test_check_health_assessment_text(monitoring_service, state_manager, mock_ssh, test_user_id):
    await setup_state(state_manager, test_user_id)
    mock_ssh.run = AsyncMock(side_effect=[
        DOCKER_STATS_OUTPUT,
        DF_OUTPUT,
        UPTIME_OUTPUT,
    ])
    result = await monitoring_service.check_health("ce_hetzner_abc123", test_user_id)
    assert len(result.assessment) > 0


@pytest.mark.asyncio
async def test_check_health_uptime_parsed(monitoring_service, state_manager, mock_ssh, test_user_id):
    await setup_state(state_manager, test_user_id)
    mock_ssh.run = AsyncMock(side_effect=[
        DOCKER_STATS_OUTPUT,
        DF_OUTPUT,
        UPTIME_OUTPUT,
    ])
    result = await monitoring_service.check_health("ce_hetzner_abc123", test_user_id)
    assert len(result.uptime) > 0
