from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "computeedge" / "templates"


@pytest.fixture
def jinja_env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), keep_trailing_newline=True)


def test_python_template_renders_django(jinja_env):
    result = jinja_env.get_template("Dockerfile.python.j2").render(
        has_requirements_txt=True,
        run_cmd="gunicorn myproject.wsgi:application --bind 0.0.0.0:8000",
        build_cmd="python manage.py collectstatic --noinput",
        extra_packages=["gunicorn"],
    )
    assert "FROM python:3.12-slim" in result
    assert "COPY requirements.txt" in result
    assert "pip install --no-cache-dir -r requirements.txt" in result
    assert "pip install --no-cache-dir gunicorn" in result
    assert "collectstatic" in result
    assert "EXPOSE 8000" in result
    assert "gunicorn myproject.wsgi:application" in result


def test_python_template_renders_flask(jinja_env):
    result = jinja_env.get_template("Dockerfile.python.j2").render(
        has_requirements_txt=True,
        run_cmd="gunicorn app:app --bind 0.0.0.0:8000",
        build_cmd=None,
        extra_packages=["gunicorn"],
    )
    assert "COPY requirements.txt" in result
    assert "gunicorn app:app" in result
    assert "collectstatic" not in result


def test_python_template_pyproject_fallback(jinja_env):
    result = jinja_env.get_template("Dockerfile.python.j2").render(
        has_requirements_txt=False,
        run_cmd="gunicorn app:app --bind 0.0.0.0:8000",
        build_cmd=None,
        extra_packages=["gunicorn"],
    )
    assert "COPY pyproject.toml" in result
    assert "pip install --no-cache-dir ." in result


def test_rails_template_renders(jinja_env):
    result = jinja_env.get_template("Dockerfile.rails.j2").render()
    assert "FROM ruby:3.3-slim" in result
    assert "bundle install" in result
    assert "assets:precompile" in result
    assert "EXPOSE 8000" in result
    assert "RAILS_ENV=production" in result
    assert "rails" in result and "server" in result
    assert '"8000"' in result or "8000" in result


def test_go_template_renders(jinja_env):
    result = jinja_env.get_template("Dockerfile.go.j2").render(entry_point="./cmd/server")
    assert "FROM golang:1.23-alpine AS builder" in result
    assert "go build -o /server ./cmd/server" in result
    assert "FROM alpine:" in result
    assert "EXPOSE 8000" in result
    assert "ENV PORT=8000" in result


def test_go_template_default_entry_point(jinja_env):
    result = jinja_env.get_template("Dockerfile.go.j2").render(entry_point=".")
    assert "go build -o /server ." in result


def test_react_template_parameterized_output_dir(jinja_env):
    """React template defaults to 'dist' (Vite standard) when no output_dir provided."""
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(stack=StackInfo(frontend="react", package_manager="npm"))
    result = jinja_env.get_template("Dockerfile.react.j2").render(analysis=analysis)
    assert "/app/dist" in result
    assert "nginx" in result


def test_react_template_vue_output_dir(jinja_env):
    """When output_dir is 'dist', the template copies dist/ for Vue."""
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(stack=StackInfo(frontend="vue", package_manager="npm"))
    result = jinja_env.get_template("Dockerfile.react.j2").render(analysis=analysis, output_dir="dist")
    assert "/app/dist" in result


from computeedge.config.loader import load_bundled_yaml
from computeedge.services.deployment import DeploymentService


def test_backend_template_django():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._backend_dockerfile_template("django", "python") == "Dockerfile.python.j2"


def test_backend_template_flask():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._backend_dockerfile_template("flask", "python") == "Dockerfile.python.j2"


def test_backend_template_rails():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._backend_dockerfile_template("rails", "ruby") == "Dockerfile.rails.j2"


def test_backend_template_go():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._backend_dockerfile_template("go", "go") == "Dockerfile.go.j2"


def test_frontend_template_vue():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._frontend_dockerfile_template("vue") == "Dockerfile.react.j2"


def test_existing_templates_unchanged():
    """Existing stacks still return their hardcoded templates."""
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._backend_dockerfile_template("fastapi", "python") == "Dockerfile.fastapi.j2"
    assert svc._backend_dockerfile_template("express", "typescript") == "Dockerfile.express-ts.j2"
    assert svc._backend_dockerfile_template("express", "javascript") == "Dockerfile.express.j2"
    assert svc._frontend_dockerfile_template("nextjs") == "Dockerfile.nextjs.j2"
    assert svc._frontend_dockerfile_template("react") == "Dockerfile.react.j2"


def test_unsupported_backend_returns_none():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._backend_dockerfile_template("rust", "rust") is None


def test_get_stack_config_finds_backend():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    config = svc._get_stack_config("django")
    assert config is not None
    assert config.get("template_type") == "language"


def test_get_stack_config_finds_frontend():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    config = svc._get_stack_config("vue")
    assert config is not None
    assert config.get("template") == "react"


def test_get_stack_config_returns_none_for_unknown():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    assert svc._get_stack_config("nonexistent") is None


def test_build_template_context_django():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(stack=StackInfo(backend="django", backend_language="python"))
    context = svc._build_template_context("django", analysis, Path("tests/fixtures/sample_django"))
    assert "gunicorn" in context["run_cmd"]
    assert "myproject" in context["run_cmd"]  # detected from wsgi.py
    assert context["build_cmd"] == "python manage.py collectstatic --noinput"
    assert "gunicorn" in context["extra_packages"]
    assert context["has_requirements_txt"] is True


def test_build_template_context_go():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(stack=StackInfo(backend="go", backend_language="go"))
    context = svc._build_template_context("go", analysis, Path("tests/fixtures/sample_go"))
    # Should find cmd/server/main.go first → entry_point = "./cmd/server"
    assert context["entry_point"] == "./cmd/server"


def test_detect_django_project_name():
    svc = DeploymentService.__new__(DeploymentService)
    name = svc._detect_django_project_name(Path("tests/fixtures/sample_django"))
    assert name == "myproject"


def test_detect_django_project_name_fallback(tmp_path):
    svc = DeploymentService.__new__(DeploymentService)
    name = svc._detect_django_project_name(tmp_path)
    assert name == "app"


def test_detect_go_entry_point():
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    entry = svc._detect_go_entry_point(Path("tests/fixtures/sample_go"))
    assert entry == "./cmd/server"


def test_detect_go_entry_point_fallback(tmp_path):
    svc = DeploymentService.__new__(DeploymentService)
    svc._stacks_config = load_bundled_yaml("stacks.yaml")
    # tmp_path has no go files → fallback to "."
    entry = svc._detect_go_entry_point(tmp_path)
    assert entry == "."
