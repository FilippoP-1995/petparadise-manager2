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

  return (
    <main className="login-page">
      <form className="card" onSubmit={handleSubmit}>
        <h1>Pet Paradise Manager V2</h1>
        <label>
          Utente
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="error-banner">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Accesso..." : "Accedi"}
        </button>
      </form>
    </main>
  );
}
