"""OTP issuance and verification — MongoDB backend."""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo.database import Database

from ..config import settings
from . import email_service
from .email_service import send_otp_email

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


@dataclass
class OtpRequestResult:
    sent: bool
    expires_in_seconds: int
    resend_after_seconds: int
    error: Optional[str] = None
    dev_code: Optional[str] = None


@dataclass
class OtpVerifyResult:
    ok: bool
    token: Optional[str] = None
    expires_in_seconds: int = 0
    error: Optional[str] = None


def request_otp(db: Database, email: str) -> OtpRequestResult:
    email = _normalize_email(email)
    now = _utcnow()

    rate_window_start = now - timedelta(seconds=settings.OTP_RATE_LIMIT_SECONDS)
    recent = db.email_otps.find_one(
        {"email": email, "created_at": {"$gte": rate_window_start}},
        sort=[("created_at", -1)],
    )
    if recent is not None:
        wait = settings.OTP_RATE_LIMIT_SECONDS - int((now - recent["created_at"]).total_seconds())
        return OtpRequestResult(
            sent=False,
            expires_in_seconds=settings.OTP_TTL_SECONDS,
            resend_after_seconds=max(1, wait),
            error="Please wait before requesting another code.",
        )

    code = _generate_code()
    expires_at = now + timedelta(seconds=settings.OTP_TTL_SECONDS)

    otp_doc = {
        "email": email,
        "code_hash": _hash_code(code),
        "expires_at": expires_at,
        "attempts": 0,
        "used": False,
        "created_at": now,
    }
    result = db.email_otps.insert_one(otp_doc)
    otp_id = result.inserted_id

    delivered = send_otp_email(recipient=email, code=code, ttl_seconds=settings.OTP_TTL_SECONDS)
    if not delivered:
        db.email_otps.update_one({"_id": otp_id}, {"$set": {"used": True}})
        return OtpRequestResult(
            sent=False,
            expires_in_seconds=settings.OTP_TTL_SECONDS,
            resend_after_seconds=settings.OTP_RATE_LIMIT_SECONDS,
            error="Failed to send verification email. Please try again.",
        )

    # In development with console fallback, surface the code so devs can test
    dev_code = code if email_service.active_transport() == "console" else None
    return OtpRequestResult(
        sent=True,
        expires_in_seconds=settings.OTP_TTL_SECONDS,
        resend_after_seconds=settings.OTP_RATE_LIMIT_SECONDS,
        dev_code=dev_code,
    )


def verify_otp(db: Database, email: str, code: str) -> OtpVerifyResult:
    email = _normalize_email(email)
    code = code.strip()
    now = _utcnow()

    otp = db.email_otps.find_one(
        {"email": email, "used": False},
        sort=[("created_at", -1)],
    )
    if otp is None:
        return OtpVerifyResult(ok=False, error="No active code. Please request a new one.")

    if otp["expires_at"] <= now:
        db.email_otps.update_one({"_id": otp["_id"]}, {"$set": {"used": True}})
        return OtpVerifyResult(ok=False, error="Code expired. Please request a new one.")

    if otp["attempts"] >= settings.OTP_MAX_ATTEMPTS:
        db.email_otps.update_one({"_id": otp["_id"]}, {"$set": {"used": True}})
        return OtpVerifyResult(ok=False, error="Too many attempts. Please request a new code.")

    new_attempts = otp["attempts"] + 1
    db.email_otps.update_one({"_id": otp["_id"]}, {"$set": {"attempts": new_attempts}})

    if otp["code_hash"] != _hash_code(code):
        remaining = settings.OTP_MAX_ATTEMPTS - new_attempts
        if remaining <= 0:
            db.email_otps.update_one({"_id": otp["_id"]}, {"$set": {"used": True}})
            return OtpVerifyResult(ok=False, error="Too many attempts. Please request a new code.")
        return OtpVerifyResult(ok=False, error=f"Incorrect code. {remaining} attempt(s) left.")

    # Success — burn OTP and mint verification token
    db.email_otps.update_one({"_id": otp["_id"]}, {"$set": {"used": True}})

    token_value = secrets.token_hex(32)
    db.verification_tokens.insert_one({
        "token": token_value,
        "email": email,
        "expires_at": now + timedelta(seconds=settings.VERIFICATION_TOKEN_TTL_SECONDS),
        "consumed_at": None,
        "created_at": now,
    })

    return OtpVerifyResult(
        ok=True,
        token=token_value,
        expires_in_seconds=settings.VERIFICATION_TOKEN_TTL_SECONDS,
    )


def consume_verification_token(db: Database, token_value: str, email: str) -> bool:
    email = _normalize_email(email)
    now = _utcnow()

    token = db.verification_tokens.find_one({"token": token_value})
    if token is None:
        return False
    if token.get("consumed_at") is not None:
        return False
    if token["email"] != email:
        return False
    if token["expires_at"] <= now:
        return False

    db.verification_tokens.update_one(
        {"_id": token["_id"]}, {"$set": {"consumed_at": now}}
    )
    return True
