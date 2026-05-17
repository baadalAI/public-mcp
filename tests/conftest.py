from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


from computeedge.state.database import Database
from computeedge.state.manager import StateManager


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def state_manager(db):
    return StateManager(db)


@pytest.fixture
async def test_user_id(db):
    return await db.create_user("testuser", "test-key")
