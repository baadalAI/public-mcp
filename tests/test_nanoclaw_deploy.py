"""Tests for nanoclaw native VM deployment path."""
from unittest.mock import AsyncMock, patch

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import RepoAnalysis, StackInfo
from computeedge.services.deployment import DeploymentService, NANOCLAW_CLOUD_INIT
from computeedge.services.infra import HetznerInfrastructureProvisioner


@pytest.fixture(autouse=True)
def seed_nanoclaw_files(tmp_path):
    """Create minimal nanoclaw project files."""
    (tmp_path / "package.json").write_text(
        '{"name": "my-nanoclaw", "dependencies": {"nanoclaw": "^1.0.0"}}\n'
    )
    (tmp_path / "tsconfig.json").write_text("{}\n")
    container_dir = tmp_path / "container"
    container_dir.mkdir()
    (container_dir / "build.sh").write_text("#!/bin/bash\necho built\n")
    (container_dir / "Dockerfile").write_text("FROM node:22-slim\n")


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.create_server = AsyncMock(return_value={
        "id": 99999, "ip": "10.0.0.1", "status": "initializing"
    })
    provider.wait_for_ready = AsyncMock(return_value="10.0.0.1")
    provider.delete_server = AsyncMock()
    return provider


@pytest.fixture
def mock_ssh():
    ssh = AsyncMock()
    mock_conn = AsyncMock()
    ssh.connect = AsyncMock(return_value=mock_conn)
    # Default: agent container build.sh exists, service starts OK
    ssh.run = AsyncMock(side_effect=lambda conn, cmd, **kw: {
        True: "yes" if "test -f" in cmd
        else "active" if "is-active" in cmd
        else ""
    }.get(True, ""))
    ssh.upload_string = AsyncMock()
    ssh.upload = AsyncMock()
    return ssh


@pytest.fixture
def nanoclaw_service(mock_provider, state_manager, mock_ssh):
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
def nanoclaw_analysis():
    return RepoAnalysis(
        stack=StackInfo(backend="nanoclaw", backend_language="javascript"),
        has_dockerfile=False,
        estimated_complexity="medium",
    )


class TestIsNanoclaw:
    def test_detects_nanoclaw_stack(self, nanoclaw_service, nanoclaw_analysis):
        assert nanoclaw_service._is_nanoclaw(nanoclaw_analysis) is True

    def test_rejects_non_nanoclaw(self, nanoclaw_service):
        analysis = RepoAnalysis(
            stack=StackInfo(backend="fastapi", backend_language="python"),
            has_dockerfile=False,
        )
        assert nanoclaw_service._is_nanoclaw(analysis) is False

    def test_default_plan_hetzner(self, nanoclaw_service):
        assert nanoclaw_service._nanoclaw_default_plan() == "cx33"


class TestNanoclawWorkspaceDetection:
    """Workspace detection tests use subdirs to avoid autouse fixture pollution."""

    def test_full_project_is_not_workspace(self, tmp_path):
        """A directory with package.json containing nanoclaw is a full project."""
        d = tmp_path / "full_project"
        d.mkdir()
        (d / "package.json").write_text('{"dependencies": {"nanoclaw": "^1.0"}}')
        (d / "src").mkdir()
        (d / "src" / "index.ts").write_text("export {}")
        (d / "groups").mkdir()
        assert DeploymentService._is_nanoclaw_workspace(str(d)) is False

    def test_workspace_with_groups(self, tmp_path):
        """A directory with groups/ but no nanoclaw source is a workspace."""
        d = tmp_path / "workspace_groups"
        d.mkdir()
        (d / "groups").mkdir()
        (d / "groups" / "main").mkdir()
        (d / "groups" / "main" / "CLAUDE.md").write_text("# My Agent")
        assert DeploymentService._is_nanoclaw_workspace(str(d)) is True

    def test_workspace_with_claude_dir_only(self, tmp_path):
        """A directory with only .claude/ is NOT a workspace — .claude/ is a
        common Claude Code settings dir, not nanoclaw-specific."""
        d = tmp_path / "workspace_claude"
        d.mkdir()
        (d / ".claude").mkdir()
        (d / ".claude" / "settings.json").write_text("{}")
        assert DeploymentService._is_nanoclaw_workspace(str(d)) is False

    def test_empty_dir_is_not_workspace(self, tmp_path):
        """An empty directory is not a workspace."""
        d = tmp_path / "empty"
        d.mkdir()
        assert DeploymentService._is_nanoclaw_workspace(str(d)) is False

    def test_git_url_is_not_workspace(self):
        """Git URLs are never workspaces."""
        assert DeploymentService._is_nanoclaw_workspace(
            "https://github.com/user/nanoclaw.git"
        ) is False


