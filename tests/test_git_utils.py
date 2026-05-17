from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from computeedge.utils.git import is_git_url


def test_is_git_url_https():
    assert is_git_url("https://github.com/user/repo.git") is True


def test_is_git_url_ssh():
    assert is_git_url("git@github.com:user/repo.git") is True


def test_is_git_url_http():
    assert is_git_url("http://github.com/user/repo") is True


def test_is_git_url_local_path():
    assert is_git_url("/home/user/myproject") is False


def test_is_git_url_relative_path():
    assert is_git_url("./myproject") is False


def test_is_git_url_windows_path():
    assert is_git_url("C:\\Users\\project") is False


@pytest.mark.asyncio
async def test_clone_repo_success(tmp_path):
    """Test clone_repo with a mocked successful git clone."""
    from computeedge.utils.git import clone_repo

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch("computeedge.utils.git.asyncio.create_subprocess_exec",
               new_callable=AsyncMock, return_value=mock_proc):
        result = await clone_repo("https://github.com/user/repo.git", tmp_path)
        assert result == tmp_path


@pytest.mark.asyncio
async def test_clone_repo_failure():
    """Test clone_repo raises GitCloneError on failure."""
    from computeedge.utils.git import clone_repo
    from computeedge.exceptions import GitCloneError

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(
        return_value=(b"", b"fatal: repository not found")
    )
    mock_proc.returncode = 128

    with patch("computeedge.utils.git.asyncio.create_subprocess_exec",
               new_callable=AsyncMock, return_value=mock_proc):
        with pytest.raises(GitCloneError, match="repository not found"):
            await clone_repo("https://github.com/user/nonexistent.git")
