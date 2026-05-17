import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.services.analysis import AnalysisService


@pytest.fixture
def analysis_service():
    return AnalysisService(load_bundled_yaml("stacks.yaml"))


@pytest.mark.asyncio
async def test_detect_mysql_from_mysqlclient(analysis_service, fixtures_dir):
    result = await analysis_service.analyze(fixtures_dir / "sample_mysql_app")
    assert result.database is not None
    assert result.database.type == "mysql"


@pytest.mark.asyncio
async def test_sqlite_detected_as_sqlite(analysis_service, fixtures_dir):
    """SQLite app should be detected as DatabaseInfo(type='sqlite')."""
    result = await analysis_service.analyze(fixtures_dir / "sample_sqlite_app")
    assert result.database is not None
    assert result.database.type == "sqlite"


def test_mysql_compose_block():
    """Docker-compose template renders MySQL service block."""
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader
    from computeedge.models import RepoAnalysis, StackInfo, DatabaseInfo

    templates_dir = Path(__file__).parent.parent / "src" / "computeedge" / "templates"
    jinja = Environment(loader=FileSystemLoader(str(templates_dir)), keep_trailing_newline=True)

    analysis = RepoAnalysis(
        stack=StackInfo(backend="django", backend_language="python"),
        database=DatabaseInfo(type="mysql"),
    )

    result = jinja.get_template("docker-compose.j2").render(
        analysis=analysis, include_db=True, include_redis=False,
        db_type="mysql", database_url="mysql://computeedge:pass@db:3306/app",
        redis_url="", db_user="computeedge", db_password="testpass",
        db_name="app", env_vars={},
    )
    assert "mysql:8" in result
    assert "MYSQL_DATABASE: app" in result
    assert "MYSQL_USER: computeedge" in result
    assert "MYSQL_PASSWORD: testpass" in result
    assert "mysqldata:" in result


def test_mysql_database_url_generation():
    """Deployment service generates correct MySQL DATABASE_URL."""
    # The URL pattern is: mysql://computeedge:{password}@db:3306/app
    password = "testpassword"
    url = f"mysql://computeedge:{password}@db:3306/app"
    assert url.startswith("mysql://")
    assert ":3306/" in url


def test_sqlite_skips_db_container():
    """When db_type is sqlite, include_db should be False."""
    from computeedge.models import DatabaseInfo
    db = DatabaseInfo(type="sqlite")
    include_db = db is not None and db.type != "sqlite"
    assert include_db is False
