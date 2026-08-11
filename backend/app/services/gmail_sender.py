"""Send mail through the Gmail API over HTTPS.

Why this exists: hosting providers commonly block outbound SMTP ports
(25/465/587) to curb spam, so ``smtplib`` times out in production while working
perfectly on a laptop. The Gmail API is an ordinary HTTPS call to port 443, so
it is unaffected — and unlike a third-party relay it needs no new account and
sends genuinely *from* the connected mailbox, which is better for deliverability
than having some other service send on Gmail's behalf.

The grant is **app-wide, not per-tenant**: one mailbox sends everything, exactly
as the single ``SMTP_USER`` did. It is stored in ``app_settings`` under the key
``gmail_send`` rather than against an owner, so switching the sending account
never depends on which host happens to be signed in.

Scope is ``gmail.send`` — permission to send, and nothing else. It cannot read
a single message.
"""

import base64
import logging
import threading
import time
from email.message import EmailMessage

import httpx
from pymongo.database import Database

from ..config import settings

logger = logging.getLogger("schedulr.gmail")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

SETTING_KEY = "gmail_send"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

_TOKEN_EXPIRY_MARGIN_SECONDS = 60
# Connection state is read on every send; a short cache keeps that off the hot
# path without making a disconnect take minutes to notice.
_STATE_TTL_SECONDS = 30

_lock = threading.Lock()
_token_cache: tuple[str, float] | None = None
_state_cache: tuple[dict | None, float] | None = None


def get_connection(db: Database) -> dict | None:
    """The stored Gmail grant, or None. Cached briefly."""
    global _state_cache
    now = time.time()
    with _lock:
        if _state_cache and _state_cache[1] > now:
            return _state_cache[0]

    doc = db.app_settings.find_one({"key": SETTING_KEY})
    with _lock:
        _state_cache = (doc, now + _STATE_TTL_SECONDS)
    return doc


def is_connected(db: Database) -> bool:
    doc = get_connection(db)
    return bool(doc and doc.get("refresh_token") and not doc.get("invalid"))


def sender_address(db: Database) -> str:
    doc = get_connection(db)
    return (doc or {}).get("email", "")


def save_connection(db: Database, refresh_token: str, email: str) -> None:
    db.app_settings.update_one(
        {"key": SETTING_KEY},
        {"$set": {"refresh_token": refresh_token, "email": email, "invalid": False}},
        upsert=True,
    )
    invalidate()


def disconnect(db: Database) -> None:
    db.app_settings.delete_one({"key": SETTING_KEY})
    invalidate()


def invalidate() -> None:
    """Drop cached token and connection state so a change applies at once."""
    global _token_cache, _state_cache
    with _lock:
        _token_cache = None
        _state_cache = None


def _access_token(db: Database) -> str | None:
    global _token_cache
    now = time.time()
    with _lock:
        if _token_cache and _token_cache[1] > now:
            return _token_cache[0]

    doc = get_connection(db)
    refresh_token = (doc or {}).get("refresh_token")
    if not refresh_token:
        return None

    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15.0,
    )
    if response.status_code != 200:
        if response.status_code in (400, 401):
            # A revoked grant never recovers; flag it so the UI can say
            # "reconnect" instead of silently never sending again.
            db.app_settings.update_one(
                {"key": SETTING_KEY}, {"$set": {"invalid": True}}
            )
            invalidate()
        raise RuntimeError(
            f"Gmail token refresh failed: HTTP {response.status_code} "
            f"{response.text[:200]}"
        )

    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Gmail token refresh returned no access_token.")

    expires_in = int(payload.get("expires_in", 3600))
    with _lock:
        _token_cache = (token, now + expires_in - _TOKEN_EXPIRY_MARGIN_SECONDS)
    return token


def exchange_code(code: str, redirect_uri: str) -> tuple[str, str]:
    """Trade an authorization code for (refresh_token, mailbox address)."""
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Code exchange failed: HTTP {response.status_code}")

    payload = response.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("no_refresh_token")

    # Which mailbox did they actually authorise? That address has to be the
    # From header, because Gmail rejects sending as anyone else.
    address = ""
    profile = httpx.get(
        GMAIL_PROFILE_URL,
        headers={"Authorization": f"Bearer {payload.get('access_token')}"},
        timeout=15.0,
    )
    if profile.status_code == 200:
        address = profile.json().get("emailAddress", "")

    return refresh_token, address


def send(db: Database, msg: EmailMessage) -> None:
    """Send an already-built message. Raises on failure."""
    token = _access_token(db)
    if not token:
        raise RuntimeError("No Gmail account is connected.")

    # Gmail sends as the authenticated mailbox and rejects a mismatched From,
    # so rewrite it rather than letting the send fail on a stale SMTP_FROM.
    address = sender_address(db)
    if address:
        display = settings.SMTP_FROM_NAME or "Shopper"
        del msg["From"]
        msg["From"] = f"{display} <{address}>"

    raw = base64.urlsafe_b64encode(bytes(msg)).decode()
    response = httpx.post(
        GMAIL_SEND_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"raw": raw},
        timeout=20.0,
    )
    if response.status_code >= 300:
        raise RuntimeError(
            f"Gmail API returned HTTP {response.status_code}: {response.text[:300]}"
        )
