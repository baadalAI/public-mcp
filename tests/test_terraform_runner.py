import json

import pytest

from computeedge.exceptions import DeploymentError
from computeedge.services.infra.terraform_runner import TerraformRunner


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self._stdout = stdout.encode()
        self._stderr = stderr.encode()

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_runner_apply_writes_tfvars_and_parses_outputs(tmp_path, monkeypatch):
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "main.tf").write_text('terraform {}')

    calls = []
    responses = [
        FakeProc(stdout=""),
        FakeProc(stdout=""),
        FakeProc(stdout=json.dumps({
            "server_id": {"value": 123},
            "public_ipv4": {"value": "1.2.3.4"},
        })),
    ]

    async def fake_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return responses.pop(0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    runner = TerraformRunner(
        binary="terraform",
        workspace_root=tmp_path / "workspace",
        template_dir=template_dir,
    )
    workspace = runner.prepare_workspace("dep-123", {"server_name": "dep-123"})
    outputs = await runner.apply(workspace)

    assert (workspace / "main.tf").exists()
    assert json.loads((workspace / "terraform.tfvars.json").read_text())["server_name"] == "dep-123"
    assert outputs["server_id"] == 123
    assert outputs["public_ipv4"] == "1.2.3.4"
    assert [call[0][1] for call in calls] == ["init", "apply", "output"]


@pytest.mark.asyncio
async def test_runner_destroy_removes_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace" / "dep"
    workspace.mkdir(parents=True)
    (workspace / "main.tf").write_text("terraform {}")

    async def fake_exec(*cmd, **kwargs):
        return FakeProc(stdout="")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    runner = TerraformRunner(
        binary="terraform",
        workspace_root=tmp_path / "workspace",
        template_dir=tmp_path / "workspace",
    )
    await runner.destroy(workspace)
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_runner_refresh_runs_refresh_only_apply(tmp_path, monkeypatch):
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "main.tf").write_text('terraform {}')

    calls = []
    responses = [
        FakeProc(stdout=""),
        FakeProc(stdout=""),
        FakeProc(stdout=json.dumps({"server_id": {"value": 123}})),
    ]

    async def fake_exec(*cmd, **kwargs):
        calls.append(cmd[1])
        return responses.pop(0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    runner = TerraformRunner(
        binary="terraform",
        workspace_root=tmp_path / "workspace",
        template_dir=template_dir,
    )
    workspace = runner.prepare_workspace("dep-123", {"server_name": "dep-123"})
    outputs = await runner.refresh(workspace)

    assert outputs["server_id"] == 123
    assert calls == ["init", "apply", "output"]


@pytest.mark.asyncio
async def test_runner_missing_binary_raises(tmp_path, monkeypatch):
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "main.tf").write_text('terraform {}')

    async def fake_exec(*cmd, **kwargs):
        raise FileNotFoundError("terraform")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    runner = TerraformRunner(
        binary="terraform",
        workspace_root=tmp_path / "workspace",
        template_dir=template_dir,
    )
    workspace = runner.prepare_workspace("dep-123", {"server_name": "dep-123"})

    with pytest.raises(DeploymentError, match="Terraform binary not found"):
        await runner.apply(workspace)
