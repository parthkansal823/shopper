"""Email delivery service.

Two entry points:

* :func:`send_email_now` — synchronous, returns ``True``/``False``. Use this
  for OTP delivery where the user is waiting on the response.
* :func:`send_email_background` — fire-and-forget wrapper for
  ``BackgroundTasks``. Failures are logged but never bubble up.

Templates are inlined HTML (with a plain-text alternative) so we don't pull
in a templating engine for half-a-dozen mails.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


def _log_console_email(*, subject: str, recipient: str, text_body: str) -> bool:
    logger.warning(
        "Email console fallback active. Subject=%r Recipient=%s Body=%r",
        subject,
        recipient,
        text_body,
    )
    return True


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

# Email clients are not browsers. Layout is tables rather than flex or grid,
# every colour is inline (Gmail drops much of a <style> block), and the sizes
# are absolute because rem is unreliable. The <style> block carries only
# progressive enhancement — dark mode and a mobile tweak — so the mail still
# reads correctly in a client that discards it entirely.

# Palette and font mirror frontend/src/index.css so the mail looks like the
# product, not a generic notification. Shopper's design is monochrome — the
# accent *is* the ink — and dark mode inverts the button rather than tinting
# it, which is why the dark rules below swap button colours outright.
# 'Plus Jakarta Sans' leads the stack for the few clients that already have it;
# webfonts can't be loaded in most email clients, so the fallbacks do the work.

_FONT = (
    "'Plus Jakarta Sans', ui-sans-serif, system-ui, -apple-system, "
    "BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
)

_INK = "#111113"        # --c-ink
_INK_SOFT = "#5c5c66"   # --c-ink-2
_MUTED = "#8e8e98"      # --c-ink-3
_LINE = "#e8e8ea"       # --c-line
_PAGE_BG = "#f4f4f5"    # --c-sunken
_CARD_BG = "#ffffff"    # --c-surface
_PANEL_BG = "#fafafa"   # --c-raised
_ACCENT = "#111113"     # --c-accent
_ON_ACCENT = "#ffffff"  # --c-on-accent

_RADIUS = "8px"         # --r-md, buttons
_RADIUS_CARD = "12px"   # --r-lg, matches .card in the app

_HEAD = """\
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<style>
  /* Progressive enhancement only — the inline styles already stand alone.
     Values are the [data-theme="dark"] block from the app's index.css. */
  @media (prefers-color-scheme: dark) {
    .sh-page  { background: #0a0a0c !important; }
    .sh-card  { background: #101013 !important; border-color: #26262c !important; }
    .sh-panel { background: #16161a !important; border-color: #26262c !important; }
    .sh-ink   { color: #f5f5f7 !important; }
    .sh-soft  { color: #a2a2ad !important; }
    .sh-muted { color: #74747f !important; }
    .sh-rule  { border-color: #26262c !important; }
    /* The app inverts the accent in dark mode; the button follows. */
    .sh-btn   { background: #f5f5f7 !important; }
    .sh-btn a { color: #0a0a0c !important; }
    .sh-link  { color: #f5f5f7 !important; }
  }
  @media only screen and (max-width: 600px) {
    .sh-card { padding: 24px !important; }
    .sh-code { font-size: 30px !important; letter-spacing: 6px !important; }
  }
</style>"""


def _wrap_html(title: str, inner: str, preheader: str = "") -> str:
    """Shell every email shares: preheader, brand line, card, footer.

    ``preheader`` is the grey snippet an inbox shows beside the subject. It is
    hidden in the body itself, and padded so the client doesn't pull the first
    line of real content in after it.
    """
    hidden_preheader = ""
    if preheader:
        hidden_preheader = (
            '<div style="display:none;max-height:0;overflow:hidden;opacity:0;'
            'mso-hide:all;font-size:1px;line-height:1px;color:#f4f4f5;">'
            f"{escape(preheader)}{'&#8199;&#65279;&#847;' * 30}</div>"
        )

    return f"""\
<!doctype html>
<html lang="en">
<head>{_HEAD}</head>
<body class="sh-page" style="margin:0;padding:0;background:{_PAGE_BG};">
{hidden_preheader}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       class="sh-page" style="background:{_PAGE_BG};padding:32px 12px;">
  <tr>
    <td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="max-width:560px;margin:0 auto;">

        <tr>
          <td style="padding:0 4px 14px;font-family:{_FONT};font-size:13px;
                     font-weight:700;letter-spacing:-0.01em;color:{_ACCENT};">
            Shopper
          </td>
        </tr>

        <tr>
          <td class="sh-card"
              style="background:{_CARD_BG};border:1px solid {_LINE};border-radius:{_RADIUS_CARD};padding:32px;">
            <h1 class="sh-ink"
                style="margin:0 0 18px;font-family:{_FONT};font-size:19px;line-height:1.3;
                       font-weight:650;color:{_INK};letter-spacing:-0.015em;">{escape(title)}</h1>
            {inner}
          </td>
        </tr>

        <tr>
          <td class="sh-muted"
              style="padding:18px 4px 0;font-family:{_FONT};font-size:12px;
                     line-height:1.5;color:{_MUTED};">
            Sent by Shopper · scheduling without the back-and-forth
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body></html>"""


def _paragraph(text: str) -> str:
    return (
        f'<p class="sh-soft" style="margin:0;font-family:{_FONT};font-size:15px;'
        f'line-height:1.6;color:{_INK_SOFT};">{escape(text)}</p>'
    )


def _detail_card(rows: list[tuple[str, str]]) -> str:
    """The labelled facts of a booking, as a table so Outlook keeps the rows."""
    cells = ""
    for index, (label, value) in enumerate(rows):
        padding_top = "0" if index == 0 else "14px"
        cells += f"""
        <tr>
          <td style="padding-top:{padding_top};">
            <p class="sh-muted" style="margin:0 0 3px;font-family:{_FONT};font-size:11px;
               font-weight:600;letter-spacing:0.07em;text-transform:uppercase;color:{_MUTED};"
            >{escape(label)}</p>
            <p class="sh-ink" style="margin:0;font-family:{_FONT};font-size:15px;
               font-weight:550;color:{_INK};">{escape(value)}</p>
          </td>
        </tr>"""

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           class="sh-panel"
           style="background:{_PANEL_BG};border:1px solid {_LINE};border-radius:{_RADIUS};
                  padding:18px;margin:20px 0;">
      {cells}
    </table>"""


def _button(url: str, label: str) -> str:
    """A bulletproof CTA — the anchor carries its own padding for Outlook."""
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:4px 0 0;">
      <tr>
        <td class="sh-btn" align="center" bgcolor="{_ACCENT}"
            style="background:{_ACCENT};border-radius:{_RADIUS};">
          <a href="{escape(url, quote=True)}"
             style="display:inline-block;padding:12px 24px;font-family:{_FONT};font-size:15px;
                    font-weight:600;color:{_ON_ACCENT};text-decoration:none;
                    border-radius:{_RADIUS};"
          >{escape(label)}</a>
        </td>
      </tr>
    </table>"""


def _footnote(html: str, ruled: bool = False) -> str:
    rule = f"margin-top:24px;padding-top:20px;border-top:1px solid {_LINE};" if ruled else "margin-top:16px;"
    classes = "sh-muted sh-rule" if ruled else "sh-muted"
    return (
        f'<p class="{classes}" style="{rule}margin-bottom:0;font-family:{_FONT};'
        f'font-size:13px;line-height:1.55;color:{_MUTED};">{html}</p>'
    )


def manage_url_for(manage_token: Optional[str]) -> str:
    if not manage_token:
        return ""
    return f"{settings.FRONTEND_URL.rstrip('/')}/manage/{manage_token}"


def _guest_booking_html(
    action: str,
    event_title: str,
    start_time: str,
    meeting_url: Optional[str],
    manage_token: Optional[str] = None,
) -> tuple[str, str]:
    headlines = {
        "booked": ("Your booking is confirmed", "Your meeting has been scheduled."),
        "rescheduled": ("Your booking was rescheduled", "Your meeting has moved to a new time."),
        "cancelled": ("Your booking was cancelled", "Your meeting has been cancelled."),
    }
    title, lead = headlines.get(action, ("Booking update", "There is an update on your booking."))

    inner = _paragraph(lead)
    inner += _detail_card([("Event", event_title), ("When", start_time)])

    if meeting_url and action != "cancelled":
        inner += _button(meeting_url, "Join video call")
        inner += _footnote(f"Or paste this link into your browser: {escape(meeting_url)}")

    text_lines = [lead, "", f"Event: {event_title}", f"When:  {start_time}"]
    if meeting_url and action != "cancelled":
        text_lines += ["", f"Join: {meeting_url}"]

    manage_url = manage_url_for(manage_token)
    if manage_url and action != "cancelled":
        inner += _footnote(
            "Need to change plans? "
            f'<a class="sh-link" href="{escape(manage_url, quote=True)}" '
            f'style="color:{_INK};font-weight:600;">Reschedule or cancel</a>'
            " — no account needed.",
            ruled=True,
        )
        text_lines += ["", f"Reschedule or cancel: {manage_url}"]

    if action == "cancelled":
        inner += _footnote("If this was unexpected, please reach out to the organiser.")

    # The inbox preview should say when, not repeat the subject line.
    preheader = f"{event_title} — {start_time}"
    return _wrap_html(title, inner, preheader), "\n".join(text_lines)


def _host_booking_html(
    action: str,
    event_title: str,
    start_time: str,
    meeting_url: Optional[str],
    guest_name: str,
    guest_email: str,
) -> tuple[str, str]:
    headlines = {
        "host_notified": ("New booking", f"{guest_name or 'Someone'} booked a meeting with you."),
        "host_cancelled_by_guest": (
            "Booking cancelled by guest",
            f"{guest_name or 'Your guest'} cancelled their meeting.",
        ),
        "host_rescheduled_by_guest": (
            "Booking rescheduled by guest",
            f"{guest_name or 'Your guest'} moved their meeting to a new time.",
        ),
    }
    title, lead = headlines.get(action, ("Booking update", "There is an update on a booking."))

    rows = [("Event", event_title), ("When", start_time)]
    if guest_name:
        rows.append(("Guest", guest_name))
    if guest_email:
        rows.append(("Email", guest_email))

    inner = _paragraph(lead)
    inner += _detail_card(rows)

    if meeting_url and action != "host_cancelled_by_guest":
        inner += _button(meeting_url, "Join video call")

    text_lines = [lead, "", f"Event: {event_title}", f"When:  {start_time}"]
    if guest_name:
        text_lines.append(f"Guest: {guest_name} <{guest_email}>")
    if meeting_url and action != "host_cancelled_by_guest":
        text_lines += ["", f"Join: {meeting_url}"]

    preheader = f"{guest_name or 'A guest'} · {start_time}"
    return _wrap_html(title, inner, preheader), "\n".join(text_lines)


def _otp_html(code: str, ttl_minutes: int) -> tuple[str, str]:
    minutes = f"{ttl_minutes} minute" + ("s" if ttl_minutes != 1 else "")

    inner = _paragraph("Enter this code to verify your email and finish booking.")
    inner += f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           class="sh-panel"
           style="background:{_PANEL_BG};border:1px solid {_LINE};border-radius:{_RADIUS};
                  padding:22px;margin:20px 0;">
      <tr>
        <td align="center">
          <p class="sh-muted" style="margin:0 0 8px;font-family:{_FONT};font-size:11px;
             font-weight:600;letter-spacing:0.07em;text-transform:uppercase;color:{_MUTED};"
          >Verification code</p>
          <p class="sh-ink sh-code" style="margin:0;font-family:{_FONT};font-size:34px;
             font-weight:700;color:{_INK};letter-spacing:9px;text-indent:9px;line-height:1.15;"
          >{escape(code)}</p>
        </td>
      </tr>
    </table>"""
    # text-indent above offsets the trailing letter-space so the digits sit
    # optically centred rather than pushed left.
    inner += _footnote(
        f"The code expires in {minutes}. If you didn't request it, you can ignore this email —"
        " nothing was booked."
    )

    text = (
        f"Your Shopper verification code is: {code}\n\n"
        f"It expires in {minutes}. If you didn't request it, you can ignore "
        "this email — nothing was booked.\n"
    )
    return _wrap_html("Verify your email", inner, f"Your code is {code}"), text


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _build_message(
    *,
    subject: str,
    recipient: str,
    html_body: str,
    text_body: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    from_addr = settings.SMTP_FROM or settings.SMTP_USER
    msg["From"] = formataddr((settings.SMTP_FROM_NAME, from_addr))
    msg["To"] = recipient
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def _deliver(msg: EmailMessage) -> None:
    """Single SMTP delivery attempt. Raises on failure."""
    context = ssl.create_default_context()
    if settings.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            context=context,
            timeout=settings.SMTP_TIMEOUT_SECONDS,
        ) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.send_message(msg)
    else:
        with smtplib.SMTP(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=settings.SMTP_TIMEOUT_SECONDS,
        ) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.send_message(msg)


def _deliver_http(msg: EmailMessage) -> None:
    """Send over a provider's HTTPS API. Raises on failure.

    Port 443 rather than an SMTP port, which is what makes this work on hosts
    that block outbound mail. The message is already built, so the parts are
    pulled back out of it rather than threading a second shape through.
    """
    provider = settings.http_email_provider
    from_addr = settings.SMTP_FROM or settings.SMTP_USER
    to_addr = msg["To"]
    subject = msg["Subject"]
    text_body = msg.get_body(preferencelist=("plain",)).get_content()
    html_part = msg.get_body(preferencelist=("html",))
    html_body = html_part.get_content() if html_part else f"<pre>{escape(text_body)}</pre>"

    if provider == "brevo":
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {"api-key": settings.BREVO_API_KEY, "accept": "application/json"}
        payload = {
            "sender": {"name": settings.SMTP_FROM_NAME, "email": from_addr},
            "to": [{"email": to_addr}],
            "subject": subject,
            "htmlContent": html_body,
            "textContent": text_body,
        }
    elif provider == "resend":
        url = "https://api.resend.com/emails"
        headers = {"Authorization": f"Bearer {settings.RESEND_API_KEY}"}
        payload = {
            "from": formataddr((settings.SMTP_FROM_NAME, from_addr)),
            "to": [to_addr],
            "subject": subject,
            "html": html_body,
            "text": text_body,
        }
    else:
        raise RuntimeError(f"Unknown HTTP email provider: {provider!r}")

    response = httpx.post(url, headers=headers, json=payload, timeout=20.0)
    if response.status_code >= 300:
        # The body carries the actionable part (unverified sender, bad key).
        raise RuntimeError(
            f"{provider} API returned HTTP {response.status_code}: {response.text[:300]}"
        )


def _send_with_retry(msg: EmailMessage) -> bool:
    """Returns True if delivered, False otherwise. Never raises."""
    attempts = max(1, settings.SMTP_RETRY_COUNT + 1)
    send = _deliver_http if settings.http_email_provider else _deliver
    for attempt in range(1, attempts + 1):
        try:
            send(msg)
            logger.info("Email delivered to %s (attempt %d)", msg["To"], attempt)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Email delivery to %s failed on attempt %d/%d: %s",
                msg["To"], attempt, attempts, exc,
            )
            if attempt < attempts:
                time.sleep(2)
    logger.error("Email delivery to %s permanently failed", msg["To"])
    return False


def diagnose_delivery(recipient: str) -> dict:
    """Attempt one real send and report exactly why it failed.

    ``email_mode`` only reflects configuration — it says the settings are
    present, not that the host can reach the SMTP server. Many hosting
    providers block outbound SMTP ports (25/465/587) to curb spam, which looks
    identical to working config right up until nothing is delivered. This runs
    the real transport and hands back the underlying error so that difference
    is visible from the deployed environment rather than inferred from silence.
    """
    report = {
        "mode": settings.email_delivery_mode,
        "host": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "user_set": bool(settings.SMTP_USER),
        "pass_set": bool(settings.SMTP_PASS),
        "from": settings.SMTP_FROM or settings.SMTP_USER,
        "delivered": False,
        "error": None,
        "error_type": None,
        "hint": None,
    }

    if settings.http_email_provider:
        report["host"] = f"{settings.http_email_provider} HTTPS API"
        report["port"] = 443
        report["pass_set"] = True
    elif settings.email_delivery_mode != "smtp":
        report["hint"] = (
            "Not sending — no HTTPS provider key, and one of "
            "SMTP_HOST/SMTP_USER/SMTP_PASS is blank, so mail is only logged."
        )
        return report

    msg = _build_message(
        subject="Shopper email delivery test",
        recipient=recipient,
        html_body=_wrap_html(
            "Email delivery works",
            _paragraph("If you are reading this, this environment can send mail."),
            "Shopper delivery test",
        ),
        text_body="If you are reading this, this environment can send mail.",
    )

    try:
        (_deliver_http if settings.http_email_provider else _deliver)(msg)
        report["delivered"] = True
        return report
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)[:300]
        report["error_type"] = type(exc).__name__

    name = report["error_type"] or ""
    lowered = (report["error"] or "").lower()
    if name in {"TimeoutError", "socket.timeout"} or "timed out" in lowered:
        report["hint"] = (
            "The connection timed out rather than being rejected, which is what "
            "a blocked outbound SMTP port looks like. Many hosts block 25/465/587. "
            "Send over an HTTPS email API instead of SMTP."
        )
    elif "refused" in lowered or name == "ConnectionRefusedError":
        report["hint"] = "Connection refused — the port is closed from this host."
    elif name == "SMTPAuthenticationError":
        report["hint"] = (
            "Credentials rejected. Gmail needs an App Password (2-Step "
            "Verification on), not the account password."
        )
    elif name.startswith("SMTP"):
        report["hint"] = "The server answered but rejected the exchange; see error."
    return report


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_email_now(
    *,
    subject: str,
    recipient: str,
    html_body: str,
    text_body: str,
) -> bool:
    """Synchronous send. Returns True on success, False otherwise."""
    if settings.email_delivery_mode == "console":
        return _log_console_email(subject=subject, recipient=recipient, text_body=text_body)
    if settings.email_delivery_mode == "disabled":
        logger.warning("Email delivery disabled; refusing to send '%s' to %s", subject, recipient)
        return False
    msg = _build_message(
        subject=subject, recipient=recipient, html_body=html_body, text_body=text_body
    )
    return _send_with_retry(msg)


_SUBJECTS = {
    "booked": "Booking confirmed: {title}",
    "rescheduled": "Booking rescheduled: {title}",
    "cancelled": "Booking cancelled: {title}",
    "host_notified": "New booking: {title}",
    "host_cancelled_by_guest": "Guest cancelled: {title}",
    "host_rescheduled_by_guest": "Guest rescheduled: {title}",
}

_HOST_ACTIONS = {"host_notified", "host_cancelled_by_guest", "host_rescheduled_by_guest"}


def send_email_background(
    action: str,
    recipient: str,
    event_title: str,
    start_time: str,
    meeting_url: Optional[str] = None,
    manage_token: Optional[str] = None,
    guest_name: str = "",
    guest_email: str = "",
) -> None:
    """Fire-and-forget booking lifecycle email.

    Designed for use with FastAPI's ``BackgroundTasks``. Never raises.
    """
    subject_template = _SUBJECTS.get(action)
    if not subject_template:
        logger.error("Unknown booking email action: %s", action)
        return
    subject = subject_template.format(title=event_title)

    if settings.email_delivery_mode == "console":
        _log_console_email(
            subject=subject,
            recipient=recipient,
            text_body=(
                f"{action}\nEvent: {event_title}\nWhen: {start_time}\n"
                f"Meeting URL: {meeting_url or 'n/a'}\n"
                f"Manage: {manage_url_for(manage_token) or 'n/a'}"
            ),
        )
        return
    if settings.email_delivery_mode == "disabled":
        logger.warning("Email delivery disabled; skipping '%s' email to %s", action, recipient)
        return

    if action in _HOST_ACTIONS:
        html_body, text_body = _host_booking_html(
            action, event_title, start_time, meeting_url, guest_name, guest_email
        )
    else:
        html_body, text_body = _guest_booking_html(
            action, event_title, start_time, meeting_url, manage_token
        )

    _send_with_retry(
        _build_message(
            subject=subject, recipient=recipient, html_body=html_body, text_body=text_body
        )
    )


def send_otp_email(recipient: str, code: str, ttl_seconds: int) -> bool:
    """Synchronous OTP delivery. Returns True on success, False otherwise."""
    ttl_minutes = max(1, ttl_seconds // 60)
    html_body, text_body = _otp_html(code, ttl_minutes)
    return send_email_now(
        subject="Your verification code",
        recipient=recipient,
        html_body=html_body,
        text_body=text_body,
    )
