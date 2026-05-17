import secrets
from datetime import datetime, timezone
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader

from computeedge.config.loader import Config
from computeedge.exceptions import WebhookError
from computeedge.state.manager import StateManager
from computeedge.utils.logger import get_logger
from computeedge.utils.ssh import SSHClient

logger = get_logger("webhook")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
WEBHOOK_PORT = 9867


class WebhookService:
    """Manages GitHub webhook auto-deploy for existing deployments."""

    def __init__(self, state: StateManager, config: Config):
        self._state = state
        self._config = config
        self._ssh = SSHClient()
        self._jinja = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            keep_trailing_newline=True,
        )

    async def connect(
        self,
        deployment_id: str,
        branch: str = "main",
        github_token: str | None = None,
        user_id: int = 0,
    ) -> dict:
        """Set up GitHub webhook + receiver on an existing deployment."""
        state = await self._state.get(deployment_id, user_id)
        if state is None:
            raise WebhookError(f"Deployment {deployment_id} not found.")

        if state.get("webhook"):
            raise WebhookError(
                f"Webhook already connected for {deployment_id}. "
                "Use disconnect_github first."
            )

        owner, repo = self._extract_github_repo(state)
        ip = state["ip"]
        private_key = state["ssh_key_path"]

        token = github_token or self._config.get_provider_token("github")
        if not token:
            raise WebhookError(
                "GitHub token not configured. Provide it via:\n"
                "1. connect_github(..., github_token='ghp_...')\n"
                "2. set_credentials(provider='github', token='ghp_...')\n"
                "3. Set COMPUTEEDGE_GITHUB_TOKEN env var\n\n"
                "Token needs 'admin:repo_hook' scope (classic) or "
                "'Webhooks' permission (fine-grained)."
            )

        webhook_secret = secrets.token_hex(20)
        webhook_url = f"http://{ip}:{WEBHOOK_PORT}/webhook"

        # Create GitHub webhook
        hook_id = await self._create_github_webhook(
            token, owner, repo, webhook_url, webhook_secret
        )

        # Install receiver on server (rollback webhook on failure)
        try:
            conn = await self._ssh.connect(ip, private_key)
            await self._install_receiver(conn, deployment_id, webhook_secret, branch)
        except Exception:
            logger.warning("SSH failed, rolling back GitHub webhook %d", hook_id)
            await self._delete_github_webhook(token, owner, repo, hook_id)
            raise

        # Save state
        await self._state.update(deployment_id, user_id, {
            "webhook": {
                "github_hook_id": hook_id,
                "webhook_secret": webhook_secret,
                "branch": branch,
                "github_repo": f"{owner}/{repo}",
                "webhook_url": webhook_url,
                "connected_at": datetime.now(timezone.utc).isoformat(),
            }
        })

        logger.info("Webhook connected for %s -> %s/%s branch %s",
                     deployment_id, owner, repo, branch)

        return {
            "status": "connected",
            "deployment_id": deployment_id,
            "webhook_url": webhook_url,
            "github_repo": f"{owner}/{repo}",
            "branch": branch,
            "message": (
                f"Auto-deploy connected. Pushes to '{branch}' on "
                f"{owner}/{repo} will auto-redeploy."
            ),
        }

    async def disconnect(self, deployment_id: str, user_id: int = 0) -> dict:
        """Remove GitHub webhook and stop receiver."""
        state = await self._state.get(deployment_id, user_id)
        if state is None:
            raise WebhookError(f"Deployment {deployment_id} not found.")

        webhook = state.get("webhook")
        if not webhook:
            raise WebhookError(f"No webhook configured for {deployment_id}.")

        # Delete GitHub webhook (best-effort)
        token = self._config.get_provider_token("github")
        if token:
            owner_repo = webhook["github_repo"]
            hook_id = webhook["github_hook_id"]
            owner, repo = owner_repo.split("/", 1)
            await self._delete_github_webhook(token, owner, repo, hook_id)
        else:
            logger.warning("No GitHub token available, skipping webhook deletion")

        # Stop receiver on server (best-effort)
        try:
            ip = state["ip"]
            private_key = state["ssh_key_path"]
            conn = await self._ssh.connect(ip, private_key)
            service_name = f"computeedge-webhook-{deployment_id}"
            await self._ssh.run(
                conn,
                f"systemctl stop {service_name} && "
                f"systemctl disable {service_name} || true"
            )
            await self._ssh.run(
                conn,
                f"rm -f /etc/systemd/system/{service_name}.service && "
                "systemctl daemon-reload"
            )
            await self._ssh.run(
                conn, f"rm -f /root/{deployment_id}/webhook_receiver.py"
            )
        except Exception as e:
            logger.warning("Failed to stop receiver on server: %s", e)

        # Clear webhook from state
        await self._state.update(deployment_id, user_id, {"webhook": None})

        logger.info("Webhook disconnected for %s", deployment_id)

        return {
            "status": "disconnected",
            "deployment_id": deployment_id,
            "message": "GitHub webhook removed and receiver stopped.",
        }

    def _extract_github_repo(self, deployment_data: dict) -> tuple[str, str]:
        """Extract (owner, repo) from normalized_repo_path."""
        normalized = deployment_data.get("normalized_repo_path", "")
        if not normalized.startswith("github.com/"):
            raise WebhookError(
                "Auto-deploy is only available for GitHub repository deployments. "
                f"Got: {normalized or deployment_data.get('repo_path', 'unknown')}"
            )
        parts = normalized.removeprefix("github.com/").split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise WebhookError(
                f"Could not parse GitHub owner/repo from: {normalized}"
            )
        return parts[0], parts[1]

    async def _create_github_webhook(
        self, token: str, owner: str, repo: str,
        webhook_url: str, webhook_secret: str,
    ) -> int:
        """Create a webhook on a GitHub repo. Returns the hook ID."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/hooks",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={
                    "config": {
                        "url": webhook_url,
                        "content_type": "json",
                        "secret": webhook_secret,
                    },
                    "events": ["push"],
                    "active": True,
                },
            )
            if resp.status_code == 404:
                raise WebhookError(
                    f"GitHub repo {owner}/{repo} not found, or token lacks "
                    "'admin:repo_hook' scope."
                )
            if resp.status_code >= 400:
                msg = resp.json().get("message", f"HTTP {resp.status_code}")
                raise WebhookError(f"GitHub API error: {msg}")
            return resp.json()["id"]

    async def _delete_github_webhook(
        self, token: str, owner: str, repo: str, hook_id: int,
    ) -> None:
        """Delete a webhook from a GitHub repo. Ignores 404 (already deleted)."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"https://api.github.com/repos/{owner}/{repo}/hooks/{hook_id}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if resp.status_code not in (204, 404):
                    logger.warning("Failed to delete webhook %d: HTTP %d",
                                   hook_id, resp.status_code)
        except Exception as e:
            logger.warning("Failed to delete webhook %d: %s", hook_id, e)

    async def _install_receiver(
        self, conn, deployment_id: str, webhook_secret: str, branch: str,
    ) -> None:
        """Upload and start the webhook receiver on the server."""
        # Render and upload receiver script
        receiver = self._jinja.get_template("webhook_receiver.py.j2").render(
            webhook_secret=webhook_secret,
            branch=branch,
            deployment_id=deployment_id,
            webhook_port=WEBHOOK_PORT,
        )
        await self._ssh.upload_string(
            conn, receiver, f"/root/{deployment_id}/webhook_receiver.py"
        )
        await self._ssh.run(
            conn, f"chmod +x /root/{deployment_id}/webhook_receiver.py"
        )

        # Render and upload systemd unit
        service_name = f"computeedge-webhook-{deployment_id}"
        unit = self._jinja.get_template("webhook.service.j2").render(
            deployment_id=deployment_id,
        )
        await self._ssh.upload_string(
            conn, unit, f"/etc/systemd/system/{service_name}.service"
        )

        # Enable and start
        await self._ssh.run(
            conn,
            f"systemctl daemon-reload && "
            f"systemctl enable {service_name} && "
            f"systemctl start {service_name}"
        )

        # Open firewall port
        await self._ssh.run(
            conn,
            f"ufw allow {WEBHOOK_PORT}/tcp 2>/dev/null || "
            f"iptables -A INPUT -p tcp --dport {WEBHOOK_PORT} -j ACCEPT 2>/dev/null || true"
        )
