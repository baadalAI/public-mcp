from __future__ import annotations

from typing import TYPE_CHECKING, Any

from computeedge.exceptions import SSHError
from computeedge.utils.logger import get_logger

if TYPE_CHECKING:
    import asyncssh

logger = get_logger("ssh")


def _import_asyncssh():
    """Lazy-import asyncssh. It hangs on Python 3.14 if loaded at module level."""
    import asyncssh as _asyncssh
    return _asyncssh


class SSHClient:
    """Thin asyncssh wrapper for SSH operations."""

    async def connect(
        self, host: str, private_key_path: str, username: str = "root"
    ) -> Any:
        """Open SSH connection. Raises SSHError on failure."""
        _asyncssh = _import_asyncssh()
        try:
            return await _asyncssh.connect(
                host,
                username=username,
                client_keys=[private_key_path],
                known_hosts=None,
            )
        except (OSError, _asyncssh.Error) as e:
            logger.error("SSH connection to %s failed: %s", host, e)
            raise SSHError(f"SSH connection to {host} failed: {e}") from e

    async def run(self, conn: Any, command: str) -> str:
        """Run command, return stdout. Raises SSHError on non-zero exit."""
        result = await conn.run(command, check=False)
        if result.exit_status != 0:
            logger.error("SSH command failed (exit %s): %s", result.exit_status, result.stderr)
            raise SSHError(
                f"Command failed (exit {result.exit_status}): {result.stderr}"
            )
        return result.stdout

    async def upload(self, conn: Any, local_path: str, remote_path: str) -> None:
        """Upload a local file to remote path via SFTP."""
        _asyncssh = _import_asyncssh()
        try:
            sftp = await conn.start_sftp_client()
            await sftp.put(local_path, remote_path)
        except (OSError, _asyncssh.Error) as e:
            logger.error("Failed to upload %s to %s: %s", local_path, remote_path, e)
            raise SSHError(f"Failed to upload {local_path} to {remote_path}: {e}") from e

    async def upload_string(
        self, conn: Any, content: str, remote_path: str
    ) -> None:
        """Write string content to remote file via SFTP."""
        _asyncssh = _import_asyncssh()
        try:
            sftp = await conn.start_sftp_client()
            # Ensure parent directory exists — SFTP open("w") won't create it
            parent = remote_path.rsplit("/", 1)[0]
            if parent:
                await sftp.makedirs(parent, exist_ok=True)
            f = await sftp.open(remote_path, "w")
            async with f:
                await f.write(content)
        except (OSError, _asyncssh.Error) as e:
            logger.error("Failed to upload string to %s: %s", remote_path, e)
            raise SSHError(f"Failed to upload to {remote_path}: {e}") from e
