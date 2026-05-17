import pytest
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from computeedge.models import RepoAnalysis, StackInfo, ValidationIssue

@pytest.fixture
def deployment_service(state_manager):
    from computeedge.services.deployment import DeploymentService
    from computeedge.config.loader import load_bundled_yaml
    stacks = load_bundled_yaml("stacks.yaml")
    service = DeploymentService(
        provider=MagicMock(),
        state=state_manager,
        stacks_config=stacks,
    )
    service._ssh = MagicMock()
    return service

def test_validate_port_consistency_warning(deployment_service, tmp_path):
    analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
    generated = {
        "Dockerfile": "FROM python:3.11\nEXPOSE 8000",
        "docker-compose.yml": 'services:\n  backend:\n    ports:\n      - "3000:3000"',
    }
    issues = deployment_service._validate_port_consistency(analysis, generated)
    warnings = [i for i in issues if i.severity == "warning" and i.check == "port"]
    assert len(warnings) >= 1

def test_validate_no_port_issues_clean_project(deployment_service, tmp_path):
    analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
    generated = {
        "Dockerfile": "FROM python:3.11\nEXPOSE 8000",
        "docker-compose.yml": 'services:\n  backend:\n    ports:\n      - "8000:8000"',
    }
    issues = deployment_service._validate_port_consistency(analysis, generated)
    assert len(issues) == 0
