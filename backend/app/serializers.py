"""Shared document -> API shape helpers.

Both the admin and public routers return bookings and event types, and both
need the same defaults applied for documents written by older versions of the
app. Keeping that in one place stops the two from drifting apart.
"""

from __future__ import annotations

from pymongo.database import Database

from .database import _doc, _oid

_EVENT_TYPE_DEFAULTS = {
    "is_active": True,
    "buffer_minutes": 0,
    "min_notice_hours": 0,
    "max_advance_days": 60,
    "max_bookings_per_day": 0,
    "location": "",
    "location_type": "video",
    "description": "",
    "accent_color": "#6366f1",
}


def event_type_doc(doc: dict) -> dict:
    """Normalise an event type document for API output."""
    d = _doc(doc)
    for key, default in _EVENT_TYPE_DEFAULTS.items():
        d.setdefault(key, default)
    d.setdefault("questions", [])
    d.pop("owner_id", None)
    return d


def booking_with_event_type(booking: dict, db: Database) -> dict:
    """A booking plus its embedded event type, with legacy defaults filled in."""
    b = _doc(booking)
    b.setdefault("notes", "")
    b.setdefault("meeting_url", "")
    b.setdefault("answers", [])
    b.pop("owner_id", None)
    b.pop("manage_token", None)

    event_type = None
    try:
        event_type = db.event_types.find_one({"_id": _oid(b.get("event_type_id", ""))})
    except ValueError:
        event_type = None

    if event_type:
        b["event_type"] = event_type_doc(event_type)
    else:
        # The event type was deleted but the booking survived — synthesise a
        # placeholder so the response still validates instead of 500-ing.
        b["event_type"] = {
            "id": b.get("event_type_id", ""),
            "title": "Deleted event type",
            "duration": max(
                5,
                int((b["end_time"] - b["start_time"]).total_seconds() // 60)
                if b.get("end_time") and b.get("start_time")
                else 30,
            ),
            "url_slug": "deleted",
            "created_at": b.get("created_at"),
            **_EVENT_TYPE_DEFAULTS,
            "is_active": False,
            "questions": [],
        }
    return b
