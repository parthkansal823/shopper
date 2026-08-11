# Shopper

A scheduling and booking platform in the vein of Calendly or Cal.com. Hosts
publish booking pages, invitees pick a slot and verify their email, and the
whole booking lifecycle — confirmation, reminders, reschedules, cancellations —
runs itself.

- **Frontend**: React 19 + Vite, deployed on Netlify
- **Backend**: FastAPI + MongoDB, deployed on Render
- **Database**: MongoDB Atlas

---

## 1. Features

### For the host
- **Event types** — multiple meeting templates with their own duration, slug,
  buffer, minimum notice, booking horizon and an optional **daily limit** that
  closes a day once it has taken enough bookings.
- **Google Calendar conflict checking** — connect a calendar and events already
  in it hide the overlapping slots, so Shopper can't book over a meeting it
  didn't create. Read-only, and opt-in separately from signing in.
- **Custom booking questions** — up to ten per event type (short text, long
  text, dropdown, checkbox, phone), required or optional. Answers are stored
  with the booking and included in the CSV export and calendar feed.
- **Availability with multiple windows per day** — a lunch break or split
  shift is just two windows on the same weekday. Overlaps are rejected.
- **Date-range blockouts** — block a single day or a holiday spanning weeks.
- **Bookings** — search, filter by scope (upcoming / past / cancelled), edit
  notes, reschedule, cancel in bulk, and export the current view to CSV.
- **Workflows** — automated email or webhook on booking created / cancelled /
  rescheduled, plus **time-based reminders** ("24 hours before") driven by a
  background scheduler.
- **Private calendar feed** — a token-addressed iCal URL to subscribe from
  Google Calendar, Apple Calendar or Outlook. Rotatable.
- **Integrations** — Slack, Discord, Teams and generic webhooks.
- **Analytics** — booking volume, popular slots, conversion.
- **API keys** — `sk_live_…` bearer tokens for the same endpoints as the UI.
- **First-run checklist** — the dashboard names what is still missing until the
  booking page can actually take a booking, and hides itself once it can.

### For the invitee
- **Public booking page** at `/book/<slug>` with a live calendar; days with no
  availability are greyed out before they click.
- **Timezone picker** — slots render in whatever timezone the invitee chooses,
  defaulting to their browser's. Booking is stored in UTC either way.
- **Email verification** — a 6-digit OTP, asked for on the final confirm step
  rather than mid-form, so the invitee fills everything in and is interrupted
  once. The server still refuses any booking without a valid token.
- **Self-service management** at `/manage/<token>` — reschedule or cancel from
  a link in the confirmation email, with no account and no email to the host.

---

## 2. Architecture

### Multi-tenancy
Every account is a tenant. `event_types`, `availability_settings`,
`availability_rules`, `blockout_dates`, `bookings`, `workflows` and
`integrations` all carry an `owner_id`, and every admin endpoint is scoped to
the authenticated user. Requests for another tenant's document return 404
rather than 403, so ids can't be probed.

Event-type slugs are **globally** unique, which keeps public links at
`/book/<slug>` without a username segment.

### Authentication
- `POST /api/auth/login` issues a JWT (HS256).
- API keys (`sk_live_…`) are accepted on the same `Authorization: Bearer`
  header; only a SHA-256 hash is stored.
- `app/security.py` is the single source of truth — routers depend on
  `require_user` / `require_owner_id` rather than parsing headers themselves.

### Datetime convention
MongoDB stores **naive UTC**. The host's timezone (from their availability
settings) is what working hours are interpreted in. Slots are returned with an
unambiguous `start_utc` so the browser can render them in any timezone, and
bookings are normalised back to UTC on the way in.

### Slot generation reads once per range, not once per day
`generate_slots` needs four things — the host's timezone, their weekly rules,
their blockouts and their existing bookings — and **none of them vary by day**.
Calling it in a loop therefore re-read all four on every iteration, which made a
month view cost roughly 120 round trips to Atlas.

`_load_range` reads each collection once for the whole span and
`generate_available_days` walks the days in memory, so a month costs 5 queries
instead of ~154. When adding anything that needs another collection, read it in
`_load_range` — putting a query inside `_slots_for_day` silently reintroduces
the N+1.

