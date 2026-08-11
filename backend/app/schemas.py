from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

QuestionType = Literal["text", "textarea", "select", "checkbox", "phone"]


class BookingQuestion(BaseModel):
    """A custom question the host adds to an event type's booking form."""

    id: str = Field(..., min_length=1, max_length=40)
    label: str = Field(..., min_length=1, max_length=160)
    type: QuestionType = "text"
    required: bool = False
    placeholder: str = Field(default="", max_length=120)
    options: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _select_needs_options(self) -> "BookingQuestion":
        if self.type == "select" and not self.options:
            raise ValueError("A dropdown question needs at least one option.")
        return self


class BookingAnswer(BaseModel):
    question_id: str = Field(..., max_length=40)
    label: str = Field(default="", max_length=160)
    value: str = Field(default="", max_length=1000)


class EventTypeBase(BaseModel):
    title: str = Field(..., min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    duration: int = Field(..., ge=5, le=480)
    url_slug: str = Field(..., min_length=2, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    accent_color: str = Field(default="#6366f1", max_length=30)
    is_active: bool = True
    buffer_minutes: int = Field(default=0, ge=0, le=120)
    min_notice_hours: int = Field(default=0, ge=0, le=168)
    max_advance_days: int = Field(default=60, ge=1, le=365)
    # 0 means unlimited. Caps how many times this event type can be booked on
    # one host-local day, so a busy link can't swallow a whole working day.
    max_bookings_per_day: int = Field(default=0, ge=0, le=50)
    location: str = Field(default="", max_length=255)
    location_type: str = Field(default="video", max_length=32)
    questions: list[BookingQuestion] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _question_ids_unique(self) -> "EventTypeBase":
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Question ids must be unique within an event type.")
        return self


class EventTypeCreate(EventTypeBase):
    pass


class EventTypeUpdate(EventTypeBase):
    pass


class EventTypeRead(EventTypeBase):
    id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AvailabilityRuleInput(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    start_time: time
    end_time: time
    is_active: bool = True

    @model_validator(mode="after")
    def _start_before_end(self) -> "AvailabilityRuleInput":
        if self.start_time >= self.end_time:
            raise ValueError("A window's start time must be before its end time.")
        return self


class AvailabilityRuleRead(BaseModel):
    id: str
    day_of_week: int
    start_time: time
    end_time: time
    is_active: bool = True

    model_config = ConfigDict(from_attributes=True)


class AvailabilityUpdate(BaseModel):
    timezone: str = Field(..., min_length=1, max_length=64)
    rules: list[AvailabilityRuleInput] = Field(default_factory=list, max_length=42)

    @model_validator(mode="after")
    def _windows_must_not_overlap(self) -> "AvailabilityUpdate":
        by_day: dict[int, list[AvailabilityRuleInput]] = {}
        for rule in self.rules:
            if rule.is_active:
                by_day.setdefault(rule.day_of_week, []).append(rule)

        for day, windows in by_day.items():
            windows.sort(key=lambda r: r.start_time)
            for earlier, later in zip(windows, windows[1:]):
                if later.start_time < earlier.end_time:
                    raise ValueError(
                        f"Availability windows overlap on day {day}. "
                        "Each window needs its own slot of time."
                    )
        return self


class AvailabilityRead(BaseModel):
    timezone: str
    rules: list[AvailabilityRuleRead]


class BookingRead(BaseModel):
    id: str
    event_type_id: str
    booker_name: str
    booker_email: str
    notes: str
    status: str
    meeting_url: str = ""
    start_time: datetime
    end_time: datetime
    created_at: datetime
    answers: list[BookingAnswer] = Field(default_factory=list)
    event_type: EventTypeRead

    model_config = ConfigDict(from_attributes=True)


class BookingCreated(BookingRead):
    """Returned once, at creation, so the invitee can be handed their link."""

    manage_token: str = ""


class PublicEventTypeRead(BaseModel):
    id: str
    title: str
    description: str
    duration: int
    url_slug: str
    accent_color: str
    timezone: str
    location: str = ""
    location_type: str = "video"
    questions: list[BookingQuestion] = Field(default_factory=list)
    host_name: str = ""
    host_welcome_message: str = ""


class SlotRead(BaseModel):
    start_time: str
    end_time: str
    start_utc: str
    display_time: str


class BookingCreate(BaseModel):
    booker_name: str = Field(..., min_length=2, max_length=120)
    booker_email: EmailStr
    notes: str = Field(default="", max_length=500)
    start_time: datetime
    verification_token: str = Field(..., min_length=16, max_length=64)
    answers: list[BookingAnswer] = Field(default_factory=list, max_length=10)


class AdminBookingCreate(BaseModel):
    event_type_id: str = Field(..., min_length=8, max_length=64)
    booker_name: str = Field(..., min_length=2, max_length=120)
    booker_email: EmailStr
    notes: str = Field(default="", max_length=500)
    start_time: datetime
    send_email: bool = True


class OtpRequest(BaseModel):
    email: EmailStr


class OtpRequestResponse(BaseModel):
    sent: bool
    expires_in_seconds: int
    resend_after_seconds: int
    dev_code: str | None = None


class OtpVerify(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=10)


class OtpVerifyResponse(BaseModel):
    verification_token: str
    expires_in_seconds: int


class BookingReschedule(BaseModel):
    start_time: datetime


class BlockoutCreate(BaseModel):
    start_date: date
    end_date: date | None = None
    reason: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _normalize_range(self) -> "BlockoutCreate":
        if self.end_date is None:
            self.end_date = self.start_date
        if self.end_date < self.start_date:
            raise ValueError("A blockout's end date cannot be before its start date.")
        if (self.end_date - self.start_date).days > 365:
            raise ValueError("A blockout cannot span more than a year.")
        return self


class BlockoutRead(BaseModel):
    id: str
    start_date: date
    end_date: date
    reason: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DashboardSummary(BaseModel):
    event_types_count: int
    upcoming_bookings_count: int
    past_bookings_count: int
    this_week_count: int = 0
    total_bookings_count: int = 0


# ------------------------------------------------------- invitee self-service --

class ManagedBookingRead(BaseModel):
    """What an invitee sees on the page their emailed link opens."""

    id: str
    event_title: str
    event_slug: str
    duration: int
    accent_color: str = "#6366f1"
    booker_name: str
    booker_email: str
    notes: str = ""
    status: str
    meeting_url: str = ""
    start_time: datetime
    end_time: datetime
    timezone: str
    host_name: str = ""
    can_reschedule: bool = True
    can_cancel: bool = True
    answers: list[BookingAnswer] = Field(default_factory=list)
