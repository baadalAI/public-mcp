# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ComputeEdge is an MCP server that analyzes repos, compares cloud deployment pricing, and deploys to Hetzner or DigitalOcean. It runs inside Claude Code/Cursor — no separate dashboard.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest tests/ -v
```

## Commands

```bash
uv run pytest tests/ -v                        # All unit tests
uv run pytest tests/test_analysis_service.py -v # Single test file
uv run pytest tests/ -v -k "test_detect_nextjs" # Single test by name
uv run pytest tests/ -v -m integration          # Integration tests (needs COMPUTEEDGE_HETZNER_TOKEN)
```

No linter or formatter is configured yet.

## Architecture

```
MCP Tools (thin wrappers) → Services (business logic) → Providers / Config / State
```

- `server.py` — FastMCP entry point. Registers tools, wires services. Provider-branching factory functions (`_make_deployment_service`, `_make_infra_service`) select Hetzner or DigitalOcean provisioner based on `provider` parameter.
- `tools/` — Factory functions (`make_*_tool`) that return async callables. Handle git URL cloning, error catching, and dict conversion via `asdict()`. Always return `{"error": ...}` on failure, never raise. `generate_configs` returns analysis + rendered configs for LLM augmentation before deploy.
- `services/` — AnalysisService (stack detection), PricingService (cost comparison), DeploymentService (provider-agnostic orchestration), MonitoringService (SSH health checks), InfrastructureLifecycleService (show/refresh/plan/destroy via provisioner abstraction).
- `services/infra/` — Provisioner abstraction layer. `base.py` defines `InfrastructureProvisioner` ABC and `ProvisionedInfrastructure` dataclass. `hetzner.py` wraps the Hetzner REST API client. `digitalocean.py` wraps the DigitalOcean REST API client (also manages firewalls). `terraform.py` wraps `TerraformRunner`. `terraform_runner.py` shells out to the `terraform` binary, manages per-deployment workspaces under `~/.computeedge/terraform/`, and writes `terraform.tfvars.json`.
- `terraform/hetzner_vm/` — Terraform module used by `TerraformRunner`. Creates `hcloud_ssh_key`, `hcloud_firewall` (ICMP + TCP 22/80/443), and `hcloud_server` with `firewall_ids` set at creation time (ensures firewall is applied before first boot). Provider version pinned to `~> 1.49`.
- `providers/hetzner.py` — Hetzner REST API client. Handles server CRUD, SSH key upload, polling for ready state.
- `providers/digitalocean.py` — DigitalOcean REST API client. Handles droplet CRUD, SSH key upload, firewall management, polling for ready state.
- `config/stacks.yaml` — Framework detection patterns (file existence, content contains/excludes). Adding a new stack requires only YAML changes.
- `config/providers.yaml` — Hardcoded plan pricing. Updated monthly; real-time API fetching is planned for Phase 3.
- `state/manager.py` — Persists deployment metadata to `~/.computeedge/deployments.json`.
- `templates/` — Jinja2 templates for Dockerfiles (fastapi, express, nextjs), docker-compose, nginx.
- `models.py` — Dataclasses: RepoAnalysis, StackInfo, DeployResult, HealthStatus, ComparisonResult, etc.
- `exceptions.py` — Hierarchy rooted at `ComputeEdgeError` with specific subtypes (AnalysisError, DeploymentError, SSHError, etc.).

## Key patterns

**Tool factory pattern**: Each tool in `tools/` is a factory `make_*_tool(services) -> async fn`. The server calls the factory at registration time (or at call time for lazy-init tools like deploy).

**Config resolution**: `Config.get_provider_token()` checks env var `COMPUTEEDGE_{PROVIDER}_TOKEN` first, then `~/.computeedge/config.yaml` under `providers.<name>.api_token`.

**Deployment IDs**: Format is `ce-{provider}-{hex}` (e.g. `ce-hetzner-a1b2c3d4`, `ce-digitalocean-e5f6a7b8`).

**Provider selection**: Tools accept a `provider_token` parameter (generic, not provider-specific) and a `provider` parameter (`"hetzner"` or `"digitalocean"`). Server.py factory functions branch on provider to create the right client + provisioner.

**Infrastructure backends**: For Hetzner, two provisioners exist behind `InfrastructureProvisioner`. `backend="api"` uses the Hetzner REST client directly (default). `backend="terraform"` uses `TerraformRunner` + the `terraform/hetzner_vm/` module. DigitalOcean currently supports API backend only. Terraform workspaces are stored in `~/.computeedge/terraform/{deployment_id}/` and workspace path is saved in `infra_metadata.workspace_dir` in state.

**LLM-assisted config augmentation**: `generate_configs` returns rendered templates + analysis. The calling LLM reviews, augments (adds missing build steps, env vars, system deps), and passes modifications back via `deploy(docker_configs={...})`. Templates are the reliable base; the LLM handles the long tail.

**Running tests**: Use `.venv/bin/python -m pytest` directly — `uv run pytest` can time out on venv metadata reads.

## Testing

Test fixtures in `tests/fixtures/` are real directory structures (sample_nextjs, sample_fastapi, sample_express, sample_fullstack, sample_monorepo). Tests use `@pytest.mark.asyncio` (auto mode). Integration tests are marked `@pytest.mark.integration` and skipped by default.

## Adding a new stack

Add detection patterns to `src/computeedge/config/stacks.yaml`. No code changes needed.

## Adding a provider

1. Add pricing data to `src/computeedge/config/providers.yaml`
2. Add provider info to `PROVIDER_INFO` in `src/computeedge/services/pricing.py`
3. Create a REST API client in `src/computeedge/providers/<provider>.py`
4. Create an infrastructure provisioner in `src/computeedge/services/infra/<provider>.py` implementing `InfrastructureProvisioner`
5. Add provider branching in `server.py` factory functions (`_make_deployment_service`, `_make_infra_service`)
6. Add the provider to `SUPPORTED_PROVIDERS` in `tools/deploy.py`

## Deploying (Local / stdio)

For local single-user mode, users pass their provider token (Hetzner or DigitalOcean) directly as the `provider_token` tool parameter on each deploy call. No server-side credential storage.

## Docker Deployment

### Local (stdio mode)
```bash
docker build -t computeedge .
docker run -i computeedge
```

### Remote (SSE mode, multi-tenant)
```bash
# Start the server
docker compose up -d

