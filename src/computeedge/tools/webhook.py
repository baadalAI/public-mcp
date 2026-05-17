from computeedge.exceptions import ComputeEdgeError
from computeedge.services.webhook import WebhookService


def make_connect_github_tool(webhook_service: WebhookService | None):
    async def connect_github(
        deployment_id: str,
        branch: str = "main",
        github_token: str | None = None,
    ) -> dict:
        """Connect a deployment to GitHub for auto-deploy on push."""
        try:
            if webhook_service is None:
                return {"error": "Webhook service not available."}
            if not deployment_id.startswith("ce-"):
                return {"error": f"Invalid deployment ID: {deployment_id}"}
            return await webhook_service.connect(
                deployment_id, branch=branch, github_token=github_token
            )
        except ComputeEdgeError as e:
            return {"error": str(e)}

    return connect_github


def make_disconnect_github_tool(webhook_service: WebhookService | None):
    async def disconnect_github(deployment_id: str) -> dict:
        """Remove GitHub webhook and stop auto-deploy for a deployment."""
        try:
            if webhook_service is None:
                return {"error": "Webhook service not available."}
            if not deployment_id.startswith("ce-"):
                return {"error": f"Invalid deployment ID: {deployment_id}"}
            return await webhook_service.disconnect(deployment_id)
        except ComputeEdgeError as e:
            return {"error": str(e)}

    return disconnect_github
