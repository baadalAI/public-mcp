import shutil
from dataclasses import asdict

from computeedge.exceptions import ComputeEdgeError
from computeedge.models import ResourceEstimate, TrafficTier
from computeedge.services.analysis import AnalysisService
from computeedge.services.pricing import PricingService
from computeedge.utils.git import clone_repo, is_git_url


def make_compare_tool(
    analysis_service: AnalysisService,
    pricing_service: PricingService,
):
    async def compare_providers(
        repo_path: str | None = None,
        stack: str | None = None,
        estimated_ram_mb: int = 512,
        estimated_cpu_vcpu: float = 1,
        estimated_storage_gb: int = 10,
        needs_database: bool = False,
        database_type: str = "postgres",
        expected_traffic: str = "low",
    ) -> dict:
        """Compare cloud provider pricing for deploying this app.
        Returns ranked recommendations with cost breakdowns."""
        cleanup = None
        try:
            if repo_path and stack is None:
                if is_git_url(repo_path):
                    repo_path = str(await clone_repo(repo_path))
                    cleanup = repo_path

                traffic = TrafficTier(expected_traffic)
                analysis = await analysis_service.analyze(repo_path)
                estimate = analysis_service.estimate_resources(
                    analysis, traffic
                )
            else:
                estimate = ResourceEstimate(
                    ram_mb=estimated_ram_mb,
                    cpu_vcpu=estimated_cpu_vcpu,
                    storage_gb=estimated_storage_gb,
                    needs_database=needs_database,
                    database_type=database_type if needs_database else None,
                    database_ram_mb=256 if needs_database else 0,
                    traffic_tier=expected_traffic,
                    notes="",
                )

            comparison = pricing_service.compare(estimate)
            return asdict(comparison)

        except (ComputeEdgeError, ValueError) as e:
            return {"error": str(e)}
        finally:
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)

    return compare_providers
