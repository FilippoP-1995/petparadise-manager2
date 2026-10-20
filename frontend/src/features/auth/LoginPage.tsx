import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "./useAuth";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      const from = (location.state as { from?: Location })?.from?.pathname ?? "/clienti";
      navigate(from, { replace: true });
    } catch {
      setError("Credenziali non valide.");
    } finally {
      setSubmitting(false);
    }
  }

  // V1 (login_page, app.py:8490): <main class="login"><h1>Pet Paradise
  // Manager</h1><p class="sub">...</p>{flash warning}<form>{field Utente}
  // {field Password}<button class="btn" style="width:100%">.
  return (
    <main className="login-page">
      <form className="login" onSubmit={handleSubmit}>
        <h1>Pet Paradise Manager</h1>
        <p className="sub">Accedi alla gestione operativa.</p>
        {error && <div className="flash warning">{error}</div>}
        <div className="field">
          <label htmlFor="login-username">Utente</label>
          <input id="login-username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
        </div>
        <div className="field" style={{ marginTop: 12 }}>
          <label htmlFor="login-password">Password</label>
          <input id="login-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        <button className="btn" style={{ width: "100%", marginTop: 20 }} type="submit" disabled={submitting}>
          {submitting ? "Accesso..." : "Accedi"}
        </button>
      </form>
    </main>
  );
}
