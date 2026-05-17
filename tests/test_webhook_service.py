from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from computeedge.exceptions import WebhookError
from computeedge.services.webhook import WebhookService


@pytest.fixture
def mock_ssh():
    ssh = AsyncMock()
    mock_conn = AsyncMock()
    ssh.connect = AsyncMock(return_value=mock_conn)
    ssh.run = AsyncMock(return_value="")
    ssh.upload_string = AsyncMock()
    return ssh


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get_provider_token = MagicMock(return_value="ghp_test_token_123")
    return config


@pytest.fixture
def webhook_service(state_manager, mock_config, mock_ssh):
    svc = WebhookService(state=state_manager, config=mock_config)
    svc._ssh = mock_ssh
    return svc


@pytest.fixture
async def sample_deployment(state_manager, test_user_id):
    """Add a sample GitHub-deployed app to state."""
    dep_id = "ce-hetzner-abc12345"
    await state_manager.add(dep_id, test_user_id, {
        "provider": "hetzner",
        "server_id": 12345,
        "ip": "1.2.3.4",
        "plan": "cx23",
        "repo_path": "https://github.com/testuser/testrepo.git",
        "normalized_repo_path": "github.com/testuser/testrepo",
        "deployed_at": "2026-03-24T12:00:00Z",
        "monthly_cost": 3.49,
        "ssh_key_path": "/tmp/test_key",
    })
    return dep_id


@pytest.fixture
async def sample_local_deployment(state_manager, test_user_id):
    """Add a sample local-path deployment to state."""
    dep_id = "ce-hetzner-local123"
    await state_manager.add(dep_id, test_user_id, {
        "provider": "hetzner",
        "server_id": 99999,
        "ip": "5.6.7.8",
        "plan": "cx23",
        "repo_path": "/Users/dev/myapp",
        "normalized_repo_path": "/Users/dev/myapp",
        "deployed_at": "2026-03-24T12:00:00Z",
        "monthly_cost": 3.49,
        "ssh_key_path": "/tmp/test_key",
    })
    return dep_id


# --- connect tests ---


@pytest.mark.asyncio
async def test_connect_creates_github_webhook(webhook_service, sample_deployment, mock_ssh, test_user_id):
    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": 777}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await webhook_service.connect(sample_deployment, user_id=test_user_id)

        assert result["status"] == "connected"
        assert result["github_repo"] == "testuser/testrepo"
        assert result["branch"] == "main"
        assert "1.2.3.4" in result["webhook_url"]

        # Verify GitHub API was called
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "testuser/testrepo" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["events"] == ["push"]
        assert payload["active"] is True


@pytest.mark.asyncio
async def test_connect_uploads_receiver_script(webhook_service, sample_deployment, mock_ssh, test_user_id):
    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": 777}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await webhook_service.connect(sample_deployment, user_id=test_user_id)

        # Check receiver script was uploaded
        upload_calls = mock_ssh.upload_string.call_args_list
        receiver_call = [c for c in upload_calls if "webhook_receiver.py" in str(c)]
        assert len(receiver_call) >= 1

        # Check the script contains correct config
        script_content = receiver_call[0][0][1]  # positional arg: content
        assert "main" in script_content  # branch
        assert sample_deployment in script_content  # deployment_id


@pytest.mark.asyncio
async def test_connect_creates_systemd_service(webhook_service, sample_deployment, mock_ssh, test_user_id):
    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": 777}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await webhook_service.connect(sample_deployment, user_id=test_user_id)

        # Check systemctl commands were run
        run_calls = [str(c) for c in mock_ssh.run.call_args_list]
        systemctl_calls = [c for c in run_calls if "systemctl" in c]
        assert len(systemctl_calls) >= 1

        # Check systemd unit was uploaded
        upload_calls = mock_ssh.upload_string.call_args_list
        unit_call = [c for c in upload_calls if ".service" in str(c)]
        assert len(unit_call) >= 1


@pytest.mark.asyncio
async def test_connect_saves_state(webhook_service, sample_deployment, state_manager, mock_ssh, test_user_id):
    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": 777}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await webhook_service.connect(sample_deployment, user_id=test_user_id)

        state = await state_manager.get(sample_deployment, test_user_id)
        webhook = state.get("webhook")
        assert webhook is not None
        assert webhook["github_hook_id"] == 777
        assert webhook["branch"] == "main"
        assert webhook["github_repo"] == "testuser/testrepo"
        assert "webhook_secret" in webhook
        assert len(webhook["webhook_secret"]) == 40  # hex(20 bytes)


@pytest.mark.asyncio
async def test_connect_rejects_local_path(webhook_service, sample_local_deployment, test_user_id):
    with pytest.raises(WebhookError, match="only available for GitHub"):
        await webhook_service.connect(sample_local_deployment, user_id=test_user_id)


@pytest.mark.asyncio
async def test_connect_rejects_existing_webhook(
    webhook_service, sample_deployment, state_manager, test_user_id,
):
    await state_manager.update(sample_deployment, test_user_id, {
        "webhook": {"github_hook_id": 999, "branch": "main"}
    })
    with pytest.raises(WebhookError, match="already connected"):
        await webhook_service.connect(sample_deployment, user_id=test_user_id)


@pytest.mark.asyncio
async def test_connect_fails_without_token(webhook_service, sample_deployment, mock_config, test_user_id):
    mock_config.get_provider_token.return_value = None
    with pytest.raises(WebhookError, match="GitHub token not configured"):
        await webhook_service.connect(sample_deployment, github_token=None, user_id=test_user_id)


