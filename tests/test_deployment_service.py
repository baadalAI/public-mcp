import secrets
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.exceptions import DeploymentError
from computeedge.models import (
    DatabaseInfo, RepoAnalysis, StackInfo,
)
from computeedge.services.deployment import DeploymentService
from computeedge.services.infra import HetznerInfrastructureProvisioner
from computeedge.state.manager import StateManager


@pytest.fixture(autouse=True)
def seed_repo_files(tmp_path):
    """Create minimal dependency files so pre-deploy validation passes."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "package.json").write_text('{"name": "app", "scripts": {"build": "next build", "start": "next start"}}\n')


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.upload_ssh_key = AsyncMock(return_value=42)
    provider.create_server = AsyncMock(return_value={
        "id": 12345, "ip": "1.2.3.4", "status": "initializing"
    })
    provider.wait_for_ready = AsyncMock(return_value="1.2.3.4")
    provider.delete_server = AsyncMock()
    return provider


@pytest.fixture
def mock_ssh():
    ssh = AsyncMock()
    mock_conn = AsyncMock()
    ssh.connect = AsyncMock(return_value=mock_conn)
    ssh.run = AsyncMock(return_value="")
    ssh.upload_string = AsyncMock()
    return ssh


@pytest.fixture
def deployment_service(mock_provider, state_manager, mock_ssh):
    provisioner = HetznerInfrastructureProvisioner(mock_provider)
    svc = DeploymentService(
        provider=mock_provider,
        state=state_manager,
        stacks_config=load_bundled_yaml("stacks.yaml"),
        provisioner=provisioner,
    )
    svc._ssh = mock_ssh
    return svc


@pytest.fixture
def fastapi_analysis():
    return RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        database=DatabaseInfo(type="postgres", orm="sqlalchemy", has_migrations=True),
        services=[],
        has_dockerfile=False,
        estimated_complexity="medium",
        env_vars_needed=["DATABASE_URL", "SECRET_KEY"],
    )


@pytest.fixture
def nextjs_analysis():
    return RepoAnalysis(
        stack=StackInfo(frontend="nextjs"),
        has_dockerfile=False,
        estimated_complexity="low",
    )


@pytest.mark.asyncio
async def test_deploy_creates_server(deployment_service, mock_provider, fastapi_analysis, tmp_path, test_user_id):
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        result = await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)
    assert result.status == "deployed"
    assert result.provider == "hetzner"
    assert result.url == "http://1.2.3.4"
    assert result.ssh_access == "ssh root@1.2.3.4"
    mock_provider.create_server.assert_called_once()


@pytest.mark.asyncio
async def test_deploy_saves_state(deployment_service, state_manager, fastapi_analysis, tmp_path, test_user_id):
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        result = await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)
    state = await state_manager.get(result.deployment_id, test_user_id)
    assert state is not None
    assert state["provider"] == "hetzner"
    assert state["ip"] == "1.2.3.4"
    assert state["server_id"] == 12345


@pytest.mark.asyncio
async def test_deploy_waits_for_cloud_init(deployment_service, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)
    ssh_commands = [call.args[1] for call in mock_ssh.run.call_args_list]
    assert any("cloud-init" in cmd for cmd in ssh_commands)
    assert any("docker version" in cmd for cmd in ssh_commands)


@pytest.mark.asyncio
async def test_deploy_renders_templates(deployment_service, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)
    upload_paths = [call.args[2] for call in mock_ssh.upload_string.call_args_list]
    assert any("docker-compose.yml" in p for p in upload_paths)
    assert any("Dockerfile" in p for p in upload_paths)


@pytest.mark.asyncio
async def test_deploy_auto_generates_env_vars(deployment_service, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)
    upload_calls = mock_ssh.upload_string.call_args_list
    env_uploads = [c for c in upload_calls if ".env" in c.args[2]]
    assert len(env_uploads) > 0
    env_content = env_uploads[0].args[1]
    assert "DATABASE_URL=" in env_content
    assert "SECRET_KEY=" in env_content


@pytest.mark.asyncio
async def test_deploy_uses_user_env_vars(deployment_service, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    user_vars = {"MY_API_KEY": "abc123", "DATABASE_URL": "custom://db"}
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        await deployment_service.deploy(str(tmp_path), fastapi_analysis, env_vars=user_vars, user_id=test_user_id)
    upload_calls = mock_ssh.upload_string.call_args_list
    env_uploads = [c for c in upload_calls if ".env" in c.args[2]]
    env_content = env_uploads[0].args[1]
    assert "MY_API_KEY=abc123" in env_content
    assert "DATABASE_URL=custom://db" in env_content


@pytest.mark.asyncio
async def test_deploy_always_generates_dockerfile_even_if_repo_has_one(deployment_service, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    """ComputeEdge always owns the infra layer — generate our Dockerfile even if repo has one."""
    fastapi_analysis.has_dockerfile = True
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)
    upload_paths = [call.args[2] for call in mock_ssh.upload_string.call_args_list]
    assert any(p.endswith("/Dockerfile") for p in upload_paths)


@pytest.mark.asyncio
async def test_deploy_cleanup_on_failure(deployment_service, mock_provider, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    """On first failure, server is kept alive for retry (not deleted immediately)."""
    mock_ssh.run = AsyncMock(side_effect=Exception("SSH command failed"))
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        with pytest.raises(DeploymentError) as exc_info:
            await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)
    # Server is kept alive for retry — not deleted on first failure
    mock_provider.delete_server.assert_not_called()
    # Diagnostics should include retry_context so agent can retry
    assert exc_info.value.diagnostics is not None
    assert exc_info.value.diagnostics.retry_context is not None
    assert exc_info.value.diagnostics.retry_context.server_id == 12345


@pytest.mark.asyncio
async def test_deploy_returns_next_steps(deployment_service, fastapi_analysis, tmp_path, test_user_id):
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        result = await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)
    assert len(result.next_steps) > 0
    assert any("DNS" in step or "domain" in step.lower() for step in result.next_steps)


@pytest.fixture
def fastapi_with_compose_analysis():
    return RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        database=DatabaseInfo(type="postgres", orm="sqlalchemy", has_migrations=True),
        services=["redis"],
        has_dockerfile=True,
        has_docker_compose=True,
        estimated_complexity="medium",
        env_vars_needed=["DATABASE_URL", "SECRET_KEY"],
    )


@pytest.mark.asyncio
async def test_deploy_always_generates_own_infra_even_when_repo_has_compose(
    deployment_service, mock_ssh, fastapi_with_compose_analysis, tmp_path, test_user_id
):
    """ComputeEdge always owns the infra layer — generate our own configs even if repo has docker-compose."""
    with patch(
        "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
        return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
    ):
        result = await deployment_service.deploy(
            str(tmp_path), fastapi_with_compose_analysis, user_id=test_user_id
        )

    assert result.status == "deployed"

    # We always generate our own infra files
    upload_calls = mock_ssh.upload_string.call_args_list
    upload_paths = [c.args[2] for c in upload_calls]
    assert any(p.endswith("/docker-compose.yml") for p in upload_paths)
    assert any(p.endswith("/nginx.conf") for p in upload_paths)
    assert any(p.endswith("/.env") for p in upload_paths)
    assert any(p.endswith("/Dockerfile") for p in upload_paths)


@pytest.mark.asyncio
async def test_deploy_always_runs_compose_from_app_dir(
    deployment_service, mock_ssh, fastapi_with_compose_analysis, tmp_path, test_user_id
):
    """ComputeEdge always runs compose from app_dir, never the repo's subdir."""
    commands_run = []

    async def ssh_run_side_effect(conn, cmd):
        commands_run.append(cmd)
        return ""
    mock_ssh.run = AsyncMock(side_effect=ssh_run_side_effect)

    with patch(
        "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
        return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
    ):
        await deployment_service.deploy(str(tmp_path), fastapi_with_compose_analysis, user_id=test_user_id)

    compose_cmds = [c for c in commands_run if "docker compose" in c and "up" in c]
    assert len(compose_cmds) == 1
    # Always runs from /root/ce-hetzner-xxx, never from repo subdirs
    assert compose_cmds[0].startswith("cd /root/ce-hetzner-")


