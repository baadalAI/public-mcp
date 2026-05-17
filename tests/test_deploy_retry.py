# tests/test_deploy_retry.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from computeedge.exceptions import DeploymentError
from computeedge.models import RepoAnalysis, StackInfo, DatabaseInfo, RetryContext, MAX_DEP_FILE_SIZE, ValidationResult
from computeedge.services.deployment import DeploymentService
from computeedge.services.infra.base import ProvisionedInfrastructure


def _make_service():
    """Create a DeploymentService with mocked dependencies."""
    svc = DeploymentService.__new__(DeploymentService)
    svc._ssh = MagicMock()
    svc._ssh.run = AsyncMock()
    svc._provider_name = "hetzner"
    return svc


@pytest.mark.asyncio
async def test_read_dependency_files_python():
    svc = _make_service()
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python", backend_path="."),
    )

    async def mock_run(conn, cmd):
        if "requirements.txt" in cmd:
            return "flask==3.0.0\npsycopg2==2.9.9"
        if "pyproject.toml" in cmd:
            raise Exception("file not found")
        if "head" in cmd:
            raise Exception("file not found")
        return ""

    svc._ssh.run = AsyncMock(side_effect=mock_run)
    result = await svc._read_dependency_files(MagicMock(), analysis, "/root/ce-hetzner-abc/repo")
    assert "requirements.txt" in result
    assert "flask" in result["requirements.txt"]


@pytest.mark.asyncio
async def test_read_dependency_files_caps_size():
    svc = _make_service()
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python", backend_path="."),
    )
    large_content = "x" * (MAX_DEP_FILE_SIZE + 1000)

    async def mock_run(conn, cmd):
        if "requirements.txt" in cmd:
            return large_content
        raise Exception("file not found")

    svc._ssh.run = AsyncMock(side_effect=mock_run)
    result = await svc._read_dependency_files(MagicMock(), analysis, "/root/ce-hetzner-abc/repo")
    assert len(result.get("requirements.txt", "")) <= MAX_DEP_FILE_SIZE


@pytest.mark.asyncio
async def test_read_dependency_files_node():
    svc = _make_service()
    analysis = RepoAnalysis(
        stack=StackInfo(backend="express", backend_language="javascript", backend_path="."),
    )

    async def mock_run(conn, cmd):
        if "package.json" in cmd:
            return '{"name": "myapp", "dependencies": {"express": "^4.0.0"}}'
        raise Exception("file not found")

    svc._ssh.run = AsyncMock(side_effect=mock_run)
    result = await svc._read_dependency_files(MagicMock(), analysis, "/root/ce-hetzner-abc/repo")
    assert "package.json" in result
    assert "express" in result["package.json"]


def test_build_suggested_configs_apt_package():
    svc = _make_service()
    generated_files = {
        "Dockerfile": (
            "FROM python:3.11-slim\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
        ),
    }
    suggested_fixes = [
        {"description": "Add system package 'libpq-dev'", "confidence": "high",
         "fix_type": "dockerfile_modification", "package": "libpq-dev"},
    ]
    result = svc._build_suggested_docker_configs(generated_files, suggested_fixes)
    assert result is not None
    assert "libpq-dev" in result["Dockerfile"]
    assert "gcc" in result["Dockerfile"]  # original package still present


def test_build_suggested_configs_env_var():
    svc = _make_service()
    generated_files = {
        "docker-compose.yml": (
            "services:\n"
            "  backend:\n"
            "    environment:\n"
            "      - SECRET_KEY=abc123\n"
        ),
    }
    suggested_fixes = [
        {"description": "Set RAILS_ENV", "confidence": "medium",
         "fix_type": "env_var", "vars": {"RAILS_ENV": "production"}},
    ]
    result = svc._build_suggested_docker_configs(generated_files, suggested_fixes)
    assert result is not None
    assert "RAILS_ENV=production" in result["docker-compose.yml"]
    assert "SECRET_KEY=abc123" in result["docker-compose.yml"]


def test_build_suggested_configs_no_match():
    svc = _make_service()
    generated_files = {"Dockerfile": "FROM python:3.11-slim\n"}
    suggested_fixes = []
    result = svc._build_suggested_docker_configs(generated_files, suggested_fixes)
    assert result is None


