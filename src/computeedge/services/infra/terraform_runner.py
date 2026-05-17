import asyncio
import json
import os
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from computeedge.exceptions import DeploymentError
from computeedge.utils.logger import get_logger

logger = get_logger("infra.terraform_runner")


class TerraformRunner:
    """Prepare a Terraform workspace and execute terraform commands."""

    def __init__(
        self,
        binary: str = "terraform",
        workspace_root: Path | None = None,
        template_dir: Path | None = None,
    ):
        self._binary = binary
        self._workspace_root = workspace_root or (Path.home() / ".computeedge" / "terraform")
        if template_dir is None:
            template_dir = Path(
                resources.files("computeedge").joinpath("terraform", "hetzner_vm")
            )
        self._template_dir = Path(template_dir)

    def prepare_workspace(self, deployment_id: str, variables: dict[str, Any]) -> Path:
        workspace = self._workspace_root / deployment_id
        workspace.mkdir(parents=True, exist_ok=True)
        self._sync_template(workspace)
        self._write_tfvars(workspace, variables)
        return workspace

    async def apply(self, workspace: Path) -> dict[str, Any]:
        await self._run(["init", "-input=false", "-no-color"], workspace)
        await self._run(["apply", "-auto-approve", "-input=false", "-no-color"], workspace)
        return await self.outputs(workspace)

    async def import_resource(self, workspace: Path, address: str, resource_id: str) -> None:
        await self._run(["import", "-input=false", "-no-color", address, resource_id], workspace)

    async def destroy(self, workspace: Path) -> None:
        if not workspace.exists():
            return
        await self._run(["init", "-input=false", "-no-color"], workspace)
        await self._run(["destroy", "-auto-approve", "-input=false", "-no-color"], workspace)
        shutil.rmtree(workspace, ignore_errors=True)

    async def outputs(self, workspace: Path) -> dict[str, Any]:
        output = await self._run(["output", "-json"], workspace)
        return self._parse_outputs(output)

    async def plan(self, workspace: Path) -> tuple[bool, str]:
        await self._run(["init", "-input=false", "-no-color"], workspace)
        stdout_text, stderr_text, returncode = await self._run_with_returncode(
            ["plan", "-detailed-exitcode", "-input=false", "-no-color"],
            workspace,
            allowed_returncodes={0, 2},
        )
        output = "\n".join(part for part in [stdout_text.strip(), stderr_text.strip()] if part).strip()
        return returncode == 2, output

    async def refresh(self, workspace: Path) -> dict[str, Any]:
        await self._run(["init", "-input=false", "-no-color"], workspace)
        await self._run(
            ["apply", "-refresh-only", "-auto-approve", "-input=false", "-no-color"],
            workspace,
        )
        return await self.outputs(workspace)

    def _sync_template(self, workspace: Path) -> None:
        if not self._template_dir.exists():
            raise DeploymentError(
                f"Terraform template directory not found: {self._template_dir}"
            )
        for source in self._template_dir.iterdir():
            target = workspace / source.name
            if source.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)

    def _write_tfvars(self, workspace: Path, variables: dict[str, Any]) -> None:
        tfvars_path = workspace / "terraform.tfvars.json"
        tfvars_path.write_text(json.dumps(variables, indent=2, sort_keys=True))
        tfvars_path.chmod(0o600)

    async def _run(self, args: list[str], cwd: Path) -> str:
        stdout_text, stderr_text, _ = await self._run_with_returncode(args, cwd)
        if stderr_text and not stdout_text:
            return stderr_text
        return stdout_text

    async def _run_with_returncode(
        self,
        args: list[str],
        cwd: Path,
        allowed_returncodes: set[int] | None = None,
    ) -> tuple[str, str, int]:
        logger.info("Running terraform %s in %s", " ".join(args), cwd)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TF_IN_AUTOMATION": "1"},
            )
        except FileNotFoundError as exc:
            logger.error("Terraform binary not found: %s", self._binary)
            raise DeploymentError(
                f"Terraform binary not found: {self._binary}",
                suggestion="Install Terraform or set COMPUTEEDGE_TERRAFORM_BIN.",
            ) from exc

        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode()
        stderr_text = stderr.decode()
        allowed = allowed_returncodes or {0}
        if proc.returncode not in allowed:
            details = stderr_text.strip() or stdout_text.strip() or "unknown terraform error"
            logger.error("Terraform %s failed (exit %s): %s", " ".join(args), proc.returncode, details)
            raise DeploymentError(
                f"Terraform {' '.join(args)} failed: {details}",
                build_log=stdout_text + ("\n" + stderr_text if stderr_text else ""),
            )
        return stdout_text, stderr_text, proc.returncode

    def _parse_outputs(self, raw_output: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_output or "{}")
        except json.JSONDecodeError as exc:
            logger.error("Terraform returned invalid JSON output: %r", raw_output[:200])
            raise DeploymentError("Terraform returned invalid JSON output") from exc
        return {
            key: value.get("value")
            for key, value in parsed.items()
            if isinstance(value, dict) and "value" in value
        }
