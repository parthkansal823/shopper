from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pymongo.database import Database

from ..config import settings
from ..database import get_db, _oid
from ..schemas import (
    BookingAnswer,
    BookingCreate,
    BookingCreated,
    BookingRead,
    BookingReschedule,
    ManagedBookingRead,
    PublicEventTypeRead,
    SlotRead,
)
from ..serializers import booking_with_event_type
from ..services.booking_service import (
    find_slot_conflict,
    generate_available_days,
    generate_slots,
    get_public_event_type,
    get_timezone,
    normalize_booking_start,
    slot_is_available,
)
from ..services.email_service import send_email_background
from ..services.otp_service import consume_verification_token
from ..services.rate_limit import check_rate_limit, client_ip
from ..services.webhook_service import fire_webhooks
from ..services.workflow_service import fire_workflows as fire_workflow_actions
from .bookings import build_booking_document, event_payload_for

router = APIRouter(prefix="/api/public", tags=["public"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validate_answers(event_type: dict, submitted: list[BookingAnswer]) -> list[dict]:
    """Match submitted answers to the event type's questions.

    Answers for questions that no longer exist are dropped, and the question's
    current label is stored alongside the value so the host still sees what was
    asked even after they edit the form.
    """
    questions = event_type.get("questions") or []
    by_id = {q["id"]: q for q in questions if isinstance(q, dict) and q.get("id")}
    submitted_by_id = {a.question_id: a.value.strip() for a in submitted}

    validated: list[dict] = []
    for question_id, question in by_id.items():
        value = submitted_by_id.get(question_id, "")
        if question.get("required") and not value:
            raise HTTPException(
                status_code=422,
                detail=f"'{question.get('label', question_id)}' is required.",
            )
        options = question.get("options") or []
        if question.get("type") == "select" and value and value not in options:
            raise HTTPException(
                status_code=422,
                detail=f"'{value}' is not a valid choice for '{question.get('label', question_id)}'.",
            )
        if value:
            validated.append({
                "question_id": question_id,
                "label": question.get("label", ""),
                "value": value[:1000],
            })
    return validated


def _host_for(db: Database, owner_id: str) -> dict:
    """The host's user document, or an empty dict if it can't be resolved."""
    if not owner_id:
        return {}
    try:
        return db.users.find_one({"_id": _oid(owner_id)}) or {}
    except ValueError:
        return {}


# ------------------------------------------------------------ discovery --

@router.get("/event-types/{slug}", response_model=PublicEventTypeRead)
def get_public_event(slug: str, db: Database = Depends(get_db)):
    event_type, timezone_name = get_public_event_type(db, slug)
    if not event_type:
        raise HTTPException(status_code=404, detail="Event type not found.")

    host = _host_for(db, event_type.get("owner_id", ""))
    return PublicEventTypeRead(
        id=event_type["id"],
        title=event_type["title"],
        description=event_type.get("description", ""),
        duration=event_type["duration"],
        url_slug=event_type["url_slug"],
        accent_color=event_type.get("accent_color", "#6366f1"),
        timezone=timezone_name,
        location=event_type.get("location", ""),
        location_type=event_type.get("location_type", "video"),
        questions=event_type.get("questions", []),
        host_name=host.get("name", ""),
        host_welcome_message=host.get("welcome_message", ""),
    )


@router.get("/event-types/{slug}/slots", response_model=list[SlotRead])
def get_slots(slug: str, date: str = Query(...), db: Database = Depends(get_db)):
    event_type, _ = get_public_event_type(db, slug)
    if not event_type:
        raise HTTPException(status_code=404, detail="Event type not found.")
    try:
        requested_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date. Use YYYY-MM-DD.")
    return generate_slots(db, event_type, requested_date)


@router.get("/event-types/{slug}/days", response_model=list[str])
def get_available_days(
    slug: str,
    month: str = Query(..., description="YYYY-MM"),
    db: Database = Depends(get_db),
):
    """Which days in a month have at least one open slot.

    Lets the booking page grey out unavailable days up front instead of making
    the invitee click each one to find out.
    """
    event_type, _ = get_public_event_type(db, slug)
    if not event_type:
        raise HTTPException(status_code=404, detail="Event type not found.")

    try:
        first = datetime.strptime(f"{month}-01", "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid month. Use YYYY-MM.")

    # Last day of the month: step to the 1st of the next, then back one.
    next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = next_month - timedelta(days=1)
    return generate_available_days(db, event_type, first, last)


# -------------------------------------------------------------- booking --

@router.post(
    "/event-types/{slug}/book",
    response_model=BookingCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_booking(
    slug: str,
    payload: BookingCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Database = Depends(get_db),
):
    check_rate_limit(
        db,
        bucket="public_booking",
        identifier=client_ip(request),
        limit=settings.RATE_LIMIT_BOOKING,
        window_seconds=settings.RATE_LIMIT_BOOKING_WINDOW,
    )

    event_type, timezone_name = get_public_event_type(db, slug)
    if not event_type:
        raise HTTPException(status_code=404, detail="Event type not found.")

    owner_id = event_type.get("owner_id", "")
    if not owner_id:
        raise HTTPException(status_code=503, detail="This booking page is not fully configured.")

    if not consume_verification_token(db, payload.verification_token, payload.booker_email):
        raise HTTPException(
            status_code=401,
            detail="Email verification expired or invalid. Please verify your email again.",
        )

    answers = _validate_answers(event_type, payload.answers)

    if not slot_is_available(db, event_type, payload.start_time, timezone_name):
        raise HTTPException(status_code=400, detail="That slot is no longer available.")

    start_utc = normalize_booking_start(payload.start_time, timezone_name)
    if find_slot_conflict(db, owner_id, start_utc, event_type["duration"]):
        raise HTTPException(
            status_code=409,
            detail="That slot was just booked by someone else. Please pick another.",
        )

    booking_doc = build_booking_document(event_type, payload, start_utc, owner_id, answers)
    result = db.bookings.insert_one(booking_doc)
    booking = db.bookings.find_one({"_id": result.inserted_id})
    enriched = booking_with_event_type(booking, db)

    background_tasks.add_task(
        send_email_background,
        action="booked",
        recipient=enriched["booker_email"],
        event_title=enriched["event_type"]["title"],
        start_time=booking["start_time"].strftime("%A, %B %d, %Y at %I:%M %p"),
        meeting_url=booking.get("meeting_url"),
        manage_token=booking.get("manage_token"),
    )

    host = _host_for(db, owner_id)
    if host.get("email"):
        background_tasks.add_task(
            send_email_background,
            action="host_notified",
            recipient=host["email"],
            event_title=enriched["event_type"]["title"],
            start_time=booking["start_time"].strftime("%A, %B %d, %Y at %I:%M %p"),
            meeting_url=booking.get("meeting_url"),
            guest_name=enriched["booker_name"],
            guest_email=enriched["booker_email"],
        )

    payload_dict = event_payload_for(enriched, booking)
    background_tasks.add_task(fire_webhooks, db, owner_id, "booking.confirmed", payload_dict)
    background_tasks.add_task(fire_workflow_actions, db, owner_id, "booking.confirmed", payload_dict)

    return {**enriched, "manage_token": booking.get("manage_token", "")}


@router.get("/bookings/{booking_id}", response_model=BookingRead)
def get_booking(booking_id: str, db: Database = Depends(get_db)):
    """Confirmation-screen lookup. Returns no owner or manage token."""
    try:
        oid = _oid(booking_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Booking not found.")

    booking = db.bookings.find_one({"_id": oid})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")
    return booking_with_event_type(booking, db)


# ----------------------------------------------- invitee self-service --

def _booking_by_manage_token(db: Database, token: str) -> dict:
    booking = db.bookings.find_one({"manage_token": token})
    if not booking:
        raise HTTPException(status_code=404, detail="This booking link is invalid or has expired.")
    return booking


def _managed_view(db: Database, booking: dict) -> ManagedBookingRead:
    owner_id = booking.get("owner_id", "")
    timezone_name = get_timezone(db, owner_id)
    host = _host_for(db, owner_id)

    event_type = None
    try:
        event_type = db.event_types.find_one({"_id": _oid(booking.get("event_type_id", ""))})
    except ValueError:
        pass
    event_type = event_type or {}

    is_past = booking["start_time"] <= _utcnow()
    is_cancelled = booking.get("status") == "cancelled"

    return ManagedBookingRead(
        id=str(booking["_id"]),
        event_title=event_type.get("title", "Meeting"),
        event_slug=event_type.get("url_slug", ""),
        duration=event_type.get(
            "duration",
            max(5, int((booking["end_time"] - booking["start_time"]).total_seconds() // 60)),
        ),
        accent_color=event_type.get("accent_color", "#6366f1"),
        booker_name=booking.get("booker_name", ""),
        booker_email=booking.get("booker_email", ""),
        notes=booking.get("notes", ""),
        status=booking.get("status", "confirmed"),
        meeting_url=booking.get("meeting_url", "") if not is_cancelled else "",
        start_time=booking["start_time"],
        end_time=booking["end_time"],
        timezone=timezone_name,
        host_name=host.get("name", ""),
        can_reschedule=not is_past and not is_cancelled and bool(event_type),
        can_cancel=not is_past and not is_cancelled,
        answers=booking.get("answers", []),
    )


@router.get("/manage/{token}", response_model=ManagedBookingRead)
def get_managed_booking(token: str, db: Database = Depends(get_db)):
    """Open a booking from the link in the invitee's confirmation email.

    The token is the credential — it is unguessable, scoped to one booking,
    and grants nothing beyond viewing, cancelling or rescheduling that booking.
    """
    return _managed_view(db, _booking_by_manage_token(db, token))


@router.post("/manage/{token}/cancel", response_model=ManagedBookingRead)
def cancel_managed_booking(
    token: str,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
):
    booking = _booking_by_manage_token(db, token)

    if booking.get("status") == "cancelled":
        return _managed_view(db, booking)
    if booking["start_time"] <= _utcnow():
        raise HTTPException(status_code=400, detail="This meeting has already started.")

    db.bookings.update_one(
        {"_id": booking["_id"]},
        {"$set": {"status": "cancelled", "cancelled_at": _utcnow(), "cancelled_by": "guest"}},
    )
    booking = db.bookings.find_one({"_id": booking["_id"]})
    enriched = booking_with_event_type(booking, db)
    owner_id = booking.get("owner_id", "")

    background_tasks.add_task(
        send_email_background,
        action="cancelled",
        recipient=booking["booker_email"],
        event_title=enriched["event_type"]["title"],
        start_time=booking["start_time"].strftime("%A, %B %d, %Y at %I:%M %p"),
        meeting_url=None,
    )

    host = _host_for(db, owner_id)
    if host.get("email"):
        background_tasks.add_task(
            send_email_background,
            action="host_cancelled_by_guest",
            recipient=host["email"],
            event_title=enriched["event_type"]["title"],
            start_time=booking["start_time"].strftime("%A, %B %d, %Y at %I:%M %p"),
            guest_name=enriched["booker_name"],
            guest_email=enriched["booker_email"],
        )

    payload_dict = event_payload_for(enriched, booking, include_meeting_url=False)
    background_tasks.add_task(fire_webhooks, db, owner_id, "booking.cancelled", payload_dict)
    background_tasks.add_task(fire_workflow_actions, db, owner_id, "booking.cancelled", payload_dict)
    return _managed_view(db, booking)


@router.post("/manage/{token}/reschedule", response_model=ManagedBookingRead)
def reschedule_managed_booking(
    token: str,
    payload: BookingReschedule,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Database = Depends(get_db),
):
    check_rate_limit(
        db,
        bucket="public_reschedule",
        identifier=client_ip(request),
        limit=settings.RATE_LIMIT_BOOKING,
        window_seconds=settings.RATE_LIMIT_BOOKING_WINDOW,
    )

    booking = _booking_by_manage_token(db, token)
    if booking.get("status") == "cancelled":
        raise HTTPException(status_code=400, detail="This booking was cancelled.")
    if booking["start_time"] <= _utcnow():
        raise HTTPException(status_code=400, detail="This meeting has already started.")

    owner_id = booking.get("owner_id", "")
    event_type_raw = db.event_types.find_one({"_id": _oid(booking["event_type_id"])})
    if not event_type_raw:
        raise HTTPException(status_code=404, detail="This event type is no longer available.")

    event_type = dict(event_type_raw)
    event_type["id"] = str(event_type.pop("_id"))
    timezone_name = get_timezone(db, owner_id)

    if not slot_is_available(db, event_type, payload.start_time, timezone_name):
        raise HTTPException(status_code=400, detail="That slot is no longer available.")

    start_utc = normalize_booking_start(payload.start_time, timezone_name)
    if find_slot_conflict(
        db, owner_id, start_utc, event_type["duration"], exclude_booking_id=booking["_id"]
    ):
        raise HTTPException(status_code=409, detail="That slot was just taken. Please pick another.")

    db.bookings.update_one(
        {"_id": booking["_id"]},
        {"$set": {
            "start_time": start_utc,
            "end_time": start_utc + timedelta(minutes=event_type["duration"]),
            "rescheduled_at": _utcnow(),
            "rescheduled_by": "guest",
        }},
    )
    booking = db.bookings.find_one({"_id": booking["_id"]})
    enriched = booking_with_event_type(booking, db)

    background_tasks.add_task(
        send_email_background,
        action="rescheduled",
        recipient=booking["booker_email"],
        event_title=enriched["event_type"]["title"],
        start_time=booking["start_time"].strftime("%A, %B %d, %Y at %I:%M %p"),
        meeting_url=booking.get("meeting_url") or None,
        manage_token=booking.get("manage_token"),
    )

    host = _host_for(db, owner_id)
    if host.get("email"):
        background_tasks.add_task(
            send_email_background,
            action="host_rescheduled_by_guest",
            recipient=host["email"],
            event_title=enriched["event_type"]["title"],
            start_time=booking["start_time"].strftime("%A, %B %d, %Y at %I:%M %p"),
            guest_name=enriched["booker_name"],
            guest_email=enriched["booker_email"],
        )

    payload_dict = event_payload_for(enriched, booking)
    background_tasks.add_task(fire_webhooks, db, owner_id, "booking.rescheduled", payload_dict)
    background_tasks.add_task(
        fire_workflow_actions, db, owner_id, "booking.rescheduled", payload_dict
    )
    return _managed_view(db, booking)
