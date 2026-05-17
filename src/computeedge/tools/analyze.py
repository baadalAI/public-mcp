import shutil
from dataclasses import asdict
from pathlib import Path

from computeedge.exceptions import ComputeEdgeError
from computeedge.services.analysis import AnalysisService
from computeedge.utils.git import clone_repo, is_git_url


def make_analyze_tool(analysis_service: AnalysisService):
    async def analyze_repo(repo_path: str) -> dict:
        """Analyze a local or remote git repository and detect the tech stack,
        database, and services used."""
        cleanup = None
        try:
            if is_git_url(repo_path):
                repo_path = str(await clone_repo(repo_path))
                cleanup = repo_path

            analysis = await analysis_service.analyze(repo_path)
            return asdict(analysis)

        except ComputeEdgeError as e:
            return {"error": str(e)}
        finally:
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)

    return analyze_repo
