from dataclasses import dataclass, field
from enum import Enum


class TrafficTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class StackInfo:
    frontend: str | None = None
    frontend_version: str | None = None
    frontend_path: str | None = None  # relative path from repo root (e.g. "app/frontend")
    backend: str | None = None
    backend_language: str | None = None
    backend_version: str | None = None
    backend_path: str | None = None  # relative path from repo root (e.g. "app/backend")
    package_manager: str = "npm"  # npm, pnpm, yarn, bun
    # Deployment topology — set by AnalysisService
    deploy_topology: str = "single"  # "single" | "frontend_only" | "backend_only" (analysis service always overrides)
    build_output_dir: str | None = None  # detected client build output: "dist", "build", ".next"
    server_entry: str | None = None  # detected server entry point: "build/server.js"


@dataclass
class DatabaseInfo:
    type: str = ""
    orm: str | None = None
    has_migrations: bool = False


@dataclass
class RepoAnalysis:
    stack: StackInfo
    database: DatabaseInfo | None = None
    services: list[str] = field(default_factory=list)
    has_dockerfile: bool = False
    has_docker_compose: bool = False
    estimated_complexity: str = "low"
    env_vars_needed: list[str] = field(default_factory=list)
    env_vars_required: list[str] = field(default_factory=list)
    env_vars_optional: list[str] = field(default_factory=list)
    migration_command: str | None = None
    system_packages: list[str] = field(default_factory=list)
    python_version: str | None = None
    node_version: str | None = None
    go_version: str | None = None


@dataclass
class ValidationIssue:
    severity: str       # "error" | "warning"
    check: str          # "dep_file", "entry_point", "build_env", "port",
                        # "import_resolution", "symbol_existence", "copy_path"
    message: str
    file: str | None = None
    suggestion: str | None = None


@dataclass
class ValidationResult:
    """Result of config-vs-repo validation."""
    valid: bool                          # True if no errors (warnings OK)
    issues: list[ValidationIssue] = field(default_factory=list)
    corrected_configs: dict[str, str] = field(default_factory=dict)
    corrections_applied: list[str] = field(default_factory=list)


@dataclass
class ResourceEstimate:
    ram_mb: int = 512
    cpu_vcpu: float = 1.0
    storage_gb: int = 10
    needs_database: bool = False
    database_type: str | None = None
    database_ram_mb: int = 0
    traffic_tier: str = "low"
    notes: str = ""


@dataclass
class CostBreakdown:
    compute: float = 0.0
    database: float = 0.0
    storage: float = 0.0
    bandwidth: float = 0.0
    base_fee: float = 0.0


@dataclass
class ProviderRecommendation:
    provider: str = ""
    plan: str = ""
    monthly_cost: float = 0.0
    breakdown: CostBreakdown = field(default_factory=CostBreakdown)
    deployment_method: str = ""
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    best_for: str = ""


@dataclass
class ComparisonResult:
    recommendations: list[ProviderRecommendation] = field(default_factory=list)
    top_pick: str = ""
    top_pick_reason: str = ""
    savings_vs_aws: str = ""


@dataclass
class DeployResult:
    status: str = ""               # "deployed" | "failed"
    provider: str = ""
    url: str = ""                  # http://{ip}
    deployment_id: str = ""        # ce-hetzner-{8-char-hex}
    monthly_cost: float = 0.0
    ssh_access: str | None = None  # "ssh root@{ip}"
    next_steps: list[str] = field(default_factory=list)


@dataclass
class DeployDiagnostics:
    """Structured diagnostics returned when deployment fails."""
    phase: str = ""                    # docker_build | compose_up | migration | health_check
    service: str = ""                  # which compose service failed
    exit_code: int = 1
    summary: str = ""
    relevant_logs: list[str] = field(default_factory=list)
    dockerfile_used: str = ""
    generated_files: dict[str, str] = field(default_factory=dict)
    context: dict[str, str] = field(default_factory=dict)
    suggested_fixes: list[dict] = field(default_factory=list)
    retry_hint: str = ""
    # AI agent retry fields
    full_logs: str = ""
    dependency_files: dict[str, str] = field(default_factory=dict)
    suggested_docker_configs: dict[str, str] | None = None
    agent_instruction: str = ""
    retry_context: "RetryContext | None" = None


# Size constants for retry diagnostics
MAX_FULL_LOG_SIZE = 50_000
MAX_DEP_FILE_SIZE = 10_000
MAX_LOCK_FILE_SIZE = 5_000


@dataclass
class RetryContext:
    """Tracks retry state between deploy attempts. Passed to/from the host LLM."""
    attempt: int = 1
    max_retries: int = 3
    server_id: int | None = None
    server_ip: str | None = None
    deployment_id: str | None = None
    ssh_key_path: str | None = None
    infra_backend: str = "api"
    infra_metadata: dict[str, str] = field(default_factory=dict)
    previous_errors: list[str] = field(default_factory=list)
    previous_fixes: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "RetryContext":
        """Deserialize from a plain dict, coercing types and ignoring unknown keys."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known_fields}
        # Coerce int fields that may arrive as strings from JSON
        for int_field in ("attempt", "max_retries"):
            if int_field in filtered and filtered[int_field] is not None:
                filtered[int_field] = int(filtered[int_field])
        if "server_id" in filtered and filtered["server_id"] is not None:
            filtered["server_id"] = int(filtered["server_id"])
        return cls(**filtered)


@dataclass
class PreDeployCheckResult:
    """Result of pre-deployment validation checks."""
    can_proceed: bool = True
    warnings: list[str] = field(default_factory=list)
    blocking_errors: list[str] = field(default_factory=list)
    missing_env_vars: list[str] = field(default_factory=list)
    unsupported_services: list[str] = field(default_factory=list)
    existing_deployment: str | None = None
    existing_ip: str | None = None
    suggestion: str | None = None


@dataclass
class ResourceUsage:
    cpu_usage_percent: float = 0.0
    ram_usage_percent: float = 0.0
    ram_used_mb: int = 0
    ram_total_mb: int = 0
    disk_usage_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0


@dataclass
class WebhookResult:
    status: str = ""               # "connected" | "disconnected"
    deployment_id: str = ""
    webhook_url: str = ""          # http://{ip}:9867/webhook
    github_repo: str = ""          # owner/repo
    branch: str = "main"
    message: str = ""


@dataclass
class HealthStatus:
    status: str = ""               # "healthy" | "degraded" | "down"
    uptime: str = ""
    resources: ResourceUsage = field(default_factory=ResourceUsage)
    monthly_cost: float = 0.0
    assessment: str = ""
    alerts: list[str] = field(default_factory=list)
