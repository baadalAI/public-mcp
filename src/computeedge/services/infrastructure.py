from dataclasses import dataclass, field

from computeedge.exceptions import DeploymentError
from computeedge.services.infra.base import (
    InfrastructurePlanResult,
    InfrastructureProvisioner,
    ProvisionedInfrastructure,
)
from computeedge.state.manager import StateManager
from computeedge.utils.logger import get_logger

logger = get_logger("infrastructure")


@dataclass
class InfrastructureInfo:
    deployment_id: str
    provider: str
    backend: str
    plan: str
    server_id: int
    ip: str | None = None
    status: str = ""
    repo_path: str | None = None
    deployed_at: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class InfrastructureRefreshResult:
    deployment_id: str
    provider: str
    backend: str
    ip: str | None = None
    status: str = ""
    changed_fields: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class InfrastructureDestroyResult:
    deployment_id: str
    provider: str
    backend: str
    status: str
    message: str


class InfrastructureLifecycleService:
    def __init__(self, state: StateManager, provisioner: InfrastructureProvisioner):
        self._state = state
        self._provisioner = provisioner

    async def _load_state(self, deployment_id: str, user_id: int) -> dict:
        state = await self._state.get(deployment_id, user_id)
        if state is None:
            logger.error("Deployment not found: %s", deployment_id)
            raise DeploymentError(f"Deployment not found: {deployment_id}")
        return state

    def _build_infrastructure(self, deployment_id: str, state: dict) -> ProvisionedInfrastructure:
        server_id = state.get("server_id")
        if server_id is None:
            logger.error("Deployment %s is missing server_id in state", deployment_id)
            raise DeploymentError(f"Deployment {deployment_id} is missing server_id in state")
        return ProvisionedInfrastructure(
            provider=state.get("provider", "hetzner"),
            plan=state.get("plan", ""),
            server_id=int(server_id),
            server_name=deployment_id,
            backend=state.get("infra_backend", "api"),
            ip=state.get("ip"),
            status=state.get("status", ""),
            metadata=dict(state.get("infra_metadata", {})),
        )

    async def show(self, deployment_id: str, user_id: int) -> InfrastructureInfo:
        logger.info("Showing infrastructure for %s", deployment_id)
        state = await self._load_state(deployment_id, user_id)
        infrastructure = self._build_infrastructure(deployment_id, state)
        return InfrastructureInfo(
            deployment_id=deployment_id,
            provider=infrastructure.provider,
            backend=infrastructure.backend,
            plan=infrastructure.plan,
            server_id=infrastructure.server_id,
            ip=infrastructure.ip,
            status=infrastructure.status,
            repo_path=state.get("repo_path"),
            deployed_at=state.get("deployed_at"),
            metadata=dict(infrastructure.metadata),
        )

    async def refresh(self, deployment_id: str, user_id: int) -> InfrastructureRefreshResult:
        logger.info("Refreshing infrastructure for %s", deployment_id)
        state = await self._load_state(deployment_id, user_id)
        infrastructure = self._build_infrastructure(deployment_id, state)
        refreshed = await self._provisioner.refresh(infrastructure)

        changed_fields = []
        if refreshed.ip != state.get("ip"):
            changed_fields.append("ip")
        if refreshed.status != state.get("status", ""):
            changed_fields.append("status")
        if dict(refreshed.metadata) != dict(state.get("infra_metadata", {})):
            changed_fields.append("infra_metadata")

        await self._state.update(
            deployment_id,
            user_id,
            {
                "ip": refreshed.ip,
                "status": refreshed.status,
                "infra_backend": refreshed.backend,
                "infra_metadata": dict(refreshed.metadata),
                "server_id": refreshed.server_id,
            },
        )

        return InfrastructureRefreshResult(
            deployment_id=deployment_id,
            provider=refreshed.provider,
            backend=refreshed.backend,
            ip=refreshed.ip,
            status=refreshed.status,
            changed_fields=changed_fields,
            metadata=dict(refreshed.metadata),
        )

    async def plan(self, deployment_id: str, user_id: int) -> InfrastructurePlanResult:
        logger.info("Planning infrastructure for %s", deployment_id)
        state = await self._load_state(deployment_id, user_id)
        infrastructure = self._build_infrastructure(deployment_id, state)
        return await self._provisioner.plan(infrastructure)

    async def destroy(self, deployment_id: str, user_id: int) -> InfrastructureDestroyResult:
        logger.info("Destroying infrastructure for %s", deployment_id)
        state = await self._load_state(deployment_id, user_id)
        infrastructure = self._build_infrastructure(deployment_id, state)
        await self._provisioner.destroy(infrastructure)
        await self._state.remove(deployment_id, user_id)
        return InfrastructureDestroyResult(
            deployment_id=deployment_id,
            provider=infrastructure.provider,
            backend=infrastructure.backend,
            status="destroyed",
            message=f"Destroyed infrastructure for {deployment_id}.",
        )
