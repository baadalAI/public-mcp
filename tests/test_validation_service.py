import pytest
from pathlib import Path
from computeedge.services.validation import ConfigValidator
from computeedge.models import RepoAnalysis, StackInfo, ValidationIssue, ValidationResult

FIXTURES = Path(__file__).parent / "fixtures"


def test_validation_result_valid_when_no_errors():
    result = ValidationResult(
        valid=True,
        issues=[],
        corrected_configs={"Dockerfile": "FROM python:3.11"},
        corrections_applied=[],
    )
    assert result.valid is True
    assert result.issues == []


def test_validation_result_invalid_when_errors():
    issue = ValidationIssue(
        severity="error",
        check="dep_file",
        message="Missing requirements.txt",
    )
    result = ValidationResult(
        valid=False,
        issues=[issue],
        corrected_configs={},
        corrections_applied=[],
    )
    assert result.valid is False
    assert len(result.issues) == 1
    assert result.issues[0].check == "dep_file"


def test_validation_result_valid_with_warnings_only():
    issue = ValidationIssue(
        severity="warning",
        check="symbol_existence",
        message="def ping_db not found in database.py",
    )
    result = ValidationResult(
        valid=True,
        issues=[issue],
        corrected_configs={"Dockerfile": "FROM python:3.11"},
        corrections_applied=[],
    )
    assert result.valid is True
    assert len(result.issues) == 1


