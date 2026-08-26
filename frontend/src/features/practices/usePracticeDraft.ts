import { useCallback, useRef, useState } from "react";

const DEBOUNCE_MS = 500;

/**
 * doc10 'Bozza persistente per nuova pratica': in V1 i dati compilati per
 * una nuova pratica potevano sparire su refresh/crash/chiusura accidentale
 * - nessun sistema di bozza esisteva per la creazione (solo per la
 * modifica). Qui il draft vive in localStorage (non nello stato React
 * volatile del form), sopravvive a refresh/chiusura per costruzione.
 *
 * Scope dichiarato per questa fase: autosave locale (debounced) + prompt
 * di ripristino esplicito + cancellazione SOLO dopo un salvataggio reale
 * confermato (2xx) - il nucleo del requisito anti-perdita-dati di doc10.
 * L'autosave server-side (`POST /api/practices/drafts`) e la coda offline
 * via Background Sync (service worker) NON sono implementati in questa
 * fase: l'infrastruttura PWA (vite-plugin-pwa/service worker) non e' mai
 * stata configurata in questo vertical slice - richiederebbe una decisione
 * architetturale a se', fuori scope per il dominio Pratiche. Vedi report
 * di fine dominio.
 */
export function usePracticeDraft<T extends Record<string, unknown>>(key: string) {
  const storageKey = `ppm:draft:${key}`;
  // Inizializzatore lazy (sincrono, valutato una sola volta al primo
  // render) - non un useEffect: se il controllo fosse asincrono, il primo
  // giro di render/effect vedrebbe comunque hasStoredDraft=false e
  // l'autosave del form potrebbe schedulare una scrittura con i valori di
  // default (vuoti) prima ancora che il prompt di ripristino compaia,
  // sovrascrivendo la bozza reale - bug osservato e corretto in questa
  // stessa fase (vedi report di fine dominio).
  const [hasStoredDraft, setHasStoredDraft] = useState(() => {
    try {
      return localStorage.getItem(storageKey) !== null;
    } catch {
      return false;
    }
  });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const save = useCallback(
    (values: T) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        try {
          localStorage.setItem(storageKey, JSON.stringify(values));
        } catch {
          // storage pieno/non disponibile: l'autosave e' un miglioramento,
          // mai un requisito bloccante per compilare il form.
        }
      }, DEBOUNCE_MS);
    },
    [storageKey],
  );

  const readDraft = useCallback((): T | null => {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw ? (JSON.parse(raw) as T) : null;
    } catch {
      return null;
    }
  }, [storageKey]);

  const clearDraft = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    try {
      localStorage.removeItem(storageKey);
    } catch {
      // niente da fare se lo storage non e' disponibile
    }
    setHasStoredDraft(false);
  }, [storageKey]);

  return { hasStoredDraft, save, readDraft, clearDraft };
}
