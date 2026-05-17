import httpx
import pytest

from computeedge.exceptions import ProviderAPIError
from computeedge.providers.digitalocean import DigitalOceanProvider


def make_provider_with_responses(responses: list[httpx.Response]):
    call_count = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if call_count < len(responses):
            resp = responses[call_count]
            call_count += 1
            return resp
        return httpx.Response(500, json={"id": "server_error", "message": "unexpected call"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DigitalOceanProvider(api_token="test-token", http_client=client)


@pytest.mark.asyncio
async def test_create_droplet():
    provider = make_provider_with_responses([
        httpx.Response(202, json={"droplet": {
            "id": 12345,
            "name": "test-srv",
            "status": "new",
            "networks": {"v4": [
                {"ip_address": "10.0.0.1", "type": "private"},
                {"ip_address": "1.2.3.4", "type": "public"},
            ]},
        }}),
    ])
    result = await provider.create_droplet("test-srv", "s-1vcpu-1gb", ssh_key_id=1)
    assert result["id"] == 12345
    assert result["status"] == "new"


@pytest.mark.asyncio
async def test_create_droplet_api_error():
    provider = make_provider_with_responses([
        httpx.Response(422, json={"id": "invalid_attribute", "message": "Size is not available in this region."}),
    ])
    with pytest.raises(ProviderAPIError, match="Size is not available"):
        await provider.create_droplet("test-srv", "bad-size", ssh_key_id=1)


@pytest.mark.asyncio
async def test_delete_droplet():
    provider = make_provider_with_responses([httpx.Response(204)])
    await provider.delete_droplet(12345)


@pytest.mark.asyncio
async def test_get_droplet():
    provider = make_provider_with_responses([
        httpx.Response(200, json={"droplet": {
            "id": 12345,
            "status": "active",
            "networks": {"v4": [
                {"ip_address": "10.0.0.1", "type": "private"},
                {"ip_address": "1.2.3.4", "type": "public"},
            ]},
        }}),
    ])
    result = await provider.get_droplet(12345)
    assert result["status"] == "active"
    assert result["ip"] == "1.2.3.4"


@pytest.mark.asyncio
async def test_get_droplet_no_public_ip():
    provider = make_provider_with_responses([
        httpx.Response(200, json={"droplet": {
            "id": 12345,
            "status": "active",
            "networks": {"v4": [
                {"ip_address": "10.0.0.1", "type": "private"},
            ]},
        }}),
    ])
    result = await provider.get_droplet(12345)
    assert result["ip"] is None


@pytest.mark.asyncio
async def test_upload_ssh_key():
    provider = make_provider_with_responses([
        httpx.Response(201, json={"ssh_key": {"id": 42, "name": "my-key", "fingerprint": "ab:cd"}}),
    ])
    key_id = await provider.upload_ssh_key("my-key", "ssh-ed25519 AAAA...")
    assert key_id == 42


@pytest.mark.asyncio
async def test_upload_ssh_key_already_exists():
    pub_key = "ssh-ed25519 AAAA..."
    provider = make_provider_with_responses([
        httpx.Response(422, json={"id": "unprocessable_entity", "message": "SSH Key is already in use on your account"}),
        httpx.Response(200, json={"ssh_keys": [
            {"id": 99, "name": "existing-key", "public_key": pub_key, "fingerprint": "ab:cd"},
        ]}),
    ])
    key_id = await provider.upload_ssh_key("my-key", pub_key)
    assert key_id == 99


@pytest.mark.asyncio
async def test_upload_ssh_key_conflict_no_match():
    provider = make_provider_with_responses([
        httpx.Response(422, json={"id": "unprocessable_entity", "message": "SSH Key is already in use on your account"}),
        httpx.Response(200, json={"ssh_keys": [
            {"id": 99, "name": "other-key", "public_key": "ssh-ed25519 DIFFERENT", "fingerprint": "xx:yy"},
        ]}),
    ])
    with pytest.raises(ProviderAPIError, match="no matching key found"):
        await provider.upload_ssh_key("my-key", "ssh-ed25519 AAAA...")


@pytest.mark.asyncio
async def test_delete_ssh_key():
    provider = make_provider_with_responses([httpx.Response(204)])
    await provider.delete_ssh_key(42)


@pytest.mark.asyncio
async def test_delete_ssh_key_already_gone():
    provider = make_provider_with_responses([httpx.Response(404, json={"id": "not_found", "message": "not found"})])
    await provider.delete_ssh_key(42)


@pytest.mark.asyncio
async def test_create_firewall():
    provider = make_provider_with_responses([
        httpx.Response(202, json={"firewall": {
            "id": "fw-abc-123",
            "name": "test-fw",
            "status": "waiting",
        }}),
    ])
    fw_id = await provider.create_firewall("test-fw", droplet_id=12345)
    assert fw_id == "fw-abc-123"


@pytest.mark.asyncio
async def test_delete_firewall():
    provider = make_provider_with_responses([httpx.Response(204)])
    await provider.delete_firewall("fw-abc-123")


@pytest.mark.asyncio
async def test_delete_firewall_already_gone():
    provider = make_provider_with_responses([httpx.Response(404, json={"id": "not_found", "message": "not found"})])
    await provider.delete_firewall("fw-abc-123")


@pytest.mark.asyncio
async def test_wait_for_ready():
    provider = make_provider_with_responses([
        httpx.Response(200, json={"droplet": {
            "id": 12345, "status": "new",
            "networks": {"v4": [{"ip_address": "1.2.3.4", "type": "public"}]},
        }}),
        httpx.Response(200, json={"droplet": {
            "id": 12345, "status": "active",
            "networks": {"v4": [{"ip_address": "1.2.3.4", "type": "public"}]},
        }}),
    ])
    ip = await provider.wait_for_ready(12345, timeout=10, poll_interval=0.1)
    assert ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_wait_for_ready_timeout():
    provider = make_provider_with_responses([
        httpx.Response(200, json={"droplet": {
            "id": 12345, "status": "new",
            "networks": {"v4": [{"ip_address": "1.2.3.4", "type": "public"}]},
        }}),
    ] * 50)
    with pytest.raises(ProviderAPIError, match="timed out"):
        await provider.wait_for_ready(12345, timeout=0.3, poll_interval=0.1)


@pytest.mark.asyncio
async def test_auth_header():
    captured_request = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"droplet": {
            "id": 1, "status": "active",
            "networks": {"v4": [{"ip_address": "0.0.0.0", "type": "public"}]},
        }})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DigitalOceanProvider(api_token="my-secret-token", http_client=client)
    await provider.get_droplet(1)
    assert captured_request["auth"] == "Bearer my-secret-token"


@pytest.mark.asyncio
async def test_create_firewall_request_body():
    captured_body = {}
    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured_body.update(json.loads(request.content))
        return httpx.Response(202, json={"firewall": {"id": "fw-1", "name": "test", "status": "waiting"}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DigitalOceanProvider(api_token="test-token", http_client=client)
    await provider.create_firewall("test-fw", droplet_id=123)

    assert captured_body["name"] == "test-fw"
    assert captured_body["droplet_ids"] == [123]
    protocols_ports = {(r["protocol"], r["ports"]) for r in captured_body["inbound_rules"]}
    assert ("tcp", "22") in protocols_ports
    assert ("tcp", "80") in protocols_ports
    assert ("tcp", "443") in protocols_ports
    assert ("icmp", "0") in protocols_ports
