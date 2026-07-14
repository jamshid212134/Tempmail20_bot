import httpx
import random
import string
import logging
import html as html_mod
import re

logger = logging.getLogger(__name__)


def generate_random_username(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def generate_random_password(length: int = 14) -> str:
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%^&*"
    return "".join(random.choices(chars, k=length))


def extract_verification_code(text: str) -> str | None:
    if not text:
        return None
    plain = html_mod.unescape(text)
    lines = plain.split("\n")
    keywords = [
        "verification code", "your code", "code is", "code:",
        "enter code", "use code", "otp code", "verification pin",
        "your pin", "pin is", "pin code", "activation code",
        "کد تایید", "کد تأیید", "کد شما", "کد ورود", "کد فعال‌سازی",
    ]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        lower_line = line.lower()
        for kw in keywords:
            if kw in lower_line:
                match = re.search(r"[:\s]+(\d{4,8})\s*$", line)
                if match:
                    return match.group(1)
                match = re.search(r"[:\s]+(\d{4,8})\s", line)
                if match:
                    return match.group(1)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"\s*(\d{4,8})\s*", line)
        if match:
            return match.group(1)
    return None


def extract_all_links(text: str) -> list[str]:
    if not text:
        return []
    plain = html_mod.unescape(text)
    raw_links = re.findall(r'(https?://[^\s<>"\']+)', plain)
    cleaned = []
    seen = set()
    for link in raw_links:
        link = link.rstrip('.,;:!?')
        link = re.sub(r'[)}\]]+$', '', link)
        if link not in seen:
            seen.add(link)
            cleaned.append(link)
    return cleaned


def extract_link_context(text: str) -> list[tuple[str, str]]:
    if not text:
        return []
    plain = html_mod.unescape(text)
    lines = plain.split("\n")
    results = []
    seen = set()
    for line in lines:
        links_in_line = re.findall(r'(https?://[^\s<>"\']+)', line)
        for link in links_in_line:
            link = link.rstrip('.,;:!?')
            link = re.sub(r'[)}\]]+$', '', link)
            if link in seen:
                continue
            seen.add(link)
            desc = _clean_link_desc(line, link)
            results.append((desc, link))
    return results


def _clean_link_desc(line: str, link: str) -> str:
    desc = line.replace(link, "").strip()
    desc = re.sub(r'^[:\s\-–—]+', '', desc)
    desc = re.sub(r'[.:;\s]+$', '', desc)
    if len(desc) > 60:
        desc = desc[:57] + "..."
    if not desc:
        ll = link.lower()
        if "delete" in ll:
            return "🗑️ لینک حذف"
        elif "verify" in ll or "confirm" in ll:
            return "✅ لینک تأیید"
        elif "login" in ll or "log" in ll:
            return "🔑 لینک ورود"
        return "🔗 لینک"
    return desc


class EmailBackend:
    name: str = ""
    address: str = ""
    password: str = ""

    async def create(self, username: str, password: str) -> str:
        raise NotImplementedError

    async def get_messages(self) -> list[dict]:
        raise NotImplementedError

    async def get_message_detail(self, msg_id: str) -> dict:
        raise NotImplementedError

    def extract_sender(self, email_data: dict) -> str:
        return "ناشناس"

    def extract_subject(self, email_data: dict) -> str:
        return "(بدون موضوع)"

    def extract_text(self, email_data: dict) -> str:
        return ""

    def extract_date(self, email_data: dict) -> str:
        return ""


class MailTmBackend(EmailBackend):
    name = "mail.tm"
    BASE_URL = "https://api.mail.tm"

    async def create(self, username: str, password: str) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.BASE_URL}/domains")
            resp.raise_for_status()
            domains = [d["domain"] for d in resp.json().get("hydra:member", [])]
            if not domains:
                raise ValueError("mail.tm: no domains available")
            domain = random.choice(domains)
            self.address = f"{username}@{domain}"
            self.password = password
            await client.post(
                f"{self.BASE_URL}/accounts",
                json={"address": self.address, "password": password},
            )
            resp = await client.post(
                f"{self.BASE_URL}/token",
                json={"address": self.address, "password": password},
            )
            resp.raise_for_status()
            self._token = resp.json()["token"]
            return self.address

    async def get_messages(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.BASE_URL}/messages",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            resp.raise_for_status()
            return resp.json().get("hydra:member", [])

    async def get_message_detail(self, msg_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.BASE_URL}/messages/{msg_id}",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            resp.raise_for_status()
            return resp.json()

    def extract_sender(self, d: dict) -> str:
        return d.get("from", {}).get("address", "ناشناس")

    def extract_subject(self, d: dict) -> str:
        return d.get("subject", "(بدون موضوع)")

    def extract_text(self, d: dict) -> str:
        return d.get("text") or d.get("html") or ""

    def extract_date(self, d: dict) -> str:
        return d.get("createdAt", "")[:10]


class GuerrillaBackend(EmailBackend):
    name = "guerrillamail"
    BASE_URL = "https://api.guerrillamail.com/ajax.php"

    async def create(self, username: str, password: str) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.BASE_URL}?f=get_email_address&ip=127.0.0.1&agent=Python"
            )
            resp.raise_for_status()
            data = resp.json()
            self.address = data.get("email_addr", "")
            self._token = data.get("sid_token", "")
            self.password = password
            return self.address

    async def get_messages(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.BASE_URL}?f=check_email&ip=127.0.0.1&sid_token={self._token}",
            )
            resp.raise_for_status()
            data = resp.json()
            emails = data.get("list", [])
            result = []
            for e in emails:
                result.append({
                    "id": e.get("mail_id"),
                    "from": {"address": e.get("mail_from", "")},
                    "subject": e.get("mail_subject", ""),
                    "text": e.get("mail_body", ""),
                    "createdAt": e.get("mail_date", ""),
                })
            return result

    async def get_message_detail(self, msg_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.BASE_URL}?f=fetch_email&ip=127.0.0.1&sid_token={self._token}&email_id={msg_id}",
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": msg_id,
                "from": {"address": data.get("mail_from", "")},
                "subject": data.get("mail_subject", ""),
                "text": data.get("mail_body", ""),
                "html": data.get("mail_body", ""),
                "createdAt": data.get("mail_date", ""),
            }

    def extract_sender(self, d: dict) -> str:
        f = d.get("from", {})
        if isinstance(f, dict):
            return f.get("address", "ناشناس")
        return str(f) if f else "ناشناس"

    def extract_subject(self, d: dict) -> str:
        return d.get("subject", "(بدون موضوع)")

    def extract_text(self, d: dict) -> str:
        return d.get("text", "")

    def extract_date(self, d: dict) -> str:
        return d.get("createdAt", "")[:10]


BACKENDS = [MailTmBackend, GuerrillaBackend]


async def create_email(username: str = None) -> tuple[EmailBackend, str]:
    if not username:
        username = generate_random_username()
    password = generate_random_password()

    errors = []
    for BackendClass in BACKENDS:
        try:
            backend = BackendClass()
            address = await backend.create(username, password)
            logger.info(f"Created email on {backend.name}: {address}")
            return backend, address
        except Exception as e:
            logger.warning(f"Failed on {BackendClass.name}: {e}")
            errors.append(f"{BackendClass.name}: {e}")

    raise RuntimeError(f"All backends failed: {'; '.join(errors)}")
