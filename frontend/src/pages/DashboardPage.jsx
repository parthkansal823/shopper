import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import EmptyState from "../components/EmptyState";
import SectionCard from "../components/SectionCard";
import Icon from "../components/Icon";
import { SkeletonList, SkeletonStats } from "../components/Skeleton";
import { useToast } from "../components/Toast";
import { api, API_BASE } from "../services/api";

const EMPTY_FORM = {
  title: "",
  description: "",
  duration: 30,
  url_slug: "",
  accent_color: "#111113",
  is_active: true,
  buffer_minutes: 0,
  min_notice_hours: 0,
  max_advance_days: 60,
  max_bookings_per_day: 0,
  location: "",
  location_type: "video",
  questions: [],
};

const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const DURATIONS = [15, 30, 45, 60, 90, 120];
const COLORS = ["#111113", "#5c5c66", "#1d4ed8", "#6d28d9", "#be185d", "#c02626", "#c2410c", "#a16207", "#0f7a3d", "#0e7490"];

const LOCATIONS = [
  { value: "video", label: "Video call", icon: "video" },
  { value: "phone", label: "Phone call", icon: "phone" },
  { value: "in_person", label: "In person", icon: "pin" },
  { value: "custom", label: "Custom", icon: "edit" },
];

const QUESTION_TYPES = [
  { value: "text", label: "Short text" },
  { value: "textarea", label: "Long text" },
  { value: "select", label: "Dropdown" },
  { value: "checkbox", label: "Checkbox" },
  { value: "phone", label: "Phone" },
];

