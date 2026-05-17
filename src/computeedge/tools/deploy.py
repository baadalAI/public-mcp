import shutil
from dataclasses import asdict
from typing import Callable

from computeedge.exceptions import ComputeEdgeError
from computeedge.models import RepoAnalysis
from computeedge.services.analysis import AnalysisService
from computeedge.services.deployment import DeploymentService
from computeedge.utils.git import clone_repo, is_git_url


NANOCLAW_UPSTREAM_REPO = "https://github.com/qwibitai/nanoclaw.git"


def make_deploy_tool(
    analysis_service: AnalysisService,
    make_deployment_service: Callable[[str, str], DeploymentService],
    state_manager,
    config=None,
):
    async def deploy(
        provider_token: str,
        user_id: int,
        repo_path: str | None = None,
        stack: str | None = None,
        provider: str = "hetzner",
        plan: str | None = None,
        env_vars: dict | None = None,
        docker_configs: dict | None = None,
        force_new: bool = False,
        override_dockerfile: bool = True,
        retry_context: dict | None = None,
    ) -> dict:
        """Deploy the application to a cloud provider.

        For nanoclaw without a repo: pass stack='nanoclaw' and no repo_path.
        This clones the upstream nanoclaw repo and deploys it.
        """
        cleanup = None
        try:
            SUPPORTED_PROVIDERS = {"hetzner", "digitalocean"}
            if provider not in SUPPORTED_PROVIDERS:
                return {"error": f"Unsupported provider: {provider}. Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"}

            DEFAULT_PLANS = {"hetzner": "cx23", "digitalocean": "s-1vcpu-1gb"}
            if plan is None:
                plan = DEFAULT_PLANS.get(provider, "cx23")

            deployment_service = make_deployment_service(provider_token, provider)

            # Deserialize retry_context if provided
            parsed_retry_context = None
            if retry_context is not None:
                from computeedge.models import RetryContext
                parsed_retry_context = RetryContext.from_dict(retry_context)

            # Nanoclaw without a repo: clone upstream
            if repo_path is None:
                if stack == "nanoclaw":
                    repo_path = NANOCLAW_UPSTREAM_REPO
                else:
                    return {"error": "repo_path is required. Pass stack='nanoclaw' to deploy vanilla nanoclaw without a repo."}

            if is_git_url(repo_path):
                repo_path = str(await clone_repo(repo_path))
                cleanup = repo_path

            # Merge saved env vars (from set_credentials) with per-request ones
            if config is not None:
                saved = config.get_env_vars()
                if saved:
                    merged = dict(saved)
                    if env_vars:
                        merged.update(env_vars)  # per-request overrides saved
                    env_vars = merged

            # Auto-detect nanoclaw: explicit stack flag OR workspace directory structure
            is_nanoclaw = (
                stack == "nanoclaw"
                or DeploymentService._is_nanoclaw_workspace(repo_path)
            )
            if is_nanoclaw:
                from computeedge.models import StackInfo
                analysis = RepoAnalysis(
                    stack=StackInfo(backend="nanoclaw", backend_language="javascript"),
                    has_dockerfile=False,
                    estimated_complexity="medium",
                )
            else:
                analysis = await analysis_service.analyze(repo_path)

            # Skip pre-deploy check on retry
            if parsed_retry_context is None:
                check = await deployment_service.pre_deploy_check(
                    analysis, env_vars, repo_path, user_id, force_new=force_new
                )
                if not check.can_proceed:
                    return asdict(check)

            result = await deployment_service.deploy(
                repo_path, analysis, plan=plan, env_vars=env_vars,
                docker_configs=docker_configs,
                override_dockerfile=override_dockerfile,
                retry_context=parsed_retry_context,
                user_id=user_id,
            )
            return asdict(result)

        except ComputeEdgeError as e:
            # Check for enriched diagnostics
            if hasattr(e, "diagnostics") and e.diagnostics is not None:
                diag = e.diagnostics
                status = "failed_retryable" if diag.retry_context is not None else "failed"
                return {
                    "status": status,
                    "error": str(e),
                    "diagnostics": asdict(diag),
                }
            # Legacy fallback
            error_dict = {"status": "failed", "error": str(e)}
            if hasattr(e, "build_log") and e.build_log:
                error_dict["build_log"] = e.build_log
            if hasattr(e, "suggestion") and e.suggestion:
                error_dict["suggestion"] = e.suggestion
            if hasattr(e, "retry_hint") and e.retry_hint:
                error_dict["retry_hint"] = e.retry_hint
            return error_dict
        finally:
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)

    return deploy


def make_redeploy_tool(
    analysis_service: AnalysisService,
    make_deployment_service: Callable[[str, str], DeploymentService],
    state_manager,
    config=None,
):
    async def redeploy(
        deployment_id: str,
        provider_token: str,
        user_id: int,
        env_vars: dict | None = None,
    ) -> dict:
        """Update code on an existing deployment without creating a new server."""
        try:
            state = await state_manager.get(deployment_id, user_id)
            if state is None:
                return {"error": f"Deployment {deployment_id} not found."}
            provider = state.get("provider", "hetzner")
            deployment_service = make_deployment_service(provider_token, provider)

            repo_path = state.get("repo_path")
            if not repo_path:
                return {"error": f"No repo_path stored for deployment {deployment_id}."}

            cleanup = None
            if is_git_url(repo_path):
                repo_path = str(await clone_repo(repo_path))
                cleanup = repo_path

            try:
                # Merge saved env vars with per-request ones
                if config is not None:
                    saved = config.get_env_vars()
                    if saved:
                        merged = dict(saved)
                        if env_vars:
                            merged.update(env_vars)
                        env_vars = merged

                analysis = await analysis_service.analyze(repo_path)
                result = await deployment_service.redeploy(
                    deployment_id, repo_path, analysis, env_vars=env_vars,
                    user_id=user_id,
                )
                return asdict(result)
            finally:
                if cleanup:
                    shutil.rmtree(cleanup, ignore_errors=True)

        except ComputeEdgeError as e:
            error_dict = {"error": str(e)}
            if hasattr(e, "suggestion") and e.suggestion:
                error_dict["suggestion"] = e.suggestion
            return error_dict

    return redeploy
