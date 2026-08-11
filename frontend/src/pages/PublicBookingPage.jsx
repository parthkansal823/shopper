import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../services/api";
import {
  COMMON_TIMEZONES,
  browserTimezone,
  dateKeyIn,
  formatTimeIn,
  shiftDateKey,
  timezoneOffsetLabel,
  toDateInputValue,
} from "../utils/date";
import Logo from "../components/Logo";
import Icon from "../components/Icon";
import ThemeToggle from "../components/ThemeToggle";
import { Skeleton } from "../components/Skeleton";
import { useToast } from "../components/Toast";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const STEPS = ["Pick a time", "Your details", "Confirm"];

function validate(form, questions) {
  const errors = {};
  if (!form.booker_name.trim()) errors.booker_name = "Your name is required.";
  if (!form.booker_email.trim()) errors.booker_email = "Email is required.";
  else if (!EMAIL_PATTERN.test(form.booker_email)) errors.booker_email = "Enter a valid email address.";

  questions.forEach((question) => {
    if (question.required && !String(form.answers?.[question.id] || "").trim()) {
      errors[`q_${question.id}`] = `${question.label} is required.`;
    }
  });
  return errors;
}

function TimezoneSelect({ value, onChange, id = "tz" }) {
  const options = useMemo(() => {
    const browser = browserTimezone();
    const extras = [value, browser].filter((zone) => zone && !COMMON_TIMEZONES.includes(zone));
    return [...new Set([...extras, ...COMMON_TIMEZONES])];
  }, [value]);

  return (
    <div className="field tz-field">
      <label className="field-label tiny subtle" htmlFor={id}>Times shown in</label>
      <select id={id} className="select" value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((zone) => (
          <option key={zone} value={zone}>
            {zone.replace(/_/g, " ")} · {timezoneOffsetLabel(zone)}
          </option>
        ))}
      </select>
    </div>
  );
}

