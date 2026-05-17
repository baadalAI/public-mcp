import asyncio
import tempfile
from pathlib import Path

from computeedge.exceptions import GitCloneError
from computeedge.utils.logger import get_logger

logger = get_logger("git")


def is_git_url(path: str) -> bool:
    """Check if a string looks like a git URL vs a local path."""
    return path.startswith(("https://", "http://", "git@", "ssh://"))


async def clone_repo(url: str, target_dir: Path | None = None) -> Path:
    """Clone a git repo via shallow clone. Returns the path to cloned repo.

    Caller is responsible for cleaning up the target directory.
    """
    if target_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="computeedge_"))

    logger.info("Cloning %s into %s", url, target_dir)
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, str(target_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("Failed to clone %s: %s", url, stderr.decode().strip())
        raise GitCloneError(f"Failed to clone {url}: {stderr.decode().strip()}")

    return target_dir
