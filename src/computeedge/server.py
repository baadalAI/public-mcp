import asyncio
import logging
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from computeedge.auth import BearerTokenMiddleware, LOCAL_USER_ID
from computeedge.config.loader import Config, load_bundled_yaml
from computeedge.providers.digitalocean import DigitalOceanProvider
from computeedge.providers.hetzner import HetznerProvider
from computeedge.services.analysis import AnalysisService
from computeedge.services.deployment import DeploymentService
from computeedge.services.infra import (
    DigitalOceanInfrastructureProvisioner,
    HetznerInfrastructureProvisioner,
    HetznerTerraformProvisioner,
    TerraformRunner,
)
from computeedge.services.infrastructure import InfrastructureLifecycleService
from computeedge.services.monitoring import MonitoringService
from computeedge.services.pricing import PricingService
from computeedge.services.validation import ConfigValidator
from computeedge.state.database import Database
from computeedge.state.manager import StateManager
from computeedge.tools.analyze import make_analyze_tool
from computeedge.tools.compare import make_compare_tool
from computeedge.tools.deploy import make_deploy_tool, make_redeploy_tool
from computeedge.tools.estimate import make_estimate_tool
from computeedge.tools.generate_configs import make_generate_configs_tool
from computeedge.tools.infrastructure import (
    make_destroy_deployment_tool,
    make_plan_infra_tool,
    make_refresh_infra_tool,
    make_show_infra_tool,
)
from computeedge.tools.credentials import make_set_credentials_tool
from computeedge.tools.monitor import make_monitor_tool

_log_dir = Path(os.path.expanduser("~/.computeedge"))
_log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(_log_dir / "mcp.log")],
)

_allowed_hosts = os.environ.get("COMPUTEEDGE_ALLOWED_HOSTS", "")
if _allowed_hosts:
    from mcp.server.sse import TransportSecuritySettings
    _transport_security = TransportSecuritySettings(allowed_hosts=_allowed_hosts.split(","))
    app = FastMCP("computeedge", transport_security=_transport_security)
else:
    app = FastMCP("computeedge")

# Initialize config and services
_stacks_config = load_bundled_yaml("stacks.yaml")
_providers_config = load_bundled_yaml("providers.yaml")
_config = Config()

try:
    _system_deps_config = load_bundled_yaml("system_deps.yaml")
except Exception:
    _system_deps_config = None

_analysis_service = AnalysisService(_stacks_config, system_deps_config=_system_deps_config)
_pricing_service = PricingService(_providers_config)
_validator = ConfigValidator()

# Database + StateManager
_db_path = Path(os.environ.get("COMPUTEEDGE_DB_PATH", str(Path.home() / ".computeedge" / "computeedge.db")))
_db = Database(_db_path)
_state = StateManager(_db)

_monitoring_service = MonitoringService(_state)


# --- Per-request service factories ---


def _make_deployment_service(provider_token: str, provider: str = "hetzner") -> DeploymentService:
    """Create a DeploymentService with the caller's provider token."""
    config = Config()

    if provider == "digitalocean":
        http_client = httpx.AsyncClient()
        do_provider = DigitalOceanProvider(provider_token, http_client)
        provisioner = DigitalOceanInfrastructureProvisioner(do_provider)
        return DeploymentService(
            provider=None,
            state=_state,
            stacks_config=_stacks_config,
            provisioner=provisioner,
            validator=_validator,
            provider_name="digitalocean",
        )

    # Hetzner (default)
    backend = config.get_provider_setting("hetzner", "deployment_backend", "api")

    if backend == "terraform":
        terraform_binary = config.get_default("terraform_binary", "terraform")
        workspace_root = config.get_default(
            "terraform_workspace_root",
            str(Path.home() / ".computeedge" / "terraform"),
        )
        runner = TerraformRunner(
            binary=str(terraform_binary),
            workspace_root=Path(workspace_root),
        )
        provisioner = HetznerTerraformProvisioner(
            token=provider_token,
            runner=runner,
            location=str(config.get_provider_setting("hetzner", "location", "nbg1")),
            image=str(config.get_provider_setting("hetzner", "image", "ubuntu-24.04")),
        )
        return DeploymentService(
            provider=None,
            state=_state,
            stacks_config=_stacks_config,
            provisioner=provisioner,
            validator=_validator,
            provider_name="hetzner",
        )

    http_client = httpx.AsyncClient()
    hetzner = HetznerProvider(provider_token, http_client)
    provisioner = HetznerInfrastructureProvisioner(hetzner)
    return DeploymentService(
        provider=None,
        state=_state,
        stacks_config=_stacks_config,
        provisioner=provisioner,
        validator=_validator,
        provider_name="hetzner",
    )


