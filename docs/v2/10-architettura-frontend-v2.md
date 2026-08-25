# 10 — Architettura Frontend V2 (React + TypeScript, mobile-first PWA)

> Deriva da `06-modello-dati-v2.md` e `09-architettura-backend-v2.md` (contratto API). Decisione già presa dall'utente: applicazione separata, React + TypeScript preferito, mobile-first/PWA. Business logic resta nel backend — il frontend è visualizzazione/interazione/UX.

## Struttura — feature-based, non type-based

```
frontend/
  src/
    features/
      practices/       # una cartella per dominio, non una per "components/hooks/pages" trasversale
        api.ts          # chiamate React Query per questo dominio
        PracticeList.tsx
        PracticeDetail.tsx
        PracticeForm.tsx
        usePracticeDraft.ts
      pickups/
      deliveries/
      cremation-cycles/
      payments/
      calendar/
      invoices/
      auth/
    shared/
      ui/               # componenti generici riusabili (Button, Card, Dialog, ...) — headless + styled
      api/               # client HTTP base, tipi generati da OpenAPI, gestione errori comune
      hooks/
    app/
      router.tsx
      providers.tsx
    main.tsx
  public/
    manifest.json
    sw.ts               # service worker (vite-plugin-pwa)
```

**Perché feature-based**: V1 ha tutto in un unico file server-renderizzato — non c'è un precedente diretto da seguire, ma la scelta è guidata dallo stesso principio guida generale ("un cambiamento a un dominio non deve richiedere di toccare file condivisi da tutti gli altri domini"). Una modifica a "Ritiri" tocca solo `features/pickups/`, mai un file centrale da 18.000 righe.

## Stato — DECISION: TanStack Query per stato server, niente Redux

- **Stato server** (dati che vengono dal backend: pratiche, pagamenti, ecc.): **TanStack Query (React Query)**. Motivazione: elimina la gestione manuale di loading/error/cache/invalidazione/refetch che oggi in V1 è scritta a mano per ogni singola fetch (pattern ripetuto, fonte di bug di sincronizzazione — es. una card che non si aggiorna dopo un salvataggio altrove). Cache automatica con invalidazione mirata per entità (es. `invalidateQueries(['practice', id])` dopo un salvataggio) sostituisce reload manuali di intere pagine.
- **Stato UI/locale** (form in corso, tab attiva, modale aperta): `useState`/`useReducer`/Context React nativi. **Niente Redux/Zustand/altre librerie di stato globale** — per la scala di questa applicazione (uso interno, non centinaia di stati globali interconnessi) aggiungerebbero complessità senza un beneficio reale, stessa logica di "niente Celery" nel doc 09.
- **Stato di bozza persistente** (draft pratica, vedi sotto): gestito con un hook dedicato (`useDraft`) sopra IndexedDB, non dentro lo stato React globale — sopravvive a refresh/crash per costruzione, non per sincronizzazione manuale con localStorage come oggi.

## Contratto API — tipizzazione condivisa, non duplicata

**DECISION**: i tipi TypeScript delle richieste/risposte sono **generati automaticamente** dallo schema OpenAPI che FastAPI produce nativamente (`openapi-typescript` o equivalente), non scritti a mano. Motivazione diretta della regola "single source of truth": lo schema Pydantic del backend è l'unica fonte; se cambia un campo, il frontend vede un errore di tipo a compile-time invece di scoprirlo a runtime da un JSON inatteso — elimina una classe intera di bug di disallineamento FE/BE che in V1 (HTML generato server-side, nessun contratto tipizzato) non era nemmeno rilevabile finché non si rompeva visivamente.

## Form — react-hook-form + zod

- Validazione **immediata/UX** lato client con `zod` (schema derivabile, dove possibile, dagli stessi vincoli espressi lato backend — es. lunghezza massima, formato telefono) — sempre **duplicata** e mai sostitutiva della validazione server-side reale (coerente con la regola generale "mai fidarsi solo del frontend per la sicurezza/correttezza").
- `react-hook-form` per performance su form grandi (es. il form pratica, oggi in V1 uno dei form più complessi) — re-render minimi, gestione nativa di campi condizionali (es. "secondo animale" che appare solo se selezionato, ora un vero array di animali invece di colonne fisse `animal2_*`).

## Bozza persistente per nuova pratica (punto esplicito della richiesta originale, sezione 11)

**RISK noto in V1, esplicitamente da risolvere**: nessun sistema di bozza per la creazione di una nuova pratica — dati persi su refresh/crash/chiusura accidentale.

