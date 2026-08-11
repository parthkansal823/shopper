"""Public OTP endpoints used by the booker before they're allowed to /book."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.database import Database

from ..config import settings
from ..database import get_db
from ..schemas import OtpRequest, OtpRequestResponse, OtpVerify, OtpVerifyResponse
from ..services import email_service, otp_service
from ..services.rate_limit import check_rate_limit, client_ip

router = APIRouter(prefix="/api/public/otp", tags=["otp"])


@router.post("/request", response_model=OtpRequestResponse)
def request_code(payload: OtpRequest, request: Request, db: Database = Depends(get_db)):
    # Per-email throttling lives in otp_service; this caps how many distinct
    # addresses one client can blast codes at.
    check_rate_limit(
        db,
        bucket="otp_request",
        identifier=client_ip(request),
        limit=settings.RATE_LIMIT_OTP,
        window_seconds=settings.RATE_LIMIT_OTP_WINDOW,
    )

    # Resolved, not configured: a Gmail account connected in the UI enables
    # sending even when no SMTP_* values are set.
    if email_service.active_transport() in {"disabled"}:
        raise HTTPException(
            status_code=503,
            detail="Email service is not configured. Please contact the organiser.",
        )
    result = otp_service.request_otp(db, payload.email)
    if not result.sent:
        code = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if result.error and "wait" in result.error.lower()
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=code, detail=result.error or "Could not send code.")
    return OtpRequestResponse(
        sent=True,
        expires_in_seconds=result.expires_in_seconds,
        resend_after_seconds=result.resend_after_seconds,
        dev_code=result.dev_code,
    )


@router.post("/verify", response_model=OtpVerifyResponse)
def verify_code(payload: OtpVerify, request: Request, db: Database = Depends(get_db)):
    check_rate_limit(
        db,
        bucket="otp_verify",
        identifier=client_ip(request),
        limit=settings.RATE_LIMIT_OTP * 3,
        window_seconds=settings.RATE_LIMIT_OTP_WINDOW,
    )

    result = otp_service.verify_otp(db, payload.email, payload.code)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Invalid code.")
    return OtpVerifyResponse(
        verification_token=result.token,
        expires_in_seconds=result.expires_in_seconds,
    )
