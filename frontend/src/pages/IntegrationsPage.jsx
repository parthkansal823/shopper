import { useEffect, useMemo, useState } from "react";
import SectionCard from "../components/SectionCard";
import Icon from "../components/Icon";
import { useToast } from "../components/Toast";
import { api } from "../services/api";

const CATALOGUE = [
  { key: "slack", name: "Slack", icon: "zap", type: "webhook", blurb: "Post a message to a channel on every booking." },
  { key: "discord", name: "Discord", icon: "zap", type: "webhook", blurb: "Send booking activity to a Discord channel." },
  { key: "teams_notify", name: "Microsoft Teams", icon: "zap", type: "webhook", blurb: "Notify a Teams channel via an incoming webhook." },
  { key: "generic_webhook", name: "Custom webhook", icon: "link", type: "webhook", blurb: "POST the raw booking payload to any URL." },
  { key: "zoom", name: "Zoom", icon: "video", type: "video_url", blurb: "Use a fixed personal meeting room." },
  { key: "teams", name: "Teams meeting", icon: "video", type: "video_url", blurb: "Use a standing Teams meeting link." },
  { key: "webex", name: "Webex", icon: "video", type: "video_url", blurb: "Use a personal Webex room." },
];

const EVENTS = ["booking.confirmed", "booking.cancelled", "booking.rescheduled"];

function ConnectModal({ integration, existing, onClose, onSave }) {
  const isWebhook = integration.type === "webhook";
  const [config, setConfig] = useState(() => ({
    webhook_url: existing?.config?.webhook_url || "",
    format: existing?.config?.format || (integration.key === "slack" ? "slack" : integration.key === "discord" ? "discord" : "json"),
    events: existing?.config?.events || EVENTS,
    meeting_url: existing?.config?.meeting_url || "",
  }));
  const [saving, setSaving] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    await onSave(integration.key, config);
    setSaving(false);
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <form className="modal" onClick={(event) => event.stopPropagation()} onSubmit={submit}>
        <header className="modal-head">
          <div>
            <h3 className="card-title">Connect {integration.name}</h3>
            <p className="card-sub">{integration.blurb}</p>
          </div>
          <button type="button" className="btn btn-icon btn-ghost" onClick={onClose} aria-label="Close">
            <Icon name="close" size={16} />
          </button>
        </header>

        <div className="modal-body stack-4">
          {isWebhook ? (
            <>
              <div className="field">
                <label className="field-label" htmlFor="hook-url">Webhook URL</label>
                <input
                  id="hook-url" className="input input-mono" type="url" required
                  placeholder="https://hooks.slack.com/services/…"
                  value={config.webhook_url}
                  onChange={(event) => setConfig({ ...config, webhook_url: event.target.value })}
                />
              </div>

              <div className="field">
                <label className="field-label" htmlFor="hook-format">Payload format</label>
                <select id="hook-format" className="select" value={config.format}
                        onChange={(event) => setConfig({ ...config, format: event.target.value })}>
                  <option value="json">Raw JSON</option>
                  <option value="slack">Slack message</option>
                  <option value="discord">Discord message</option>
                </select>
              </div>

              <div className="field">
                <span className="field-label">Send on</span>
                {EVENTS.map((eventName) => (
                  <label className="check" key={eventName}>
                    <input
                      type="checkbox"
                      checked={config.events.includes(eventName)}
                      onChange={(changed) => setConfig({
                        ...config,
                        events: changed.target.checked
                          ? [...config.events, eventName]
                          : config.events.filter((item) => item !== eventName),
                      })}
                    />
                    <span className="mono">{eventName}</span>
                  </label>
                ))}
              </div>
            </>
          ) : (
            <div className="field">
              <label className="field-label" htmlFor="meet-url">Meeting link</label>
              <input
                id="meet-url" className="input input-mono" type="url" required
                placeholder="https://zoom.us/j/1234567890"
                value={config.meeting_url}
                onChange={(event) => setConfig({ ...config, meeting_url: event.target.value })}
              />
              <span className="hint">Guests receive this link with every confirmation.</span>
            </div>
          )}
        </div>

        <footer className="modal-foot">
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? <><span className="spinner" /> Saving…</> : "Connect"}
          </button>
        </footer>
      </form>
    </div>
  );
}

