import shutil
from dataclasses import asdict

from computeedge.exceptions import ComputeEdgeError
from computeedge.models import TrafficTier
from computeedge.services.analysis import AnalysisService
from computeedge.utils.git import clone_repo, is_git_url


def make_estimate_tool(analysis_service: AnalysisService):
    async def estimate_resources(
        repo_path: str, expected_traffic: str = "low"
    ) -> dict:
        """Estimate CPU, RAM, and storage requirements for deploying this app."""
        cleanup = None
        try:
            if is_git_url(repo_path):
                repo_path = str(await clone_repo(repo_path))
                cleanup = repo_path

            traffic = TrafficTier(expected_traffic)
            analysis = await analysis_service.analyze(repo_path)
            estimate = analysis_service.estimate_resources(analysis, traffic)
            return asdict(estimate)

        except (ComputeEdgeError, ValueError) as e:
            return {"error": str(e)}
        finally:
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)

    return estimate_resources
