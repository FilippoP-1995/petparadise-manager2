import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { usePractices } from "@/features/practices/api";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import { useClient, useDeactivateClient, useUpdateClient, type ClientCreateInput } from "./api";

const STATUS_LABELS: Record<string, string> = {
  ritirato: "Ritirato",
  in_programma: "In programma",
  cremato: "Cremato",
  da_consegnare: "Da consegnare",
  consegnato: "Consegnato",
  smaltito: "Smaltito",
};

// V1 (client_detail, app.py:15410-15418): titlebar nome+"Torna alla
// lista", .section "Anagrafica" (form di modifica, stessi 13 campi del
// form "Aggiungi cliente"), .section "Pratiche collegate". V1 elimina con
// un DELETE reale da qui - V2 usa sempre "Disattiva" (soft, coerente con
// il resto del dominio), non un'eliminazione definitiva.
export function ClientDetailPage() {
  const { clientId } = useParams();
  const navigate = useNavigate();
  const id = Number(clientId);
  const { data: client, isLoading, isError } = useClient(id);
  const { data: practices } = usePractices({ clientId: id });
  const update = useUpdateClient();
  const deactivate = useDeactivateClient();
  const [form, setForm] = useState<ClientCreateInput | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [deactivateOpen, setDeactivateOpen] = useState(false);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !client) return <p className="error-banner">Cliente non trovato.</p>;

  const values = form ?? client;

  function field(key: keyof ClientCreateInput) {
    return {
      value: values[key] ?? "",
      onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...values, [key]: e.target.value }),
    };
  }

  async function handleSave() {
    setServerError(null);
    try {
      await update.mutateAsync({ clientId: id, input: values });
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  async function handleDeactivate() {
    await deactivate.mutateAsync(id);
    navigate("/clienti");
  }

  const name = [client.first_name, client.last_name].filter(Boolean).join(" ") || client.company_name || "Cliente senza nome";

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>{name}</h1>
          <div className="sub">Anagrafica cliente</div>
        </div>
        <button className="btn-ghost" onClick={() => navigate("/clienti")}>
          Torna alla lista
        </button>
      </div>

      <div className="section">
        <h2>Anagrafica</h2>
        <div className="fields">
          <div className="field">
            <label>
              Nome
              <input {...field("first_name")} />
            </label>
          </div>
          <div className="field">
            <label>
              Cognome
              <input {...field("last_name")} />
            </label>
          </div>
          <div className="field full">
            <label>
              Ragione sociale
              <input {...field("company_name")} />
            </label>
          </div>
          <div className="field">
            <label>
              Telefono
              <input {...field("phone")} />
            </label>
          </div>
          <div className="field">
            <label>
              Secondo telefono
              <input {...field("phone_2")} />
            </label>
          </div>
          <div className="field full">
            <label>
              Email
              <input {...field("email")} />
            </label>
          </div>
          <div className="field">
            <label>
              Codice fiscale
              <input {...field("tax_code")} />
            </label>
          </div>
          <div className="field">
            <label>
              Partita IVA
              <input {...field("vat_number")} />
            </label>
          </div>
          <div className="field full">
            <label>
              Indirizzo
              <input {...field("street")} />
            </label>
          </div>
          <div className="field">
            <label>
              Comune
              <input {...field("city")} />
            </label>
          </div>
          <div className="field">
            <label>
              Provincia
              <input maxLength={2} {...field("province")} />
            </label>
          </div>
          <div className="field">
            <label>
              CAP
              <input {...field("zip")} />
            </label>
          </div>
          <div className="field full">
            <label>
              Note
              <input {...field("notes")} />
            </label>
          </div>
        </div>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button className="btn" disabled={update.isPending} onClick={handleSave}>
            {update.isPending ? "Salvataggio..." : "Salva anagrafica"}
          </button>
          <button className="btn-ghost" onClick={() => setDeactivateOpen(true)}>
            Disattiva cliente
          </button>
        </div>
      </div>

      <div style={{ height: 14 }} />

      <section className="section">
        <h2>Pratiche collegate</h2>
        {/* V1 (client_detail, app.py:15410-15417): stessa tabella,
            ultima colonna "Recupero" con date_it(pickup_date). Il campo
            pickup_date esiste nel modello V2 (backend/models/practice.py)
            ma non e' esposto da nessuno schema/endpoint verso il
            frontend (solo uso interno in dashboard_service) - gap di
            esposizione API disclosed, non un dominio mancante. Qui si usa
            "Creazione"/created_at, l'unica data reale gia' disponibile,
            invece di inventare pickup_date lato client. */}
        <div className="tablebox">
          <table className="premium-table">
            <thead>
              <tr>
                <th>Pratica</th>
                <th>Animale</th>
                <th>Stato</th>
                <th>Creazione</th>
              </tr>
            </thead>
            <tbody>
              {practices && practices.length > 0 ? (
                practices.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <a href={`/pratiche/${p.id}`} onClick={(e) => { e.preventDefault(); navigate(`/pratiche/${p.id}`); }}>
                        {p.practice_number}
                      </a>
                    </td>
                    <td>{p.animals.map((a) => a.name ?? "-").join(", ") || "-"}</td>
                    <td>{STATUS_LABELS[p.status] ?? p.status}</td>
                    <td>{new Date(p.created_at).toLocaleDateString("it-IT")}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="sub">
                    Nessuna pratica collegata.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <ConfirmModal
        open={deactivateOpen}
        onClose={() => setDeactivateOpen(false)}
        onConfirm={handleDeactivate}
        title="Disattiva cliente"
        description={`Disattivare ${name}?`}
        confirmLabel="Disattiva"
        danger
        isPending={deactivate.isPending}
      />
    </main>
  );
}
