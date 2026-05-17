import pytest

from computeedge.config.loader import load_bundled_yaml
from computeedge.models import (
    ComparisonResult,
    CostBreakdown,
    ResourceEstimate,
)
from computeedge.services.pricing import PricingService


@pytest.fixture
def pricing_service():
    providers_config = load_bundled_yaml("providers.yaml")
    return PricingService(providers_config)


def _low_traffic_with_db():
    return ResourceEstimate(
        ram_mb=1024,
        cpu_vcpu=1,
        storage_gb=10,
        needs_database=True,
        database_type="postgres",
        database_ram_mb=256,
        traffic_tier="low",
        notes="",
    )


def _low_traffic_frontend_only():
    return ResourceEstimate(
        ram_mb=256,
        cpu_vcpu=0.5,
        storage_gb=1,
        needs_database=False,
        database_type=None,
        database_ram_mb=0,
        traffic_tier="low",
        notes="",
    )


def test_compare_returns_comparison_result(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    assert isinstance(result, ComparisonResult)
    assert len(result.recommendations) > 0
    assert result.top_pick != ""
    assert result.top_pick_reason != ""
    assert result.savings_vs_aws != ""


def test_compare_sorted_by_cost(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    costs = [r.monthly_cost for r in result.recommendations]
    assert costs == sorted(costs)


def test_compare_hetzner_cheapest_for_low_traffic(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    assert result.top_pick == "hetzner"
    assert result.recommendations[0].provider == "hetzner"


def test_compare_cost_breakdown_fields(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    for rec in result.recommendations:
        assert isinstance(rec.breakdown, CostBreakdown)
        assert rec.monthly_cost > 0
        assert rec.breakdown.compute >= 0


def test_compare_hetzner_no_managed_db(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    hetzner = next(r for r in result.recommendations if r.provider == "hetzner")
    assert hetzner.breakdown.database == 0


def test_compare_digitalocean_includes_managed_db(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    do = next(
        r for r in result.recommendations if r.provider == "digitalocean"
    )
    assert do.breakdown.database > 0


def test_compare_frontend_only(pricing_service):
    result = pricing_service.compare(_low_traffic_frontend_only())
    assert len(result.recommendations) > 0
    for rec in result.recommendations:
        assert rec.breakdown.database == 0


def test_compare_railway_includes_base_fee(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    railway = next(
        r for r in result.recommendations if r.provider == "railway"
    )
    assert railway.breakdown.base_fee > 0


def test_compare_savings_vs_aws(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    assert "save" in result.savings_vs_aws.lower() or "%" in result.savings_vs_aws


def test_compare_deployment_methods(pricing_service):
    result = pricing_service.compare(_low_traffic_with_db())
    for rec in result.recommendations:
        assert rec.deployment_method != ""
        assert len(rec.pros) > 0
        assert len(rec.cons) > 0
        assert rec.best_for != ""


def test_compare_no_matching_plans(pricing_service):
    """When resource requirements exceed all plans, return empty recommendations."""
    huge = ResourceEstimate(
        ram_mb=128000,
        cpu_vcpu=64,
        storage_gb=10000,
        needs_database=False,
        database_type=None,
        database_ram_mb=0,
        traffic_tier="high",
        notes="",
    )
    result = pricing_service.compare(huge)
    assert result.recommendations == []
    assert result.top_pick == ""
    assert "No providers" in result.top_pick_reason


def test_compare_hetzner_recommends_cx23(pricing_service):
    """cx23 should be the recommended Hetzner plan (cheapest non-deprecated)."""
    result = pricing_service.compare(_low_traffic_with_db())
    hetzner = next(r for r in result.recommendations if r.provider == "hetzner")
    assert hetzner.plan == "cx23"
    assert hetzner.monthly_cost == 3.49
