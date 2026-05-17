from pathlib import Path

import httpx

from computeedge.exceptions import DeploymentError
from computeedge.services.infra.base import (
    InfrastructurePlanResult,
    InfrastructureProvisioner,
    ProvisionedInfrastructure,
)
from computeedge.services.infra.terraform_runner import TerraformRunner
from computeedge.utils.logger import get_logger

logger = get_logger("infra.terraform")


class HetznerTerraformProvisioner(InfrastructureProvisioner):
    """Terraform-backed Hetzner provisioner."""

    def __init__(
        self,
        token: str,
        runner: TerraformRunner,
        location: str = "nbg1",
        image: str = "ubuntu-24.04",
    ):
        self._token = token
        self._runner = runner
        self._location = location
        self._image = image

    async def provision(
        self,
        deployment_id: str,
        plan: str,
        public_key_content: str,
        user_data: str | None = None,
    ) -> ProvisionedInfrastructure:
        logger.info("Provisioning Terraform infrastructure for %s (plan: %s)", deployment_id, plan)
        workspace = self._runner.prepare_workspace(
            deployment_id,
            {
                "hcloud_token": self._token,
                "server_name": deployment_id,
                "server_type": plan,
                "location": self._location,
                "image": self._image,
                "ssh_public_key": public_key_content,
            },
        )
        existing_key_id = await self._find_existing_ssh_key(public_key_content)
        if existing_key_id is not None:
            logger.info("SSH key already exists in Hetzner (id=%s), importing into Terraform state", existing_key_id)
            await self._runner.import_resource(workspace, "hcloud_ssh_key.computeedge", str(existing_key_id))

        outputs = await self._runner.apply(workspace)
        server_id = outputs.get("server_id")
        public_ip = outputs.get("public_ipv4")
        if server_id is None or public_ip is None:
            logger.error("Terraform missing required outputs for %s: server_id=%s public_ipv4=%s", deployment_id, server_id, public_ip)
            raise DeploymentError(
                "Terraform did not return required Hetzner outputs (server_id/public_ipv4)"
            )

        metadata = {
            "workspace_dir": str(workspace),
        }
        if outputs.get("firewall_id") is not None:
            metadata["firewall_id"] = str(outputs["firewall_id"])

        return ProvisionedInfrastructure(
            provider="hetzner",
            plan=plan,
            server_id=int(server_id),
            server_name=deployment_id,
            backend="terraform",
            ssh_key_id=int(outputs["ssh_key_id"]) if outputs.get("ssh_key_id") is not None else None,
            ip=str(public_ip),
            status=str(outputs.get("server_status", "created")),
            metadata=metadata,
        )

    async def wait_until_ready(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        return infrastructure.with_ip(infrastructure.ip or "", status="running")

    async def cleanup(self, infrastructure: ProvisionedInfrastructure) -> None:
        await self.destroy(infrastructure)

    async def destroy(self, infrastructure: ProvisionedInfrastructure) -> None:
        logger.info("Destroying Terraform infrastructure for %s", infrastructure.server_name)
        workspace_dir = infrastructure.metadata.get("workspace_dir")
        if not workspace_dir:
            logger.error("Terraform workspace metadata missing for %s", infrastructure.server_name)
            raise DeploymentError("Terraform workspace metadata missing for cleanup")
        await self._runner.destroy(Path(workspace_dir))

    async def refresh(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        logger.info("Refreshing Terraform state for %s", infrastructure.server_name)
        workspace_dir = infrastructure.metadata.get("workspace_dir")
        if not workspace_dir:
            logger.error("Terraform workspace metadata missing for refresh of %s", infrastructure.server_name)
            raise DeploymentError("Terraform workspace metadata missing for refresh")
        outputs = await self._runner.refresh(Path(workspace_dir))
        server_id = outputs.get("server_id")
        public_ip = outputs.get("public_ipv4")
        if server_id is None or public_ip is None:
            logger.error("Terraform refresh missing required outputs for %s: server_id=%s public_ipv4=%s", infrastructure.server_name, server_id, public_ip)
            raise DeploymentError(
                "Terraform outputs are missing required Hetzner values (server_id/public_ipv4)"
            )
        metadata = dict(infrastructure.metadata)
        if outputs.get("firewall_id") is not None:
            metadata["firewall_id"] = str(outputs["firewall_id"])
        return ProvisionedInfrastructure(
            provider=infrastructure.provider,
            plan=infrastructure.plan,
            server_id=int(server_id),
            server_name=infrastructure.server_name,
            backend=infrastructure.backend,
            ssh_key_id=int(outputs["ssh_key_id"]) if outputs.get("ssh_key_id") is not None else infrastructure.ssh_key_id,
            ip=str(public_ip),
            status=str(outputs.get("server_status", infrastructure.status)),
            metadata=metadata,
        )

    async def _find_existing_ssh_key(self, public_key_content: str) -> int | None:
        """Return the Hetzner SSH key ID if the public key is already registered, else None."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.hetzner.cloud/v1/ssh_keys",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                if resp.status_code != 200:
                    return None
                normalized = public_key_content.strip()
                for key in resp.json().get("ssh_keys", []):
                    if key.get("public_key", "").strip() == normalized:
                        return key["id"]
        except Exception:
            pass
        return None

    async def plan(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> InfrastructurePlanResult:
        workspace_dir = infrastructure.metadata.get("workspace_dir")
        if not workspace_dir:
            raise DeploymentError("Terraform workspace metadata missing for plan")
        has_changes, output = await self._runner.plan(Path(workspace_dir))
        summary = "Terraform detected pending infrastructure changes." if has_changes else "Terraform reports no infrastructure changes."
        return InfrastructurePlanResult(
            provider=infrastructure.provider,
            backend=infrastructure.backend,
            has_changes=has_changes,
            summary=summary,
            raw_output=output,
            metadata={"workspace_dir": workspace_dir},
        )
