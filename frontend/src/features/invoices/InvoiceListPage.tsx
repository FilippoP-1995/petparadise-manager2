import { Link } from "react-router-dom";

import { formatMoney } from "@/shared/money";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { useInvoices } from "./api";

export function InvoiceListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const { data: invoices, isLoading, isError } = useInvoices({ q, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Fatture</h1>
          <p className="sub">
            Ogni fattura identifica e apre la pratica collegata. Una pratica con acconto e saldo fatturati
            separatamente compare come due righe distinte.
          </p>
        </div>
        {/* V1 (invoices_page, app.py:13836) non ha un pulsante di creazione
            manuale qui - le fatture nascono dall'input inline "N. fattura"
            nell'Archivio o dal popover Pagamento. V2 ha un'entita' Fattura
            di prima classe (InvoiceRead con id proprio, riconciliazione
            propria, correzioni tracciate) invece di un semplice campo
            testo sulla pratica - questo pulsante e' il solo punto di
            ingresso per crearla, differenza reale dovuta al motore V2
            (mai rimuovere: servirebbe a rendere impossibile fatturare le
            pratiche a cui V1 non arriva con l'inline, es. senza pratica). */}
        <Link className="btn" to="/fatture/nuova">
          + Nuova fattura
        </Link>
      </div>

      {/* V1 (invoices_page, app.py:13836) ha un form di ricerca con filtri
          Tipo (Entrambe/Fatturate/Da fatturare), Dal, Al - e una seconda
          sezione "Da fatturare" (pratiche senza numero fattura). GET
          /api/invoices (backend/api/routes/invoices.py) espone solo q/
          practice_id/limit/offset: nessun filtro data, nessun filtro tipo,
          nessun endpoint per elencare le pratiche non ancora fatturate.
          Non essendoci un endpoint reale da cui pescare quei dati, la
          sezione "Da fatturare" e i filtri Tipo/Dal/Al non vengono
          costruiti qui (sarebbe UI finta) - gap disclosurato invece che
          fabbricato, come da mandato. La ricerca sotto usa lo stesso campo
          q (numero fattura o pratica) davvero supportato dal backend. */}
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
        <section className="tablebox fatture-tablebox">
          <h2>Fatture emesse</h2>
          <table>
            <thead>
              <tr>
                <th>Fattura</th>
                <th>Data</th>
                <th>Pratica</th>
                <th>Cliente</th>
                <th>Animale</th>
                <th>Circuito</th>
                <th>Totale</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((invoice) => (
                <tr key={invoice.id}>
                  <td>
                    <Link to={`/fatture/${invoice.id}`}>{invoice.invoice_number}</Link>
                  </td>
                  <td>{invoice.invoice_date ? new Date(`${invoice.invoice_date}T00:00:00`).toLocaleDateString("it-IT") : "-"}</td>
                  <td>
                    {invoice.practice_id ? (
                      <Link to={`/pratiche/${invoice.practice_id}`}>{invoice.practice_number_snapshot}</Link>
                    ) : (
                      invoice.practice_number_snapshot || "-"
                    )}
                  </td>
                  <td>{invoice.owner_name ?? "-"}</td>
                  <td>{invoice.animal_names ?? "-"}</td>
                  <td>{invoice.channel}</td>
                  <td>{formatMoney(invoice.total_amount_cents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
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
