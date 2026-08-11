"""Authentication routes: password login, Google OAuth2, JWT token management."""

import logging
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ..config import settings
from ..database import get_db
from ..migrations import claim_orphaned_data
from ..security import (
    API_KEY_PREFIX,
    create_access_token,
    hash_api_key,
    hash_password,
    require_owner_id,
    require_user,
    verify_password,
)
from ..services import calendar_sync
from ..services.rate_limit import check_rate_limit, client_ip

logger = logging.getLogger("schedulr.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
RESERVED_USERNAMES = {
    "admin", "api", "app", "auth", "book", "dashboard", "login", "logout",
    "manage", "profile", "settings", "signup", "register", "support", "www",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _public_user(user: dict) -> dict:
    """The user shape returned to the client. Never includes secrets."""
    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "name": user.get("name", ""),
        "avatar_url": user.get("avatar_url", ""),
        "oauth_provider": user.get("oauth_provider"),
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
        "bio": user.get("bio", ""),
        "title": user.get("title", ""),
        "company": user.get("company", ""),
        "website": user.get("website", ""),
        "twitter": user.get("twitter", ""),
        "linkedin": user.get("linkedin", ""),
        "avatar_color": user.get("avatar_color", "#18181b"),
        "welcome_message": user.get("welcome_message", ""),
        "booking_username": user.get("booking_username", ""),
        "has_password": bool(user.get("hashed_password")),
    }


def _token_response(user: dict) -> dict:
    return {
        "access_token": create_access_token(
            {"sub": str(user["_id"]), "email": user["email"]}
        ),
        "token_type": "bearer",
        "user": _public_user(user),
    }


def _get_or_create_oauth_user(db: Database, *, email: str, name: str, avatar_url: str,
                              provider: str, provider_id: str) -> dict:
    email = email.strip().lower()
    now = _utcnow()
    user = db.users.find_one({"email": email})

    if user:
        db.users.update_one({"_id": user["_id"]}, {"$set": {
            "name": name or user.get("name", ""),
            "avatar_url": avatar_url or user.get("avatar_url", ""),
            "oauth_provider": provider,
            "oauth_provider_id": provider_id,
            "last_login": now,
        }})
        return db.users.find_one({"_id": user["_id"]})

    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="New account registration is disabled.")

    result = db.users.insert_one({
        "email": email,
        "name": name or email.split("@")[0],
        "avatar_url": avatar_url,
        "hashed_password": None,
        "oauth_provider": provider,
        "oauth_provider_id": provider_id,
        "is_active": True,
        "created_at": now,
        "last_login": now,
    })
    claim_orphaned_data(db, str(result.inserted_id))
    return db.users.find_one({"_id": result.inserted_id})


# ------------------------------------------------------------------ schemas --

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., max_length=128)


class ProfileUpdate(BaseModel):
    name: str = Field(default="", max_length=120)
    bio: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=120)
    company: str = Field(default="", max_length=120)
    website: str = Field(default="", max_length=200)
    twitter: str = Field(default="", max_length=100)
    linkedin: str = Field(default="", max_length=200)
    avatar_color: str = Field(default="#18181b", max_length=30)
    welcome_message: str = Field(default="", max_length=500)
    booking_username: str = Field(default="", max_length=40)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


# ------------------------------------------------------------------- routes --

@router.post("/register", summary="Create a new account")
def register(payload: RegisterRequest, request: Request, db: Database = Depends(get_db)):
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="New account registration is disabled.")

    check_rate_limit(
        db,
        bucket="register",
        identifier=client_ip(request),
        limit=settings.RATE_LIMIT_LOGIN,
        window_seconds=settings.RATE_LIMIT_LOGIN_WINDOW,
    )

    email = payload.email.strip().lower()
    now = _utcnow()
    doc = {
        "email": email,
        "name": payload.name.strip() or email.split("@")[0],
        "avatar_url": "",
        "hashed_password": hash_password(payload.password),
        "oauth_provider": None,
        "oauth_provider_id": None,
        "is_active": True,
        "created_at": now,
        "last_login": now,
    }

    try:
        result = db.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email already registered.")

    claim_orphaned_data(db, str(result.inserted_id))
    user = db.users.find_one({"_id": result.inserted_id})
    logger.info("New user registered: %s", email)
    return _token_response(user)


