import httpx
import pytest

from computeedge.exceptions import ProviderAPIError
from computeedge.providers.hetzner import HetznerProvider


@pytest.fixture
def mock_transport():
    return httpx.MockTransport(lambda request: httpx.Response(200, json={}))


@pytest.fixture
def provider(mock_transport):
    client = httpx.AsyncClient(transport=mock_transport)
    return HetznerProvider(api_token="test-token", http_client=client)


def make_provider_with_responses(responses: list[httpx.Response]):
    call_count = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if call_count < len(responses):
            resp = responses[call_count]
            call_count += 1
            return resp
        return httpx.Response(500, json={"error": {"message": "unexpected call"}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HetznerProvider(api_token="test-token", http_client=client)


@pytest.mark.asyncio
async def test_create_server():
    provider = make_provider_with_responses([
        httpx.Response(201, json={"server": {"id": 12345, "public_net": {"ipv4": {"ip": "1.2.3.4"}}, "status": "initializing"}}),
    ])
    result = await provider.create_server("test-srv", "cx22", ssh_key_id=1)
    assert result["id"] == 12345
    assert result["ip"] == "1.2.3.4"
    assert result["status"] == "initializing"


@pytest.mark.asyncio
async def test_create_server_api_error():
    provider = make_provider_with_responses([
        httpx.Response(422, json={"error": {"message": "invalid server type", "code": "invalid_input"}}),
    ])
    with pytest.raises(ProviderAPIError, match="invalid server type"):
        await provider.create_server("test-srv", "bad_type", ssh_key_id=1)


@pytest.mark.asyncio
async def test_delete_server():
    provider = make_provider_with_responses([httpx.Response(200, json={})])
    await provider.delete_server(12345)


@pytest.mark.asyncio
async def test_upload_ssh_key():
    provider = make_provider_with_responses([
        httpx.Response(201, json={"ssh_key": {"id": 42, "name": "computeedge-key"}}),
    ])
    key_id = await provider.upload_ssh_key("my-key", "ssh-ed25519 AAAA...")
    assert key_id == 42


@pytest.mark.asyncio
async def test_upload_ssh_key_already_exists():
    pub_key = "ssh-ed25519 AAAA..."
    provider = make_provider_with_responses([
        httpx.Response(409, json={"error": {"message": "SSH key with the same fingerprint already exists", "code": "uniqueness_error"}}),
        httpx.Response(200, json={"ssh_keys": [
            {"id": 99, "name": "existing-key", "public_key": pub_key},
        ]}),
    ])
    key_id = await provider.upload_ssh_key("my-key", pub_key)
    assert key_id == 99


@pytest.mark.asyncio
async def test_upload_ssh_key_already_exists_no_match():
    provider = make_provider_with_responses([
        httpx.Response(409, json={"error": {"message": "SSH key with the same fingerprint already exists", "code": "uniqueness_error"}}),
        httpx.Response(200, json={"ssh_keys": [
            {"id": 99, "name": "other-key", "public_key": "ssh-ed25519 DIFFERENTKEY"},
        ]}),
    ])
    with pytest.raises(ProviderAPIError, match="no matching key found"):
        await provider.upload_ssh_key("my-key", "ssh-ed25519 AAAA...")


@pytest.mark.asyncio
async def test_delete_ssh_key():
    provider = make_provider_with_responses([httpx.Response(204, json={})])
    await provider.delete_ssh_key(42)


@pytest.mark.asyncio
async def test_delete_ssh_key_already_gone():
    provider = make_provider_with_responses([httpx.Response(404, json={"error": {"message": "not found", "code": "not_found"}})])
    await provider.delete_ssh_key(42)  # should not raise


@pytest.mark.asyncio
async def test_get_server():
    provider = make_provider_with_responses([
        httpx.Response(200, json={"server": {"id": 12345, "public_net": {"ipv4": {"ip": "1.2.3.4"}}, "status": "running"}}),
    ])
    result = await provider.get_server(12345)
    assert result["status"] == "running"
    assert result["ip"] == "1.2.3.4"


@pytest.mark.asyncio
async def test_wait_for_ready():
    provider = make_provider_with_responses([
        httpx.Response(200, json={"server": {"id": 12345, "public_net": {"ipv4": {"ip": "1.2.3.4"}}, "status": "initializing"}}),
        httpx.Response(200, json={"server": {"id": 12345, "public_net": {"ipv4": {"ip": "1.2.3.4"}}, "status": "running"}}),
    ])
    ip = await provider.wait_for_ready(12345, timeout=10, poll_interval=0.1)
    assert ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_wait_for_ready_timeout():
    provider = make_provider_with_responses([
        httpx.Response(200, json={"server": {"id": 12345, "public_net": {"ipv4": {"ip": "1.2.3.4"}}, "status": "initializing"}}),
    ] * 50)
    with pytest.raises(ProviderAPIError, match="timed out"):
        await provider.wait_for_ready(12345, timeout=0.3, poll_interval=0.1)


@pytest.mark.asyncio
async def test_auth_header():
    captured_request = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"server": {"id": 1, "public_net": {"ipv4": {"ip": "0.0.0.0"}}, "status": "running"}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HetznerProvider(api_token="my-secret-token", http_client=client)
    await provider.get_server(1)
    assert captured_request["auth"] == "Bearer my-secret-token"