def test_build_suggested_configs_multiple_apt_packages():
    svc = _make_service()
    generated_files = {
        "Dockerfile": (
            "FROM python:3.11-slim\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*\n"
        ),
    }
    suggested_fixes = [
        {"fix_type": "dockerfile_modification", "package": "libpq-dev",
         "description": "add libpq-dev", "confidence": "high"},
        {"fix_type": "dockerfile_modification", "package": "libffi-dev",
         "description": "add libffi-dev", "confidence": "high"},
    ]
    result = svc._build_suggested_docker_configs(generated_files, suggested_fixes)
    assert result is not None
    assert "libpq-dev" in result["Dockerfile"]
    assert "libffi-dev" in result["Dockerfile"]


def test_build_agent_instruction_rule_match():
    svc = _make_service()
    instruction = svc._build_agent_instruction(
        phase="docker_build",
        error_summary="pg_config not found",
        suggested_fixes=[{"description": "Add libpq-dev", "confidence": "high"}],
        has_suggested_configs=True,
        retry_context=None,
    )
    assert "docker_build" in instruction
    assert "pg_config" in instruction
    assert "suggested_docker_configs" in instruction
    assert "retry_context" in instruction


def test_build_agent_instruction_no_match():
    svc = _make_service()
    instruction = svc._build_agent_instruction(
        phase="docker_build",
        error_summary="Unknown error XYZ",
        suggested_fixes=[],
        has_suggested_configs=False,
        retry_context=None,
    )
    assert "generated_files" in instruction or "logs" in instruction.lower()
    assert "Analyze" in instruction or "analyze" in instruction


def test_build_agent_instruction_final_attempt():
    svc = _make_service()
    ctx = RetryContext(
        attempt=3, max_retries=3,
        previous_errors=["error1", "error2", "error3"],
    )
    instruction = svc._build_agent_instruction(
        phase="docker_build",
        error_summary="Still failing",
        suggested_fixes=[],
        has_suggested_configs=False,
        retry_context=ctx,
    )
    assert "3 attempts" in instruction or "final" in instruction.lower()
    assert "deleted" in instruction.lower() or "user" in instruction.lower()


def test_build_agent_instruction_includes_previous_fixes():
    svc = _make_service()
    ctx = RetryContext(
        attempt=2, max_retries=3,
        previous_errors=["pg_config not found"],
        previous_fixes=[{"attempt": 1, "fix_description": "Added libpq-dev"}],
    )
    instruction = svc._build_agent_instruction(
        phase="docker_build",
        error_summary="psycopg2 still fails",
        suggested_fixes=[],
        has_suggested_configs=False,
        retry_context=ctx,
    )
    assert "libpq-dev" in instruction or "previous" in instruction.lower()


@pytest.mark.asyncio
async def test_connect_to_existing_server_success():
    svc = _make_service()
    svc._ssh.connect = AsyncMock(return_value=MagicMock())
    svc._ssh.run = AsyncMock(return_value="Docker Compose version v2.24.0")

    ctx = RetryContext(server_ip="1.2.3.4", ssh_key_path="/tmp/key")
    conn = await svc._connect_to_existing_server(ctx)
    assert conn is not None
    svc._ssh.connect.assert_called_once_with("1.2.3.4", "/tmp/key")


@pytest.mark.asyncio
async def test_connect_to_existing_server_gone():
    from unittest.mock import patch
    svc = _make_service()
    svc._ssh.connect = AsyncMock(side_effect=Exception("Connection refused"))

    ctx = RetryContext(server_ip="1.2.3.4", ssh_key_path="/tmp/key")
    with patch("asyncio.sleep", new_callable=AsyncMock):
        conn = await svc._connect_to_existing_server(ctx)
    assert conn is None
    assert svc._ssh.connect.call_count == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_connect_to_existing_server_docker_validation_fails():
    svc = _make_service()
    svc._ssh.connect = AsyncMock(return_value=MagicMock())
    svc._ssh.run = AsyncMock(side_effect=Exception("docker: command not found"))

    ctx = RetryContext(server_ip="1.2.3.4", ssh_key_path="/tmp/key")
    with pytest.raises(DeploymentError, match="Docker validation"):
        await svc._connect_to_existing_server(ctx)


