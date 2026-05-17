from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import DeployResult, PreDeployCheckResult
from computeedge.services.analysis import AnalysisService
from computeedge.tools.deploy import make_deploy_tool


@pytest.fixture
def analysis_service():
    return AnalysisService(load_bundled_yaml("stacks.yaml"))


@pytest.fixture
def mock_deployment_service():
    svc = AsyncMock()
    svc.deploy = AsyncMock(return_value=DeployResult(
        status="deployed",
        provider="hetzner",
        url="http://1.2.3.4",
        deployment_id="ce-hetzner-abc12345",
        monthly_cost=3.99,
        ssh_access="ssh root@1.2.3.4",
        next_steps=["Point DNS to 1.2.3.4"],
    ))
    # pre_deploy_check is now async
    svc.pre_deploy_check = AsyncMock(return_value=PreDeployCheckResult())
    return svc


@pytest.fixture
def make_mock_deployment_service(mock_deployment_service):
    """Factory callable that returns the mock deployment service."""
    def _make(token, provider="hetzner"):
        return mock_deployment_service
    return _make


@pytest.fixture
def mock_state_manager():
    sm = AsyncMock()
    sm.get = AsyncMock(return_value=None)
    return sm


@pytest.mark.asyncio
async def test_deploy_tool_success(analysis_service, mock_deployment_service, make_mock_deployment_service, mock_state_manager, fixtures_dir):
    deploy = make_deploy_tool(analysis_service, make_mock_deployment_service, mock_state_manager)
    result = await deploy(provider_token="test-token", user_id=1, repo_path=str(fixtures_dir / "sample_fastapi"))
    assert result["status"] == "deployed"
    assert result["provider"] == "hetzner"
    assert result["deployment_id"].startswith("ce-hetzner-")
    mock_deployment_service.deploy.assert_called_once()


@pytest.mark.asyncio
async def test_deploy_tool_unsupported_provider(analysis_service, make_mock_deployment_service, mock_state_manager, fixtures_dir):
    deploy = make_deploy_tool(analysis_service, make_mock_deployment_service, mock_state_manager)
    result = await deploy(provider_token="test-token", user_id=1, repo_path=str(fixtures_dir / "sample_fastapi"), provider="aws")
    assert "error" in result
    assert "unsupported" in result["error"].lower()


@pytest.mark.asyncio
async def test_deploy_tool_nonexistent_path(analysis_service, make_mock_deployment_service, mock_state_manager):
    deploy = make_deploy_tool(analysis_service, make_mock_deployment_service, mock_state_manager)
    result = await deploy(provider_token="test-token", user_id=1, repo_path="/nonexistent/path")
    assert "error" in result


@pytest.mark.asyncio
async def test_deploy_tool_passes_env_vars(analysis_service, mock_deployment_service, make_mock_deployment_service, mock_state_manager, fixtures_dir):
    deploy = make_deploy_tool(analysis_service, make_mock_deployment_service, mock_state_manager)
    env = {"MY_KEY": "val"}
    await deploy(provider_token="test-token", user_id=1, repo_path=str(fixtures_dir / "sample_fastapi"), env_vars=env)
    call_kwargs = mock_deployment_service.deploy.call_args
    assert call_kwargs.kwargs.get("env_vars") == env or call_kwargs[1].get("env_vars") == env


@pytest.mark.asyncio
async def test_deploy_tool_passes_docker_configs(analysis_service, mock_deployment_service, make_mock_deployment_service, mock_state_manager, fixtures_dir):
    deploy = make_deploy_tool(analysis_service, make_mock_deployment_service, mock_state_manager)
    configs = {
        "docker-compose.yml": "services:\n  app:\n    build: ./repo",
        "nginx.conf": "worker_processes auto;",
        "Dockerfile": "FROM python:3.11-slim",
    }
    await deploy(provider_token="test-token", user_id=1, repo_path=str(fixtures_dir / "sample_fastapi"), docker_configs=configs)
    call_kwargs = mock_deployment_service.deploy.call_args
    assert call_kwargs.kwargs.get("docker_configs") == configs or call_kwargs[1].get("docker_configs") == configs


