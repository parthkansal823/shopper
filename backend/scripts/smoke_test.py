"""End-to-end smoke test for the Shopper backend.

Runs the whole API against a throwaway database: tenant isolation, the public
booking flow, invitee self-service, blockouts, CSV export and the reminder
scheduler. Nothing is mocked.

    docker run -d -p 27099:27017 --name shopper-test-mongo mongo:7
    python scripts/smoke_test.py

Set SMOKE_MONGODB_URI to point at a different server. The target database is
dropped at the start of every run, so never aim it at real data.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

SMOKE_URI = os.getenv("SMOKE_MONGODB_URI", "mongodb://127.0.0.1:27099")
SMOKE_DB = os.getenv("SMOKE_MONGODB_DB", "shopper_smoke")

# Must be set before app.config imports and calls load_dotenv().
os.environ.update({
    "MONGODB_URI": SMOKE_URI,
    "MONGODB_DB_NAME": SMOKE_DB,
    "APP_ENV": "development",
    "DEBUG": "false",
    "SECRET_KEY": "smoke-test-secret-key-that-is-long-enough-for-validation-xx",
    "SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASS": "",  # forces console email mode
    "SEED_ON_STARTUP": "false",
    "DEFAULT_TIMEZONE": "Asia/Kolkata",
    "REMINDER_SCHEDULER_ENABLED": "false",
    "RATE_LIMIT_ENABLED": "false",
    "CORS_ORIGINS": "http://localhost:5173",
    "FRONTEND_URL": "http://localhost:5173",
})

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
# The backend package root, resolved from this file rather than hard-coded, so
# the suite runs on any machine and in CI — not just where it was written.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient  # noqa: E402
from pymongo import MongoClient  # noqa: E402

MongoClient(SMOKE_URI).drop_database(SMOKE_DB)

from app.main import app  # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(f"{'PASS' if condition else 'FAIL'}  {label}" + (f"  -- {detail}" if detail and not condition else ""))


def fresh_verification_token(client, email):
    """Clear the per-email OTP cooldown, then run request+verify."""
    from app.database import get_db
    get_db().email_otps.delete_many({"email": email})
    code = client.post("/api/public/otp/request", json={"email": email}).json()["dev_code"]
    return client.post("/api/public/otp/verify", json={"email": email, "code": code}).json()["verification_token"]


with TestClient(app) as c:
    # ---------------------------------------------------------- auth --
    r = c.post("/api/auth/register", json={"email": "alice@example.com", "password": "supersecret1", "name": "Alice"})
    check("register alice", r.status_code == 200, r.text)
    alice = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = c.post("/api/auth/register", json={"email": "bob@example.com", "password": "supersecret1", "name": "Bob"})
    check("register bob", r.status_code == 200, r.text)
    bob = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = c.post("/api/auth/register", json={"email": "alice@example.com", "password": "supersecret1"})
    check("duplicate email rejected", r.status_code == 409, r.text)

    r = c.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrongpassword"})
    check("wrong password rejected", r.status_code == 401, r.text)

    r = c.post("/api/auth/login", json={"email": "alice@example.com", "password": "supersecret1"})
    check("login works (bcrypt roundtrip)", r.status_code == 200, r.text)

    # ------------------------------------------- auth is now required --
    for method, path in [
        ("get", "/api/event-types"), ("get", "/api/bookings"), ("get", "/api/availability"),
        ("get", "/api/blockouts"), ("get", "/api/workflows"), ("get", "/api/summary"),
        ("get", "/api/bookings/export.csv"),
    ]:
        r = getattr(c, method)(path)
        check(f"unauthenticated {path} -> 401", r.status_code == 401, f"got {r.status_code}")

    r = c.get("/api/event-types", headers={"Authorization": "Bearer garbage.token.here"})
    check("invalid token -> 401", r.status_code == 401, r.text)

    # ------------------------------------------------- availability --
    # Mon-Fri 10:00-13:00 and 14:00-17:00 (a lunch break => two windows/day).
    rules = []
    for day in range(5):
        rules.append({"day_of_week": day, "start_time": "10:00:00", "end_time": "13:00:00", "is_active": True})
        rules.append({"day_of_week": day, "start_time": "14:00:00", "end_time": "17:00:00", "is_active": True})
    r = c.put("/api/availability", headers=alice, json={"timezone": "Asia/Kolkata", "rules": rules})
    check("multi-window availability saved", r.status_code == 200 and len(r.json()["rules"]) == 10, r.text)

    r = c.put("/api/availability", headers=alice, json={
        "timezone": "Asia/Kolkata",
        "rules": [
            {"day_of_week": 0, "start_time": "10:00:00", "end_time": "13:00:00", "is_active": True},
            {"day_of_week": 0, "start_time": "12:00:00", "end_time": "15:00:00", "is_active": True},
        ]})
    check("overlapping windows rejected", r.status_code == 422, f"got {r.status_code}")

    r = c.put("/api/availability", headers=alice, json={"timezone": "Not/AZone", "rules": []})
    check("bad timezone rejected", r.status_code == 422, f"got {r.status_code}")

    c.put("/api/availability", headers=alice, json={"timezone": "Asia/Kolkata", "rules": rules})
    c.put("/api/availability", headers=bob, json={"timezone": "Asia/Kolkata", "rules": rules})

    # -------------------------------------------------- event types --
    et_payload = {
        "title": "Discovery Call", "description": "Intro chat", "duration": 30,
        "url_slug": "alice-discovery", "accent_color": "#18181b", "is_active": True,
        "buffer_minutes": 0, "min_notice_hours": 0, "max_advance_days": 60,
        "location": "", "location_type": "video",
        "questions": [
            {"id": "company", "label": "Company", "type": "text", "required": False, "placeholder": "", "options": []},
            {"id": "topic", "label": "Topic", "type": "textarea", "required": True, "placeholder": "", "options": []},
            {"id": "size", "label": "Team size", "type": "select", "required": False, "placeholder": "", "options": ["1-10", "11-50"]},
        ],
    }
    r = c.post("/api/event-types", headers=alice, json=et_payload)
    check("create event type with questions", r.status_code == 201, r.text)
    alice_et = r.json()
    check("questions persisted", len(alice_et["questions"]) == 3, str(alice_et.get("questions")))

    r = c.post("/api/event-types", headers=bob, json={**et_payload, "url_slug": "alice-discovery"})
    check("slug collision across tenants rejected", r.status_code == 409, f"got {r.status_code}")

    r = c.post("/api/event-types", headers=bob, json={**et_payload, "url_slug": "bob-call", "title": "Bob Call"})
    check("bob creates own event type", r.status_code == 201, r.text)
    bob_et = r.json()

    r = c.post("/api/event-types", headers=alice, json={**et_payload, "url_slug": "bad-select", "questions": [
        {"id": "x", "label": "X", "type": "select", "required": False, "placeholder": "", "options": []}]})
    check("select question without options rejected", r.status_code == 422, f"got {r.status_code}")

    # -------------------------------------------- TENANT ISOLATION --
    r = c.get("/api/event-types", headers=alice)
    slugs = {e["url_slug"] for e in r.json()}
    check("alice sees only her event types", slugs == {"alice-discovery"}, str(slugs))

    r = c.get("/api/event-types", headers=bob)
    slugs = {e["url_slug"] for e in r.json()}
    check("bob sees only his event types", slugs == {"bob-call"}, str(slugs))

    r = c.put(f"/api/event-types/{alice_et['id']}", headers=bob, json={**et_payload, "url_slug": "hijacked"})
    check("bob cannot edit alice's event type", r.status_code == 404, f"got {r.status_code}")

    r = c.delete(f"/api/event-types/{alice_et['id']}", headers=bob)
    check("bob cannot delete alice's event type", r.status_code == 404, f"got {r.status_code}")

    r = c.patch(f"/api/event-types/{alice_et['id']}/toggle", headers=bob)
    check("bob cannot toggle alice's event type", r.status_code == 404, f"got {r.status_code}")

    r = c.get("/api/summary", headers=bob)
    check("bob's summary counts only his data", r.json()["event_types_count"] == 1, r.text)

    # ------------------------------------------------ public booking --
    r = c.get("/api/public/event-types/alice-discovery")
    check("public event type resolves", r.status_code == 200, r.text)
    pub = r.json()
    check("public payload carries questions", len(pub["questions"]) == 3, str(pub))
    check("public payload carries host name", pub["host_name"] == "Alice", str(pub))

    # Find a weekday at least 3 days out.
    tz_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    probe = tz_now.date() + timedelta(days=3)
    while probe.weekday() > 4:
        probe += timedelta(days=1)
    day = probe.isoformat()

    r = c.get(f"/api/public/event-types/alice-discovery/slots?date={day}")
    check("slots returned", r.status_code == 200 and len(r.json()) > 0, r.text)
    slots = r.json()
    check("slots expose start_utc", all("start_utc" in s for s in slots), str(slots[:1]))

    times = [s["start_time"][11:16] for s in slots]
    check("lunch break respected (no 13:00/13:30 slot)", "13:00" not in times and "13:30" not in times, str(times))
    check("both windows present (10:00 and 14:00)", "10:00" in times and "14:00" in times, str(times))
    check("slot count is 12 (2 windows x 3h / 30m)", len(slots) == 12, f"got {len(slots)}: {times}")

    r = c.get(f"/api/public/event-types/alice-discovery/days?month={probe.strftime('%Y-%m')}")
    check("available-days endpoint works", r.status_code == 200 and day in r.json(), r.text[:200])

    # OTP -> book
    r = c.post("/api/public/otp/request", json={"email": "guest@example.com"})
    check("otp requested", r.status_code == 200 and r.json().get("dev_code"), r.text)
    code = r.json()["dev_code"]

    r = c.post("/api/public/otp/verify", json={"email": "guest@example.com", "code": "000000"})
    check("wrong otp rejected", r.status_code == 400, f"got {r.status_code}")

    vtoken = fresh_verification_token(c, "guest@example.com")
    check("otp verified", bool(vtoken), "no token")

    chosen = slots[2]
    booking_body = {
        "booker_name": "Guest User", "booker_email": "guest@example.com", "notes": "Hello",
        "start_time": chosen["start_utc"], "verification_token": vtoken,
        "answers": [{"question_id": "company", "label": "Company", "value": "Acme"},
                    {"question_id": "size", "label": "Team size", "value": "1-10"}],
    }
    r = c.post("/api/public/event-types/alice-discovery/book", json=booking_body)
    check("booking without required answer rejected", r.status_code == 422, f"got {r.status_code}: {r.text[:200]}")

    booking_body["answers"].append({"question_id": "topic", "label": "Topic", "value": "Pricing"})
    booking_body["verification_token"] = fresh_verification_token(c, "guest@example.com")

    r = c.post("/api/public/event-types/alice-discovery/book", json=booking_body)
    check("booking created", r.status_code == 201, r.text[:400])
    booking = r.json()
    manage_token = booking.get("manage_token")
    check("manage token issued", bool(manage_token), str(booking.keys()))
    check("answers stored", len(booking["answers"]) == 3, str(booking.get("answers")))

    r = c.post("/api/public/event-types/alice-discovery/book", json=booking_body)
    check("verification token is single-use", r.status_code == 401, f"got {r.status_code}")

    r = c.get(f"/api/public/event-types/alice-discovery/slots?date={day}")
    check("booked slot removed from availability", len(r.json()) == 11, f"got {len(r.json())}")

    # ------------------------------------- invitee self-service --
    r = c.get(f"/api/public/manage/{manage_token}")
    check("manage link resolves", r.status_code == 200, r.text[:200])
    check("manage view allows reschedule", r.json()["can_reschedule"] is True, r.text[:200])

    r = c.get("/api/public/manage/not-a-real-token")
    check("bogus manage token -> 404", r.status_code == 404, f"got {r.status_code}")

    r = c.get(f"/api/public/event-types/alice-discovery/slots?date={day}")
    new_slot = r.json()[5]
    r = c.post(f"/api/public/manage/{manage_token}/reschedule", json={"start_time": new_slot["start_utc"]})
    check("guest reschedule works", r.status_code == 200, r.text[:300])

    r = c.post(f"/api/public/manage/{manage_token}/cancel")
    check("guest cancel works", r.status_code == 200 and r.json()["status"] == "cancelled", r.text[:200])

    r = c.post(f"/api/public/manage/{manage_token}/reschedule", json={"start_time": new_slot["start_utc"]})
    check("cancelled booking cannot be rescheduled", r.status_code == 400, f"got {r.status_code}")

    # ------------------------------------------------- blockouts --
    r = c.post("/api/blockouts", headers=alice, json={"start_date": day, "end_date": (probe + timedelta(days=1)).isoformat(), "reason": "Away"})
    check("date-range blockout created", r.status_code == 201, r.text[:200])
    blockout_id = r.json()["id"]

    r = c.get(f"/api/public/event-types/alice-discovery/slots?date={day}")
    check("blocked day yields no slots", r.json() == [], f"got {len(r.json())}")

    r = c.post("/api/blockouts", headers=alice, json={"start_date": day, "reason": "Dup"})
    check("overlapping blockout rejected", r.status_code == 409, f"got {r.status_code}")

    r = c.get("/api/blockouts", headers=bob)
    check("bob sees none of alice's blockouts", r.json() == [], r.text[:200])

    r = c.delete(f"/api/blockouts/{blockout_id}", headers=bob)
    check("bob cannot delete alice's blockout", r.status_code == 404, f"got {r.status_code}")

    r = c.delete(f"/api/blockouts/{blockout_id}", headers=alice)
    check("alice deletes her blockout", r.status_code == 204, f"got {r.status_code}")

    # ------------------------------------------------ bookings API --
    r = c.get("/api/bookings", headers=alice)
    check("alice sees her booking", len(r.json()) == 1, str(len(r.json())))

    r = c.get("/api/bookings", headers=bob)
    check("bob sees no bookings (isolation)", r.json() == [], str(len(r.json())))

    r = c.get("/api/bookings?search=Guest", headers=alice)
    check("booking search matches", len(r.json()) == 1, str(len(r.json())))

    r = c.get("/api/bookings?search=NoSuchPerson", headers=alice)
    check("booking search filters out", r.json() == [], str(len(r.json())))

    r = c.get("/api/bookings/export.csv", headers=alice)
    check("csv export works", r.status_code == 200 and "Guest User" in r.text, r.text[:200])
    check("csv has header row", r.text.startswith("Booking ID,Event,Guest name"), r.text[:80])

    # -------------------------------------------------- workflows --
    r = c.post("/api/workflows", headers=alice, json={
        "name": "24h reminder", "trigger": "before_event", "action": "email_guest",
        "subject": "Reminder: {{event_title}}", "body": "See you at {{start_time}}. Manage: {{manage_url}}",
        "webhook_url": "", "active": True, "offset_minutes": 1440})
    check("reminder workflow created", r.status_code == 201, r.text[:300])

    r = c.post("/api/workflows", headers=alice, json={
        "name": "bad", "trigger": "before_event", "action": "webhook",
        "subject": "", "body": "", "webhook_url": "not-a-url", "active": True, "offset_minutes": 60})
    check("webhook workflow with bad URL rejected", r.status_code == 422, f"got {r.status_code}")

    r = c.get("/api/workflows", headers=bob)
    check("bob sees no workflows (isolation)", r.json() == [], r.text[:200])

    # ------------------------------------- reminder scheduler run --
    import asyncio
    from app.database import get_db
    from app.services.scheduler import run_due_workflows

    db = get_db()
    # A confirmed booking 23h out falls inside the 24h reminder window.
    soon = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=23)
    db.bookings.insert_one({
        "owner_id": str(db.users.find_one({"email": "alice@example.com"})["_id"]),
        "event_type_id": alice_et["id"], "booker_name": "Reminder Guest",
        "booker_email": "reminder@example.com", "notes": "", "status": "confirmed",
        "meeting_url": "https://example.com/m", "start_time": soon,
        "end_time": soon + timedelta(minutes=30), "answers": [],
        "manage_token": "reminder-token-abc", "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
    })
    sent = asyncio.run(run_due_workflows(db))
    check("scheduler dispatches due reminder", sent == 1, f"sent={sent}")
    sent_again = asyncio.run(run_due_workflows(db))
    check("scheduler does not double-send", sent_again == 0, f"sent={sent_again}")

    # ------------------------------------------------ ical feed --
    r = c.get("/api/calendar/feed", headers=alice)
    check("ical feed url issued", r.status_code == 200 and "/api/public/ical/" in r.json()["url"], r.text[:200])
    feed_token = r.json()["url"].rsplit("/", 1)[-1].replace(".ics", "")

    r = c.get(f"/api/public/ical/{feed_token}.ics")
    check("ical feed renders", r.status_code == 200 and "BEGIN:VCALENDAR" in r.text, r.text[:120])
    check("ical feed excludes other tenants", "Bob" not in r.text, "leak")

    r = c.get("/api/public/ical/guessable-username.ics")
    check("unknown ical token -> 404", r.status_code == 404, f"got {r.status_code}")

    r = c.post("/api/calendar/feed/rotate", headers=alice)
    new_token = r.json()["url"].rsplit("/", 1)[-1].replace(".ics", "")
    check("feed rotation changes token", new_token != feed_token)
    check("old feed token revoked", c.get(f"/api/public/ical/{feed_token}.ics").status_code == 404)

    # -------------------------------------------------- profile --
    r = c.put("/api/auth/profile", headers=alice, json={"name": "Alice A", "booking_username": "alice"})
    check("profile username set", r.status_code == 200 and r.json()["booking_username"] == "alice", r.text[:200])

    r = c.put("/api/auth/profile", headers=bob, json={"name": "Bob B", "booking_username": "alice"})
    check("duplicate username rejected", r.status_code == 409, f"got {r.status_code}")

    r = c.put("/api/auth/profile", headers=bob, json={"name": "Bob B", "booking_username": "admin"})
    check("reserved username rejected", r.status_code == 409, f"got {r.status_code}")

    r = c.put("/api/auth/profile", headers=bob, json={"name": "Bob B", "booking_username": ""})
    check("blank username allowed for multiple users", r.status_code == 200, r.text[:200])
    r = c.put("/api/auth/profile", headers=alice, json={"name": "Alice A", "booking_username": ""})
    check("second blank username also allowed (no unique-index clash)", r.status_code == 200, r.text[:200])

    # -------------------------------------------------- api keys --
    r = c.post("/api/auth/api-keys", headers=alice)
    check("api key generated", r.status_code == 200 and r.json()["key"].startswith("sk_live_"), r.text[:200])
    api_key = r.json()["key"]

    r = c.get("/api/event-types", headers={"Authorization": f"Bearer {api_key}"})
    check("api key authenticates", r.status_code == 200, r.text[:200])

    c.delete("/api/auth/api-keys", headers=alice)
    r = c.get("/api/event-types", headers={"Authorization": f"Bearer {api_key}"})
    check("revoked api key rejected", r.status_code == 401, f"got {r.status_code}")

print("\n" + "=" * 60)
print(f"PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("\nFailures:")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