def _make_infra_service(provider_token: str, backend: str, provider: str = "hetzner") -> InfrastructureLifecycleService:
    """Create an InfrastructureLifecycleService with the caller's token."""
    config = Config()

    if provider == "digitalocean":
        http_client = httpx.AsyncClient()
        do_provider = DigitalOceanProvider(provider_token, http_client)
        provisioner = DigitalOceanInfrastructureProvisioner(do_provider)
        return InfrastructureLifecycleService(_state, provisioner)

    # Hetzner (default)
    if backend == "terraform":
        terraform_binary = config.get_default("terraform_binary", "terraform")
        workspace_root = config.get_default(
            "terraform_workspace_root",
            str(Path.home() / ".computeedge" / "terraform"),
        )
        runner = TerraformRunner(
            binary=str(terraform_binary),
            workspace_root=Path(workspace_root),
        )
        provisioner = HetznerTerraformProvisioner(
            token=provider_token,
            runner=runner,
            location=str(config.get_provider_setting("hetzner", "location", "nbg1")),
            image=str(config.get_provider_setting("hetzner", "image", "ubuntu-24.04")),
        )
    else:
        http_client = httpx.AsyncClient()
        hetzner = HetznerProvider(provider_token, http_client)
        provisioner = HetznerInfrastructureProvisioner(hetzner)

    return InfrastructureLifecycleService(_state, provisioner)


# --- Stateless tool functions ---

_analyze_fn = make_analyze_tool(_analysis_service)
_estimate_fn = make_estimate_tool(_analysis_service)
_compare_fn = make_compare_tool(_analysis_service, _pricing_service)


@app.tool()
async def analyze_repo(repo_path: str) -> dict:
    """Analyze a local or remote git repository and detect the tech stack,
    database, and services used."""
    return await _analyze_fn(repo_path)


@app.tool()
async def generate_configs(repo_path: str, env_vars: dict | None = None, topology: str | None = None) -> dict:
    """Analyze a repo and generate deployment configs (Dockerfiles, docker-compose,
    nginx, .env). Review and augment the returned configs, then pass modifications
    to deploy() via the docker_configs parameter.

    Args:
        topology: Override auto-detected topology. One of "single", "split",
                  "frontend_only", "backend_only". If None, uses auto-detection."""
    # generate_configs only renders templates -- works without a Hetzner token.
    svc = DeploymentService(
        provider=None,
        state=_state,
        stacks_config=_stacks_config,
        validator=_validator,
    )
    fn = make_generate_configs_tool(_analysis_service, svc, _validator)
    return await fn(repo_path, env_vars=env_vars, topology=topology)


@app.tool()
async def estimate_resources(
    repo_path: str, expected_traffic: str = "low"
) -> dict:
    """Estimate CPU, RAM, and storage requirements for deploying this app."""
    return await _estimate_fn(repo_path, expected_traffic)


@app.tool()
async def compare_providers(
    repo_path: str | None = None,
    stack: str | None = None,
    estimated_ram_mb: int = 512,
    estimated_cpu_vcpu: float = 1,
    estimated_storage_gb: int = 10,
    needs_database: bool = False,
    database_type: str = "postgres",
    expected_traffic: str = "low",
) -> dict:
    """Compare cloud provider pricing for deploying this app.
    Returns ranked recommendations with cost breakdowns."""
    return await _compare_fn(
        repo_path=repo_path,
        stack=stack,
        estimated_ram_mb=estimated_ram_mb,
        estimated_cpu_vcpu=estimated_cpu_vcpu,
        estimated_storage_gb=estimated_storage_gb,
        needs_database=needs_database,
        database_type=database_type,
        expected_traffic=expected_traffic,
    )


_set_credentials_fn = make_set_credentials_tool(_config)


@app.tool()
async def set_credentials(
    provider: str | None = None,
    token: str | None = None,
    key: str | None = None,
    value: str | None = None,
) -> dict:
    """Save a provider API token or app env var to ~/.computeedge/config.yaml.

    For provider tokens: set_credentials(provider="hetzner", token="your-token")
    For app env vars:    set_credentials(key="DATABASE_URL", value="postgres://...")

    Saved env vars are automatically included in every deploy."""
    return await _set_credentials_fn(provider=provider, token=token, key=key, value=value)


# --- Deployment tools (require provider_token) ---


@app.tool()
async def deploy(
    provider_token: str,
    repo_path: str | None = None,
    stack: str | None = None,
    provider: str = "hetzner",
    plan: str | None = None,
    env_vars: dict | None = None,
    docker_configs: dict | None = None,
    force_new: bool = False,
    override_dockerfile: bool = False,
    retry_context: dict | None = None,
) -> dict:
    """Deploy the application to a cloud provider.
    Requires the user's provider API token.
    For nanoclaw: pass stack='nanoclaw' without repo_path to deploy vanilla nanoclaw.
    Or pass repo_path to a nanoclaw fork for customized deployments."""
    user_id = LOCAL_USER_ID
    deploy_fn = make_deploy_tool(_analysis_service, _make_deployment_service, _state, config=_config)
    return await deploy_fn(
        provider_token, user_id, repo_path=repo_path, stack=stack,
        provider=provider, plan=plan,
        env_vars=env_vars, docker_configs=docker_configs, force_new=force_new,
        override_dockerfile=override_dockerfile, retry_context=retry_context,
    )


