import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../components/AuthContext";
import { useToast } from "../components/Toast";

const API_BASE = import.meta.env.VITE_API_URL || "https://shopper-backend-mqcf.onrender.com";

export default function AuthCallbackPage() {
  const [params] = useSearchParams();
  const { login } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  useEffect(() => {
    const token = params.get("token");
    if (!token) {
      toast.error("Authentication failed — no token received.");
      navigate("/login", { replace: true });
      return;
    }

    fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error("Invalid token.");
        return r.json();
      })
      .then((user) => {
        login(token, user);
        toast.success(`Welcome, ${user.name || user.email}!`);
        navigate("/dashboard", { replace: true });
      })
      .catch(() => {
        toast.error("Authentication failed. Please try again.");
        navigate("/login", { replace: true });
      });
  }, []);

  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center",
      justifyContent: "center", minHeight: "100vh", gap: 16,
      color: "var(--text-muted)",
    }}>
      <span style={{ fontSize: 32 }}>⏳</span>
      <p style={{ fontWeight: 600 }}>Signing you in…</p>
    </div>
  );
}
