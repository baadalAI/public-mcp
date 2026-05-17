import pytest
from pathlib import Path
from computeedge.state.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


async def test_create_user(db):
    user_id = await db.create_user("alice", "raw-api-key-123")
    assert user_id is not None
    user = await db.get_user_by_api_key("raw-api-key-123")
    assert user is not None
    assert user["name"] == "alice"
    assert user["id"] == user_id


async def test_invalid_api_key_returns_none(db):
    await db.create_user("alice", "real-key")
    user = await db.get_user_by_api_key("wrong-key")
    assert user is None


async def test_add_deployment(db):
    user_id = await db.create_user("alice", "key-1")
    await db.add_deployment("ce-hetzner-abc123", user_id, {
        "provider": "hetzner",
        "ip": "1.2.3.4",
        "status": "deployed",
    })
    dep = await db.get_deployment("ce-hetzner-abc123", user_id)
    assert dep is not None
    assert dep["ip"] == "1.2.3.4"


async def test_deployment_scoped_by_user(db):
    user_a = await db.create_user("alice", "key-a")
    user_b = await db.create_user("bob", "key-b")
    await db.add_deployment("ce-hetzner-abc123", user_a, {"ip": "1.2.3.4"})
    dep = await db.get_deployment("ce-hetzner-abc123", user_b)
    assert dep is None


async def test_list_deployments_scoped(db):
    user_a = await db.create_user("alice", "key-a")
    user_b = await db.create_user("bob", "key-b")
    await db.add_deployment("ce-hetzner-aaa", user_a, {"ip": "1.1.1.1"})
    await db.add_deployment("ce-hetzner-bbb", user_b, {"ip": "2.2.2.2"})
    alice_deps = await db.list_deployments(user_a)
    assert len(alice_deps) == 1
    assert "ce-hetzner-aaa" in alice_deps


async def test_update_deployment(db):
    user_id = await db.create_user("alice", "key-1")
    await db.add_deployment("ce-hetzner-abc", user_id, {"ip": "1.1.1.1", "status": "deployed"})
    await db.update_deployment("ce-hetzner-abc", user_id, {"status": "destroyed"})
    dep = await db.get_deployment("ce-hetzner-abc", user_id)
    assert dep["status"] == "destroyed"


async def test_remove_deployment(db):
    user_id = await db.create_user("alice", "key-1")
    await db.add_deployment("ce-hetzner-abc", user_id, {"ip": "1.1.1.1"})
    await db.remove_deployment("ce-hetzner-abc", user_id)
    dep = await db.get_deployment("ce-hetzner-abc", user_id)
    assert dep is None


async def test_find_by_repo(db):
    user_id = await db.create_user("alice", "key-1")
    await db.add_deployment("ce-hetzner-abc", user_id, {
        "repo_path": "https://github.com/user/repo",
        "normalized_repo_path": "github.com/user/repo",
    })
    result = await db.find_deployment_by_repo("github.com/user/repo", user_id)
    assert result is not None
    assert result[0] == "ce-hetzner-abc"