@app.tool()
async def redeploy(
    deployment_id: str,
    provider_token: str,
    env_vars: dict | None = None,
) -> dict:
    """Update code on an existing deployment without creating a new server.
    Requires the user's provider API token."""
    user_id = LOCAL_USER_ID
    redeploy_fn = make_redeploy_tool(_analysis_service, _make_deployment_service, _state, config=_config)
    return await redeploy_fn(deployment_id, provider_token, user_id, env_vars=env_vars)


@app.tool()
async def monitor(deployment_id: str) -> dict:
    """Check the health and resource usage of a deployed application."""
    user_id = LOCAL_USER_ID
    monitor_fn = make_monitor_tool(_monitoring_service, _state)
    return await monitor_fn(deployment_id, user_id)


@app.tool()
async def show_infra(deployment_id: str, provider_token: str) -> dict:
    """Show stored infrastructure details for a deployment.
    Requires the user's provider API token."""
    user_id = LOCAL_USER_ID
    try:
        state = await _state.get(deployment_id, user_id)
        if state is None:
            return {"error": f"Deployment not found: {deployment_id}"}
        provider = state.get("provider", "hetzner")
        backend = state.get("infra_backend") or _config.get_provider_setting(provider, "deployment_backend", "api")
        svc = _make_infra_service(provider_token, str(backend), provider=provider)
    except Exception as e:
        return {"error": str(e)}
    fn = make_show_infra_tool(svc)
    return await fn(deployment_id, user_id)


@app.tool()
async def refresh_infra(deployment_id: str, provider_token: str) -> dict:
    """Refresh live infrastructure state from the provider and update local state.
    Requires the user's provider API token."""
    user_id = LOCAL_USER_ID
    try:
        state = await _state.get(deployment_id, user_id)
        if state is None:
            return {"error": f"Deployment not found: {deployment_id}"}
        provider = state.get("provider", "hetzner")
        backend = state.get("infra_backend") or _config.get_provider_setting(provider, "deployment_backend", "api")
        svc = _make_infra_service(provider_token, str(backend), provider=provider)
    except Exception as e:
        return {"error": str(e)}
    fn = make_refresh_infra_tool(svc)
    return await fn(deployment_id, user_id)


@app.tool()
async def plan_infra(deployment_id: str, provider_token: str) -> dict:
    """Show infrastructure drift or planned changes for a deployment.
    Requires the user's provider API token."""
    user_id = LOCAL_USER_ID
    try:
        state = await _state.get(deployment_id, user_id)
        if state is None:
            return {"error": f"Deployment not found: {deployment_id}"}
        provider = state.get("provider", "hetzner")
        backend = state.get("infra_backend") or _config.get_provider_setting(provider, "deployment_backend", "api")
        svc = _make_infra_service(provider_token, str(backend), provider=provider)
    except Exception as e:
        return {"error": str(e)}
    fn = make_plan_infra_tool(svc)
    return await fn(deployment_id, user_id)


@app.tool()
async def destroy_deployment(deployment_id: str, provider_token: str) -> dict:
    """Destroy infrastructure for a deployment and remove it from local state.
    Requires the user's provider API token."""
    user_id = LOCAL_USER_ID
    try:
        state = await _state.get(deployment_id, user_id)
        if state is None:
            return {"error": f"Deployment not found: {deployment_id}"}
        provider = state.get("provider", "hetzner")
        backend = state.get("infra_backend") or _config.get_provider_setting(provider, "deployment_backend", "api")
        svc = _make_infra_service(provider_token, str(backend), provider=provider)
    except Exception as e:
        return {"error": str(e)}
    fn = make_destroy_deployment_tool(svc)
    return await fn(deployment_id, provider_token, user_id)


# --- Startup and transport ---


async def _init_db():
    await _db.initialize()


async def run():
    await _init_db()
    transport = os.environ.get("COMPUTEEDGE_TRANSPORT", "stdio")

    if transport == "sse":
        import secrets as _secrets

        import uvicorn
        from starlette.responses import HTMLResponse, JSONResponse
        from starlette.routing import Route

        _register_template = (
            Path(__file__).parent / "templates" / "register.html"
        ).read_text()

        async def health(request):
            return JSONResponse({"status": "ok"})

        async def register_page(request):
            return HTMLResponse(_register_template)

        async def register_api(request):
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)

            name = (body.get("name") or "").strip()
            email = (body.get("email") or "").strip()

            if not name:
                return JSONResponse({"error": "Name is required"}, status_code=400)
            if not email or "@" not in email:
                return JSONResponse({"error": "Valid email is required"}, status_code=400)

            api_key = f"ce_{_secrets.token_urlsafe(32)}"
            user_id = await _db.create_user(name, api_key, email=email)

            return JSONResponse(
                {"user_id": user_id, "api_key": api_key, "name": name},
                status_code=201,
            )

        starlette_app = app.sse_app()
        starlette_app.routes.insert(0, Route("/health", health))
        starlette_app.routes.insert(0, Route("/register", register_page))
        starlette_app.routes.insert(0, Route("/api/register", register_api, methods=["POST"]))
        starlette_app.add_middleware(BearerTokenMiddleware, db=_db)
        host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        port = int(os.environ.get("FASTMCP_PORT", "8080"))
        config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    else:
        await app.run_stdio_async()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