function Calendar({ selectedDate, onSelectDate, availableDays, onMonthChange, loading }) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const [viewMonth, setViewMonth] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));

  const year = viewMonth.getFullYear();
  const month = viewMonth.getMonth();
  const monthKey = `${year}-${String(month + 1).padStart(2, "0")}`;

  useEffect(() => { onMonthChange(monthKey); }, [monthKey, onMonthChange]);

  const lastDay = new Date(year, month + 1, 0).getDate();
  const pad = (new Date(year, month, 1).getDay() + 6) % 7;
  const cells = [...Array(pad).fill(null), ...Array.from({ length: lastDay }, (_, i) => i + 1)];

  return (
    <div className="calendar">
      <div className="calendar-head">
        <button type="button" className="btn btn-icon btn-ghost" onClick={() => setViewMonth(new Date(year, month - 1, 1))} aria-label="Previous month">
          <Icon name="chevronLeft" size={15} />
        </button>
        <span className="calendar-month">{viewMonth.toLocaleDateString("en-US", { month: "long", year: "numeric" })}</span>
        <button type="button" className="btn btn-icon btn-ghost" onClick={() => setViewMonth(new Date(year, month + 1, 1))} aria-label="Next month">
          <Icon name="chevronRight" size={15} />
        </button>
      </div>

      <div className="calendar-weekdays">
        {["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((day) => <span key={day}>{day}</span>)}
      </div>

      <div className={`calendar-grid${loading ? " is-loading" : ""}`}>
        {cells.map((day, index) => {
          if (!day) return <span key={`pad-${index}`} />;
          const date = new Date(year, month, day);
          date.setHours(0, 0, 0, 0);
          const iso = toDateInputValue(date);
          const isPast = date < today;
          const unavailable = !isPast && availableDays !== null && !availableDays.has(iso);
          const classes = [
            "calendar-day",
            selectedDate === iso ? "is-selected" : "",
            date.getTime() === today.getTime() ? "is-today" : "",
            unavailable ? "is-unavailable" : "",
          ].filter(Boolean).join(" ");

          return (
            <button key={iso} type="button" disabled={isPast || unavailable} className={classes} onClick={() => onSelectDate(iso)}>
              {day}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function QuestionField({ question, value, onChange, error }) {
  const id = `q-${question.id}`;
  const shared = {
    id,
    value: value || "",
    onChange: (event) => onChange(question.id, event.target.value),
    "aria-invalid": error ? "true" : "false",
  };

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {question.label}{question.required ? <span className="req"> *</span> : null}
      </label>

      {question.type === "textarea" ? (
        <textarea className="textarea" rows="3" placeholder={question.placeholder} {...shared} />
      ) : question.type === "select" ? (
        <select className="select" {...shared}>
          <option value="">Select an option</option>
          {(question.options || []).map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      ) : question.type === "checkbox" ? (
        <label className="check">
          <input
            type="checkbox"
            checked={value === "Yes"}
            onChange={(event) => onChange(question.id, event.target.checked ? "Yes" : "")}
          />
          {question.placeholder || "Yes"}
        </label>
      ) : (
        <input className="input" type={question.type === "phone" ? "tel" : "text"} placeholder={question.placeholder} {...shared} />
      )}

      {error ? <span className="error-text">{error}</span> : null}
    </div>
  );
}

export default function PublicBookingPage() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const resendTimer = useRef(null);

  // Slots come back keyed by absolute start_utc, so a day's response is good
  // for any timezone the visitor picks — cache it and re-filter client-side
  // instead of refetching three days on every date or timezone change.
  const slotCache = useRef(new Map());

  const [eventType, setEventType] = useState(null);
  const [loadingEvent, setLoadingEvent] = useState(true);
  const [timezone, setTimezone] = useState(browserTimezone);
  const [selectedDate, setSelectedDate] = useState(() => toDateInputValue(new Date()));
  const [slots, setSlots] = useState([]);
  const [loadingSlots, setLoadingSlots] = useState(false);
  const [availableDays, setAvailableDays] = useState(null);
  const [loadingDays, setLoadingDays] = useState(false);
  const [selectedSlot, setSelectedSlot] = useState("");
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({ booker_name: "", booker_email: "", notes: "", answers: {} });
  const [touched, setTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [otpStage, setOtpStage] = useState("idle");
  const [otpCode, setOtpCode] = useState("");
  const [otpSending, setOtpSending] = useState(false);
  const [otpVerifying, setOtpVerifying] = useState(false);
  const [verificationToken, setVerificationToken] = useState("");
  const [verifiedEmail, setVerifiedEmail] = useState("");
  const [resendIn, setResendIn] = useState(0);
  const [devCode, setDevCode] = useState("");

  const questions = eventType?.questions || [];
  const errors = useMemo(() => validate(form, questions), [form, questions]);
  const hasErrors = Object.keys(errors).length > 0;
  const emailValid = !errors.booker_email && form.booker_email.trim().length > 0;
  const isVerified = otpStage === "verified" && verifiedEmail === form.booker_email.trim().toLowerCase();
  const chosenSlot = slots.find((slot) => slot.start_utc === selectedSlot);

  useEffect(() => {
    slotCache.current.clear();
    (async () => {
      setLoadingEvent(true);
      try {
        setEventType(await api.getPublicEventType(slug));
      } catch (error) {
        toast.error(error.message || "Could not load this booking page.");
      } finally {
        setLoadingEvent(false);
      }
    })();
  }, [slug, toast]);

  // Slots are generated per host-local day, so a visitor day can straddle two
  // of them. Fetch the neighbours and keep what lands on the chosen date.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!slug || !selectedDate) return;
      const days = [shiftDateKey(selectedDate, -1), selectedDate, shiftDateKey(selectedDate, 1)];
      const cached = days.map((day) => slotCache.current.get(`${slug}|${day}`));
      const allCached = cached.every(Boolean);

      // Only show the skeleton for a real fetch; a cache hit should feel instant.
      if (!allCached) setLoadingSlots(true);
      try {
        const results = await Promise.all(
          days.map((day, index) => {
            if (cached[index]) return cached[index];
            return api.getSlots(slug, day)
              .then((slotsForDay) => {
                slotCache.current.set(`${slug}|${day}`, slotsForDay);
                return slotsForDay;
              })
              .catch(() => []);
          })
        );
        if (cancelled) return;

        const byStart = new Map();
        results.flat().forEach((slot) => byStart.set(slot.start_utc, slot));
        setSlots(
          [...byStart.values()]
            .filter((slot) => dateKeyIn(slot.start_utc, timezone) === selectedDate)
            .sort((a, b) => a.start_utc.localeCompare(b.start_utc))
        );
        setSelectedSlot("");
      } catch (error) {
        if (!cancelled) toast.error(error.message || "Could not load times.");
      } finally {
        if (!cancelled) setLoadingSlots(false);
      }
    })();
    return () => { cancelled = true; };
  }, [slug, selectedDate, timezone, toast]);

  const handleMonthChange = useCallback(async (monthKey) => {
    if (!slug) return;
    setLoadingDays(true);
    try {
      const days = await api.getAvailableDays(slug, monthKey);
      setAvailableDays(new Set(days));

      // Land the visitor on a day that actually has openings. Defaulting to
      // today shows an empty slot list whenever today is fully booked, past
      // its cut-off, or simply not a working day.
      setSelectedDate((current) => {
        if (days.includes(current)) return current;
        const next = days.find((day) => day >= toDateInputValue(new Date()));
        return next || current;
      });
    } catch {
      setAvailableDays(null); // non-fatal: days just aren't greyed out
    } finally {
      setLoadingDays(false);
    }
  }, [slug]);

  useEffect(() => {
    if (resendIn <= 0) {
      clearInterval(resendTimer.current);
      resendTimer.current = null;
      return undefined;
    }
    if (!resendTimer.current) {
      resendTimer.current = setInterval(() => setResendIn((n) => (n <= 1 ? 0 : n - 1)), 1000);
    }
    return () => { clearInterval(resendTimer.current); resendTimer.current = null; };
  }, [resendIn]);

  function resetVerification() {
    setOtpStage("idle"); setOtpCode(""); setVerificationToken("");
    setVerifiedEmail(""); setResendIn(0); setDevCode("");
  }

  async function sendCode() {
    if (!emailValid) { setTouched(true); return; }
    setOtpSending(true);
    try {
      const data = await api.requestOtp(form.booker_email.trim());
      setOtpStage("sent");
      setResendIn(data.resend_after_seconds || 60);
      if (data.dev_code) setDevCode(data.dev_code);
      else toast.success("Verification code sent.");
    } catch (error) {
      toast.error(error.message || "Could not send the code.");
    } finally {
      setOtpSending(false);
    }
  }

  async function verifyCode() {
    if (!otpCode.trim()) return;
    setOtpVerifying(true);
    try {
      const data = await api.verifyOtp(form.booker_email.trim(), otpCode.trim());
      setVerificationToken(data.verification_token);
      setVerifiedEmail(form.booker_email.trim().toLowerCase());
      setOtpStage("verified");
      toast.success("Email verified.");
    } catch (error) {
      toast.error(error.message || "That code wasn't right.");
    } finally {
      setOtpVerifying(false);
    }
  }

  async function confirmBooking() {
    if (!selectedSlot) { toast.error("Pick a time first."); return; }
    setTouched(true);
    if (hasErrors || !isVerified) { toast.error("Complete your details and verify your email."); return; }

    setSubmitting(true);
    try {
      const answers = questions
        .map((question) => ({
          question_id: question.id,
          label: question.label,
          value: String(form.answers?.[question.id] || "").trim(),
        }))
        .filter((answer) => answer.value);

      const booking = await api.createBooking(slug, {
        booker_name: form.booker_name,
        booker_email: form.booker_email,
        notes: form.notes,
        start_time: selectedSlot,
        verification_token: verificationToken,
        answers,
      });

      if (booking.manage_token) {
        sessionStorage.setItem(`shopper_manage_${booking.id}`, booking.manage_token);
      }
      navigate(`/book/${slug}/confirmed/${booking.id}`);
    } catch (error) {
      toast.error(error.message || "Could not confirm the booking.");
      if (/verif/i.test(error.message || "")) resetVerification();
    } finally {
      setSubmitting(false);
    }
  }

  const dateLabel = new Date(`${selectedDate}T00:00:00`).toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric",
  });

  return (
    <div className="public">
      <header className="public-bar">
        <Logo size={28} tile />
        <ThemeToggle />
      </header>

      <main className="public-main">
        <div className="booking card">
          <aside className="booking-aside">
            {loadingEvent ? (
              <div className="stack-3">
                <Skeleton width="40%" height={12} />
                <Skeleton width="75%" height={22} />
                <Skeleton width="100%" />
              </div>
            ) : (
              <>
                <span className="booking-accent" style={{ background: eventType?.accent_color || "var(--c-accent)" }} />
                {eventType?.host_name ? <p className="small subtle">{eventType.host_name}</p> : null}
                <h1 className="booking-title">{eventType?.title}</h1>
                {eventType?.description ? <p className="small muted">{eventType.description}</p> : null}

                <div className="row-wrap" style={{ gap: 6, marginTop: "var(--s4)" }}>
                  <span className="badge"><Icon name="clock" size={12} />{eventType?.duration} min</span>
                  <span className="badge">
                    <Icon name={eventType?.location_type === "phone" ? "phone" : eventType?.location_type === "in_person" ? "pin" : "video"} size={12} />
                    {eventType?.location || (eventType?.location_type === "in_person" ? "In person" : eventType?.location_type === "phone" ? "Phone call" : "Video call")}
                  </span>
                </div>

                {eventType?.host_welcome_message ? (
                  <p className="panel small muted" style={{ marginTop: "var(--s5)" }}>{eventType.host_welcome_message}</p>
                ) : null}

                {chosenSlot ? (
                  <div className="booking-chosen">
                    <p className="tiny subtle">Selected</p>
                    <p className="small" style={{ fontWeight: 600 }}>
                      {formatTimeIn(chosenSlot.start_utc, timezone)} · {dateLabel}
                    </p>
                  </div>
                ) : null}
              </>
            )}
          </aside>

          <section className="booking-main">
            <ol className="steps">
              {STEPS.map((label, index) => (
                <li key={label} className={`step${index === step ? " is-active" : ""}${index < step ? " is-done" : ""}`}>
                  <span className="step-dot">{index < step ? <Icon name="check" size={11} strokeWidth={3} /> : index + 1}</span>
                  <span className="step-label">{label}</span>
                </li>
              ))}
            </ol>

            {step === 0 && (
              <div className="stack-4">
                <div className="row-between" style={{ flexWrap: "wrap" }}>
                  <h2>Pick a date and time</h2>
                  <TimezoneSelect value={timezone} onChange={setTimezone} />
                </div>

                <Calendar
                  selectedDate={selectedDate}
                  availableDays={availableDays}
                  loading={loadingDays}
                  onMonthChange={handleMonthChange}
                  onSelectDate={(value) => { setSelectedDate(value); setSelectedSlot(""); }}
                />

                <div>
                  <h3 className="small" style={{ marginBottom: "var(--s3)" }}>{dateLabel}</h3>
                  {loadingSlots ? (
                    <div className="slot-grid">
                      {Array.from({ length: 8 }).map((_, index) => <Skeleton key={index} height={36} radius="8px" />)}
                    </div>
                  ) : slots.length === 0 ? (
                    <p className="empty small">No open times on this day.</p>
                  ) : (
                    <div className="slot-grid" role="radiogroup" aria-label="Available times">
                      {slots.map((slot) => (
                        <button
                          key={slot.start_utc}
                          type="button"
                          role="radio"
                          aria-checked={selectedSlot === slot.start_utc}
                          className={`slot${selectedSlot === slot.start_utc ? " is-active" : ""}`}
                          onClick={() => setSelectedSlot(slot.start_utc)}
                        >
                          {formatTimeIn(slot.start_utc, timezone)}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="row-end">
                  <button className="btn btn-primary" disabled={!selectedSlot} onClick={() => setStep(1)}>
                    Continue <Icon name="arrowRight" size={14} />
                  </button>
                </div>
              </div>
            )}

            {step === 1 && (
              <div className="stack-4">
                <button className="btn-link" onClick={() => setStep(0)}>
                  <Icon name="chevronLeft" size={13} /> Back
                </button>
                <h2>Your details</h2>

                <form
                  className="stack-4"
                  onSubmit={(event) => { event.preventDefault(); setTouched(true); if (!hasErrors) setStep(2); }}
                  noValidate
                >
                  <div className="field">
                    <label className="field-label" htmlFor="name">Full name</label>
                    <input
                      id="name" className="input" placeholder="Jane Smith"
                      value={form.booker_name}
                      onChange={(event) => setForm({ ...form, booker_name: event.target.value })}
                      aria-invalid={touched && errors.booker_name ? "true" : "false"}
                    />
                    {touched && errors.booker_name && <span className="error-text">{errors.booker_name}</span>}
                  </div>

                  <div className="field">
                    <label className="field-label" htmlFor="email">Email</label>
                    <input
                      id="email" className="input" type="email" placeholder="jane@example.com"
                      value={form.booker_email}
                      onChange={(event) => {
                        setForm({ ...form, booker_email: event.target.value });
                        if (otpStage !== "idle") resetVerification();
                      }}
                      aria-invalid={touched && errors.booker_email ? "true" : "false"}
                    />
                    {touched && errors.booker_email
                      ? <span className="error-text">{errors.booker_email}</span>
                      : <span className="hint">We&apos;ll send a code here to confirm the booking.</span>}
                  </div>

                  {questions.map((question) => (
                    <QuestionField
                      key={question.id}
                      question={question}
                      value={form.answers?.[question.id]}
                      onChange={(id, value) => setForm((current) => ({ ...current, answers: { ...current.answers, [id]: value } }))}
                      error={touched ? errors[`q_${question.id}`] : ""}
                    />
                  ))}

                  <div className="field">
                    <label className="field-label" htmlFor="notes">
                      Anything else? <span className="opt">optional</span>
                    </label>
                    <textarea
                      id="notes" className="textarea" rows="3" placeholder="Context that would help"
                      value={form.notes}
                      onChange={(event) => setForm({ ...form, notes: event.target.value })}
                    />
                  </div>

                  <div className="row-end">
                    <button type="submit" className="btn btn-primary" disabled={hasErrors}>
                      Review <Icon name="arrowRight" size={14} />
                    </button>
                  </div>
                </form>
              </div>
            )}

            {step === 2 && (
              <div className="stack-4">
                <button className="btn-link" onClick={() => setStep(1)}>
                  <Icon name="chevronLeft" size={13} /> Back
                </button>
                <h2>Confirm your booking</h2>

                <dl className="dl panel">
                  <div><dt>Event</dt><dd>{eventType?.title}</dd></div>
                  <div>
                    <dt>When</dt>
                    <dd>
                      {chosenSlot ? formatTimeIn(chosenSlot.start_utc, timezone) : ""} · {dateLabel}
                      <span className="tiny subtle" style={{ display: "block", fontWeight: 400 }}>
                        {timezone.replace(/_/g, " ")} {timezoneOffsetLabel(timezone)}
                      </span>
                    </dd>
                  </div>
                  <div><dt>Guest</dt><dd>{form.booker_name}<span className="tiny subtle" style={{ display: "block", fontWeight: 400 }}>{form.booker_email}</span></dd></div>
                  {questions.map((question) => {
                    const value = form.answers?.[question.id];
                    return value ? <div key={question.id}><dt>{question.label}</dt><dd>{value}</dd></div> : null;
                  })}
                  {form.notes ? <div><dt>Notes</dt><dd>{form.notes}</dd></div> : null}
                </dl>

                {isVerified ? (
                  <p className="small" style={{ color: "var(--c-ok)", display: "flex", alignItems: "center", gap: 5 }}>
                    <Icon name="check" size={13} strokeWidth={2.6} /> Email verified
                  </p>
                ) : (
                  <div className="panel stack-3">
                    <div>
                      <p className="small" style={{ fontWeight: 600 }}>Verify your email</p>
                      <p className="tiny subtle">
                        We&apos;ll send a 6-digit code to {form.booker_email} so we know the booking is really yours.
                      </p>
                    </div>

                    {otpStage === "idle" ? (
                      <button type="button" className="btn" onClick={sendCode} disabled={!emailValid || otpSending}>
                        {otpSending ? <span className="spinner" /> : "Send code"}
                      </button>
                    ) : (
                      <div className="field">
                        <label className="field-label" htmlFor="otp">Verification code</label>
                        <div className="input-group">
                          <input
                            id="otp" className="input input-otp" inputMode="numeric" autoComplete="one-time-code"
                            maxLength={6} placeholder="000000" value={otpCode}
                            onChange={(event) => setOtpCode(event.target.value.replace(/\D/g, ""))}
                          />
                          <button type="button" className="btn btn-primary" onClick={verifyCode} disabled={otpCode.length < 4 || otpVerifying}>
                            {otpVerifying ? <span className="spinner" /> : "Verify"}
                          </button>
                        </div>
                        <span className="hint">
                          {resendIn > 0
                            ? `You can resend in ${resendIn}s`
                            : <button type="button" className="btn-link" onClick={sendCode}>Resend code</button>}
                        </span>
                        {devCode && (
                          <p className="banner banner-warn tiny">
                            Development mode — SMTP isn&apos;t configured. Your code is{" "}
                            <strong data-dev-code={devCode}>{devCode}</strong>.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}

                <div className="row-end">
                  <button className="btn btn-primary btn-lg" onClick={confirmBooking} disabled={submitting || !isVerified}>
                    {submitting ? <><span className="spinner" /> Confirming…</> : "Confirm booking"}
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
