import asyncio

import httpx

from computeedge.exceptions import ProviderAPIError
from computeedge.utils.logger import get_logger

logger = get_logger("digitalocean")

BASE_URL = "https://api.digitalocean.com/v2"


class DigitalOceanProvider:
    """DigitalOcean REST API client."""

    def __init__(self, api_token: str, http_client: httpx.AsyncClient):
        self._token = api_token
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _check_response(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                data = response.json()
                msg = data.get("message", f"HTTP {response.status_code}")
            except Exception:
                msg = f"HTTP {response.status_code}"
            raise ProviderAPIError(f"DigitalOcean API error: {msg}")

    def _parse_droplet(self, droplet: dict) -> dict:
        ip = None
        for net in droplet.get("networks", {}).get("v4", []):
            if net.get("type") == "public":
                ip = net["ip_address"]
                break
        return {
            "id": droplet["id"],
            "ip": ip,
            "status": droplet["status"],
        }

    async def create_droplet(
        self,
        name: str,
        size: str,
        ssh_key_id: int,
        image: str = "ubuntu-24-04-x64",
        region: str = "nyc3",
        user_data: str | None = None,
    ) -> dict:
        payload: dict = {
            "name": name,
            "size": size,
            "image": image,
            "region": region,
            "ssh_keys": [ssh_key_id],
        }
        if user_data is not None:
            payload["user_data"] = user_data
        response = await self._client.post(
            f"{BASE_URL}/droplets", headers=self._headers(), json=payload,
        )
        self._check_response(response)
        return self._parse_droplet(response.json()["droplet"])

    async def delete_droplet(self, droplet_id: int) -> None:
        response = await self._client.delete(
            f"{BASE_URL}/droplets/{droplet_id}", headers=self._headers(),
        )
        if response.status_code == 204:
            return
        self._check_response(response)

    async def upload_ssh_key(self, name: str, public_key: str) -> int:
        response = await self._client.post(
            f"{BASE_URL}/account/keys",
            headers=self._headers(),
            json={"name": name, "public_key": public_key},
        )
        if response.status_code == 422 and "already in use" in response.json().get("message", "").lower():
            logger.info("SSH key already registered, searching for match")
            list_resp = await self._client.get(
                f"{BASE_URL}/account/keys", headers=self._headers(),
            )
            self._check_response(list_resp)
            normalized = public_key.strip()
            for key in list_resp.json()["ssh_keys"]:
                if key.get("public_key", "").strip() == normalized:
                    logger.info("Matched existing SSH key id=%s", key["id"])
                    return key["id"]
            raise ProviderAPIError(
                "SSH key conflict but no matching key found in account"
            )
        self._check_response(response)
        return response.json()["ssh_key"]["id"]

    async def delete_ssh_key(self, ssh_key_id: int) -> None:
        response = await self._client.delete(
            f"{BASE_URL}/account/keys/{ssh_key_id}", headers=self._headers(),
        )
        if response.status_code == 404:
            logger.info("SSH key %s already gone, skipping", ssh_key_id)
            return
        if response.status_code == 204:
            return
        self._check_response(response)

    async def create_firewall(self, name: str, droplet_id: int) -> str:
        all_addrs = {"addresses": ["0.0.0.0/0", "::/0"]}
        payload = {
            "name": name,
            "droplet_ids": [droplet_id],
            "inbound_rules": [
                {"protocol": "icmp", "ports": "0", "sources": all_addrs},
                {"protocol": "tcp", "ports": "22", "sources": all_addrs},
                {"protocol": "tcp", "ports": "80", "sources": all_addrs},
                {"protocol": "tcp", "ports": "443", "sources": all_addrs},
            ],
            "outbound_rules": [
                {"protocol": "tcp", "ports": "0", "destinations": all_addrs},
                {"protocol": "udp", "ports": "0", "destinations": all_addrs},
                {"protocol": "icmp", "ports": "0", "destinations": all_addrs},
            ],
        }
        response = await self._client.post(
            f"{BASE_URL}/firewalls", headers=self._headers(), json=payload,
        )
        self._check_response(response)
        return response.json()["firewall"]["id"]

    async def delete_firewall(self, firewall_id: str) -> None:
        response = await self._client.delete(
            f"{BASE_URL}/firewalls/{firewall_id}", headers=self._headers(),
        )
        if response.status_code == 404:
            logger.info("Firewall %s already gone, skipping", firewall_id)
            return
        if response.status_code == 204:
            return
        self._check_response(response)

    async def get_droplet(self, droplet_id: int) -> dict:
        response = await self._client.get(
            f"{BASE_URL}/droplets/{droplet_id}", headers=self._headers(),
        )
        self._check_response(response)
        return self._parse_droplet(response.json()["droplet"])

    async def wait_for_ready(
        self,
        droplet_id: int,
        timeout: int = 120,
        poll_interval: float = 5.0,
    ) -> str:
        import time

        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                raise ProviderAPIError(
                    f"Droplet {droplet_id} timed out waiting for 'active' status"
                )
            droplet = await self.get_droplet(droplet_id)
            if droplet["status"] == "active":
                logger.info(
                    "Droplet %s is active at %s", droplet_id, droplet["ip"],
                )
                return droplet["ip"]
            logger.info(
                "Droplet %s status: %s, waiting...",
                droplet_id,
                droplet["status"],
            )
            interval = 3.0 if elapsed < 30 else poll_interval
            await asyncio.sleep(interval)