@pytest.mark.asyncio
async def test_connect_rolls_back_on_ssh_failure(webhook_service, sample_deployment, mock_ssh, test_user_id):
    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        # GitHub webhook creation succeeds
        mock_resp_create = MagicMock()
        mock_resp_create.status_code = 201
        mock_resp_create.json.return_value = {"id": 888}

        # Webhook deletion succeeds
        mock_resp_delete = MagicMock()
        mock_resp_delete.status_code = 204

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp_create)
        mock_client.delete = AsyncMock(return_value=mock_resp_delete)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # SSH connect fails
        mock_ssh.connect = AsyncMock(side_effect=Exception("SSH failed"))

        with pytest.raises(Exception, match="SSH failed"):
            await webhook_service.connect(sample_deployment, user_id=test_user_id)

        # Verify rollback: webhook should be deleted
        # The service creates a new httpx client for the delete call
        assert mock_client_cls.call_count >= 2  # one for create, one for delete


@pytest.mark.asyncio
async def test_connect_not_found(webhook_service, test_user_id):
    with pytest.raises(WebhookError, match="not found"):
        await webhook_service.connect("ce-hetzner-nonexist", user_id=test_user_id)


# --- disconnect tests ---


@pytest.mark.asyncio
async def test_disconnect_deletes_webhook(
    webhook_service, sample_deployment, state_manager, mock_ssh, test_user_id,
):
    await state_manager.update(sample_deployment, test_user_id, {
        "webhook": {
            "github_hook_id": 555,
            "webhook_secret": "abc",
            "branch": "main",
            "github_repo": "testuser/testrepo",
            "webhook_url": "http://1.2.3.4:9867/webhook",
        }
    })

    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 204

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await webhook_service.disconnect(sample_deployment, user_id=test_user_id)

        assert result["status"] == "disconnected"
        mock_client.delete.assert_called_once()
        assert "555" in str(mock_client.delete.call_args)


@pytest.mark.asyncio
async def test_disconnect_stops_service(
    webhook_service, sample_deployment, state_manager, mock_ssh, test_user_id,
):
    await state_manager.update(sample_deployment, test_user_id, {
        "webhook": {
            "github_hook_id": 555,
            "webhook_secret": "abc",
            "branch": "main",
            "github_repo": "testuser/testrepo",
            "webhook_url": "http://1.2.3.4:9867/webhook",
        }
    })

    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await webhook_service.disconnect(sample_deployment, user_id=test_user_id)

        run_calls = [str(c) for c in mock_ssh.run.call_args_list]
        stop_calls = [c for c in run_calls if "systemctl stop" in c]
        assert len(stop_calls) >= 1


@pytest.mark.asyncio
async def test_disconnect_clears_state(
    webhook_service, sample_deployment, state_manager, mock_ssh, test_user_id,
):
    await state_manager.update(sample_deployment, test_user_id, {
        "webhook": {
            "github_hook_id": 555,
            "webhook_secret": "abc",
            "branch": "main",
            "github_repo": "testuser/testrepo",
            "webhook_url": "http://1.2.3.4:9867/webhook",
        }
    })

    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await webhook_service.disconnect(sample_deployment, user_id=test_user_id)

        state = await state_manager.get(sample_deployment, test_user_id)
        assert state.get("webhook") is None


@pytest.mark.asyncio
async def test_disconnect_no_webhook(webhook_service, sample_deployment, test_user_id):
    with pytest.raises(WebhookError, match="No webhook configured"):
        await webhook_service.disconnect(sample_deployment, user_id=test_user_id)


@pytest.mark.asyncio
async def test_disconnect_survives_github_api_failure(
    webhook_service, sample_deployment, state_manager, mock_ssh, test_user_id,
):
    await state_manager.update(sample_deployment, test_user_id, {
        "webhook": {
            "github_hook_id": 555,
            "webhook_secret": "abc",
            "branch": "main",
            "github_repo": "testuser/testrepo",
            "webhook_url": "http://1.2.3.4:9867/webhook",
        }
    })

    with patch("computeedge.services.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 500  # GitHub API fails

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # Should still succeed (best-effort GitHub deletion)
        result = await webhook_service.disconnect(sample_deployment, user_id=test_user_id)
        assert result["status"] == "disconnected"


# --- helper tests ---


@pytest.mark.asyncio
async def test_extract_github_repo(webhook_service, sample_deployment, state_manager, test_user_id):
    state = await state_manager.get(sample_deployment, test_user_id)
    owner, repo = webhook_service._extract_github_repo(state)
    assert owner == "testuser"
    assert repo == "testrepo"


@pytest.mark.asyncio
async def test_extract_github_repo_rejects_non_github(webhook_service, sample_local_deployment, state_manager, test_user_id):
    state = await state_manager.get(sample_local_deployment, test_user_id)
    with pytest.raises(WebhookError, match="only available for GitHub"):
        webhook_service._extract_github_repo(state)


def test_receiver_template_renders(webhook_service):
    template = webhook_service._jinja.get_template("webhook_receiver.py.j2")
    rendered = template.render(
        webhook_secret="test_secret_abc",
        branch="develop",
        deployment_id="ce-hetzner-test1234",
        webhook_port=9867,
    )
    assert "test_secret_abc" in rendered
    assert "develop" in rendered
    assert "ce-hetzner-test1234" in rendered
    assert "9867" in rendered
