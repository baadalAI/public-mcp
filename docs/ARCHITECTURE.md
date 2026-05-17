# ComputeEdge System Architecture

## Overview

ComputeEdge is an MCP (Model Context Protocol) server that runs inside Claude Code or Cursor. It analyzes repositories, compares cloud deployment pricing, and deploys applications to Hetzner Cloud — all without a separate dashboard.

```
+---------------------------+
|    Claude Code / Cursor   |
|  (MCP Client)             |
+------------+--------------+
             |  MCP Protocol (stdio)
             v
+---------------------------+
|    ComputeEdge MCP Server |
|  (FastMCP)                |
+------------+--------------+
             |
   +---------+---------+
   |         |         |
   v         v         v
 Tools   Services   Providers
```

---

## High-Level Architecture

ComputeEdge follows a three-layer architecture:

```
MCP Tools (thin wrappers)
        |
        v
Services (business logic)
        |
        v
Providers / Config / State
```

### Layer 1: MCP Tools

Thin wrappers registered with FastMCP. Each tool is produced by a factory function (`make_*_tool`) that returns an async callable. Tools handle:
- Git URL cloning and cleanup
- Error catching and serialization
- Dict conversion via `dataclasses.asdict()`
- Always return `{"error": ...}` on failure — never raise

### Layer 2: Services

Core business logic, stateless where possible:
- **AnalysisService** — Stack detection, resource estimation
- **PricingService** — Cost comparison across providers
- **DeploymentService** — Hetzner orchestration (server creation, Docker setup, migrations)
- **MonitoringService** — SSH-based health checks

### Layer 3: Providers / Config / State

- **HetznerProvider** — REST API client for Hetzner Cloud
- **Config** — Credential resolution (env vars -> config file)
- **StateManager** — Deployment metadata persistence to disk

---

## Directory Structure

```
src/computeedge/
├── server.py                    # FastMCP entry point, tool registration
├── models.py                    # Dataclasses (RepoAnalysis, DeployResult, etc.)
├── exceptions.py                # Exception hierarchy (ComputeEdgeError base)
│
├── tools/                       # MCP tool factories
│   ├── analyze.py               #   analyze_repo
│   ├── estimate.py              #   estimate_resources
│   ├── compare.py               #   compare_providers
│   ├── deploy.py                #   deploy, redeploy
│   ├── monitor.py               #   monitor
│   ├── credentials.py           #   set_credentials
│   └── install_skills.py        #   install_skills
│
├── services/                    # Business logic
│   ├── analysis.py              #   Stack detection, resource estimation
│   ├── pricing.py               #   Provider cost comparison
│   ├── deployment.py            #   Hetzner deployment orchestration
│   └── monitoring.py            #   Health checks via SSH
│
├── providers/
│   └── hetzner.py               # Hetzner Cloud REST API client
│
├── config/
│   ├── loader.py                # YAML loading, credential management
│   ├── stacks.yaml              # Framework detection patterns
│   └── providers.yaml           # Cloud provider pricing data
│
├── state/
│   └── manager.py               # Deployment state (deployments.json)
│
├── templates/                   # Jinja2 templates
│   ├── Dockerfile.fastapi.j2
│   ├── Dockerfile.express.j2
│   ├── Dockerfile.express-ts.j2
│   ├── Dockerfile.nextjs.j2
│   ├── Dockerfile.react.j2
│   ├── docker-compose.j2
│   └── nginx.conf.j2
│
├── utils/
│   ├── git.py                   # Git URL detection, shallow clone
│   ├── ssh.py                   # SSH client (asyncssh wrapper)
│   └── logger.py                # Logging setup
│
└── skills/
    └── computeedge-deploy/
        └── SKILL.md             # Claude Code skill definition
```

---

## Data Flow Diagrams

### Repository Analysis Flow

```
User: analyze_repo("/path/to/repo")
           |
           v
  +------------------+
  |  make_analyze_tool|
  |  (tools/analyze)  |
  +--------+---------+
           |
           v
  +------------------+
  | AnalysisService  |
  | .analyze()       |
  +--------+---------+
           |
    +------+------+------+------+
    |      |      |      |      |
    v      v      v      v      v
  Detect  Detect  Detect Detect Extract
  Front-  Back-   DB     Svcs   Env
  end     end                   Vars
    |      |      |      |      |
    v      v      v      v      v
  +----------------------------------+
  |         RepoAnalysis             |
  |  stack: StackInfo                |
  |  database: DatabaseInfo | None   |
  |  services: [redis, s3, ...]     |
  |  has_dockerfile: bool            |
  |  has_docker_compose: bool        |
  |  env_vars_required: [str]        |
  |  migration_command: str | None   |
  |  package_manager: str            |
  +----------------------------------+
```

