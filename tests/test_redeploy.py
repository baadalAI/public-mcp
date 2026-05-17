import pytest

from computeedge.state.manager import StateManager


def test_normalize_local_path_strips_trailing_slash(state_manager):
    assert state_manager.normalize_repo_path("/Users/me/myapp/") == "/Users/me/myapp"


def test_normalize_git_url_strips_dot_git(state_manager):
    result = state_manager.normalize_repo_path("https://github.com/user/myapp.git")
    assert result == "github.com/user/myapp"


def test_normalize_git_url_without_dot_git(state_manager):
    result = state_manager.normalize_repo_path("https://github.com/user/myapp")
    assert result == "github.com/user/myapp"


@pytest.mark.asyncio
async def test_find_by_repo_returns_match(state_manager, test_user_id):
    await state_manager.add("ce-hetzner-abc123", test_user_id, {
        "provider": "hetzner", "ip": "1.2.3.4",
        "repo_path": "/Users/me/myapp",
        "normalized_repo_path": state_manager.normalize_repo_path("/Users/me/myapp"),
    })
    result = await state_manager.find_by_repo("/Users/me/myapp/", test_user_id)
    assert result is not None
    assert result[0] == "ce-hetzner-abc123"


@pytest.mark.asyncio
async def test_find_by_repo_returns_none_when_no_match(state_manager, test_user_id):
    await state_manager.add("ce-hetzner-abc123", test_user_id, {
        "provider": "hetzner", "ip": "1.2.3.4",
        "repo_path": "/Users/me/other-app",
        "normalized_repo_path": state_manager.normalize_repo_path("/Users/me/other-app"),
    })
    result = await state_manager.find_by_repo("/Users/me/myapp", test_user_id)
    assert result is None


@pytest.mark.asyncio
async def test_find_by_repo_matches_git_url_to_local(state_manager, test_user_id):
    await state_manager.add("ce-hetzner-abc123", test_user_id, {
        "provider": "hetzner", "ip": "1.2.3.4",
        "repo_path": "https://github.com/user/myapp.git",
        "normalized_repo_path": state_manager.normalize_repo_path("https://github.com/user/myapp.git"),
    })
    result = await state_manager.find_by_repo("https://github.com/user/myapp", test_user_id)
    assert result is not None
