from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import (
    DatabaseInfo, RepoAnalysis, StackInfo, ValidationIssue,
)
from computeedge.services.analysis import AnalysisService
from computeedge.services.deployment import DeploymentService
from computeedge.services.validation import ConfigValidator
from computeedge.tools.generate_configs import make_generate_configs_tool


@pytest.fixture
def analysis_service():
    svc = AsyncMock(spec=AnalysisService)
    svc.analyze = AsyncMock(return_value=RepoAnalysis(
        stack=StackInfo(backend="fastapi", backend_language="python"),
        database=DatabaseInfo(type="postgres", orm="sqlalchemy", has_migrations=True),
    ))
    return svc


@pytest.fixture
def deployment_service(state_manager):
    svc = DeploymentService(
        provider=None,
        state=state_manager,
        stacks_config=load_bundled_yaml("stacks.yaml"),
    )
    return svc


@pytest.mark.asyncio
async def test_generate_configs_returns_all_keys(analysis_service, deployment_service, tmp_path):
    """generate_configs tool returns analysis, configs, config_notes, validation_issues."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    fn = make_generate_configs_tool(analysis_service, deployment_service, ConfigValidator())
    result = await fn(str(tmp_path))

    assert "analysis" in result
    assert "configs" in result
    assert "config_notes" in result
    assert "validation_issues" in result
    assert "Dockerfile" in result["configs"]
    assert "docker-compose.yml" in result["configs"]
    assert "nginx.conf" in result["configs"]
    assert ".env" in result["configs"]


@pytest.mark.asyncio
async def test_generate_configs_with_env_vars(analysis_service, deployment_service, tmp_path):
    """User env_vars are included in the generated .env."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    fn = make_generate_configs_tool(analysis_service, deployment_service, ConfigValidator())
    result = await fn(str(tmp_path), env_vars={"API_KEY": "test123"})

    assert "API_KEY=test123" in result["configs"][".env"]


@pytest.mark.asyncio
async def test_generate_configs_error_handling(deployment_service, tmp_path):
    """Returns error dict when analysis fails."""
    from computeedge.exceptions import AnalysisError

    analysis_svc = AsyncMock(spec=AnalysisService)
    analysis_svc.analyze = AsyncMock(side_effect=AnalysisError("No stack detected"))

    fn = make_generate_configs_tool(analysis_svc, deployment_service, ConfigValidator())
    result = await fn(str(tmp_path))

    assert "error" in result
    assert "No stack detected" in result["error"]


@pytest.mark.asyncio
async def test_generate_configs_no_metadata_keys_in_output(analysis_service, deployment_service, tmp_path):
    """Internal metadata keys (_config_notes, _templates_used) should not be in configs."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    fn = make_generate_configs_tool(analysis_service, deployment_service, ConfigValidator())
    result = await fn(str(tmp_path))

    # Metadata should be extracted to top-level, not in configs
    assert "_config_notes" not in result["configs"]
    assert "_templates_used" not in result["configs"]
    # But should be at top level
    assert "config_notes" in result


@pytest.mark.asyncio
async def test_topology_override(analysis_service, deployment_service, tmp_path):
    """LLM can override auto-detected topology."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    fn = make_generate_configs_tool(analysis_service, deployment_service, ConfigValidator())

    # Default: analysis fixture has backend_only (no frontend)
    result_default = await fn(str(tmp_path))
    assert "Dockerfile" in result_default["configs"]

    # Override to frontend_only — should still work (uses backend as frontend fallback)
    result_override = await fn(str(tmp_path), topology="backend_only")
    assert "Dockerfile" in result_override["configs"]


@pytest.mark.asyncio
async def test_topology_override_invalid(analysis_service, deployment_service, tmp_path):
    """Invalid topology returns error."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")

    fn = make_generate_configs_tool(analysis_service, deployment_service, ConfigValidator())
    result = await fn(str(tmp_path), topology="nonsense")

    assert "error" in result
    assert "Invalid topology" in result["error"]


@pytest.mark.asyncio
async def test_config_keys_round_trip(analysis_service, deployment_service, tmp_path):
    """Keys from generate_configs should work when passed back as docker_configs to deploy."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    fn = make_generate_configs_tool(analysis_service, deployment_service, ConfigValidator())
    result = await fn(str(tmp_path))

    configs = result["configs"]

    # Verify all config keys are strings (serializable)
    for key, value in configs.items():
        assert isinstance(key, str), f"Key {key!r} is not a string"
        assert isinstance(value, str), f"Value for {key!r} is not a string"

    # Verify Dockerfile keys end with "Dockerfile" or are infra files
    infra_files = {"docker-compose.yml", "nginx.conf", ".env"}
    for key in configs:
        assert key in infra_files or key.endswith("Dockerfile") or key == "Dockerfile.frontend", \
            f"Unexpected config key: {key!r}"

    # Verify infra file keys match _INFRA_FILES expectations
    assert "docker-compose.yml" in configs
    assert "nginx.conf" in configs
    assert ".env" in configs


@pytest.mark.asyncio
async def test_unified_topology_keys_round_trip(deployment_service, tmp_path):
    """Fullstack app with different paths produces a single unified Dockerfile."""
    analysis_svc = AsyncMock(spec=AnalysisService)
    analysis_svc.analyze = AsyncMock(return_value=RepoAnalysis(
        stack=StackInfo(
            frontend="nextjs", backend="fastapi", backend_language="python",
            deploy_topology="single",
            backend_path="api", frontend_path="web",
        ),
    ))
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    fn = make_generate_configs_tool(analysis_svc, deployment_service, ConfigValidator())
    result = await fn(str(tmp_path))

    configs = result["configs"]
    assert "Dockerfile" in configs, f"Expected single 'Dockerfile', got: {list(configs.keys())}"
    # Should be unified, not path-prefixed
    assert not any(k.endswith("/Dockerfile") for k in configs if k != "Dockerfile")


@pytest.mark.asyncio
async def test_configs_accepted_by_apply_patches(deployment_service, analysis_service, tmp_path):
    """Generated config keys should be routable by _apply_docker_config_patches."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    fn = make_generate_configs_tool(analysis_service, deployment_service, ConfigValidator())
    result = await fn(str(tmp_path))

    configs = result["configs"]

    # Simulate what _apply_docker_config_patches does: route each key
    infra_files = {"nginx.conf", "docker-compose.yml", "docker-compose.yaml", ".env"}
    app_dir = "/root/ce-hetzner-abc123"
    for key in configs:
        if key in infra_files:
            dest = f"{app_dir}/{key}"
        else:
            dest = f"{app_dir}/repo/{key}"
        # All destinations should be valid paths (no empty segments)
        assert "//" not in dest, f"Double slash in destination for key {key!r}: {dest}"
        assert dest.endswith(key.split("/")[-1]), f"Destination doesn't end with filename for {key!r}"
