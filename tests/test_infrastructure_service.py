from unittest.mock import AsyncMock

import pytest

from computeedge.services.infra.base import (
    InfrastructurePlanResult,
    ProvisionedInfrastructure,
)
from computeedge.services.infrastructure import InfrastructureLifecycleService


async def _seed_state(state_manager, user_id):
    await state_manager.add("ce-hetzner-abc12345", user_id, {
        "provider": "hetzner",
        "infra_backend": "terraform",
        "infra_metadata": {"workspace_dir": "/tmp/workspace"},
        "server_id": 12345,
        "ip": "1.2.3.4",
        "status": "running",
        "plan": "cx23",
        "repo_path": "/tmp/app",
        "deployed_at": "2026-03-28T12:00:00Z",
    })


@pytest.mark.asyncio
async def test_show_returns_state_snapshot(state_manager, test_user_id):
    await _seed_state(state_manager, test_user_id)
    service = InfrastructureLifecycleService(state_manager, AsyncMock())

    result = await service.show("ce-hetzner-abc12345", test_user_id)

    assert result.deployment_id == "ce-hetzner-abc12345"
    assert result.backend == "terraform"
    assert result.server_id == 12345


@pytest.mark.asyncio
async def test_refresh_updates_state(state_manager, test_user_id):
    await _seed_state(state_manager, test_user_id)
    provisioner = AsyncMock()
    provisioner.refresh = AsyncMock(return_value=ProvisionedInfrastructure(
        provider="hetzner",
        plan="cx23",
        server_id=12345,
        server_name="ce-hetzner-abc12345",
        backend="terraform",
        ip="5.6.7.8",
        status="running",
        metadata={"workspace_dir": "/tmp/workspace", "firewall_id": "77"},
    ))
    service = InfrastructureLifecycleService(state_manager, provisioner)

    result = await service.refresh("ce-hetzner-abc12345", test_user_id)

    assert result.changed_fields == ["ip", "infra_metadata"]
    state = await state_manager.get("ce-hetzner-abc12345", test_user_id)
    assert state["ip"] == "5.6.7.8"
    assert state["infra_metadata"]["firewall_id"] == "77"


@pytest.mark.asyncio
async def test_plan_delegates_to_provisioner(state_manager, test_user_id):
    await _seed_state(state_manager, test_user_id)
    provisioner = AsyncMock()
    provisioner.plan = AsyncMock(return_value=InfrastructurePlanResult(
        provider="hetzner",
        backend="terraform",
        has_changes=True,
        summary="Terraform detected pending infrastructure changes.",
    ))
    service = InfrastructureLifecycleService(state_manager, provisioner)

    result = await service.plan("ce-hetzner-abc12345", test_user_id)

    assert result.has_changes is True
    provisioner.plan.assert_called_once()


@pytest.mark.asyncio
async def test_destroy_removes_state_after_destroy(state_manager, test_user_id):
    await _seed_state(state_manager, test_user_id)
    provisioner = AsyncMock()
    provisioner.destroy = AsyncMock()
    service = InfrastructureLifecycleService(state_manager, provisioner)

    result = await service.destroy("ce-hetzner-abc12345", test_user_id)

    assert result.status == "destroyed"
    assert await state_manager.get("ce-hetzner-abc12345", test_user_id) is None