@pytest.mark.asyncio
async def test_deploy_tool_clones_git_url(analysis_service, mock_deployment_service, make_mock_deployment_service, mock_state_manager, fixtures_dir):
    """When given a git URL, deploy tool clones before analyzing."""
    deploy = make_deploy_tool(analysis_service, make_mock_deployment_service, mock_state_manager)

    with patch("computeedge.tools.deploy.is_git_url", return_value=True), \
         patch("computeedge.tools.deploy.clone_repo", new_callable=AsyncMock,
               return_value=Path(str(fixtures_dir / "sample_fastapi"))) as mock_clone, \
         patch("shutil.rmtree") as mock_rmtree:
        result = await deploy(provider_token="test-token", user_id=1, repo_path="https://github.com/example/repo")

    assert result["status"] == "deployed"
    mock_clone.assert_called_once_with("https://github.com/example/repo")
    mock_rmtree.assert_called_once()
    mock_deployment_service.deploy.assert_called_once()


@pytest.mark.asyncio
async def test_deploy_tool_failed_retryable():
    """Tool returns failed_retryable when diagnostics have retry_context."""
    from computeedge.models import RetryContext, DeployDiagnostics
    from computeedge.exceptions import DeploymentError

    ctx = RetryContext(attempt=1, server_id=123, server_ip="1.2.3.4",
                       deployment_id="ce-hetzner-abc", ssh_key_path="/tmp/key")
    diag = DeployDiagnostics(
        phase="docker_build",
        agent_instruction="Deploy failed...",
        retry_context=ctx,
    )
    err = DeploymentError("Build failed", diagnostics=diag)

    mock_analysis_svc = MagicMock()
    mock_analysis_svc.analyze = AsyncMock(return_value=MagicMock())
    mock_deploy_svc = MagicMock()
    mock_deploy_svc.deploy = AsyncMock(side_effect=err)
    mock_deploy_svc.pre_deploy_check = AsyncMock(
        return_value=MagicMock(can_proceed=True)
    )

    mock_state_manager = AsyncMock()
    make_deploy_svc = lambda token, provider="hetzner": mock_deploy_svc

    deploy_fn = make_deploy_tool(mock_analysis_svc, make_deploy_svc, mock_state_manager)
    result = await deploy_fn(provider_token="test-token", user_id=1, repo_path="/tmp/repo")

    assert result["status"] == "failed_retryable"
    assert "diagnostics" in result
    assert result["diagnostics"]["retry_context"]["server_id"] == 123


@pytest.mark.asyncio
async def test_deploy_tool_failed_final():
    """Tool returns failed when diagnostics have no retry_context (final failure)."""
    from computeedge.models import DeployDiagnostics
    from computeedge.exceptions import DeploymentError

    diag = DeployDiagnostics(
        phase="docker_build",
        agent_instruction="Deploy failed after 3 attempts...",
        retry_context=None,
    )
    err = DeploymentError("Build failed", diagnostics=diag)

    mock_analysis_svc = MagicMock()
    mock_analysis_svc.analyze = AsyncMock(return_value=MagicMock())
    mock_deploy_svc = MagicMock()
    mock_deploy_svc.deploy = AsyncMock(side_effect=err)
    mock_deploy_svc.pre_deploy_check = AsyncMock(
        return_value=MagicMock(can_proceed=True)
    )

    mock_state_manager = AsyncMock()
    make_deploy_svc = lambda token, provider="hetzner": mock_deploy_svc

    deploy_fn = make_deploy_tool(mock_analysis_svc, make_deploy_svc, mock_state_manager)
    result = await deploy_fn(provider_token="test-token", user_id=1, repo_path="/tmp/repo")

    assert result["status"] == "failed"
    assert "diagnostics" in result


