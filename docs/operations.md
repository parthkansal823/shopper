# Operations

Running Shopper in production: deploying, keeping it awake, upgrading, and what
to check when something looks wrong.

The [README](../README.md) has the first-time deployment walkthrough. This is
the runbook for after that.

---

## 1. What runs where

| | Where | Sleeps? |
| :-- | :-- | :-- |
| Frontend | Netlify (static) | No |
| Backend | Render free web service | **After ~15 min idle** |
| Database | MongoDB Atlas M0 | **After ~60 days idle** |

Two different clocks, and one of them is dangerous.

Render suspends a free service after roughly 15 minutes without traffic, and
waking it costs the next visitor about 50 seconds. Annoying, but self-healing —
a request wakes it.

Atlas is the trap: an M0 cluster pauses after around **60 days with no
connections and does not auto-wake**. A request will not revive it. It stays
paused until someone clicks *Resume* in the Atlas dashboard, and until then
`/health` reports `"database":"down"` with HTTP 503.

---

## 2. Keeping both awake

One monitor covers both, because `/health` is not a static response — it runs
`get_db().command("ping")`, so every check is real database activity as well as
real HTTP traffic.

**UptimeRobot (recommended).** Free tier checks every 5 minutes, comfortably
inside Render's ~15 minute window.

- Monitor type **HTTP(S)**, URL `https://<your-backend>/health`
- **`HEAD` is supported** — the endpoint is declared for both `GET` and `HEAD`.
  The handler still runs and still pings Mongo; only the body is dropped.
- **Raise the request timeout above 30 s.** If the service *has* gone to sleep,
  the waking request takes ~50 s and a shorter timeout reports a false outage.
  Leave a retry/confirmation setting on.
- Because the endpoint returns **503** when Mongo is unreachable, the monitor
  doubles as a database alarm: 503 means the API is up but the database is not.

**GitHub Actions (alternative).** `.github/workflows/keep-alive.yml` pings every
14 minutes and needs no third-party account. Set the repo variable
`BACKEND_URL` if your URL differs. Its weakness: GitHub disables scheduled
workflows on repositories with no activity for 60 days — if cold starts
reappear, check the workflow is still enabled.

Render's free tier allows 750 instance-hours against a ~730-hour month, so one
always-on service fits — but there is no room for a second in the same account.

---

## 3. Configuration that derives itself

Two things are deliberately **not** pinned in `render.yaml`, because pinning
them is what broke the deployment the first time the service was recreated:

- **`API_PUBLIC_URL`** falls back to `RENDER_EXTERNAL_URL`, which Render
  injects with the service's real public URL.
- **All three OAuth callbacks** derive from `API_PUBLIC_URL`.

So a service recreated on a new hostname keeps working. A hard-coded
`http://localhost:8000` default is silently wrong in production: the server
sends Google a localhost `redirect_uri` and every attempt dies with
`redirect_uri_mismatch`, no matter what is registered.

Set `API_PUBLIC_URL` explicitly only behind a custom domain Render doesn't know
about. On any other host it must be set, since `RENDER_EXTERNAL_URL` is
Render-specific.

The two that **cannot** be derived are the frontend's origin —
`FRONTEND_URL` and `CORS_ORIGINS`. A mismatch between `CORS_ORIGINS` and the
real Netlify origin is the classic "works locally, blocked in production".

---

## 4. Boot guards

The app deliberately fails fast rather than starting misconfigured. In
production it will not boot if:

- `SECRET_KEY` is missing, still the default, or under 32 characters
- `MONGODB_URI` points at localhost
- `CORS_ORIGINS` is empty or `*`

A boot failure here is the app telling you a secret is missing. Read the logs
and supply it — don't relax the check.

---

## 5. Upgrading

`app/migrations.py` runs automatically at startup and is idempotent:

- assigns pre-existing global data to the oldest account
- renames `integrations.user_id` → `owner_id`
- converts single-day blockouts to date ranges
- backfills `manage_token` so old bookings get self-service links
- clears blank `booking_username` values that would collide
- drops legacy indexes that conflict with per-tenant uniqueness

