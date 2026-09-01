import base64
import hashlib
import hmac
import re
import time
from typing import Optional
from urllib.parse import urlencode

import httpx

from config import settings

AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"

# How long a "state" token (and therefore an in-flight OAuth attempt) stays valid.
STATE_TTL_SECONDS = 600

APP_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class NotionOAuthError(Exception):
    """Raised when the authorization code can't be exchanged for a token."""


def is_valid_app_user_id(app_user_id: str) -> bool:
    return bool(APP_USER_ID_RE.match(app_user_id))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_state(app_user_id: str) -> str:
    """Signs app_user_id + a timestamp so the callback can trust the state
    param without needing server-side session storage for pending logins."""
    payload_b64 = _b64url_encode(f"{app_user_id}:{int(time.time())}".encode())
    signature = hmac.new(
        settings.SESSION_SECRET.encode(), payload_b64.encode(), hashlib.sha256
    ).digest()
    return f"{payload_b64}.{_b64url_encode(signature)}"


def verify_state(state: str) -> Optional[str]:
    """Returns the app_user_id embedded in state if the signature is valid
    and the state hasn't expired, else None."""
    try:
        payload_b64, sig_b64 = state.split(".", 1)
        expected_sig = hmac.new(
            settings.SESSION_SECRET.encode(), payload_b64.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_b64url_encode(expected_sig), sig_b64):
            return None

        app_user_id, issued_at = _b64url_decode(payload_b64).decode().rsplit(":", 1)
        if time.time() - int(issued_at) > STATE_TTL_SECONDS:
            return None
        if not is_valid_app_user_id(app_user_id):
            return None
        return app_user_id
    except Exception:
        return None


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.NOTION_CLIENT_ID,
        "response_type": "code",
        "owner": "user",
        "redirect_uri": settings.NOTION_REDIRECT_URI,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchanges an authorization code for an access token. Raises
    NotionOAuthError on any failure (invalid/expired code, network issues)."""
    basic = base64.b64encode(
        f"{settings.NOTION_CLIENT_ID}:{settings.NOTION_CLIENT_SECRET}".encode()
    ).decode()
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/json",
    }
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.NOTION_REDIRECT_URI,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(TOKEN_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise NotionOAuthError("network_error") from exc

    if resp.status_code != 200:
        raise NotionOAuthError("token_exchange_failed")

    return resp.json()
