from computeedge.providers.digitalocean import DigitalOceanProvider
from computeedge.services.infra.base import (
    InfrastructurePlanResult,
    InfrastructureProvisioner,
    ProvisionedInfrastructure,
)
from computeedge.utils.logger import get_logger

logger = get_logger("infra.digitalocean")


class DigitalOceanInfrastructureProvisioner(InfrastructureProvisioner):
    """Provision infrastructure using the DigitalOcean API client."""

    def __init__(self, provider: DigitalOceanProvider):
        self._provider = provider

    async def provision(
        self,
        deployment_id: str,
        plan: str,
        public_key_content: str,
        user_data: str | None = None,
    ) -> ProvisionedInfrastructure:
        logger.info("Provisioning DigitalOcean droplet for %s (plan: %s)", deployment_id, plan)
        ssh_key_id = await self._provider.upload_ssh_key(
            f"computeedge-{deployment_id}", public_key_content,
        )
        droplet = await self._provider.create_droplet(
            deployment_id, plan, ssh_key_id, user_data=user_data,
        )
        return ProvisionedInfrastructure(
            provider="digitalocean",
            plan=plan,
            server_id=droplet["id"],
            server_name=deployment_id,
            backend="api",
            ssh_key_id=ssh_key_id,
            ip=droplet.get("ip"),
            status=droplet.get("status", ""),
        )

    async def wait_until_ready(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        ip = await self._provider.wait_for_ready(infrastructure.server_id)
        firewall_id = await self._provider.create_firewall(
            f"{infrastructure.server_name}-firewall",
            infrastructure.server_id,
        )
        logger.info("Created firewall %s for droplet %s", firewall_id, infrastructure.server_id)
        metadata = dict(infrastructure.metadata)
        metadata["firewall_id"] = firewall_id
        return ProvisionedInfrastructure(
            provider=infrastructure.provider,
            plan=infrastructure.plan,
            server_id=infrastructure.server_id,
            server_name=infrastructure.server_name,
            backend=infrastructure.backend,
            ssh_key_id=infrastructure.ssh_key_id,
            ip=ip,
            status="running",
            metadata=metadata,
        )

    async def cleanup(self, infrastructure: ProvisionedInfrastructure) -> None:
        logger.info("Cleaning up DigitalOcean droplet %s", infrastructure.server_id)
        firewall_id = infrastructure.metadata.get("firewall_id")
        if firewall_id:
            await self._provider.delete_firewall(firewall_id)
        await self._provider.delete_droplet(infrastructure.server_id)
        if infrastructure.ssh_key_id:
            await self._provider.delete_ssh_key(infrastructure.ssh_key_id)

    async def destroy(self, infrastructure: ProvisionedInfrastructure) -> None:
        logger.info("Destroying DigitalOcean droplet %s", infrastructure.server_id)
        await self.cleanup(infrastructure)

    async def refresh(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        droplet = await self._provider.get_droplet(infrastructure.server_id)
        return ProvisionedInfrastructure(
            provider=infrastructure.provider,
            plan=infrastructure.plan,
            server_id=droplet["id"],
            server_name=infrastructure.server_name,
            backend=infrastructure.backend,
            ssh_key_id=infrastructure.ssh_key_id,
            ip=droplet.get("ip"),
            status=droplet.get("status", ""),
            metadata=dict(infrastructure.metadata),
        )

    async def plan(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> InfrastructurePlanResult:
        refreshed = await self.refresh(infrastructure)
        summary = (
            "Live DigitalOcean API backend does not support a declarative plan. "
            f"Current droplet status is '{refreshed.status}'."
        )
        return InfrastructurePlanResult(
            provider=infrastructure.provider,
            backend=infrastructure.backend,
            has_changes=False,
            summary=summary,
            metadata={"server_id": str(refreshed.server_id)},
        )
