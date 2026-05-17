import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import RepoAnalysis, StackInfo, ValidationResult
from computeedge.services.deployment import DeploymentService
from computeedge.services.infra import HetznerInfrastructureProvisioner
from computeedge.services.validation import ConfigValidator


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
    svc = DeploymentService(
        provider=mock_provider,
        state=state_manager,
        stacks_config=load_bundled_yaml("stacks.yaml"),
        provisioner=provisioner,
    )
    svc._ssh = AsyncMock()
    svc._ssh.connect = AsyncMock(return_value=AsyncMock())
    svc._ssh.run = AsyncMock(return_value="")
    svc._ssh.upload_string = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_pre_deploy_check_blocks_on_missing_required_vars(deployment_service, test_user_id):
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        env_vars_required=["OPENAI_API_KEY", "STRIPE_SECRET_KEY"],
    )
    result = await deployment_service.pre_deploy_check(analysis, env_vars=None, repo_path="/tmp/app", user_id=test_user_id)
    assert result.can_proceed is False
    assert "OPENAI_API_KEY" in result.missing_env_vars
    assert "STRIPE_SECRET_KEY" in result.missing_env_vars


@pytest.mark.asyncio
async def test_pre_deploy_check_passes_when_required_vars_provided(deployment_service, test_user_id):
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        env_vars_required=["OPENAI_API_KEY"],
    )
    result = await deployment_service.pre_deploy_check(
        analysis, env_vars={"OPENAI_API_KEY": "sk-test123"}, repo_path="/tmp/app", user_id=test_user_id
    )
    assert result.can_proceed is True
    assert len(result.missing_env_vars) == 0


@pytest.mark.asyncio
async def test_pre_deploy_check_passes_when_no_required_vars(deployment_service, test_user_id):
    analysis = RepoAnalysis(
        stack=StackInfo(frontend="nextjs"),
        env_vars_required=[],
    )
    result = await deployment_service.pre_deploy_check(analysis, env_vars=None, repo_path="/tmp/app", user_id=test_user_id)
    assert result.can_proceed is True


@pytest.mark.asyncio
async def test_pre_deploy_check_warns_on_duplicate_deployment(deployment_service, state_manager, test_user_id):
    await state_manager.add("ce-hetzner-existing", test_user_id, {
        "provider": "hetzner", "ip": "5.6.7.8",
        "repo_path": "/tmp/myapp",
        "normalized_repo_path": state_manager.normalize_repo_path("/tmp/myapp"),
    })
    analysis = RepoAnalysis(stack=StackInfo(frontend="nextjs"))
    result = await deployment_service.pre_deploy_check(analysis, env_vars=None, repo_path="/tmp/myapp", user_id=test_user_id)
    assert result.can_proceed is False
    assert result.existing_deployment == "ce-hetzner-existing"
    assert result.existing_ip == "5.6.7.8"


@pytest.mark.asyncio
async def test_pre_deploy_check_allows_force_new(deployment_service, state_manager, test_user_id):
    await state_manager.add("ce-hetzner-existing", test_user_id, {
        "provider": "hetzner", "ip": "5.6.7.8",
        "repo_path": "/tmp/myapp",
        "normalized_repo_path": state_manager.normalize_repo_path("/tmp/myapp"),
    })
    analysis = RepoAnalysis(stack=StackInfo(frontend="nextjs"))
    result = await deployment_service.pre_deploy_check(
        analysis, env_vars=None, repo_path="/tmp/myapp", user_id=test_user_id, force_new=True
    )
    assert result.can_proceed is True


# --- Port consistency tests (extracted from _validate_before_deploy) ---

def test_validate_port_consistency_mismatch(deployment_service):
    analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
    generated = {
        "Dockerfile": "FROM python:3.11\nEXPOSE 8000",
        "docker-compose.yml": 'services:\n  backend:\n    ports:\n      - "3000:3000"',
    }
    issues = deployment_service._validate_port_consistency(analysis, generated)
    warnings = [i for i in issues if i.severity == "warning" and i.check == "port"]
    assert len(warnings) == 1


def test_validate_port_consistency_match(deployment_service):
    analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
    generated = {
        "Dockerfile": "FROM python:3.11\nEXPOSE 8000",
        "docker-compose.yml": 'services:\n  backend:\n    ports:\n      - "8000:8000"',
    }
    issues = deployment_service._validate_port_consistency(analysis, generated)
    assert len(issues) == 0


# --- Deploy integration tests with ConfigValidator ---

@pytest.mark.asyncio
async def test_deploy_blocked_before_infra_on_validation_error(mock_provider, state_manager, test_user_id):
    """Validation error should raise DeploymentError before any server is created."""
    validator = ConfigValidator()
    provisioner = HetznerInfrastructureProvisioner(mock_provider)
    svc = DeploymentService(
        provider=mock_provider,
        state=state_manager,
        stacks_config=load_bundled_yaml("stacks.yaml"),
        validator=validator,
        provisioner=provisioner,
    )
    svc._ssh = AsyncMock()
    svc._ssh.connect = AsyncMock(return_value=AsyncMock())
    svc._ssh.run = AsyncMock(return_value="")
    svc._ssh.upload_string = AsyncMock()

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        # No requirements.txt — validator will catch missing dep file
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        from computeedge.exceptions import DeploymentError
        with pytest.raises(DeploymentError, match="Pre-deploy validation failed"):
            await svc.deploy(str(tmp), analysis, user_id=test_user_id)
    # Server was never created
    mock_provider.create_server.assert_not_called()


@pytest.mark.asyncio
async def test_deploy_validates_even_with_docker_configs(mock_provider, state_manager, test_user_id):
    """When validator is set, user-supplied docker_configs are also validated."""
    validator = ConfigValidator()
    provisioner = HetznerInfrastructureProvisioner(mock_provider)
    svc = DeploymentService(
        provider=mock_provider,
        state=state_manager,
        stacks_config=load_bundled_yaml("stacks.yaml"),
        validator=validator,
        provisioner=provisioner,
    )
    svc._ssh = AsyncMock()
    svc._ssh.connect = AsyncMock(return_value=AsyncMock())
    svc._ssh.run = AsyncMock(return_value="")
    svc._ssh.upload_string = AsyncMock()

    with tempfile.TemporaryDirectory() as tmp:
        # No requirements.txt — Dockerfile references it, so validation should catch it
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        from computeedge.exceptions import DeploymentError
        with pytest.raises(DeploymentError, match="Pre-deploy validation failed"):
            await svc.deploy(
                str(tmp), analysis,
                docker_configs={"Dockerfile": "FROM python:3.11\nCOPY requirements.txt .\n"},
                user_id=test_user_id,
            )
    mock_provider.create_server.assert_not_called()