**Take an Atlas snapshot before the first deploy of a new version.** Set
`RUN_MIGRATIONS_ON_STARTUP=false` afterwards if you prefer running them
deliberately.

### One index can fail to build

`uniq_confirmed_slot` is unique over `(owner_id, start_time)` for confirmed
bookings, so it **cannot be created if the data already contains a double
booking**. That is logged as a warning and startup continues — the
application-level conflict check still runs, but the race is no longer closed.

Find the offenders:

```javascript
db.bookings.aggregate([
  { $match: { status: "confirmed" } },
  { $group: { _id: { o: "$owner_id", t: "$start_time" }, n: { $sum: 1 } } },
  { $match: { n: { $gt: 1 } } }
])
```

Cancel or move the duplicates, then restart to let the index build.

---

## 6. Incident checklist

**Start at `/health`.** It answers three questions at once:

```json
{ "status": "ok", "database": "up", "email_mode": "gmail" }
```

| Symptom | Look at |
| :-- | :-- |
| Site loads, every API call fails | `CORS_ORIGINS` vs the real frontend origin; or `VITE_API_URL` was unset at build time and the bundle points somewhere else |
| `"database":"down"`, HTTP 503 | Atlas paused (Resume it), or the Atlas IP allowlist no longer has `0.0.0.0/0` — Render's egress IPs are dynamic |
| First request takes ~50 s | Cold start; check the keep-alive monitor is still running |
| Booking fails at the code step | Email. See [Email delivery](email-delivery.md) |
| Confirmation links point at `localhost` | `FRONTEND_URL` is wrong |
| Google sign-in fails | Consent screen still in *Testing* with your account not listed as a test user; or a redirect URI mismatch |
| `/docs` is reachable in production | `APP_ENV` isn't `production` |

**A booking page that shows nothing usually isn't broken.** With no availability
rules and no active event type there is genuinely nothing to offer. Check the
dashboard's setup checklist first.

---

## 7. Tests

```bash
docker run -d -p 27099:27017 --name shopper-test-mongo mongo:7
cd backend && python scripts/smoke_test.py
```

82 checks covering tenant isolation, auth enforcement, slot generation with
lunch breaks, the OTP and booking flow, custom questions, invitee
reschedule/cancel, blockouts, CSV export, the reminder scheduler and the iCal
feed. Nothing is mocked.

CI runs exactly this on every push (`.github/workflows/ci.yml`) against a
MongoDB service container, plus a frontend build — which catches the failure
mode that actually breaks deploys: an unresolved import from a component that
was never committed.

**The suite drops its target database on every run**, so never point
`SMOKE_MONGODB_URI` at real data. Override `SMOKE_MONGODB_DB` to be safe:

```bash
SMOKE_MONGODB_URI="mongodb+srv://…" SMOKE_MONGODB_DB=shopper_ci_verify \
  python scripts/smoke_test.py
```

---

## 8. Security posture

- All admin endpoints require a bearer token; there are no unauthenticated
  reads of booking data.
- Another tenant's document returns **404, not 403**, so ids can't be probed.
- CORS is restricted to `CORS_ORIGINS`; credentials are not accepted, since
  auth travels in the `Authorization` header.
- Public booking, reschedule, OTP, login and registration are rate limited per
  IP, backed by Mongo with a TTL index.
- The iCal feed is an unguessable rotatable token returning only that host's
  bookings. Rotating invalidates the old URL immediately.
- Invitee manage links grant nothing beyond viewing, rescheduling or
  cancelling that one booking.
- API keys are stored as SHA-256 hashes; a database dump yields no usable keys.
- Google Calendar access is **read-only** (`calendar.readonly`) and reads busy
  intervals only — never titles, guests or descriptions. Gmail access is
  `gmail.send` only and cannot read mail.
- OAuth flows are pinned to a single-use `state` row with a 10 minute TTL, so a
  captured callback URL cannot be replayed.
- `/docs` and `/openapi.json` are disabled when `APP_ENV=production`.

Refresh tokens and API keys live in the database and in host environment
variables — never in git. `backend/.env` and `frontend/.env` are gitignored and
have never been committed.
