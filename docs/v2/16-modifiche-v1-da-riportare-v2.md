# 16 — Modifiche V1 da riportare in V2

> Tracking delle modifiche fatte direttamente su V1 (`app.py`) durante la fase di evoluzione V1 (V2 in pausa, dal 2026-09-10). Per ciascuna: cosa è cambiato, perché, e lo stato verso V2. Aggiornato ad ogni modifica V1 approvata e pushata.

| # | Data | Modifica V1 | Commit | Stato verso V2 |
|---|---|---|---|---|
| 1 | 2026-09-10 | Ripristino sistemico dello stato di navigazione ("indietro" ripristina scroll/ricerca/filtri/giorno-settimana/elemento espanso invece di ricaricare dai default) | `cf2f5ba` (main) | **Da valutare, non da portare 1:1** |
| 2 | 2026-09-10 | Fix Cremazioni: la gesture nativa di back su iPhone non ripristinava lo stato (giorno swipeato / ciclo espanso), perché mancava la sincronizzazione con l'URL reale del browser (a differenza di Calendario, che già la faceva) | `1e54bca` (main) | **Da valutare, non da portare 1:1** |
| 3 | 2026-09-10 | Fix Cremazioni: lo swipe indietro oltre il confine settimana non riportava al giorno esatto di partenza (es. domenica) ma sempre al giorno corrente, per doppia causa server-side (giorno iniziale evidenziato che ignorava il parametro richiesto + sentinella che puntava al lunedì della settimana invece che al giorno immediatamente precedente) | `7beb739` (main) | **Da valutare, non da portare 1:1** |
| 4 | 2026-09-11 | Ricavi per voce del preventivo in Bilanci, etichette operative PELO/NO PELO, fatture condivise tra più pratiche, note pratica visibili in Cremazioni senza espandere il ciclo | `341cfda` (main) | **Da portare in V2 (funzionalità reali, non solo comportamento di navigazione)** |

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

## Dettaglio #4 — Ricavi per voce, etichette PELO/NO PELO, fatture condivise, note in Cremazioni

**Cosa è cambiato in V1** (vedi commit `341cfda` per il diff completo), quattro modifiche correlate implementate insieme:

- **Ricavi per voce del preventivo**: nuova sezione dentro Bilanci (`revenue_by_quote_category()`) che aggrega le 9 vere voci economiche del preventivo (Cremazione, Ritiro, Riconsegna, Serale, Notturno, Festivo, Urne, Calchi, Accessori — i 6 campi prezzo piatti su `practices` + le 3 categorie reali di `practice_items`) per il periodo già selezionato dai filtri Bilanci esistenti. Base preventivo/fatturazione, non cassa: `balance_movements` non sa a quale voce del preventivo appartiene un incasso.
- **Etichette operative PELO/NO PELO**: aggiunte con lo stesso meccanismo duplicato in 6 punti già usato dalle altre 14 etichette (colonna DB, `tag_badges`, `tag_controls`, form, `normalized_fields`).
- **Fatture condivise tra più pratiche**: il numero fattura non è mai stato un vincolo UNIQUE a DB — solo un controllo applicativo (`invoice_conflict()`, richiamato da 9 punti di scrittura) lo impediva. Reso permissivo in un unico punto sorgente; il numero fattura non è più trattato come identificatore univoco della pratica (resta `practice_number`). Nuova sezione "Fatture condivise" in `/fatture` che raggruppa per numero e mostra il totale combinato = somma dei singoli importi (mai duplicato).
- **Note pratica in Cremazioni**: la nota della pratica (colonna `notes`, già selezionata da `SELECT *`) è ora visibile in una preview troncata (80 caratteri) direttamente nella card compatta del ciclo, in entrambe le viste (giorno/settimana), senza doverlo espandere. Nessuna nuova colonna, nessuna duplicazione dati.

**Perché**: quattro richieste esplicite dell'utente, analizzate insieme per le dipendenze reali (la condivisione fatture non tocca Bilanci; i ricavi per voce restano attribuiti alla pratica corretta indipendentemente da quante pratiche condividono una fattura).

**Stato verso V2**: a differenza dei Dettagli #1-#3 (solo comportamento di navigazione), queste sono **funzionalità economiche/dati reali** che andranno effettivamente reimplementate in V2, non solo verificate con gli strumenti nativi della SPA:
- Ricavi per voce: stesso calcolo (6 campi prezzo + 3 categorie item) da riprodurre lato backend V2, integrato nella vista Bilanci/report equivalente di V2.
- PELO/NO PELO: aggiungere le due nuove etichette allo schema/enum tag di V2 (verificare come V2 modella le etichette operative — probabilmente diverso dal pattern a colonne piatte di V1).
- Fatture condivise: verificare/rimuovere l'equivalente vincolo di unicità in V2 (se presente) e implementare la vista raggruppata.
- Note in Cremazioni: aggiungere la preview nota al componente card compatta del ciclo in V2 (probabilmente più semplice in React, dato che i dati della pratica sono già in memoria/state, nessun problema di query aggiuntiva).