Stack detection is declarative — patterns are defined in `stacks.yaml`:

```yaml
frontend:
  nextjs:
    indicators:
      - file: "next.config.js"
      - file: "next.config.mjs"
    version_file: "package.json"

backend:
  fastapi:
    indicators:
      - file: "requirements.txt"
        contains: "fastapi"
    language: python
```

Adding a new stack requires only YAML changes — no code modifications.

### Deployment Flow

```
User: deploy("/path/to/repo")
         |
         v
+------------------+
| make_deploy_tool |
| (tools/deploy)   |
+--------+---------+
         |
    (1) Clone if git URL
         |
    (2) Analyze repo
         |
    (3) Pre-deploy validation
         |   - Check for duplicate deployments (EC-13)
         |   - Validate required env vars (EC-8)
         |
    (4) If validation fails -> return PreDeployCheckResult
         |
         v
+--------------------+
| DeploymentService  |
| .deploy()          |
+--------+-----------+
         |
  +------+------+------+------+------+------+------+
  |      |      |      |      |      |      |      |
  v      v      v      v      v      v      v      v
Upload  Create Wait   SSH    Install Upload Upload  Run
SSH     Server for    Connect Docker  Repo   Docker  Compose
Key            Ready  (retry)         (tar)  Files   Up
  |      |      |      |      |      |      |      |
  |      v      v      |      |      |      |      v
  |   Hetzner  Hetzner |      |      |      |   Run
  |   API      API     |      |      |      |   Migrations
  |   (create) (poll)  |      |      |      |   (if detected)
  |      |      |      |      |      |      |      |
  +------+------+------+------+------+------+------+
         |
         v
  +------------------+
  | Save to State    |
  | Manager          |
  +------------------+
         |
         v
  +------------------+
  |   DeployResult   |
  |  status: deployed|
  |  url: http://IP  |
  |  ssh_access: ... |
  +------------------+
```

### Cost Comparison Flow

```
User: compare_providers(repo_path="/path")
         |
         v
   AnalysisService.analyze()
         |
         v
   AnalysisService.estimate_resources()
         |
         v
   +-------------------+
   |  PricingService    |
   |  .compare()        |
   +--------+----------+
            |
   +--------+--------+--------+
   |        |        |        |
   v        v        v        v
 Hetzner  Railway  Digital-  AWS
 plans    plans    Ocean     (baseline)
   |        |      plans       |
   v        v        v         v
 Match    Match    Match    Reference
 cheapest cheapest cheapest  costs
   |        |        |         |
   +--------+--------+---------+
            |
            v
   +--------------------+
   | ComparisonResult   |
   |  recommendations[] |
   |  top_pick_reasoning|
   |  savings_vs_aws    |
   +--------------------+
```

---

## Service Details

### AnalysisService (`services/analysis.py`)

**Responsibilities:**
- Scan repository files and detect tech stack
- Classify environment variables as required vs optional
- Detect package manager (npm/pnpm/yarn/bun) from lockfiles
- Detect database migration tools (alembic, prisma, django, knex, typeorm)
- Estimate resource requirements by traffic tier

**Key Methods:**
| Method | Input | Output |
|--------|-------|--------|
| `analyze(repo_path)` | Path to repo | `RepoAnalysis` |
| `estimate_resources(analysis, traffic)` | Analysis + tier | `ResourceEstimate` |

**Detection Strategy:**
1. Walk the directory tree, collect file names and partial contents
2. Match against `stacks.yaml` patterns (file existence, content contains/excludes)
3. First match wins for each category (frontend, backend, database)
4. Services detected by keyword presence in any file

### DeploymentService (`services/deployment.py`)

**Responsibilities:**
- Orchestrate full deployment to Hetzner Cloud
- Pre-validate deployments (missing env vars, duplicates)
- Generate Docker configs from Jinja2 templates
- Handle repo upload via git archive or tar with exclusions
- Run database migrations after deployment
- Support redeployment (code update without new server)

