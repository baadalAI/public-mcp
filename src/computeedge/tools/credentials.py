from computeedge.config.loader import Config
from computeedge.exceptions import ConfigError

SUPPORTED_PROVIDERS = {"hetzner", "digitalocean", "github"}


def make_set_credentials_tool(config: Config):
    async def set_credentials(
        provider: str | None = None,
        token: str | None = None,
        key: str | None = None,
        value: str | None = None,
    ) -> dict:
        """Save a provider API token or application env var to ~/.computeedge/config.yaml.

        For provider tokens:
            set_credentials(provider="hetzner", token="your-token")
            Supported providers: hetzner, digitalocean, github.

        For app env vars (auto-included in every deploy):
            set_credentials(key="DATABASE_URL", value="postgres://...")
            set_credentials(key="STRIPE_KEY", value="sk_live_...")

        All values stored with owner-only file permissions (chmod 600).
        """
        try:
            # App env var path
            if key is not None:
                if not key or not key.strip():
                    return {"error": "Key cannot be empty."}
                if value is None or not str(value).strip():
                    return {"error": "Value cannot be empty."}

                key = key.strip()
                value = str(value).strip()
                config_path = config.save_env_var(key, value)

                return {
                    "status": "saved",
                    "type": "env_var",
                    "key": key,
                    "config_path": str(config_path),
                    "message": (
                        f"Env var {key} saved to {config_path} (permissions: 600). "
                        f"It will be automatically included in future deploys."
                    ),
                }

            # Provider token path
            if provider is None:
                return {
                    "error": "Provide either provider+token (for API tokens) "
                    "or key+value (for app env vars)."
                }

            provider = provider.lower().strip()
            if provider not in SUPPORTED_PROVIDERS:
                return {
                    "error": f"Unknown provider '{provider}'. "
                    f"Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
                }

            if not token or not token.strip():
                return {"error": "Token cannot be empty."}

            config_path = config.save_provider_token(provider, token.strip())

            return {
                "status": "saved",
                "type": "provider_token",
                "provider": provider,
                "config_path": str(config_path),
                "message": (
                    f"{provider} token saved to {config_path} (permissions: 600). "
                    f"You can now use {provider} tools without passing provider_token each time."
                ),
            }

        except ConfigError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to save credentials: {e}"}

    return set_credentials
