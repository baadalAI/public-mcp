from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import RepoAnalysis, StackInfo
from computeedge.services.analysis import AnalysisService
from computeedge.services.deployment import DeploymentService

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "computeedge" / "templates"


@pytest.fixture
def analysis_service():
    return AnalysisService(load_bundled_yaml("stacks.yaml"))


@pytest.fixture
def jinja_env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), keep_trailing_newline=True)


@pytest.mark.asyncio
async def test_detect_typescript_express(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_typescript_express")
    assert result.stack.backend == "express"
    assert result.stack.backend_language == "typescript"


@pytest.mark.asyncio
async def test_plain_express_stays_javascript(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_express")
    assert result.stack.backend == "express"
    assert result.stack.backend_language == "javascript"


def test_deployment_selects_ts_template_for_typescript():
    """DeploymentService._backend_dockerfile_template returns TS variant for typescript."""
    svc = DeploymentService.__new__(DeploymentService)
    assert svc._backend_dockerfile_template("express", "typescript") == "Dockerfile.express-ts.j2"
    assert svc._backend_dockerfile_template("express", "javascript") == "Dockerfile.express.j2"
    assert svc._backend_dockerfile_template("express", None) == "Dockerfile.express.j2"
    assert svc._backend_dockerfile_template("fastapi", "python") == "Dockerfile.fastapi.j2"


def test_express_ts_template_has_build_step(jinja_env):
    analysis = RepoAnalysis(stack=StackInfo(backend="express", backend_language="typescript"))
    result = jinja_env.get_template("Dockerfile.express-ts.j2").render(analysis=analysis)
    assert "npm run build" in result
    assert "dist/index.js" in result
    assert "npm ci" in result