@pytest.mark.asyncio
async def test_deploy_tool_passes_retry_context():
    """Tool deserializes retry_context dict and passes to service."""
    mock_analysis_svc = MagicMock()
    mock_analysis_svc.analyze = AsyncMock(return_value=MagicMock())
    mock_deploy_svc = MagicMock()
    mock_deploy_svc.deploy = AsyncMock(return_value=DeployResult(
        status="deployed", provider="hetzner", url="http://1.2.3.4",
        deployment_id="ce-hetzner-abc", monthly_cost=3.49,
        ssh_access="ssh root@1.2.3.4", next_steps=[],
    ))

    mock_state_manager = AsyncMock()
    make_deploy_svc = lambda token, provider="hetzner": mock_deploy_svc

    deploy_fn = make_deploy_tool(mock_analysis_svc, make_deploy_svc, mock_state_manager)
    ctx_dict = {"attempt": 2, "server_id": 123, "server_ip": "1.2.3.4",
                "deployment_id": "ce-hetzner-abc", "ssh_key_path": "/tmp/key"}

    await deploy_fn(provider_token="test-token", user_id=1, repo_path="/tmp/repo", retry_context=ctx_dict)

    # Verify retry_context was passed as RetryContext instance
    call_kwargs = mock_deploy_svc.deploy.call_args
    passed_ctx = call_kwargs.kwargs.get("retry_context") or call_kwargs[1].get("retry_context")
    assert passed_ctx is not None
    assert passed_ctx.attempt == 2
    assert passed_ctx.server_id == 123


@pytest.mark.asyncio
async def test_deploy_tool_skips_pre_deploy_check_on_retry():
    """Tool skips pre_deploy_check when retry_context is provided."""
    mock_analysis_svc = MagicMock()
    mock_analysis_svc.analyze = AsyncMock(return_value=MagicMock())
    mock_deploy_svc = MagicMock()
    mock_deploy_svc.deploy = AsyncMock(return_value=DeployResult(
        status="deployed", provider="hetzner", url="http://1.2.3.4",
        deployment_id="ce-hetzner-abc", monthly_cost=3.49,
        ssh_access=None, next_steps=[],
    ))
    mock_deploy_svc.pre_deploy_check = AsyncMock()

    mock_state_manager = AsyncMock()
    make_deploy_svc = lambda token, provider="hetzner": mock_deploy_svc

    deploy_fn = make_deploy_tool(mock_analysis_svc, make_deploy_svc, mock_state_manager)
    ctx_dict = {"attempt": 2, "server_id": 123}

    await deploy_fn(provider_token="test-token", user_id=1, repo_path="/tmp/repo", retry_context=ctx_dict)

    mock_deploy_svc.pre_deploy_check.assert_not_called()


@pytest.mark.asyncio
async def test_deploy_tool_auto_detects_nanoclaw_workspace(tmp_path):
    """Deploy auto-detects nanoclaw workspace without stack='nanoclaw'."""
    # Create a workspace dir (groups/ but no package.json with nanoclaw)
    workspace = tmp_path / "my_workspace"
    workspace.mkdir()
    (workspace / "groups").mkdir()
    (workspace / "groups" / "main").mkdir()
    (workspace / "groups" / "main" / "CLAUDE.md").write_text("# My Agent")

    mock_analysis_svc = MagicMock()
    mock_analysis_svc.analyze = AsyncMock()  # should NOT be called
    mock_deploy_svc = MagicMock()
    mock_deploy_svc.deploy = AsyncMock(return_value=DeployResult(
        status="deployed", provider="hetzner", url="ssh root@1.2.3.4",
        deployment_id="ce-hetzner-abc", monthly_cost=7.59,
        ssh_access="ssh root@1.2.3.4", next_steps=[],
    ))
    mock_deploy_svc.pre_deploy_check = AsyncMock(
        return_value=MagicMock(can_proceed=True)
    )

    mock_state_manager = AsyncMock()
    make_deploy_svc = lambda token, provider="hetzner": mock_deploy_svc

    deploy_fn = make_deploy_tool(mock_analysis_svc, make_deploy_svc, mock_state_manager)
    # No stack="nanoclaw" — should auto-detect from directory structure
    result = await deploy_fn(
        provider_token="test-token", user_id=1, repo_path=str(workspace),
    )

    assert result["status"] == "deployed"
    # Analysis service should NOT have been called (synthetic analysis used)
    mock_analysis_svc.analyze.assert_not_called()
    # Deploy service should have been called with nanoclaw analysis
    call_args = mock_deploy_svc.deploy.call_args
    analysis_arg = call_args[0][1]  # second positional arg
    assert analysis_arg.stack.backend == "nanoclaw"