@pytest.mark.asyncio
async def test_deploy_with_docker_configs_skips_generation(deployment_service, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    """When docker_configs is provided, skip template generation and upload configs as-is."""
    configs = {
        "docker-compose.yml": "services:\n  app:\n    build: ./repo\n    ports:\n      - '8000:8000'",
        "nginx.conf": "worker_processes auto;",
        "backend/Dockerfile": "FROM python:3.11-slim\nCMD ['uvicorn']",
    }
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key",
               return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        result = await deployment_service.deploy(str(tmp_path), fastapi_analysis, docker_configs=configs, user_id=test_user_id)

    assert result.status == "deployed"

    upload_calls = mock_ssh.upload_string.call_args_list
    upload_paths = [c.args[2] for c in upload_calls]

    # docker-compose.yml and nginx.conf go to app_dir/
    assert any(p.endswith("/docker-compose.yml") for p in upload_paths)
    assert any(p.endswith("/nginx.conf") for p in upload_paths)

    # Dockerfile goes to app_dir/repo/backend/Dockerfile
    assert any("repo/backend/Dockerfile" in p for p in upload_paths)

    # Verify the content matches what was passed
    compose_upload = [c for c in upload_calls if "docker-compose.yml" in c.args[2]][0]
    assert "build: ./repo" in compose_upload.args[1]


@pytest.mark.asyncio
async def test_deploy_without_docker_configs_uses_templates(deployment_service, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    """When docker_configs is None, use the existing template-based generation."""
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key",
               return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        result = await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)

    assert result.status == "deployed"
    upload_calls = mock_ssh.upload_string.call_args_list
    upload_paths = [c.args[2] for c in upload_calls]
    assert any("docker-compose.yml" in p for p in upload_paths)
    assert any("Dockerfile" in p for p in upload_paths)


@pytest.mark.asyncio
async def test_deploy_with_docker_configs_runs_compose_from_app_dir(deployment_service, mock_ssh, fastapi_analysis, tmp_path, test_user_id):
    """When docker_configs is provided, docker compose runs from app_dir (not repo dir)."""
    commands_run = []
    async def ssh_run_side_effect(conn, cmd):
        commands_run.append(cmd)
        return ""
    mock_ssh.run = AsyncMock(side_effect=ssh_run_side_effect)

    configs = {
        "docker-compose.yml": "services:\n  app:\n    build: ./repo",
    }
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key",
               return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        await deployment_service.deploy(str(tmp_path), fastapi_analysis, docker_configs=configs, user_id=test_user_id)

    compose_cmds = [c for c in commands_run if "docker compose" in c and "up" in c]
    assert len(compose_cmds) == 1
    assert "/repo" not in compose_cmds[0].split("&&")[0]


@pytest.mark.asyncio
async def test_deploy_frontend_only(deployment_service, mock_ssh, nextjs_analysis, tmp_path, test_user_id):
    with patch("computeedge.services.deployment.DeploymentService._resolve_ssh_key", return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA")):
        result = await deployment_service.deploy(str(tmp_path), nextjs_analysis, user_id=test_user_id)
    assert result.status == "deployed"
    upload_calls = mock_ssh.upload_string.call_args_list
    env_uploads = [c for c in upload_calls if ".env" in c.args[2]]
    if env_uploads:
        env_content = env_uploads[0].args[1]
        assert "DATABASE_URL" not in env_content


@pytest.mark.asyncio
async def test_deploy_no_unbound_error_when_ssh_fails_before_connect(
    deployment_service, mock_provider, mock_ssh, fastapi_analysis, tmp_path, test_user_id
):
    """If SSH connect fails, cleanup should not raise UnboundLocalError for conn."""
    mock_ssh.connect = AsyncMock(side_effect=Exception("SSH refused"))

    with patch(
        "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
        return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
    ), patch("asyncio.sleep", new_callable=AsyncMock):  # Skip retry delays
        with pytest.raises(DeploymentError, match="Deployment failed"):
            await deployment_service.deploy(str(tmp_path), fastapi_analysis, user_id=test_user_id)

    # Server is kept alive for retry (not deleted on first failure)
    mock_provider.delete_server.assert_not_called()


def test_template_context_includes_python_version(deployment_service, tmp_path):
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        python_version="3.12",
    )
    context = deployment_service._build_template_context("fastapi", analysis, tmp_path)
    assert context["python_version"] == "3.12"


def test_generate_dockerfiles_unified_fullstack(deployment_service, tmp_path):
    """Fullstack app with different paths produces a single unified Dockerfile."""
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(
        stack=StackInfo(
            frontend="nextjs", backend="fastapi", backend_language="python",
            deploy_topology="single",
            backend_path="api", frontend_path="web",
        ),
    )
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    dockerfiles = deployment_service._generate_dockerfiles(analysis, tmp_path)
    assert "Dockerfile" in dockerfiles, f"Expected single 'Dockerfile' key, got: {list(dockerfiles.keys())}"
    assert len(dockerfiles) == 1
    # Unified template should include both frontend build stage and backend runtime
    content = dockerfiles["Dockerfile"]
    assert "frontend-builder" in content
    assert "python" in content.lower()

def test_template_context_includes_system_packages(deployment_service, tmp_path):
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        system_packages=["gcc", "pkg-config", "libpq-dev"],
    )
    context = deployment_service._build_template_context("fastapi", analysis, tmp_path)
    assert context["system_packages"] == ["gcc", "pkg-config", "libpq-dev"]

