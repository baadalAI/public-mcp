# Deploying Nanoclaw with ComputeEdge

ComputeEdge can deploy [nanoclaw](https://github.com/qwibitai/nanoclaw) — a lightweight AI assistant framework that runs Claude agents in isolated containers — to a cloud VM with a single MCP tool call.

Nanoclaw runs natively on the VM (not inside Docker). Docker is used by nanoclaw itself to spawn isolated agent containers for each conversation.

---

## Quick Start

### Deploy vanilla nanoclaw (no repo needed)

```
deploy(
    provider_token="your-hetzner-token",
    stack="nanoclaw",
    env_vars={
        "ANTHROPIC_API_KEY": "sk-ant-xxxxx",
        "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"
    }
)
```

That's it. A VM is provisioned, nanoclaw is installed and running, and your Telegram bot is live.

### Deploy without an API key

If you use Claude Code's own authentication instead of an API key:

```
deploy(
    provider_token="your-hetzner-token",
    stack="nanoclaw",
    env_vars={"TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"}
)
```

After deploy, authenticate Claude Code on the server:

```bash
ssh root@<ip>
claude login
```

---

## Deployment Scenarios

### 1. Vanilla nanoclaw (no repo)

You just want a nanoclaw instance. No fork, no local project.

```
deploy(
    provider_token="your-hetzner-token",
    stack="nanoclaw",
    env_vars={"ANTHROPIC_API_KEY": "sk-ant-xxxxx"}
)
```

Clones the upstream nanoclaw repo, builds it, and starts the service.

### 2. Your GitHub fork

You forked nanoclaw and customized it with your own agents, skills, and channel configs.

```
deploy(
    provider_token="your-hetzner-token",
    repo_path="https://github.com/yourname/my-nanoclaw.git",
    env_vars={"ANTHROPIC_API_KEY": "sk-ant-xxxxx"}
)
```

### 3. Local nanoclaw project

You cloned nanoclaw locally and have been developing on it.

```
deploy(
    provider_token="your-hetzner-token",
    repo_path="/Users/you/nanoclaw",
    env_vars={"ANTHROPIC_API_KEY": "sk-ant-xxxxx"}
)
```

Uploads your code including `groups/` (agent configs) and `container/skills/` (custom skills). Excludes `.env` (secrets) and `store/` (local SQLite database) automatically.

### 4. Workspace from npm install (auto-detected)

You installed nanoclaw globally via npm, ran it from a local directory, set up agents and channels there.

```bash
# What you did locally:
npm install -g nanoclaw
mkdir ~/my-bot && cd ~/my-bot
nanoclaw                      # creates groups/, .claude/, etc.
# ... configured agents, added channels ...
```

```
# Now deploy — auto-detected as a nanoclaw workspace:
deploy(
    provider_token="your-hetzner-token",
    repo_path="/Users/you/my-bot",
    env_vars={"ANTHROPIC_API_KEY": "sk-ant-xxxxx"}
)
```

ComputeEdge detects the workspace structure (`groups/` or `.claude/` without nanoclaw source code), clones the upstream nanoclaw repo on the VM, and overlays your agent configs on top. No `stack="nanoclaw"` needed — it's auto-detected.

**What gets uploaded from your workspace:**

| Uploaded | Not uploaded | Why excluded |
|----------|-------------|-------------|
| `groups/` | `.env` | Contains secrets |
| `.claude/` | `store/` | Local SQLite DB |
| `container/skills/` | `node_modules/` | Rebuilt on VM |

---

## Channel Credentials

Pass channel credentials as `env_vars` at deploy time. Nanoclaw auto-registers channels at startup when credentials are present.

| Channel | Environment Variable(s) |
|---------|------------------------|
| Telegram | `TELEGRAM_BOT_TOKEN` |
| WhatsApp | `WHATSAPP_PHONE_ID`, `WHATSAPP_TOKEN` |
| Discord | `DISCORD_TOKEN` |
| Slack | `SLACK_BOT_TOKEN` |

Example with multiple channels:

```
deploy(
    provider_token="your-hetzner-token",
    stack="nanoclaw",
    env_vars={
        "ANTHROPIC_API_KEY": "sk-ant-xxxxx",
        "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
        "DISCORD_TOKEN": "MTIz.abc.xyz"
    }
)
```

You can add channels later without redeploying the whole thing:

```
redeploy(
    deployment_id="ce-hetzner-a1b2c3d4",
    provider_token="your-hetzner-token",
    env_vars={"SLACK_BOT_TOKEN": "xoxb-xxxxx"}
)
```

New env vars are merged with existing ones — previous credentials are preserved.

---

## Server Plans

Nanoclaw spawns Docker containers with Chromium for each agent task, so it needs more resources than a typical web app. The default plan provides 4GB RAM and 2 vCPUs.

| Provider | Default Plan | RAM | vCPUs | Monthly Cost |
|----------|-------------|-----|-------|-------------|
| Hetzner | cx32 | 4 GB | 2 | ~$7.59 |
| DigitalOcean | s-2vcpu-4gb | 4 GB | 2 | ~$24.00 |

Override with the `plan` parameter:

```
deploy(
    provider_token="your-hetzner-token",
    stack="nanoclaw",
    plan="cx42",    # 8GB RAM, 4 vCPUs for heavy agent workloads
    env_vars={...}
)
```

---

## What Gets Installed on the VM

The VM is provisioned with Ubuntu and the following via cloud-init:

- **Docker** + docker-compose (for nanoclaw's agent containers)
- **Node.js 22** (nanoclaw runtime)
- **Git** + build-essential
- **Claude Code CLI** (`@anthropic-ai/claude-code`)

After cloud-init, the deploy process:

1. Clones/uploads your nanoclaw project to `/opt/nanoclaw/repo`
2. Runs `npm ci` and `npm run build`
3. Builds the agent container via `./container/build.sh` (if present)
4. Writes `.env` with your credentials
5. Creates a systemd service (`nanoclaw.service`)
6. Starts the service

---

## Managing Your Deployment

### View logs

```bash
ssh root@<ip> journalctl -u nanoclaw -f
```

### Restart the service

```bash
ssh root@<ip> systemctl restart nanoclaw
```

### Update code (redeploy)

```
redeploy(
    deployment_id="ce-hetzner-a1b2c3d4",
    provider_token="your-hetzner-token"
)
```

This stops the service, re-uploads your code, runs `npm ci` + `npm run build`, rebuilds the agent container, and restarts the service.

### Add or modify agents via SSH

```bash
ssh root@<ip>
cd /opt/nanoclaw/repo
claude              # opens Claude Code with nanoclaw skills
/setup              # configure channels interactively
/customize          # modify agent behavior
/add-telegram       # add a Telegram channel
```

### Destroy the deployment

```
destroy_deployment(
    deployment_id="ce-hetzner-a1b2c3d4",
    provider_token="your-hetzner-token"
)
```

---

## How Agents Work in Nanoclaw

Each messaging group (Telegram group, WhatsApp chat, etc.) gets its own isolated agent:

- **Agent definition**: `groups/<name>/CLAUDE.md` — defines the agent's personality, behavior, and context
- **Agent skills**: `container/skills/` — custom tools available to agents at runtime
- **Isolation**: Each agent task spawns a dedicated Docker container with its own filesystem
- **Credentials**: Never passed to agent containers directly — OneCLI Agent Vault injects them at request time

### Creating a new agent

1. Create a group directory: `groups/my-team/`
2. Write `groups/my-team/CLAUDE.md` with the agent's instructions
3. Redeploy to push the changes to the server

Or SSH in and use Claude Code:

```bash
ssh root@<ip>
cd /opt/nanoclaw/repo
claude
/customize
```

---

## Architecture on the VM

```
VM (Hetzner/DigitalOcean)
├── Docker daemon
├── Node.js 22 + Claude Code CLI
├── /opt/nanoclaw/repo/          (nanoclaw project)
│   ├── dist/                    (compiled TypeScript)
│   ├── groups/                  (per-group agent configs)
│   │   └── main/CLAUDE.md
│   ├── container/               (agent container definition)
│   │   ├── Dockerfile
│   │   ├── build.sh
│   │   └── skills/
│   ├── .claude/                 (Claude Code config)
│   ├── .env                     (credentials)
│   └── store/                   (SQLite DB, created at runtime)
├── /etc/systemd/system/nanoclaw.service
└── Agent containers (spawned per-task, auto-cleaned)
```

Nanoclaw runs as a systemd service. It polls messaging channels, and when triggered, spawns isolated Docker containers that run Claude Agent SDK to handle each conversation.
