import pytest
from pathlib import Path
from computeedge.config.loader import load_bundled_yaml

def test_repo_analysis_has_new_fields():
    """RepoAnalysis should have system_packages, python_version, node_version, go_version."""
    from computeedge.models import RepoAnalysis, StackInfo
    analysis = RepoAnalysis(stack=StackInfo())
    assert analysis.system_packages == []
    assert analysis.python_version is None
    assert analysis.node_version is None
    assert analysis.go_version is None

def test_validation_issue_dataclass():
    """ValidationIssue should have severity, check, message, file, suggestion."""
    from computeedge.models import ValidationIssue
    issue = ValidationIssue(severity="error", check="dep_file", message="Missing requirements.txt")
    assert issue.severity == "error"
    assert issue.file is None
    assert issue.suggestion is None


@pytest.fixture
def analysis_service():
    stacks = load_bundled_yaml("stacks.yaml")
    system_deps = load_bundled_yaml("system_deps.yaml")
    from computeedge.services.analysis import AnalysisService
    return AnalysisService(stacks, system_deps_config=system_deps)

@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"

@pytest.mark.asyncio
async def test_detect_system_packages_psycopg2(analysis_service, tmp_path):
    """Project with psycopg2 in requirements.txt should detect libpq-dev."""
    (tmp_path / "requirements.txt").write_text("fastapi==0.100.0\npsycopg2>=2.9\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await analysis_service.analyze(tmp_path)
    assert "libpq-dev" in result.system_packages
    assert "gcc" in result.system_packages

@pytest.mark.asyncio
async def test_detect_system_packages_multiple(analysis_service, tmp_path):
    """Multiple packages needing system deps should be combined and deduplicated."""
    (tmp_path / "requirements.txt").write_text("psycopg2\nmysqlclient\ncryptography\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await analysis_service.analyze(tmp_path)
    assert "libpq-dev" in result.system_packages
    assert "default-libmysqlclient-dev" in result.system_packages
    assert "libffi-dev" in result.system_packages
    assert "libssl-dev" in result.system_packages
    assert len(result.system_packages) == len(set(result.system_packages))

@pytest.mark.asyncio
async def test_no_system_packages_when_none_needed(analysis_service, tmp_path):
    """Project with no C-extension packages should have empty system_packages."""
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\npydantic\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await analysis_service.analyze(tmp_path)
    assert result.system_packages == []

@pytest.mark.asyncio
async def test_detect_system_packages_pyproject_toml(analysis_service, tmp_path):
    """Parse dependencies from pyproject.toml [project.dependencies]."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"\ndependencies = [\n    "psycopg2>=2.9",\n    "Pillow[jpeg]>=9.0",\n    "uvicorn",\n]\n')
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await analysis_service.analyze(tmp_path)
    assert "libpq-dev" in result.system_packages
    assert "libjpeg-dev" in result.system_packages

@pytest.mark.asyncio
async def test_detect_system_packages_pipfile(analysis_service, tmp_path):
    """Parse dependencies from Pipfile [packages] section."""
    (tmp_path / "Pipfile").write_text('[packages]\npsycopg2 = ">=2.9"\nflask = "*"\n\n[dev-packages]\npytest = "*"\n')
    (tmp_path / "main.py").write_text("from flask import Flask\napp = Flask(__name__)")
    (tmp_path / "requirements.txt").write_text("flask\n")
    result = await analysis_service.analyze(tmp_path)
    assert "libpq-dev" in result.system_packages

@pytest.mark.asyncio
async def test_detect_system_packages_case_insensitive(analysis_service, tmp_path):
    """Package lookup should be case-insensitive."""
    (tmp_path / "requirements.txt").write_text("PyYAML>=6.0\n")
    (tmp_path / "main.py").write_text("import yaml")
    result = await analysis_service.analyze(tmp_path)
    assert "libyaml-dev" in result.system_packages

@pytest.mark.asyncio
async def test_no_system_deps_config_graceful(tmp_path):
    """AnalysisService without system_deps_config should return empty system_packages."""
    stacks = load_bundled_yaml("stacks.yaml")
    from computeedge.services.analysis import AnalysisService
    service = AnalysisService(stacks)
    (tmp_path / "requirements.txt").write_text("psycopg2\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await service.analyze(tmp_path)
    assert result.system_packages == []
