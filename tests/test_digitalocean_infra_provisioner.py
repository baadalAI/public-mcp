from unittest.mock import AsyncMock

import pytest

from computeedge.services.infra.digitalocean import DigitalOceanInfrastructureProvisioner


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.upload_ssh_key = AsyncMock(return_value=42)
    provider.create_droplet = AsyncMock(return_value={
        "id": 12345,
        "ip": None,
        "status": "new",
    })
    provider.wait_for_ready = AsyncMock(return_value="1.2.3.4")
    provider.create_firewall = AsyncMock(return_value="fw-abc-123")
    provider.delete_droplet = AsyncMock()
    provider.delete_ssh_key = AsyncMock()
    provider.delete_firewall = AsyncMock()
    provider.get_droplet = AsyncMock(return_value={
        "id": 12345,
        "ip": "1.2.3.4",
        "status": "active",
    })
    return provider


@pytest.mark.asyncio
async def test_provision_creates_droplet_and_returns_handle(mock_provider):
    provisioner = DigitalOceanInfrastructureProvisioner(mock_provider)
    result = await provisioner.provision(
        "ce-digitalocean-abc12345", "s-1vcpu-1gb", "ssh-ed25519 AAAA",
    )
    assert result.provider == "digitalocean"
    assert result.plan == "s-1vcpu-1gb"
    assert result.server_id == 12345
    assert result.server_name == "ce-digitalocean-abc12345"
    assert result.ssh_key_id == 42
    assert result.backend == "api"
    mock_provider.upload_ssh_key.assert_called_once()
    mock_provider.create_droplet.assert_called_once_with(
        "ce-digitalocean-abc12345", "s-1vcpu-1gb", 42, user_data=None,
    )


@pytest.mark.asyncio
async def test_wait_until_ready_updates_ip_and_creates_firewall(mock_provider):
    provisioner = DigitalOceanInfrastructureProvisioner(mock_provider)
    infrastructure = await provisioner.provision(
        "ce-digitalocean-abc12345", "s-1vcpu-1gb", "ssh-ed25519 AAAA",
    )
    ready = await provisioner.wait_until_ready(infrastructure)
    assert ready.ip == "1.2.3.4"
    assert ready.status == "running"
    assert ready.metadata.get("firewall_id") == "fw-abc-123"
    mock_provider.wait_for_ready.assert_called_once_with(12345)
    mock_provider.create_firewall.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_deletes_all_resources(mock_provider):
    provisioner = DigitalOceanInfrastructureProvisioner(mock_provider)
    infrastructure = await provisioner.provision(
        "ce-digitalocean-abc12345", "s-1vcpu-1gb", "ssh-ed25519 AAAA",
    )
    ready = await provisioner.wait_until_ready(infrastructure)
    await provisioner.cleanup(ready)
    mock_provider.delete_firewall.assert_called_once_with("fw-abc-123")
    mock_provider.delete_droplet.assert_called_once_with(12345)
    mock_provider.delete_ssh_key.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_cleanup_without_firewall(mock_provider):
    provisioner = DigitalOceanInfrastructureProvisioner(mock_provider)
    infrastructure = await provisioner.provision(
        "ce-digitalocean-abc12345", "s-1vcpu-1gb", "ssh-ed25519 AAAA",
    )
    await provisioner.cleanup(infrastructure)
    mock_provider.delete_firewall.assert_not_called()
    mock_provider.delete_droplet.assert_called_once_with(12345)


@pytest.mark.asyncio
async def test_destroy_calls_cleanup(mock_provider):
    provisioner = DigitalOceanInfrastructureProvisioner(mock_provider)
    infrastructure = await provisioner.provision(
        "ce-digitalocean-abc12345", "s-1vcpu-1gb", "ssh-ed25519 AAAA",
    )
    await provisioner.destroy(infrastructure)
    mock_provider.delete_droplet.assert_called_once_with(12345)


@pytest.mark.asyncio
async def test_refresh_returns_updated_state(mock_provider):
    provisioner = DigitalOceanInfrastructureProvisioner(mock_provider)
    infrastructure = await provisioner.provision(
        "ce-digitalocean-abc12345", "s-1vcpu-1gb", "ssh-ed25519 AAAA",
    )
    refreshed = await provisioner.refresh(infrastructure)
    assert refreshed.ip == "1.2.3.4"
    assert refreshed.status == "active"
    mock_provider.get_droplet.assert_called_once_with(12345)


@pytest.mark.asyncio
async def test_plan_returns_status_summary(mock_provider):
    provisioner = DigitalOceanInfrastructureProvisioner(mock_provider)
    infrastructure = await provisioner.provision(
        "ce-digitalocean-abc12345", "s-1vcpu-1gb", "ssh-ed25519 AAAA",
    )
    result = await provisioner.plan(infrastructure)
    assert result.provider == "digitalocean"
    assert result.has_changes is False
    assert "active" in result.summary
