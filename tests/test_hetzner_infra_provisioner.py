from unittest.mock import AsyncMock

import pytest

from computeedge.services.infra.hetzner import HetznerInfrastructureProvisioner


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.upload_ssh_key = AsyncMock(return_value=42)
    provider.create_server = AsyncMock(return_value={
        "id": 12345,
        "ip": "1.2.3.4",
        "status": "initializing",
    })
    provider.wait_for_ready = AsyncMock(return_value="1.2.3.4")
    provider.delete_server = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_provision_creates_server_and_returns_handle(mock_provider):
    provisioner = HetznerInfrastructureProvisioner(mock_provider)

    result = await provisioner.provision(
        "ce-hetzner-abc12345",
        "cx23",
        "ssh-ed25519 AAAA",
    )

    assert result.provider == "hetzner"
    assert result.plan == "cx23"
    assert result.server_id == 12345
    assert result.server_name == "ce-hetzner-abc12345"
    assert result.ssh_key_id == 42
    mock_provider.upload_ssh_key.assert_called_once()
    mock_provider.create_server.assert_called_once_with(
        "ce-hetzner-abc12345", "cx23", 42, user_data=None,
    )


@pytest.mark.asyncio
async def test_wait_until_ready_updates_ip(mock_provider):
    provisioner = HetznerInfrastructureProvisioner(mock_provider)
    infrastructure = await provisioner.provision(
        "ce-hetzner-abc12345",
        "cx23",
        "ssh-ed25519 AAAA",
    )

    ready = await provisioner.wait_until_ready(infrastructure)

    assert ready.ip == "1.2.3.4"
    assert ready.status == "running"
    mock_provider.wait_for_ready.assert_called_once_with(12345)


@pytest.mark.asyncio
async def test_cleanup_deletes_server(mock_provider):
    provisioner = HetznerInfrastructureProvisioner(mock_provider)
    infrastructure = await provisioner.provision(
        "ce-hetzner-abc12345",
        "cx23",
        "ssh-ed25519 AAAA",
    )

    await provisioner.cleanup(infrastructure)

    mock_provider.delete_server.assert_called_once_with(12345)