**DECISION V2**: un unico meccanismo di autosave per **tutti** i form critici (nuova pratica, modifica pratica), non due sistemi divergenti come oggi (V1 ha localStorage solo per gli eventi calendario, autosave server-side incrementale solo per la modifica pratica, e **nulla** per la creazione):

1. Ogni modifica al form (debounced, es. 500ms di inattività) viene scritta in **IndexedDB** locale (non `localStorage`: supporta dati strutturati più grandi senza limiti di stringa, ed è transazionale) sotto una chiave stabile (`draft:new-practice:<uuid-locale>`).
2. In parallelo, un **autosave server-side** (stesso principio già presente in V1 per la modifica, esteso ora anche alla creazione): il draft viene inviato periodicamente a un endpoint dedicato `POST /api/practices/drafts` che lo salva come riga in una tabella `practice_drafts` (bozza, non ancora una pratica reale — nessun impatto sul modello dati V2 "vero" finché non viene confermata).
3. Al riavvio dell'app (refresh, riapertura dopo crash, cambio di rete), se esiste un draft locale/server più recente dell'ultimo salvataggio confermato, l'utente vede un prompt esplicito "Riprendi bozza non salvata?" — mai un ripristino silenzioso che potrebbe confondersi con dati nuovi digitati per errore due volte.
4. Il draft viene **eliminato solo dopo un salvataggio reale confermato** (risposta 2xx dalla creazione pratica definitiva) — mai su un semplice "cambio pagina", coerente con la regola esplicita dell'utente ("il draft si cancella solo dopo un salvataggio reale confermato").
5. Funziona **offline**: IndexedDB scrive comunque anche senza connessione; il salvataggio server-side va in coda (`background sync` del service worker, vedi sotto) e riparte alla riconnessione.

## PWA — DECISION: Vite + vite-plugin-pwa

- Sostituisce la gestione manuale di manifest/service worker di V1 con lo strumento standard dell'ecosistema Vite, che genera un service worker con strategie di cache configurabili (es. `NetworkFirst` per le chiamate API, `CacheFirst` per asset statici) invece di codice scritto a mano.
- **Notifiche push**: stesso schema VAPID/Web Push già usato e funzionante in V1 — **riusato as-is**, non reinventato (il canale di invio è indipendente dal frontend che lo consuma, nessuna ragione per cambiarlo).
- **Background Sync API** per la coda di autosave offline del draft (punto 5 sopra) — quando il browser non supporta l'API (fallback), un retry al ripristino della connessione gestito lato applicazione (`navigator.onLine`/evento `online`) copre lo stesso caso in modo meno elegante ma funzionalmente equivalente.

## Stile visivo — mobile-first, Tailwind + Radix

- **Tailwind CSS**: utility-first, permette di costruire rapidamente un'interfaccia coerente mobile-first (classi responsive `sm:`/`md:`/`lg:`) senza il CSS scritto a mano riga per riga di V1 (1.513 righe in un unico blocco, fonte già nota di bug di specificità/duplicazione).
- **Radix UI** (primitive headless, non stilizzate) per i componenti interattivi che richiedono comportamento accessibile corretto e non banale (dropdown, dialog, popover, tooltip) — evita di reimplementare a mano gestione del focus/tastiera/touch come oggi in V1, dove è stato necessario correggere manualmente più volte bug touch/scroll specifici (es. lo swipe diagonale nel popup pagamento risolto in questa stessa sessione). Radix fornisce il comportamento corretto, Tailwind lo stile.
- Grafica: la richiesta esplicita dell'utente permette un rifacimento anche radicale rispetto a V1 — nessun vincolo di "deve sembrare uguale", solo il vincolo che nessun cambiamento visivo comprometta la funzionalità.

## Routing e performance

- **React Router**, con code-splitting per feature (`React.lazy` per ogni sezione — Pratiche, Calendario, Cremazioni, ecc.) così l'utente su mobile con connessione debole scarica solo il codice della sezione che sta effettivamente usando, non l'intero bundle applicativo a ogni visita (V1 oggi serve tutto HTML/CSS/JS inline ad ogni richiesta pagina — non paragonabile 1:1, ma il principio "non caricare più di quanto serve alla vista corrente" è lo stesso già richiesto esplicitamente dall'utente per le performance in generale, sezione 15 della richiesta originale).
- Liste lunghe (es. archivio pratiche): paginazione reale lato server (mai "carica tutto e filtra in memoria", pattern già segnalato come problema in V1 nell'audit doc 04) + virtualizzazione lato client per le viste con molte righe visibili contemporaneamente.