**Key Methods:**
| Method | Description |
|--------|-------------|
| `pre_deploy_check()` | Validate before creating infrastructure |
| `deploy()` | Full deployment: server + Docker + code |
| `redeploy()` | Update code on existing server |

**Upload Strategy:**
- **Git repos**: `git archive HEAD` (respects `.gitignore`, excludes untracked files)
- **Non-git dirs**: `tar` with exclusion patterns (`node_modules`, `.venv`, `dist`, `.git`, etc.)

**Template Selection:**
```
Backend:
  fastapi    -> Dockerfile.fastapi.j2
  express    -> Dockerfile.express.j2
  express+ts -> Dockerfile.express-ts.j2

Frontend:
  nextjs     -> Dockerfile.nextjs.j2
  react      -> Dockerfile.react.j2

All templates are package-manager-aware (npm/pnpm/yarn/bun)
```

### PricingService (`services/pricing.py`)

**Responsibilities:**
- Match resource requirements to cheapest plan per provider
- Calculate itemized costs (compute, database, storage, bandwidth)
- Compare savings vs AWS equivalents
- Rank providers and explain top pick

**Supported Providers:**
| Provider | Plans | Notes |
|----------|-------|-------|
| Hetzner | cx22, cx23, cx33, cax11, cpx11 | No managed DB — self-hosted |
| Railway | Hobby, Pro | Usage-based billing with base fee |
| DigitalOcean | basic_1gb, basic_2gb | Optional managed Postgres (+$15/mo) |

### MonitoringService (`services/monitoring.py`)

**Responsibilities:**
- SSH into deployed servers
- Check Docker container health via `docker stats`
- Monitor disk usage, system uptime
- Generate alerts based on thresholds

**Alert Thresholds:**
| Metric | Warning | Critical |
|--------|---------|----------|
| CPU | 80% | 95% |
| Memory | 85% | 95% |
| Disk | 80% | 90% |

---

## Configuration System

### Credential Resolution

```
1. Environment variable:  COMPUTEEDGE_HETZNER_TOKEN
            |
            v (not found?)
2. Config file:  ~/.computeedge/config.yaml
   providers:
     hetzner:
       api_token: <token>
            |
            v (not found?)
3. Return None (tools return helpful error message)
```

### Lazy-Init Pattern (server.py)

The deployment service uses lazy initialization to avoid requiring MCP reconnections after adding credentials:

```python
def _get_deployment_service() -> DeploymentService | None:
    # Re-reads config on each call until token found
    # Once found, caches the service instance
    if _deployment_resolved:
        return _deployment_service_cache
    token = Config().get_provider_token("hetzner")
    if not token:
        return None  # don't cache — retry next call
    # ... create and cache service
```

### State Persistence

Deployment metadata is stored at `~/.computeedge/deployments.json`:

```json
{
  "ce-hetzner-a1b2c3d4": {
    "provider": "hetzner",
    "server_id": 12345,
    "ip": "1.2.3.4",
    "plan": "cx23",
    "repo_path": "/Users/you/myapp",
    "normalized_repo_path": "/Users/you/myapp",
    "monthly_cost": 3.49,
    "deployed_at": "2026-03-24T12:00:00+00:00",
    "ssh_key_path": "~/.ssh/id_ed25519"
  }
}
```

---

## Exception Hierarchy

```
ComputeEdgeError (base)
├── AnalysisError           # Repo analysis failures
├── PricingError            # Cost calculation failures
├── DeploymentError         # Deployment failures
│   ├── .build_log          #   Docker build output
│   ├── .suggestion         #   Human-readable fix suggestion
│   └── .retry_hint         #   Retry guidance
├── ConfigError             # Credential/config issues
├── GitCloneError           # Git clone failures
├── SSHError                # SSH connection/command failures
└── ProviderAPIError        # Cloud provider API errors
```

All exceptions are caught by tool factories and converted to `{"error": str(e)}` dicts — the MCP client never sees raw exceptions.

---

## Deployment Lifecycle