class TestCheckDependencyFiles:
    def setup_method(self):
        self.validator = ConfigValidator()

    def test_python_requirements_txt_exists(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY requirements.txt .\nRUN pip install -r requirements.txt"}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_fastapi")
        dep_errors = [i for i in result.issues if i.check == "dep_file" and i.severity == "error"]
        assert len(dep_errors) == 0

    def test_python_pyproject_referenced_but_only_requirements_exists(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY pyproject.toml .\nRUN pip install --no-cache-dir ."}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_missing_deps")
        assert "requirements.txt" in result.corrected_configs["Dockerfile"]
        assert "pyproject.toml" not in result.corrected_configs["Dockerfile"]
        assert len(result.corrections_applied) >= 1

    def test_python_no_deps_file_at_all(self, tmp_path):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY requirements.txt ."}
        result = self.validator.validate(configs, analysis, tmp_path)
        dep_errors = [i for i in result.issues if i.check == "dep_file" and i.severity == "error"]
        assert len(dep_errors) >= 1
        assert not result.valid

    def test_go_missing_go_sum(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/app\ngo 1.23\n")
        (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
        analysis = RepoAnalysis(stack=StackInfo(backend="go", backend_language="go"))
        configs = {"Dockerfile": "COPY go.mod go.sum ./"}
        result = self.validator.validate(configs, analysis, tmp_path)
        dep_errors = [i for i in result.issues if i.check == "dep_file" and i.severity == "error"]
        assert any("go.sum" in e.message for e in dep_errors)

    def test_node_missing_package_json(self, tmp_path):
        analysis = RepoAnalysis(stack=StackInfo(frontend="react", backend=None))
        configs = {"Dockerfile": "COPY package*.json ./"}
        result = self.validator.validate(configs, analysis, tmp_path)
        dep_errors = [i for i in result.issues if i.check == "dep_file" and i.severity == "error"]
        assert len(dep_errors) >= 1


class TestCheckEntryPoint:
    def setup_method(self):
        self.validator = ConfigValidator()

    def test_fastapi_correct_entry_point(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec uvicorn main:app' > /start.sh"}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_fastapi")
        entry_errors = [i for i in result.issues if i.check == "entry_point" and i.severity == "error"]
        assert len(entry_errors) == 0

    def test_fastapi_wrong_entry_point_auto_fixed(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec uvicorn main:app --host 0.0.0.0' > /start.sh"}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_wrong_entry")
        assert "app.main:app" in result.corrected_configs["Dockerfile"]
        assert "uvicorn main:app" not in result.corrected_configs["Dockerfile"]
        assert len(result.corrections_applied) >= 1

    def test_express_correct_entry_point(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="express", backend_language="javascript"))
        configs = {"Dockerfile": 'CMD ["node", "src/index.js"]'}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_express")
        entry_errors = [i for i in result.issues if i.check == "entry_point" and i.severity == "error"]
        assert len(entry_errors) == 0

    def test_django_wsgi_correct(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="django", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec gunicorn myproject.wsgi:application' > /start.sh"}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_django")
        entry_errors = [i for i in result.issues if i.check == "entry_point" and i.severity == "error"]
        assert len(entry_errors) == 0

    def test_django_wsgi_missing_auto_fixed(self, tmp_path):
        proj = tmp_path / "myproject"
        proj.mkdir()
        (tmp_path / "requirements.txt").write_text("django\ngunicorn\n")
        (tmp_path / "manage.py").write_text("")
        (proj / "wsgi.py").write_text("application = None\n")
        analysis = RepoAnalysis(stack=StackInfo(backend="django", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec gunicorn wrongname.wsgi:application' > /start.sh"}
        result = self.validator.validate(configs, analysis, tmp_path)
        assert "myproject.wsgi:application" in result.corrected_configs["Dockerfile"]

    def test_streamlit_correct(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="streamlit", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec streamlit run app.py' > /start.sh"}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_streamlit")
        entry_errors = [i for i in result.issues if i.check == "entry_point" and i.severity == "error"]
        assert len(entry_errors) == 0

    def test_streamlit_missing_auto_fixed(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("streamlit\n")
        (tmp_path / "app.py").write_text("import streamlit as st\n")
        analysis = RepoAnalysis(stack=StackInfo(backend="streamlit", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec streamlit run nonexistent.py' > /start.sh"}
        result = self.validator.validate(configs, analysis, tmp_path)
        assert "streamlit run app.py" in result.corrected_configs["Dockerfile"]


class TestCheckImports:
    def setup_method(self):
        self.validator = ConfigValidator()

    def test_all_imports_resolve(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec uvicorn main:app' > /start.sh"}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_fastapi")
        import_errors = [i for i in result.issues if i.check == "import_resolution"]
        assert len(import_errors) == 0

    def test_missing_first_party_import_flagged(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec uvicorn app.main:app' > /start.sh"}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_missing_import")
        import_errors = [i for i in result.issues if i.check == "import_resolution" and i.severity == "error"]
        assert len(import_errors) >= 2
        messages = [e.message for e in import_errors]
        assert any("settings" in m for m in messages)
        assert any("database" in m for m in messages)

    def test_stdlib_and_pip_imports_not_flagged(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec uvicorn main:app' > /start.sh"}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_fastapi")
        import_errors = [i for i in result.issues if i.check == "import_resolution"]
        assert len(import_errors) == 0


class TestCheckSymbols:
    def setup_method(self):
        self.validator = ConfigValidator()

    def test_missing_symbol_is_warning(self, tmp_path):
        pkg = tmp_path / "app"
        pkg.mkdir()
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        (pkg / "__init__.py").touch()
        (pkg / "main.py").write_text("from app.database import get_db, ping_db\nfrom fastapi import FastAPI\napp = FastAPI()\n")
        (pkg / "database.py").write_text("def get_db():\n    pass\n")
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec uvicorn app.main:app' > /start.sh"}
        result = self.validator.validate(configs, analysis, tmp_path)
        sym_warnings = [i for i in result.issues if i.check == "symbol_existence" and i.severity == "warning"]
        assert any("ping_db" in w.message for w in sym_warnings)
        assert result.valid

    def test_all_symbols_found_no_warning(self, tmp_path):
        pkg = tmp_path / "app"
        pkg.mkdir()
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        (pkg / "__init__.py").touch()
        (pkg / "main.py").write_text("from app.database import get_db\nfrom fastapi import FastAPI\napp = FastAPI()\n")
        (pkg / "database.py").write_text("def get_db():\n    pass\n")
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "RUN printf '#!/bin/sh\\nexec uvicorn app.main:app' > /start.sh"}
        result = self.validator.validate(configs, analysis, tmp_path)
        sym_warnings = [i for i in result.issues if i.check == "symbol_existence"]
        assert len(sym_warnings) == 0


class TestCheckCopyPaths:
    def setup_method(self):
        self.validator = ConfigValidator()

    def test_valid_copy_paths(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY requirements.txt .\nCOPY main.py ."}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_fastapi")
        copy_errors = [i for i in result.issues if i.check == "copy_path"]
        assert len(copy_errors) == 0

    def test_missing_copy_path_flagged(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "FROM python:3.11\nCOPY nonexistent_dir/ .\nCOPY requirements.txt ."}
        result = self.validator.validate(configs, analysis, tmp_path)
        copy_errors = [i for i in result.issues if i.check == "copy_path" and i.severity == "error"]
        assert any("nonexistent_dir" in e.message for e in copy_errors)

    def test_multistage_from_copy_not_checked(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY --from=frontend-builder /build/dist /var/www/html\nCOPY requirements.txt ."}
        result = self.validator.validate(configs, analysis, tmp_path)
        copy_errors = [i for i in result.issues if i.check == "copy_path"]
        assert len(copy_errors) == 0

    def test_glob_copy_path_at_least_one_match(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY requirements*.txt ."}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_fastapi")
        copy_errors = [i for i in result.issues if i.check == "copy_path"]
        assert len(copy_errors) == 0


class TestGenerateConfigsIntegration:
    def setup_method(self):
        self.validator = ConfigValidator()

    def test_corrections_applied_in_response(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY pyproject.toml .\nRUN pip install --no-cache-dir ."}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_missing_deps", auto_fix=True)
        assert len(result.corrections_applied) >= 1
        assert "requirements.txt" in result.corrected_configs["Dockerfile"]

    def test_deploy_mode_does_not_autofix(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY pyproject.toml .\nRUN pip install --no-cache-dir ."}
        result = self.validator.validate(configs, analysis, FIXTURES / "sample_missing_deps", auto_fix=False)
        assert not result.valid
        dep_errors = [i for i in result.issues if i.check == "dep_file"]
        assert len(dep_errors) >= 1
        assert "pyproject.toml" in result.corrected_configs["Dockerfile"]
        assert len(result.corrections_applied) == 0


class TestDeployHardGate:
    def setup_method(self):
        self.validator = ConfigValidator()

    def test_deploy_blocks_on_validation_error(self, tmp_path):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY requirements.txt ."}
        result = self.validator.validate(configs, analysis, tmp_path, auto_fix=False)
        assert not result.valid
        assert any(i.severity == "error" for i in result.issues)

    def test_corrected_configs_pass_deploy_validation(self):
        analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
        configs = {"Dockerfile": "COPY pyproject.toml .\nRUN pip install --no-cache-dir ."}
        gen_result = self.validator.validate(configs, analysis, FIXTURES / "sample_missing_deps", auto_fix=True)
        assert "requirements.txt" in gen_result.corrected_configs["Dockerfile"]
        deploy_result = self.validator.validate(
            gen_result.corrected_configs, analysis, FIXTURES / "sample_missing_deps", auto_fix=False,
        )
        dep_errors = [i for i in deploy_result.issues if i.check == "dep_file" and i.severity == "error"]
        assert len(dep_errors) == 0


class TestExistingFixturesPass:
    """All existing valid fixtures should pass validation cleanly."""

    def setup_method(self):
        self.validator = ConfigValidator()

    @pytest.mark.parametrize("fixture_name", [
        "sample_fastapi",
        "sample_express",
        "sample_go",
    ])
    def test_valid_fixture_passes(self, fixture_name):
        fixture_path = FIXTURES / fixture_name
        if fixture_name == "sample_fastapi":
            analysis = RepoAnalysis(stack=StackInfo(backend="fastapi", backend_language="python"))
            configs = {"Dockerfile": "COPY requirements.txt .\nRUN pip install -r requirements.txt\nRUN printf '#!/bin/sh\\nexec uvicorn main:app' > /start.sh"}
        elif fixture_name == "sample_express":
            analysis = RepoAnalysis(stack=StackInfo(backend="express", backend_language="javascript"))
            configs = {"Dockerfile": 'COPY package*.json ./\nCMD ["node", "src/index.js"]'}
        elif fixture_name == "sample_go":
            analysis = RepoAnalysis(stack=StackInfo(backend="go", backend_language="go"))
            configs = {"Dockerfile": "COPY go.mod go.sum ./\nCOPY main.go ."}
        else:
            pytest.skip(f"No config template for {fixture_name}")

        result = self.validator.validate(configs, analysis, fixture_path)
        errors = [i for i in result.issues if i.severity == "error"]
        assert len(errors) == 0, f"Unexpected errors for {fixture_name}: {[e.message for e in errors]}"
