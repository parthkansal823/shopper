# API reference

Generated from the live route table. When `APP_ENV` is not `production`, the
interactive docs at **`/docs`** are the authoritative, always-current version —
they are disabled in production so they can't be used to map the API.

---

## Authentication

Two credential types share one header:

```http
Authorization: Bearer <jwt>            # from POST /api/auth/login
Authorization: Bearer sk_live_…        # an API key
```

JWTs are HS256 and expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (default 1440).
API keys don't expire — only a SHA-256 hash is stored, so the full key is shown
once at creation and never again.

**Endpoints under `/api/public/…` need no credentials.** Everything else is
scoped to the authenticated tenant.

### Status codes

| Code | Meaning here |
| :-- | :-- |
| `401` | No credential, or it's invalid/expired |
| `403` | Authenticated but the action is disabled (e.g. registration off) |
| `404` | Not found **or not yours** — the two are deliberately indistinguishable |
| `409` | The slot was taken between loading it and confirming |
| `422` | Validation failed (a required custom question, a bad date) |
| `429` | Rate limited |
| `502` | An upstream failed — almost always email delivery |
| `503` | A capability isn't configured (e.g. Google OAuth) |

---

## Public — no auth

The invitee-facing surface. All of it is rate limited per IP.

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET` | `/api/public/event-types/{slug}` | The booking page's event type, host name, questions |
| `GET` | `/api/public/event-types/{slug}/slots?date=YYYY-MM-DD` | Bookable slots for one day |
| `GET` | `/api/public/event-types/{slug}/days?month=YYYY-MM` | Which days in a month have any opening |
| `POST` | `/api/public/event-types/{slug}/book` | Create a booking — needs a verification token |
| `POST` | `/api/public/otp/request` | Email a 6-digit code |
| `POST` | `/api/public/otp/verify` | Exchange code → `verification_token` |
| `GET` | `/api/public/bookings/{booking_id}` | Confirmation page data |
| `GET` | `/api/public/manage/{token}` | Invitee self-service view |
| `POST` | `/api/public/manage/{token}/reschedule` | Move that one booking |
| `POST` | `/api/public/manage/{token}/cancel` | Cancel that one booking |
| `GET` | `/api/public/ical/{token}` | Private iCal feed (unguessable, rotatable) |

### The booking sequence

Slots and days are cheap reads; the write has three preconditions.

```
POST /api/public/otp/request      { "email": "…" }
POST /api/public/otp/verify       { "email": "…", "code": "123456" }
   → { "verification_token": "…" }                    # 15 min TTL, single use

