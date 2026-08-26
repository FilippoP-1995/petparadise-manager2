import { useArticles, useOrderArticle, useRecentArticleOrders } from "./api";

export function ArticleListPage() {
  const { data: articles, isLoading, isError } = useArticles();
  const { data: recent } = useRecentArticleOrders();
  const order = useOrderArticle();

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Prodotti</h1>
        <p className="sub">Seleziona un prodotto sotto la voce "Da ordinare".</p>
      </div>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento dei prodotti.</p>}
      {articles && articles.length === 0 && <p className="empty-state">Nessun prodotto disponibile.</p>}

      {articles && articles.length > 0 && (
        <section className="grid">
          {articles.map((article) => (
            <article className="section" key={article.id}>
              <span className="badge tag-outline-orange">Da ordinare</span>
              <h2>{article.name}</h2>
              <p className="sub">Invia la richiesta di ordine al centro notifiche.</p>
              <button
                className="btn"
                disabled={order.isPending}
                onClick={() => {
                  if (confirm(`Inviare la richiesta per ${article.name}?`)) order.mutate(article.id);
                }}
              >
                Ordina prodotto
              </button>
            </article>
          ))}
        </section>
      )}

      <div className="card" style={{ marginTop: 20 }}>
        <h2>Ultime richieste</h2>
        {(!recent || recent.length === 0) && <p className="sub">Nessuna richiesta inviata.</p>}
        {recent && recent.length > 0 && (
          <div className="timeline">
            {recent.map((o) => (
              <div className="event" key={o.id}>
                <b>{o.article_name}</b>
                <br />
                <small className="sub">
                  Richiesto da {o.ordered_by_name} · {new Date(o.created_at).toLocaleString("it-IT")}
                </small>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
