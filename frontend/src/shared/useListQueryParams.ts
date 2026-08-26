import { useSearchParams } from "react-router-dom";

/**
 * Pattern condiviso per lo stato di una lista (ricerca, paginazione).
 *
 * Perche' esiste: in V1 ogni pagina-elenco doveva reinventare da zero come
 * mantenere filtri/posizione/selezione tornando indietro dal dettaglio (vedi
 * la lunga serie di correzioni sulla pagina Fatture in questa stessa
 * sessione) - un problema risolto pagina per pagina invece che una volta
 * sola. Qui lo stato della lista vive nell'URL (query string), non in
 * sessionStorage: la cronologia del browser lo preserva nativamente, e
 * <ScrollRestoration> di react-router (app/router.tsx) ripristina la
 * posizione di scroll tornando indietro - stesso comportamento, gratis, per
 * ogni pagina-elenco che usa questo hook, non una soluzione locale duplicata.
 */
export function useListQueryParams() {
  const [searchParams, setSearchParams] = useSearchParams();

  const q = searchParams.get("q") ?? "";
  const offset = Number(searchParams.get("offset") ?? "0");

  function setSearch(value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set("q", value);
      else next.delete("q");
      next.delete("offset"); // una nuova ricerca riparte sempre dalla prima pagina
      return next;
    });
  }

  function setOffset(value: number) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("offset", String(value));
      return next;
    });
  }

  return { q, offset, setSearch, setOffset };
}
