import pytest
from pathlib import Path

from computeedge.config.loader import load_bundled_yaml
from computeedge.services.analysis import AnalysisService
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "computeedge" / "templates"


@pytest.fixture
def jinja_env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), keep_trailing_newline=True)


def test_streamlit_in_stacks_yaml():
    config = load_bundled_yaml("stacks.yaml")
    streamlit = config["backend"]["streamlit"]
    assert streamlit["language"] == "python"
    assert streamlit["default_port"] == 8501
    assert streamlit["deployable"] is True
    assert streamlit["template_type"] == "language"
    assert "{entry_point}" in streamlit["run_cmd"]
    assert "--server.headless=true" in streamlit["run_cmd"]


def test_flask_default_port_matches_run_cmd():
    """Flask default_port should be 8000 to match its gunicorn bind port."""
    config = load_bundled_yaml("stacks.yaml")
    flask = config["backend"]["flask"]
    assert flask["default_port"] == 8000


@pytest.mark.asyncio
async def test_detect_standalone_streamlit():
    config = load_bundled_yaml("stacks.yaml")
    svc = AnalysisService(config)
    result = await svc.analyze(Path("tests/fixtures/sample_streamlit"))
    assert result.stack.backend == "streamlit"
    assert result.stack.frontend is None
    assert result.stack.deploy_topology == "backend_only"
    assert result.stack.backend_language == "python"


@pytest.mark.asyncio
async def test_detect_streamlit_version():
    config = load_bundled_yaml("stacks.yaml")
    svc = AnalysisService(config)
    result = await svc.analyze(Path("tests/fixtures/sample_streamlit"))
    assert result.stack.backend_version == "1"


@pytest.mark.asyncio
async def test_detect_streamlit_fastapi_coexistence():
    """When FastAPI + Streamlit coexist, FastAPI is backend, Streamlit promoted to frontend."""
    config = load_bundled_yaml("stacks.yaml")
    svc = AnalysisService(config)
    result = await svc.analyze(Path("tests/fixtures/sample_streamlit_fastapi"))
    assert result.stack.backend == "fastapi"
    assert result.stack.frontend == "streamlit"
    assert result.stack.deploy_topology == "single"


@pytest.mark.asyncio
async def test_react_wins_over_streamlit_promotion():
    """When React + FastAPI + Streamlit coexist, React is frontend (Streamlit not promoted)."""
    config = load_bundled_yaml("stacks.yaml")
    svc = AnalysisService(config)
    result = await svc.analyze(Path("tests/fixtures/sample_react_fastapi_streamlit"))
    assert result.stack.frontend == "react"
    assert result.stack.backend == "fastapi"


# ---------------------------------------------------------------------------
# Task 5: Template tests — dynamic ports and WebSocket headers
# ---------------------------------------------------------------------------

def test_python_dockerfile_dynamic_port(jinja_env):
    """Dockerfile.python.j2 renders EXPOSE 8501 when port=8501."""
    result = jinja_env.get_template("Dockerfile.python.j2").render(
        has_requirements_txt=True,
        run_cmd="streamlit run app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true",
        build_cmd=None,
        extra_packages=[],
        port=8501,
    )
    assert "EXPOSE 8501" in result
    assert "EXPOSE 8000" not in result


def test_python_dockerfile_default_port(jinja_env):
    """Dockerfile.python.j2 still defaults to EXPOSE 8000 when port not provided."""
    result = jinja_env.get_template("Dockerfile.python.j2").render(
        has_requirements_txt=True,
        run_cmd="gunicorn app:app --bind 0.0.0.0:8000",
        build_cmd=None,
        extra_packages=[],
    )
    assert "EXPOSE 8000" in result


