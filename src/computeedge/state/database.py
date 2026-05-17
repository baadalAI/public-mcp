import json
import secrets
from pathlib import Path

import aiosqlite
import bcrypt

from computeedge.utils.logger import get_logger

logger = get_logger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    api_key_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deployments (
    id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, user_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""


class Database:
    """SQLite-backed storage for users and deployments."""

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self):
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        # Migrate: add email column if missing (existing DBs)
        try:
            await self._db.execute("SELECT email FROM users LIMIT 1")
        except Exception:
            await self._db.execute("ALTER TABLE users ADD COLUMN email TEXT")
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    # --- Users ---

    async def create_user(self, name: str, raw_api_key: str, email: str | None = None) -> int:
        api_key_hash = bcrypt.hashpw(
            raw_api_key.encode(), bcrypt.gensalt()
        ).decode()
        cursor = await self._db.execute(
            "INSERT INTO users (name, email, api_key_hash) VALUES (?, ?, ?)",
            (name, email, api_key_hash),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_user_by_api_key(self, raw_api_key: str) -> dict | None:
        cursor = await self._db.execute("SELECT id, name, api_key_hash FROM users")
        rows = await cursor.fetchall()
        for row in rows:
            if bcrypt.checkpw(raw_api_key.encode(), row["api_key_hash"].encode()):
                return {"id": row["id"], "name": row["name"]}
        return None

    # --- Deployments ---

    async def add_deployment(self, deployment_id: str, user_id: int, data: dict):
        await self._db.execute(
            "INSERT OR REPLACE INTO deployments (id, user_id, data_json) VALUES (?, ?, ?)",
            (deployment_id, user_id, json.dumps(data)),
        )
        await self._db.commit()

    async def get_deployment(self, deployment_id: str, user_id: int) -> dict | None:
        cursor = await self._db.execute(
            "SELECT data_json FROM deployments WHERE id = ? AND user_id = ?",
            (deployment_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row["data_json"])

    async def list_deployments(self, user_id: int) -> dict:
        cursor = await self._db.execute(
            "SELECT id, data_json FROM deployments WHERE user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return {row["id"]: json.loads(row["data_json"]) for row in rows}

    async def update_deployment(self, deployment_id: str, user_id: int, updates: dict):
        existing = await self.get_deployment(deployment_id, user_id)
        if existing is None:
            return
        existing.update(updates)
        await self._db.execute(
            "UPDATE deployments SET data_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (json.dumps(existing), deployment_id, user_id),
        )
        await self._db.commit()

    async def remove_deployment(self, deployment_id: str, user_id: int):
        await self._db.execute(
            "DELETE FROM deployments WHERE id = ? AND user_id = ?",
            (deployment_id, user_id),
        )
        await self._db.commit()

    async def find_deployment_by_repo(
        self, normalized_repo_path: str, user_id: int
    ) -> tuple[str, dict] | None:
        cursor = await self._db.execute(
            "SELECT id, data_json FROM deployments WHERE user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            data = json.loads(row["data_json"])
            if data.get("normalized_repo_path") == normalized_repo_path:
                return (row["id"], data)
        return None
