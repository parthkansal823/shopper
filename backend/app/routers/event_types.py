from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pymongo.database import Database

from ..database import get_db, _oid
from ..schemas import DashboardSummary, EventTypeCreate, EventTypeRead, EventTypeUpdate
from ..security import require_owner_id
from ..serializers import event_type_doc
from ..services.email_service import send_email_background

router = APIRouter(prefix="/api", tags=["event-types"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _owned_event_type(db: Database, event_type_id: str, owner_id: str) -> dict:
    """Fetch an event type the caller owns, or 404.

    A 404 (not 403) for someone else's id keeps the endpoint from confirming
    that an id exists.
    """
    try:
        oid = _oid(event_type_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Event type not found.")

    doc = db.event_types.find_one({"_id": oid, "owner_id": owner_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Event type not found.")
    return doc


@router.get("/event-types", response_model=list[EventTypeRead])
def list_event_types(
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    docs = db.event_types.find({"owner_id": owner_id}, sort=[("created_at", -1)])
    return [event_type_doc(d) for d in docs]


@router.post("/event-types", response_model=EventTypeRead, status_code=status.HTTP_201_CREATED)
def create_event_type(
    payload: EventTypeCreate,
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    # Slugs are globally unique so public /book/<slug> links need no username.
    if db.event_types.find_one({"url_slug": payload.url_slug}):
        raise HTTPException(status_code=409, detail="That link is already taken. Try another slug.")

    doc = payload.model_dump()
    doc["owner_id"] = owner_id
    doc["created_at"] = _utcnow()
    result = db.event_types.insert_one(doc)
    return event_type_doc(db.event_types.find_one({"_id": result.inserted_id}))


@router.put("/event-types/{event_type_id}", response_model=EventTypeRead)
def update_event_type(
    event_type_id: str,
    payload: EventTypeUpdate,
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    existing = _owned_event_type(db, event_type_id, owner_id)

    if db.event_types.find_one({"url_slug": payload.url_slug, "_id": {"$ne": existing["_id"]}}):
        raise HTTPException(status_code=409, detail="That link is already taken. Try another slug.")

    db.event_types.update_one({"_id": existing["_id"]}, {"$set": payload.model_dump()})
    return event_type_doc(db.event_types.find_one({"_id": existing["_id"]}))


@router.delete("/event-types/{event_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event_type(
    event_type_id: str,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    event_type = _owned_event_type(db, event_type_id, owner_id)

    upcoming = list(db.bookings.find({
        "owner_id": owner_id,
        "event_type_id": event_type_id,
        "status": "confirmed",
        "start_time": {"$gte": _utcnow()},
    }))
    for booking in upcoming:
        background_tasks.add_task(
            send_email_background,
            action="cancelled",
            recipient=booking["booker_email"],
            event_title=event_type["title"],
            start_time=booking["start_time"].strftime("%A, %B %d, %Y at %I:%M %p"),
            meeting_url=None,
        )

    db.bookings.delete_many({"owner_id": owner_id, "event_type_id": event_type_id})
    db.event_types.delete_one({"_id": event_type["_id"]})


@router.patch("/event-types/{event_type_id}/toggle", response_model=EventTypeRead)
def toggle_event_type(
    event_type_id: str,
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    event_type = _owned_event_type(db, event_type_id, owner_id)
    db.event_types.update_one(
        {"_id": event_type["_id"]},
        {"$set": {"is_active": not event_type.get("is_active", True)}},
    )
    return event_type_doc(db.event_types.find_one({"_id": event_type["_id"]}))


@router.post("/event-types/{event_type_id}/duplicate", response_model=EventTypeRead,
             status_code=status.HTTP_201_CREATED)
def duplicate_event_type(
    event_type_id: str,
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    source = _owned_event_type(db, event_type_id, owner_id)

    base_slug = f"{source['url_slug']}-copy"
    slug = base_slug
    counter = 2
    while db.event_types.find_one({"url_slug": slug}):
        slug = f"{base_slug}-{counter}"
        counter += 1

    copy = {
        "owner_id": owner_id,
        "title": f"{source['title']} (Copy)",
        "description": source.get("description", ""),
        "duration": source["duration"],
        "url_slug": slug,
        "accent_color": source.get("accent_color", "#6366f1"),
        "is_active": False,
        "buffer_minutes": source.get("buffer_minutes", 0),
        "min_notice_hours": source.get("min_notice_hours", 0),
        "max_advance_days": source.get("max_advance_days", 60),
        "max_bookings_per_day": source.get("max_bookings_per_day", 0),
        "location": source.get("location", ""),
        "location_type": source.get("location_type", "video"),
        "questions": source.get("questions", []),
        "created_at": _utcnow(),
    }
    result = db.event_types.insert_one(copy)
    return event_type_doc(db.event_types.find_one({"_id": result.inserted_id}))


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    db: Database = Depends(get_db),
    owner_id: str = Depends(require_owner_id),
):
    now = _utcnow()
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=7)

    scope = {"owner_id": owner_id}
    return DashboardSummary(
        event_types_count=db.event_types.count_documents(scope),
        upcoming_bookings_count=db.bookings.count_documents(
            {**scope, "start_time": {"$gte": now}, "status": "confirmed"}
        ),
        past_bookings_count=db.bookings.count_documents(
            {**scope, "start_time": {"$lt": now}, "status": "confirmed"}
        ),
        this_week_count=db.bookings.count_documents(
            {**scope, "start_time": {"$gte": week_start, "$lt": week_end}, "status": "confirmed"}
        ),
        total_bookings_count=db.bookings.count_documents(scope),
    )