function slugify(value) {
  return value
    .toLowerCase().trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

/** Readable, unique-within-event id derived from the question label. */
function questionId(label, taken) {
  const base = slugify(label).replace(/-/g, "_").slice(0, 30) || "question";
  let candidate = base;
  let n = 2;
  while (taken.includes(candidate)) { candidate = `${base}_${n}`; n += 1; }
  return candidate;
}

function ShareModal({ item, onClose }) {
  const toast = useToast();
  const url = `${window.location.origin}/book/${item.url_slug}`;
  const embed = `<iframe src="${url}" width="100%" height="720" frameborder="0" title="Book ${item.title}"></iframe>`;

  async function copy(value, label) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied.`);
    } catch {
      toast.error("Could not copy — select the text instead.");
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header className="modal-head">
          <div>
            <h3 className="card-title">Share “{item.title}”</h3>
            <p className="card-sub">Anyone with the link can book a time.</p>
          </div>
          <button className="btn btn-icon btn-ghost" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button>
        </header>

        <div className="modal-body stack-4">
          <div className="field">
            <span className="field-label">Booking link</span>
            <div className="mono-box"><code>{url}</code>
              <button className="btn btn-sm" onClick={() => copy(url, "Link")}><Icon name="copy" size={12} /> Copy</button>
            </div>
          </div>

          <div className="field">
            <span className="field-label">Embed on your site</span>
            <div className="mono-box"><code>{embed}</code>
              <button className="btn btn-sm" onClick={() => copy(embed, "Embed code")}><Icon name="copy" size={12} /> Copy</button>
            </div>
          </div>

          <div className="row-2" style={{ flexWrap: "wrap" }}>
            <a className="btn btn-sm" target="_blank" rel="noreferrer"
               href={`mailto:?subject=${encodeURIComponent(`Book a ${item.title}`)}&body=${encodeURIComponent(url)}`}>
              <Icon name="mail" size={13} /> Email
            </a>
            <a className="btn btn-sm" target="_blank" rel="noreferrer"
               href={`https://wa.me/?text=${encodeURIComponent(`Book a time with me: ${url}`)}`}>
              WhatsApp
            </a>
            <a className="btn btn-sm" target="_blank" rel="noreferrer" href={url}>
              <Icon name="external" size={13} /> Preview
            </a>
          </div>
        </div>

        <footer className="modal-foot">
          <button className="btn" onClick={onClose}>Done</button>
        </footer>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const toast = useToast();

  const [summary, setSummary] = useState(null);
  const [eventTypes, setEventTypes] = useState([]);
  // Assume set up until proven otherwise, so the checklist never flashes
  // on screen for an established host while data loads.
  const [hasAvailability, setHasAvailability] = useState(true);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [touched, setTouched] = useState(false);
  const [slugTouched, setSlugTouched] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [shareTarget, setShareTarget] = useState(null);
  const [copiedSlug, setCopiedSlug] = useState("");

  const errors = useMemo(() => {
    const found = {};
    if (form.title.trim().length < 2) found.title = "Give it a name people will recognise.";
    if (!SLUG_PATTERN.test(form.url_slug)) found.url_slug = "Use lowercase letters, numbers and hyphens.";
    if (form.duration < 5 || form.duration > 480) found.duration = "Between 5 and 480 minutes.";
    return found;
  }, [form]);
  const isValid = Object.keys(errors).length === 0;

  async function load() {
    setLoading(true);
    try {
      const [summaryData, list, availability] = await Promise.all([
        api.getSummary(),
        api.getEventTypes(),
        // Only used for the setup checklist; a failure here shouldn't blank
        // the whole dashboard.
        api.getAvailability().catch(() => null),
      ]);
      setSummary(summaryData);
      setEventTypes(list);
      setHasAvailability(
        availability === null || (availability.rules || []).some((rule) => rule.is_active !== false)
      );
    } catch (error) {
      toast.error(error.message || "Could not load your dashboard.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function resetEditor() {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setTouched(false);
    setSlugTouched(false);
    setShowAdvanced(false);
  }

  function setTitle(value) {
    setForm((current) => ({
      ...current,
      title: value,
      url_slug: slugTouched ? current.url_slug : slugify(value),
    }));
  }

  function prepareQuestions(questions) {
    const taken = [];
    return questions
      .filter((question) => question.label.trim())
      .map((question) => {
        const isNew = !question.id || question.id.startsWith("question");
        const id = isNew ? questionId(question.label, taken) : question.id;
        taken.push(id);
        return {
          id,
          label: question.label.trim(),
          type: question.type,
          required: Boolean(question.required),
          placeholder: (question.placeholder || "").trim(),
          options: question.type === "select" ? question.options || [] : [],
        };
      });
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setTouched(true);
    if (!isValid) return;

    const payload = { ...form, questions: prepareQuestions(form.questions) };
    const badSelect = payload.questions.find((q) => q.type === "select" && q.options.length === 0);
    if (badSelect) {
      toast.error(`“${badSelect.label}” is a dropdown, so it needs at least one option.`);
      return;
    }

    setSubmitting(true);
    try {
      if (editingId) {
        await api.updateEventType(editingId, payload);
        toast.success("Event type updated.");
      } else {
        await api.createEventType(payload);
        toast.success("Event type created.");
      }
      resetEditor();
      load();
    } catch (error) {
      toast.error(error.message || "Could not save this event type.");
    } finally {
      setSubmitting(false);
    }
  }

  function startEdit(item) {
    setEditingId(item.id);
    setForm({ ...EMPTY_FORM, ...item, questions: item.questions ?? [] });
    setTouched(false);
    setSlugTouched(true);
    setShowAdvanced(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function remove(item) {
    if (!window.confirm(`Delete “${item.title}”? Upcoming bookings will be cancelled and guests notified.`)) return;
    try {
      await api.deleteEventType(item.id);
      toast.success("Event type deleted.");
      if (editingId === item.id) resetEditor();
      load();
    } catch (error) {
      toast.error(error.message || "Could not delete it.");
    }
  }

  async function toggle(item) {
    try {
      await api.toggleEventType(item.id);
      load();
    } catch (error) {
      toast.error(error.message || "Could not update it.");
    }
  }

  async function duplicate(item) {
    try {
      await api.duplicateEventType(item.id);
      toast.success("Duplicated — the copy is paused until you publish it.");
      load();
    } catch (error) {
      toast.error(error.message || "Could not duplicate it.");
    }
  }

  async function copyLink(slug) {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/book/${slug}`);
      setCopiedSlug(slug);
      window.setTimeout(() => setCopiedSlug(""), 1600);
    } catch {
      toast.error("Could not copy the link.");
    }
  }

  const stats = [
    { label: "Event types", value: summary?.event_types_count ?? 0 },
    { label: "Upcoming", value: summary?.upcoming_bookings_count ?? 0 },
    { label: "This week", value: summary?.this_week_count ?? 0 },
    { label: "All bookings", value: summary?.total_bookings_count ?? 0 },
  ];

  // Until availability and an event type both exist, the public booking page
  // has nothing to show — so say that plainly instead of leaving empty panels.
  const setupSteps = [
    {
      done: hasAvailability,
      label: "Set your weekly availability",
      hint: "The hours guests are allowed to book.",
      to: "/availability",
      action: "Set hours",
    },
    {
      done: eventTypes.length > 0,
      label: "Create your first event type",
      hint: "A meeting people can book — its length, and its link.",
      to: null,
      action: "Create one",
    },
  ];
  const setupRemaining = setupSteps.filter((step) => !step.done);

  return (
    <div className="stack">
      {!loading && setupRemaining.length > 0 && (
        <SectionCard
          title="Finish setting up"
          subtitle="Your booking page stays empty until these are done."
        >
          <ol className="steps-list">
            {setupSteps.map((step) => (
              <li key={step.label} className={`setup-step${step.done ? " is-done" : ""}`}>
                <span className="setup-mark" aria-hidden="true">
                  {step.done ? <Icon name="check" size={12} strokeWidth={3} /> : null}
                </span>
                <span className="setup-text">
                  <span className="setup-label">{step.label}</span>
                  <span className="tiny subtle">{step.hint}</span>
                </span>
                {!step.done && (
                  step.to
                    ? <Link className="btn btn-sm" to={step.to}>{step.action}</Link>
                    : (
                      <button
                        className="btn btn-sm"
                        onClick={() => {
                          resetEditor();
                          document.getElementById("event-type-editor")
                            ?.scrollIntoView({ behavior: "smooth", block: "start" });
                        }}
                      >
                        {step.action}
                      </button>
                    )
                )}
              </li>
            ))}
          </ol>
        </SectionCard>
      )}

      {loading && !summary ? <SkeletonStats /> : (
        <div className="grid-auto">
          {stats.map((stat) => (
            <div key={stat.label} className="card stat">
              <p className="stat-label">{stat.label}</p>
              <p className="stat-value">{stat.value}</p>
            </div>
          ))}
        </div>
      )}

      <div className="split">
        <SectionCard
          title="Your event types"
          subtitle="The meeting types guests can book."
          actions={<span className="badge">{eventTypes.length} total</span>}
        >
          {loading ? (
            <SkeletonList count={2} />
          ) : eventTypes.length === 0 ? (
            <EmptyState
              icon="calendar"
              title="No event types yet"
              description="Create your first one on the right, then share the link."
            />
          ) : (
            <div className="stack-3">
              {eventTypes.map((item) => {
                const location = LOCATIONS.find((entry) => entry.value === item.location_type) || LOCATIONS[0];
                return (
                  <article key={item.id} className={`item${item.is_active === false ? " is-muted" : ""}`} style={{ flexDirection: "column" }}>
                    <div className="item-main" style={{ width: "100%" }}>
                      <div className="row-2">
                        <span className="swatch" style={{ background: item.accent_color }} />
                        <h3 className="item-title">{item.title}</h3>
                        {item.is_active === false ? <span className="badge">Paused</span> : null}
                      </div>
                      {item.description ? <p className="small muted" style={{ marginTop: 4 }}>{item.description}</p> : null}

                      <div className="row-wrap" style={{ gap: 5, marginTop: "var(--s3)" }}>
                        <span className="badge"><Icon name="clock" size={11} />{item.duration} min</span>
                        <span className="badge"><Icon name={location.icon} size={11} />{location.label}</span>
                        {item.buffer_minutes > 0 ? <span className="badge">{item.buffer_minutes} min buffer</span> : null}
                        {(item.questions || []).length > 0 ? <span className="badge">{item.questions.length} question{item.questions.length > 1 ? "s" : ""}</span> : null}
                        <span className="badge badge-mono">/book/{item.url_slug}</span>
                      </div>
                    </div>

                    <div className="item-foot" style={{ width: "100%" }}>
                      <div className="item-actions">
                        <button className="btn btn-sm" onClick={() => copyLink(item.url_slug)}>
                          <Icon name={copiedSlug === item.url_slug ? "check" : "copy"} size={12} />
                          {copiedSlug === item.url_slug ? "Copied" : "Copy link"}
                        </button>
                        <button className="btn btn-sm" onClick={() => setShareTarget(item)}><Icon name="share" size={12} /> Share</button>
                        <a className="btn btn-sm" href={`/book/${item.url_slug}`} target="_blank" rel="noreferrer"><Icon name="external" size={12} /> Preview</a>
                      </div>

                      <div className="item-actions">
                        <button className="btn btn-icon btn-ghost" onClick={() => duplicate(item)} title="Duplicate" aria-label="Duplicate"><Icon name="duplicate" size={14} /></button>
                        <button className="btn btn-icon btn-ghost" onClick={() => toggle(item)} title={item.is_active === false ? "Activate" : "Pause"} aria-label={item.is_active === false ? "Activate" : "Pause"}>
                          <Icon name={item.is_active === false ? "play" : "pause"} size={14} />
                        </button>
                        <button className="btn btn-icon btn-ghost" onClick={() => startEdit(item)} title="Edit" aria-label="Edit"><Icon name="edit" size={14} /></button>
                        <button className="btn btn-icon btn-ghost btn-danger" onClick={() => remove(item)} title="Delete" aria-label="Delete"><Icon name="trash" size={14} /></button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </SectionCard>

        <SectionCard
          id="event-type-editor"
          title={editingId ? "Edit event type" : "New event type"}
          subtitle={editingId ? "Changes apply to future bookings." : "Set up a booking page in under a minute."}
          actions={editingId ? <button className="btn btn-sm btn-ghost" onClick={resetEditor}>Cancel</button> : null}
        >
          <form className="stack-4" onSubmit={handleSubmit} noValidate>
            <div className="field">
              <label className="field-label" htmlFor="et-title">Title</label>
              <input id="et-title" className="input" value={form.title} placeholder="Intro Call"
                     onChange={(event) => setTitle(event.target.value)}
                     aria-invalid={touched && errors.title ? "true" : "false"} />
              {touched && errors.title && <span className="error-text">{errors.title}</span>}
            </div>

            <div className="field">
              <label className="field-label" htmlFor="et-slug">Booking link</label>
              <div className="input-affix">
                <span className="affix affix-start">/book/</span>
                <input id="et-slug" className="input input-mono" value={form.url_slug} placeholder="intro-call"
                       onChange={(event) => { setSlugTouched(true); setForm({ ...form, url_slug: slugify(event.target.value) }); }} />
              </div>
              {touched && errors.url_slug ? <span className="error-text">{errors.url_slug}</span>
                : <span className="hint">Created from the title until you edit it yourself.</span>}
            </div>

            <div className="field">
              <label className="field-label" htmlFor="et-desc">Description <span className="opt">optional</span></label>
              <textarea id="et-desc" className="textarea" rows="2" value={form.description}
                        placeholder="What should guests expect from this meeting?"
                        onChange={(event) => setForm({ ...form, description: event.target.value })} />
            </div>

            <div className="field">
              <span className="field-label">Duration</span>
              <div className="row-wrap" style={{ gap: 5 }}>
                {DURATIONS.map((minutes) => (
                  <button key={minutes} type="button"
                          className={`chip${form.duration === minutes ? " is-active" : ""}`}
                          onClick={() => setForm({ ...form, duration: minutes })}>
                    {minutes}m
                  </button>
                ))}
              </div>
              <div className="input-affix" style={{ maxWidth: 150, marginTop: 6 }}>
                <input className="input num" type="number" min="5" max="480" value={form.duration}
                       onChange={(event) => setForm({ ...form, duration: Number(event.target.value) })} />
                <span className="affix affix-end">min</span>
              </div>
              {touched && errors.duration && <span className="error-text">{errors.duration}</span>}
            </div>

            <div className="field">
              <span className="field-label">Where does it happen?</span>
              <div className="tile-grid">
                {LOCATIONS.map((location) => (
                  <button key={location.value} type="button"
                          className={`tile-btn${form.location_type === location.value ? " is-active" : ""}`}
                          onClick={() => setForm({ ...form, location_type: location.value })}>
                    <Icon name={location.icon} size={16} />
                    {location.label}
                  </button>
                ))}
              </div>
              {(form.location_type === "in_person" || form.location_type === "custom") && (
                <input className="input" style={{ marginTop: 8 }} value={form.location}
                       placeholder={form.location_type === "in_person" ? "Address or venue" : "Instructions for guests"}
                       onChange={(event) => setForm({ ...form, location: event.target.value })} />
              )}
            </div>

            <div className="field">
              <span className="field-label">Accent colour</span>
              <div className="swatch-grid">
                {COLORS.map((color) => (
                  <button key={color} type="button" className="swatch-btn"
                          style={{ background: color }}
                          aria-pressed={form.accent_color === color}
                          aria-label={`Use ${color}`}
                          onClick={() => setForm({ ...form, accent_color: color })} />
                ))}
              </div>
            </div>

            <div className="panel row-between">
              <div>
                <p className="small" style={{ fontWeight: 600 }}>Accepting bookings</p>
                <p className="hint">Paused pages stay hidden from guests.</p>
              </div>
              <button type="button" className="switch" role="switch" aria-checked={form.is_active}
                      aria-label="Accepting bookings"
                      onClick={() => setForm({ ...form, is_active: !form.is_active })} />
            </div>

            <button type="button" className="btn btn-sm" onClick={() => setShowAdvanced((value) => !value)}>
              <Icon name={showAdvanced ? "chevronDown" : "chevronRight"} size={13} />
              {showAdvanced ? "Hide advanced settings" : "Advanced settings"}
            </button>

            {showAdvanced && (
              <div className="stack-4">
                <div className="grid-2">
                  <div className="field">
                    <label className="field-label" htmlFor="buffer">Buffer after</label>
                    <div className="input-affix">
                      <input id="buffer" className="input num" type="number" min="0" max="120" step="5" value={form.buffer_minutes}
                             onChange={(event) => setForm({ ...form, buffer_minutes: Number(event.target.value) })} />
                      <span className="affix affix-end">min</span>
                    </div>
                    <span className="hint">Breathing room between meetings.</span>
                  </div>

                  <div className="field">
                    <label className="field-label" htmlFor="notice">Minimum notice</label>
                    <div className="input-affix">
                      <input id="notice" className="input num" type="number" min="0" max="168" value={form.min_notice_hours}
                             onChange={(event) => setForm({ ...form, min_notice_hours: Number(event.target.value) })} />
                      <span className="affix affix-end">hrs</span>
                    </div>
                    <span className="hint">How much warning you need.</span>
                  </div>
                </div>

                <div className="grid-2">
                  <div className="field">
                    <label className="field-label" htmlFor="advance">Bookable up to</label>
                    <div className="input-affix">
                      <input id="advance" className="input num" type="number" min="1" max="365" value={form.max_advance_days}
                             onChange={(event) => setForm({ ...form, max_advance_days: Number(event.target.value) })} />
                      <span className="affix affix-end">days ahead</span>
                    </div>
                  </div>

                  <div className="field">
                    <label className="field-label" htmlFor="daily-cap">Daily limit</label>
                    <div className="input-affix">
                      <input id="daily-cap" className="input num" type="number" min="0" max="50" value={form.max_bookings_per_day}
                             onChange={(event) => setForm({ ...form, max_bookings_per_day: Number(event.target.value) })} />
                      <span className="affix affix-end">per day</span>
                    </div>
                    <span className="hint">
                      {form.max_bookings_per_day > 0
                        ? `Closes the day after ${form.max_bookings_per_day} booking${form.max_bookings_per_day === 1 ? "" : "s"}.`
                        : "0 means no limit."}
                    </span>
                  </div>
                </div>

                <div className="field">
                  <span className="field-label">Booking questions</span>
                  <span className="hint">Answers arrive with the booking and in the CSV export.</span>

                  <div className="stack-3" style={{ marginTop: 8 }}>
                    {form.questions.length === 0 ? (
                      <p className="empty small">No extra questions — guests just give a name and email.</p>
                    ) : (
                      form.questions.map((question, index) => (
                        <div className="question" key={question.id || index}>
                          <div className="question-top">
                            <input className="input" value={question.label} placeholder="What should we cover?"
                                   onChange={(event) => setForm((current) => ({
                                     ...current,
                                     questions: current.questions.map((q, i) => (i === index ? { ...q, label: event.target.value } : q)),
                                   }))} />
                            <button type="button" className="btn btn-icon btn-danger" aria-label="Remove question"
                                    onClick={() => setForm((current) => ({
                                      ...current,
                                      questions: current.questions.filter((_, i) => i !== index),
                                    }))}>
                              <Icon name="trash" size={13} />
                            </button>
                          </div>

                          <div className="question-meta">
                            <select className="select" value={question.type}
                                    onChange={(event) => setForm((current) => ({
                                      ...current,
                                      questions: current.questions.map((q, i) => (i === index ? { ...q, type: event.target.value } : q)),
                                    }))}>
                              {QUESTION_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
                            </select>

                            <label className="check tiny">
                              <input type="checkbox" checked={question.required}
                                     onChange={(event) => setForm((current) => ({
                                       ...current,
                                       questions: current.questions.map((q, i) => (i === index ? { ...q, required: event.target.checked } : q)),
                                     }))} />
                              Required
                            </label>
                          </div>

                          {question.type === "select" ? (
                            <input className="input" value={(question.options || []).join(", ")} placeholder="1-10, 11-50, 51+"
                                   onChange={(event) => setForm((current) => ({
                                     ...current,
                                     questions: current.questions.map((q, i) => (i === index
                                       ? { ...q, options: event.target.value.split(",").map((o) => o.trim()).filter(Boolean) }
                                       : q)),
                                   }))} />
                          ) : (
                            <input className="input" value={question.placeholder || ""} placeholder="Placeholder text (optional)"
                                   onChange={(event) => setForm((current) => ({
                                     ...current,
                                     questions: current.questions.map((q, i) => (i === index ? { ...q, placeholder: event.target.value } : q)),
                                   }))} />
                          )}
                        </div>
                      ))
                    )}

                    <button type="button" className="btn btn-sm" disabled={form.questions.length >= 10}
                            onClick={() => setForm((current) => ({
                              ...current,
                              questions: [...current.questions, {
                                id: questionId("question", current.questions.map((q) => q.id)),
                                label: "", type: "text", required: false, placeholder: "", options: [],
                              }],
                            }))}>
                      <Icon name="plus" size={13} /> Add question
                    </button>
                  </div>
                </div>
              </div>
            )}

            <div className="row-2">
              <button type="submit" className="btn btn-primary" disabled={submitting}>
                {submitting ? <><span className="spinner" /> Saving…</> : editingId ? "Save changes" : "Create event type"}
              </button>
              {editingId && <button type="button" className="btn btn-ghost" onClick={resetEditor}>Cancel</button>}
            </div>
          </form>
        </SectionCard>
      </div>

      {shareTarget && <ShareModal item={shareTarget} onClose={() => setShareTarget(null)} />}
    </div>
  );
}
