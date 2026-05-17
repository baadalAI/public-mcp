import asyncio
import shutil
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.services.deployment import DeploymentService


@pytest.fixture
def deployment_service(state_manager):
    provider = AsyncMock()
    svc = DeploymentService(provider=provider, state=state_manager, stacks_config=load_bundled_yaml("stacks.yaml"))
    svc._ssh = AsyncMock()
    svc._ssh.run = AsyncMock(return_value="")
    svc._ssh.upload = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_upload_repo_includes_uncommitted_files(deployment_service, tmp_path):
    """For git repos, all working-tree files are included, not just committed ones."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "app.py").write_text("print('hello')")
    (repo / ".gitignore").write_text("node_modules\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "bigfile.js").write_text("x" * 10000)

    proc = await asyncio.create_subprocess_exec("git", "init", cwd=str(repo))
    await proc.wait()
    proc = await asyncio.create_subprocess_exec("git", "add", "app.py", ".gitignore", cwd=str(repo))
    await proc.wait()
    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", "init",
        cwd=str(repo),
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "HOME": str(tmp_path)},
    )
    await proc.wait()

    # Add an uncommitted file — this MUST be included in the deploy
    (repo / "new_config.json").write_text('{"key": "value"}')

    # Capture the tar before _upload_repo deletes it
    saved_tar = tmp_path / "saved.tar.gz"

    original_upload = deployment_service._ssh.upload

    async def save_and_upload(conn, local_path, remote_path):
        shutil.copy2(local_path, str(saved_tar))
        return await original_upload(conn, local_path, remote_path)

    deployment_service._ssh.upload = AsyncMock(side_effect=save_and_upload)

    conn = AsyncMock()
    await deployment_service._upload_repo(conn, str(repo), "/root/test-deploy")

    deployment_service._ssh.upload.assert_called_once()

    with tarfile.open(str(saved_tar), "r:gz") as tar:
        names = tar.getnames()
        # .gitignore patterns are respected — node_modules excluded
        assert not any("node_modules" in n for n in names)
        # Committed files included
        assert any("app.py" in n for n in names)
        # Uncommitted files also included
        assert any("new_config.json" in n for n in names)
        # .git directory excluded
        assert not any(n == ".git" or n.startswith(".git/") for n in names)


@pytest.mark.asyncio
async def test_upload_repo_excludes_bloat_for_non_git(deployment_service, tmp_path):
    """For non-git dirs, tar with exclusion list."""
    repo = tmp_path / "localapp"
    repo.mkdir()
    (repo / "app.py").write_text("print('hello')")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "bigfile.js").write_text("x" * 10000)
    (repo / ".venv").mkdir()
    (repo / ".venv" / "lib.py").write_text("x")

    # Capture the tar before _upload_repo deletes it
    saved_tar = tmp_path / "saved.tar.gz"

    original_upload = deployment_service._ssh.upload

    async def save_and_upload(conn, local_path, remote_path):
        shutil.copy2(local_path, str(saved_tar))
        return await original_upload(conn, local_path, remote_path)

    deployment_service._ssh.upload = AsyncMock(side_effect=save_and_upload)

    conn = AsyncMock()
    await deployment_service._upload_repo(conn, str(repo), "/root/test-deploy")

    deployment_service._ssh.upload.assert_called_once()

    with tarfile.open(str(saved_tar), "r:gz") as tar:
        names = tar.getnames()
        assert not any("node_modules" in n for n in names)
        assert not any(".venv" in n for n in names)
        assert any("app.py" in n for n in names)