### What stops a slot being double-booked
Four independent layers, because each one alone has a hole:

1. **Other Shopper bookings** — busy times span *every* event type the host
   offers, so a 09:00 "Intro Call" also blocks 09:00 on "Deep Dive".
2. **The host's external calendar** — `services/calendar_sync.py` asks Google's
   freeBusy endpoint for the same span. This **fails open**: if Google is
   unreachable or the grant was revoked, the host keeps their availability
   rather than having the booking page go dark. That trades a possible double
   book for uptime, so every failure is logged, and a rejected grant marks the
   integration `invalid` so the host is told to reconnect.
3. **A daily cap** — `max_bookings_per_day` (0 = unlimited) closes the day once
   reached. Counted in the *host's* timezone, since that is the day the cap is
   about, and per event type, so one link filling up doesn't close another.
4. **A unique index** — `uniq_confirmed_slot` on `(owner_id, start_time)`,
   partial on `status: "confirmed"`. The application checks for a conflict
   before inserting, but two simultaneous requests can both pass that check;
   only the database can settle the race, and the loser gets a 409. It is
   partial so a cancelled booking stops reserving the slot.

### Reminder scheduler
`app/services/scheduler.py` polls every 60 seconds for bookings entering a
workflow's reminder window. Delivery is at-most-once per (booking, workflow),
enforced by a unique index on `reminder_log` — the insert *is* the lock, so
overlapping polls or a second instance cannot double-send. Catch-up is bounded
to two hours so a service that was asleep doesn't flood old reminders.

### Layout
```
backend/app/
  main.py           app wiring, CORS, security headers, lifespan
  config.py         env-driven settings + production validation
  security.py       password hashing, JWT, auth dependencies
  database.py       Mongo client and index management
  migrations.py     idempotent startup migrations
  serializers.py    document -> API shape helpers
  schemas.py        Pydantic request/response models
  routers/          auth, event_types, availability, bookings, blockouts,
                    public, otp, integrations, calendar, workflows
  services/         booking_service (slots), calendar_sync (Google freeBusy),
                    email, otp, webhooks, workflows, scheduler, rate_limit
  scripts/          smoke_test.py
frontend/src/
  pages/            route-level components
  components/       Toast, Skeleton, ThemeToggle, AuthContext…
  services/api.js   API client
  utils/date.js     timezone-aware formatting
  index.css         design tokens + component styles
```

---

## 3. Running locally

**Prerequisites**: Python 3.11+, Node 18+, MongoDB (local or Atlas).

**Backend**
```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit
uvicorn app.main:app --reload
```

**Frontend**
```bash
cd frontend
npm install
cp .env.example .env          # VITE_API_URL=http://127.0.0.1:8000
npm run dev
```

Leave `SMTP_*` blank in development: emails are written to the server log, and
the booking OTP is shown in the UI so you can complete a booking end to end.

**If email silently isn't arriving**, check these in order — the first is by far
the most common, and none of them raise an error:

1. **The server is running with stale settings.** `.env` is read once at
   startup, so adding `SMTP_PASS` to a running server changes nothing until you
   restart it.
2. **Gmail needs an App Password**, not your account password, and 2-Step
   Verification must be on for that option to exist. Google displays it as
   `xxxx xxxx xxxx xxxx`; the spaces are only for readability, so paste the 16
   characters without them.
3. **Check `/health`.** `"email_mode"` reports what the server actually
   resolved: `smtp` (will send), `console` (logged only — some `SMTP_*` value
   is blank), or `disabled`.
4. **Look in spam.** A brand-new Gmail sender with no SPF/DKIM alignment is
   frequently filtered, especially the OTP mail. Delivery succeeding in the log
   means Gmail accepted it for relay, not that it reached the inbox.

To test the whole path without going through the booking UI:

```bash
cd backend
python -c "from app.services.email_service import send_email_now; \
print(send_email_now(subject='Shopper test', recipient='you@example.com', \
html_body='<p>hello</p>', text_body='hello'))"
```

#### Mail that works locally but not once deployed
This is a different problem, and the usual cause is not configuration:
**many hosting providers block outbound SMTP ports (25, 465, 587)** to curb
spam. Your laptop has no such restriction, so the same settings that work in
development go silent in production.