@router.post("/login", summary="Email + password login")
def login(payload: LoginRequest, request: Request, db: Database = Depends(get_db)):
    check_rate_limit(
        db,
        bucket="login",
        identifier=client_ip(request),
        limit=settings.RATE_LIMIT_LOGIN,
        window_seconds=settings.RATE_LIMIT_LOGIN_WINDOW,
    )

    email = payload.email.strip().lower()
    user = db.users.find_one({"email": email})

    # Same message and roughly the same work either way, so the response
    # doesn't reveal whether an address is registered.
    if not user or not verify_password(payload.password, user.get("hashed_password")):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="This account has been disabled.")

    db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": _utcnow()}})
    return _token_response(user)


# --------------------------------------------------------------- Google OAuth --

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get("/google", summary="Redirect to Google OAuth consent screen")
def google_login():
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}")


@router.get("/google/callback", summary="Handle Google OAuth callback")
async def google_callback(code: str, db: Database = Depends(get_db)):
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange authorization code.")

        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {token_resp.json().get('access_token')}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch user info from Google.")
        userinfo = userinfo_resp.json()

    email = userinfo.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google did not return an email address.")

    user = _get_or_create_oauth_user(
        db,
        email=email,
        name=userinfo.get("name", ""),
        avatar_url=userinfo.get("picture", ""),
        provider="google",
        provider_id=userinfo.get("sub", ""),
    )
    jwt_token = create_access_token({"sub": str(user["_id"]), "email": user["email"]})
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL.rstrip('/')}/auth/callback?token={jwt_token}"
    )


# ----------------------------------------------------- Google Calendar sync --

def _calendar_redirect(status_value: str) -> RedirectResponse:
    base = settings.FRONTEND_URL.rstrip("/")
    return RedirectResponse(url=f"{base}/integrations?calendar={status_value}")