def test_template_context_includes_node_version(deployment_service, tmp_path):
    from computeedge.models import RepoAnalysis, StackInfo
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "4"}}')
    analysis = RepoAnalysis(
        stack=StackInfo(frontend="nextjs"),
        node_version="18",
    )
    context = deployment_service._build_template_context("nextjs", analysis, tmp_path)
    assert context["node_version"] == "18"

def test_fastapi_template_renders_with_system_packages(deployment_service, tmp_path):
    from computeedge.models import RepoAnalysis, StackInfo
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        system_packages=["gcc", "pkg-config", "libpq-dev"],
        python_version="3.12",
    )
    context = deployment_service._build_template_context("fastapi", analysis, tmp_path)
    context["analysis"] = analysis
    template = deployment_service._jinja.get_template("Dockerfile.fastapi.j2")
    rendered = template.render(**context)
    assert "python:3.12" in rendered
    assert "libpq-dev" in rendered
    assert "apt-get" in rendered


def test_render_only_service_creation(state_manager):
    """DeploymentService should be constructable without a provider for render-only usage."""
    svc = DeploymentService(
        provider=None,
        state=state_manager,
        stacks_config=load_bundled_yaml("stacks.yaml"),
    )
    assert svc is not None


def test_render_configs_backend_only(deployment_service, fastapi_analysis, tmp_path):
    """render_configs returns Dockerfile, docker-compose, nginx, .env for backend_only."""
    configs = deployment_service.render_configs(fastapi_analysis, str(tmp_path))
    assert "Dockerfile" in configs
    assert "docker-compose.yml" in configs
    assert "nginx.conf" in configs
    assert ".env" in configs
    assert "FROM python" in configs["Dockerfile"]
    assert "SECRET_KEY=" in configs[".env"]