```
         deploy()
            |
            v
  +-------------------+
  |  1. PRE-VALIDATE  |   Check for duplicates, missing env vars
  +--------+----------+
           |
           v
  +-------------------+
  |  2. CREATE        |   Hetzner server + SSH key
  +--------+----------+
           |
           v
  +-------------------+
  |  3. PROVISION     |   Install Docker, upload code
  +--------+----------+
           |
           v
  +-------------------+
  |  4. DEPLOY        |   docker compose up, run migrations
  +--------+----------+
           |
           v
  +-------------------+
  |  5. RECORD        |   Save to deployments.json
  +--------+----------+
           |
           v
       DeployResult
    (status, url, cost)

     redeploy(id)          monitor(id)
         |                      |
         v                      v
  +-------------+        +-------------+
  | RE-UPLOAD   |        | SSH CHECK   |
  | code only   |        | docker stats|
  | same server |        | disk usage  |
  +-------------+        +-------------+
         |                      |
         v                      v
   DeployResult            HealthStatus
```

---

## Key Design Decisions

### 1. Tool Factory Pattern
Tools are created via factory functions rather than class methods. This enables:
- Dependency injection (services passed at creation time)
- Consistent error handling wrapper
- Easy testing with mock services

### 2. Declarative Stack Detection
Framework patterns live in `stacks.yaml`, not code. This makes adding support for new frameworks a config-only change.

### 3. Template-Based Docker Generation
Jinja2 templates generate Dockerfiles and docker-compose configs. Templates are package-manager-aware (npm/pnpm/yarn/bun) and stack-specific.

### 4. Pre-Deploy Validation Gate
Before creating any infrastructure, `pre_deploy_check()` validates:
- No duplicate deployments for the same repo (unless `force_new=True`)
- All required environment variables are provided
This prevents wasted resources and confusing multi-deployment scenarios.

### 5. Path Normalization for Duplicate Detection
`StateManager.normalize_repo_path()` strips URL schemes, `.git` suffixes, and trailing slashes to match deployments regardless of how the path was specified.

### 6. Structured Error Context
`DeploymentError` carries optional `build_log`, `suggestion`, and `retry_hint` fields, giving the AI client enough context to help the user fix issues without re-reading logs.

---

## External Dependencies

| Package | Purpose |
|---------|---------|
| `mcp>=1.0` | MCP server framework (FastMCP) |
| `httpx>=0.27` | Async HTTP client for Hetzner API |
| `asyncssh>=2.14` | SSH connections and SFTP |
| `jinja2>=3.1` | Docker template rendering |
| `pyyaml>=6.0` | YAML config parsing |

---

## Test Architecture

```
tests/
├── conftest.py                       # Shared fixtures (state_manager, fixtures_dir)
├── fixtures/                         # Real directory structures for testing
│   ├── sample_fastapi/               #   FastAPI project
│   ├── sample_express/               #   Express.js project
│   ├── sample_nextjs/                #   Next.js project
│   ├── sample_fullstack/             #   Full-stack project
│   ├── sample_monorepo/              #   Monorepo structure
│   ├── sample_pnpm_express/          #   pnpm-based Express
│   ├── sample_yarn_nextjs/           #   yarn-based Next.js
│   ├── sample_typescript_express/    #   TypeScript Express
│   └── sample_missing_envvars/       #   Missing env vars scenario
│
├── test_analysis_service.py          # Stack detection tests
├── test_deployment_service.py        # Deployment orchestration tests
├── test_pricing_service.py           # Cost comparison tests
├── test_monitoring_service.py        # Health check tests
├── test_deploy_tool.py               # Deploy tool factory tests
├── test_pre_deploy_checks.py         # Pre-deploy validation tests
├── test_git_archive_upload.py        # Repo upload tests
├── test_package_manager_detection.py # npm/pnpm/yarn/bun tests
├── test_typescript_detection.py      # TS detection tests
├── test_migration_detection.py       # Migration tool detection tests
├── test_env_var_classification.py    # Env var classification tests
├── test_build_failure_handling.py    # Build error handling tests
├── test_redeploy.py                  # Path normalization tests
├── test_hetzner_provider.py          # Hetzner API tests
├── test_hetzner_integration.py       # Integration tests (needs real token)
└── ...                               # Additional unit tests
```

**140 tests total** | All pass | 1 skipped (integration)

Tests use real fixture directories rather than mocks for analysis, ensuring detection logic works against realistic project structures.
