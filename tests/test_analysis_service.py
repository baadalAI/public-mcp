import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import RepoAnalysis, StackInfo, DatabaseInfo, TrafficTier
from computeedge.services.analysis import AnalysisService


@pytest.fixture
def analysis_service():
    stacks_config = load_bundled_yaml("stacks.yaml")
    return AnalysisService(stacks_config)


@pytest.mark.asyncio
async def test_detect_nextjs(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_nextjs")
    assert result.stack.frontend == "nextjs"
    assert result.stack.frontend_version == "14"
    assert result.stack.backend is None
    assert result.database is None
    assert result.estimated_complexity == "low"


@pytest.mark.asyncio
async def test_detect_fastapi_with_postgres(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_fastapi")
    assert result.stack.backend == "fastapi"
    assert result.stack.backend_language == "python"
    assert result.database is not None
    assert result.database.type == "postgres"
    assert result.database.orm == "sqlalchemy"
    assert result.estimated_complexity == "medium"


@pytest.mark.asyncio
async def test_detect_express_with_mongodb(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_express")
    assert result.stack.backend == "express"
    assert result.stack.backend_language == "javascript"
    assert result.database is not None
    assert result.database.type == "mongodb"
    assert result.database.orm == "mongoose"


@pytest.mark.asyncio
async def test_detect_fullstack(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_fullstack")
    assert result.stack.frontend == "nextjs"
    assert result.stack.backend == "fastapi"
    assert result.database is not None
    assert result.database.type == "postgres"
    assert "redis" in result.services
    assert result.has_docker_compose is True
    assert result.estimated_complexity == "high"


@pytest.mark.asyncio
async def test_detect_monorepo_frontend_in_subdir(analysis_service, fixtures_dir):
    """Frontend should be detected even when package.json with react is in a subdirectory
    and root package.json exists without react."""
    result = await analysis_service.analyze(fixtures_dir / "sample_monorepo")
    assert result.stack.frontend == "react"
    assert result.stack.backend == "fastapi"


@pytest.mark.asyncio
async def test_detect_unknown_stack(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_unknown")
    assert result.stack.frontend is None
    assert result.stack.backend == "go"
    assert result.stack.backend_language == "go"


@pytest.mark.asyncio
async def test_detect_env_vars(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_nextjs")
    assert "NEXT_PUBLIC_API_URL" in result.env_vars_needed
    assert "SECRET_KEY" in result.env_vars_needed


@pytest.mark.asyncio
async def test_detect_dockerfile(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_nextjs")
    assert result.has_dockerfile is False


@pytest.mark.asyncio
async def test_nonexistent_path(analysis_service):
    from computeedge.exceptions import AnalysisError
    with pytest.raises(AnalysisError):
        await analysis_service.analyze("/nonexistent/path")


@pytest.mark.asyncio
async def test_estimate_resources_low_traffic_frontend_only(
    analysis_service, fixtures_dir
):
    analysis = await analysis_service.analyze(fixtures_dir / "sample_nextjs")
    estimate = analysis_service.estimate_resources(analysis, TrafficTier.LOW)
    assert estimate.ram_mb == 256
    assert estimate.cpu_vcpu == 0.5
    assert estimate.storage_gb == 1
    assert estimate.needs_database is False
    assert estimate.traffic_tier == "low"


@pytest.mark.asyncio
async def test_estimate_resources_low_traffic_with_db(
    analysis_service, fixtures_dir
):
    analysis = await analysis_service.analyze(fixtures_dir / "sample_fastapi")
    estimate = analysis_service.estimate_resources(analysis, TrafficTier.LOW)
    assert estimate.ram_mb == 1024
    assert estimate.cpu_vcpu == 1
    assert estimate.storage_gb == 10
    assert estimate.needs_database is True
    assert estimate.database_type == "postgres"
    assert estimate.database_ram_mb == 256


@pytest.mark.asyncio
async def test_estimate_resources_medium_traffic(
    analysis_service, fixtures_dir
):
    analysis = await analysis_service.analyze(fixtures_dir / "sample_fastapi")
    estimate = analysis_service.estimate_resources(analysis, TrafficTier.MEDIUM)
    assert estimate.ram_mb == 2048
    assert estimate.cpu_vcpu == 2
    assert estimate.storage_gb == 20


@pytest.mark.asyncio
async def test_estimate_resources_high_traffic(
    analysis_service, fixtures_dir
):
    analysis = await analysis_service.analyze(fixtures_dir / "sample_fastapi")
    estimate = analysis_service.estimate_resources(analysis, TrafficTier.HIGH)
    assert estimate.ram_mb == 4096
    assert estimate.cpu_vcpu == 4
    assert estimate.storage_gb == 50


@pytest.mark.asyncio
async def test_estimate_resources_fullstack_with_redis(
    analysis_service, fixtures_dir
):
    analysis = await analysis_service.analyze(
        fixtures_dir / "sample_fullstack"
    )
    estimate = analysis_service.estimate_resources(analysis, TrafficTier.LOW)
    assert estimate.ram_mb == 1536
    assert estimate.cpu_vcpu == 1.5
    assert estimate.needs_database is True
    assert "redis" in analysis.services
