# tests/test_retry_context.py
from dataclasses import asdict

from computeedge.models import RetryContext


def test_retry_context_defaults():
    ctx = RetryContext()
    assert ctx.attempt == 1
    assert ctx.max_retries == 3
    assert ctx.server_id is None
    assert ctx.previous_errors == []
    assert ctx.previous_fixes == []


def test_retry_context_round_trip():
    ctx = RetryContext(
        attempt=2, max_retries=3, server_id=12345,
        server_ip="1.2.3.4", deployment_id="ce-hetzner-abc12345",
        ssh_key_path="/tmp/key",
        previous_errors=["pg_config not found"],
        previous_fixes=[{"attempt": 1, "fix_description": "added libpq-dev"}],
    )
    d = asdict(ctx)
    restored = RetryContext.from_dict(d)
    assert restored.attempt == 2
    assert restored.server_id == 12345
    assert restored.previous_errors == ["pg_config not found"]


def test_retry_context_from_dict_coerces_types():
    d = {"attempt": "2", "server_id": "12345", "max_retries": "3"}
    ctx = RetryContext.from_dict(d)
    assert ctx.attempt == 2
    assert ctx.server_id == 12345
    assert ctx.max_retries == 3


def test_retry_context_from_dict_missing_keys():
    ctx = RetryContext.from_dict({"attempt": 2})
    assert ctx.attempt == 2
    assert ctx.max_retries == 3  # default
    assert ctx.server_id is None  # default


def test_retry_context_from_dict_ignores_unknown_keys():
    ctx = RetryContext.from_dict({"attempt": 1, "unknown_field": "value"})
    assert ctx.attempt == 1
    assert not hasattr(ctx, "unknown_field")
