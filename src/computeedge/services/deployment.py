import re
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from computeedge.exceptions import DeploymentError
from computeedge.models import DeployDiagnostics, DeployResult, PreDeployCheckResult, RepoAnalysis
from computeedge.services.infra import (
    InfrastructureProvisioner,
    ProvisionedInfrastructure,
)
from computeedge.state.manager import StateManager
from computeedge.utils.logger import get_logger
from computeedge.utils.ssh import SSHClient

logger = get_logger("deployment")

NANOCLAW_UPSTREAM_REPO = "https://github.com/qwibitai/nanoclaw.git"

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

PLAN_COSTS = {
    # Hetzner
    "cx22": 3.99,
    "cx23": 3.49,
    "cx32": 7.59,
    "cx33": 6.49,
    "cax11": 3.99,
    "cpx11": 4.99,
    # DigitalOcean
    "s-1vcpu-1gb": 6.0,
    "s-1vcpu-2gb": 12.0,
    "s-2vcpu-2gb": 18.0,
    "s-2vcpu-4gb": 24.0,
}

DOCKER_INSTALL_SCRIPT = (
    "apt-get update -qq && "
    "apt-get install -y -qq docker.io docker-compose-v2 && "
    "systemctl enable docker && "
    "systemctl start docker"
)

CLOUD_INIT_SCRIPT = """\
#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose-v2
runcmd:
  - systemctl enable docker
  - systemctl start docker
"""

NANOCLAW_CLOUD_INIT = """\
#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose-v2
  - git
  - build-essential
runcmd:
  - systemctl enable docker
  - systemctl start docker
  - curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  - apt-get install -y -qq nodejs
  - npm install -g @anthropic-ai/claude-code
"""


