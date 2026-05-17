"""Integration tests that hit the real Hetzner API.

Requires COMPUTEEDGE_HETZNER_TOKEN env var.
Run with: uv run pytest tests/ -v -m integration
"""
import os

import httpx
import pytest

from computeedge.providers.hetzner import HetznerProvider

pytestmark = pytest.mark.integration


@pytest.fixture
def hetzner_token():
    token = os.environ.get("COMPUTEEDGE_HETZNER_TOKEN")
    if not token:
        pytest.skip("COMPUTEEDGE_HETZNER_TOKEN not set")
    return token


@pytest.fixture
async def provider(hetzner_token):
    client = httpx.AsyncClient()
    yield HetznerProvider(api_token=hetzner_token, http_client=client)
    await client.aclose()


@pytest.mark.asyncio
async def test_create_and_delete_server(provider):
    """Create a cx22 server, verify it starts, then delete it."""
    import asyncssh
    key = asyncssh.generate_private_key("ssh-ed25519")
    pub_key = key.export_public_key().decode()
    ssh_key_id = await provider.upload_ssh_key("integration-test", pub_key)

    server = None
    try:
        # Create server
        server = await provider.create_server(
            "ce-integration-test", "cx22", ssh_key_id=ssh_key_id
        )
        assert server["id"] > 0
        assert server["status"] in ("initializing", "running")

        # Wait for ready
        ip = await provider.wait_for_ready(server["id"], timeout=120)
        assert ip  # should be a non-empty IP

    finally:
        # Always cleanup
        if server:
            await provider.delete_server(server["id"])
