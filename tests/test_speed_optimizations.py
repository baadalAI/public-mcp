import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from computeedge.services.deployment import DeploymentService
from computeedge.config.loader import load_bundled_yaml

@pytest.fixture
def deployment_service(state_manager):
    stacks = load_bundled_yaml("stacks.yaml")
    service = DeploymentService(
        provider=MagicMock(),
        state=state_manager,
        stacks_config=stacks,
    )
    service._ssh = MagicMock()
    return service

@pytest.mark.asyncio
async def test_ssh_connect_retry_uses_5s_delays(deployment_service):
    """SSH connect should use 5s initial delay and 5s retry intervals."""
    connect_mock = AsyncMock(side_effect=Exception("Connection refused"))
    deployment_service._ssh.connect = connect_mock

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(Exception):
            await deployment_service._ssh_connect_with_retry("1.2.3.4", "/tmp/key")

    assert mock_sleep.call_args_list[0][0][0] == 5, "Expected 5s initial delay"
    for call in mock_sleep.call_args_list:
        assert call[0][0] == 5, f"Expected 5s delay, got {call[0][0]}s"


@pytest.mark.asyncio
async def test_cloud_init_and_repo_upload_run_in_parallel(deployment_service, tmp_path):
    """Cloud-init wait and repo upload should be dispatched concurrently via asyncio.gather."""
    import asyncio

    async def mock_run(conn, cmd):
        return ""

    async def mock_upload_repo(conn, repo_path, app_dir):
        pass

    deployment_service._ssh.run = mock_run
    deployment_service._upload_repo = mock_upload_repo

    conn = MagicMock()
    with patch("asyncio.gather", wraps=asyncio.gather) as mock_gather:
        await deployment_service._wait_for_cloud_init_and_upload_repo(
            conn, str(tmp_path), "/root/test-deploy"
        )
        assert mock_gather.called


@pytest.mark.asyncio
async def test_hetzner_polling_uses_tighter_interval():
    """Initial polling should use 3s interval for first 30 seconds."""
    from computeedge.providers.hetzner import HetznerProvider

    provider = HetznerProvider.__new__(HetznerProvider)
    call_count = 0

    async def mock_get_server(server_id):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            return {"status": "running", "ip": "1.2.3.4"}
        return {"status": "initializing"}

    provider.get_server = mock_get_server

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await provider.wait_for_ready(123)
        if mock_sleep.call_args_list:
            first_interval = mock_sleep.call_args_list[0][0][0]
            assert first_interval == 3.0, f"Expected 3s initial interval, got {first_interval}"
