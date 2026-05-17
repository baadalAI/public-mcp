from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import RepoAnalysis, StackInfo
from computeedge.services.analysis import AnalysisService

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "computeedge" / "templates"


@pytest.fixture
def analysis_service():
    return AnalysisService(load_bundled_yaml("stacks.yaml"))


@pytest.fixture
def jinja_env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), keep_trailing_newline=True)


def _mock_analysis(package_manager="npm", **kwargs):
    return RepoAnalysis(stack=StackInfo(package_manager=package_manager, **kwargs))


# --- Analysis detection tests ---


@pytest.mark.asyncio
async def test_detect_pnpm_from_lockfile(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_pnpm_express")
    assert result.stack.package_manager == "pnpm"


@pytest.mark.asyncio
async def test_detect_yarn_from_lockfile(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_yarn_nextjs")
    assert result.stack.package_manager == "yarn"


@pytest.mark.asyncio
async def test_default_to_npm(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_nextjs")
    assert result.stack.package_manager == "npm"


@pytest.mark.asyncio
async def test_existing_express_fixture_uses_npm(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_express")
    assert result.stack.package_manager == "npm"


# --- Template rendering tests ---


def test_express_template_npm(jinja_env):
    result = jinja_env.get_template("Dockerfile.express.j2").render(analysis=_mock_analysis("npm"))
    assert "npm ci" in result


def test_express_template_pnpm(jinja_env):
    result = jinja_env.get_template("Dockerfile.express.j2").render(analysis=_mock_analysis("pnpm"))
    assert "pnpm install --frozen-lockfile" in result
    assert "npm ci" not in result


def test_express_template_yarn(jinja_env):
    result = jinja_env.get_template("Dockerfile.express.j2").render(analysis=_mock_analysis("yarn"))
    assert "yarn install --frozen-lockfile" in result
    assert "npm ci" not in result


def test_express_template_bun(jinja_env):
    result = jinja_env.get_template("Dockerfile.express.j2").render(analysis=_mock_analysis("bun"))
    assert "bun install --frozen-lockfile" in result
    assert "oven/bun" in result


def test_nextjs_template_pnpm(jinja_env):
    result = jinja_env.get_template("Dockerfile.nextjs.j2").render(analysis=_mock_analysis("pnpm", frontend="nextjs"))
    assert "pnpm install --frozen-lockfile" in result
    assert "npm ci" not in result


def test_react_template_yarn(jinja_env):
    result = jinja_env.get_template("Dockerfile.react.j2").render(analysis=_mock_analysis("yarn", frontend="react"))
    assert "yarn install --frozen-lockfile" in result
    assert "npm ci" not in result
