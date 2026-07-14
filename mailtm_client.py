import httpx
import random
import string
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mail.tm"


def generate_random_username(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def generate_random_password(length: int = 14) -> str:
    chars = (
        string.ascii_uppercase
        + string.ascii_lowercase
        + string.digits
        + "!@#$%^&*"
    )
    return "".join(random.choices(chars, k=length))


async def get_available_domains() -> list[str]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{BASE_URL}/domains")
        resp.raise_for_status()
        data = resp.json()
        return [item["domain"] for item in data.get("hydra:member", [])]


async def create_account(address: str, password: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/accounts",
            json={"address": address, "password": password},
        )
        resp.raise_for_status()
        return resp.json()


async def get_token(address: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/token",
            json={"address": address, "password": password},
        )
        resp.raise_for_status()
        return resp.json()["token"]


async def get_messages(token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE_URL}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json().get("hydra:member", [])


async def get_message_detail(token: str, msg_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE_URL}/messages/{msg_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def delete_account(token: str) -> bool:
    async with httpx.AsyncClient(timeout=15) as client:
        resp_me = await client.get(
            f"{BASE_URL}/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp_me.status_code != 200:
            return False
        account_id = resp_me.json().get("id")
        if not account_id:
            return False
        resp = await client.delete(
            f"{BASE_URL}/accounts/{account_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.status_code == 204
