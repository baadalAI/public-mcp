from unittest.mock import AsyncMock, patch

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import RepoAnalysis, StackInfo
from computeedge.services.deployment import DeploymentService
from computeedge.services.infra import HetznerInfrastructureProvisioner


@pytest.fixture(autouse=True)
def seed_repo_files(tmp_path):
    """Create minimal dependency files so pre-deploy validation passes."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.upload_ssh_key = AsyncMock(return_value=42)
    provider.create_server = AsyncMock(return_value={"id": 12345, "ip": "1.2.3.4", "status": "initializing"})
    provider.wait_for_ready = AsyncMock(return_value="1.2.3.4")
    provider.delete_server = AsyncMock()
    return provider


@pytest.fixture
def deployment_service(mock_provider, state_manager):
    provisioner = HetznerInfrastructureProvisioner(mock_provider)
    svc = DeploymentService(provider=mock_provider, state=state_manager, stacks_config=load_bundled_yaml("stacks.yaml"), provisioner=provisioner)
    svc._ssh = AsyncMock()
    svc._ssh.connect = AsyncMock(return_value=AsyncMock())
    svc._ssh.run = AsyncMock(return_value="")
    svc._ssh.upload_string = AsyncMock()
    svc._ssh.upload = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_always_generates_dockerfile_even_when_repo_has_one(deployment_service, tmp_path, test_user_id):
    """ComputeEdge always generates its own Dockerfile, even if the repo already has one."""
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        has_dockerfile=True,
    )

    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key",
               return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        result = await deployment_service.deploy(str(tmp_path), analysis, user_id=test_user_id)

    assert result.status == "deployed"
    upload_paths = [c.args[2] for c in deployment_service._ssh.upload_string.call_args_list]
    assert any(p.endswith("/Dockerfile") for p in upload_paths)
