import { useState } from "react";

import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import { useArticles, useOrderArticle, useRecentArticleOrders, type Article } from "./api";

export function ArticleListPage() {
  const { data: articles, isLoading, isError } = useArticles();
  const { data: recent } = useRecentArticleOrders();
  const order = useOrderArticle();
  const [orderTarget, setOrderTarget] = useState<Article | null>(null);

  return (
    <main className="wrap">
      {/* V1 (articles_page, app.py:13716-13717): il template interno usa
          "Articoli"/"articolo", ma un .replace() finale li riscrive sempre
          in "Prodotti"/"prodotto" (le virgolette tipografiche intorno a
          "Da ordinare" sopravvivono al replace) - qui riprodotto il testo
          REALMENTE renderizzato, non il template intermedio. h1+sub dentro
          un <div> - senza, .titlebar (flex space-between) spinge il
          sottotitolo tutto a destra invece che sotto il titolo. */}
      <div className="titlebar">
        <div>
          <h1>Prodotti</h1>
          <p className="sub">Seleziona un prodotto sotto la voce “Da ordinare”.</p>
        </div>
      </div>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento dei prodotti.</p>}
      {/* V1 (articles_page, app.py:13716): <section class="article-grid">
          e' sempre presente, anche vuota - nessun messaggio "Nessun
          prodotto disponibile" quando non ci sono articoli attivi. */}
      {articles && (
        <section className="article-grid">
          {articles.map((article) => (
            // V1: id="article-{id}" sull'<article> - e' l'ancora usata dal
            // link del promemoria di riordino (/prodotti#article-{id}).
            <article className="article-card" id={`article-${article.id}`} key={article.id}>
              <div>
                <span className="badge tag-outline-orange">Da ordinare</span>
                <h2>{article.name}</h2>
              </div>
              <p>Invia la richiesta di ordine al centro notifiche.</p>
              <button className="btn" disabled={order.isPending} onClick={() => setOrderTarget(article)}>
                Ordina prodotto
              </button>
            </article>
          ))}
        </section>
      )}

      <div className="section" style={{ marginTop: 20 }}>
        <h2>Ultime richieste</h2>
        {(!recent || recent.length === 0) && <p className="sub">Nessuna richiesta inviata.</p>}
        {recent && recent.length > 0 && (
          <div className="timeline">
            {recent.map((o) => (
              <div className="event" key={o.id}>
                <b>{o.article_name}</b>
                <br />
                {/* V1 (articles_page, app.py:13715): valore grezzo di
                    created_at con "T" sostituita da uno spazio, nessuna
                    localizzazione it-IT (stesso pattern di
                    PracticeTrashPage) - stesso identico testo. */}
                <small className="sub">
                  Richiesto da {o.ordered_by_name} · {(o.created_at || "").replace("T", " ")}
                </small>
              </div>
            ))}
          </div>
        )}
      </div>

      <ConfirmModal
        open={orderTarget != null}
        onClose={() => setOrderTarget(null)}
        onConfirm={() => {
          if (orderTarget) order.mutate(orderTarget.id);
          setOrderTarget(null);
        }}
        title="Ordina prodotto"
        description={orderTarget ? `Inviare la richiesta per ${orderTarget.name}?` : ""}
        confirmLabel="Invia richiesta"
        isPending={order.isPending}
      />
    </main>
  );
}
