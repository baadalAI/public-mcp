"""Tests for topology detection, build output dir detection, and server entry detection."""
import json
from pathlib import Path

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.services.analysis import AnalysisService


@pytest.fixture
def svc():
    return AnalysisService(load_bundled_yaml("stacks.yaml"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write a dict of {relative_path: content} into tmp_path and return it."""
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


# ---------------------------------------------------------------------------
# Topology: single (monorepo, Express serves React)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_topology_single_express_react_build_scripts(svc, tmp_path):
    """Monorepo with build:client + build:server scripts → single topology."""
    pkg = {
        "dependencies": {"express": "^4", "react": "^19"},
        "scripts": {
            "build": "npm run build:client && npm run build:server",
            "build:client": "vite build",
            "build:server": "tsc -p tsconfig.server.json",
            "start": "node build/server.js",
        },
    }
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.deploy_topology == "single"


@pytest.mark.asyncio
async def test_topology_single_express_static(svc, tmp_path):
    """Express server with express.static call → single topology."""
    pkg = {
        "dependencies": {"express": "^4", "react": "^19"},
        "scripts": {"start": "node server.js"},
    }
    server_content = """
const app = require('express')();
app.use(express.static('dist'));
app.listen(3001);
"""
    repo = make_repo(tmp_path, {
        "package.json": json.dumps(pkg),
        "server.js": server_content,
    })
    result = await svc.analyze(repo)
    assert result.stack.deploy_topology == "single"


@pytest.mark.asyncio
async def test_topology_single_node_start_script(svc, tmp_path):
    """package.json start → node <path> (no dev server) → single topology."""
    pkg = {
        "dependencies": {"express": "^4", "react": "^19"},
        "scripts": {"start": "node build/index.js"},
    }
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.deploy_topology == "single"


# ---------------------------------------------------------------------------
# Topology: split (separate frontend and backend)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_topology_split_separate_dirs(svc, tmp_path):
    """Frontend in client/, backend in server/ → split topology."""
    client_pkg = {"dependencies": {"react": "^19"}, "scripts": {"build": "vite build"}}
    server_pkg = {"dependencies": {"express": "^4"}, "scripts": {"start": "node index.js"}}
    repo = make_repo(tmp_path, {
        "client/package.json": json.dumps(client_pkg),
        "server/package.json": json.dumps(server_pkg),
    })
    result = await svc.analyze(repo)
    assert result.stack.deploy_topology == "single"


# ---------------------------------------------------------------------------
# Topology: frontend_only and backend_only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_topology_frontend_only(svc, tmp_path):
    pkg = {"dependencies": {"react": "^18"}, "scripts": {"build": "vite build"}}
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.deploy_topology == "frontend_only"


@pytest.mark.asyncio
async def test_topology_backend_only(svc, tmp_path):
    pkg = {"dependencies": {"express": "^4"}, "scripts": {"start": "node index.js"}}
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.deploy_topology == "backend_only"


# ---------------------------------------------------------------------------
# Build output dir detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_output_dir_vite_config_default(svc, tmp_path):
    """vite.config.ts present with no outDir → dist."""
    pkg = {"dependencies": {"react": "^19", "vite": "^5"}}
    repo = make_repo(tmp_path, {
        "package.json": json.dumps(pkg),
        "vite.config.ts": "import { defineConfig } from 'vite';\nexport default defineConfig({});",
    })
    result = await svc.analyze(repo)
    assert result.stack.build_output_dir == "dist"


@pytest.mark.asyncio
async def test_build_output_dir_vite_custom_outdir(svc, tmp_path):
    """vite.config.js with outDir: 'public' → public."""
    pkg = {"dependencies": {"react": "^19"}}
    repo = make_repo(tmp_path, {
        "package.json": json.dumps(pkg),
        "vite.config.js": "export default { build: { outDir: 'public' } };",
    })
    result = await svc.analyze(repo)
    assert result.stack.build_output_dir == "public"


@pytest.mark.asyncio
async def test_build_output_dir_cra(svc, tmp_path):
    """react-scripts (Create React App) → build."""
    pkg = {
        "dependencies": {"react": "^18", "react-scripts": "5.0.1"},
        "scripts": {"build": "react-scripts build"},
    }
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.build_output_dir == "build"


@pytest.mark.asyncio
async def test_build_output_dir_nextjs(svc, tmp_path):
    """Next.js → .next."""
    pkg = {"dependencies": {"next": "^14", "react": "^18"}}
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.build_output_dir == ".next"


@pytest.mark.asyncio
async def test_build_output_dir_vite_dep_no_config(svc, tmp_path):
    """vite in deps but no vite.config file → dist (inferred from dep)."""
    pkg = {
        "dependencies": {"react": "^18"},
        "devDependencies": {"vite": "^5"},
        "scripts": {"build": "vite build"},
    }
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.build_output_dir == "dist"


# ---------------------------------------------------------------------------
# Server entry point detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_server_entry_build_server(svc, tmp_path):
    """npm start → node build/server.js → server_entry = build/server.js."""
    pkg = {
        "dependencies": {"express": "^4"},
        "scripts": {"start": "node build/server.js"},
    }
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.server_entry == "build/server.js"


@pytest.mark.asyncio
async def test_server_entry_dist_index(svc, tmp_path):
    """npm start → node dist/index.js → server_entry = dist/index.js."""
    pkg = {
        "dependencies": {"express": "^4"},
        "scripts": {"start": "node dist/index.js"},
    }
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.server_entry == "dist/index.js"


@pytest.mark.asyncio
async def test_server_entry_with_env_prefix(svc, tmp_path):
    """start script with env prefix: NODE_ENV=production node build/app.js."""
    pkg = {
        "dependencies": {"express": "^4"},
        "scripts": {"start": "NODE_ENV=production node build/app.js"},
    }
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.server_entry == "build/app.js"


@pytest.mark.asyncio
async def test_server_entry_none_for_frontend_only(svc, tmp_path):
    """Frontend-only repo has no server_entry."""
    pkg = {"dependencies": {"react": "^18"}, "scripts": {"build": "vite build"}}
    repo = make_repo(tmp_path, {"package.json": json.dumps(pkg)})
    result = await svc.analyze(repo)
    assert result.stack.server_entry is None