class DeploymentService:
    """Orchestrates deploying an app to a cloud VPS."""

    def __init__(
        self,
        provider=None,
        state: StateManager = None,
        stacks_config: dict = None,
        provisioner: InfrastructureProvisioner | None = None,
        validator=None,
        provider_name: str = "hetzner",
    ):
        self._provider = provider
        self._validator = validator
        self._provider_name = provider_name
        if provisioner:
            self._provisioner = provisioner
        else:
            self._provisioner = None
        self._state = state
        self._stacks_config = stacks_config
        self._ssh = SSHClient()
        self._jinja = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            keep_trailing_newline=True,
        )

    async def _create_infrastructure(
        self,
        deployment_id: str,
        plan: str,
        public_key_content: str,
        user_data: str | None = None,
    ) -> ProvisionedInfrastructure:
        logger.info("Provisioning infrastructure for %s (plan: %s)", deployment_id, plan)
        return await self._provisioner.provision(
            deployment_id, plan, public_key_content, user_data=user_data,
        )

    async def _wait_for_infrastructure(
        self,
        infrastructure: ProvisionedInfrastructure,
    ) -> ProvisionedInfrastructure:
        logger.info("Waiting for server to be ready...")
        infrastructure = await self._provisioner.wait_until_ready(infrastructure)
        if not infrastructure.ip:
            raise DeploymentError("Provisioner returned no public IP for the deployment")
        return infrastructure

    def _build_infrastructure_handle(
        self,
        deployment_id: str,
        plan: str,
        server_id: int,
        ip: str | None = None,
        backend: str = "api",
        metadata: dict[str, str] | None = None,
    ) -> ProvisionedInfrastructure:
        return ProvisionedInfrastructure(
            provider=self._provider_name,
            plan=plan,
            server_id=server_id,
            server_name=deployment_id,
            backend=backend,
            ip=ip,
            status="running" if ip else "",
            metadata=metadata or {},
        )

    def _get_stack_config(self, stack_name: str) -> dict | None:
        """Search all stacks.yaml sections for a stack by name."""
        for section in ("frontend", "backend", "database"):
            section_config = self._stacks_config.get(section, {})
            if stack_name in section_config:
                return section_config[stack_name]
        return None

    def _detect_django_project_name(self, repo_path: Path) -> str:
        """Scan for */wsgi.py and return the parent directory name."""
        for wsgi_file in repo_path.rglob("wsgi.py"):
            name = wsgi_file.parent.name
            if name and name != str(repo_path.name):
                return name
        logger.warning("Could not detect Django project name, using fallback 'app'")
        return "app"

    def _detect_go_entry_point(self, repo_path: Path) -> str:
        """Check stacks.yaml entry_points in order, return Go build target."""
        go_config = self._get_stack_config("go") or {}
        entry_points = go_config.get("entry_points", ["main.go"])
        for ep in entry_points:
            if (repo_path / ep).exists():
                # Transform file path to Go build target:
                # cmd/server/main.go → ./cmd/server
                # main.go → .
                target = ep.replace("/main.go", "").replace("main.go", "")
                if not target:
                    return "."
                return f"./{target}"
        return "."

    def _detect_streamlit_entry_point(self, repo_path: Path) -> str:
        """Find the main Streamlit app file."""
        candidates = ["app.py", "streamlit_app.py", "main.py"]
        for name in candidates:
            if (repo_path / name).exists():
                return name
        # Fallback: scan root .py files for streamlit imports
        for py_file in repo_path.glob("*.py"):
            content = py_file.read_text(errors="replace")
            if "import streamlit" in content or "from streamlit" in content:
                return py_file.name
        return "app.py"

    def _build_template_context(self, stack_name: str, analysis: RepoAnalysis, repo_path: Path | None) -> dict:
        """Build Jinja2 template context from stacks.yaml config fields and detected analysis."""
        stack_config = self._get_stack_config(stack_name) or {}
        # Prefer detected build_output_dir over stacks.yaml; fall back to stacks.yaml; then "dist"
        detected_output = analysis.stack.build_output_dir
        yaml_output = stack_config.get("output_dir")
        context = {
            "run_cmd": stack_config.get("run_cmd", ""),
            "build_cmd": stack_config.get("build_cmd"),
            "install_cmd": stack_config.get("install_cmd"),
            "extra_packages": stack_config.get("extra_packages", []),
            "output_dir": detected_output or yaml_output or "dist",
            "build_output_dir": detected_output or yaml_output or "dist",
            "server_entry": analysis.stack.server_entry or "dist/index.js",
            "has_requirements_txt": (repo_path / "requirements.txt").exists()
                                    if repo_path else True,
        }
        context["port"] = int(stack_config.get("default_port", 8000))

        # Resolve Django {app_name} placeholder
        if "{app_name}" in context.get("run_cmd", ""):
            if repo_path:
                app_name = self._detect_django_project_name(repo_path)
            else:
                app_name = "app"
            context["run_cmd"] = context["run_cmd"].replace("{app_name}", app_name)

        # Resolve Streamlit {entry_point} placeholder
        if "{entry_point}" in context.get("run_cmd", ""):
            if repo_path:
                entry_point = self._detect_streamlit_entry_point(repo_path)
            else:
                entry_point = "app.py"
            context["run_cmd"] = context["run_cmd"].replace("{entry_point}", entry_point)

        if stack_name == "go":
            context["entry_point"] = self._detect_go_entry_point(repo_path) if repo_path else "."

        # Smart deploy: pass version and system package info for templates
        context["python_version"] = analysis.python_version
        context["node_version"] = analysis.node_version
        context["go_version"] = analysis.go_version
        context["system_packages"] = analysis.system_packages

        # Inject migration dependencies (e.g. alembic, DB driver) when detected
        extra_pkgs = list(context.get("extra_packages", []))
        if (analysis.database and analysis.database.has_migrations
                and analysis.migration_command and "alembic" in analysis.migration_command):
            if "alembic" not in extra_pkgs:
                extra_pkgs.append("alembic")
        if analysis.database and analysis.database.type == "postgres":
            if not any(p.startswith("psycopg") for p in extra_pkgs):
                extra_pkgs.append("psycopg2-binary")
        context["extra_packages"] = extra_pkgs

        # Inject Node migration tool packages (often devDeps stripped by --production)
        extra_node_pkgs: list[str] = []
        if analysis.database and analysis.database.has_migrations and analysis.migration_command:
            cmd = analysis.migration_command
            _NODE_MIGRATION_TOOLS = {
                "prisma": "prisma",
                "knex": "knex",
                "sequelize": "sequelize-cli",
                "typeorm": "typeorm",
            }
            for keyword, package in _NODE_MIGRATION_TOOLS.items():
                if keyword in cmd and package not in extra_node_pkgs:
                    extra_node_pkgs.append(package)
        context["extra_node_packages"] = extra_node_pkgs

        return context

    async def pre_deploy_check(self, analysis: RepoAnalysis, env_vars: dict | None,
                               repo_path: str, user_id: int, force_new: bool = False) -> PreDeployCheckResult:
        """Validate deployment prerequisites before creating any infrastructure."""
        result = PreDeployCheckResult()

        # EC-13: Check for duplicate deployments
        if not force_new:
            existing = await self._state.find_by_repo(repo_path, user_id)
            if existing is not None:
                dep_id, dep_data = existing
                result.can_proceed = False
                result.existing_deployment = dep_id
                result.existing_ip = dep_data.get("ip")
                result.suggestion = (
                    f"You already have a deployment for this repo: {dep_id} at {dep_data.get('ip')}. "
                    f"Use redeploy('{dep_id}') to update it, or deploy with force_new=true for a new server."
                )
                return result

        # EC-8: Check for missing required environment variables
        if analysis.env_vars_required:
            provided = set((env_vars or {}).keys())
            missing = [v for v in analysis.env_vars_required if v not in provided]
            if missing:
                result.can_proceed = False
                result.missing_env_vars = missing
                result.suggestion = (
                    f"Re-run deploy with env_vars providing: {', '.join(missing)}"
                )

        return result

    def _validate_before_deploy(self, analysis: RepoAnalysis, generated_files: dict[str, str], repo_path, env_vars: dict | None) -> list:
        """Validate generated Dockerfiles against the repo before creating infrastructure."""
        from computeedge.models import ValidationIssue
        issues = []
        repo_path = Path(repo_path)

        backend_path = repo_path
        if analysis.stack.backend_path and analysis.stack.backend_path != ".":
            backend_path = repo_path / analysis.stack.backend_path

        frontend_path = repo_path
        if analysis.stack.frontend_path and analysis.stack.frontend_path != ".":
            frontend_path = repo_path / analysis.stack.frontend_path

        # 1. Dependency file existence
        if analysis.stack.backend_language == "python":
            if not (backend_path / "requirements.txt").exists() and not (backend_path / "pyproject.toml").exists():
                issues.append(ValidationIssue(
                    severity="error", check="dep_file",
                    message=f"Missing requirements.txt or pyproject.toml in {backend_path}",
                    file="Dockerfile",
                    suggestion="Create a requirements.txt with your Python dependencies",
                ))
        elif analysis.stack.backend_language in ("javascript", "typescript") or analysis.stack.frontend:
            check_path = frontend_path if not analysis.stack.backend else backend_path
            if not (check_path / "package.json").exists():
                issues.append(ValidationIssue(
                    severity="error", check="dep_file",
                    message=f"Missing package.json in {check_path}",
                    file="Dockerfile",
                    suggestion="Run npm init to create a package.json",
                ))
        elif analysis.stack.backend_language == "go":
            if not (backend_path / "go.mod").exists():
                issues.append(ValidationIssue(
                    severity="error", check="dep_file",
                    message=f"Missing go.mod in {backend_path}",
                    file="Dockerfile",
                    suggestion="Run go mod init to create go.mod",
                ))

        # 2. Entry point existence
        dockerfile = generated_files.get("Dockerfile", "")
        node_cmd = re.search(r'CMD\s+\["node",\s*"([^"]+)"', dockerfile)
        if node_cmd:
            entry = node_cmd.group(1)
            entry_path = backend_path / entry
            if not entry_path.exists():
                issues.append(ValidationIssue(
                    severity="error", check="entry_point",
                    message=f"Entry point '{entry}' not found at {entry_path}",
                    file="Dockerfile",
                    suggestion=f"Verify that {entry} exists or will be created by the build step",
                ))
        # Check uvicorn/gunicorn module paths (direct CMD or inside sh -c)
        for pattern in [
            r'CMD\s+\["uvicorn",\s*"([^":]+):',          # CMD ["uvicorn", "mod:app"]
            r'CMD\s+\["sh".*?(?:uvicorn|gunicorn)\s+([^":\s]+):',  # CMD ["sh", "-c", "gunicorn mod:app ..."]
        ]:
            match = re.search(pattern, dockerfile)
            if match:
                module = match.group(1)
                module_file = module.replace(".", "/") + ".py"
                module_path = backend_path / module_file
                if not module_path.exists():
                    issues.append(ValidationIssue(
                        severity="error", check="entry_point",
                        message=f"Module '{module_file}' not found at {module_path}",
                        file="Dockerfile",
                        suggestion=f"Verify that {module_file} exists in your project",
                    ))
                break

        # Check streamlit entry point (inside sh -c)
        streamlit_match = re.search(r'streamlit\s+run\s+(\S+)', dockerfile)
        if streamlit_match:
            entry = streamlit_match.group(1)
            entry_path = backend_path / entry
            if not entry_path.exists():
                issues.append(ValidationIssue(
                    severity="error", check="entry_point",
                    message=f"Streamlit entry '{entry}' not found at {entry_path}",
                    file="Dockerfile",
                    suggestion=f"Verify that {entry} exists in your project",
                ))

        # 3. Port consistency
        compose = generated_files.get("docker-compose.yml", "")
        expose_match = re.search(r'EXPOSE\s+(\d+)', dockerfile)
        port_match = re.search(r'"(\d+):(\d+)"', compose)
        if expose_match and port_match:
            expose_port = expose_match.group(1)
            compose_port = port_match.group(2)
            if expose_port != compose_port:
                issues.append(ValidationIssue(
                    severity="warning", check="port",
                    message=f"Dockerfile EXPOSE {expose_port} doesn't match compose port mapping {compose_port}",
                    file="docker-compose.yml",
                    suggestion=f"Update the port mapping to match EXPOSE {expose_port}",
                ))

        return issues

    def _validate_port_consistency(self, analysis: RepoAnalysis, generated_files: dict[str, str]) -> list:
        """Check port consistency between Dockerfile and docker-compose."""
        from computeedge.models import ValidationIssue
        issues = []
        dockerfile = generated_files.get("Dockerfile", "")
        compose = generated_files.get("docker-compose.yml", "")
        expose_match = re.search(r'EXPOSE\s+(\d+)', dockerfile)
        port_match = re.search(r'"(\d+):(\d+)"', compose)
        if expose_match and port_match:
            expose_port = expose_match.group(1)
            compose_port = port_match.group(2)
            if expose_port != compose_port:
                issues.append(ValidationIssue(
                    severity="warning", check="port",
                    message=f"Dockerfile EXPOSE {expose_port} doesn't match compose port mapping {compose_port}",
                    file="docker-compose.yml",
                    suggestion=f"Update the port mapping to match EXPOSE {expose_port}",
                ))
        return issues

    # Frontends that have an npm/yarn/bun build step producing static assets.
    _JS_FRONTENDS: set[str] = {"react", "vue", "nextjs", "nuxt", "angular", "svelte", "sveltekit", "astro", "vite"}

    def _needs_unified_container(self, analysis: RepoAnalysis) -> bool:
        """Return True when frontend + backend should be combined into one container.

        Uses the unified Dockerfile (embedded nginx) instead of separate containers.
        Only applies when the frontend is a JS framework with a build step —
        non-JS frontends like Streamlit are served by their own process and
        don't need an nginx/static-asset container.
        """
        frontend = analysis.stack.frontend
        backend = analysis.stack.backend
        if not frontend or not backend:
            return False
        # Only JS-based frontends produce static assets that need nginx
        if frontend not in self._JS_FRONTENDS:
            return False
        # Different directories → always unified
        if (analysis.stack.frontend_path or ".") != (analysis.stack.backend_path or "."):
            return True
        # Different languages (e.g. Python backend + JS frontend) → unified
        if analysis.stack.backend_language not in ("javascript", "typescript"):
            return True
        return False

    def _build_unified_context(self, analysis: RepoAnalysis, repo_path: Path | None) -> dict:
        """Build template context for the unified Dockerfile."""
        backend = analysis.stack.backend or "fastapi"
        context = self._build_template_context(backend, analysis, repo_path)
        context["analysis"] = analysis
        context["frontend_path"] = analysis.stack.frontend_path or "."
        context["backend_path"] = analysis.stack.backend_path or "."
        # Override has_requirements_txt to check backend_path, not repo root
        if repo_path:
            backend_abs = repo_path / (analysis.stack.backend_path or ".")
            context["has_requirements_txt"] = (backend_abs / "requirements.txt").exists()
        context["backend_language"] = analysis.stack.backend_language or "python"
        context["backend_port"] = self._detect_server_port(analysis)
        context["build_output_dir"] = analysis.stack.build_output_dir or "dist"

        # Build the run command for the backend
        run_cmd = context.get("run_cmd", "")
        if not run_cmd:
            lang = analysis.stack.backend_language
            port = context["backend_port"]
            if lang == "python":
                if backend == "fastapi":
                    run_cmd = f"uvicorn main:app --host 127.0.0.1 --port {port}"
                else:
                    run_cmd = f"gunicorn app:app --bind 127.0.0.1:{port}"
            elif lang in ("javascript", "typescript"):
                entry = analysis.stack.server_entry or "index.js"
                run_cmd = f"node {entry}"
            elif lang == "go":
                run_cmd = "./server"
        else:
            # Rewrite bind address to 127.0.0.1 (nginx handles external traffic)
            run_cmd = run_cmd.replace("0.0.0.0", "127.0.0.1")

        context["run_cmd"] = run_cmd
        return context
    def _generate_dockerfiles(self, analysis: RepoAnalysis, repo_path: Path | None = None) -> dict[str, str]:
        """Render Dockerfile templates locally and return filename→content without uploading."""
        result = {}
        backend = analysis.stack.backend
        frontend = analysis.stack.frontend

        # Unified container: both frontend + backend in one Dockerfile with embedded nginx
        if self._needs_unified_container(analysis):
            context = self._build_unified_context(analysis, repo_path)
            result["Dockerfile"] = self._jinja.get_template("Dockerfile.unified.j2").render(**context)
            return result

        # True Node monorepo: one JS process serves both frontend and backend
        if backend and frontend:
            context = self._build_template_context(backend or "express", analysis, repo_path)
            context["analysis"] = analysis
            context["port"] = self._detect_server_port(analysis)
            context["app_port"] = context["port"]
            result["Dockerfile"] = self._jinja.get_template("Dockerfile.fullstack-node.j2").render(**context)
            return result

        # Single stack: backend only or frontend only
        if backend:
            template_name = self._backend_dockerfile_template(backend, analysis.stack.backend_language)
            if template_name:
                context = self._build_template_context(backend, analysis, repo_path)
                context["analysis"] = analysis
                result["Dockerfile"] = self._jinja.get_template(template_name).render(**context)

        elif frontend:
            template_name = self._frontend_dockerfile_template(frontend)
            if template_name:
                context = self._build_template_context(frontend, analysis, repo_path)
                context["analysis"] = analysis
                result["Dockerfile"] = self._jinja.get_template(template_name).render(**context)

        return result

    def render_configs(
        self,
        analysis: RepoAnalysis,
        repo_path: str,
        env_vars: dict | None = None,
    ) -> dict[str, str]:
        """Render all deployment configs without deploying.

        Generates secrets (SECRET_KEY, db_password) and includes them
        in the returned .env and docker-compose.yml.

        Returns dict with keys varying by topology:
          Dockerfile (and Dockerfile.frontend or path-prefixed for split),
          docker-compose.yml, nginx.conf, .env,
          plus _config_notes and _templates_used metadata.
        """
        result = {}

        # --- Dockerfiles (same logic as _generate_dockerfiles) ---
        backend = analysis.stack.backend
        frontend = analysis.stack.frontend
        unified = self._needs_unified_container(analysis)

        if unified:
            context = self._build_unified_context(analysis, Path(repo_path))
            result["Dockerfile"] = self._jinja.get_template("Dockerfile.unified.j2").render(**context)
        elif backend and frontend:
            # True Node monorepo
            context = self._build_template_context(backend or "express", analysis, Path(repo_path))
            context["analysis"] = analysis
            context["port"] = self._detect_server_port(analysis)
            context["app_port"] = context["port"]
            result["Dockerfile"] = self._jinja.get_template("Dockerfile.fullstack-node.j2").render(**context)
        elif backend:
            template_name = self._backend_dockerfile_template(backend, analysis.stack.backend_language)
            if template_name:
                context = self._build_template_context(backend, analysis, Path(repo_path))
                context["analysis"] = analysis
                result["Dockerfile"] = self._jinja.get_template(template_name).render(**context)
        elif frontend:
            template_name = self._frontend_dockerfile_template(frontend)
            if template_name:
                context = self._build_template_context(frontend, analysis, Path(repo_path))
                context["analysis"] = analysis
                result["Dockerfile"] = self._jinja.get_template(template_name).render(**context)

        # --- Secrets & env ---
        include_db = analysis.database is not None and analysis.database.type != "sqlite"
        include_redis = "redis" in analysis.services
        db_type = analysis.database.type if analysis.database else None

        generated_env = {}
        generated_env["SECRET_KEY"] = secrets.token_hex(32)
        db_password = secrets.token_hex(16)
        if include_db:
            if db_type == "postgres":
                generated_env["DATABASE_URL"] = f"postgresql://computeedge:{db_password}@db:5432/app"
            elif db_type == "mysql":
                generated_env["DATABASE_URL"] = f"mysql://computeedge:{db_password}@db:3306/app"
            elif db_type == "mongodb":
                generated_env["DATABASE_URL"] = f"mongodb://computeedge:{db_password}@db:27017/app"
        if include_redis:
            generated_env["REDIS_URL"] = "redis://redis:6379/0"

        if env_vars:
            generated_env.update(env_vars)

        result[".env"] = "\n".join(f"{k}={v}" for k, v in generated_env.items())

        # --- docker-compose ---
        services = ["app"]
        app_port = self._detect_server_port(analysis)
        env_vars_for_compose = {k: v for k, v in generated_env.items() if k not in ("DATABASE_URL", "REDIS_URL")}

        result["docker-compose.yml"] = self._jinja.get_template("docker-compose.j2").render(
            analysis=analysis, include_db=include_db, include_redis=include_redis,
            db_type=db_type, database_url=generated_env.get("DATABASE_URL", ""),
            redis_url=generated_env.get("REDIS_URL", ""),
            db_user="computeedge", db_password=db_password, db_name="app",
            env_vars=env_vars_for_compose,
            app_port=app_port,
            embedded_nginx=unified,
        )

        # --- nginx (only needed when nginx is NOT embedded in the app container) ---
        if unified:
            # Unified container has nginx inside — generate a minimal placeholder
            result["nginx.conf"] = "# nginx is embedded in the app container\n"
        else:
            result["nginx.conf"] = self._jinja.get_template("nginx.conf.j2").render(
                services=services, use_ssl=False, domain=None,
                app_port=app_port,
            )

        # --- config_notes ---
        notes = []
        if analysis.stack.backend:
            desc = f"Backend: {analysis.stack.backend}"
            if analysis.database:
                desc += f" with {analysis.database.type}"
                if analysis.database.orm:
                    desc += f" via {analysis.database.orm}"
            notes.append(desc)
        if analysis.stack.frontend:
            desc = f"Frontend: {analysis.stack.frontend}"
            if analysis.stack.frontend_version:
                desc += f" {analysis.stack.frontend_version}"
            if analysis.stack.package_manager:
                desc += f" with {analysis.stack.package_manager}"
            notes.append(desc)
        if analysis.database and analysis.database.type != "sqlite":
            notes.append(f"Database container included ({analysis.database.type})")
        if "redis" in analysis.services:
            notes.append("Redis container included")
        if analysis.migration_command:
            notes.append(f"Migrations will run: {analysis.migration_command}")

        # Track which templates were used
        templates_used = []
        if unified:
            templates_used.append("Dockerfile.unified.j2")
        elif backend and frontend:
            templates_used.append("Dockerfile.fullstack-node.j2")
        else:
            if backend:
                tpl = self._backend_dockerfile_template(backend, analysis.stack.backend_language)
                if tpl:
                    templates_used.append(tpl)
            if frontend:
                tpl = self._frontend_dockerfile_template(frontend)
                if tpl:
                    templates_used.append(tpl)
        for t in templates_used:
            notes.append(f"Template used: {t}")

        result["_config_notes"] = notes
        result["_templates_used"] = templates_used

        return result

    def _is_nanoclaw(self, analysis: RepoAnalysis) -> bool:
        """Return True when the detected stack is nanoclaw (needs native VM deploy)."""
        stack_config = self._get_stack_config(analysis.stack.backend or "")
        if stack_config and stack_config.get("deploy_mode") == "native":
            return True
        return analysis.stack.backend == "nanoclaw"

    def _nanoclaw_default_plan(self) -> str:
        """Return the recommended default plan for nanoclaw based on provider."""
        stack_config = self._get_stack_config("nanoclaw") or {}
        defaults = stack_config.get("default_plan", {})
        return defaults.get(self._provider_name, "cx32")

    @staticmethod
    def _is_nanoclaw_workspace(repo_path: str) -> bool:
        """Detect a nanoclaw workspace directory (not a full project).

        A workspace is created when the user installs nanoclaw globally via npm
        and runs it from a directory. It has groups/ and/or .claude/ but no
        package.json with nanoclaw as a dependency.
        """
        from computeedge.utils.git import is_git_url
        if is_git_url(repo_path):
            return False
        p = Path(repo_path)
        if not p.is_dir():
            return False
        # If package.json exists and contains nanoclaw, it's a full project
        pkg = p / "package.json"
        if pkg.is_file():
            try:
                content = pkg.read_text()
                if '"nanoclaw"' in content:
                    return False
            except Exception:
                pass
        # Workspace indicators: has groups/ or .claude/ but no nanoclaw source
        has_groups = (p / "groups").is_dir()
        has_src = (p / "src" / "index.ts").is_file()
        # Only groups/ is nanoclaw-specific. .claude/ is used by Claude Code
        # for any project, so it must not trigger nanoclaw detection alone.
        return has_groups and not has_src

    # Workspace dirs to overlay onto the upstream clone
    _NANOCLAW_WORKSPACE_DIRS = ["groups", ".claude", "container/skills"]

    async def _upload_nanoclaw_workspace(self, conn, workspace_path: str, work_dir: str):
        """Upload workspace files (groups, .claude, skills) onto an upstream clone.

        Uses Python's tarfile module instead of shelling out to tar,
        so this works on Windows without requiring tar in PATH.
        """
        import tarfile
        import tempfile

        local = Path(workspace_path)
        skip_names = {".env", "store"}

        for rel_dir in self._NANOCLAW_WORKSPACE_DIRS:
            src = local / rel_dir
            if not src.is_dir():
                continue
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tar_path = tmp.name

            def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
                if info.name.split("/")[0] in skip_names:
                    return None
                return info

            with tarfile.open(tar_path, "w:gz") as tf:
                for item in src.iterdir():
                    if item.name in skip_names:
                        continue
                    tf.add(str(item), arcname=item.name, filter=_filter)

            dest_dir = f"{work_dir}/{rel_dir}"
            await self._ssh.run(conn, f"mkdir -p {dest_dir}")
            await self._ssh.upload(conn, tar_path, f"{dest_dir}/_workspace.tar.gz")
            await self._ssh.run(
                conn,
                f"tar -xzf {dest_dir}/_workspace.tar.gz -C {dest_dir} && rm {dest_dir}/_workspace.tar.gz",
            )
            Path(tar_path).unlink(missing_ok=True)

    async def _deploy_nanoclaw(
        self,
        repo_path: str,
        analysis: RepoAnalysis,
        plan: str,
        env_vars: dict | None,
        deployment_id: str,
        user_id: int = 0,
    ) -> DeployResult:
        """Deploy nanoclaw directly on a VM (no docker-compose wrapper).

        Steps:
        1. Provision VM with Node.js 22 + Docker via cloud-init
        2. Clone/upload the repo
        3. npm install + npm run build
        4. Build agent container (./container/build.sh)
        5. Write .env with ANTHROPIC_API_KEY + user env_vars
        6. Create and start systemd service
        """
        import asyncio

        private_key, public_key_path, public_key_content = self._resolve_ssh_key(deployment_id)

        infrastructure = await self._create_infrastructure(
            deployment_id, plan, public_key_content,
            user_data=NANOCLAW_CLOUD_INIT,
        )
        server_id = infrastructure.server_id
        infrastructure = await self._wait_for_infrastructure(infrastructure)
        ip = infrastructure.ip

        conn = await self._ssh_connect_with_retry(ip, private_key)

        app_dir = f"/opt/nanoclaw"

        try:
            # Wait for cloud-init (installs Node.js + Docker)
            logger.info("Waiting for cloud-init (Node.js + Docker install)")
            await self._ssh.run(conn, "cloud-init status --wait 2>/dev/null || true")
            await self._ssh.run(conn, "node --version")
            await self._ssh.run(conn, "docker version >/dev/null 2>&1")

            # Clone/upload repo
            await self._ssh.run(conn, f"mkdir -p {app_dir}")
            work_dir = f"{app_dir}/repo"
            from computeedge.utils.git import is_git_url

            is_workspace_only = self._is_nanoclaw_workspace(repo_path)

            if is_workspace_only:
                # Workspace-only: user installed nanoclaw via npm globally.
                # Their directory has groups/, .claude/, maybe container/skills/
                # but no package.json with nanoclaw source.
                # Clone upstream first, then overlay their workspace files.
                logger.info("Detected nanoclaw workspace (no source). Cloning upstream + overlaying workspace.")
                await self._ssh.run(
                    conn,
                    f"git clone --depth 1 {NANOCLAW_UPSTREAM_REPO} {work_dir}",
                )
                # Overlay workspace files (groups, .claude, container/skills)
                await self._upload_nanoclaw_workspace(conn, repo_path, work_dir)
            elif is_git_url(repo_path):
                logger.info("Cloning nanoclaw repo on server")
                await self._ssh.run(conn, f"git clone --depth 1 {repo_path} {work_dir}")
            else:
                logger.info("Uploading nanoclaw project")
                await self._upload_repo(
                    conn, repo_path, app_dir,
                    extra_excludes=self._NANOCLAW_EXTRA_EXCLUDES,
                )

            # Install dependencies and build
            logger.info("Installing dependencies and building nanoclaw")
            pkg_manager = analysis.stack.package_manager or "npm" if not is_workspace_only else "npm"
            if pkg_manager == "pnpm":
                await self._ssh.run(conn, f"cd {work_dir} && corepack enable && pnpm install --frozen-lockfile")
            elif pkg_manager == "yarn":
                await self._ssh.run(conn, f"cd {work_dir} && yarn install --frozen-lockfile")
            elif pkg_manager == "bun":
                await self._ssh.run(conn, f"cd {work_dir} && bun install --frozen-lockfile")
            else:
                await self._ssh.run(conn, f"cd {work_dir} && npm ci")

            await self._ssh.run(conn, f"cd {work_dir} && npm run build")

            # Build agent container if container/build.sh exists
            build_script = f"{work_dir}/container/build.sh"
            has_build_script = await self._ssh.run(
                conn, f"test -f {build_script} && echo yes || echo no"
            )
            if has_build_script.strip() == "yes":
                logger.info("Building nanoclaw agent container")
                await self._ssh.run(
                    conn,
                    f"cd {work_dir} && chmod +x container/build.sh && ./container/build.sh",
                )

            # Write .env file
            env_content_lines = []
            if env_vars:
                for key, value in env_vars.items():
                    env_content_lines.append(f"{key}={value}")
            env_content = "\n".join(env_content_lines)
            if env_content:
                await self._ssh.upload_string(conn, env_content, f"{work_dir}/.env")

            # Render and upload systemd service
            service_content = self._jinja.get_template("nanoclaw.service.j2").render(
                app_dir=work_dir,
                env_vars=env_vars or {},
            )
            await self._ssh.upload_string(
                conn, service_content, "/etc/systemd/system/nanoclaw.service"
            )

            # Enable and start the service
            logger.info("Starting nanoclaw systemd service")
            await self._ssh.run(conn, "systemctl daemon-reload")
            await self._ssh.run(conn, "systemctl enable nanoclaw")
            await self._ssh.run(conn, "systemctl start nanoclaw")

            # Verify it started
            await asyncio.sleep(3)
            status_output = await self._ssh.run(
                conn, "systemctl is-active nanoclaw || true"
            )
            is_running = status_output.strip() == "active"

            monthly_cost = PLAN_COSTS.get(plan, 0.0)

            await self._state.add(deployment_id, user_id, {
                "provider": infrastructure.provider,
                "infra_backend": infrastructure.backend,
                "infra_metadata": dict(infrastructure.metadata),
                "server_id": infrastructure.server_id,
                "ip": ip,
                "plan": infrastructure.plan,
                "repo_path": repo_path,
                "normalized_repo_path": self._state.normalize_repo_path(repo_path),
                "stack": asdict(analysis.stack),
                "deployed_at": datetime.now(timezone.utc).isoformat(),
                "monthly_cost": monthly_cost,
                "ssh_key_path": private_key,
                "deploy_mode": "native",
            })

            status = "deployed" if is_running else "deployed_with_warnings"

            # Detect which channels were configured via env_vars
            configured_channels = []
            ev = env_vars or {}
            if ev.get("TELEGRAM_BOT_TOKEN"):
                configured_channels.append("Telegram")
            if ev.get("WHATSAPP_PHONE_ID") or ev.get("WHATSAPP_TOKEN"):
                configured_channels.append("WhatsApp")
            if ev.get("DISCORD_TOKEN"):
                configured_channels.append("Discord")
            if ev.get("SLACK_BOT_TOKEN"):
                configured_channels.append("Slack")

            has_api_key = bool(ev.get("ANTHROPIC_API_KEY"))

            next_steps = [
                f"Nanoclaw is running at {work_dir} on {ip}.",
            ]

            if not has_api_key:
                next_steps.append(
                    f"No ANTHROPIC_API_KEY provided. Authenticate Claude Code on the server: "
                    f"ssh root@{ip} && claude login"
                )

            if configured_channels:
                next_steps.append(
                    f"Channels auto-configured: {', '.join(configured_channels)}."
                )
            else:
                next_steps.append(
                    "No channel credentials provided. Add channels by redeploying with env_vars "
                    "(e.g. TELEGRAM_BOT_TOKEN) or SSH in and run: cd /opt/nanoclaw/repo && claude → /setup"
                )

            next_steps.extend([
                f"View logs: ssh root@{ip} journalctl -u nanoclaw -f",
                f"SSH access: ssh root@{ip}",
                f"Redeploy: redeploy(deployment_id='{deployment_id}', provider_token='...')",
            ])
            if not is_running:
                next_steps.insert(0, "WARNING: nanoclaw service may not have started cleanly. Check logs with: journalctl -u nanoclaw -e")

            return DeployResult(
                status=status,
                provider=self._provider_name,
                url=f"ssh root@{ip}",
                deployment_id=deployment_id,
                monthly_cost=monthly_cost,
                ssh_access=f"ssh root@{ip}",
                next_steps=next_steps,
            )

        except Exception as e:
            # Cleanup on failure
            try:
                await self._provisioner.cleanup(infrastructure)
            except Exception:
                logger.error("Failed to cleanup server %s after nanoclaw deploy failure", server_id)

            raise DeploymentError(
                f"Nanoclaw deployment failed: {e}",
                suggestion=f"Check cloud-init logs on the server. SSH: ssh root@{ip}",
            ) from e

    async def _redeploy_nanoclaw(
        self,
        deployment_id: str,
        repo_path: str,
        analysis: RepoAnalysis,
        env_vars: dict | None,
        state: dict,
        user_id: int = 0,
    ) -> DeployResult:
        """Update nanoclaw on an existing VM: pull code, rebuild, restart service."""
        ip = state["ip"]
        private_key = state.get("ssh_key_path")
        if not private_key:
            raise DeploymentError(f"No SSH key found for deployment {deployment_id}")

        conn = await self._ssh_connect_with_retry(ip, private_key)
        work_dir = "/opt/nanoclaw/repo"

        # Stop service before updating
        await self._ssh.run(conn, "systemctl stop nanoclaw || true")

        # Re-upload repo
        logger.info("Updating nanoclaw code")
        await self._ssh.run(conn, f"rm -rf {work_dir}")
        await self._ssh.run(conn, "mkdir -p /opt/nanoclaw")
        from computeedge.utils.git import is_git_url
        if is_git_url(repo_path):
            await self._ssh.run(conn, f"git clone --depth 1 {repo_path} {work_dir}")
        else:
            await self._upload_repo(
                conn, repo_path, "/opt/nanoclaw",
                extra_excludes=self._NANOCLAW_EXTRA_EXCLUDES,
            )

        # Install and build
        pkg_manager = analysis.stack.package_manager or "npm"
        if pkg_manager == "pnpm":
            await self._ssh.run(conn, f"cd {work_dir} && corepack enable && pnpm install --frozen-lockfile")
        elif pkg_manager == "yarn":
            await self._ssh.run(conn, f"cd {work_dir} && yarn install --frozen-lockfile")
        elif pkg_manager == "bun":
            await self._ssh.run(conn, f"cd {work_dir} && bun install --frozen-lockfile")
        else:
            await self._ssh.run(conn, f"cd {work_dir} && npm ci")

        await self._ssh.run(conn, f"cd {work_dir} && npm run build")

        # Rebuild agent container if build.sh exists
        build_script = f"{work_dir}/container/build.sh"
        has_build_script = await self._ssh.run(
            conn, f"test -f {build_script} && echo yes || echo no"
        )
        if has_build_script.strip() == "yes":
            logger.info("Rebuilding nanoclaw agent container")
            await self._ssh.run(
                conn,
                f"cd {work_dir} && chmod +x container/build.sh && ./container/build.sh",
                timeout=300,
            )

        # Update .env if new env_vars provided
        if env_vars:
            # Merge with existing .env
            existing_env = {}
            try:
                raw = await self._ssh.run(conn, f"cat {work_dir}/.env 2>/dev/null || true")
                for line in raw.strip().splitlines():
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        existing_env[k.strip()] = v.strip()
            except Exception:
                pass
            existing_env.update(env_vars)
            env_content = "\n".join(f"{k}={v}" for k, v in existing_env.items())
            await self._ssh.upload_string(conn, env_content, f"{work_dir}/.env")

        # Restart service
        logger.info("Restarting nanoclaw service")
        await self._ssh.run(conn, "systemctl daemon-reload")
        await self._ssh.run(conn, "systemctl start nanoclaw")

        monthly_cost = state.get("monthly_cost", 0.0)

        await self._state.update(deployment_id, user_id, {
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "repo_path": repo_path,
            "normalized_repo_path": self._state.normalize_repo_path(repo_path),
        })

        return DeployResult(
            status="deployed",
            provider=self._provider_name,
            url=f"ssh root@{ip}",
            deployment_id=deployment_id,
            monthly_cost=monthly_cost,
            ssh_access=f"ssh root@{ip}",
            next_steps=[
                "Code updated, dependencies rebuilt, service restarted.",
                f"View logs: ssh root@{ip} journalctl -u nanoclaw -f",
            ],
        )

    async def deploy(self, repo_path: str, analysis: RepoAnalysis,
                     plan: str = "cx23", env_vars: dict | None = None,
                     docker_configs: dict | None = None,
                     override_dockerfile: bool = False,
                     retry_context: "RetryContext | None" = None,
                     user_id: int = 0) -> DeployResult:
        from computeedge.models import RetryContext, MAX_FULL_LOG_SIZE

        # Retry path: reuse existing server
        if retry_context is not None:
            return await self._deploy_retry(
                repo_path, analysis, plan, env_vars, docker_configs, retry_context,
                user_id=user_id,
            )

        if self._provisioner is None:
            raise DeploymentError(
                "Cannot deploy without a configured provider. "
                "Pass provider_token to the deploy tool."
            )

        # Nanoclaw: direct VM deployment (no docker-compose)
        if self._is_nanoclaw(analysis):
            deployment_id = f"ce-{self._provider_name}-{secrets.token_hex(4)}"
            if plan == "cx23":
                plan = self._nanoclaw_default_plan()
            return await self._deploy_nanoclaw(
                repo_path, analysis, plan, env_vars, deployment_id, user_id=user_id,
            )

        # First attempt: normal flow
        deployment_id = f"ce-{self._provider_name}-{secrets.token_hex(4)}"
        infrastructure = None
        server_id = None
        conn = None
        ip = None
        private_key = None

        try:
            # Validate before provisioning — no server billed if this fails
            configs_to_validate = docker_configs if docker_configs is not None else self._generate_dockerfiles(analysis, Path(repo_path))
            if self._validator:
                validation = self._validator.validate(configs_to_validate, analysis, repo_path, auto_fix=False)
                if not validation.valid:
                    error_msgs = "; ".join(i.message for i in validation.issues if i.severity == "error")
                    suggestion = next((i.suggestion for i in validation.issues if i.suggestion), None)
                    raise DeploymentError(f"Pre-deploy validation failed: {error_msgs}", suggestion=suggestion)
            # Port consistency check
            port_issues = self._validate_port_consistency(analysis, configs_to_validate)
            if port_issues:
                import logging
                _logger = logging.getLogger("computeedge.deployment")
                warnings_msg = "; ".join(i.message for i in port_issues)
                _logger.warning(f"Port consistency warnings: {warnings_msg}")

            private_key, public_key_path, public_key_content = self._resolve_ssh_key(deployment_id)

            infrastructure = await self._create_infrastructure(
                deployment_id, plan, public_key_content,
                user_data=CLOUD_INIT_SCRIPT,
            )
            server_id = infrastructure.server_id
            infrastructure = await self._wait_for_infrastructure(infrastructure)
            ip = infrastructure.ip

            conn = await self._ssh_connect_with_retry(ip, private_key)

            app_dir = f"/root/{deployment_id}"
            await self._wait_for_cloud_init_and_upload_repo(conn, repo_path, app_dir)

            await self._upload_docker_files(conn, analysis, app_dir, env_vars, repo_path=repo_path)
            if docker_configs is not None:
                await self._apply_docker_config_patches(conn, docker_configs, app_dir)

            logger.info("Running docker compose up")
            compose_dir = app_dir
            await self._ssh.run(conn, f"cd {app_dir} && DOCKER_BUILDKIT=1 docker compose up -d --build")

            # Run database migrations if detected — we always own the compose
            # stack so we know the service name is "backend"
            if analysis.migration_command and analysis.database is not None:
                import asyncio as _asyncio
                logger.info("Waiting for database to be ready...")
                await _asyncio.sleep(10)

                service_name = "app"
                migration_dir = compose_dir
                logger.info("Running migrations: %s", analysis.migration_command)
                try:
                    await self._ssh.run(
                        conn,
                        f"cd {migration_dir} && docker compose exec -T {service_name} {analysis.migration_command}"
                    )
                except Exception as migration_error:
                    raise DeploymentError(
                        f"Migration failed: {migration_error}. "
                        "The database may be in a partial state. "
                        "Check the logs and re-run migrations manually after fixing the issue.",
                        suggestion=f"SSH in and run: docker compose exec {service_name} {analysis.migration_command}",
                    ) from migration_error

            monthly_cost = PLAN_COSTS.get(plan, 0.0)
            exposed_port = await self._detect_exposed_port(conn)
            url = f"http://{ip}" if exposed_port == 80 else f"http://{ip}:{exposed_port}"

            await self._state.add(deployment_id, user_id, {
                "provider": infrastructure.provider,
                "infra_backend": infrastructure.backend,
                "infra_metadata": dict(infrastructure.metadata),
                "server_id": infrastructure.server_id,
                "ip": ip,
                "plan": infrastructure.plan,
                "repo_path": repo_path,
                "normalized_repo_path": self._state.normalize_repo_path(repo_path),
                "stack": asdict(analysis.stack),
                "deployed_at": datetime.now(timezone.utc).isoformat(),
                "monthly_cost": monthly_cost,
                "ssh_key_path": private_key,
            })

            return DeployResult(
                status="deployed",
                provider=self._provider_name,
                url=url,
                deployment_id=deployment_id,
                monthly_cost=monthly_cost,
                ssh_access=f"ssh root@{ip}",
                next_steps=[
                    f"Point your domain's A record to {ip}",
                    "SSL will auto-configure once DNS propagates",
                    f"Run 'computeedge monitor {deployment_id}' to check health",
                    f"SSH access: ssh root@{ip}",
                ],
            )

        except Exception as e:
            container_logs = ""
            generated_files_map = {}

            if conn is not None:
                try:
                    app_dir_for_logs = f"/root/{deployment_id}"
                    raw_logs = await self._ssh.run(
                        conn, f"cd {app_dir_for_logs} && docker compose logs 2>&1 || true"
                    )
                    if len(raw_logs) > MAX_FULL_LOG_SIZE:
                        container_logs = raw_logs[:5000] + "\n...[truncated]...\n" + raw_logs[-(MAX_FULL_LOG_SIZE - 5000):]
                    else:
                        container_logs = raw_logs
                except Exception:
                    pass

            # If no container logs (e.g. build failed before any container started),
            # use the build error output from the exception itself
            if not container_logs.strip():
                error_output = str(e)
                if len(error_output) > MAX_FULL_LOG_SIZE:
                    container_logs = error_output[:5000] + "\n...[truncated]...\n" + error_output[-(MAX_FULL_LOG_SIZE - 5000):]
                else:
                    container_logs = error_output

            # Build enriched diagnostics
            base_diagnostics = self._build_deploy_diagnostics(
                phase=self._infer_failure_phase(str(e)),
                error_msg=str(e),
                container_logs=container_logs,
                analysis=analysis,
                generated_files=generated_files_map,
            )

            # Keep server alive and build retry context if server was created
            if server_id is not None:
                # Read dependency files if SSH connection is available
                dep_files = {}
                if conn is not None:
                    try:
                        dep_files = await self._read_dependency_files(
                            conn, analysis, f"/root/{deployment_id}/repo"
                        )
                    except Exception:
                        pass

                # Build suggested configs
                suggested_configs = self._build_suggested_docker_configs(
                    base_diagnostics.generated_files, base_diagnostics.suggested_fixes
                )

                # Build retry context — keep server alive for retry
                new_retry_context = RetryContext(
                    attempt=1,
                    max_retries=3,
                    server_id=server_id,
                    server_ip=ip,
                    deployment_id=deployment_id,
                    ssh_key_path=private_key,
                    infra_backend=infrastructure.backend,
                    infra_metadata=dict(infrastructure.metadata),
                    previous_errors=[str(e)[:200]],
                    previous_fixes=[],
                )

                base_diagnostics.full_logs = container_logs
                base_diagnostics.dependency_files = dep_files
                base_diagnostics.suggested_docker_configs = suggested_configs
                base_diagnostics.retry_context = new_retry_context
                base_diagnostics.agent_instruction = self._build_agent_instruction(
                    phase=base_diagnostics.phase,
                    error_summary=str(e)[:200],
                    suggested_fixes=base_diagnostics.suggested_fixes,
                    has_suggested_configs=suggested_configs is not None,
                    retry_context=None,  # first attempt, no prior context
                )

            error_msg = str(e)
            if container_logs:
                error_msg += f"\n\n--- Container Logs ---\n{container_logs[:2000]}"

            raise DeploymentError(
                f"Deployment failed: {error_msg}",
                diagnostics=base_diagnostics,
            ) from e

    async def _deploy_retry(self, repo_path: str, analysis: RepoAnalysis,
                            plan: str, env_vars: dict | None,
                            docker_configs: dict | None,
                            retry_context: "RetryContext",
                            user_id: int = 0) -> DeployResult:
        """Handle a retry attempt using an existing server."""
        from computeedge.models import RetryContext, MAX_FULL_LOG_SIZE

        deployment_id = retry_context.deployment_id
        infrastructure = None
        server_id = retry_context.server_id
        app_dir = f"/root/{deployment_id}"

        # Connect to existing server
        conn = await self._connect_to_existing_server(retry_context)
        if conn is None:
            # Server gone — fall back to fresh deploy
            logger.warning("Existing server gone, falling back to fresh deploy")
            private_key, _, public_key_content = self._resolve_ssh_key(deployment_id)
            infrastructure = await self._create_infrastructure(
                deployment_id, plan, public_key_content,
                user_data=CLOUD_INIT_SCRIPT,
            )
            server_id = infrastructure.server_id
            infrastructure = await self._wait_for_infrastructure(infrastructure)
            ip = infrastructure.ip
            conn = await self._ssh_connect_with_retry(ip, private_key)
            await self._wait_for_cloud_init(conn)
            await self._ssh.run(conn, f"mkdir -p {app_dir}")
            await self._upload_repo(conn, repo_path, app_dir)
            retry_context.server_id = server_id
            retry_context.server_ip = ip
            retry_context.ssh_key_path = private_key
            retry_context.infra_backend = infrastructure.backend
            retry_context.infra_metadata = dict(infrastructure.metadata)

        try:
            # Re-generate templates, then apply patches on top
            await self._upload_docker_files(conn, analysis, app_dir, env_vars, repo_path=repo_path)
            if docker_configs:
                await self._apply_docker_config_patches(conn, docker_configs, app_dir)

            # Rebuild
            await self._ssh.run(conn, f"cd {app_dir} && docker compose down 2>/dev/null || true")
            await self._ssh.run(conn, f"cd {app_dir} && DOCKER_BUILDKIT=1 docker compose up -d --build")

            # Migrations
            if analysis.migration_command and analysis.database is not None:
                import asyncio as _asyncio
                await _asyncio.sleep(10)
                try:
                    await self._ssh.run(
                        conn,
                        f"cd {app_dir} && docker compose exec -T backend {analysis.migration_command}"
                    )
                except Exception as migration_error:
                    raise DeploymentError(
                        f"Migration failed on retry: {migration_error}",
                        suggestion=f"SSH in: docker compose exec backend {analysis.migration_command}",
                    ) from migration_error

            monthly_cost = PLAN_COSTS.get(plan, 0.0)
            ip = retry_context.server_ip
            exposed_port = await self._detect_exposed_port(conn)
            url = f"http://{ip}" if exposed_port == 80 else f"http://{ip}:{exposed_port}"

            await self._state.add(deployment_id, user_id, {
                "provider": infrastructure.provider if infrastructure else self._provider_name,
                "infra_backend": infrastructure.backend if infrastructure else retry_context.infra_backend,
                "infra_metadata": dict(infrastructure.metadata) if infrastructure else dict(retry_context.infra_metadata),
                "server_id": server_id,
                "ip": ip,
                "plan": plan,
                "repo_path": repo_path,
                "normalized_repo_path": self._state.normalize_repo_path(repo_path),
                "stack": asdict(analysis.stack),
                "deployed_at": datetime.now(timezone.utc).isoformat(),
                "monthly_cost": monthly_cost,
                "ssh_key_path": retry_context.ssh_key_path,
            })

            return DeployResult(
                status="deployed",
                provider=self._provider_name,
                url=url,
                deployment_id=deployment_id,
                monthly_cost=monthly_cost,
                ssh_access=f"ssh root@{ip}",
                next_steps=[
                    f"Deployed successfully on retry attempt {retry_context.attempt}.",
                    f"Point your domain's A record to {ip}",
                ],
            )

        except Exception as e:
            container_logs = ""
            try:
                raw_logs = await self._ssh.run(
                    conn, f"cd {app_dir} && docker compose logs 2>&1 || true"
                )
                if len(raw_logs) > MAX_FULL_LOG_SIZE:
                    container_logs = raw_logs[:5000] + "\n...[truncated]...\n" + raw_logs[-(MAX_FULL_LOG_SIZE - 5000):]
                else:
                    container_logs = raw_logs
            except Exception:
                pass

            # If no container logs (e.g. build failed before any container started),
            # use the build error output from the exception itself
            if not container_logs.strip():
                error_output = str(e)
                if len(error_output) > MAX_FULL_LOG_SIZE:
                    container_logs = error_output[:5000] + "\n...[truncated]...\n" + error_output[-(MAX_FULL_LOG_SIZE - 5000):]
                else:
                    container_logs = error_output

            is_final = retry_context.attempt >= retry_context.max_retries

            if is_final:
                # Final attempt — cleanup
                try:
                    infra_to_cleanup = infrastructure or self._build_infrastructure_handle(
                        deployment_id,
                        plan,
                        server_id,
                        retry_context.server_ip,
                        backend=retry_context.infra_backend,
                        metadata=dict(retry_context.infra_metadata),
                    )
                    await self._provisioner.cleanup(infra_to_cleanup)
                except Exception:
                    logger.error("Failed to cleanup server %s", server_id)

            base_diagnostics = self._build_deploy_diagnostics(
                phase=self._infer_failure_phase(str(e)),
                error_msg=str(e),
                container_logs=container_logs,
                analysis=analysis,
            )

            base_diagnostics.full_logs = container_logs

            if is_final:
                # No more retries
                retry_context.previous_errors.append(str(e)[:200])
                base_diagnostics.retry_context = None
                base_diagnostics.agent_instruction = self._build_agent_instruction(
                    phase=base_diagnostics.phase,
                    error_summary=str(e)[:200],
                    suggested_fixes=base_diagnostics.suggested_fixes,
                    has_suggested_configs=False,
                    retry_context=retry_context,
                )
            else:
                # More retries available
                try:
                    dep_files = await self._read_dependency_files(
                        conn, analysis, f"{app_dir}/repo"
                    )
                except Exception:
                    dep_files = {}

                suggested_configs = self._build_suggested_docker_configs(
                    base_diagnostics.generated_files, base_diagnostics.suggested_fixes
                )

                # Build updated retry context
                new_ctx = RetryContext(
                    attempt=retry_context.attempt + 1,
                    max_retries=retry_context.max_retries,
                    server_id=server_id,
                    server_ip=retry_context.server_ip,
                    deployment_id=deployment_id,
                    ssh_key_path=retry_context.ssh_key_path,
                    infra_backend=retry_context.infra_backend,
                    infra_metadata=dict(retry_context.infra_metadata),
                    previous_errors=retry_context.previous_errors + [str(e)[:200]],
                    previous_fixes=retry_context.previous_fixes + (
                        [{"attempt": retry_context.attempt,
                          "fix_description": "applied docker_configs",
                          "docker_configs_used": docker_configs or {},
                          "result": str(e)[:200]}]
                        if docker_configs else []
                    ),
                )

                base_diagnostics.dependency_files = dep_files
                base_diagnostics.suggested_docker_configs = suggested_configs
                base_diagnostics.retry_context = new_ctx
                base_diagnostics.agent_instruction = self._build_agent_instruction(
                    phase=base_diagnostics.phase,
                    error_summary=str(e)[:200],
                    suggested_fixes=base_diagnostics.suggested_fixes,
                    has_suggested_configs=suggested_configs is not None,
                    retry_context=retry_context,
                )

            error_msg = str(e)
            if container_logs:
                error_msg += f"\n\n--- Container Logs ---\n{container_logs[:2000]}"

            raise DeploymentError(
                f"Deployment failed: {error_msg}",
                diagnostics=base_diagnostics,
            ) from e

    def _resolve_ssh_key(self, deployment_id: str) -> tuple[str, str, str]:
        ssh_dir = Path.home() / ".ssh"
        for key_name in ["id_ed25519", "id_rsa"]:
            private = ssh_dir / key_name
            public = ssh_dir / f"{key_name}.pub"
            if private.exists() and public.exists():
                logger.info("Using existing SSH key: %s", private)
                return str(private), str(public), public.read_text().strip()

        import asyncssh
        keys_dir = Path.home() / ".computeedge" / "keys"
        keys_dir.mkdir(parents=True, exist_ok=True)
        private_path = keys_dir / deployment_id
        public_path = keys_dir / f"{deployment_id}.pub"
        key = asyncssh.generate_private_key("ssh-ed25519")
        private_path.write_bytes(key.export_private_key())
        private_path.chmod(0o600)
        public_path.write_text(key.export_public_key().decode())
        logger.info("Generated new SSH key: %s", private_path)
        return str(private_path), str(public_path), public_path.read_text().strip()

    async def _ssh_connect_with_retry(self, ip: str, private_key: str):
        import asyncio
        max_attempts = 20
        delay = 5
        logger.info("Waiting for SSH to become available on %s", ip)
        await asyncio.sleep(delay)
        for attempt in range(max_attempts):
            try:
                return await self._ssh.connect(ip, private_key)
            except Exception as e:
                if attempt == max_attempts - 1:
                    raise
                logger.warning("SSH attempt %d/%d failed: %s", attempt + 1, max_attempts, e)
                await asyncio.sleep(delay)

    async def _wait_for_cloud_init(self, conn):
        """Wait for cloud-init to finish (Docker install via user_data)."""
        logger.info("Waiting for cloud-init to complete")
        await self._ssh.run(conn, "cloud-init status --wait 2>/dev/null || true")
        # Verify Docker is available
        await self._ssh.run(conn, "docker version >/dev/null 2>&1")

    async def _wait_for_cloud_init_and_upload_repo(self, conn, repo_path: str, app_dir: str):
        """Wait for cloud-init and upload repo in parallel."""
        import asyncio

        async def wait_cloud_init():
            await self._wait_for_cloud_init(conn)

        async def upload_repo():
            await self._ssh.run(conn, f"mkdir -p {app_dir}")
            await self._upload_repo(conn, repo_path, app_dir)

        await asyncio.gather(wait_cloud_init(), upload_repo())

    _TAR_EXCLUDE_PATTERNS = [
        "node_modules", ".next", "__pycache__", ".venv", "venv",
        "dist", "build", ".git", ".tox", ".mypy_cache", ".pytest_cache",
    ]

    # Extra excludes for nanoclaw local uploads: don't leak secrets or
    # ship local-only state (SQLite DB) to the remote VM.
    _NANOCLAW_EXTRA_EXCLUDES = [".env", "store"]

    async def _upload_repo(self, conn, repo_path: str, app_dir: str,
                           extra_excludes: list[str] | None = None):
        from computeedge.utils.git import is_git_url
        if is_git_url(repo_path):
            logger.info("Cloning repo on server: %s", repo_path)
            await self._ssh.run(conn, f"git clone --depth 1 {repo_path} {app_dir}/repo")
            return

        import asyncio
        import tempfile
        local_path = Path(repo_path)
        if not local_path.exists():
            raise DeploymentError(f"Repo path does not exist: {repo_path}")

        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tar_path = tmp.name

        # Build exclude list: start with standard patterns + any extras
        exclude_patterns = list(self._TAR_EXCLUDE_PATTERNS)
        if extra_excludes:
            exclude_patterns.extend(extra_excludes)

        # If git repo, also read .gitignore for extra excludes
        git_dir = local_path / ".git"
        if git_dir.is_dir():
            # Use git to list ignored patterns from all .gitignore files
            # and add .git itself to exclusions
            exclude_patterns.append(".git")
            gitignore = local_path / ".gitignore"
            if gitignore.is_file():
                for line in gitignore.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # Strip trailing slashes for tar compatibility
                        exclude_patterns.append(line.rstrip("/"))

        exclude_args = []
        for pattern in exclude_patterns:
            exclude_args.extend(["--exclude", pattern])
        proc = await asyncio.create_subprocess_exec(
            "tar", "-czf", tar_path, *exclude_args, "-C", str(local_path), ".",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            Path(tar_path).unlink(missing_ok=True)
            raise DeploymentError(f"Failed to create tar archive: {stderr.decode().strip()}")

        # Size warning
        archive_size_mb = Path(tar_path).stat().st_size / (1024 * 1024)
        if archive_size_mb > 100:
            logger.warning("Repo archive is %.0fMB — upload may be slow", archive_size_mb)

        await self._ssh.upload(conn, tar_path, f"{app_dir}/repo.tar.gz")
        await self._ssh.run(conn, f"mkdir -p {app_dir}/repo && tar -xzf {app_dir}/repo.tar.gz -C {app_dir}/repo")
        Path(tar_path).unlink(missing_ok=True)

    async def _upload_docker_files(self, conn, analysis: RepoAnalysis, app_dir: str,
                                   env_vars: dict | None,
                                   repo_path: str | None = None):
        # Render all configs using the shared method
        configs = self.render_configs(analysis, repo_path or ".", env_vars)

        # Upload Dockerfiles to their correct repo paths (handles SSH path routing)
        from computeedge.utils.git import is_git_url
        local_path = Path(repo_path) if repo_path and not is_git_url(repo_path) else None
        await self._upload_generated_dockerfiles(conn, analysis, app_dir, repo_path=local_path)

        # Ensure app_dir exists before uploading infra files (guards against race on retry)
        await self._ssh.run(conn, f"mkdir -p {app_dir}")

        # Upload infra files (skip metadata keys starting with _)
        await self._ssh.upload_string(conn, configs["docker-compose.yml"], f"{app_dir}/docker-compose.yml")
        await self._ssh.upload_string(conn, configs["nginx.conf"], f"{app_dir}/nginx.conf")
        await self._ssh.upload_string(conn, configs[".env"], f"{app_dir}/.env")

    async def _upload_pregenerated_configs(self, conn, docker_configs: dict, app_dir: str, env_vars: dict | None):
        """Upload pre-generated docker configs instead of using template generation."""
        generated_env = {"SECRET_KEY": secrets.token_hex(32)}
        if env_vars:
            generated_env.update(env_vars)
        env_content = "\n".join(f"{k}={v}" for k, v in generated_env.items())

        for filename, content in docker_configs.items():
            if filename in self._INFRA_FILES:
                dest = f"{app_dir}/{filename}"
            else:
                dest = f"{app_dir}/repo/{filename}"
                parent = "/".join(dest.split("/")[:-1])
                await self._ssh.run(conn, f"mkdir -p {parent}")
            await self._ssh.upload_string(conn, content, dest)

        await self._ssh.upload_string(conn, env_content, f"{app_dir}/.env")

    # Files that live at the app_dir level (infra config), not inside the repo build context
    _INFRA_FILES = {"nginx.conf", "docker-compose.yml", "docker-compose.yaml", ".env"}

    async def _apply_docker_config_patches(self, conn, docker_configs: dict, app_dir: str):
        """Patch template-generated files with docker_configs overrides.

        Uploads only the keys present in docker_configs, leaving all other
        template-generated files (nginx.conf, docker-compose.yml, etc.) intact.
        Env vars are intentionally NOT touched here — they were already written
        correctly by _upload_docker_files.
        """
        for filename, content in docker_configs.items():
            if filename in self._INFRA_FILES:
                dest = f"{app_dir}/{filename}"
            else:
                dest = f"{app_dir}/repo/{filename}"
                parent = "/".join(dest.split("/")[:-1])
                await self._ssh.run(conn, f"mkdir -p {parent}")
            await self._ssh.upload_string(conn, content, dest)

    async def _find_compose_info(self, conn, repo_dir: str) -> tuple[str, str]:
        """Return (compose_directory, compose_filename) for docker compose -f."""
        candidates = [
            (repo_dir, "docker-compose.yml"),
            (repo_dir, "docker-compose.yaml"),
            (f"{repo_dir}/docker", "docker-compose.yml"),
            (f"{repo_dir}/docker", "docker-compose.yaml"),
        ]
        for directory, filename in candidates:
            result = await self._ssh.run(conn, f"test -f {directory}/{filename} && echo yes || echo no")
            if result.strip() == "yes":
                return directory, filename
        return repo_dir, "docker-compose.yml"

    async def _detect_exposed_port(self, conn) -> int:
        """Detect the host port exposed by running containers. Returns 80 as default."""
        try:
            # Get host ports bound by running containers, pick the lowest HTTP-like port
            output = await self._ssh.run(
                conn,
                "docker ps --format '{{.Ports}}' | grep -oE '0\\.0\\.0\\.0:[0-9]+' | cut -d: -f2 | sort -n | head -1"
            )
            port = output.strip()
            if port and port.isdigit():
                return int(port)
        except Exception:
            pass
        return 80

    async def _upload_generated_dockerfiles(self, conn, analysis: RepoAnalysis,
                                            app_dir: str, repo_path: Path | None = None):
        """Generate and upload Dockerfiles into the repo directory at detected paths."""
        backend = analysis.stack.backend
        frontend = analysis.stack.frontend

        if self._needs_unified_container(analysis):
            # Unified container: one Dockerfile at repo root (build context = repo root)
            context = self._build_unified_context(analysis, repo_path)
            dockerfile = self._jinja.get_template("Dockerfile.unified.j2").render(**context)
            dest = f"{app_dir}/repo/Dockerfile"
            await self._ssh.upload_string(conn, dockerfile, dest)
            return

        if backend and frontend:
            # True Node monorepo — one process serves both
            context = self._build_template_context(backend or "express", analysis, repo_path)
            context["analysis"] = analysis
            context["port"] = self._detect_server_port(analysis)
            context["app_port"] = context["port"]
            dockerfile = self._jinja.get_template("Dockerfile.fullstack-node.j2").render(**context)
            deploy_path = analysis.stack.backend_path or "."
            dest = f"{app_dir}/repo/{deploy_path}/Dockerfile"
            await self._ssh.upload_string(conn, dockerfile, dest)
            return

        # Single stack: backend only or frontend only
        if backend:
            template_name = self._backend_dockerfile_template(backend, analysis.stack.backend_language)
            if template_name:
                context = self._build_template_context(backend, analysis, repo_path)
                context["analysis"] = analysis
                dockerfile = self._jinja.get_template(template_name).render(**context)
                dest = f"{app_dir}/repo/{analysis.stack.backend_path or '.'}/Dockerfile"
                await self._ssh.upload_string(conn, dockerfile, dest)

        elif frontend:
            template_name = self._frontend_dockerfile_template(frontend)
            if template_name:
                context = self._build_template_context(frontend, analysis, repo_path)
                context["analysis"] = analysis
                dockerfile = self._jinja.get_template(template_name).render(**context)
                dest = f"{app_dir}/repo/{analysis.stack.frontend_path or '.'}/Dockerfile"
                await self._ssh.upload_string(conn, dockerfile, dest)

    def _detect_server_port(self, analysis: RepoAnalysis) -> int:
        """Return the port the app server listens on, from stacks.yaml default_port."""
        stack_name = analysis.stack.backend or analysis.stack.frontend
        if stack_name:
            config = self._get_stack_config(stack_name)
            if config and config.get("default_port"):
                return int(config["default_port"])
        return 3000

    def _backend_dockerfile_template(self, backend: str, language: str | None = None) -> str | None:
        # Existing hardcoded paths — untouched
        if backend == "fastapi":
            return "Dockerfile.fastapi.j2"
        if backend == "express":
            if language == "typescript":
                return "Dockerfile.express-ts.j2"
            return "Dockerfile.express.j2"

        # Config-driven selection for new stacks
        stack_config = self._get_stack_config(backend)
        if not stack_config or not stack_config.get("deployable"):
            return None

        template_type = stack_config.get("template_type")
        if template_type == "language":
            return f"Dockerfile.{stack_config['language']}.j2"
        if template_type == "framework":
            return f"Dockerfile.{backend}.j2"
        return None

    def _frontend_dockerfile_template(self, frontend: str) -> str | None:
        # Existing hardcoded paths — untouched
        if frontend == "nextjs":
            return "Dockerfile.nextjs.j2"
        if frontend == "react":
            return "Dockerfile.react.j2"

        stack_config = self._get_stack_config(frontend)
        if not stack_config or not stack_config.get("deployable"):
            return None

        # Config-driven: language-based template (e.g., Streamlit → Dockerfile.python.j2)
        if stack_config.get("template_type") == "language":
            return f"Dockerfile.{stack_config['language']}.j2"

        # Template reuse (Vue → React template)
        reuse = stack_config.get("template")
        if reuse:
            return self._frontend_dockerfile_template(reuse)
        return None

    _DIAGNOSIS_RULES = [
        {
            "pattern": r"mysql/mysql\.h.*No such file",
            "description": "Add system package 'default-libmysqlclient-dev' to Dockerfile",
            "confidence": "high",
            "fix_type": "dockerfile_modification",
            "package": "default-libmysqlclient-dev",
        },
        {
            "pattern": r"pg_config.*not found",
            "description": "Add system package 'libpq-dev' to Dockerfile",
            "confidence": "high",
            "fix_type": "dockerfile_modification",
            "package": "libpq-dev",
        },
        {
            "pattern": r"ModuleNotFoundError: No module named '(\w+)'",
            "description": "Python module '{match}' not installed — add it to requirements.txt or pip install",
            "confidence": "high",
            "fix_type": "dependency_missing",
        },
        {
            "pattern": r"Cannot find module '([^']+)'",
            "description": "Node module '{match}' not found — run npm install or check package.json",
            "confidence": "high",
            "fix_type": "dependency_missing",
        },
        {
            "pattern": r"ECONNREFUSED 127\.0\.0\.1:\d+",
            "description": "App is connecting to localhost instead of the 'db' Docker service — update DATABASE_URL to use 'db' as hostname",
            "confidence": "high",
            "fix_type": "config_issue",
        },
        {
            "pattern": r"assets:precompile.*fail",
            "description": "Rails asset precompilation failed — ensure SECRET_KEY_BASE and RAILS_ENV=production are set at build time",
            "confidence": "medium",
            "fix_type": "dockerfile_modification",
        },
        {
            "pattern": r"go: module requires Go >= (\d+\.\d+)",
            "description": "Go version mismatch — update base image to golang:{match}-alpine",
            "confidence": "medium",
            "fix_type": "dockerfile_modification",
        },
        {
            "pattern": r"bind: address already in use",
            "description": "Port conflict — another process is using the same port, change the CMD port",
            "confidence": "medium",
            "fix_type": "config_issue",
        },
        {
            "pattern": r"COPY failed:.*file not found",
            "description": "Docker COPY failed — check build context path and .dockerignore",
            "confidence": "medium",
            "fix_type": "dockerfile_modification",
        },
    ]

    def _diagnose_failure(self, logs: str, dockerfile_used: str) -> list[dict]:
        """Match error logs against known patterns and return suggested fixes."""
        import re
        fixes = []
        for rule in self._DIAGNOSIS_RULES:
            match = re.search(rule["pattern"], logs, re.IGNORECASE)
            if match:
                description = rule["description"]
                if "{match}" in description and match.groups():
                    description = description.replace("{match}", match.group(1))
                fix = {
                    "description": description,
                    "confidence": rule["confidence"],
                    "fix_type": rule["fix_type"],
                }
                # Pass through extra keys (package, vars) for config patching
                for extra_key in ("package", "vars"):
                    if extra_key in rule:
                        fix[extra_key] = rule[extra_key]
                fixes.append(fix)
        return fixes

    def _infer_failure_phase(self, error_msg: str) -> str:
        """Infer which deployment phase failed from the error message."""
        lower = error_msg.lower()
        if "migration" in lower:
            return "migration"
        if "compose up" in lower or "container" in lower:
            return "compose_up"
        if "build" in lower or "dockerfile" in lower:
            return "docker_build"
        return "unknown"

    def _build_deploy_diagnostics(self, phase: str, error_msg: str,
                                   container_logs: str, analysis: RepoAnalysis,
                                   dockerfile_used: str = "",
                                   generated_files: dict | None = None) -> "DeployDiagnostics":
        """Build structured diagnostics from a deployment failure."""
        from computeedge.models import DeployDiagnostics

        # Extract relevant error lines
        relevant = []
        for line in container_logs.splitlines():
            line_stripped = line.strip()
            if any(kw in line_stripped.upper() for kw in ["ERROR", "FATAL", "EXCEPTION", "FAILED", "CANNOT", "NOT FOUND"]):
                relevant.append(line_stripped)
        if not relevant and container_logs.strip():
            # Take last 5 lines as fallback
            relevant = [l.strip() for l in container_logs.splitlines()[-5:] if l.strip()]

        suggested_fixes = self._diagnose_failure(container_logs, dockerfile_used)

        context = {
            "stack": analysis.stack.backend or analysis.stack.frontend or "",
            "database": analysis.database.type if analysis.database else "",
        }

        retry_hint = ""
        if suggested_fixes:
            retry_hint = "Call deploy with docker_configs parameter using the modified Dockerfile"

        return DeployDiagnostics(
            phase=phase,
            service="backend" if analysis.stack.backend else "frontend",
            summary=error_msg[:200],
            relevant_logs=relevant[:10],
            dockerfile_used=dockerfile_used,
            generated_files=generated_files or {},
            context=context,
            suggested_fixes=suggested_fixes,
            retry_hint=retry_hint,
        )

    _DEP_FILES_BY_LANGUAGE = {
        "python": ["requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg"],
        "javascript": ["package.json", "tsconfig.json"],
        "typescript": ["package.json", "tsconfig.json"],
        "go": ["go.mod"],
        "ruby": ["Gemfile"],
    }
    _LOCK_FILES = ["package-lock.json", "go.sum", "Gemfile.lock"]
    _UNIVERSAL_FILES = ["Dockerfile", ".env.example", "Makefile"]

    async def _read_dependency_files(self, conn, analysis: RepoAnalysis,
                                     repo_dir: str) -> dict[str, str]:
        """Read dependency/config files from the uploaded repo via SSH."""
        from computeedge.models import MAX_DEP_FILE_SIZE, MAX_LOCK_FILE_SIZE

        language = analysis.stack.backend_language or ""
        files_to_read = list(self._DEP_FILES_BY_LANGUAGE.get(language, []))
        files_to_read.extend(self._UNIVERSAL_FILES)

        # Determine search paths (repo root + backend/frontend subdirs)
        search_paths = [repo_dir]
        if analysis.stack.backend_path and analysis.stack.backend_path != ".":
            search_paths.append(f"{repo_dir}/{analysis.stack.backend_path}")
        if analysis.stack.frontend_path and analysis.stack.frontend_path != ".":
            search_paths.append(f"{repo_dir}/{analysis.stack.frontend_path}")

        result = {}
        for search_path in search_paths:
            prefix = search_path.replace(repo_dir, "").strip("/")
            for filename in files_to_read:
                cap = MAX_LOCK_FILE_SIZE if filename in self._LOCK_FILES else MAX_DEP_FILE_SIZE
                try:
                    content = await self._ssh.run(
                        conn, f"head -c {cap} {search_path}/{filename} 2>/dev/null"
                    )
                    if content and content.strip():
                        key = f"{prefix}/{filename}" if prefix else filename
                        result[key] = content[:cap]
                except Exception:
                    pass

        # Also try lock files separately with lower cap
        for lock_file in self._LOCK_FILES:
            for search_path in search_paths:
                prefix = search_path.replace(repo_dir, "").strip("/")
                try:
                    content = await self._ssh.run(
                        conn, f"head -c {MAX_LOCK_FILE_SIZE} {search_path}/{lock_file} 2>/dev/null"
                    )
                    if content and content.strip():
                        key = f"{prefix}/{lock_file}" if prefix else lock_file
                        result[key] = content[:MAX_LOCK_FILE_SIZE]
                except Exception:
                    pass

        return result

    def _build_suggested_docker_configs(self, generated_files: dict[str, str],
                                        suggested_fixes: list[dict]) -> dict[str, str] | None:
        """Apply rule-based fixes to generated configs, producing patched versions."""
        import re
        if not suggested_fixes:
            return None

        actionable = [f for f in suggested_fixes
                      if f.get("fix_type") in ("dockerfile_modification", "env_var")]
        if not actionable:
            return None

        patched = dict(generated_files)

        for fix in actionable:
            fix_type = fix["fix_type"]

            if fix_type == "dockerfile_modification" and "package" in fix:
                # Insert apt package into the apt-get install line
                package = fix["package"]
                for fname, content in patched.items():
                    if "Dockerfile" in fname:
                        patched[fname] = re.sub(
                            r"(apt-get install -y\s+(?:--no-install-recommends\s+)?)(.*?)(\s*&&)",
                            lambda m: (
                                m.group(1) + m.group(2).rstrip() + " " + package + m.group(3)
                                if package not in m.group(2) else m.group(0)
                            ),
                            content,
                        )

            elif fix_type == "env_var" and "vars" in fix:
                # Append env vars to docker-compose environment section
                env_vars = fix["vars"]
                compose_key = next(
                    (k for k in patched if "compose" in k.lower()), None
                )
                if compose_key:
                    content = patched[compose_key]
                    env_lines = "\n".join(
                        f"      - {k}={v}" for k, v in env_vars.items()
                    )
                    # Find the last environment var line and append after it
                    patched[compose_key] = re.sub(
                        r"(environment:\n(?:\s+- .+\n)+)",
                        lambda m: m.group(0) + env_lines + "\n",
                        content,
                    )

        if patched == generated_files:
            return None
        return patched

    def _build_agent_instruction(self, phase: str, error_summary: str,
                                 suggested_fixes: list[dict],
                                 has_suggested_configs: bool,
                                 retry_context: "RetryContext | None") -> str:
        """Generate natural language instruction for the host LLM."""
        from computeedge.models import RetryContext

        # Final attempt — tell LLM to surface to user
        if retry_context and retry_context.attempt >= retry_context.max_retries:
            error_history = ""
            for i, err in enumerate(retry_context.previous_errors, 1):
                error_history += f" [{i}] {err},"
            return (
                f"Deploy failed after {retry_context.max_retries} attempts."
                f" Previous errors:{error_history.rstrip(',')}."
                " The server has been deleted."
                " Present the full diagnostics to the user and ask for guidance."
            )

        parts = [f"Deploy failed during {phase}: {error_summary}."]

        # What was previously tried
        if retry_context and retry_context.previous_fixes:
            prev = "; ".join(
                f.get("fix_description", "unknown fix") for f in retry_context.previous_fixes
            )
            parts.append(f"Previously tried: {prev}.")

        # What to do next
        if has_suggested_configs:
            parts.append(
                "A patched config is provided in `suggested_docker_configs`."
                " Call deploy with `docker_configs` set to the value of"
                " `suggested_docker_configs` and pass back `retry_context` unchanged."
            )
        else:
            parts.append(
                "No automatic fix is available. The full build logs, generated"
                " Dockerfile, and dependency files are included in diagnostics."
                " Analyze the logs to identify the root cause, modify the"
                " Dockerfile in `generated_files` to fix the issue, and call"
                " deploy with your modified files as `docker_configs`."
                " Pass back `retry_context` unchanged."
            )

        return " ".join(parts)

    async def _connect_to_existing_server(self, retry_context) -> object | None:
        """Connect to a kept-alive server from a previous attempt.

        Returns the SSH connection, or None if the server is gone (triggering fallback).
        Raises DeploymentError if connection succeeds but Docker validation fails.
        """
        import asyncio
        last_error = None
        for attempt in range(3):  # initial + 2 retries
            try:
                if attempt > 0:
                    logger.info("Retry SSH to existing server %s (%d/2)",
                                retry_context.server_ip, attempt)
                    await asyncio.sleep(5)
                conn = await self._ssh.connect(retry_context.server_ip, retry_context.ssh_key_path)
                # Validate Docker is still operational
                try:
                    await self._ssh.run(conn, "docker compose version")
                except Exception as docker_err:
                    raise DeploymentError(
                        f"Docker validation failed on existing server {retry_context.server_ip}: {docker_err}"
                    )
                return conn
            except DeploymentError:
                raise  # Don't catch our own validation error
            except Exception as e:
                last_error = e
                logger.warning("SSH to existing server failed (attempt %d): %s", attempt + 1, e)

        logger.warning("Server %s appears gone after 3 attempts: %s",
                        retry_context.server_ip, last_error)
        return None

    async def redeploy(self, deployment_id: str, repo_path: str,
                       analysis: RepoAnalysis, env_vars: dict | None = None,
                       user_id: int = 0) -> DeployResult:
        """Update code on an existing server without creating a new one."""
        state = await self._state.get(deployment_id, user_id)
        if state is None:
            raise DeploymentError(f"Deployment {deployment_id} not found")

        # Nanoclaw: native VM redeploy path
        if state.get("deploy_mode") == "native" or self._is_nanoclaw(analysis):
            return await self._redeploy_nanoclaw(
                deployment_id, repo_path, analysis, env_vars, state, user_id=user_id,
            )

        ip = state["ip"]
        private_key = state.get("ssh_key_path")
        if not private_key:
            raise DeploymentError(f"No SSH key found for deployment {deployment_id}")

        conn = await self._ssh_connect_with_retry(ip, private_key)

        app_dir = f"/root/{deployment_id}"

        # Re-upload repo
        await self._ssh.run(conn, f"rm -rf {app_dir}/repo")
        await self._upload_repo(conn, repo_path, app_dir)

        # Rebuild containers
        await self._ssh.run(conn, f"cd {app_dir} && DOCKER_BUILDKIT=1 docker compose up -d --build")

        # Run migrations if detected
        if analysis.migration_command and analysis.database is not None:
            import asyncio as _asyncio
            await _asyncio.sleep(10)
            try:
                await self._ssh.run(
                    conn,
                    f"cd {app_dir} && docker compose exec -T backend {analysis.migration_command}"
                )
            except Exception as migration_error:
                raise DeploymentError(
                    f"Redeploy succeeded but migration failed: {migration_error}",
                    suggestion=f"SSH in and run: docker compose exec backend {analysis.migration_command}",
                ) from migration_error

        monthly_cost = state.get("monthly_cost", 0.0)
        exposed_port = await self._detect_exposed_port(conn)
        url = f"http://{ip}" if exposed_port == 80 else f"http://{ip}:{exposed_port}"

        # Update state with new timestamp
        await self._state.update(deployment_id, user_id, {
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "repo_path": repo_path,
            "normalized_repo_path": self._state.normalize_repo_path(repo_path),
        })

        return DeployResult(
            status="deployed",
            provider=self._provider_name,
            url=url,
            deployment_id=deployment_id,
            monthly_cost=monthly_cost,
            ssh_access=f"ssh root@{ip}",
            next_steps=["Code updated and containers rebuilt."],
        )
