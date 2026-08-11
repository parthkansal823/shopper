"""Read a host's external Google Calendar so Shopper stops double-booking them.

Shopper only knows about bookings made through Shopper. A host with a dentist
appointment in their personal calendar would still be offered as free. This
queries Google's freeBusy endpoint for the same span slot generation is about
and folds the result into the busy list.

Failure is deliberately *open*: if Google is unreachable or the grant was
revoked, the host keeps their Shopper availability rather than having their
booking page go dark. That risks a double-book, so every failure is logged.

Two caches keep this off the hot path: access tokens (refreshed roughly hourly)
and freeBusy answers (a few seconds, enough to cover the burst of slot requests
one calendar view produces).
"""

import logging
import threading
import time
from datetime import datetime, timezone

import httpx
from pymongo.database import Database

from ..config import settings

logger = logging.getLogger("schedulr.calendar")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"

INTEGRATION_KEY = "google_calendar"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

# freeBusy answers are cached this long. Long enough that one calendar render
# makes a single call, short enough that a just-added meeting shows up quickly.
_FREEBUSY_TTL_SECONDS = 20
_TOKEN_EXPIRY_MARGIN_SECONDS = 60

_lock = threading.Lock()
_token_cache: dict[str, tuple[str, float]] = {}          # owner_id -> (token, expires_at)
_freebusy_cache: dict[tuple, tuple[list, float]] = {}    # key -> (busy, expires_at)


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def get_connection(db: Database, owner_id: str) -> dict | None:
    """The stored Google Calendar grant for a host, if they connected one."""
    return db.integrations.find_one({"owner_id": owner_id, "key": INTEGRATION_KEY})


def is_connected(db: Database, owner_id: str) -> bool:
    doc = get_connection(db, owner_id)
    return bool(doc and doc.get("config", {}).get("refresh_token"))


def _access_token(db: Database, owner_id: str) -> str | None:
    """A valid access token for the host, refreshing only when the cache is cold."""
    now = time.time()
    with _lock:
        cached = _token_cache.get(owner_id)
        if cached and cached[1] > now:
            return cached[0]

    doc = get_connection(db, owner_id)
    refresh_token = (doc or {}).get("config", {}).get("refresh_token")
    if not refresh_token:
        return None

    try:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Calendar token refresh failed for %s: %s", owner_id, exc)
        return None

    if response.status_code != 200:
        # A revoked or expired grant never recovers on retry; drop it so the
        # host sees "disconnected" and can reconnect rather than silently
        # losing conflict checking forever.
        if response.status_code in (400, 401):
            logger.warning("Calendar grant rejected for %s; marking disconnected", owner_id)
            db.integrations.update_one(
                {"owner_id": owner_id, "key": INTEGRATION_KEY},
                {"$set": {"config.invalid": True}},
            )
        else:
            logger.warning("Calendar token refresh HTTP %s for %s", response.status_code, owner_id)
        return None

    payload = response.json()
    token = payload.get("access_token")
    if not token:
        return None

    expires_in = int(payload.get("expires_in", 3600))
    with _lock:
        _token_cache[owner_id] = (token, now + expires_in - _TOKEN_EXPIRY_MARGIN_SECONDS)
    return token


def get_busy_ranges(
    db: Database, owner_id: str, start_utc: datetime, end_utc: datetime
) -> list[tuple[datetime, datetime]]:
    """Busy intervals from the host's Google Calendar, as naive UTC pairs.

    Returns an empty list when no calendar is connected or the lookup fails —
    see the module docstring on why this fails open.
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        return []

    key = (owner_id, start_utc, end_utc)
    now = time.time()
    with _lock:
        cached = _freebusy_cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

    token = _access_token(db, owner_id)
    if not token:
        return []

    try:
        response = httpx.post(
            GOOGLE_FREEBUSY_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "timeMin": start_utc.replace(tzinfo=timezone.utc).isoformat(),
                "timeMax": end_utc.replace(tzinfo=timezone.utc).isoformat(),
                "items": [{"id": "primary"}],
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("freeBusy request failed for %s: %s", owner_id, exc)
        return []

    if response.status_code != 200:
        logger.warning("freeBusy HTTP %s for %s", response.status_code, owner_id)
        return []

    busy: list[tuple[datetime, datetime]] = []
    calendars = response.json().get("calendars", {})
    for calendar in calendars.values():
        for period in calendar.get("busy", []):
            try:
                start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            busy.append((_naive_utc(start), _naive_utc(end)))

    with _lock:
        _freebusy_cache[key] = (busy, now + _FREEBUSY_TTL_SECONDS)
    return busy


def invalidate(owner_id: str) -> None:
    """Drop cached tokens and freeBusy answers for a host.

    Called on connect and disconnect so the change takes effect immediately
    rather than after the cache expires.
    """
    with _lock:
        _token_cache.pop(owner_id, None)
        for key in [k for k in _freebusy_cache if k[0] == owner_id]:
            _freebusy_cache.pop(key, None)
