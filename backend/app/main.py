import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .database import ensure_indexes, get_db
from .migrations import run_migrations
from .routers import (
    auth,
    availability,
    blockouts,
    bookings,
    calendar,
    event_types,
    integrations,
    otp,
    public,
    workflows,
)
from .seed import seed_database
from .services import email_service
from .services.scheduler import scheduler_loop

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("schedulr")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (env=%s)", settings.APP_NAME, settings.APP_ENV)

    problems = settings.validation_errors()
    if problems:
        for problem in problems:
            logger.critical("Configuration error: %s", problem)
        raise RuntimeError(
            "Refusing to start with an unsafe production configuration. "
            "Fix the errors above."
        )

    db = get_db()

    if settings.RUN_MIGRATIONS_ON_STARTUP:
        try:
            run_migrations(db)
        except Exception:
            logger.exception("Migrations failed")

    try:
        ensure_indexes(db)
        logger.info("MongoDB indexes ready")
    except Exception:
        logger.exception("Failed to create indexes")

    if settings.SEED_ON_STARTUP:
        try:
            seed_database(db)
        except Exception:
            logger.exception("Seeding failed")

    if settings.email_delivery_mode == "smtp":
        logger.info("SMTP configured: %s at %s", settings.SMTP_USER, settings.SMTP_HOST)
    elif settings.email_delivery_mode == "console":
        logger.warning("SMTP not configured. Using console email fallback in %s.", settings.APP_ENV)
    else:
        logger.warning("SMTP not configured and console fallback disabled. Email unavailable.")

    scheduler_task: asyncio.Task | None = None
    if settings.REMINDER_SCHEDULER_ENABLED:
        scheduler_task = asyncio.create_task(scheduler_loop())

    yield

    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
    logger.info("Shutting down %s", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
    # The interactive docs expose every endpoint and schema; keep them off in
    # production where they serve no one but an attacker mapping the API.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,  # auth travels in the Authorization header, not cookies
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s: %r", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@app.get("/", include_in_schema=False)
def root():
    return {"name": settings.APP_NAME, "env": settings.APP_ENV}


@app.api_route("/health", methods=["GET", "HEAD"], tags=["meta"])
def health_check():
    """Liveness probe. Also the endpoint the keep-alive cron pings."""
    db_ok = True
    try:
        get_db().command("ping")
    except Exception:
        logger.exception("MongoDB health check failed")
        db_ok = False

    payload = {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "email_mode": email_service.active_transport(),
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=payload)


app.include_router(auth.router)
app.include_router(event_types.router)
app.include_router(availability.router)
app.include_router(bookings.router)
app.include_router(public.router)
app.include_router(blockouts.router)
app.include_router(otp.router)
app.include_router(integrations.router)
app.include_router(calendar.router)
app.include_router(calendar.public_router)
app.include_router(workflows.router)
