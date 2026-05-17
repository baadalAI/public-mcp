import pytest

from computeedge.models import DeployDiagnostics
from computeedge.exceptions import DeploymentError


def test_deploy_diagnostics_dataclass():
    diag = DeployDiagnostics(
        phase="docker_build",
        service="backend",
        exit_code=1,
        summary="Missing system library",
        relevant_logs=["ERROR: Could not build wheels"],
        dockerfile_used="Dockerfile.python.j2",
        generated_files={"Dockerfile.backend": "FROM python:3.12-slim"},
        context={"stack": "django", "database": "mysql"},
    )
    assert diag.phase == "docker_build"
    assert diag.exit_code == 1
    assert len(diag.relevant_logs) == 1


def test_deployment_error_has_diagnostics():
    diag = DeployDiagnostics(phase="docker_build", service="backend")
    err = DeploymentError("Build failed", diagnostics=diag)
    assert err.diagnostics is not None
    assert err.diagnostics.phase == "docker_build"


def test_deployment_error_backwards_compatible():
    """Existing DeploymentError usage still works."""
    err = DeploymentError("Deploy failed", suggestion="Try again")
    assert err.suggestion == "Try again"
    assert err.diagnostics is None


# --- Task 9 tests below ---

from computeedge.services.deployment import DeploymentService


def test_diagnose_missing_mysql_header():
    svc = DeploymentService.__new__(DeploymentService)
    logs = "fatal error: mysql/mysql.h: No such file or directory"
    fixes = svc._diagnose_failure(logs, "Dockerfile.python.j2")
    assert len(fixes) >= 1
    assert fixes[0]["confidence"] == "high"
    assert "libmysqlclient" in fixes[0]["description"]


def test_diagnose_missing_pg_config():
    svc = DeploymentService.__new__(DeploymentService)
    logs = "Error: pg_config executable not found"
    fixes = svc._diagnose_failure(logs, "Dockerfile.python.j2")
    assert len(fixes) >= 1
    assert fixes[0]["confidence"] == "high"
    assert "libpq-dev" in fixes[0]["description"]


def test_diagnose_python_module_not_found():
    svc = DeploymentService.__new__(DeploymentService)
    logs = "ModuleNotFoundError: No module named 'celery'"
    fixes = svc._diagnose_failure(logs, "Dockerfile.python.j2")
    assert len(fixes) >= 1
    assert "celery" in fixes[0]["description"]


def test_diagnose_node_module_not_found():
    svc = DeploymentService.__new__(DeploymentService)
    logs = "Error: Cannot find module 'express'"
    fixes = svc._diagnose_failure(logs, "Dockerfile.express.j2")
    assert len(fixes) >= 1
    assert "express" in fixes[0]["description"]


def test_diagnose_localhost_connection():
    svc = DeploymentService.__new__(DeploymentService)
    logs = "ECONNREFUSED 127.0.0.1:5432"
    fixes = svc._diagnose_failure(logs, "Dockerfile.python.j2")
    assert len(fixes) >= 1
    assert "localhost" in fixes[0]["description"].lower() or "db" in fixes[0]["description"]


def test_diagnose_unknown_error():
    svc = DeploymentService.__new__(DeploymentService)
    logs = "Something completely unexpected happened"
    fixes = svc._diagnose_failure(logs, "Dockerfile.python.j2")
    assert len(fixes) == 0


def test_build_deploy_diagnostics():
    svc = DeploymentService.__new__(DeploymentService)
    from computeedge.models import RepoAnalysis, StackInfo, DatabaseInfo
    analysis = RepoAnalysis(
        stack=StackInfo(backend="django", backend_language="python"),
        database=DatabaseInfo(type="mysql"),
    )
    diag = svc._build_deploy_diagnostics(
        phase="docker_build",
        error_msg="Build failed",
        container_logs="ERROR: Could not build wheels for mysqlclient\nfatal error: mysql/mysql.h: No such file or directory",
        analysis=analysis,
        dockerfile_used="Dockerfile.python.j2",
        generated_files={"Dockerfile.backend": "FROM python:3.12"},
    )
    assert diag.phase == "docker_build"
    assert len(diag.relevant_logs) >= 1
    assert diag.context["stack"] == "django"
    assert len(diag.suggested_fixes) >= 1


from computeedge.models import RetryContext


def test_deploy_diagnostics_new_fields():
    ctx = RetryContext(attempt=1, server_id=123)
    diag = DeployDiagnostics(
        phase="docker_build",
        full_logs="Step 1/5: FROM python:3.11-slim\nStep 2/5: RUN apt-get...\nERROR: pg_config not found",
        dependency_files={"requirements.txt": "psycopg2==2.9.9\nflask==3.0.0"},
        suggested_docker_configs={"Dockerfile": "FROM python:3.11-slim\nRUN apt-get install libpq-dev"},
        agent_instruction="Deploy failed during docker_build...",
        retry_context=ctx,
    )
    assert diag.full_logs.startswith("Step 1/5")
    assert "requirements.txt" in diag.dependency_files
    assert diag.suggested_docker_configs is not None
    assert diag.agent_instruction.startswith("Deploy failed")
    assert diag.retry_context.server_id == 123
