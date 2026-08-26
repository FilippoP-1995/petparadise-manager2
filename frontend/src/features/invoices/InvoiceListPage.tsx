import { Link } from "react-router-dom";

import { useListQueryParams } from "@/shared/useListQueryParams";

import { useInvoices } from "./api";

function money(cents: number) {
  return (cents / 100).toLocaleString("it-IT", { style: "currency", currency: "EUR" });
}

export function InvoiceListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const { data: invoices, isLoading, isError } = useInvoices({ q, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Fatture</h1>
        <Link className="btn" to="/fatture/nuova">
          + Nuova fattura
        </Link>
      </div>
      <p className="sub">Ogni fattura identifica e apre la pratica collegata.</p>

      <input
        className="search-input"
        placeholder="Cerca per numero fattura o numero pratica..."
        defaultValue={q}
        onChange={(e) => setSearch(e.target.value)}
      />

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento delle fatture.</p>}
      {invoices && invoices.length === 0 && <p className="empty-state">Nessuna fattura trovata.</p>}

      {invoices && invoices.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Numero</th>
              <th>Data</th>
              <th>Pratica</th>
              <th>Canale</th>
              <th>Importo</th>
            </tr>
          </thead>
          <tbody>
            {invoices.map((invoice) => (
              <tr key={invoice.id}>
                <td>
                  <Link to={`/fatture/${invoice.id}`}>{invoice.invoice_number}</Link>
                </td>
                <td>{invoice.invoice_date ?? "-"}</td>
                <td>
                  {invoice.practice_id ? (
                    <Link to={`/pratiche/${invoice.practice_id}`}>{invoice.practice_number_snapshot}</Link>
                  ) : (
                    invoice.practice_number_snapshot || "-"
                  )}
                </td>
                <td>{invoice.channel}</td>
                <td>{money(invoice.total_amount_cents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!invoices || invoices.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