export default function IntegrationsPage() {
  const toast = useToast();

  const [connections, setConnections] = useState({});
  const [loading, setLoading] = useState(true);
  const [target, setTarget] = useState(null);
  const [testing, setTesting] = useState("");
  const [search, setSearch] = useState("");

  const [apiKeys, setApiKeys] = useState([]);
  const [freshKey, setFreshKey] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);

  const [feedUrl, setFeedUrl] = useState("");
  const [feedBusy, setFeedBusy] = useState(false);

  const [calendarSync, setCalendarSync] = useState({ connected: false, configured: true, needs_reconnect: false });
  const [calBusy, setCalBusy] = useState(false);

  const [emailTest, setEmailTest] = useState(null);
  const [emailBusy, setEmailBusy] = useState(false);

  const [gmail, setGmail] = useState({ connected: false, configured: true, needs_reconnect: false, email: "" });
  const [gmailBusy, setGmailBusy] = useState(false);

  async function refreshGmail() {
    try {
      setGmail(await api.getGmailStatus());
    } catch {
      setGmail((current) => ({ ...current, connected: false }));
    }
  }

  async function connectGmail() {
    setGmailBusy(true);
    try {
      const { url } = await api.startGmailConnect();
      window.location.href = url;
    } catch (error) {
      toast.error(error.message || "Could not start Gmail setup.");
      setGmailBusy(false);
    }
  }

  async function disconnectGmail() {
    if (!window.confirm("Disconnect Gmail? Outgoing mail falls back to SMTP, which many hosts block.")) return;
    setGmailBusy(true);
    try {
      await api.disconnectGmail();
      toast.success("Gmail disconnected.");
      refreshGmail();
    } catch (error) {
      toast.error(error.message || "Could not disconnect.");
    } finally {
      setGmailBusy(false);
    }
  }

  async function runEmailTest() {
    setEmailBusy(true);
    setEmailTest(null);
    try {
      const result = await api.testEmailDelivery();
      setEmailTest(result);
      if (result.delivered) toast.success("Test email sent — check your inbox.");
      else toast.error("This server could not send email.");
    } catch (error) {
      setEmailTest({ delivered: false, error: error.message || "Request failed." });
      toast.error(error.message || "Could not run the test.");
    } finally {
      setEmailBusy(false);
    }
  }

  async function refreshCalendarSync() {
    try {
      setCalendarSync(await api.getCalendarSyncStatus());
    } catch {
      setCalendarSync((current) => ({ ...current, connected: false }));
    }
  }

  async function connectCalendar() {
    setCalBusy(true);
    try {
      const { url } = await api.startCalendarSync();
      window.location.href = url;   // leaves the app for Google's consent screen
    } catch (error) {
      toast.error(error.message || "Could not start Google Calendar setup.");
      setCalBusy(false);
    }
  }

  async function disconnectCalendar() {
    if (!window.confirm("Disconnect Google Calendar? Your personal events will stop blocking times.")) return;
    setCalBusy(true);
    try {
      await api.disconnectCalendarSync();
      toast.success("Google Calendar disconnected.");
      refreshCalendarSync();
    } catch (error) {
      toast.error(error.message || "Could not disconnect.");
    } finally {
      setCalBusy(false);
    }
  }

  async function load() {
    setLoading(true);
    try {
      const [list, keys] = await Promise.all([api.getIntegrations(), api.getApiKeys().catch(() => [])]);
      setConnections(Object.fromEntries(list.map((item) => [item.key, item])));
      setApiKeys(keys);
    } catch (error) {
      toast.error(error.message || "Could not load integrations.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);
  useEffect(() => {
    api.getCalendarFeed().then((feed) => setFeedUrl(feed.url)).catch(() => setFeedUrl(""));
  }, []);

  // The Google consent screen returns to /integrations?calendar=<result>.
  // Report it, then strip the parameter so a refresh doesn't repeat the toast.
  useEffect(() => {
    refreshCalendarSync();
    refreshGmail();

    const params = new URLSearchParams(window.location.search);
    const outcomes = {
      calendar: {
        connected: ["success", "Google Calendar connected — your events now block times."],
        denied: ["error", "Google Calendar access was declined."],
        invalid: ["error", "That setup link expired. Please try connecting again."],
        failed: ["error", "Google rejected the connection. Please try again."],
        norefresh: ["error", "Google didn't return lasting access. Remove Shopper from your Google account permissions, then reconnect."],
      },
      gmail: {
        connected: ["success", "Gmail connected — outgoing mail now sends over HTTPS."],
        denied: ["error", "Gmail access was declined."],
        invalid: ["error", "That setup link expired. Please try connecting again."],
        failed: ["error", "Google rejected the connection. Please try again."],
        norefresh: ["error", "Google didn't return lasting access. Remove Shopper from your Google account permissions, then reconnect."],
      },
    };

    let matched = false;
    for (const [key, messages] of Object.entries(outcomes)) {
      const [kind, message] = messages[params.get(key)] || [];
      if (!kind) continue;
      matched = true;
      if (kind === "success") toast.success(message);
      else toast.error(message);
    }
    if (matched) window.history.replaceState({}, "", window.location.pathname);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return CATALOGUE;
    return CATALOGUE.filter((item) => `${item.name} ${item.blurb}`.toLowerCase().includes(query));
  }, [search]);

  async function save(key, config) {
    try {
      await api.saveIntegration(key, config);
      toast.success("Integration connected.");
      setTarget(null);
      load();
    } catch (error) {
      toast.error(error.message || "Could not connect it.");
    }
  }

  async function disconnect(key) {
    if (!window.confirm("Disconnect this integration?")) return;
    try {
      await api.disconnectIntegration(key);
      toast.success("Disconnected.");
      load();
    } catch (error) {
      toast.error(error.message || "Could not disconnect it.");
    }
  }

  async function test(key) {
    setTesting(key);
    try {
      await api.testIntegration(key);
      toast.success("Test sent — check the destination.");
    } catch (error) {
      toast.error(error.message || "Test failed.");
    } finally {
      setTesting("");
    }
  }

  async function copy(value, label) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied.`);
    } catch {
      toast.error("Could not copy.");
    }
  }

  async function createKey() {
    setKeyBusy(true);
    try {
      const created = await api.generateApiKey();
      setFreshKey(created.key);
      setApiKeys([{ prefix: created.prefix, created_at: created.created_at }]);
      toast.success("API key created — copy it now.");
    } catch (error) {
      toast.error(error.message || "Could not create a key.");
    } finally {
      setKeyBusy(false);
    }
  }

  async function revokeKeys() {
    if (!window.confirm("Revoke the API key? Anything using it will stop working.")) return;
    try {
      await api.revokeApiKey();
      setApiKeys([]);
      setFreshKey("");
      toast.success("API key revoked.");
    } catch (error) {
      toast.error(error.message || "Could not revoke it.");
    }
  }

  async function rotateFeed() {
    if (!window.confirm("Generate a new calendar URL? Existing subscriptions stop updating.")) return;
    setFeedBusy(true);
    try {
      const feed = await api.rotateCalendarFeed();
      setFeedUrl(feed.url);
      toast.success("New URL generated — re-subscribe your calendar.");
    } catch (error) {
      toast.error(error.message || "Could not rotate the URL.");
    } finally {
      setFeedBusy(false);
    }
  }

  const connectedCount = Object.keys(connections).length;

  return (
    <div className="stack">
      <div className="grid-auto">
        <div className="card stat"><p className="stat-label">Connected</p><p className="stat-value">{loading ? "—" : connectedCount}</p></div>
        <div className="card stat"><p className="stat-label">Available</p><p className="stat-value">{CATALOGUE.length}</p></div>
        <div className="card stat"><p className="stat-label">API keys</p><p className="stat-value">{apiKeys.length}</p></div>
      </div>

      <SectionCard
        title="Email delivery"
        subtitle="How confirmations and verification codes leave the server."
      >
        <div className="stack-3">
          {gmail.configured && (
            <div className="row-between" style={{ flexWrap: "wrap", gap: "var(--s3)" }}>
              <div style={{ maxWidth: "52ch" }}>
                <p className="small" style={{ fontWeight: 600 }}>
                  Send through Gmail
                  {gmail.connected && (
                    <span className="badge" style={{ marginLeft: 8 }}>
                      <Icon name="check" size={11} strokeWidth={3} /> Active
                    </span>
                  )}
                </p>
                <p className="tiny subtle">
                  {gmail.connected
                    ? `Mail is sent from ${gmail.email} over HTTPS.`
                    : "Most hosts block the ports SMTP needs, which is why mail can work locally but not once deployed. Sending through Gmail uses HTTPS instead, and needs no other service."}
                </p>
              </div>
              {gmail.connected ? (
                <button className="btn btn-sm btn-ghost btn-danger" onClick={disconnectGmail} disabled={gmailBusy}>
                  {gmailBusy ? <span className="spinner" /> : <Icon name="close" size={13} />} Disconnect
                </button>
              ) : (
                <button className="btn btn-sm" onClick={connectGmail} disabled={gmailBusy}>
                  {gmailBusy ? <span className="spinner" /> : <Icon name="link" size={13} />} Connect Gmail
                </button>
              )}
            </div>
          )}

          {gmail.needs_reconnect && (
            <p className="banner banner-warn tiny">
              Google rejected the saved permission — access was probably revoked. Reconnect to resume sending.
            </p>
          )}
          <div className="row-between" style={{ flexWrap: "wrap", gap: "var(--s3)" }}>
            <p className="tiny subtle" style={{ maxWidth: "52ch" }}>
              Booking confirmations and verification codes all go out from the server the
              app is running on. If mail works locally but not once deployed, run this
              there — it reports the actual reason instead of failing silently.
            </p>
            <button className="btn btn-sm" onClick={runEmailTest} disabled={emailBusy}>
              {emailBusy ? <span className="spinner" /> : <Icon name="zap" size={13} />} Send test email
            </button>
          </div>

          {emailTest && (
            emailTest.delivered ? (
              <p className="banner banner-ok tiny">
                Delivered to your address via {emailTest.host}:{emailTest.port}. If it isn&apos;t
                in your inbox, check spam.
              </p>
            ) : (
              <div className="banner banner-danger tiny">
                <p style={{ margin: 0, fontWeight: 600 }}>
                  Could not send{emailTest.error_type ? ` — ${emailTest.error_type}` : ""}
                </p>
                {emailTest.error && (
                  <p className="mono" style={{ margin: "6px 0 0", wordBreak: "break-word" }}>
                    {emailTest.error}
                  </p>
                )}
                {emailTest.hint && <p style={{ margin: "6px 0 0" }}>{emailTest.hint}</p>}
              </div>
            )
          )}
        </div>
      </SectionCard>

      <SectionCard
        title="Google Calendar sync"
        subtitle="Check your real calendar before offering a time, so you're never double-booked."
      >
        {!calendarSync.configured ? (
          <p className="hint">
            Google OAuth isn&apos;t configured on the server, so calendar sync is unavailable.
          </p>
        ) : (
          <div className="stack-3">
            <div className="row-between" style={{ flexWrap: "wrap", gap: "var(--s3)" }}>
              <div>
                <p className="small" style={{ fontWeight: 600 }}>
                  {calendarSync.connected ? "Connected" : "Not connected"}
                  {calendarSync.connected && (
                    <span className="badge" style={{ marginLeft: 8 }}>
                      <Icon name="check" size={11} strokeWidth={3} /> Checking conflicts
                    </span>
                  )}
                </p>
                <p className="tiny subtle">
                  {calendarSync.connected
                    ? "Events in your Google Calendar now hide the overlapping slots on your booking page."
                    : "Shopper only knows about bookings made here. Connect your calendar so personal events block those times too."}
                </p>
              </div>

              {calendarSync.connected ? (
                <button className="btn btn-sm btn-ghost btn-danger" onClick={disconnectCalendar} disabled={calBusy}>
                  {calBusy ? <span className="spinner" /> : <Icon name="close" size={13} />} Disconnect
                </button>
              ) : (
                <button className="btn btn-sm" onClick={connectCalendar} disabled={calBusy}>
                  {calBusy ? <span className="spinner" /> : <Icon name="link" size={13} />} Connect Google Calendar
                </button>
              )}
            </div>

            {calendarSync.needs_reconnect && (
              <p className="banner banner-warn tiny">
                Google rejected the saved permission — this usually means access was revoked.
                Reconnect to resume conflict checking.
              </p>
            )}

            <p className="hint">
              Read-only. Shopper reads busy times only — never event titles, guests or details.
            </p>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Calendar subscription"
        subtitle="Add confirmed bookings to Google Calendar, Apple Calendar or Outlook."
      >
        <div className="stack-3">
          <div className="mono-box">
            <code>{feedUrl || "Loading your private feed URL…"}</code>
            <button className="btn btn-sm" disabled={!feedUrl} onClick={() => copy(feedUrl, "Calendar URL")}>
              <Icon name="copy" size={12} /> Copy
            </button>
          </div>
          <p className="hint">
            Treat this like a password — anyone with the link can read your bookings. Paste it into
            your calendar app's “subscribe by URL” option.
          </p>
          <div>
            <button className="btn btn-sm btn-ghost btn-danger" onClick={rotateFeed} disabled={feedBusy || !feedUrl}>
              {feedBusy ? <span className="spinner" /> : <Icon name="refresh" size={13} />} Generate new URL
            </button>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title="Integrations"
        subtitle="Send booking activity where your team already works."
        actions={
          <div className="search">
            <Icon name="search" size={14} />
            <input className="input" style={{ minWidth: 190 }} placeholder="Search integrations"
                   value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
        }
      >
        <div className="grid-3">
          {visible.map((item) => {
            const existing = connections[item.key];
            return (
              <article key={item.key} className="card card-body stack-3">
                <div className="row-between" style={{ alignItems: "flex-start" }}>
                  <span className="feature-icon" style={{ marginBottom: 0 }}><Icon name={item.icon} size={16} /></span>
                  {existing ? <span className="badge badge-ok"><span className="dot" />Connected</span> : null}
                </div>

                <div>
                  <h3 style={{ fontSize: "0.875rem" }}>{item.name}</h3>
                  <p className="tiny muted" style={{ marginTop: 2 }}>{item.blurb}</p>
                </div>

                <div className="row-2" style={{ flexWrap: "wrap" }}>
                  <button className="btn btn-sm" onClick={() => setTarget(item)}>
                    {existing ? "Edit" : "Connect"}
                  </button>
                  {existing && item.type === "webhook" && (
                    <button className="btn btn-sm" onClick={() => test(item.key)} disabled={testing === item.key}>
                      {testing === item.key ? <span className="spinner" /> : null} Test
                    </button>
                  )}
                  {existing && (
                    <button className="btn btn-sm btn-ghost btn-danger" onClick={() => disconnect(item.key)}>Remove</button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </SectionCard>

      <SectionCard title="API access" subtitle="Use the same endpoints as the dashboard from your own code.">
        <div className="stack-4">
          {freshKey && (
            <div className="banner banner-warn">
              <Icon name="alert" size={15} />
              <div style={{ minWidth: 0 }}>
                <p className="small" style={{ fontWeight: 600 }}>Copy this key now — it is not shown again.</p>
                <div className="mono-box" style={{ marginTop: 8 }}>
                  <code>{freshKey}</code>
                  <button className="btn btn-sm" onClick={() => copy(freshKey, "API key")}><Icon name="copy" size={12} /> Copy</button>
                </div>
              </div>
            </div>
          )}

          {apiKeys.length === 0 ? (
            <p className="empty small">No API key yet.</p>
          ) : (
            <div className="list-bordered">
              {apiKeys.map((key) => (
                <div className="list-row" key={key.prefix}>
                  <div className="row-2">
                    <Icon name="key" size={15} className="subtle" />
                    <div>
                      <p className="small mono">{key.prefix}…</p>
                      <p className="tiny subtle">
                        Created {key.created_at ? new Date(key.created_at).toLocaleDateString() : "recently"}
                      </p>
                    </div>
                  </div>
                  <button className="btn btn-sm btn-ghost btn-danger" onClick={revokeKeys}>Revoke</button>
                </div>
              ))}
            </div>
          )}

          <div className="row-2">
            <button className="btn" onClick={createKey} disabled={keyBusy}>
              {keyBusy ? <span className="spinner" /> : <Icon name="plus" size={13} />}
              {apiKeys.length ? "Replace key" : "Create API key"}
            </button>
          </div>

          <div className="mono-box" style={{ display: "block", whiteSpace: "pre-wrap" }}>
            <code>{`curl -H "Authorization: Bearer sk_live_…" \\\n  ${window.location.origin.replace(/^https?:\/\//, "https://")}/api/bookings`}</code>
          </div>
        </div>
      </SectionCard>

      {target && (
        <ConnectModal
          integration={target}
          existing={connections[target.key]}
          onClose={() => setTarget(null)}
          onSave={save}
        />
      )}
    </div>
  );
}