POST /api/public/event-types/{slug}/book
{
  "booker_name": "Jane Smith",
  "booker_email": "jane@example.com",
  "start_time": "2026-08-17T09:00:00+00:00",   # start_utc from /slots
  "verification_token": "…",
  "notes": "",
  "answers": [
    { "question_id": "topic", "label": "What would you like to cover?",
      "value": "Pricing" }
  ]
}
```

Three things reject a booking that looks fine:

- **A required custom question with no answer** → `422` naming the question.
- **A missing or spent `verification_token`** → the email must be verified
  server-side; the client cannot skip it.
- **The slot going while the invitee filled the form** → `409`.

`/otp/request` is throttled **per email** as well as per IP, so an immediate
retry returns `429` with a `resend_after_seconds` hint.

---

## Auth and account

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `POST` | `/api/auth/register` | Create an account |
| `POST` | `/api/auth/login` | Email + password → JWT |
| `GET` | `/api/auth/me` | The current user |
| `PUT` | `/api/auth/profile` | Name, booking username, welcome message |
| `PUT` | `/api/auth/change-password` | Email-registered accounts only |
| `GET` | `/api/auth/google` | Redirect to Google sign-in |
| `GET` | `/api/auth/google/callback` | Sign-in callback |
| `POST` | `/api/auth/api-keys` | Generate a key — **full value shown once** |
| `GET` | `/api/auth/api-keys` | List keys (prefix only) |
| `DELETE` | `/api/auth/api-keys` | Revoke all keys |
| `POST` | `/api/auth/email/test` | Send a test email; returns the real failure reason |

An account created through Google has **no password**, so
`/api/auth/change-password` doesn't apply to it — and if `GOOGLE_CLIENT_SECRET`
is missing in that environment, such an account cannot sign in at all.

### Google grants

Three separate grants, each with its own callback. Keeping them apart means
signing in never requires handing over a mailbox or calendar.

| Method | Path | Grant |
| :-- | :-- | :-- |
| `GET` | `/api/auth/google/calendar/connect` | Returns a consent URL (`calendar.readonly`) |
| `GET` | `/api/auth/google/calendar/callback` | Stores the grant |
| `GET` | `/api/auth/google/calendar/status` | Connected? Needs reconnect? |
| `DELETE` | `/api/auth/google/calendar` | Disconnect |
| `GET` | `/api/auth/google/gmail/connect` | Returns a consent URL (`gmail.send`) |
| `GET` | `/api/auth/google/gmail/callback` | Stores the grant |
| `GET` | `/api/auth/google/gmail/status` | Connected? |
| `DELETE` | `/api/auth/google/gmail` | Disconnect |

`connect` returns `{ "url": … }` for the browser to navigate to; it does not
redirect, so the caller keeps control. State is a single-use row with a 10
minute TTL, so a captured callback URL cannot be replayed.

---

## Host management — auth required

| Method | Path | Purpose |
| :-- | :-- | :-- |
| `GET`/`POST` | `/api/event-types` | List / create |
| `PUT`/`DELETE` | `/api/event-types/{id}` | Update / delete |
| `PATCH` | `/api/event-types/{id}/toggle` | Activate or deactivate |
| `POST` | `/api/event-types/{id}/duplicate` | Copy, with a fresh slug |
| `GET`/`PUT` | `/api/availability` | Weekly windows and timezone |
| `GET`/`POST` | `/api/blockouts` | Date-range blockouts |
| `DELETE` | `/api/blockouts/{id}` | Remove one |
| `GET`/`POST` | `/api/bookings` | List (search, scope filters) / create |
| `GET` | `/api/bookings/export.csv` | CSV of the current view |
| `PATCH` | `/api/bookings/{id}/notes` | Edit host notes |
| `POST` | `/api/bookings/{id}/reschedule` | Move a booking |
| `POST` | `/api/bookings/{id}/cancel` | Cancel |
| `GET`/`POST` | `/api/workflows` | Automations and reminders |
| `PUT`/`DELETE` | `/api/workflows/{id}` | Update / delete |
| `PATCH` | `/api/workflows/{id}/toggle` | Enable or disable |
| `GET` | `/api/integrations` | Connected integrations |
| `POST`/`DELETE` | `/api/integrations/{key}` | Connect / disconnect |
| `POST` | `/api/integrations/{key}/test` | Fire a test payload |
| `GET` | `/api/calendar/feed` | Private iCal URL |
| `POST` | `/api/calendar/feed/rotate` | Invalidate and reissue |
| `GET` | `/api/summary` | Dashboard counters |
| `GET` | `/api/timezones` | Supported timezone list |

Slugs are **globally** unique, so creating an event type can fail on a slug
another tenant already holds.

---

## Meta

| Method | Path | Notes |
| :-- | :-- | :-- |
| `GET`/`HEAD` | `/health` | Liveness **and** a real database ping |
| `GET` | `/docs`, `/openapi.json` | Disabled when `APP_ENV=production` |

`/health` is not a static response — it runs a Mongo `ping`, so every check is
also database activity. It answers `HEAD` as well as `GET`, which is what
uptime monitors send by default.

```json
{ "status": "ok", "database": "up", "email_mode": "gmail" }
```

- `status` — `ok`, or `degraded` with HTTP `503` when Mongo is unreachable.
- `email_mode` — the transport **actually resolved at request time**, one of
  `gmail`, `sendgrid`, `resend`, `smtp`, `console`, `disabled`. This reflects a
  Gmail account connected through the UI, which no environment variable would
  reveal.

---

## Rate limits

Backed by Mongo with a TTL index, so limits survive a restart and expire
without a cleanup job. Defaults, all per IP:

| Bucket | Limit | Window |
| :-- | :-- | :-- |
| Login | 10 | 5 min |
| Booking | 10 | 1 hour |
| OTP request/verify | 8 | 1 hour |

`/api/public/otp/request` adds a **per-email** cooldown on top (default 60 s),
returned as `resend_after_seconds` so the UI can count down.
