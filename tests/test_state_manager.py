import pytest
from pathlib import Path
from computeedge.state.database import Database
from computeedge.state.manager import StateManager


@pytest.fixture
async def state(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.initialize()
    user_id = await db.create_user("testuser", "test-key")
    mgr = StateManager(db)
    yield mgr, user_id
    await db.close()


async def test_add_and_get(state):
    mgr, user_id = state
    await mgr.add("ce-hetzner-abc", user_id, {"ip": "1.2.3.4", "status": "deployed"})
    dep = await mgr.get("ce-hetzner-abc", user_id)
    assert dep is not None
    assert dep["ip"] == "1.2.3.4"


async def test_list_all_scoped(state):
    mgr, user_id = state
    await mgr.add("ce-hetzner-aaa", user_id, {"ip": "1.1.1.1"})
    deps = await mgr.list_all(user_id)
    assert "ce-hetzner-aaa" in deps


async def test_update(state):
    mgr, user_id = state
    await mgr.add("ce-hetzner-abc", user_id, {"ip": "1.1.1.1", "status": "deployed"})
    await mgr.update("ce-hetzner-abc", user_id, {"status": "destroyed"})
    dep = await mgr.get("ce-hetzner-abc", user_id)
    assert dep["status"] == "destroyed"


async def test_remove(state):
    mgr, user_id = state
    await mgr.add("ce-hetzner-abc", user_id, {"ip": "1.1.1.1"})
    await mgr.remove("ce-hetzner-abc", user_id)
    dep = await mgr.get("ce-hetzner-abc", user_id)
    assert dep is None


async def test_find_by_repo(state):
    mgr, user_id = state
    await mgr.add("ce-hetzner-abc", user_id, {
        "repo_path": "https://github.com/user/repo",
        "normalized_repo_path": "github.com/user/repo",
    })
    result = await mgr.find_by_repo("https://github.com/user/repo", user_id)
    assert result is not None
    assert result[0] == "ce-hetzner-abc"