def test_render_configs_frontend_only(deployment_service, nextjs_analysis, tmp_path):
    """render_configs returns Dockerfile for frontend_only topology."""
    configs = deployment_service.render_configs(nextjs_analysis, str(tmp_path))
    assert "Dockerfile" in configs
    assert "docker-compose.yml" in configs
    assert "nginx.conf" in configs
    assert ".env" in configs
    assert "node" in configs["Dockerfile"].lower() or "FROM" in configs["Dockerfile"]


def test_render_configs_with_database(deployment_service, fastapi_analysis, tmp_path):
    """render_configs includes DATABASE_URL in .env when database is detected."""
    configs = deployment_service.render_configs(fastapi_analysis, str(tmp_path))
    assert "DATABASE_URL=" in configs[".env"]
    assert "SECRET_KEY=" in configs[".env"]
    assert "DATABASE_URL" in configs["docker-compose.yml"] or "db:" in configs["docker-compose.yml"]


def test_render_configs_with_redis(deployment_service, tmp_path):
    """render_configs includes REDIS_URL when redis service is detected."""
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        services=["redis"],
    )
    configs = deployment_service.render_configs(analysis, str(tmp_path))
    assert "REDIS_URL=" in configs[".env"]


def test_render_configs_with_env_vars(deployment_service, fastapi_analysis, tmp_path):
    """User-provided env_vars are merged into .env."""
    configs = deployment_service.render_configs(
        fastapi_analysis, str(tmp_path), env_vars={"MY_VAR": "my_value"}
    )
    assert "MY_VAR=my_value" in configs[".env"]


def test_render_configs_secret_stability(deployment_service, fastapi_analysis, tmp_path):
    """Secrets in .env should match those referenced in docker-compose.yml."""
    import re
    configs = deployment_service.render_configs(fastapi_analysis, str(tmp_path))
    env_lines = dict(line.split("=", 1) for line in configs[".env"].split("\n") if "=" in line)
    assert "SECRET_KEY" in env_lines
    assert len(env_lines["SECRET_KEY"]) == 64  # token_hex(32) produces 64 chars
    if "DATABASE_URL" in env_lines:
        match = re.search(r"computeedge:(\w+)@", env_lines["DATABASE_URL"])
        assert match, "DATABASE_URL should contain password"
        db_password = match.group(1)
        assert db_password in configs["docker-compose.yml"]


def test_render_configs_single_topology(deployment_service, tmp_path):
    """Single topology produces one Dockerfile from fullstack-node template."""
    from computeedge.models import RepoAnalysis, StackInfo
    (tmp_path / "package.json").write_text('{"scripts":{"build:client":"","build:server":"","start":"node server.js"}}')
    analysis = RepoAnalysis(
        stack=StackInfo(
            frontend="react", backend="express", backend_language="javascript",
            deploy_topology="single",
            server_entry="dist/server.js",
        ),
    )
    configs = deployment_service.render_configs(analysis, str(tmp_path))
    assert "Dockerfile" in configs
    assert "Dockerfile.frontend" not in configs
    assert not any(k.endswith("/Dockerfile") for k in configs if k != "Dockerfile")


def test_render_configs_includes_config_notes(deployment_service, fastapi_analysis, tmp_path):
    """render_configs includes _config_notes and _templates_used metadata."""
    configs = deployment_service.render_configs(fastapi_analysis, str(tmp_path))
    assert "_config_notes" in configs
    assert "_templates_used" in configs
    assert any("fastapi" in n.lower() for n in configs["_config_notes"])
    assert any("postgres" in n.lower() for n in configs["_config_notes"])
    # Backend-only uses the fastapi template
    assert any("fastapi" in t.lower() for t in configs["_templates_used"])
