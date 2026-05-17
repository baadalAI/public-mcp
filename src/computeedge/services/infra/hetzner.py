from computeedge.providers.hetzner import HetznerProvider
from computeedge.services.infra.base import (
    InfrastructurePlanResult,
    InfrastructureProvisioner,
    ProvisionedInfrastructure,
)
from computeedge.utils.logger import get_logger

logger = get_logger("infra.hetzner")


class HetznerInfrastructureProvisioner(InfrastructureProvisioner):
    """Provision infrastructure using the existing Hetzner API client."""

    def __init__(self, provider: HetznerProvider):
        self._provider = provider

    async def provision(
        self,
        deployment_id: str,
        plan: str,
        public_key_content: str,
        user_data: str | None = None,
    ) -> ProvisionedInfrastructure:
        logger.info("Provisioning Hetzner server for %s (plan: %s)", deployment_id, plan)
        ssh_key_id = await self._provider.upload_ssh_key(
            f"computeedge-{deployment_id}", public_key_content
        )
        server = await self._provider.create_server(
            deployment_id, plan, ssh_key_id, user_data=user_data
        )
        return ProvisionedInfrastructure(
            provider="hetzner",
            plan=plan,
            server_id=server["id"],
            server_name=deployment_id,
            backend="api",
            ssh_key_id=ssh_key_id,
            ip=server.get("ip"),
            status=server.get("status", ""),
        )

    async def wait_until_ready(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        ip = await self._provider.wait_for_ready(infrastructure.server_id)
        return infrastructure.with_ip(ip, status="running")

    async def cleanup(self, infrastructure: ProvisionedInfrastructure) -> None:
        logger.info("Cleaning up Hetzner server %s", infrastructure.server_id)
        await self._provider.delete_server(infrastructure.server_id)
        if infrastructure.ssh_key_id:
            await self._provider.delete_ssh_key(infrastructure.ssh_key_id)

    async def destroy(self, infrastructure: ProvisionedInfrastructure) -> None:
        logger.info("Destroying Hetzner server %s", infrastructure.server_id)
        await self.cleanup(infrastructure)

    async def refresh(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        server = await self._provider.get_server(infrastructure.server_id)
        return ProvisionedInfrastructure(
            provider=infrastructure.provider,
            plan=infrastructure.plan,
            server_id=server["id"],
            server_name=infrastructure.server_name,
            backend=infrastructure.backend,
            ssh_key_id=infrastructure.ssh_key_id,
            ip=server.get("ip"),
            status=server.get("status", ""),
            metadata=dict(infrastructure.metadata),
        )

    async def plan(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> InfrastructurePlanResult:
        refreshed = await self.refresh(infrastructure)
        summary = (
            "Live Hetzner API backend does not support a declarative Terraform-style plan. "
            f"Current server status is '{refreshed.status}'."
        )
        return InfrastructurePlanResult(
            provider=infrastructure.provider,
            backend=infrastructure.backend,
            has_changes=False,
            summary=summary,
            metadata={"server_id": str(refreshed.server_id)},
        )
