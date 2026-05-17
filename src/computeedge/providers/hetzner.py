import asyncio

import httpx

from computeedge.exceptions import ProviderAPIError
from computeedge.utils.logger import get_logger

logger = get_logger("hetzner")

BASE_URL = "https://api.hetzner.cloud/v1"


class HetznerProvider:
    """Hetzner Cloud REST API client."""

    def __init__(self, api_token: str, http_client: httpx.AsyncClient):
        self._token = api_token
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _check_response(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            data = response.json()
            msg = data.get("error", {}).get("message", f"HTTP {response.status_code}")
            raise ProviderAPIError(f"Hetzner API error: {msg}")

    def _parse_server(self, server: dict) -> dict:
        return {
            "id": server["id"],
            "ip": server["public_net"]["ipv4"]["ip"],
            "status": server["status"],
        }

    async def create_server(self, name: str, server_type: str, ssh_key_id: int,
                            image: str = "ubuntu-24.04", location: str = "nbg1",
                            user_data: str | None = None) -> dict:
        payload = {"name": name, "server_type": server_type, "image": image, "location": location, "ssh_keys": [ssh_key_id]}
        if user_data is not None:
            payload["user_data"] = user_data
        response = await self._client.post(f"{BASE_URL}/servers", headers=self._headers(), json=payload)
        self._check_response(response)
        return self._parse_server(response.json()["server"])

    async def delete_server(self, server_id: int) -> None:
        response = await self._client.delete(f"{BASE_URL}/servers/{server_id}", headers=self._headers())
        self._check_response(response)

    async def upload_ssh_key(self, name: str, public_key: str) -> int:
        response = await self._client.post(f"{BASE_URL}/ssh_keys", headers=self._headers(),
            json={"name": name, "public_key": public_key})
        if response.status_code == 409:
            logger.info("SSH key fingerprint already registered, reusing existing key")
            list_resp = await self._client.get(f"{BASE_URL}/ssh_keys", headers=self._headers())
            self._check_response(list_resp)
            normalized = public_key.strip()
            for key in list_resp.json()["ssh_keys"]:
                if key.get("public_key", "").strip() == normalized:
                    logger.info("Matched existing SSH key id=%s name=%s", key["id"], key["name"])
                    return key["id"]
            raise ProviderAPIError("SSH key fingerprint conflict but no matching key found in account")
        self._check_response(response)
        return response.json()["ssh_key"]["id"]

    async def delete_ssh_key(self, ssh_key_id: int) -> None:
        response = await self._client.delete(f"{BASE_URL}/ssh_keys/{ssh_key_id}", headers=self._headers())
        if response.status_code == 404:
            logger.info("SSH key %s already gone, skipping", ssh_key_id)
            return
        self._check_response(response)

    async def get_server(self, server_id: int) -> dict:
        response = await self._client.get(f"{BASE_URL}/servers/{server_id}", headers=self._headers())
        self._check_response(response)
        return self._parse_server(response.json()["server"])

    async def wait_for_ready(self, server_id: int, timeout: int = 120, poll_interval: float = 5.0) -> str:
        import time
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                raise ProviderAPIError(f"Server {server_id} timed out waiting for 'running' status")
            server = await self.get_server(server_id)
            if server["status"] == "running":
                logger.info("Server %s is running at %s", server_id, server["ip"])
                return server["ip"]
            logger.info("Server %s status: %s, waiting...", server_id, server["status"])
            interval = 3.0 if elapsed < 30 else 5.0
            await asyncio.sleep(interval)
