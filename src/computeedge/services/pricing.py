"""PricingService: compares cloud provider costs for given resource requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from computeedge.models import (
    ComparisonResult,
    CostBreakdown,
    ProviderRecommendation,
    ResourceEstimate,
)
from computeedge.utils.logger import get_logger

logger = get_logger("pricing")

# Active hours per month by traffic tier
_ACTIVE_HOURS: dict[str, int] = {
    "low": 360,
    "medium": 720,
    "high": 720,
}

# Static provider metadata for pros/cons/best_for/deployment_method
PROVIDER_INFO: dict[str, dict[str, Any]] = {
    "railway": {
        "deployment_method": "git push / CLI",
        "pros": [
            "Zero-config deployments",
            "Built-in managed Postgres and Redis",
            "Simple git-push workflow",
        ],
        "cons": [
            "Usage-based billing can be unpredictable",
            "Credit system may cause shutdowns",
            "Less control over infrastructure",
        ],
        "best_for": "Prototypes and small apps with simple deployment needs",
    },
    "hetzner": {
        "deployment_method": "SSH / Docker Compose",
        "pros": [
            "Extremely low cost",
            "Dedicated resources",
            "No managed DB overhead",
        ],
        "cons": [
            "Manual server setup required",
            "No managed database — use Docker Compose",
            "More operational overhead",
        ],
        "best_for": "Cost-sensitive projects comfortable with server management",
    },
    "digitalocean": {
        "deployment_method": "Droplet / App Platform",
        "pros": [
            "Managed Postgres available",
            "Good documentation and UI",
            "Reliable infrastructure",
        ],
        "cons": [
            "Managed database adds significant cost",
            "More expensive than Hetzner",
            "Egress fees after 1TB",
        ],
        "best_for": "Teams wanting managed database with simple setup",
    },
}


@dataclass
class _CostResult:
    total: float
    breakdown: CostBreakdown


class PricingService:
    """Compares provider costs for given resource requirements."""

    def __init__(self, providers_config: dict[str, Any]) -> None:
        self._config = providers_config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(self, requirements: ResourceEstimate) -> ComparisonResult:
        """Return a ComparisonResult with sorted provider recommendations."""
        recommendations: list[ProviderRecommendation] = []

        for provider_name, provider_data in self._config.items():
            if provider_name == "aws_equivalents":
                continue
            rec = self._match_plan(provider_name, provider_data, requirements)
            if rec is not None:
                recommendations.append(rec)

        # Sort by monthly cost ascending
        recommendations.sort(key=lambda r: r.monthly_cost)

        if not recommendations:
            return ComparisonResult(
                recommendations=[],
                top_pick="",
                top_pick_reason="No providers found matching requirements",
                savings_vs_aws="",
            )

        top = recommendations[0]
        top_pick_reason = self._generate_top_pick_reason(top, requirements)
        savings_vs_aws = self._calculate_aws_savings(top, requirements)

        return ComparisonResult(
            recommendations=recommendations,
            top_pick=top.provider,
            top_pick_reason=top_pick_reason,
            savings_vs_aws=savings_vs_aws,
        )

    # ------------------------------------------------------------------
    # Plan matching
    # ------------------------------------------------------------------

    def _match_plan(
        self,
        provider_name: str,
        provider_data: dict[str, Any],
        requirements: ResourceEstimate,
    ) -> ProviderRecommendation | None:
        """Find the cheapest plan for this provider that meets requirements."""
        plans: dict[str, Any] = provider_data.get("plans", {})
        best: tuple[float, str, _CostResult] | None = None

        for plan_name, plan_data in plans.items():
            # Check resource requirements
            if plan_data.get("ram_mb", 0) < requirements.ram_mb:
                continue
            if plan_data.get("cpu_vcpu", 0) < requirements.cpu_vcpu:
                continue
            if plan_data.get("storage_gb", 0) < requirements.storage_gb:
                continue

            # Calculate cost
            if "monthly_base" in plan_data:
                cost_result = self._calculate_railway_cost(
                    plan_data, requirements
                )
            else:
                cost_result = self._calculate_fixed_cost(
                    provider_name, plan_data, requirements
                )

            if best is None or cost_result.total < best[0]:
                best = (cost_result.total, plan_name, cost_result)

        if best is None:
            return None

        total_cost, plan_name, cost_result = best
        info = PROVIDER_INFO.get(provider_name, {})

        return ProviderRecommendation(
            provider=provider_name,
            plan=plan_name,
            monthly_cost=round(total_cost, 2),
            breakdown=cost_result.breakdown,
            deployment_method=info.get("deployment_method", ""),
            pros=list(info.get("pros", [])),
            cons=list(info.get("cons", [])),
            best_for=info.get("best_for", ""),
        )

    # ------------------------------------------------------------------
    # Cost calculation
    # ------------------------------------------------------------------

    def _calculate_railway_cost(
        self,
        plan_data: dict[str, Any],
        requirements: ResourceEstimate,
    ) -> _CostResult:
        """Railway usage-based cost: base_fee + cpu * rate + memory * rate."""
        base_fee = float(plan_data.get("monthly_base", 0))
        active_hours = _ACTIVE_HOURS.get(requirements.traffic_tier, 360)
        cpu_rate = float(plan_data.get("compute_per_vcpu_hour", 0))
        mem_rate = float(plan_data.get("memory_per_gb_hour", 0))

        compute = requirements.cpu_vcpu * cpu_rate * active_hours
        ram_gb = requirements.ram_mb / 1024.0
        memory_cost = ram_gb * mem_rate * active_hours

        # Railway has built-in managed Postgres — no extra DB cost
        db_cost = 0.0

        total = base_fee + compute + memory_cost + db_cost

        breakdown = CostBreakdown(
            compute=round(compute + memory_cost, 4),
            database=round(db_cost, 4),
            storage=0.0,
            bandwidth=0.0,
            base_fee=round(base_fee, 4),
        )
        return _CostResult(total=total, breakdown=breakdown)

    def _calculate_fixed_cost(
        self,
        provider_name: str,
        plan_data: dict[str, Any],
        requirements: ResourceEstimate,
    ) -> _CostResult:
        """Fixed monthly cost for Hetzner / DigitalOcean."""
        compute = float(plan_data.get("monthly", 0))
        db_cost = 0.0

        if requirements.needs_database:
            has_managed_postgres = plan_data.get("managed_postgres", False)
            if has_managed_postgres:
                db_cost = float(
                    plan_data.get("managed_postgres_monthly", 0)
                )
            # If no managed_postgres (e.g. Hetzner), db_cost stays 0

        total = compute + db_cost

        breakdown = CostBreakdown(
            compute=round(compute, 4),
            database=round(db_cost, 4),
            storage=0.0,
            bandwidth=0.0,
            base_fee=0.0,
        )
        return _CostResult(total=total, breakdown=breakdown)

    # ------------------------------------------------------------------
    # Output text generation
    # ------------------------------------------------------------------

    def _generate_top_pick_reason(
        self,
        top: ProviderRecommendation,
        requirements: ResourceEstimate,
    ) -> str:
        info = PROVIDER_INFO.get(top.provider, {})
        best_for = info.get("best_for", "")
        return (
            f"{top.provider.title()} is the most cost-effective option at "
            f"${top.monthly_cost:.2f}/month. {best_for}."
        )

    def _calculate_aws_savings(
        self,
        top: ProviderRecommendation,
        requirements: ResourceEstimate,
    ) -> str:
        aws_equivalents: dict[str, Any] = self._config.get(
            "aws_equivalents", {}
        )
        tier_data = aws_equivalents.get(requirements.traffic_tier, {})
        aws_total = float(tier_data.get("total", 0))

        if aws_total <= 0:
            return ""

        savings = aws_total - top.monthly_cost
        if savings > 0:
            pct = (savings / aws_total) * 100
            return (
                f"Save ${savings:.2f}/month ({pct:.0f}%) vs equivalent AWS setup"
            )
        return f"AWS equivalent costs ${aws_total:.2f}/month"