@pytest.mark.asyncio
async def test_deploy_failure_keeps_server_when_retries_remain(tmp_path):
    """On first failure, server should NOT be deleted if retries remain."""
    svc = _make_service()
    svc._provider = MagicMock()
    svc._provider.delete_server = AsyncMock()
    _infra = ProvisionedInfrastructure(
        provider="hetzner", plan="cx23", server_id=123,
        server_name="ce-hetzner-test", backend="api", ip="1.2.3.4", status="running",
    )
    svc._provisioner = MagicMock()
    svc._provisioner.provision = AsyncMock(return_value=_infra)
    svc._provisioner.wait_until_ready = AsyncMock(return_value=_infra)
    svc._provisioner.cleanup = AsyncMock()
    svc._state = AsyncMock()
    svc._stacks_config = {}
    svc._jinja = MagicMock()
    svc._validate_port_consistency = MagicMock(return_value=[])
    svc._validator = MagicMock()
    svc._validator.validate = MagicMock(return_value=ValidationResult(valid=True, issues=[], corrected_configs={}, corrections_applied=[]))

    # SSH connect succeeds, but docker compose up fails
    mock_conn = MagicMock()
    svc._ssh_connect_with_retry = AsyncMock(return_value=mock_conn)
    svc._ssh.run = AsyncMock(side_effect=[
        None,  # Docker install
        None,  # mkdir
        None,  # upload repo steps...
    ])
    svc._upload_repo = AsyncMock()
    svc._upload_docker_files = AsyncMock()

    # Make compose up fail
    call_count = 0
    async def run_side_effect(conn, cmd):
        nonlocal call_count
        call_count += 1
        if "docker compose up" in cmd:
            raise Exception("Build error: pg_config not found")
        if "docker compose logs" in cmd:
            return "ERROR: pg_config not found"
        return ""
    svc._ssh.run = AsyncMock(side_effect=run_side_effect)

    svc._resolve_ssh_key = MagicMock(return_value=("/tmp/key", "/tmp/key.pub", "ssh-ed25519 AAAA"))
    svc._read_dependency_files = AsyncMock(return_value={"requirements.txt": "psycopg2"})
    svc._build_suggested_docker_configs = MagicMock(return_value=None)

    analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))

    # Create minimal repo files so pre-deploy validation passes
    from pathlib import Path
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (repo / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    with pytest.raises(DeploymentError) as exc_info:
        await svc.deploy(str(repo), analysis)

    # Server should NOT have been deleted
    svc._provider.delete_server.assert_not_called()
    # Diagnostics should have retry_context
    assert exc_info.value.diagnostics is not None
    assert exc_info.value.diagnostics.retry_context is not None
    assert exc_info.value.diagnostics.retry_context.server_id == 123


@pytest.mark.asyncio
async def test_deploy_final_failure_deletes_server():
    """On final attempt failure, server should be deleted."""
    svc = _make_service()
    svc._provider = MagicMock()
    svc._provider.delete_server = AsyncMock()
    svc._state = AsyncMock()
    svc._stacks_config = {}

    svc._provisioner = MagicMock()
    svc._provisioner.cleanup = AsyncMock()

    mock_conn = MagicMock()
    svc._connect_to_existing_server = AsyncMock(return_value=mock_conn)
    svc._upload_docker_files = AsyncMock()
    svc._apply_docker_config_patches = AsyncMock()

    async def run_side_effect(conn, cmd):
        if "docker compose down" in cmd:
            return ""
        if "docker compose up" in cmd:
            raise Exception("Still failing")
        if "docker compose logs" in cmd:
            return "ERROR: still broken"
        return ""
    svc._ssh.run = AsyncMock(side_effect=run_side_effect)
    svc._read_dependency_files = AsyncMock(return_value={})
    svc._build_suggested_docker_configs = MagicMock(return_value=None)

    analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
    retry_ctx = RetryContext(
        attempt=3, max_retries=3, server_id=123,
        server_ip="1.2.3.4", deployment_id="ce-hetzner-abc",
        ssh_key_path="/tmp/key",
    )

    with pytest.raises(DeploymentError) as exc_info:
        await svc.deploy("/tmp/repo", analysis,
                         docker_configs={"Dockerfile": "FROM python:3.11"},
                         retry_context=retry_ctx)

    # Server SHOULD be cleaned up on final attempt
    svc._provisioner.cleanup.assert_called_once()
    # Diagnostics should NOT have retry_context (final failure)
    diag = exc_info.value.diagnostics
    assert diag is not None
    assert diag.retry_context is None


@pytest.mark.asyncio
async def test_deploy_with_retry_context_skips_server_creation():
    """When retry_context is provided, skip server creation and connect to existing."""
    svc = _make_service()
    svc._provider = MagicMock()
    svc._provider.create_server = AsyncMock()  # should NOT be called
    svc._state = AsyncMock()
    svc._stacks_config = {}

    mock_conn = MagicMock()
    svc._connect_to_existing_server = AsyncMock(return_value=mock_conn)
    svc._upload_docker_files = AsyncMock()
    svc._apply_docker_config_patches = AsyncMock()

    # Make it succeed
    svc._ssh.run = AsyncMock(return_value="")
    svc._detect_exposed_port = AsyncMock(return_value=80)

    analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
    retry_ctx = RetryContext(
        attempt=2, max_retries=3, server_id=123,
        server_ip="1.2.3.4", deployment_id="ce-hetzner-abc",
        ssh_key_path="/tmp/key",
    )

    result = await svc.deploy(
        "/tmp/repo", analysis,
        docker_configs={"Dockerfile": "FROM python:3.11"},
        retry_context=retry_ctx,
    )

    svc._provider.create_server.assert_not_called()
    svc._connect_to_existing_server.assert_called_once()
    assert result.status == "deployed"


@pytest.mark.asyncio
async def test_previous_fixes_tracked_across_retries():
    """Verify that previous_fixes accumulates across retry attempts."""
    svc = _make_service()
    svc._provider = MagicMock()
    svc._provider.delete_server = AsyncMock()
    svc._state = AsyncMock()
    svc._stacks_config = {}

    svc._provisioner = MagicMock()
    svc._provisioner.cleanup = AsyncMock()

    mock_conn = MagicMock()
    svc._connect_to_existing_server = AsyncMock(return_value=mock_conn)
    svc._upload_docker_files = AsyncMock()
    svc._apply_docker_config_patches = AsyncMock()
    svc._read_dependency_files = AsyncMock(return_value={})
    svc._build_suggested_docker_configs = MagicMock(return_value=None)

    async def run_side_effect(conn, cmd):
        if "docker compose down" in cmd:
            return ""
        if "docker compose up" in cmd:
            raise Exception("Still broken")
        if "docker compose logs" in cmd:
            return "ERROR: still broken"
        return ""
    svc._ssh.run = AsyncMock(side_effect=run_side_effect)

    analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))

    # Simulate attempt 2 (retry of attempt 1)
    ctx = RetryContext(
        attempt=2, max_retries=3, server_id=123,
        server_ip="1.2.3.4", deployment_id="ce-hetzner-abc",
        ssh_key_path="/tmp/key",
        previous_errors=["first error"],
        previous_fixes=[{"attempt": 1, "fix_description": "first fix",
                         "docker_configs_used": {}, "result": "first error"}],
    )

    with pytest.raises(DeploymentError) as exc_info:
        await svc.deploy("/tmp/repo", analysis,
                         docker_configs={"Dockerfile": "FROM python:3.11"},
                         retry_context=ctx)

    diag = exc_info.value.diagnostics
    assert diag.retry_context is not None
    # Should now be attempt 3
    assert diag.retry_context.attempt == 3
    # Previous errors should have both
    assert len(diag.retry_context.previous_errors) == 2
    assert "first error" in diag.retry_context.previous_errors[0]
    # Previous fixes should have both
    assert len(diag.retry_context.previous_fixes) == 2
