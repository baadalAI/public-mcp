from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class ProvisionedInfrastructure:
    provider: str
    plan: str
    server_id: int
    server_name: str
    backend: str = "api"
    ssh_key_id: int | None = None
    ip: str | None = None
    status: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def with_ip(self, ip: str, status: str = "running") -> "ProvisionedInfrastructure":
        return replace(self, ip=ip, status=status)


@dataclass(frozen=True)
class InfrastructurePlanResult:
    provider: str
    backend: str
    has_changes: bool
    summary: str
    raw_output: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class InfrastructureProvisioner(ABC):
    @abstractmethod
    async def provision(
        self,
        deployment_id: str,
        plan: str,
        public_key_content: str,
        user_data: str | None = None,
    ) -> ProvisionedInfrastructure:
        """Create provider infrastructure and return a tracked handle."""

    @abstractmethod
    async def wait_until_ready(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        """Wait for provisioned infrastructure to become reachable."""

    @abstractmethod
    async def cleanup(self, infrastructure: ProvisionedInfrastructure) -> None:
        """Delete created infrastructure for a failed deployment."""

    @abstractmethod
    async def destroy(self, infrastructure: ProvisionedInfrastructure) -> None:
        """Delete infrastructure for an explicit teardown request."""

    @abstractmethod
    async def refresh(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        """Read current provider state and return an updated infrastructure snapshot."""

    @abstractmethod
    async def plan(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> InfrastructurePlanResult:
        """Return a plan or drift summary for the infrastructure."""