`"email_mode":"smtp"` on `/health` does **not** rule this out — it only reports
that the settings are present, never that the host can reach the mail server.

Two ways to tell them apart:

1. **Read the deploy logs.** Failures are already logged:
   `Email delivery to … failed on attempt 1/2: <the real error>`, followed by
   `permanently failed`. A **timeout** means the port is blocked; a rejection
   means credentials or policy.
2. **`POST /api/auth/email/test`** (authenticated) runs one real send from the
   deployed environment and returns the underlying error plus a hint. It only
   ever mails the caller's own address.

```bash
curl -X POST https://<your-backend>.onrender.com/api/auth/email/test \
     -H "Authorization: Bearer <your-jwt>"
```

If the port is blocked, no amount of SMTP configuration will fix it — the mail
has to leave over HTTPS instead.

**Easiest: send through Gmail.** Integrations → **Connect Gmail**. It reuses
the Google OAuth already configured, needs no third-party account or API key,
and sends over HTTPS like any other request. The grant is scoped
`gmail.send` — permission to send and nothing else; it cannot read a message.

The connected mailbox becomes the sender, and its address overwrites the `From`
header, because Gmail refuses to send as anyone else. The grant is stored once
in `app_settings` and applies app-wide, exactly as the single `SMTP_USER` did —
it is not per-tenant. Google's own limit is roughly 500 messages a day on a
free account.

Register the third callback in the Google console:

```
https://<your-backend>.onrender.com/api/auth/google/gmail/callback
```

**Alternative: a provider's HTTPS API.** Set **one** of these and redeploy;
either takes precedence over `SMTP_*` automatically:

```
BREVO_API_KEY=xkeysib-…      # or
RESEND_API_KEY=re_…
```

`SMTP_FROM` and `SMTP_FROM_NAME` still supply the sender, and **that address
must be a verified sender with the provider** or the API rejects the send —
which the test button will tell you verbatim. Note the free tiers differ in a
way that matters for a booking app: Brevo sends to any recipient once the
sender address is verified, while Resend needs a **verified domain** before it
will mail anyone other than your own account.

`/health` reports the transport actually in use — `gmail`, `brevo`, `resend`,
`smtp`, `console` or `disabled` — resolved at request time rather than read
from configuration, so a Gmail account connected through the UI shows up there
immediately. That is the quickest confirmation the new path is live.

Precedence is: a connected Gmail account, then `BREVO_API_KEY`, then
`RESEND_API_KEY`, then `SMTP_*`. Connecting Gmail therefore takes over without
removing any existing SMTP settings, and disconnecting falls straight back to
them.

Interactive API docs are at `/docs` — disabled automatically in production.

**An empty database looks broken.** With no availability and no event types,
every page is an empty panel and `/book/<slug>` 404s — nothing is wrong. Either
follow the dashboard's setup checklist, or seed demo data (two event types, a
week of availability with a lunch break, two sample bookings):

```bash
cd backend
python -c "from app.database import get_db; from app.seed import seed_database; seed_database(get_db())"
```

It is a no-op once any event type exists, so it can't overwrite real data.

### `localhost` and `127.0.0.1` are not interchangeable here
On Windows, `localhost` usually resolves to IPv6 `::1` first while uvicorn binds
IPv4 by default, and Vite binds IPv6. Mixing them produces failures that look
like application bugs but are not:

| Symptom | Cause |
| :-- | :-- |
| `{"detail":"Not Found"}` on a route you know exists | the request reached a *different* server on that port |
| Frontend "refused to connect" on `127.0.0.1:5173` | Vite is listening on `::1` — use `localhost:5173` |
| CORS errors locally | `VITE_API_URL` origin isn't in the backend's `CORS_ORIGINS` |

If a port behaves strangely, confirm which process owns it and check *both*
families before debugging the code:

```bash
curl -s localhost:8000/openapi.json | grep -o '"title":"[^"]*"'   # who is this?
curl -s 127.0.0.1:8000/openapi.json | grep -o '"title":"[^"]*"'   # same server?
```

`CORS_ORIGINS` in `.env.example` lists both spellings for port 5173 so either
works — keep it that way.

---

## 4. Tests

```bash
docker run -d -p 27099:27017 --name shopper-test-mongo mongo:7
cd backend && python scripts/smoke_test.py
```

