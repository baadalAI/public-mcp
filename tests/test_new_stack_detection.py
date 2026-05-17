import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.services.analysis import AnalysisService


@pytest.fixture
def analysis_service():
    return AnalysisService(load_bundled_yaml("stacks.yaml"))


@pytest.mark.asyncio
async def test_detect_django(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_django")
    assert result.stack.backend == "django"
    assert result.stack.backend_language == "python"


@pytest.mark.asyncio
async def test_detect_flask(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_flask")
    assert result.stack.backend == "flask"
    assert result.stack.backend_language == "python"


@pytest.mark.asyncio
async def test_detect_rails(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_rails")
    assert result.stack.backend == "rails"
    assert result.stack.backend_language == "ruby"


@pytest.mark.asyncio
async def test_detect_go(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_go")
    assert result.stack.backend == "go"
    assert result.stack.backend_language == "go"


@pytest.mark.asyncio
async def test_detect_vue(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_vue")
    assert result.stack.frontend == "vue"
    assert result.stack.frontend_version == "3"


@pytest.mark.asyncio
async def test_django_deployable(analysis_service):
    """Django should be marked deployable in stacks.yaml."""
    config = load_bundled_yaml("stacks.yaml")
    assert config["backend"]["django"].get("deployable") is True


@pytest.mark.asyncio
async def test_vue_deployable(analysis_service):
    """Vue should be marked deployable in stacks.yaml."""
    config = load_bundled_yaml("stacks.yaml")
    assert config["frontend"]["vue"].get("deployable") is True


@pytest.mark.asyncio
async def test_django_has_template_type(analysis_service):
    config = load_bundled_yaml("stacks.yaml")
    assert config["backend"]["django"].get("template_type") == "language"
    assert config["backend"]["django"].get("language") == "python"


@pytest.mark.asyncio
async def test_go_has_entry_points(analysis_service):
    config = load_bundled_yaml("stacks.yaml")
    assert "entry_points" in config["backend"]["go"]
    assert "main.go" in config["backend"]["go"]["entry_points"]
