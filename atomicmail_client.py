import httpx
import hashlib
import json
import time
import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AUTH_URL = "https://auth.atomicmail.ai"
API_URL = "https://api.atomicmail.ai"
DEFAULT_SCRYPT_SALT = "0b980734412c292d6549110276b604ab1dea4883bd460d77d1b984adf8bca083"
CREDENTIALS_DIR = Path.home() / ".atomicmail"

SCRYPT_N = 16_384
SCRYPT_R = 8
SCRYPT_P = 1
POW_HASH_BYTES = 64


def _decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Invalid JWT")
    payload_b64 = parts[1]
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    decoded = base64.urlsafe_b64decode(payload_b64)
    return json.loads(decoded)


def _has_leading_zero_bits(hash_bytes: bytes, bits: int) -> bool:
    full_bytes = bits // 8
    remaining_bits = bits % 8
    for i in range(full_bytes):
        if hash_bytes[i] != 0:
            return False
    if remaining_bits:
        mask = (0xFF << (8 - remaining_bits)) & 0xFF
        if (hash_bytes[full_bytes] & mask) != 0:
            return False
    return True


def solve_pow(challenge: str, difficulty: int, salt: str) -> tuple[str, str]:
    nonce = 0
    while True:
        data = f"{challenge}:{nonce}".encode("utf-8")
        salt_bytes = salt.encode("utf-8")
        digest = hashlib.scrypt(
            data, salt=salt_bytes, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=POW_HASH_BYTES
        )
        if _has_leading_zero_bits(digest, difficulty):
            return digest.hex(), str(nonce)
        nonce += 1
        if nonce % 256 == 0:
            logger.info(f"PoW progress: nonce={nonce}")


