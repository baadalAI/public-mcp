from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from computeedge.services.infra.base import ProvisionedInfrastructure
from computeedge.services.infra.terraform import HetznerTerraformProvisioner


@pytest.mark.asyncio
async def test_terraform_provisioner_maps_outputs(tmp_path):
    runner = MagicMock()
    workspace = tmp_path / "dep"
    runner.prepare_workspace.return_value = workspace
    runner.apply = AsyncMock(return_value={
        "server_id": 12345,
        "public_ipv4": "1.2.3.4",
        "ssh_key_id": 42,
        "firewall_id": 77,
        "server_status": "running",
    })
    provisioner = HetznerTerraformProvisioner(
        "token-123",
        runner,
        location="hel1",
        image="ubuntu-24.04",
    )

    infrastructure = await provisioner.provision(
        "ce-hetzner-abc12345",
        "cx23",
        "ssh-ed25519 AAAA",
    )

    assert infrastructure.backend == "terraform"
    assert infrastructure.server_id == 12345
    assert infrastructure.ip == "1.2.3.4"
    assert infrastructure.metadata["workspace_dir"] == str(workspace)
    assert infrastructure.metadata["firewall_id"] == "77"
    runner.prepare_workspace.assert_called_once()
    runner.apply.assert_called_once_with(workspace)


@pytest.mark.asyncio
async def test_terraform_cleanup_uses_workspace_metadata():
    runner = MagicMock()
    runner.destroy = AsyncMock()
    provisioner = HetznerTerraformProvisioner("token-123", runner)
    infrastructure = ProvisionedInfrastructure(
        provider="hetzner",
        plan="cx23",
        server_id=123,
        server_name="ce-hetzner-abc12345",
        backend="terraform",
        ip="1.2.3.4",
        metadata={"workspace_dir": "/tmp/workspace"},
    )

    await provisioner.cleanup(infrastructure)

    runner.destroy.assert_called_once_with(Path("/tmp/workspace"))
