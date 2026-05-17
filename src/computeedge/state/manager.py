import os
import re

from computeedge.state.database import Database
from computeedge.utils.logger import get_logger

logger = get_logger("state")


class StateManager:
    """Manages deployment state in SQLite, scoped by user_id."""

    def __init__(self, db: Database):
        self._db = db

    async def add(self, deployment_id: str, user_id: int, data: dict):
        await self._db.add_deployment(deployment_id, user_id, data)

    async def get(self, deployment_id: str, user_id: int) -> dict | None:
        return await self._db.get_deployment(deployment_id, user_id)

    async def list_all(self, user_id: int) -> dict:
        return await self._db.list_deployments(user_id)

    async def update(self, deployment_id: str, user_id: int, updates: dict):
        await self._db.update_deployment(deployment_id, user_id, updates)

    async def remove(self, deployment_id: str, user_id: int):
        await self._db.remove_deployment(deployment_id, user_id)

    @staticmethod
    def normalize_repo_path(repo_path: str) -> str:
        """Normalize a repo path for comparison."""
        path = repo_path.rstrip("/")
        git_url_match = re.match(r"https?://(.+?)(?:\.git)?$", path)
        if git_url_match:
            return git_url_match.group(1)
        ssh_match = re.match(r"git@(.+?):(.+?)(?:\.git)?$", path)
        if ssh_match:
            return f"{ssh_match.group(1)}/{ssh_match.group(2)}"
        return os.path.realpath(path)

    async def find_by_repo(self, repo_path: str, user_id: int) -> tuple[str, dict] | None:
        normalized = self.normalize_repo_path(repo_path)
        return await self._db.find_deployment_by_repo(normalized, user_id)
