# ComputeEdge

An MCP server for Claude Code that analyzes repositories, compares cloud provider pricing, and deploys applications to the cloud — all from your editor. Supports deploying any web app (FastAPI, Next.js, Express, etc.) and nanoclaw agent frameworks.

## What it does

| Tool | Description |
|------|-------------|
| `analyze_repo` | Detect tech stack, database, and services in any repo |
| `estimate_resources` | Estimate CPU, RAM, and storage requirements |
| `compare_providers` | Compare Hetzner, DigitalOcean, Railway, and AWS pricing |
| `generate_configs` | Generate Dockerfiles, docker-compose, and nginx configs |
| `deploy` | Provision a server and deploy your app (Hetzner or DigitalOcean) |
| `redeploy` | Update code on an existing deployment |
| `monitor` | Check health and resource usage of deployments |
| `show_infra` | View infrastructure details for a deployment |
| `destroy_deployment` | Tear down a deployment |

## Setup

### Option A: Run locally (stdio)

Clone and install:

```bash
git clone https://github.com/baadalAI/public-mcp.git
cd public-mcp
uv venv && uv pip install -e ".[dev]"
```

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "computeedge": {
      "command": "/path/to/public-mcp/.venv/bin/python",
      "args": ["-m", "computeedge.server"],
      "env": {
        "PYTHONPATH": "/path/to/public-mcp/src"
      }
    }
  }
}
```

Replace `/path/to/public-mcp` with the actual path where you cloned the repo.

In local mode there is no authentication — it runs as a single user. You pass your provider API token (Hetzner or DigitalOcean) directly in each deploy call.

### Option B: Run locally with Docker (stdio)

```bash
docker build -t computeedge .
```

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "computeedge": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "computeedge"]
    }
  }
}
```

### Option C: Deploy remotely (SSE, multi-tenant)

For teams or shared access, deploy the server with SSE transport:

```bash
git clone https://github.com/baadalAI/public-mcp.git
cd public-mcp
docker compose up -d
```

This starts the MCP server on port 8080 with bearer token authentication and a self-service registration page.

**Register users via the web UI:**

Visit `http://your-server-ip:8080/register` — users enter their name and email to get an API key. Keys are shown once and cannot be retrieved later.

**Or create API keys via CLI:**

```bash
docker compose exec computeedge computeedge-admin create-user --name "alice"
# Prints: API key: ce_xxxxxxx...
```

**Connect from Claude Code:**

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "computeedge": {
      "type": "sse",
      "url": "http://your-server-ip:8080/sse",
      "headers": {
        "Authorization": "Bearer ce_your-api-key-here"
      }
    }
  }
}
```

Each user passes their own provider API token (Hetzner or DigitalOcean) per-request in tool calls — tokens are never stored server-side. Deployment state is isolated per user.

## Usage

### Analyze a repo

```
Analyze https://github.com/user/repo
```

Returns detected stack (Next.js, FastAPI, etc.), database, services, and environment variables.

### Compare cloud costs

```
Compare providers for https://github.com/user/repo
```

Shows pricing across Hetzner, DigitalOcean, Railway, and AWS with recommendations.

### Deploy a web app

```
Deploy https://github.com/user/repo to Hetzner with provider_token="your-hetzner-token"
```

```
Deploy https://github.com/user/repo to DigitalOcean with provider_token="your-do-token" provider="digitalocean"
```

Provisions a VPS/Droplet, generates Docker configs, deploys your app, and returns the live URL. The `generate_configs` tool is called first to produce Dockerfiles — Claude reviews and augments them before deploying.

### Deploy nanoclaw

Deploy a nanoclaw agent framework instance — see [docs/nanoclaw-deployment.md](docs/nanoclaw-deployment.md) for the full guide.

**Vanilla nanoclaw (no repo needed):**

```
deploy(provider_token="your-hetzner-token", stack="nanoclaw", env_vars={"ANTHROPIC_API_KEY": "sk-ant-xxx", "TELEGRAM_BOT_TOKEN": "123:ABC"})
```

**Your nanoclaw fork:**

```
deploy(provider_token="your-hetzner-token", repo_path="https://github.com/user/my-nanoclaw.git", env_vars={"ANTHROPIC_API_KEY": "sk-ant-xxx"})
```

**Local nanoclaw workspace (installed via npm):**

```
deploy(provider_token="your-hetzner-token", repo_path="/path/to/my-agents", env_vars={"ANTHROPIC_API_KEY": "sk-ant-xxx"})
```

Nanoclaw is deployed natively on the VM (not inside Docker) with Node.js 22, Docker for agent containers, and a systemd service. Auto-detects nanoclaw workspaces from directory structure.

### Monitor

```
Monitor deployment ce-hetzner-abc12345
Monitor deployment ce-digitalocean-abc12345
```

Returns CPU, RAM, disk usage, uptime, and health status.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPUTEEDGE_TRANSPORT` | `stdio` | Transport mode: `stdio` or `sse` |
| `COMPUTEEDGE_DB_PATH` | `~/.computeedge/computeedge.db` | SQLite database path |
| `FASTMCP_HOST` | `0.0.0.0` | SSE server bind host |
| `FASTMCP_PORT` | `8080` | SSE server bind port |

## Architecture

```
MCP Tools (thin wrappers) -> Services (business logic) -> Providers / Config / State
```

- **Tools** — Factory functions that handle input validation and error catching
- **Services** — AnalysisService, PricingService, DeploymentService, MonitoringService
- **Providers** — Cloud API clients (Hetzner and DigitalOcean REST APIs)
- **Config** — YAML-based stack detection patterns and provider pricing
- **State** — SQLite database with per-user deployment isolation
- **Auth** — Bearer token middleware (SSE mode), bcrypt-hashed API keys, self-service registration

## Multi-tenancy

- **Auth**: Bearer token API keys via registration page or `computeedge-admin create-user`
- **State isolation**: SQLite with per-user deployment scoping
- **Provider tokens**: Passed per-request (never stored server-side)
- **Registration**: Self-service at `/register` (SSE mode only)
- **Stdio mode**: No auth, single local user

## Adding a new stack

Add detection patterns to `src/computeedge/config/stacks.yaml`. No code changes needed.

## Adding a provider

1. Add pricing data to `src/computeedge/config/providers.yaml`
2. Add provider info to `PROVIDER_INFO` in `src/computeedge/services/pricing.py`
3. Create a REST API client in `src/computeedge/providers/<provider>.py`
4. Create an infrastructure provisioner in `src/computeedge/services/infra/<provider>.py`
5. Add provider branching in `server.py` factory functions

## Testing

```bash
uv run pytest tests/ -v                        # All unit tests
uv run pytest tests/ -v -k "test_name"         # Single test
uv run pytest tests/ -v -m integration          # Integration tests (needs tokens)
```

## License

[MIT](LICENSE) © 2026 Ashleyn Castelino and Keshav Dalmia
