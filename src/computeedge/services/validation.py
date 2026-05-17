"""Config validation service — verifies generated configs against actual repo structure."""

import re
from pathlib import Path

from computeedge.models import RepoAnalysis, ValidationIssue, ValidationResult


class ConfigValidator:
    """Validates generated deployment configs against repo files.

    Two modes:
      - auto_fix=True  (generate_configs): fixes deterministic issues, returns corrected configs
      - auto_fix=False (deploy): report-only, never modifies configs
    """

    def validate(
        self,
        configs: dict[str, str],
        analysis: RepoAnalysis,
        repo_path: str | Path,
        auto_fix: bool = True,
    ) -> ValidationResult:
        repo_path = Path(repo_path)
        issues: list[ValidationIssue] = []
        fixes: dict[str, str] = {}

        backend_path = repo_path
        if analysis.stack.backend_path and analysis.stack.backend_path != ".":
            backend_path = repo_path / analysis.stack.backend_path

        frontend_path = repo_path
        if analysis.stack.frontend_path and analysis.stack.frontend_path != ".":
            frontend_path = repo_path / analysis.stack.frontend_path

        # Check 1: Dependency files
        dep_issues, dep_fixes = self._check_dependency_files(
            analysis, repo_path, backend_path, frontend_path, configs,
        )
        issues.extend(dep_issues)
        fixes.update(dep_fixes)

        # Check 2: Entry point detection
        entry_issues, entry_fixes = self._check_entry_point(
            analysis, repo_path, backend_path, configs,
        )
        issues.extend(entry_issues)
        fixes.update(entry_fixes)

        dockerfile = configs.get("Dockerfile", "")

        # Check 3: First-party import resolution
        entry_file = self._resolve_entry_file(analysis, backend_path, fixes.get("Dockerfile", dockerfile))
        if entry_file and entry_file.exists():
            import_issues = self._check_imports(entry_file, backend_path, analysis)
            issues.extend(import_issues)

        # Check 4: Symbol existence in resolved modules
        if entry_file and entry_file.exists():
            symbol_issues = self._check_symbols(entry_file, backend_path)
            issues.extend(symbol_issues)

        # Check 5: Docker COPY path verification
        copy_issues, copy_fixes = self._check_copy_paths(
            fixes.get("Dockerfile", dockerfile), repo_path,
        )
        issues.extend(copy_issues)
        fixes.update(copy_fixes)

        # Build corrected configs
        corrected = dict(configs)
        corrections_applied: list[str] = []
        if auto_fix and fixes:
            for key, new_content in fixes.items():
                if key in corrected and corrected[key] != new_content:
                    corrected[key] = new_content
            for key in fixes:
                if configs.get(key) != corrected.get(key):
                    corrections_applied.append(f"Auto-fixed {key}")
            # Remove errors that have corresponding fixes (they're resolved)
            fixed_checks = {i.check for i in issues if i.severity == "error" and i.file in fixes}
            issues = [i for i in issues if not (i.severity == "error" and i.check in fixed_checks and i.file in fixes)]

        has_errors = any(i.severity == "error" for i in issues)
        return ValidationResult(
            valid=not has_errors,
            issues=issues,
            corrected_configs=corrected,
            corrections_applied=corrections_applied,
        )

    def _check_dependency_files(
        self,
        analysis: RepoAnalysis,
        repo_path: Path,
        backend_path: Path,
        frontend_path: Path,
        configs: dict[str, str],
    ) -> tuple[list[ValidationIssue], dict[str, str]]:
        issues: list[ValidationIssue] = []
        fixes: dict[str, str] = {}
        dockerfile = configs.get("Dockerfile", "")

        lang = analysis.stack.backend_language

        if lang == "python":
            has_req = (backend_path / "requirements.txt").exists()
            has_pyproject = (backend_path / "pyproject.toml").exists()

            if not has_req and not has_pyproject:
                issues.append(ValidationIssue(
                    severity="error",
                    check="dep_file",
                    message=f"No requirements.txt or pyproject.toml found in {backend_path}",
                    file="Dockerfile",
                    suggestion="Create a requirements.txt with your Python dependencies",
                ))
            elif "pyproject.toml" in dockerfile and not has_pyproject and has_req:
                issues.append(ValidationIssue(
                    severity="error",
                    check="dep_file",
                    message=f"Dockerfile references pyproject.toml but only requirements.txt exists in {backend_path}",
                    file="Dockerfile",
                    suggestion="Use requirements.txt instead of pyproject.toml",
                ))
                fixed = dockerfile.replace("pyproject.toml", "requirements.txt")
                fixed = re.sub(
                    r"RUN pip install --no-cache-dir \.",
                    "RUN pip install --no-cache-dir -r requirements.txt",
                    fixed,
                )
                fixes["Dockerfile"] = fixed
            elif "requirements.txt" in dockerfile and not has_req and has_pyproject:
                issues.append(ValidationIssue(
                    severity="error",
                    check="dep_file",
                    message=f"Dockerfile references requirements.txt but only pyproject.toml exists in {backend_path}",
                    file="Dockerfile",
                    suggestion="Use pyproject.toml instead of requirements.txt",
                ))
                fixed = dockerfile.replace("COPY requirements.txt .", "COPY pyproject.toml .")
                fixed = re.sub(
                    r"RUN pip install --no-cache-dir -r requirements\.txt",
                    "RUN pip install --no-cache-dir .",
                    fixed,
                )
                fixes["Dockerfile"] = fixed

        elif lang in ("javascript", "typescript") or (analysis.stack.frontend and not analysis.stack.backend):
            check_path = frontend_path if not analysis.stack.backend else backend_path
            if not (check_path / "package.json").exists():
                issues.append(ValidationIssue(
                    severity="error",
                    check="dep_file",
                    message=f"Missing package.json in {check_path}",
                    file="Dockerfile",
                    suggestion="Run npm init to create a package.json",
                ))

        elif lang == "go":
            if not (backend_path / "go.mod").exists():
                issues.append(ValidationIssue(
                    severity="error",
                    check="dep_file",
                    message=f"Missing go.mod in {backend_path}",
                    file="Dockerfile",
                    suggestion="Run go mod init to create go.mod",
                ))
            elif not (backend_path / "go.sum").exists():
                issues.append(ValidationIssue(
                    severity="error",
                    check="dep_file",
                    message=f"Missing go.sum in {backend_path} (Dockerfile COPYs both go.mod and go.sum)",
                    file="Dockerfile",
                    suggestion="Run go mod tidy to generate go.sum",
                ))

        return issues, fixes

    def _check_entry_point(
        self,
        analysis: RepoAnalysis,
        repo_path: Path,
        backend_path: Path,
        configs: dict[str, str],
    ) -> tuple[list[ValidationIssue], dict[str, str]]:
        issues: list[ValidationIssue] = []
        fixes: dict[str, str] = {}
        dockerfile = configs.get("Dockerfile", "")
        lang = analysis.stack.backend_language
        framework = analysis.stack.backend

        if lang == "python" and framework in ("fastapi", "flask"):
            current_module = None
            for pattern in [r'uvicorn\s+([^\s:]+):', r'gunicorn\s+([^\s:]+):']:
                m = re.search(pattern, dockerfile)
                if m:
                    current_module = m.group(1)
                    break
            if current_module is None:
                return issues, fixes
            module_file = current_module.replace(".", "/") + ".py"
            if (backend_path / module_file).exists():
                return issues, fixes
            search_pattern = "FastAPI(" if framework == "fastapi" else "Flask(__name__)"
            detected = self._find_framework_entry(backend_path, search_pattern)
            if detected:
                rel = detected.relative_to(backend_path)
                module_path = str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")
                new_entry = f"{module_path}:app"
                old_entry = f"{current_module}:app"
                fixed = dockerfile.replace(old_entry, new_entry)
                fixes["Dockerfile"] = fixed
            else:
                issues.append(ValidationIssue(
                    severity="error", check="entry_point",
                    message=f"Module '{module_file}' not found at {backend_path / module_file}",
                    file="Dockerfile",
                    suggestion="Verify the uvicorn/gunicorn entry point module exists",
                ))

        elif lang == "python" and framework == "django":
            m = re.search(r'gunicorn\s+([^\s:]+):', dockerfile)
            if m:
                module = m.group(1)
                wsgi_file = module.replace(".", "/") + ".py"
                if not (backend_path / wsgi_file).exists():
                    wsgi_candidates = list(backend_path.rglob("wsgi.py"))
                    if wsgi_candidates:
                        wsgi = wsgi_candidates[0]
                        rel = wsgi.relative_to(backend_path)
                        parent = rel.parent
                        new_module = str(parent).replace("/", ".").replace("\\", ".")
                        old_entry = f"{module}:application"
                        new_entry = f"{new_module}.wsgi:application"
                        fixed = dockerfile.replace(old_entry, new_entry)
                        fixes["Dockerfile"] = fixed
                    else:
                        issues.append(ValidationIssue(
                            severity="error", check="entry_point",
                            message=f"Django wsgi module '{wsgi_file}' not found",
                            file="Dockerfile",
                            suggestion="Verify your Django project has a wsgi.py",
                        ))

        elif lang == "python" and framework == "streamlit":
            m = re.search(r'streamlit\s+run\s+(\S+)', dockerfile)
            if m:
                entry = m.group(1)
                if not (backend_path / entry).exists():
                    for fallback in ["app.py", "streamlit_app.py", "main.py"]:
                        if (backend_path / fallback).exists():
                            fixed = dockerfile.replace(f"streamlit run {entry}", f"streamlit run {fallback}")
                            fixes["Dockerfile"] = fixed
                            break
                    else:
                        issues.append(ValidationIssue(
                            severity="error", check="entry_point",
                            message=f"Streamlit entry '{entry}' not found at {backend_path / entry}",
                            file="Dockerfile",
                            suggestion="Verify the Streamlit entry point file path",
                        ))

        elif lang in ("javascript", "typescript"):
            m = re.search(r'CMD\s+\["node",\s*"([^"]+)"', dockerfile)
            if m:
                entry = m.group(1)
                if not (backend_path / entry).exists():
                    for fallback in ["index.js", "server.js", "app.js", "src/index.js"]:
                        if (backend_path / fallback).exists():
                            fixed = dockerfile.replace(f'"node", "{entry}"', f'"node", "{fallback}"')
                            fixes["Dockerfile"] = fixed
                            break
                    else:
                        issues.append(ValidationIssue(
                            severity="error", check="entry_point",
                            message=f"Entry point '{entry}' not found at {backend_path / entry}",
                            file="Dockerfile",
                            suggestion="Verify the Node.js entry point file path",
                        ))

        return issues, fixes

    def _resolve_entry_file(self, analysis: RepoAnalysis, backend_path: Path, dockerfile: str) -> Path | None:
        if analysis.stack.backend_language != "python":
            return None
        for pattern in [r'uvicorn\s+([^\s:]+):', r'gunicorn\s+([^\s:]+):']:
            m = re.search(pattern, dockerfile)
            if m:
                module = m.group(1)
                module_file = module.replace(".", "/") + ".py"
                return backend_path / module_file
        return None

    def _check_imports(self, entry_file: Path, backend_path: Path, analysis: RepoAnalysis) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        try:
            content = entry_file.read_text(errors="ignore")
        except OSError:
            return issues
        try:
            rel = entry_file.relative_to(backend_path)
        except ValueError:
            return issues
        parts = rel.parts
        if len(parts) < 2:
            return issues
        package_name = parts[0]
        for line in content.splitlines():
            line = line.strip()
            m = re.match(r'^from\s+([\w.]+)\s+import\s+', line)
            if m:
                module_path = m.group(1)
                if not module_path.startswith(package_name + ".") and module_path != package_name:
                    continue
                file_path = backend_path / module_path.replace(".", "/")
                py_file = file_path.with_suffix(".py")
                if not py_file.exists() and not file_path.is_dir():
                    issues.append(ValidationIssue(
                        severity="error", check="import_resolution",
                        message=f"{entry_file.name} imports '{module_path}' but {py_file.relative_to(backend_path)} does not exist",
                        file=str(entry_file.relative_to(backend_path)),
                        suggestion=f"Create {py_file.relative_to(backend_path)} or remove the import",
                    ))
            m = re.match(r'^import\s+([\w.]+)', line)
            if m:
                module_path = m.group(1)
                if not module_path.startswith(package_name + "."):
                    continue
                file_path = backend_path / module_path.replace(".", "/")
                py_file = file_path.with_suffix(".py")
                if not py_file.exists() and not file_path.is_dir():
                    issues.append(ValidationIssue(
                        severity="error", check="import_resolution",
                        message=f"{entry_file.name} imports '{module_path}' but {py_file.relative_to(backend_path)} does not exist",
                        file=str(entry_file.relative_to(backend_path)),
                        suggestion=f"Create {py_file.relative_to(backend_path)} or remove the import",
                    ))
        return issues

    def _check_symbols(self, entry_file: Path, backend_path: Path) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        try:
            content = entry_file.read_text(errors="ignore")
        except OSError:
            return issues
        try:
            rel = entry_file.relative_to(backend_path)
        except ValueError:
            return issues
        parts = rel.parts
        if len(parts) < 2:
            return issues
        package_name = parts[0]
        for line in content.splitlines():
            line = line.strip()
            m = re.match(r'^from\s+([\w.]+)\s+import\s+(.+)', line)
            if not m:
                continue
            module_path = m.group(1)
            if not module_path.startswith(package_name + ".") and module_path != package_name:
                continue
            target_file = (backend_path / module_path.replace(".", "/")).with_suffix(".py")
            if not target_file.exists():
                continue
            try:
                target_content = target_file.read_text(errors="ignore")
            except OSError:
                continue
            imports_str = m.group(2).strip().rstrip("\\")
            names = [n.strip().split(" as ")[0].strip() for n in imports_str.split(",")]
            for name in names:
                if not name or name.startswith("("):
                    continue
                name = name.strip("()")
                if not name:
                    continue
                patterns = [
                    rf'^def\s+{re.escape(name)}\s*[\(:]',
                    rf'^class\s+{re.escape(name)}[\s\(:]',
                    rf'^{re.escape(name)}\s*=',
                    rf'^{re.escape(name)}\s*:',
                ]
                found = any(re.search(p, target_content, re.MULTILINE) for p in patterns)
                if not found:
                    init_file = target_file.parent / "__init__.py"
                    if init_file.exists():
                        try:
                            init_content = init_file.read_text(errors="ignore")
                            found = name in init_content
                        except OSError:
                            pass
                if not found:
                    issues.append(ValidationIssue(
                        severity="warning", check="symbol_existence",
                        message=f"'{name}' imported from '{module_path}' but not found in {target_file.relative_to(backend_path)}",
                        file=str(entry_file.relative_to(backend_path)),
                        suggestion=f"Verify '{name}' is defined in {target_file.name} or re-exported via __init__.py",
                    ))
        return issues

    def _check_copy_paths(self, dockerfile: str, repo_path: Path) -> tuple[list[ValidationIssue], dict[str, str]]:
        issues: list[ValidationIssue] = []
        fixes: dict[str, str] = {}
        for line in dockerfile.splitlines():
            stripped = line.strip()
            if not stripped.upper().startswith("COPY"):
                continue
            if "--from=" in stripped.lower():
                continue
            rest = re.sub(r'^COPY\s+', '', stripped, flags=re.IGNORECASE)
            rest = re.sub(r'--\w+=\S+\s+', '', rest)
            parts = rest.split()
            if len(parts) < 2:
                continue
            sources = parts[:-1]
            for src in sources:
                if src.startswith("$") or src.startswith("{"):
                    continue
                src_path = repo_path / src
                if "*" in src or "?" in src or "[" in src:
                    matches = list(repo_path.glob(src))
                    if not matches:
                        issues.append(ValidationIssue(
                            severity="error", check="copy_path",
                            message=f"COPY source pattern '{src}' matches no files in repo",
                            file="Dockerfile",
                            suggestion=f"Verify the glob pattern '{src}' is correct",
                        ))
                elif not src_path.exists():
                    issues.append(ValidationIssue(
                        severity="error", check="copy_path",
                        message=f"COPY source '{src}' not found in repo at {src_path}",
                        file="Dockerfile",
                        suggestion=f"Verify '{src}' exists or fix the path",
                    ))
        return issues, fixes

    def _find_framework_entry(self, search_dir: Path, pattern: str) -> Path | None:
        best: Path | None = None
        best_score = -1
        for py_file in search_dir.rglob("*.py"):
            rel = str(py_file.relative_to(search_dir))
            if any(skip in rel for skip in ("test", "migration", "alembic", "__pycache__")):
                continue
            try:
                content = py_file.read_text(errors="ignore")
            except OSError:
                continue
            if pattern not in content:
                continue
            score = 0
            if "include_router(" in content:
                score += 2
            if "add_middleware(" in content:
                score += 1
            if py_file.name == "main.py":
                score += 1
            if score > best_score:
                best_score = score
                best = py_file
        return best
