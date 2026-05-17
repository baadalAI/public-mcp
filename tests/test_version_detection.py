import pytest
from pathlib import Path
from computeedge.config.loader import load_bundled_yaml
from computeedge.services.analysis import AnalysisService

@pytest.fixture
def analysis_service():
    stacks = load_bundled_yaml("stacks.yaml")
    system_deps = load_bundled_yaml("system_deps.yaml")
    return AnalysisService(stacks, system_deps_config=system_deps)

@pytest.mark.asyncio
async def test_python_version_from_python_version_file(analysis_service, tmp_path):
    (tmp_path / ".python-version").write_text("3.12.1\n")
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await analysis_service.analyze(tmp_path)
    assert result.python_version == "3.12"

@pytest.mark.asyncio
async def test_python_version_from_pyproject_requires_python(analysis_service, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n')
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await analysis_service.analyze(tmp_path)
    assert result.python_version == "3.11"

@pytest.mark.asyncio
async def test_python_version_from_runtime_txt(analysis_service, tmp_path):
    (tmp_path / "runtime.txt").write_text("python-3.10.4\n")
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await analysis_service.analyze(tmp_path)
    assert result.python_version == "3.10"

@pytest.mark.asyncio
async def test_python_version_fallback_none(analysis_service, tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    result = await analysis_service.analyze(tmp_path)
    assert result.python_version is None

@pytest.mark.asyncio
async def test_node_version_from_engines(analysis_service, tmp_path):
    (tmp_path / "package.json").write_text('{"engines": {"node": ">=20"}, "dependencies": {"express": "4"}}')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("const express = require('express')")
    result = await analysis_service.analyze(tmp_path)
    assert result.node_version == "20"

@pytest.mark.asyncio
async def test_node_version_from_nvmrc(analysis_service, tmp_path):
    (tmp_path / ".nvmrc").write_text("v18.17.0\n")
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "4"}}')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("const express = require('express')")
    result = await analysis_service.analyze(tmp_path)
    assert result.node_version == "18"

@pytest.mark.asyncio
async def test_node_version_from_node_version_file(analysis_service, tmp_path):
    (tmp_path / ".node-version").write_text("16\n")
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "4"}}')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("const express = require('express')")
    result = await analysis_service.analyze(tmp_path)
    assert result.node_version == "16"

@pytest.mark.asyncio
async def test_node_version_fallback_none(analysis_service, tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "4"}}')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("const express = require('express')")
    result = await analysis_service.analyze(tmp_path)
    assert result.node_version is None

@pytest.mark.asyncio
async def test_go_version_from_go_mod(analysis_service, tmp_path):
    (tmp_path / "go.mod").write_text("module myapp\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}")
    result = await analysis_service.analyze(tmp_path)
    assert result.go_version == "1.22"

@pytest.mark.asyncio
async def test_go_version_fallback_none(analysis_service, tmp_path):
    (tmp_path / "main.go").write_text("package main\nfunc main() {}")
    result = await analysis_service.analyze(tmp_path)
    assert result.go_version is None