class AtomicMailClient:
    def __init__(self):
        self._http = httpx.AsyncClient(timeout=120)
        self._session_jwt: str | None = None
        self._capability_jwt: str | None = None
        self._capability_expires: float = 0
        self._api_key: str | None = None
        self._account_id: str | None = None
        self._address: str | None = None

    async def close(self):
        await self._http.aclose()

    async def register(self, username: str) -> dict:
        logger.info(f"Requesting challenge for {username}...")
        resp = await self._http.post(f"{AUTH_URL}/api/v1/challenge")
        if resp.status_code != 200:
            raise ValueError(f"Challenge request failed: {resp.status_code} {resp.text}")

        challenge_jwt = None
        auth_header = resp.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            challenge_jwt = auth_header[7:].strip()

        if not challenge_jwt:
            raise ValueError("No challenge JWT in response")

        payload = _decode_jwt_payload(challenge_jwt)
        challenge = payload.get("jti")
        difficulty = payload.get("difficulty")
        salt = payload.get("salt", DEFAULT_SCRYPT_SALT)

        if not challenge or not difficulty:
            raise ValueError(f"Malformed challenge payload: {payload}")

        logger.info(f"Solving PoW (difficulty={difficulty})...")
        pow_hex, nonce = solve_pow(challenge, int(difficulty), salt)
        logger.info(f"PoW solved! nonce={nonce}")

        resp = await self._http.post(
            f"{AUTH_URL}/api/v1/session",
            headers={"Authorization": f"Bearer {challenge_jwt}"},
            json={"powHex": pow_hex, "nonce": nonce, "username": username},
        )
        if resp.status_code != 200:
            error_data = {}
            try:
                error_data = resp.json()
            except Exception:
                pass
            hint = error_data.get("error", {}).get("hint", "")
            raise ValueError(f"Session exchange failed: {resp.status_code} {resp.text} {hint}")

        session_jwt = None
        auth_header = resp.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            session_jwt = auth_header[7:].strip()

        resp_data = resp.json() if resp.text.strip() else {}
        api_key = resp_data.get("apiKey")

        if not session_jwt:
            raise ValueError("No session JWT in response")

        self._session_jwt = session_jwt
        self._api_key = api_key
        self._address = f"{username}@atomicmail.ai"

        await self._refresh_capability()

        session_payload = _decode_jwt_payload(session_jwt)
        self._account_id = (
            session_payload.get("accountId")
            or session_payload.get("sub")
        )

        if not self._account_id or self._account_id in ("session", "challenge"):
            self._account_id = await self._resolve_account_id()

        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        creds = {
            "username": username,
            "address": self._address,
            "api_key": api_key,
            "account_id": self._account_id,
            "session_jwt": session_jwt,
        }
        cred_file = CREDENTIALS_DIR / f"{username}.json"
        cred_file.write_text(json.dumps(creds, indent=2))

        return creds

    async def login_with_api_key(self, api_key: str, username: str) -> dict:
        self._api_key = api_key
        self._address = f"{username}@atomicmail.ai"

        logger.info(f"Logging in with API key for {username}...")

        resp = await self._http.post(f"{AUTH_URL}/api/v1/challenge")
        if resp.status_code != 200:
            raise ValueError(f"Challenge request failed: {resp.status_code}")

        challenge_jwt = None
        auth_header = resp.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            challenge_jwt = auth_header[7:].strip()

        payload = _decode_jwt_payload(challenge_jwt)
        challenge = payload.get("jti")
        difficulty = payload.get("difficulty")
        salt = payload.get("salt", DEFAULT_SCRYPT_SALT)

        pow_hex, nonce = solve_pow(challenge, int(difficulty), salt)

        resp = await self._http.post(
            f"{AUTH_URL}/api/v1/session",
            headers={"Authorization": f"Bearer {challenge_jwt}"},
            json={"powHex": pow_hex, "nonce": nonce, "apiKey": api_key},
        )
        if resp.status_code != 200:
            raise ValueError(f"Session exchange failed: {resp.status_code} {resp.text}")

        session_jwt = None
        auth_header = resp.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            session_jwt = auth_header[7:].strip()

        self._session_jwt = session_jwt

        await self._refresh_capability()

        session_payload = _decode_jwt_payload(session_jwt)
        self._account_id = (
            session_payload.get("accountId")
            or session_payload.get("sub")
        )

        if not self._account_id or self._account_id in ("session", "challenge"):
            self._account_id = await self._resolve_account_id()

        return {"username": username, "address": self._address, "api_key": api_key, "account_id": self._account_id}

    async def _refresh_capability(self):
        if not self._session_jwt:
            raise ValueError("No session JWT")

        resp = await self._http.post(
            f"{AUTH_URL}/api/v1/capability",
            headers={"Authorization": f"Bearer {self._session_jwt}"},
        )
        if resp.status_code != 200:
            raise ValueError(f"Capability request failed: {resp.status_code} {resp.text}")

        cap_jwt = None
        auth_header = resp.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            cap_jwt = auth_header[7:].strip()

        if not cap_jwt:
            raise ValueError("No capability JWT in response")

        self._capability_jwt = cap_jwt
        self._capability_expires = time.time() + 110

    async def _ensure_capability(self):
        if not self._capability_jwt or time.time() >= self._capability_expires:
            await self._refresh_capability()

    async def _resolve_account_id(self) -> str:
        await self._ensure_capability()
        result = await self.jmap_request([
            ["Email/query", {"accountId": "$$first", "limit": 1}, "q0"]
        ])
        for item in result.get("methodResponses", []):
            if item[0] == "error":
                continue
        resp = await self._http.post(
            f"{API_URL}/jmap",
            headers={"Authorization": f"Bearer {self._capability_jwt}"},
            json={
                "using": ["urn:ietf:params:jmap:core"],
                "methodCalls": [["Account/get", {}, "a0"]],
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("methodResponses", []):
                if item[0] == "Account/get":
                    accounts = item[1].get("list", [])
                    if accounts:
                        return accounts[0].get("id", "")
        return ""

    async def jmap_request(self, ops: list) -> dict:
        await self._ensure_capability()
        resp = await self._http.post(
            f"{API_URL}/jmap",
            headers={"Authorization": f"Bearer {self._capability_jwt}"},
            json={
                "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
                "methodCalls": ops,
            },
        )
        if resp.status_code != 200:
            raise ValueError(f"JMAP request failed: {resp.status_code} {resp.text}")
        return resp.json()

    async def get_messages(self, account_id: str, mailbox_id: str = None) -> list:
        query = {"accountId": account_id, "limit": 50}
        if mailbox_id:
            query["mailboxId"] = mailbox_id

        result = await self.jmap_request([
            ["Email/query", query, "eq0"],
            ["Email/get", {
                "accountId": account_id,
                "#ids": {"resultOf": "eq0", "name": "Email/query", "path": "/ids"},
                "properties": [
                    "id", "subject", "from", "to", "receivedAt",
                    "textBody", "htmlBody", "bodyValues",
                    "preview", "keywords"
                ],
            }, "eg0"],
        ])

        emails = []
        for item in result.get("methodResponses", []):
            if item[0] == "Email/get":
                emails.extend(item[1].get("list", []))
        return emails

    async def get_email_detail(self, account_id: str, email_id: str) -> dict:
        result = await self.jmap_request([
            ["Email/get", {
                "accountId": account_id,
                "ids": [email_id],
                "properties": [
                    "id", "subject", "from", "to", "receivedAt",
                    "textBody", "htmlBody", "bodyValues",
                    "preview", "keywords"
                ],
            }, "ed0"],
        ])

        for item in result.get("methodResponses", []):
            if item[0] == "Email/get":
                emails = item[1].get("list", [])
                if emails:
                    return emails[0]
        return {}

    @staticmethod
    def extract_text_from_email(email_data: dict) -> str:
        body_values = email_data.get("bodyValues", {})
        text_parts = email_data.get("textBody", [])

        if text_parts and body_values:
            texts = []
            for part in text_parts:
                part_id = part.get("partId", "")
                if part_id in body_values:
                    texts.append(body_values[part_id].get("value", ""))
            if texts:
                return "\n".join(texts)

        return email_data.get("text") or email_data.get("preview") or ""

    @staticmethod
    def extract_sender(email_data: dict) -> str:
        from_field = email_data.get("from", [])
        if isinstance(from_field, list) and from_field:
            return from_field[0].get("email", "ناشناس")
        elif isinstance(from_field, dict):
            return from_field.get("email", "ناشناس")
        return "ناشناس"


_clients: dict[str, AtomicMailClient] = {}


def get_client(username: str) -> AtomicMailClient:
    if username not in _clients:
        _clients[username] = AtomicMailClient()
    return _clients[username]


def get_client_by_address(address: str) -> AtomicMailClient | None:
    for uname, client in _clients.items():
        if client._address == address:
            return client
    return None


async def register(username: str) -> dict:
    client = get_client(username)
    return await client.register(username)


async def get_messages(username: str, account_id: str) -> list:
    client = get_client(username)
    return await client.get_messages(account_id)


async def get_email_detail(username: str, account_id: str, email_id: str) -> dict:
    client = get_client(username)
    return await client.get_email_detail(account_id, email_id)


def extract_text_from_email(email_data: dict) -> str:
    return AtomicMailClient.extract_text_from_email(email_data)


def extract_sender(email_data: dict) -> str:
    return AtomicMailClient.extract_sender(email_data)


def load_credentials(username: str) -> dict | None:
    cred_file = CREDENTIALS_DIR / f"{username}.json"
    if cred_file.exists():
        return json.loads(cred_file.read_text())
    return None


def list_saved_credentials() -> list[dict]:
    if not CREDENTIALS_DIR.exists():
        return []
    creds = []
    for f in CREDENTIALS_DIR.glob("*.json"):
        try:
            creds.append(json.loads(f.read_text()))
        except Exception:
            pass
    return creds
