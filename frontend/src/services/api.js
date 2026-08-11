const API_BASE = import.meta.env.VITE_API_URL || "https://shopper-backend-mqcf.onrender.com";

export const TOKEN_KEY = "shopper_token";
export const USER_KEY = "shopper_user";

function getToken() {
  return localStorage.getItem(TOKEN_KEY) || null;
}

/** Routes an unauthenticated visitor can be on without a token being a bug. */
function isPublicRoute() {
  const path = window.location.pathname;
  return (
    path === "/" ||
    path === "/login" ||
    path.startsWith("/book/") ||
    path.startsWith("/manage/") ||
    path.startsWith("/auth/")
  );
}

async function request(path, options = {}) {
  const { hideSpinUpWarning, raw, ...fetchOptions } = options;

  let timeoutId;
  if (!hideSpinUpWarning) {
    // The free Render tier sleeps; warn the user rather than look frozen.
    timeoutId = setTimeout(() => {
      window.dispatchEvent(new CustomEvent("api-slow"));
    }, 4000);
  }

  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (!raw) headers["Content-Type"] = headers["Content-Type"] || "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  try {
    const response = await fetch(`${API_BASE}${path}`, { ...fetchOptions, headers });

    if (timeoutId) clearTimeout(timeoutId);
    window.dispatchEvent(new CustomEvent("api-fast"));

    if (response.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      // A 401 on a public page is expected (no session); only bounce a user
      // who was actually inside the dashboard.
      if (!isPublicRoute()) window.location.href = "/login";
      throw new Error("Session expired. Please sign in again.");
    }

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(formatError(data) || "Something went wrong");
    }

    if (raw) return response;
    if (response.status === 204) return null;
    return response.json();
  } catch (error) {
    if (timeoutId) clearTimeout(timeoutId);
    window.dispatchEvent(new CustomEvent("api-fast"));
    throw error;
  }
}

/** FastAPI validation errors arrive as a list of objects, not a string. */
function formatError(data) {
  const detail = data?.detail;
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "string" ? d : d.msg || ""))
      .filter(Boolean)
      .join(". ");
  }
  return "";
}

