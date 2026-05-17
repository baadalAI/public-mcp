"""AnalysisService: detects the tech stack of a local repository."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from computeedge.exceptions import AnalysisError
from computeedge.models import (
    DatabaseInfo,
    RepoAnalysis,
    ResourceEstimate,
    StackInfo,
    TrafficTier,
)
from computeedge.utils.logger import get_logger

logger = get_logger("analysis")


class AnalysisService:
    """Analyzes a local repository directory and returns a RepoAnalysis."""

    def __init__(self, stacks_config: dict[str, Any], system_deps_config: dict[str, Any] | None = None) -> None:
        self._config = stacks_config
        self._system_deps = system_deps_config or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(self, repo_path: str | Path) -> RepoAnalysis:
        """Analyze the repository at *repo_path* and return a RepoAnalysis.

        Raises:
            AnalysisError: if the path does not exist or is not a directory.
        """
        repo_path = Path(repo_path)
        logger.info("Analyzing repository: %s", repo_path)
        if not repo_path.exists() or not repo_path.is_dir():
            logger.error("Repository path does not exist or is not a directory: %s", repo_path)
            raise AnalysisError(f"Repository path does not exist or is not a directory: {repo_path}")

        # Collect all files (root + one level deep)
        all_files = self._collect_files(repo_path)

        # Read text content of dependency and source files
        all_text = self._read_relevant_files(repo_path, all_files)

        # Detect frameworks
        frontend, frontend_version, frontend_path = self._detect_frontend(repo_path, all_files, all_text)
        backend, backend_language, backend_version, backend_path = self._detect_backend(repo_path, all_files, all_text)

        # Promote Streamlit to frontend when it coexists with another backend
        if backend and backend != "streamlit" and frontend is None:
            streamlit_config = self._config.get("backend", {}).get("streamlit", {})
            for indicator in streamlit_config.get("indicators", []):
                streamlit_path = self._check_indicator_path(repo_path, all_files, indicator)
                if streamlit_path is not None:
                    frontend = "streamlit"
                    frontend_path = streamlit_path
                    frontend_version = self._extract_version(repo_path, all_files, "streamlit")
                    break

        package_manager = self._detect_package_manager(all_files)

        build_output_dir = self._detect_build_output_dir(repo_path, all_files, all_text, frontend)
        server_entry = self._detect_server_entry(repo_path, all_files) if backend else None
        deploy_topology = self._detect_topology(
            repo_path, all_files, all_text,
            frontend, frontend_path, backend, backend_path, server_entry,
        )

        stack = StackInfo(
            frontend=frontend,
            frontend_version=frontend_version,
            frontend_path=frontend_path,
            backend=backend,
            backend_language=backend_language,
            backend_version=backend_version,
            backend_path=backend_path,
            package_manager=package_manager,
            deploy_topology=deploy_topology,
            build_output_dir=build_output_dir,
            server_entry=server_entry,
        )

        # Detect database
        database = self._detect_database(repo_path, all_files, all_text)

        # Detect extra services
        services = self._detect_services(all_text)

        # Detect Docker artifacts
        has_dockerfile = self._file_exists(repo_path, all_files, "Dockerfile")
        has_docker_compose = self._file_exists(repo_path, all_files, "docker-compose.yml") or \
                             self._file_exists(repo_path, all_files, "docker-compose.yaml")

        # Extract env vars
        env_vars_needed = self._extract_env_vars(repo_path, all_files)
        env_vars_required, env_vars_optional = self._classify_env_vars(
            env_vars_needed, repo_path, all_files
        )

        # Detect migration command
        migration_command = self._detect_migration_command(
            repo_path, all_files, all_text, database, backend
        )

        # Estimate complexity
        component_count = sum([
            frontend is not None,
            backend is not None,
            database is not None,
        ])
        if component_count >= 3:
            complexity = "high"
        elif component_count == 2:
            complexity = "medium"
        else:
            complexity = "low"

        system_packages = self._scan_system_dependencies(repo_path)

        python_version = self._detect_python_version(repo_path)
        node_version = self._detect_node_version(repo_path)
        go_version = self._detect_go_version(repo_path)

        return RepoAnalysis(
            stack=stack,
            database=database,
            services=services,
            has_dockerfile=has_dockerfile,
            has_docker_compose=has_docker_compose,
            estimated_complexity=complexity,
            env_vars_needed=env_vars_needed,
            env_vars_required=env_vars_required,
            env_vars_optional=env_vars_optional,
            migration_command=migration_command,
            system_packages=system_packages,
            python_version=python_version,
            node_version=node_version,
            go_version=go_version,
        )

    def estimate_resources(
        self,
        analysis: RepoAnalysis,
        traffic_tier: TrafficTier = TrafficTier.LOW,
    ) -> ResourceEstimate:
        """Map a RepoAnalysis to a ResourceEstimate using the stacks.yaml resources table."""
        resource_key = self._get_resource_key(analysis)
        tier_key = traffic_tier.value  # "low" | "medium" | "high"

        resources_config = self._config.get("resources", {})
        tier_config = resources_config.get(tier_key, {})
        entry = tier_config.get(resource_key, {})

        notes = self._build_notes(resource_key, tier_key, analysis)

        return ResourceEstimate(
            ram_mb=entry.get("ram_mb", 512),
            cpu_vcpu=entry.get("cpu_vcpu", 1.0),
            storage_gb=entry.get("storage_gb", 10),
            needs_database=analysis.database is not None,
            database_type=analysis.database.type if analysis.database else None,
            database_ram_mb=entry.get("database_ram_mb", 0),
            traffic_tier=tier_key,
            notes=notes,
        )

    def _build_notes(self, resource_key: str, tier: str, analysis: RepoAnalysis) -> str:
        """Generate contextual notes about the resource estimate."""
        parts: list[str] = []
        components = []
        if analysis.stack.frontend:
            components.append(analysis.stack.frontend)
        if analysis.stack.backend:
            components.append(analysis.stack.backend)
        if analysis.database:
            components.append(analysis.database.type)

        if tier == "low":
            parts.append(
                "Running all services on a single instance via Docker Compose "
                "is sufficient for this traffic level."
            )
        elif tier == "medium":
            parts.append(
                "Consider dedicated database hosting for better reliability "
                "at this traffic level."
            )
        else:
            parts.append(
                "High-traffic setup — consider horizontal scaling and "
                "managed database services."
            )

        if components:
            parts.append(f"Detected stack: {', '.join(components)}.")

        return " ".join(parts)

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------

    # Directories that should never be traversed during file collection.
    _SKIP_DIRS: set[str] = {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
        ".next", ".nuxt",
    }

    def _collect_files(self, repo_path: Path) -> list[Path]:
        """Return all source/config files in the repo, scanning the full tree.

        Skips common non-source directories (node_modules, .git, etc.) to
        keep the scan fast without imposing an arbitrary depth limit.
        """
        files: list[Path] = []
        try:
            for item in repo_path.rglob("*"):
                try:
                    relative = item.relative_to(repo_path)
                    # Skip entire subtrees that are never useful
                    if self._SKIP_DIRS & set(relative.parts):
                        continue
                    if item.is_file():
                        files.append(item)
                except ValueError:
                    pass
        except PermissionError:
            pass
        return files

    def _file_exists(self, repo_path: Path, all_files: list[Path], name: str) -> bool:
        """Return True if a file named *name* exists anywhere in the collected files."""
        for f in all_files:
            if f.name == name:
                return True
        return False

    def _get_file_path(self, repo_path: Path, all_files: list[Path], name: str) -> Path | None:
        """Return the Path of the first file matching *name*, or None."""
        for f in all_files:
            if f.name == name:
                return f
        return None

    def _read_file_safe(self, path: Path) -> str:
        """Read a file and return its contents; return empty string on error."""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _read_relevant_files(self, repo_path: Path, all_files: list[Path]) -> str:
        """Read dependency and source files and return concatenated text."""
        relevant_extensions = {".json", ".toml", ".txt", ".py", ".js", ".ts", ".mjs", ".yaml", ".yml", ".prisma", ".mod", ".ini"}
        relevant_names = {"Gemfile", "Cargo.toml", "go.mod", "requirements.txt", "package.json", "pyproject.toml", "alembic.ini"}
        parts: list[str] = []
        for f in all_files:
            if f.suffix in relevant_extensions or f.name in relevant_names:
                parts.append(self._read_file_safe(f))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Indicator checking
    # ------------------------------------------------------------------

    def _check_indicator(
        self,
        repo_path: Path,
        all_files: list[Path],
        indicator: dict[str, Any],
    ) -> bool:
        """Evaluate a single indicator dict from stacks.yaml.

        Supported keys:
        - file: filename that must exist
        - contains: string that the file must contain (optional)
        - excludes: string that the file must NOT contain (optional)

        Checks ALL files matching the name (e.g. both root/package.json
        and frontend/package.json) and returns True if any match.
        """
        return self._check_indicator_path(repo_path, all_files, indicator) is not None

    def _check_indicator_path(
        self,
        repo_path: Path,
        all_files: list[Path],
        indicator: dict[str, Any],
    ) -> str | None:
        """Evaluate an indicator and return the relative directory path if matched, else None."""
        filename = indicator.get("file")
        if filename is None:
            return None

        matching_files = [f for f in all_files if f.name == filename]
        if not matching_files:
            return None

        contains = indicator.get("contains")
        excludes = indicator.get("excludes")

        for file_path in matching_files:
            if contains is None and excludes is None:
                return str(file_path.parent.relative_to(repo_path))

            content = self._read_file_safe(file_path)

            if contains is not None and contains not in content:
                continue

            if excludes is not None and excludes in content:
                continue

            return str(file_path.parent.relative_to(repo_path))

        return None

    # ------------------------------------------------------------------
    # Frontend detection
    # ------------------------------------------------------------------

    def _detect_frontend(
        self,
        repo_path: Path,
        all_files: list[Path],
        all_text: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Return (framework_name, version, relative_path) or (None, None, None)."""
        frontend_config: dict[str, Any] = self._config.get("frontend", {})

        for framework, info in frontend_config.items():
            indicators: list[dict] = info.get("indicators", [])
            for indicator in indicators:
                rel_path = self._check_indicator_path(repo_path, all_files, indicator)
                if rel_path is not None:
                    version = self._extract_version(repo_path, all_files, framework)
                    return framework, version, rel_path

        return None, None, None

    # ------------------------------------------------------------------
    # Backend detection
    # ------------------------------------------------------------------

    def _detect_backend(
        self,
        repo_path: Path,
        all_files: list[Path],
        all_text: str,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Return (framework_name, language, version, relative_path) or (None, None, None, None)."""
        backend_config: dict[str, Any] = self._config.get("backend", {})

        for framework, info in backend_config.items():
            indicators: list[dict] = info.get("indicators", [])
            for indicator in indicators:
                rel_path = self._check_indicator_path(repo_path, all_files, indicator)
                if rel_path is not None:
                    language = info.get("language")
                    version = self._extract_version(repo_path, all_files, framework)

                    # Detect TypeScript for JS backends
                    if language == "javascript" and self._file_exists(repo_path, all_files, "tsconfig.json"):
                        language = "typescript"

                    return framework, language, version, rel_path

        return None, None, None, None

    # ------------------------------------------------------------------
    # Database detection
    # ------------------------------------------------------------------

    def _detect_database(
        self,
        repo_path: Path,
        all_files: list[Path],
        all_text: str,
    ) -> DatabaseInfo | None:
        """Detect database type and ORM from file contents."""
        db_config: dict[str, Any] = self._config.get("database", {})

        # Known ORM / client library names that appear as file_contains keywords
        _known_orm_keywords: set[str] = {
            "sqlalchemy", "prisma", "mongoose", "pymongo",
            "django_orm", "psycopg", "mysql2", "pymysql",
        }

        for db_type, info in db_config.items():
            indicators: list[dict] = info.get("indicators", [])
            for indicator in indicators:
                keywords: list[str] = indicator.get("file_contains", [])
                matched_kw: str | None = None
                for kw in keywords:
                    if kw in all_text:
                        matched_kw = kw
                        break

                if matched_kw is not None:
                    orm_indicators: dict[str, list[str]] = info.get("orm_indicators", {})
                    orm = self._detect_orm(orm_indicators, all_files, all_text)

                    # Fallback: if no orm found via orm_indicators, check if the
                    # matched keyword is itself a known ORM/client library name
                    if orm is None and matched_kw in _known_orm_keywords:
                        orm = matched_kw

                    # Second fallback: check all keywords for a known ORM name
                    if orm is None:
                        for kw in keywords:
                            if kw in _known_orm_keywords and kw in all_text:
                                orm = kw
                                break

                    has_migrations = self._check_has_migrations(repo_path, all_files, orm)
                    return DatabaseInfo(type=db_type, orm=orm, has_migrations=has_migrations)

                # Check file_exists indicators (e.g., sqlite: db.sqlite3)
                file_exists_name = indicator.get("file_exists")
                if file_exists_name and matched_kw is None:
                    if self._file_exists(repo_path, all_files, file_exists_name):
                        return DatabaseInfo(type=db_type, orm=None, has_migrations=False)

        return None

    def _detect_orm(
        self,
        orm_indicators: dict[str, list[str]],
        all_files: list[Path],
        all_text: str,
    ) -> str | None:
        """Return the first ORM whose indicator files or keywords are found.

        Strategy:
        1. Check if the ORM name itself appears as a keyword in all_text.
        2. Check if any of the indicator paths (files/dirs) exist.
        """
        for orm_name, indicator_list in orm_indicators.items():
            # Check if the ORM name is a keyword in the collected text
            # e.g. "sqlalchemy" appears in requirements.txt
            if orm_name in all_text:
                return orm_name

            # Check indicator files/paths
            for indicator in indicator_list:
                indicator_path = indicator.rstrip("/")
                for f in all_files:
                    if f.name == indicator_path or indicator_path in str(f):
                        return orm_name

        return None

    def _check_has_migrations(
        self,
        repo_path: Path,
        all_files: list[Path],
        orm: str | None,
    ) -> bool:
        """Return True if migration files are detected for the given ORM."""
        if orm == "sqlalchemy":
            return self._file_exists(repo_path, all_files, "alembic.ini")
        if orm == "prisma":
            for f in all_files:
                if "migrations" in str(f) and f.suffix == ".sql":
                    return True
        return False

    # ------------------------------------------------------------------
    # Migration command detection
    # ------------------------------------------------------------------

    def _detect_migration_command(
        self,
        repo_path: Path,
        all_files: list[Path],
        all_text: str,
        database: DatabaseInfo | None,
        backend: str | None,
    ) -> str | None:
        """Detect the migration tool and return the CLI command to run migrations."""
        if database is None:
            return None

        # Django
        if self._file_exists(repo_path, all_files, "manage.py") and backend == "django":
            return "python manage.py migrate"

        # Rails
        if self._file_exists(repo_path, all_files, "routes.rb") and backend == "rails":
            return "rails db:migrate"

        # Flask-Migrate (prefer over raw Alembic when flask-migrate is present)
        if backend == "flask" and "flask-migrate" in all_text:
            return "flask db upgrade"

        # Alembic (FastAPI/SQLAlchemy or Flask without flask-migrate)
        if self._file_exists(repo_path, all_files, "alembic.ini"):
            return "python -m alembic upgrade head"

        # Prisma (Express/Next.js)
        if self._file_exists(repo_path, all_files, "schema.prisma"):
            return "npx prisma migrate deploy"

        # Knex (Express)
        if "knex" in all_text:
            return "npx knex migrate:latest"

        # TypeORM (Express)
        if "typeorm" in all_text:
            return "npx typeorm migration:run"

        return None

    # ------------------------------------------------------------------
    # Services detection
    # ------------------------------------------------------------------

    def _detect_services(self, all_text: str) -> list[str]:
        """Return list of extra services found (redis, s3, websockets)."""
        services_config: dict[str, Any] = self._config.get("services", {})
        detected: list[str] = []

        for service, info in services_config.items():
            indicators: list[dict] = info.get("indicators", [])
            for indicator in indicators:
                keywords: list[str] = indicator.get("file_contains", [])
                if any(kw in all_text for kw in keywords):
                    detected.append(service)
                    break

        return detected

    # ------------------------------------------------------------------
    # Env var extraction
    # ------------------------------------------------------------------

    # Env var names that ComputeEdge generates automatically — never "required"
    _AUTO_GENERATED_VARS: set[str] = {"DATABASE_URL", "SECRET_KEY", "REDIS_URL"}

    # Patterns that indicate a placeholder, not a real default
    _PLACEHOLDER_PATTERNS: tuple[str, ...] = (
        "your-", "changeme", "xxx", "TODO", "FIXME", "replace",
        "<", "example", "placeholder", "put-your", "sk-",
    )

    def _is_placeholder(self, value: str) -> bool:
        """Return True if the value looks like a placeholder, not a real default."""
        if not value or not value.strip():
            return True
        v = value.strip()
        return any(p in v.lower() for p in self._PLACEHOLDER_PATTERNS)

    # Regex patterns that capture env var names from source code.
    # Note: import.meta.env.* (Vite/Astro) is excluded — those are build-time
    # constants baked into the frontend bundle, not runtime env vars.
    _CODE_ENV_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"""os\.getenv\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""),
        re.compile(r"""os\.environ\[?\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""),
        re.compile(r"""os\.environ\.get\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""),
        re.compile(r"""process\.env\.([A-Za-z_][A-Za-z0-9_]*)"""),
    ]

    # Env var names that are standard runtime/infra noise, not app config.
    _NOISE_ENV_VARS: set[str] = {
        "PATH", "HOME", "USER", "SHELL", "LANG", "TERM", "PWD",
        "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
        "NODE_ENV", "CI", "PORT", "HOST",
    }

    def _extract_env_vars(self, repo_path: Path, all_files: list[Path]) -> list[str]:
        """Extract env var names from .env files and source code references."""
        env_file_names = {
            ".env", ".env.example", ".env.sample", ".env.local", ".env.template",
        }
        seen: set[str] = set()
        result: list[str] = []

        def _add(key: str) -> None:
            if key not in seen and key not in self._NOISE_ENV_VARS:
                seen.add(key)
                result.append(key)

        # 1. Scan .env* files for KEY=value lines
        for f in all_files:
            if f.name in env_file_names:
                content = self._read_file_safe(f)
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
                    if match:
                        _add(match.group(1))

        # 2. Scan source files for runtime env var references
        source_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs"}
        for f in all_files:
            if f.suffix in source_extensions:
                content = self._read_file_safe(f)
                for pattern in self._CODE_ENV_PATTERNS:
                    for m in pattern.finditer(content):
                        _add(m.group(1))

        return result

    def _classify_env_vars(self, env_vars_needed: list[str], repo_path: Path,
                           all_files: list[Path]) -> tuple[list[str], list[str]]:
        """Classify env vars into required (placeholder/empty) and optional (real default).

        Returns (required, optional) lists. Auto-generated vars are excluded from required.
        """
        env_file_names = {".env.example", ".env.sample", ".env.template"}
        var_values: dict[str, str | None] = {}

        for f in all_files:
            if f.name in env_file_names:
                content = self._read_file_safe(f)
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
                    if match:
                        key, value = match.group(1), match.group(2)
                        if key not in var_values:
                            var_values[key] = value

        required: list[str] = []
        optional: list[str] = []

        for var in env_vars_needed:
            if var in self._AUTO_GENERATED_VARS:
                continue
            value = var_values.get(var)
            if value is None or self._is_placeholder(value):
                if var not in required:
                    required.append(var)
            else:
                if var not in optional:
                    optional.append(var)

        return required, optional

    # ------------------------------------------------------------------
    # Topology detection
    # ------------------------------------------------------------------

    def _detect_topology(
        self,
        repo_path: Path,
        all_files: list[Path],
        all_text: str,
        frontend: str | None,
        frontend_path: str | None,
        backend: str | None,
        backend_path: str | None,
        server_entry: str | None,
    ) -> str:
        """Determine the deployment topology.

        Returns one of:
        - "single"        — monorepo: one process serves both frontend and backend
        - "split"         — separate frontend and backend containers
        - "frontend_only" — SPA with no backend
        - "backend_only"  — API with no frontend
        """
        if not frontend and not backend:
            return "backend_only"
        if frontend and not backend:
            return "frontend_only"
        if backend and not frontend:
            return "backend_only"

        # Both frontend and backend detected — always deploy as a single
        # unified container (frontend build stage + backend runtime + embedded nginx).
        return "single"

    # ------------------------------------------------------------------
    # Build output directory detection
    # ------------------------------------------------------------------

    def _detect_build_output_dir(
        self,
        repo_path: Path,
        all_files: list[Path],
        all_text: str,
        frontend: str | None,
    ) -> str | None:
        """Detect where the frontend build tool outputs its files.

        Priority:
        1. vite.config outDir setting
        2. Presence of vite.config.* → "dist"
        3. react-scripts (CRA) → "build"
        4. Next.js → ".next"
        5. stacks.yaml output_dir field
        6. None (let template default)
        """
        if frontend == "nextjs":
            return ".next"

        # Check explicit outDir in vite.config
        for vite_name in ("vite.config.ts", "vite.config.js", "vite.config.mjs"):
            vite_path = self._get_file_path(repo_path, all_files, vite_name)
            if vite_path:
                content = self._read_file_safe(vite_path)
                import re
                m = re.search(r"outDir\s*:\s*['\"]([^'\"]+)['\"]", content)
                if m:
                    return m.group(1)
                # vite.config exists but no outDir — default is "dist"
                return "dist"

        # package.json deps: vite present → dist, react-scripts → build
        pkg_path = self._get_file_path(repo_path, all_files, "package.json")
        if pkg_path:
            pkg_text = self._read_file_safe(pkg_path)
            if '"vite"' in pkg_text:
                return "dist"
            if '"react-scripts"' in pkg_text:
                return "build"

        # stacks.yaml hint
        if frontend:
            frontend_config = self._config.get("frontend", {})
            stack_output_dir = frontend_config.get(frontend, {}).get("output_dir")
            if stack_output_dir:
                return stack_output_dir

        return None

    # ------------------------------------------------------------------
    # Server entry point detection
    # ------------------------------------------------------------------

    def _detect_server_entry(self, repo_path: Path, all_files: list[Path]) -> str | None:
        """Detect the compiled server entry point from package.json scripts.start.

        E.g. "node build/server.js" → "build/server.js"
             "node dist/index.js"   → "dist/index.js"
        Returns None if not determinable.
        """
        import re
        pkg_path = self._get_file_path(repo_path, all_files, "package.json")
        if not pkg_path:
            return None
        pkg_text = self._read_file_safe(pkg_path)
        m = re.search(r'"start"\s*:\s*"([^"]+)"', pkg_text)
        if not m:
            return None
        start_cmd = m.group(1)
        # Match: node <path>   or   NODE_ENV=production node <path>
        node_match = re.search(r"\bnode\s+([\w./\-]+\.js)", start_cmd)
        if node_match:
            return node_match.group(1)
        return None

    # ------------------------------------------------------------------
    # Package manager detection
    # ------------------------------------------------------------------

    def _detect_package_manager(self, all_files: list[Path]) -> str:
        """Detect JS package manager from lockfile presence."""
        for f in all_files:
            if f.name == "pnpm-lock.yaml":
                return "pnpm"
            if f.name == "bun.lockb":
                return "bun"
            if f.name == "yarn.lock":
                return "yarn"
        return "npm"

    # ------------------------------------------------------------------
    # Version extraction
    # ------------------------------------------------------------------

    # Map from our framework name -> npm package name or pip package name
    _PACKAGE_NAME_MAP: dict[str, str] = {
        "nextjs": "next",
        "react": "react",
        "vue": "vue",
        "express": "express",
        "fastapi": "fastapi",
        "django": "django",
        "flask": "flask",
        "streamlit": "streamlit",
    }

    def _extract_version(
        self,
        repo_path: Path,
        all_files: list[Path],
        framework: str,
    ) -> str | None:
        """Try to extract the major version of a framework from dependency files."""
        # Resolve the package name (npm / pip) for this framework
        pkg_name = self._PACKAGE_NAME_MAP.get(framework, framework)

        # Try package.json for JS frameworks
        pkg_path = self._get_file_path(repo_path, all_files, "package.json")
        if pkg_path is not None:
            content = self._read_file_safe(pkg_path)
            # Look for "pkg_name": "version" or "^version"
            pattern = rf'"{re.escape(pkg_name)}"\s*:\s*"[\^~]?(\d+)'
            match = re.search(pattern, content)
            if match:
                return match.group(1)

        # Try requirements.txt for Python frameworks
        req_path = self._get_file_path(repo_path, all_files, "requirements.txt")
        if req_path is not None:
            content = self._read_file_safe(req_path)
            pattern = rf"^{re.escape(pkg_name)}[=><~!]+(\d+)"
            match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(1)

        # Try pyproject.toml for Python frameworks
        pyproject_path = self._get_file_path(repo_path, all_files, "pyproject.toml")
        if pyproject_path is not None:
            content = self._read_file_safe(pyproject_path)
            pattern = rf'"{re.escape(pkg_name)}[>=<~^!]+(\d+)'
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    # ------------------------------------------------------------------
    # System dependency scanning
    # ------------------------------------------------------------------

    def _scan_system_dependencies(self, repo_path: Path) -> list[str]:
        """Scan dependency files and map Python packages to required system (apt) packages."""
        python_pkgs = self._system_deps.get("python_packages", {})
        if not python_pkgs:
            return []

        declared = set()

        # Parse requirements.txt
        req_path = repo_path / "requirements.txt"
        if req_path.exists():
            for line in req_path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                match = re.match(r'^([A-Za-z0-9_.-]+)', line)
                if match:
                    declared.add(match.group(1))

        # Parse pyproject.toml [project.dependencies] using tomllib
        pyproject_path = repo_path / "pyproject.toml"
        if pyproject_path.exists():
            try:
                import tomllib
                with open(pyproject_path, "rb") as f:
                    pyproject = tomllib.load(f)
                for dep in pyproject.get("project", {}).get("dependencies", []):
                    match = re.match(r'^([A-Za-z0-9_.-]+)', dep)
                    if match:
                        declared.add(match.group(1))
            except Exception:
                pass

        # Parse Pipfile [packages]
        pipfile_path = repo_path / "Pipfile"
        if pipfile_path.exists():
            content = pipfile_path.read_text(errors="replace")
            in_packages = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped == "[packages]":
                    in_packages = True
                    continue
                if re.match(r'^\[', stripped):
                    in_packages = False
                    continue
                if in_packages and "=" in stripped:
                    pkg_name = stripped.split("=")[0].strip()
                    if pkg_name:
                        declared.add(pkg_name)

        # Look up system deps (case-insensitive)
        pkg_lookup = {k.lower(): v for k, v in python_pkgs.items()}
        system_pkgs = set()
        for pkg in declared:
            deps = pkg_lookup.get(pkg.lower(), None)
            if deps:
                system_pkgs.update(deps)

        if system_pkgs:
            base = self._system_deps.get("python_base_build_packages", [])
            system_pkgs.update(base)

        return sorted(system_pkgs)

    # ------------------------------------------------------------------
    # Language version detection
    # ------------------------------------------------------------------

    def _detect_python_version(self, repo_path: Path) -> str | None:
        """Detect Python version from project files. Returns major.minor or None."""
        pv = repo_path / ".python-version"
        if pv.exists():
            content = pv.read_text(errors="replace").strip()
            match = re.match(r'(\d+\.\d+)', content)
            if match:
                return match.group(1)

        pyproject = repo_path / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(errors="replace")
            match = re.search(r'requires-python\s*=\s*"[><=!~]*(\d+\.\d+)', content)
            if match:
                return match.group(1)

        rt = repo_path / "runtime.txt"
        if rt.exists():
            content = rt.read_text(errors="replace").strip()
            match = re.match(r'python-(\d+\.\d+)', content)
            if match:
                return match.group(1)

        return None

    def _detect_node_version(self, repo_path: Path) -> str | None:
        """Detect Node.js version from project files. Returns major version or None."""
        pkg = repo_path / "package.json"
        if pkg.exists():
            try:
                import json
                data = json.loads(pkg.read_text(errors="replace"))
                node_ver = data.get("engines", {}).get("node", "")
                if node_ver:
                    match = re.search(r'(\d+)', node_ver)
                    if match:
                        return match.group(1)
            except (json.JSONDecodeError, AttributeError):
                pass

        nvmrc = repo_path / ".nvmrc"
        if nvmrc.exists():
            content = nvmrc.read_text(errors="replace").strip().lstrip("v")
            match = re.match(r'(\d+)', content)
            if match:
                return match.group(1)

        nv = repo_path / ".node-version"
        if nv.exists():
            content = nv.read_text(errors="replace").strip().lstrip("v")
            match = re.match(r'(\d+)', content)
            if match:
                return match.group(1)

        return None

    def _detect_go_version(self, repo_path: Path) -> str | None:
        """Detect Go version from go.mod. Returns major.minor or None."""
        gomod = repo_path / "go.mod"
        if gomod.exists():
            content = gomod.read_text(errors="replace")
            match = re.search(r'^go\s+(\d+\.\d+)', content, re.MULTILINE)
            if match:
                return match.group(1)
        return None

    # ------------------------------------------------------------------
    # Resource key mapping
    # ------------------------------------------------------------------

    def _get_resource_key(self, analysis: RepoAnalysis) -> str:
        """Map a RepoAnalysis to a resource table key like 'frontend_backend_db_redis'."""
        has_frontend = analysis.stack.frontend is not None
        has_backend = analysis.stack.backend is not None
        has_db = analysis.database is not None
        has_redis = "redis" in analysis.services

        if has_frontend and has_backend and has_db and has_redis:
            return "frontend_backend_db_redis"
        if has_frontend and has_backend and has_db:
            return "frontend_backend_db"
        if has_frontend and has_backend:
            return "frontend_backend"
        if has_frontend:
            return "frontend_only"
        if has_backend and has_db:
            return "frontend_backend_db"  # reuse closest key
        if has_backend:
            return "frontend_backend"  # reuse closest key
        return "frontend_only"  # fallback