82 checks covering tenant isolation, auth enforcement, slot generation with
lunch breaks, the OTP and booking flow, custom questions, invitee
reschedule/cancel, blockouts, CSV export, the reminder scheduler and the iCal
feed. It drops its target database on every run, so never point
`SMOKE_MONGODB_URI` at real data.

---

## 5. Deployment

### 5.1 Database — MongoDB Atlas
1. Create a free **M0** cluster.
2. Database Access → add a user with *Read and write to any database*.
3. Network Access → allow `0.0.0.0/0` (Render's egress IPs are dynamic).
4. Copy the `mongodb+srv://…` connection string.

### 5.2 Backend — Render
Deploy from `backend/render.yaml`, then fill in the secrets marked
`sync: false` in the dashboard:

| Variable | Value |
| :-- | :-- |
| `MONGODB_URI` | the Atlas connection string |
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` | Gmail address + **App Password** |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | optional |

Then set these to your real URLs (no trailing slash):
`FRONTEND_URL`, `API_PUBLIC_URL`, `CORS_ORIGINS`, `GOOGLE_REDIRECT_URI`,
`GOOGLE_CALENDAR_REDIRECT_URI`.

The app **refuses to start in production** if `SECRET_KEY` is missing, default
or under 32 characters, if `MONGODB_URI` points at localhost, or if
`CORS_ORIGINS` is empty or `*`. A boot failure here is the app telling you a
secret is missing — check the logs rather than relaxing the check.

#### 5.2.1 Google OAuth — two redirect URIs, not one
Signing in and reading a calendar are **separate grants**, so both callbacks
must be registered under *Authorised redirect URIs* in the Google Cloud console
(APIs & Services → Credentials). Register the local pair too if you develop
against Google:

```
https://<your-backend>.onrender.com/api/auth/google/callback
https://<your-backend>.onrender.com/api/auth/google/calendar/callback
https://<your-backend>.onrender.com/api/auth/google/gmail/callback
http://localhost:8000/api/auth/google/callback
http://localhost:8000/api/auth/google/calendar/callback
http://localhost:8000/api/auth/google/gmail/callback
```

Three grants, three callbacks: signing in, reading a calendar for conflicts,
and sending mail. They are deliberately separate so a host can sign in without
handing over their calendar or mailbox.

*Authorised JavaScript origins* stays empty — the code never leaves the server,
so only redirect URIs matter. Two things that reliably cost an hour:

- A **new client defaults to "Testing"**, which blocks anyone not listed under
  OAuth consent screen → Test users. The failure looks like a generic
  "Access blocked", not a permissions message.
- Redirect URIs are matched **exactly**. A trailing space, or a console that
  helpfully upgraded `http://localhost` to `https://`, produces
  `Error 400: redirect_uri_mismatch`. Changes can also take a few minutes to
  propagate.

Calendar sync needs `GOOGLE_CLIENT_SECRET` as well as the id — the server
exchanges a refresh token offline. Leave both unset to hide the feature; the
booking page keeps working, just without conflict checking.

### 5.3 Frontend — Netlify
`netlify.toml` is committed, so Netlify picks up base, build command, publish
directory and the SPA redirect automatically. Set one environment variable:

```
VITE_API_URL = https://<your-backend>.onrender.com
```

Vite inlines this at build time, so **changing it requires a redeploy**, not
just a restart.

### 5.4 Keeping the free backend and database awake
Two different things go to sleep, on very different clocks:

| | Idles after | Wakes on a request? |
| :-- | :-- | :-- |
| **Render** free web service | ~15 minutes | Yes — but the visitor waits ~50 s |
| **Atlas** M0 cluster | ~60 days with no connections | **No** — it stays paused until someone clicks *Resume* |

One monitor covers both. `/health` is not a static response — it runs
`get_db().command("ping")`, so every check is real database activity as well as
real HTTP traffic. Keeping it pinged keeps Render warm *and* the 60-day Atlas
idle timer from ever running down.

**UptimeRobot (recommended).** Its free tier checks every 5 minutes, which is
comfortably inside Render's ~15 minute window.

- Monitor type **HTTP(S)**, URL `https://<your-backend>.onrender.com/health`
- `HEAD` is supported — `/health` is declared
  `@app.api_route(..., methods=["GET", "HEAD"])`. The handler still runs and
  still pings Mongo; only the body is dropped. `GET` works identically if you
  prefer to alert on the response body.
- **Raise the request timeout.** If the service *has* gone to sleep, the waking
  request takes ~50 s and a 30 s timeout reports a false outage. Leave a
  retry/confirmation setting on so one slow check isn't an alert.
- Because the endpoint returns **503** when Mongo is unreachable, the monitor
  doubles as a database alarm — a 503 means the API is up but the database is
  not, which is exactly when you want to know.

**GitHub Actions (alternative).** `.github/workflows/keep-alive.yml` pings
`/health` every 14 minutes and needs no third-party account. Set the repo
variable `BACKEND_URL` if your URL differs. Its weakness is that GitHub
disables scheduled workflows on repositories with no activity for 60 days — if
cold starts reappear, check the workflow is still enabled. Running both is
harmless, just redundant.

Render's free tier allows 750 instance-hours per month against a ~730-hour
month, so one always-on service fits — but there is no room for a second free
service in the same account.

### 5.5 First deploy checklist
1. Register the first account immediately — on a database that already holds
   data, the oldest account inherits it.
2. Set a booking username in **Profile**.
3. Set availability, then create an event type.
4. Open `/book/<slug>` in a private window and book a slot to confirm SMTP
   works end to end.
5. Check the confirmation email contains a working reschedule/cancel link —
   if the link points at `localhost`, `FRONTEND_URL` is wrong.
6. Optional: connect Google Calendar in **Integrations** and confirm a busy
   hour in that calendar disappears from `/book/<slug>`. This is the only way
   to prove the grant works — the connect button succeeding proves the token
   was stored, not that freeBusy is being read.

---

## 6. Upgrading an existing deployment

`app/migrations.py` runs automatically at startup and is idempotent:

- assigns pre-existing global data to the oldest account
- renames `integrations.user_id` → `owner_id`
- converts single-day blockouts to date ranges
- backfills `manage_token` on existing bookings so old bookings get
  self-service links
- clears blank `booking_username` values that would collide under the new
  unique index
- drops the legacy indexes that conflict with per-tenant uniqueness

Take an Atlas snapshot before the first deploy of this version. Set
`RUN_MIGRATIONS_ON_STARTUP=false` afterwards if you prefer to run them
deliberately.

**One index can fail to build on an existing database.** `uniq_confirmed_slot`
is unique over `(owner_id, start_time)` for confirmed bookings, so it cannot be
created if the data already contains a double booking. That is logged as a
warning and startup continues — the application-level conflict check still
runs, but the race is no longer closed. To find the offenders:

```javascript
db.bookings.aggregate([
  { $match: { status: "confirmed" } },
  { $group: { _id: { o: "$owner_id", t: "$start_time" }, n: { $sum: 1 } } },
  { $match: { n: { $gt: 1 } } }
])
```

Cancel or move the duplicates, then restart to let the index build.

Existing event types have no `max_bookings_per_day`; the serializer defaults it
to `0` (unlimited), so nothing changes until a host sets one.

---

## 7. Security notes

- All admin endpoints require a bearer token; there are no unauthenticated
  reads of booking data.
- CORS is restricted to `CORS_ORIGINS`; credentials are not accepted, since
  auth travels in the `Authorization` header.
- Public booking, reschedule, OTP request/verify, login and registration are
  rate limited per IP, backed by Mongo with a TTL index.
- The iCal feed is addressed by an unguessable rotatable token and returns
  only that host's bookings.
- Invitee manage links are unguessable per-booking tokens that grant nothing
  beyond viewing, rescheduling or cancelling that one booking.
- `/docs` and `/openapi.json` are disabled when `APP_ENV=production`.
- Google Calendar access is requested **read-only**
  (`calendar.readonly`) and only busy intervals are read — never event titles,
  descriptions or guests. Refresh tokens live in `integrations.config` and are
  never returned by the API; `/status` reports only whether a calendar is
  connected.
- The calendar OAuth flow is pinned to a single-use `state` row with a 10 minute
  TTL rather than trusting anything in the callback, so a captured callback URL
  cannot be replayed. Disconnecting deletes the stored grant and clears the
  cached token immediately.
