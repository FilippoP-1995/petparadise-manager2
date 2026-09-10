# 16 — Modifiche V1 da riportare in V2

> Tracking delle modifiche fatte direttamente su V1 (`app.py`) durante la fase di evoluzione V1 (V2 in pausa, dal 2026-09-10). Per ciascuna: cosa è cambiato, perché, e lo stato verso V2. Aggiornato ad ogni modifica V1 approvata e pushata.

| # | Data | Modifica V1 | Commit | Stato verso V2 |
|---|---|---|---|---|
| 1 | 2026-09-10 | Ripristino sistemico dello stato di navigazione ("indietro" ripristina scroll/ricerca/filtri/giorno-settimana/elemento espanso invece di ricaricare dai default) | `cf2f5ba` (main) | **Da valutare, non da portare 1:1** |
| 2 | 2026-09-10 | Fix Cremazioni: la gesture nativa di back su iPhone non ripristinava lo stato (giorno swipeato / ciclo espanso), perché mancava la sincronizzazione con l'URL reale del browser (a differenza di Calendario, che già la faceva) | `1e54bca` (main) | **Da valutare, non da portare 1:1** |
| 3 | 2026-09-10 | Fix Cremazioni: lo swipe indietro oltre il confine settimana non riportava al giorno esatto di partenza (es. domenica) ma sempre al giorno corrente, per doppia causa server-side (giorno iniziale evidenziato che ignorava il parametro richiesto + sentinella che puntava al lunedì della settimana invece che al giorno immediatamente precedente) | `7beb739` (main) | **Da valutare, non da portare 1:1** |

## Dettaglio #1 — Ripristino stato di navigazione

**Cosa è cambiato in V1** (vedi commit `cf2f5ba` per il diff completo):
- Consolidato in un unico sistema condiviso (`PPM_LIST_PAGES`/`setupListStateRestore`) il ripristino di scroll/ricerca/riga evidenziata, prima duplicato in 3 varianti quasi identiche (generica + Fatture + Archivio), esteso a Cremazioni, Calendario, Collaboratori, Smaltimenti storico.
- Cremazioni: l'espansione di un ciclo ora sopravvive al ritorno da una pratica collegata (riuso di `cremationOpenPendingCycle`, già esistente per un altro flusso).
- Calendario: aprire il dettaglio evento / il wizard "Nuovo evento" ora passa `return_to` calcolato dal vivo, così tornando indietro si resta sul giorno/settimana da cui si era partiti invece di ricadere su "oggi".
- Smaltimenti: i link storico preservano i filtri periodo (`dal`/`al`/`stato`).

**Perché**: bug reale segnalato dall'utente — "indietro" doveva ripristinare lo stato esatto, non solo ricaricare la pagina precedente dai suoi default. Vedi analisi completa nella conversazione (causa radice: `Cache-Control: no-store` su ogni pagina disabilita la bfcache nativa del browser, quindi tutto lo stato va ricostruito a mano via `sessionStorage`/query string).

**Stato verso V2**: **non è una porzione di UI/grafica da copiare 1:1** (V2 è una SPA React con `react-router-dom`, `ScrollRestoration`, React Query — un'architettura di navigazione completamente diversa da V1, che è multi-page-reload puro). Quando si riprenderà lo sviluppo V2, va garantito lo **stesso comportamento finale** ("indietro" ripristina stato esatto: scroll, filtri, giorno calendario, elemento espanso), verificando cosa V2 offre già gratis tramite `ScrollRestoration`/history del browser nativa (probabilmente già buona parte, essendo una SPA) e colmando i gap specifici (es. stato di espansione di un accordion, se non già gestito da state locale React che sopravvive al mount/unmount del router). Non richiede di reimplementare `PPM_LIST_PAGES` in V2: richiede solo di verificare/testare gli stessi scenari (lista→dettaglio→indietro, Cremazioni con ciclo espanso, Calendario con giorno diverso da oggi) e chiudere eventuali gap con gli strumenti nativi di React Router.

## Dettaglio #2 — Fix gesture di back iPhone su Cremazioni

**Cosa è cambiato in V1** (vedi commit `1e54bca` per il diff completo): il fix #1 sopra copriva solo il ritorno tramite link espliciti con `return_to` (es. bottone/link "torna alla pagina precedente"), ma NON la gesture nativa di back di iOS Safari (swipe dal bordo, o pulsante di sistema), perché quella si basa sulla vera cronologia del browser e non vede parametri codificati nei link in uscita — vede solo l'URL reale dell'entry di history. Cremazioni non aveva alcuna sincronizzazione dell'URL reale per due stati: giorno selezionato nello swipe settimanale e ciclo espanso. Calendario invece già lo faceva (`calendarSyncUrlToDay`), motivo per cui da Calendario la gesture funzionava già correttamente mentre da Cremazioni no. Aggiunte `cremationSyncUrlToDay(idx)` (chiamata da `cremationSelectDay` e dall'IntersectionObserver dei day pages) e `cremationSyncUrlToExpanded()` (chiamata da `cremationToggleCycleCard`), entrambe basate su `history.replaceState`, stesso pattern di Calendario. Corretto anche un bug reale: l'attributo delle daybar card di Cremazioni è `data-cremation-day`, non `data-date` (copiato per errore dalla convenzione di Calendario).

**Perché**: bug segnalato dall'utente con riproduzione precisa — il link esplicito funzionava, la gesture iPhone no; da Calendario invece funzionava anche con la gesture. Causa radice: mancava la sincronizzazione URL↔stato per la gesture nativa, non per i link.

**Stato verso V2**: stesso discorso del Dettaglio #1 — non da portare 1:1 (V1 multi-page-reload vs V2 SPA React Router). Quando si riprenderà V2, verificare che React Router + `ScrollRestoration` gestiscano già nativamente il back-gesture per lo stato equivalente (giorno selezionato, ciclo espanso in Cremazioni), dato che in una SPA la cronologia del browser è gestita direttamente dal router e non richiede sync manuale via `replaceState` come in V1.

## Dettaglio #3 — Fix ripristino giorno esatto dopo swipe oltre il confine settimana (Cremazioni)

**Cosa è cambiato in V1** (vedi commit `7beb739` per il diff completo): nella vista settimanale di Cremazioni (`cremation_schedule_week`), il giorno inizialmente evidenziato al caricamento (`board_date`) ignorava sempre il parametro `data` della richiesta e sceglieva "oggi" ogni volta che oggi cadeva nella settimana mostrata — indipendentemente da quale giorno esatto fosse stato richiesto. In più, la sentinella del carosello raggiunta swipando indietro oltre lunedì puntava a `data=lunedì della settimana precedente` invece che al giorno immediatamente precedente (la domenica), perdendo così quale giorno esatto restaurare. Corretto seguendo esattamente lo stesso pattern già usato da Calendario per la stessa identica sentinella (`start-1 giorno`, non `start-7 giorni`).

**Perché**: bug segnalato dall'utente — da domenica, swipe avanti oltre il confine settimana (arrivo a lunedì della settimana successiva) poi swipe indietro doveva riportare esattamente alla domenica di partenza, ma riportava sempre al giorno corrente.

**Stato verso V2**: stesso discorso dei Dettagli #1/#2 — non da portare 1:1. Quando si riprenderà V2, verificare che l'equivalente React (giorno selezionato in una vista settimanale Cremazioni, se implementata con un carosello analogo) gestisca correttamente il caso "swipe avanti oltre il confine settimana poi indietro" tramite gli strumenti nativi di React Router/state locale, senza il bisogno di sentinelle server-side come in V1.