# Create API keys for users
docker compose exec computeedge computeedge-admin create-user --name "alice"

# Users connect via SSE with their API key and pass their own provider token per-request
```

### Environment variables
| Variable | Default | Description |
|----------|---------|-------------|
| `COMPUTEEDGE_TRANSPORT` | `stdio` | Transport mode: `stdio` or `sse` |
| `COMPUTEEDGE_DB_PATH` | `~/.computeedge/computeedge.db` | SQLite database path |
| `FASTMCP_HOST` | `0.0.0.0` | SSE server bind host |
| `FASTMCP_PORT` | `8080` | SSE server bind port |

## Multi-Tenancy

- **Auth**: Bearer token API keys, managed via `computeedge-admin create-user`
- **State isolation**: SQLite with per-user deployment scoping. Users cannot see or modify other users' deployments.
- **Provider tokens**: Passed per-request via `provider_token` tool parameter (never stored server-side). Each user deploys to their own Hetzner or DigitalOcean account.
- **Stdio mode**: Skips auth, uses a default local user. Single-user, same as before.

## Known Issues / Setup Gotchas

### MCP server configuration

The `.mcp.json` uses the venv Python directly with `PYTHONPATH` to avoid `uv run` re-sync overhead. If `uv run` hangs due to a `VIRTUAL_ENV` env var mismatch (e.g. Homebrew's framework dir), prepend `VIRTUAL_ENV= `.
