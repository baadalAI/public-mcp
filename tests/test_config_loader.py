import os
from pathlib import Path
from unittest.mock import patch

import pytest

from computeedge.config.loader import Config, load_yaml


def test_load_yaml_stacks():
    config_dir = Path(__file__).parent.parent / "src" / "computeedge" / "config"
    data = load_yaml(config_dir / "stacks.yaml")
    assert "frontend" in data
    assert "backend" in data
    assert "database" in data
    assert "resources" in data


def test_load_yaml_providers():
    config_dir = Path(__file__).parent.parent / "src" / "computeedge" / "config"
    data = load_yaml(config_dir / "providers.yaml")
    assert "railway" in data
    assert "hetzner" in data
    assert "digitalocean" in data


def test_load_yaml_missing_file():
    with pytest.raises(FileNotFoundError):
        load_yaml(Path("/nonexistent/file.yaml"))


def test_config_provider_token_from_env():
    with patch.dict(os.environ, {"COMPUTEEDGE_HETZNER_TOKEN": "test-token-123"}):
        config = Config()
        assert config.get_provider_token("hetzner") == "test-token-123"


def test_config_provider_token_missing():
    with patch.dict(os.environ, {}, clear=True):
        config = Config(config_path=Path("/nonexistent/config.yaml"))
        assert config.get_provider_token("hetzner") is None


def test_config_default_values():
    config = Config(config_path=Path("/nonexistent/config.yaml"))
    assert config.get_default("region", "us-east") == "us-east"
    assert config.get_default("budget_max_monthly", 20) == 20


def test_config_provider_setting_from_env():
    with patch.dict(os.environ, {"COMPUTEEDGE_HETZNER_DEPLOYMENT_BACKEND": "terraform"}):
        config = Config(config_path=Path("/nonexistent/config.yaml"))
        assert config.get_provider_setting("hetzner", "deployment_backend", "api") == "terraform"


def test_load_system_deps_yaml():
    from computeedge.config.loader import load_bundled_yaml
    config = load_bundled_yaml("system_deps.yaml")
    assert "python_packages" in config
    assert "psycopg2" in config["python_packages"]
    assert config["python_packages"]["psycopg2"] == ["libpq-dev"]
    assert "python_base_build_packages" in config
    assert "gcc" in config["python_base_build_packages"]


def test_config_malformed_yaml_logs_warning(tmp_path, caplog):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(": invalid: yaml: [[[")
    import logging
    with caplog.at_level(logging.WARNING, logger="computeedge.config"):
        config = Config(config_path=config_file)
    assert config.get_provider_token("hetzner") is None
    assert any("Failed to parse" in record.message for record in caplog.records)
