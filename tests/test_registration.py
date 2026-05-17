"""Tests for the registration API endpoint."""
import json

import pytest

from computeedge.state.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


class TestCreateUserWithEmail:
    @pytest.mark.asyncio
    async def test_create_user_with_email(self, db):
        user_id = await db.create_user("alice", "ce_test123", email="alice@example.com")
        assert user_id > 0

    @pytest.mark.asyncio
    async def test_create_user_without_email(self, db):
        user_id = await db.create_user("bob", "ce_test456")
        assert user_id > 0

    @pytest.mark.asyncio
    async def test_api_key_validates_after_create(self, db):
        await db.create_user("charlie", "ce_mykey", email="charlie@test.com")
        user = await db.get_user_by_api_key("ce_mykey")
        assert user is not None
        assert user["name"] == "charlie"

    @pytest.mark.asyncio
    async def test_wrong_key_returns_none(self, db):
        await db.create_user("dave", "ce_correct", email="dave@test.com")
        user = await db.get_user_by_api_key("ce_wrong")
        assert user is None


class TestEmailMigration:
    @pytest.mark.asyncio
    async def test_initialize_creates_email_column(self, db):
        """New databases should have the email column."""
        cursor = await db._db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]
        assert "email" in columns

    @pytest.mark.asyncio
    async def test_migrate_adds_email_column(self, tmp_path):
        """Existing databases without email column get it added."""
        import aiosqlite

        db_path = tmp_path / "old.db"
        # Create old schema without email
        async with aiosqlite.connect(str(db_path)) as conn:
            await conn.execute("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    api_key_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE deployments (
                    id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id, user_id)
                )
            """)
            await conn.commit()

        # Now initialize with new code — should migrate
        db = Database(db_path)
        await db.initialize()

        cursor = await db._db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]
        assert "email" in columns
        await db.close()


class TestAuthPublicPaths:
    def test_register_is_public(self):
        from computeedge.auth import BearerTokenMiddleware
        assert "/register" in BearerTokenMiddleware._PUBLIC_PATHS
        assert "/api/register" in BearerTokenMiddleware._PUBLIC_PATHS