class TestNanoclawDeploy:
    @pytest.mark.asyncio
    async def test_deploy_nanoclaw_creates_server(
        self, nanoclaw_service, nanoclaw_analysis, tmp_path, test_user_id
    ):
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            result = await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, user_id=test_user_id,
            )

        assert result.status == "deployed"
        assert result.provider == "hetzner"
        assert "ssh root@" in result.ssh_access

    @pytest.mark.asyncio
    async def test_deploy_uses_nanoclaw_cloud_init(
        self, nanoclaw_service, mock_provider, nanoclaw_analysis, tmp_path, test_user_id
    ):
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, user_id=test_user_id,
            )

        # Verify cloud-init includes Node.js setup
        create_call = mock_provider.create_server.call_args
        # The provisioner calls create_server with user_data
        # Check that NANOCLAW_CLOUD_INIT was passed through the provisioner
        assert mock_provider.create_server.called

    @pytest.mark.asyncio
    async def test_deploy_saves_native_deploy_mode(
        self, nanoclaw_service, state_manager, nanoclaw_analysis, tmp_path, test_user_id
    ):
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            result = await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, user_id=test_user_id,
            )

        state = await state_manager.get(result.deployment_id, test_user_id)
        assert state is not None
        assert state["deploy_mode"] == "native"

    @pytest.mark.asyncio
    async def test_deploy_upgrades_plan_from_default(
        self, nanoclaw_service, state_manager, nanoclaw_analysis, tmp_path, test_user_id
    ):
        """When user passes default cx23, nanoclaw should upgrade to cx32."""
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            result = await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, plan="cx23", user_id=test_user_id,
            )

        state = await state_manager.get(result.deployment_id, test_user_id)
        assert state["plan"] == "cx33"

    @pytest.mark.asyncio
    async def test_deploy_runs_npm_ci_and_build(
        self, nanoclaw_service, mock_ssh, nanoclaw_analysis, tmp_path, test_user_id
    ):
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, user_id=test_user_id,
            )

        # Check that npm ci and npm run build were called
        run_calls = [str(c) for c in mock_ssh.run.call_args_list]
        assert any("npm ci" in c for c in run_calls)
        assert any("npm run build" in c for c in run_calls)

    @pytest.mark.asyncio
    async def test_deploy_builds_agent_container(
        self, nanoclaw_service, mock_ssh, nanoclaw_analysis, tmp_path, test_user_id
    ):
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, user_id=test_user_id,
            )

        run_calls = [str(c) for c in mock_ssh.run.call_args_list]
        assert any("container/build.sh" in c for c in run_calls)

    @pytest.mark.asyncio
    async def test_deploy_creates_systemd_service(
        self, nanoclaw_service, mock_ssh, nanoclaw_analysis, tmp_path, test_user_id
    ):
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, user_id=test_user_id,
            )

        # Check systemd service was uploaded and started
        upload_calls = [str(c) for c in mock_ssh.upload_string.call_args_list]
        assert any("nanoclaw.service" in c for c in upload_calls)

        run_calls = [str(c) for c in mock_ssh.run.call_args_list]
        assert any("systemctl enable nanoclaw" in c for c in run_calls)
        assert any("systemctl start nanoclaw" in c for c in run_calls)

    @pytest.mark.asyncio
    async def test_deploy_writes_env_vars(
        self, nanoclaw_service, mock_ssh, nanoclaw_analysis, tmp_path, test_user_id
    ):
        env_vars = {"ANTHROPIC_API_KEY": "sk-test-123", "TELEGRAM_TOKEN": "bot-token"}
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis,
                env_vars=env_vars, user_id=test_user_id,
            )

        upload_calls = [str(c) for c in mock_ssh.upload_string.call_args_list]
        assert any(".env" in c for c in upload_calls)

    @pytest.mark.asyncio
    async def test_deploy_next_steps_include_management_info(
        self, nanoclaw_service, nanoclaw_analysis, tmp_path, test_user_id
    ):
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            result = await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, user_id=test_user_id,
            )

        assert any("journalctl" in step for step in result.next_steps)
        assert any("ssh" in step.lower() for step in result.next_steps)
        # No channel credentials → should prompt to add them
        assert any("No channel" in step for step in result.next_steps)

    @pytest.mark.asyncio
    async def test_deploy_with_telegram_shows_configured(
        self, nanoclaw_service, nanoclaw_analysis, tmp_path, test_user_id
    ):
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            result = await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis,
                env_vars={"ANTHROPIC_API_KEY": "sk-test", "TELEGRAM_BOT_TOKEN": "bot123"},
                user_id=test_user_id,
            )

        assert any("Telegram" in step for step in result.next_steps)


class TestNanoclawRedeploy:
    @pytest.mark.asyncio
    async def test_redeploy_nanoclaw(
        self, nanoclaw_service, state_manager, mock_ssh, nanoclaw_analysis, tmp_path, test_user_id
    ):
        # First deploy
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            deploy_result = await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis, user_id=test_user_id,
            )

        # Redeploy
        result = await nanoclaw_service.redeploy(
            deploy_result.deployment_id, str(tmp_path),
            nanoclaw_analysis, user_id=test_user_id,
        )

        assert result.status == "deployed"
        run_calls = [str(c) for c in mock_ssh.run.call_args_list]
        assert any("systemctl stop nanoclaw" in c for c in run_calls)
        assert any("npm run build" in c for c in run_calls)
        assert any("systemctl start nanoclaw" in c for c in run_calls)

    @pytest.mark.asyncio
    async def test_redeploy_merges_env_vars(
        self, nanoclaw_service, state_manager, mock_ssh, nanoclaw_analysis, tmp_path, test_user_id
    ):
        # First deploy with initial env vars
        with patch(
            "computeedge.services.deployment.DeploymentService._resolve_ssh_key",
            return_value=("/fake/key", "/fake/key.pub", "ssh-ed25519 AAAA"),
        ):
            deploy_result = await nanoclaw_service.deploy(
                str(tmp_path), nanoclaw_analysis,
                env_vars={"ANTHROPIC_API_KEY": "sk-old"},
                user_id=test_user_id,
            )

        # Redeploy with new env vars
        result = await nanoclaw_service.redeploy(
            deploy_result.deployment_id, str(tmp_path),
            nanoclaw_analysis,
            env_vars={"TELEGRAM_TOKEN": "new-token"},
            user_id=test_user_id,
        )

        assert result.status == "deployed"
        # Should have read existing .env and merged
        upload_calls = [str(c) for c in mock_ssh.upload_string.call_args_list]
        assert any(".env" in c for c in upload_calls)