const json = (method, body) => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  // ---------------------------------------------------------------- auth --
  register: (payload) => request("/api/auth/register", json("POST", payload)),
  login: (payload) => request("/api/auth/login", json("POST", payload)),
  getMe: () => request("/api/auth/me"),
  updateProfile: (payload) => request("/api/auth/profile", json("PUT", payload)),
  changePassword: (payload) => request("/api/auth/change-password", json("PUT", payload)),
  googleLoginUrl: () => `${API_BASE}/api/auth/google`,

  testEmailDelivery: () => request("/api/auth/email/test", json("POST")),
  getGmailStatus: () => request("/api/auth/google/gmail/status"),
  startGmailConnect: () => request("/api/auth/google/gmail/connect"),
  disconnectGmail: () => request("/api/auth/google/gmail", json("DELETE")),

  // ------------------------------------------------- Google Calendar sync --
  getCalendarSyncStatus: () => request("/api/auth/google/calendar/status"),
  startCalendarSync: () => request("/api/auth/google/calendar/connect"),
  disconnectCalendarSync: () => request("/api/auth/google/calendar", json("DELETE")),

  // --------------------------------------------------------- event types --
  getSummary: () => request("/api/summary"),
  getEventTypes: () => request("/api/event-types"),
  createEventType: (payload) => request("/api/event-types", json("POST", payload)),
  updateEventType: (id, payload) => request(`/api/event-types/${id}`, json("PUT", payload)),
  deleteEventType: (id) => request(`/api/event-types/${id}`, json("DELETE")),
  toggleEventType: (id) => request(`/api/event-types/${id}/toggle`, json("PATCH")),
  duplicateEventType: (id) => request(`/api/event-types/${id}/duplicate`, json("POST")),

  // -------------------------------------------------------- availability --
  getAvailability: () => request("/api/availability"),
  updateAvailability: (payload) => request("/api/availability", json("PUT", payload)),
  getTimezones: () => request("/api/timezones"),

  // ------------------------------------------------------------ bookings --
  getBookings: (params = {}) => {
    // Accepts either a scope string or a params object.
    const opts = typeof params === "string" ? { scope: params } : params;
    const query = new URLSearchParams();
    query.set("scope", opts.scope || "all");
    if (opts.search) query.set("search", opts.search);
    if (opts.eventTypeId) query.set("event_type_id", opts.eventTypeId);
    return request(`/api/bookings?${query.toString()}`);
  },
  createAdminBooking: (payload) => request("/api/bookings", json("POST", payload)),
  cancelBooking: (id) => request(`/api/bookings/${id}/cancel`, json("POST")),
  rescheduleBooking: (id, payload) => request(`/api/bookings/${id}/reschedule`, json("POST", payload)),
  updateBookingNotes: (id, notes) => request(`/api/bookings/${id}/notes`, json("PATCH", { notes })),

  /** Streams the current booking view to a CSV download. */
  exportBookingsCsv: async (params = {}) => {
    const query = new URLSearchParams();
    query.set("scope", params.scope || "all");
    if (params.search) query.set("search", params.search);
    if (params.eventTypeId) query.set("event_type_id", params.eventTypeId);

    const response = await request(`/api/bookings/export.csv?${query.toString()}`, { raw: true });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `shopper-bookings-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },

  // ----------------------------------------------------------- blockouts --
  getBlockouts: () => request("/api/blockouts"),
  createBlockout: (payload) => request("/api/blockouts", json("POST", payload)),
  deleteBlockout: (id) => request(`/api/blockouts/${id}`, json("DELETE")),

  // ------------------------------------------------------ public booking --
  getPublicEventType: (slug) => request(`/api/public/event-types/${slug}`),
  getSlots: (slug, date) => request(`/api/public/event-types/${slug}/slots?date=${date}`),
  getAvailableDays: (slug, month) => request(`/api/public/event-types/${slug}/days?month=${month}`),
  createBooking: (slug, payload) => request(`/api/public/event-types/${slug}/book`, json("POST", payload)),
  getPublicBooking: (id) => request(`/api/public/bookings/${id}`),

  // ------------------------------------------- invitee self-service links --
  getManagedBooking: (token) => request(`/api/public/manage/${token}`),
  cancelManagedBooking: (token) => request(`/api/public/manage/${token}/cancel`, json("POST")),
  rescheduleManagedBooking: (token, payload) =>
    request(`/api/public/manage/${token}/reschedule`, json("POST", payload)),

  // ----------------------------------------------------------------- OTP --
  requestOtp: (email) => request("/api/public/otp/request", json("POST", { email })),
  verifyOtp: (email, code) => request("/api/public/otp/verify", json("POST", { email, code })),

  // -------------------------------------------------------- integrations --
  getIntegrations: () => request("/api/integrations"),
  saveIntegration: (key, config) => request(`/api/integrations/${key}`, json("POST", { config })),
  disconnectIntegration: (key) => request(`/api/integrations/${key}`, json("DELETE")),
  testIntegration: (key) => request(`/api/integrations/${key}/test`, json("POST")),

  // ------------------------------------------------------------ API keys --
  getApiKeys: () => request("/api/auth/api-keys"),
  generateApiKey: () => request("/api/auth/api-keys", json("POST")),
  revokeApiKey: () => request("/api/auth/api-keys", json("DELETE")),

  // ------------------------------------------------------- calendar feed --
  getCalendarFeed: () => request("/api/calendar/feed"),
  rotateCalendarFeed: () => request("/api/calendar/feed/rotate", json("POST")),

  // ------------------------------------------------------------ workflows --
  getWorkflows: () => request("/api/workflows"),
  createWorkflow: (payload) => request("/api/workflows", json("POST", payload)),
  updateWorkflow: (id, payload) => request(`/api/workflows/${id}`, json("PUT", payload)),
  toggleWorkflow: (id) => request(`/api/workflows/${id}/toggle`, json("PATCH")),
  deleteWorkflow: (id) => request(`/api/workflows/${id}`, json("DELETE")),
};

export { API_BASE };