def test_compose_unified_port(jinja_env):
    """docker-compose renders port 80 for unified (embedded nginx) topology."""
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(stack=StackInfo(
        frontend="streamlit", backend="fastapi",
        deploy_topology="single",
    ))
    result = jinja_env.get_template("docker-compose.j2").render(
        analysis=analysis, include_db=False, include_redis=False,
        db_type=None, env_vars={}, app_port=8000,
        embedded_nginx=True, topology="single",
    )
    assert "80:80" in result
    # No separate nginx service when embedded
    assert "nginx:" not in result or "image: nginx" not in result


def test_compose_dynamic_backend_port_standalone(jinja_env):
    """docker-compose renders backend port as 8501 for standalone Streamlit."""
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(stack=StackInfo(
        backend="streamlit", deploy_topology="backend_only",
    ))
    result = jinja_env.get_template("docker-compose.j2").render(
        analysis=analysis, include_db=False, include_redis=False,
        db_type=None, env_vars={}, app_port=8501,
        embedded_nginx=False, topology="backend_only",
    )
    assert "8501:8501" in result


def test_nginx_websocket_headers_backend_only(jinja_env):
    """nginx backend-only location includes WebSocket headers."""
    result = jinja_env.get_template("nginx.conf.j2").render(
        services=["app"], use_ssl=False, domain=None,
        topology="backend_only", app_port=8501,
    )
    assert "proxy_set_header Upgrade" in result
    assert "proxy_set_header Connection" in result
    assert "8501" in result


def test_nginx_single_upstream(jinja_env):
    """nginx template renders single upstream to app service."""
    result = jinja_env.get_template("nginx.conf.j2").render(
        services=["app"], use_ssl=False, domain=None,
        topology="single", app_port=8000,
    )
    assert "server app:8000" in result
    assert "proxy_set_header Upgrade" in result


# ---------------------------------------------------------------------------
# Task 6: Deployment service — entry point detection and template context
# ---------------------------------------------------------------------------

from computeedge.services.deployment import DeploymentService
from computeedge.models import RepoAnalysis, StackInfo


def test_detect_streamlit_entry_point_app_py():
    svc = DeploymentService.__new__(DeploymentService)
    entry = svc._detect_streamlit_entry_point(Path("tests/fixtures/sample_streamlit"))
    assert entry == "app.py"


def test_detect_streamlit_entry_point_fallback(tmp_path):
    """Falls back to scanning .py files for streamlit imports."""
    (tmp_path / "dashboard.py").write_text("import streamlit as st\nst.title('hi')")
    svc = DeploymentService.__new__(DeploymentService)
    entry = svc._detect_streamlit_entry_point(tmp_path)
    assert entry == "dashboard.py"


def test_detect_streamlit_entry_point_default(tmp_path):
    """Returns app.py when no streamlit files found."""
    svc = DeploymentService.__new__(DeploymentService)
    entry = svc._detect_streamlit_entry_point(tmp_path)
    assert entry == "app.py"


def test_build_template_context_streamlit():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    analysis = RepoAnalysis(stack=StackInfo(backend="streamlit", backend_language="python"))
    context = svc._build_template_context("streamlit", analysis, Path("tests/fixtures/sample_streamlit"))
    assert "streamlit run app.py" in context["run_cmd"]
    assert "--server.port=8501" in context["run_cmd"]
    assert "--server.headless=true" in context["run_cmd"]
    assert context["port"] == 8501


def test_build_template_context_preserves_existing_ports(tmp_path):
    """Existing stacks get their correct ports from _build_template_context."""
    (tmp_path / "requirements.txt").write_text("flask")
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    analysis = RepoAnalysis(stack=StackInfo(backend="flask", backend_language="python"))
    context = svc._build_template_context("flask", analysis, tmp_path)
    assert context["port"] == 8000


def test_frontend_dockerfile_template_streamlit():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._frontend_dockerfile_template("streamlit") == "Dockerfile.python.j2"


def test_backend_dockerfile_template_streamlit():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._backend_dockerfile_template("streamlit", "python") == "Dockerfile.python.j2"
