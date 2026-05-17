from pathlib import Path

import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.services.analysis import AnalysisService
from computeedge.services.pricing import PricingService
from computeedge.tools.analyze import make_analyze_tool
from computeedge.tools.estimate import make_estimate_tool
from computeedge.tools.compare import make_compare_tool


@pytest.fixture
def analysis_service():
    return AnalysisService(load_bundled_yaml("stacks.yaml"))


@pytest.fixture
def pricing_service():
    return PricingService(load_bundled_yaml("providers.yaml"))


@pytest.mark.asyncio
async def test_analyze_tool_local_path(analysis_service, fixtures_dir):
    analyze = make_analyze_tool(analysis_service)
    result = await analyze(str(fixtures_dir / "sample_nextjs"))
    assert "stack" in result
    assert result["stack"]["frontend"] == "nextjs"


@pytest.mark.asyncio
async def test_analyze_tool_nonexistent_path(analysis_service):
    analyze = make_analyze_tool(analysis_service)
    result = await analyze("/nonexistent/path")
    assert "error" in result


@pytest.mark.asyncio
async def test_estimate_tool(analysis_service, fixtures_dir):
    estimate = make_estimate_tool(analysis_service)
    result = await estimate(str(fixtures_dir / "sample_fastapi"), "low")
    assert "ram_mb" in result
    assert result["needs_database"] is True
    assert result["traffic_tier"] == "low"


@pytest.mark.asyncio
async def test_compare_tool_with_repo_path(analysis_service, pricing_service, fixtures_dir):
    compare = make_compare_tool(analysis_service, pricing_service)
    result = await compare(repo_path=str(fixtures_dir / "sample_fastapi"))
    assert "recommendations" in result
    assert "top_pick" in result
    assert len(result["recommendations"]) > 0


@pytest.mark.asyncio
async def test_compare_tool_with_explicit_params(
    analysis_service, pricing_service
):
    compare = make_compare_tool(analysis_service, pricing_service)
    result = await compare(
        stack="fastapi",
        estimated_ram_mb=1024,
        estimated_cpu_vcpu=1,
        estimated_storage_gb=10,
        needs_database=True,
        database_type="postgres",
        expected_traffic="low",
    )
    assert "recommendations" in result
    assert "top_pick" in result


@pytest.mark.asyncio
async def test_analyze_tool_returns_dict(analysis_service, fixtures_dir):
    analyze = make_analyze_tool(analysis_service)
    result = await analyze(str(fixtures_dir / "sample_fullstack"))
    assert isinstance(result, dict)
    assert isinstance(result["stack"], dict)
    assert isinstance(result["services"], list)
