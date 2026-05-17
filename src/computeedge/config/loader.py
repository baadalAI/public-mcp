import logging
import os
from pathlib import Path
from typing import Any

import yaml

from computeedge.exceptions import ConfigError

logger = logging.getLogger("computeedge.config")


def load_yaml(path: Path) -> dict:
    """Load and parse a YAML file. Raises FileNotFoundError if missing."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def load_bundled_yaml(name: str) -> dict:
    """Load a YAML file bundled with the package (in config/)."""
    path = Path(__file__).parent / name
    return load_yaml(path)


class Config:
    """Loads user configuration from env vars and ~/.computeedge/config.yaml."""

    def __init__(self, config_path: Path | None = None):
        if config_path is None:
            config_path = Path.home() / ".computeedge" / "config.yaml"
        self._config_path = config_path
        self._file_config = self._load_file(config_path)

    def _load_file(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return load_yaml(path)
        except Exception as e:
            logger.warning("Failed to parse config file %s: %s", path, e)
            return {}

    def get_provider_token(self, provider: str) -> str | None:
        """Get provider API token. Checks env var first, then config file."""
        env_key = f"COMPUTEEDGE_{provider.upper()}_TOKEN"
        env_val = os.environ.get(env_key)
        if env_val:
            return env_val
        return (
            self._file_config
            .get("providers", {})
            .get(provider, {})
            .get("api_token")
        )

    def get_provider_setting(self, provider: str, key: str, fallback: Any = None) -> Any:
        """Get provider setting from env, then provider config, then defaults."""
        env_key = f"COMPUTEEDGE_{provider.upper()}_{key.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val

        provider_settings = (
            self._file_config
            .get("providers", {})
            .get(provider, {})
        )
        if key in provider_settings:
            return provider_settings[key]

        return self.get_default(key, fallback)

    def save_provider_token(self, provider: str, token: str) -> Path:
        """Save a provider API token to ~/.computeedge/config.yaml with secure permissions."""
        config_path = self._config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)

        config = {}
        if config_path.exists():
            try:
                config = yaml.safe_load(config_path.read_text()) or {}
            except Exception:
                config = {}

        providers = config.setdefault("providers", {})
        providers.setdefault(provider, {})["api_token"] = token

        config_path.write_text(yaml.dump(config, default_flow_style=False))
        config_path.chmod(0o600)

        # Reload so subsequent get_provider_token calls see the new value
        self._file_config = config

        return config_path

    def save_env_var(self, key: str, value: str) -> Path:
        """Save an application env var to ~/.computeedge/config.yaml."""
        config_path = self._config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)

        config = {}
        if config_path.exists():
            try:
                config = yaml.safe_load(config_path.read_text()) or {}
            except Exception:
                config = {}

        config.setdefault("env_vars", {})[key] = value

        config_path.write_text(yaml.dump(config, default_flow_style=False))
        config_path.chmod(0o600)

        self._file_config = config
        return config_path

    def get_env_vars(self) -> dict[str, str]:
        """Return all saved application env vars."""
        return dict(self._file_config.get("env_vars", {}))

    def delete_env_var(self, key: str) -> bool:
        """Delete a saved env var. Returns True if it existed."""
        config_path = self._config_path
        env_vars = self._file_config.get("env_vars", {})
        if key not in env_vars:
            return False

        del env_vars[key]
        if not env_vars:
            self._file_config.pop("env_vars", None)
        else:
            self._file_config["env_vars"] = env_vars

        config_path.write_text(yaml.dump(self._file_config, default_flow_style=False))
        config_path.chmod(0o600)
        return True

    def get_default(self, key: str, fallback: Any = None) -> Any:
        """Get a default setting from config file."""
        return self._file_config.get("defaults", {}).get(key, fallback)
