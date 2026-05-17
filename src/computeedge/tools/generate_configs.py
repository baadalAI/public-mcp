import shutil
from dataclasses import asdict
from pathlib import Path

from computeedge.exceptions import ComputeEdgeError
from computeedge.services.analysis import AnalysisService
from computeedge.services.deployment import DeploymentService
from computeedge.services.validation import ConfigValidator
from computeedge.utils.git import clone_repo, is_git_url


def make_generate_configs_tool(
    analysis_service: AnalysisService,
    deployment_service: DeploymentService,
    validator: ConfigValidator,
):
    async def generate_configs(repo_path: str, env_vars: dict | None = None, topology: str | None = None) -> dict:
        """Analyze a repo and generate deployment configs.

        Returns analysis + rendered Dockerfiles, docker-compose, nginx, .env
        so the LLM can review and augment before deploying.

        Args:
            topology: Override auto-detected topology. One of "single", "split",
                      "frontend_only", "backend_only". If None, uses auto-detection.
        """
        cleanup = None
        try:
            if is_git_url(repo_path):
                repo_path = str(await clone_repo(repo_path))
                cleanup = repo_path

            analysis = await analysis_service.analyze(repo_path)

            # Override topology if the LLM knows better
            if topology is not None:
                valid = {"single", "split", "frontend_only", "backend_only"}
                if topology not in valid:
                    return {"error": f"Invalid topology '{topology}'. Must be one of: {', '.join(sorted(valid))}"}
                analysis.stack.deploy_topology = topology

            rendered = deployment_service.render_configs(analysis, repo_path, env_vars)

            # Extract metadata (prefixed with _) from rendered configs
            config_notes = rendered.pop("_config_notes", [])
            rendered.pop("_templates_used", None)

            # Validate and auto-fix configs
            validation = validator.validate(rendered, analysis, repo_path, auto_fix=True)
            rendered = validation.corrected_configs

            # Port consistency check
            port_issues = deployment_service._validate_port_consistency(analysis, rendered)
            validation_issues = [asdict(issue) for issue in validation.issues + port_issues]

            result = {
                "analysis": asdict(analysis),
                "configs": rendered,
                "config_notes": config_notes,
                "validation_issues": validation_issues,
            }
            if validation.corrections_applied:
                result["corrections_applied"] = validation.corrections_applied
            return result

        except ComputeEdgeError as e:
            return {"error": str(e)}
        finally:
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)

    return generate_configs
