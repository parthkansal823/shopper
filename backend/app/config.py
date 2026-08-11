import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_SECRET_KEY = "change-me-in-production-use-a-long-random-string"


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list_env(name: str, default: List[str] | None = None) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return default or []
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    # ----- App metadata -----
    APP_NAME: str = os.getenv("APP_NAME", "Shopper API")
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = _bool_env("DEBUG", default=False)

    # ----- MongoDB -----
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "schedulr")

    # ----- CORS -----
    CORS_ORIGINS: List[str] = _list_env(
        "CORS_ORIGINS",
        default=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://shopper-ap823.netlify.app",
        ],
    )

    # ----- Behaviour toggles -----
    SEED_ON_STARTUP: bool = _bool_env("SEED_ON_STARTUP", default=False)
    ALLOW_REGISTRATION: bool = _bool_env("ALLOW_REGISTRATION", default=True)
    RUN_MIGRATIONS_ON_STARTUP: bool = _bool_env("RUN_MIGRATIONS_ON_STARTUP", default=True)
    REMINDER_SCHEDULER_ENABLED: bool = _bool_env("REMINDER_SCHEDULER_ENABLED", default=True)
    REMINDER_POLL_SECONDS: int = int(os.getenv("REMINDER_POLL_SECONDS", "60"))

    # ----- Defaults -----
    DEFAULT_TIMEZONE: str = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")

    # ----- SMTP -----
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASS: str = os.getenv("SMTP_PASS", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "")
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "Shopper")
    SMTP_TIMEOUT_SECONDS: int = int(os.getenv("SMTP_TIMEOUT_SECONDS", "10"))
    SMTP_RETRY_COUNT: int = int(os.getenv("SMTP_RETRY_COUNT", "1"))

    # ----- HTTPS email APIs -----
    # Set one of these when the host blocks outbound SMTP. Either takes
    # precedence over SMTP_*; SMTP_FROM / SMTP_FROM_NAME still supply the
    # sender identity, and that address must be a verified sender with the
    # provider or the send is rejected.
    SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")

    # ----- Auth / JWT -----
    SECRET_KEY: str = os.getenv("SECRET_KEY", DEFAULT_SECRET_KEY)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    # ----- Rate limiting (requests per window, per client IP) -----
    RATE_LIMIT_ENABLED: bool = _bool_env("RATE_LIMIT_ENABLED", default=True)
    RATE_LIMIT_LOGIN: int = int(os.getenv("RATE_LIMIT_LOGIN", "10"))
    RATE_LIMIT_LOGIN_WINDOW: int = int(os.getenv("RATE_LIMIT_LOGIN_WINDOW", "300"))
    RATE_LIMIT_BOOKING: int = int(os.getenv("RATE_LIMIT_BOOKING", "10"))
    RATE_LIMIT_BOOKING_WINDOW: int = int(os.getenv("RATE_LIMIT_BOOKING_WINDOW", "3600"))
    RATE_LIMIT_OTP: int = int(os.getenv("RATE_LIMIT_OTP", "8"))
    RATE_LIMIT_OTP_WINDOW: int = int(os.getenv("RATE_LIMIT_OTP_WINDOW", "3600"))

    # ----- Public URLs -----
    # FRONTEND_URL builds the invitee's manage/reschedule links; API_PUBLIC_URL
    # builds the iCal subscription URL and every OAuth callback below. Both must
    # be reachable from outside.
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    # Render injects RENDER_EXTERNAL_URL with the service's real public URL, so
    # fall back to it before localhost. That makes every OAuth callback and the
    # iCal feed URL correct on a fresh deploy with nothing configured by hand —
    # the failure it prevents is silent, because a localhost callback looks fine
    # in the logs and only dies at Google's redirect_uri check.
    API_PUBLIC_URL: str = (
        os.getenv("API_PUBLIC_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or "http://localhost:8000"
    )

    # ----- Google OAuth -----
    # Three separate grants — signing in, reading a calendar, sending mail — so
    # a host can sign in without handing over their mailbox. Each needs its own
    # callback registered in the Google console.
    #
    # The defaults are derived from API_PUBLIC_URL rather than hard-coded to
    # localhost. A literal localhost default is silently wrong in production:
    # the server sends Google a localhost redirect_uri and every attempt dies
    # with redirect_uri_mismatch, no matter what is registered. Deriving them
    # means a deployment that sets API_PUBLIC_URL is correct by default, and
    # the explicit variables remain available to override.
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "") or (
        f"{API_PUBLIC_URL.rstrip('/')}/api/auth/google/callback"
    )
    GOOGLE_CALENDAR_REDIRECT_URI: str = os.getenv("GOOGLE_CALENDAR_REDIRECT_URI", "") or (
        f"{API_PUBLIC_URL.rstrip('/')}/api/auth/google/calendar/callback"
    )
    GOOGLE_GMAIL_REDIRECT_URI: str = os.getenv("GOOGLE_GMAIL_REDIRECT_URI", "") or (
        f"{API_PUBLIC_URL.rstrip('/')}/api/auth/google/gmail/callback"
    )

    # ----- OTP -----
    OTP_TTL_SECONDS: int = int(os.getenv("OTP_TTL_SECONDS", "600"))
    OTP_RATE_LIMIT_SECONDS: int = int(os.getenv("OTP_RATE_LIMIT_SECONDS", "60"))
    OTP_MAX_ATTEMPTS: int = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
    VERIFICATION_TOKEN_TTL_SECONDS: int = int(os.getenv("VERIFICATION_TOKEN_TTL_SECONDS", "900"))

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def smtp_configured(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_USER and self.SMTP_PASS)

    @property
    def http_email_provider(self) -> str:
        """Which HTTPS email API is configured, if any.

        Preferred over SMTP wherever both are set: hosting providers commonly
        block outbound SMTP ports (25/465/587) to curb spam, and an HTTPS API
        goes out over 443 like any other request.
        """
        if self.SENDGRID_API_KEY:
            return "sendgrid"
        if self.RESEND_API_KEY:
            return "resend"
        return ""

    @property
    def email_delivery_mode(self) -> str:
        if self.http_email_provider:
            return self.http_email_provider
        if self.smtp_configured:
            return "smtp"
        if not self.is_production:
            return "console"
        return "disabled"

    @property
    def email_enabled(self) -> bool:
        return self.email_delivery_mode in {"smtp", "console"}

    def validation_errors(self) -> List[str]:
        """Misconfigurations that must block a production boot."""
        problems: List[str] = []
        if not self.is_production:
            return problems

        if self.SECRET_KEY == DEFAULT_SECRET_KEY or len(self.SECRET_KEY) < 32:
            problems.append(
                "SECRET_KEY is unset, default, or shorter than 32 characters. "
                "Every JWT would be forgeable. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if not self.MONGODB_URI or "localhost" in self.MONGODB_URI or "127.0.0.1" in self.MONGODB_URI:
            problems.append(
                "MONGODB_URI points at localhost in production. Set it to your "
                "MongoDB Atlas connection string."
            )
        if not self.CORS_ORIGINS:
            problems.append("CORS_ORIGINS is empty. Set it to your frontend's public URL.")
        if any(origin == "*" for origin in self.CORS_ORIGINS):
            problems.append("CORS_ORIGINS contains '*', which is unsafe in production.")
        return problems


settings = Settings()