@router.get("/google/calendar/connect", summary="Begin the Google Calendar grant")
def google_calendar_connect(
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    """Return the consent URL for read-only calendar access.

    The browser leaves with no Authorization header, so the caller is pinned to
    a single-use ``state`` row rather than trusting anything in the callback.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")

    state = secrets.token_urlsafe(32)
    db.oauth_states.insert_one({
        "state": state,
        "owner_id": owner_id,
        "purpose": "google_calendar",
        "created_at": _utcnow(),
        "expires_at": _utcnow() + timedelta(minutes=10),
    })

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_CALENDAR_REDIRECT_URI,
        "response_type": "code",
        "scope": f"openid email {calendar_sync.CALENDAR_SCOPE}",
        "access_type": "offline",
        # Without this Google withholds the refresh token on repeat grants,
        # leaving the server unable to read the calendar later.
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return {"url": f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"}


@router.get("/google/calendar/callback", summary="Store the Google Calendar grant")
async def google_calendar_callback(
    state: str,
    code: str = "",
    error: str = "",
    db: Database = Depends(get_db),
):
    # Single use: consume the state whatever happens, so a leaked callback URL
    # cannot be replayed.
    row = db.oauth_states.find_one_and_delete({"state": state, "purpose": "google_calendar"})
    if not row:
        return _calendar_redirect("invalid")
    if error:
        return _calendar_redirect("denied")

    owner_id = row["owner_id"]

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_CALENDAR_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
    if token_resp.status_code != 200:
        logger.warning("Calendar code exchange failed: HTTP %s", token_resp.status_code)
        return _calendar_redirect("failed")

    payload = token_resp.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        # Google only returns one on first consent; prompt=consent should stop
        # this, but a grant made outside this flow could still land here.
        return _calendar_redirect("norefresh")

    db.integrations.update_one(
        {"owner_id": owner_id, "key": calendar_sync.INTEGRATION_KEY},
        {"$set": {
            "type": "oauth",
            "config": {"refresh_token": refresh_token, "invalid": False},
            "connected_at": _utcnow(),
        }},
        upsert=True,
    )
    calendar_sync.invalidate(owner_id)
    return _calendar_redirect("connected")


@router.get("/google/calendar/status", summary="Is a calendar connected?")
def google_calendar_status(
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    doc = calendar_sync.get_connection(db, owner_id)
    config = (doc or {}).get("config", {})
    return {
        "connected": bool(doc and config.get("refresh_token") and not config.get("invalid")),
        "needs_reconnect": bool(doc and config.get("invalid")),
        "connected_at": doc["connected_at"].isoformat() if doc and doc.get("connected_at") else None,
        "configured": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET),
    }


@router.delete("/google/calendar", summary="Disconnect Google Calendar")
def google_calendar_disconnect(
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    db.integrations.delete_one({"owner_id": owner_id, "key": calendar_sync.INTEGRATION_KEY})
    calendar_sync.invalidate(owner_id)
    return {"connected": False}


# ------------------------------------------------------------------- profile --

@router.get("/me", summary="Return the currently authenticated user")
def me(user: dict = Depends(require_user)):
    return _public_user(user)


@router.put("/profile", summary="Update the current user's profile")
def update_profile(
    payload: ProfileUpdate,
    user: dict = Depends(require_user),
    db: Database = Depends(get_db),
):
    updates = payload.model_dump()
    username = updates.pop("booking_username", "").strip().lower()

    if username:
        if len(username) < 2 or not USERNAME_PATTERN.match(username):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Booking username must be at least 2 characters, start and end with a "
                    "letter or number, and contain only lowercase letters, numbers and hyphens."
                ),
            )
        if username in RESERVED_USERNAMES:
            raise HTTPException(status_code=409, detail="That booking username is reserved.")
        if db.users.find_one({"booking_username": username, "_id": {"$ne": user["_id"]}}):
            raise HTTPException(status_code=409, detail="That booking username is already taken.")

    updates["name"] = updates["name"].strip() or user.get("name", "")
    updates["avatar_color"] = updates["avatar_color"] or "#18181b"

    operation: dict = {"$set": updates}
    if username:
        operation["$set"]["booking_username"] = username
    else:
        # Unset rather than store "" — a blank string would collide with every
        # other blank one under the unique index.
        operation["$unset"] = {"booking_username": ""}

    try:
        db.users.update_one({"_id": user["_id"]}, operation)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="That booking username is already taken.")

    return _public_user(db.users.find_one({"_id": user["_id"]}))


@router.put("/change-password", summary="Change password for email-registered users")
def change_password(
    payload: ChangePasswordRequest,
    user: dict = Depends(require_user),
    db: Database = Depends(get_db),
):
    if not user.get("hashed_password"):
        raise HTTPException(
            status_code=400,
            detail="Password change is only available for email/password accounts.",
        )
    if not verify_password(payload.current_password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"hashed_password": hash_password(payload.new_password)}},
    )
    return {"ok": True}


# ------------------------------------------------------------------ API keys --

@router.get("/api-keys", summary="List API keys (prefix only)")
def list_api_keys(user: dict = Depends(require_user)):
    return [
        {
            "prefix": k["prefix"],
            "created_at": (
                k["created_at"].isoformat()
                if isinstance(k.get("created_at"), datetime)
                else k.get("created_at")
            ),
        }
        for k in user.get("api_keys") or []
    ]


@router.post("/api-keys", summary="Generate a new API key")
def generate_api_key(user: dict = Depends(require_user), db: Database = Depends(get_db)):
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    now = _utcnow()
    entry = {"prefix": raw[:16], "hashed": hash_api_key(raw), "created_at": now}

    # One active key per account: generating a new one replaces the old.
    db.users.update_one({"_id": user["_id"]}, {"$set": {"api_keys": [entry]}})
    return {"key": raw, "prefix": entry["prefix"], "created_at": now.isoformat()}


@router.delete("/api-keys", summary="Revoke all API keys")
def revoke_api_keys(user: dict = Depends(require_user), db: Database = Depends(get_db)):
    db.users.update_one({"_id": user["_id"]}, {"$set": {"api_keys": []}})
    return {"ok": True}
