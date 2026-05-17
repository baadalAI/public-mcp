import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.services.analysis import AnalysisService


@pytest.fixture
def analysis_service():
    return AnalysisService(load_bundled_yaml("stacks.yaml"))


@pytest.mark.asyncio
async def test_detect_alembic_migration(analysis_service, fixtures_dir):
    """sample_fastapi has alembic.ini -> should detect alembic migration command."""
    result = await analysis_service.analyze(fixtures_dir / "sample_fastapi")
    assert result.migration_command == "python -m alembic upgrade head"


@pytest.mark.asyncio
async def test_detect_prisma_migration(analysis_service, fixtures_dir):
    """sample_fullstack has prisma/schema.prisma -> should detect prisma migration."""
    result = await analysis_service.analyze(fixtures_dir / "sample_fullstack")
    assert result.migration_command == "npx prisma migrate deploy"


@pytest.mark.asyncio
async def test_no_migration_for_express_mongo(analysis_service, fixtures_dir):
    """sample_express uses MongoDB/mongoose -> no migration command (schema-less)."""
    result = await analysis_service.analyze(fixtures_dir / "sample_express")
    assert result.migration_command is None


@pytest.mark.asyncio
async def test_no_migration_for_frontend_only(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_nextjs")
    assert result.migration_command is None


@pytest.mark.asyncio
async def test_detect_django_migration(analysis_service, fixtures_dir):
    """sample_django has manage.py + django backend -> python manage.py migrate."""
    result = await analysis_service.analyze(fixtures_dir / "sample_django")
    assert result.migration_command == "python manage.py migrate"


@pytest.mark.asyncio
async def test_detect_rails_migration(analysis_service, fixtures_dir):
    """sample_rails has config/routes.rb -> rails db:migrate."""
    result = await analysis_service.analyze(fixtures_dir / "sample_rails")
    assert result.migration_command == "rails db:migrate"


@pytest.mark.asyncio
async def test_detect_flask_migrate(analysis_service, fixtures_dir):
    """sample_flask_migrate has flask-migrate in requirements -> flask db upgrade."""
    result = await analysis_service.analyze(fixtures_dir / "sample_flask_migrate")
    assert result.migration_command == "flask db upgrade"


@pytest.mark.asyncio
async def test_flask_migrate_takes_priority_over_alembic(analysis_service, fixtures_dir):
    """When both alembic.ini and flask-migrate exist, prefer flask db upgrade."""
    result = await analysis_service.analyze(fixtures_dir / "sample_flask_migrate")
    # sample_flask_migrate has BOTH alembic.ini and flask-migrate in requirements
    assert result.migration_command == "flask db upgrade"
    assert result.migration_command != "python -m alembic upgrade head"
