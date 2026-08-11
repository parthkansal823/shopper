import logging

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import OperationFailure

from .config import settings

logger = logging.getLogger("schedulr.database")

_client: MongoClient = MongoClient(settings.MONGODB_URI)


def get_db() -> Database:
    return _client[settings.MONGODB_DB_NAME]


def ensure_indexes(db: Database) -> None:
    """Create indexes on startup. All operations are idempotent."""
    db.users.create_index("email", unique=True)
    # Sparse: only users who set a public booking username occupy the namespace.
    db.users.create_index("booking_username", unique=True, sparse=True)

    # url_slug stays globally unique so public /book/<slug> links keep working
    # without a username segment.
    db.event_types.create_index("url_slug", unique=True)
    db.event_types.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])

    db.bookings.create_index([("owner_id", ASCENDING), ("start_time", ASCENDING)])
    db.bookings.create_index([("start_time", ASCENDING)])
    db.bookings.create_index("booker_email")
    db.bookings.create_index("event_type_id")
    db.bookings.create_index("status")
    db.bookings.create_index("manage_token", unique=True, sparse=True)

    db.availability_rules.create_index([("owner_id", ASCENDING), ("day_of_week", ASCENDING)])
    db.availability_settings.create_index("owner_id", unique=True, sparse=True)

    db.blockout_dates.create_index(
        [("owner_id", ASCENDING), ("start_date", ASCENDING), ("end_date", ASCENDING)]
    )

    db.email_otps.create_index("email")
    db.email_otps.create_index([("created_at", DESCENDING)])
    db.verification_tokens.create_index("token", unique=True)
    db.verification_tokens.create_index("email")
    db.integrations.create_index([("owner_id", ASCENDING), ("key", ASCENDING)], unique=True)
    db.workflows.create_index([("owner_id", ASCENDING), ("created_at", ASCENDING)])

    # One reminder per (booking, workflow) — this is what makes the scheduler
    # safe to run on every poll and across restarts.
    db.reminder_log.create_index(
        [("booking_id", ASCENDING), ("workflow_id", ASCENDING)], unique=True
    )

    # Last line of defence against a double book. The application checks for a
    # conflict before inserting, but two simultaneous requests can both pass
    # that check; only the database can settle the race. Partial, so cancelled
    # bookings don't reserve the slot forever.
    try:
        db.bookings.create_index(
            [("owner_id", ASCENDING), ("start_time", ASCENDING)],
            unique=True,
            partialFilterExpression={"status": "confirmed"},
            name="uniq_confirmed_slot",
        )
    except OperationFailure as exc:
        # Pre-existing duplicates would fail this. Log rather than block boot —
        # the application-level check still runs.
        logger.warning("Could not create the unique confirmed-slot index: %s", exc)

    # Mongo evicts these automatically; no cleanup job needed.
    db.rate_limits.create_index("expires_at", expireAfterSeconds=0)
    db.oauth_states.create_index("state", unique=True)
    db.oauth_states.create_index("expires_at", expireAfterSeconds=0)


# ------------------------------------------------------------------ helpers --

def _doc(document: dict | None) -> dict | None:
    """Convert MongoDB _id (ObjectId) to string id."""
    if document is None:
        return None
    d = dict(document)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    return d


def _oid(id_str: str) -> ObjectId:
    """Parse string to ObjectId, raising 404-friendly ValueError on bad input."""
    try:
        return ObjectId(id_str)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid id: {id_str!r}")
