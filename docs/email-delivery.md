# Email delivery

Shopper sends verification codes, booking confirmations, reschedule and cancel
notices, and workflow reminders. If email is broken, **bookings are broken** —
an invitee cannot complete one without receiving a code.

This is the part of the system most likely to work perfectly on a laptop and
fail in production, so it's documented in more detail than its size suggests.

---

## 1. Transports

Four ways out, resolved **at request time** in this order:

| Order | Transport | Needs | Leaves over |
| :-- | :-- | :-- | :-- |
| 1 | **Gmail** | A grant connected in the UI, or `GMAIL_REFRESH_TOKEN` | HTTPS 443 |
| 2 | **SendGrid** | `SENDGRID_API_KEY` | HTTPS 443 |
| 3 | **Resend** | `RESEND_API_KEY` | HTTPS 443 |
| 4 | **SMTP** | `SMTP_HOST` + `SMTP_USER` + `SMTP_PASS` | Port 587/465 |

Enabling one takes over **without** removing the settings below it, and
removing it falls straight back. That is deliberate: you can switch transports
without dismantling a working configuration.

If none are configured: `console` in development (mail is logged, and the OTP
is surfaced in the UI so a booking can still be completed end to end), and
`disabled` in production.

`GET /health` reports which is live in `email_mode`. It is resolved, not read
from config — a Gmail account connected through the UI shows up there even
though no environment variable mentions it.

---

## 2. Why SMTP fails once deployed

**Many hosting providers block outbound SMTP ports (25, 465, 587)** to stop
their platform being used for spam. Render is one of them.

Your laptop has no such restriction, so the exact same credentials that work in
development go silent in production. It's a confusing failure because nothing
is *misconfigured*:

- `/health` still reports `"email_mode":"smtp"` — that only says the settings
  are present, never that the host can reach the mail server.
- The credentials are valid; they're simply never able to be presented.
- The connection **times out** rather than being refused, so it looks like
  slowness before it looks like a wall.

**No amount of SMTP configuration fixes this.** The mail has to leave over
HTTPS instead. That's what the other three transports are for.

---

## 3. Gmail (recommended)

No third-party account, and mail genuinely comes *from* your mailbox, which is
better for deliverability than a relay sending on your behalf.

Scope is **`gmail.send`** — permission to send, nothing else. It cannot read a
message. The grant is stored **app-wide** in `app_settings`, exactly as the
single `SMTP_USER` was; it is not per-tenant.

Gmail sends as the authenticated mailbox and rejects a mismatched `From`, so
the sender header is rewritten to the connected address. A free Google account
allows roughly **500 messages/day**.

### Option A — connect in the app

Integrations → **Connect Gmail**. Requires being able to sign in, and the
callback URI registered in the Google console.

### Option B — one environment variable

Use this when you can't reach the admin UI, or an OAuth redirect is awkward.

1. Google Console → **Credentials** → your OAuth client → *Authorised redirect
   URIs* → add `https://developers.google.com/oauthplayground`.
2. Open **https://developers.google.com/oauthplayground** → **⚙ gear** → tick
   **Use your own OAuth credentials** → paste your client ID and secret.
   *Skipping this issues a token for Google's playground app, not yours.*
3. Paste `https://www.googleapis.com/auth/gmail.send` into **Input your own
   scopes** → **Authorize APIs** → approve.
4. **Exchange authorization code for tokens** → copy the `refresh_token`
   (starts with `1//`).
5. Set `GMAIL_REFRESH_TOKEN` on the host. `GMAIL_SENDER` is optional and
   defaults to `SMTP_FROM`.

The playground redirect URI can be removed afterwards — it is only used while
*obtaining* a token. A grant connected in the UI takes precedence over this
variable.

### Enable the Gmail API

A new Google Cloud project has the Gmail API **off**. Having OAuth credentials
does not enable it, and the failure is a `403` at send time, long after
everything looks configured:

```
https://console.cloud.google.com/apis/library/gmail.googleapis.com
```

Make sure the project selector matches the project your OAuth client lives in.

---

## 4. SendGrid and Resend

Both are one environment variable and no OAuth. The free tiers differ in a way
that decides which is usable:

- **SendGrid** — needs only a *single verified sender*, so a plain Gmail
  address works and it will mail any invitee. `SENDGRID_API_KEY`.
- **Resend** — needs a **domain you own** before it will mail anyone other
  than your own account. Fine with a domain, useless without one for a booking
  app. `RESEND_API_KEY`.

`SMTP_FROM` / `SMTP_FROM_NAME` still supply the sender, and **that address must
be verified with the provider** or the API rejects the send.

---

## 5. Troubleshooting

Work down this list; the first two are by far the most common and neither
raises an error.

**1. The server is running with stale settings.** `.env` is read once at
startup. Adding `SMTP_PASS` to a running server changes nothing until restart.

**2. Gmail needs an App Password**, not the account password, and 2-Step
Verification must be on for that option to exist. Google shows it as
`xxxx xxxx xxxx xxxx`; the spaces are display only.

**3. Check `/health`.** `email_mode` tells you which transport is live —
frequently the answer is "not the one you just configured".

**4. Use the test button.** Integrations → **Send test email** runs one real
send *from the deployed environment* and returns the underlying exception plus
a hint. It only ever mails the caller's own address, so it cannot be used to
send elsewhere. Equivalent to:

```bash
curl -X POST https://<your-backend>/api/auth/email/test \
     -H "Authorization: Bearer <your-jwt>"
```

**5. Read the deploy logs.** Failures are logged with the real reason:

```
Email delivery to … failed on attempt 1/2: <the actual error>
Email delivery to … permanently failed
```

| The error says | Cause | Fix |
| :-- | :-- | :-- |
| timed out | outbound SMTP blocked | switch to Gmail or SendGrid |
| `SMTPAuthenticationError` | wrong credential on the host | fix `SMTP_PASS` |
| `Gmail API returned HTTP 403` | Gmail API not enabled on the project | enable it |
| `token refresh failed: 401` + `invalid_client` | playground used Google's credentials, not yours | redo with the ⚙ gear ticked |
| `403 insufficient scope` | `gmail.send` not granted | redo the authorize step |
| provider `HTTP 403` unverified sender | `SMTP_FROM` not verified | verify the sender |

**6. Look in spam.** Delivery succeeding in the log means the provider accepted
the message for relay, not that it reached an inbox. A brand-new sender with no
SPF/DKIM alignment is frequently filtered.

---

## 6. Templates

One set of templates in `services/email_service.py`, each returning an HTML and
a plain-text part. Email clients are not browsers, so:

- **Tables, not flex or grid** — Outlook renders `<div>` cards badly.
- **Every colour inline** — Gmail drops much of a `<style>` block. The block
  carries only progressive enhancement (dark mode, one mobile breakpoint), so
  the mail still reads correctly in a client that discards it entirely.
- **A hidden preheader** — the grey snippet beside the subject line, padded so
  the client doesn't pull the first line of body copy in after it.
- **A bulletproof CTA** — the anchor carries its own padding, because Outlook
  ignores padding on a table cell.

The palette and font mirror `frontend/src/index.css`, so mail looks like the
product rather than a generic notification. Shopper's design is monochrome —
the accent *is* the ink — and dark mode **inverts** the button rather than
tinting it.
