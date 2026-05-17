from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from computeedge.exceptions import SSHError
from computeedge.utils.ssh import SSHClient


@pytest.fixture
def ssh_client():
    return SSHClient()


@pytest.mark.asyncio
async def test_connect_success(ssh_client):
    mock_conn = AsyncMock()
    with patch("asyncssh.connect", new_callable=AsyncMock, return_value=mock_conn) as mock_connect:
        conn = await ssh_client.connect("1.2.3.4", "/path/to/key")
        assert conn == mock_conn
        mock_connect.assert_called_once_with(
            "1.2.3.4",
            username="root",
            client_keys=["/path/to/key"],
            known_hosts=None,
        )


@pytest.mark.asyncio
async def test_connect_failure(ssh_client):
    with patch("asyncssh.connect", new_callable=AsyncMock, side_effect=OSError("Connection refused")):
        with pytest.raises(SSHError, match="Connection refused"):
            await ssh_client.connect("1.2.3.4", "/path/to/key")


@pytest.mark.asyncio
async def test_run_success(ssh_client):
    mock_result = MagicMock()
    mock_result.exit_status = 0
    mock_result.stdout = "hello world\n"
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)

    output = await ssh_client.run(mock_conn, "echo hello world")
    assert output == "hello world\n"
    mock_conn.run.assert_called_once_with("echo hello world", check=False)


@pytest.mark.asyncio
async def test_run_nonzero_exit(ssh_client):
    mock_result = MagicMock()
    mock_result.exit_status = 1
    mock_result.stderr = "command not found"
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=mock_result)

    with pytest.raises(SSHError, match="command not found"):
        await ssh_client.run(mock_conn, "bad_command")


@pytest.mark.asyncio
async def test_upload_string(ssh_client):
    mock_sftp = AsyncMock()
    mock_sftp.open = AsyncMock()
    mock_conn = AsyncMock()
    mock_conn.start_sftp_client = AsyncMock(return_value=mock_sftp)

    mock_file = AsyncMock()
    mock_file.write = AsyncMock()
    mock_file.__aenter__ = AsyncMock(return_value=mock_file)
    mock_file.__aexit__ = AsyncMock(return_value=False)
    mock_sftp.open.return_value = mock_file

    await ssh_client.upload_string(mock_conn, "file content", "/remote/path.txt")
    mock_sftp.open.assert_called_once_with("/remote/path.txt", "w")
    mock_file.write.assert_called_once_with("file content")
