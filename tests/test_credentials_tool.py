import pytest
from pathlib import Path

from computeedge.config.loader import Config
from computeedge.tools.credentials import make_set_credentials_tool


@pytest.fixture
def tmp_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    return Config(config_path=config_path)


@pytest.fixture
def set_credentials(tmp_config):
    return make_set_credentials_tool(tmp_config)


# --- Provider token tests ---


@pytest.mark.asyncio
async def test_save_hetzner_token(set_credentials, tmp_config):
    result = await set_credentials(provider="hetzner", token="test-token-123")
    assert result["status"] == "saved"
    assert result["type"] == "provider_token"
    assert result["provider"] == "hetzner"
    assert tmp_config.get_provider_token("hetzner") == "test-token-123"


@pytest.mark.asyncio
async def test_save_digitalocean_token(set_credentials, tmp_config):
    result = await set_credentials(provider="digitalocean", token="dop_v1_abc123")
    assert result["status"] == "saved"
    assert result["provider"] == "digitalocean"
    assert tmp_config.get_provider_token("digitalocean") == "dop_v1_abc123"


@pytest.mark.asyncio
async def test_save_github_token(set_credentials, tmp_config):
    result = await set_credentials(provider="github", token="ghp_abc123")
    assert result["status"] == "saved"
    assert tmp_config.get_provider_token("github") == "ghp_abc123"


@pytest.mark.asyncio
async def test_unsupported_provider(set_credentials):
    result = await set_credentials(provider="aws", token="some-token")
    assert "error" in result
    assert "Unknown provider" in result["error"]


@pytest.mark.asyncio
async def test_empty_token(set_credentials):
    result = await set_credentials(provider="hetzner", token="")
    assert "error" in result
    assert "empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_whitespace_token(set_credentials):
    result = await set_credentials(provider="hetzner", token="   ")
    assert "error" in result
    assert "empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_provider_case_insensitive(set_credentials, tmp_config):
    result = await set_credentials(provider="Hetzner", token="test-token")
    assert result["status"] == "saved"
    assert result["provider"] == "hetzner"


@pytest.mark.asyncio
async def test_token_stripped(set_credentials, tmp_config):
    result = await set_credentials(provider="hetzner", token="  token-with-spaces  ")
    assert result["status"] == "saved"
    assert tmp_config.get_provider_token("hetzner") == "token-with-spaces"


@pytest.mark.asyncio
async def test_overwrite_existing_token(set_credentials, tmp_config):
    await set_credentials(provider="hetzner", token="old-token")
    result = await set_credentials(provider="hetzner", token="new-token")
    assert result["status"] == "saved"
    assert tmp_config.get_provider_token("hetzner") == "new-token"


@pytest.mark.asyncio
async def test_config_file_permissions(set_credentials, tmp_config):
    result = await set_credentials(provider="hetzner", token="test-token")
    config_path = Path(result["config_path"])
    assert config_path.exists()
    assert oct(config_path.stat().st_mode & 0o777) == "0o600"


# --- Env var tests ---


@pytest.mark.asyncio
async def test_save_env_var(set_credentials, tmp_config):
    result = await set_credentials(key="DATABASE_URL", value="postgres://localhost/mydb")
    assert result["status"] == "saved"
    assert result["type"] == "env_var"
    assert result["key"] == "DATABASE_URL"
    assert tmp_config.get_env_vars() == {"DATABASE_URL": "postgres://localhost/mydb"}


@pytest.mark.asyncio
async def test_save_multiple_env_vars(set_credentials, tmp_config):
    await set_credentials(key="DATABASE_URL", value="postgres://localhost/mydb")
    await set_credentials(key="STRIPE_KEY", value="sk_live_abc")
    env_vars = tmp_config.get_env_vars()
    assert env_vars == {
        "DATABASE_URL": "postgres://localhost/mydb",
        "STRIPE_KEY": "sk_live_abc",
    }


@pytest.mark.asyncio
async def test_overwrite_env_var(set_credentials, tmp_config):
    await set_credentials(key="DATABASE_URL", value="old-url")
    await set_credentials(key="DATABASE_URL", value="new-url")
    assert tmp_config.get_env_vars() == {"DATABASE_URL": "new-url"}


@pytest.mark.asyncio
async def test_env_var_empty_key(set_credentials):
    result = await set_credentials(key="", value="something")
    assert "error" in result
    assert "empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_env_var_empty_value(set_credentials):
    result = await set_credentials(key="MY_VAR", value="")
    assert "error" in result
    assert "empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_env_var_strips_whitespace(set_credentials, tmp_config):
    result = await set_credentials(key="  MY_KEY  ", value="  my-value  ")
    assert result["status"] == "saved"
    assert result["key"] == "MY_KEY"
    assert tmp_config.get_env_vars() == {"MY_KEY": "my-value"}


@pytest.mark.asyncio
async def test_env_var_file_permissions(set_credentials):
    result = await set_credentials(key="SECRET", value="s3cret")
    config_path = Path(result["config_path"])
    assert oct(config_path.stat().st_mode & 0o777) == "0o600"


@pytest.mark.asyncio
async def test_no_args_returns_error(set_credentials):
    result = await set_credentials()
    assert "error" in result


@pytest.mark.asyncio
async def test_env_var_and_provider_coexist(set_credentials, tmp_config):
    await set_credentials(provider="hetzner", token="my-token")
    await set_credentials(key="DATABASE_URL", value="postgres://localhost/db")
    assert tmp_config.get_provider_token("hetzner") == "my-token"
    assert tmp_config.get_env_vars() == {"DATABASE_URL": "postgres://localhost/db"}


# --- Config.delete_env_var tests ---


@pytest.mark.asyncio
async def test_delete_env_var(set_credentials, tmp_config):
    await set_credentials(key="MY_VAR", value="val")
    assert tmp_config.delete_env_var("MY_VAR") is True
    assert tmp_config.get_env_vars() == {}


@pytest.mark.asyncio
async def test_delete_nonexistent_env_var(tmp_config):
    assert tmp_config.delete_env_var("NOPE") is False
