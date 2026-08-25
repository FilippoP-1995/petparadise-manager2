# 11 — Piano di Test (V2)

> Deriva dall'architettura finale (doc 09 backend, doc 10 frontend, doc 07 migrazione). Copre: test di dominio, integrazione, API, frontend, end-to-end, migrazione, non-regressione verso V1.

## Backend — piramide dei test

1. **Unit — `domain/`** (la parte più preziosa da testare in isolamento): ogni macchina a stati (Pratica, Ritiro, Riconsegna, Ciclo di cremazione) testata su **tutte** le transizioni dichiarate — sia quelle permesse (side-effect corretti dichiarati) sia quelle vietate (deve sollevare `InvalidTransitionError`, mai fallire silenziosamente). Nessun DB, nessun mock: sono funzioni pure su dati in memoria, veloci da eseguire ad ogni commit.
2. **Integrazione — `repositories/` e `services/`**: contro un **Postgres reale** (container Docker dedicato ai test, stesso motore/dialetto della produzione — mai SQLite come sostituto, per non nascondere differenze di comportamento SQL specifiche di Postgres, es. vincoli CHECK/ENUM/transazioni). Ogni servizio (`create_practice`, `register_payment`, `transition_pickup_status`, ...) testato end-to-end contro il DB: transazionalità (un fallimento a metà non lascia scritture parziali), audit_log popolato correttamente, vincoli di unicità (es. `invoice_number` duplicato deve fallire, mai sovrascrivere silenziosamente).
3. **API — `TestClient`/`httpx.AsyncClient`**: ogni endpoint testato per contratto (status code, forma della risposta secondo lo schema Pydantic), autorizzazione (un ruolo senza permesso riceve 403, mai un comportamento silenzioso diverso), casi di errore (input invalido → 422 con dettaglio, non un 500 generico).
4. **Contratto FE/BE**: verifica automatica in CI che lo schema OpenAPI generato dal backend e i tipi TypeScript generati per il frontend siano sincronizzati (rigenerazione dei tipi + `git diff` vuoto) — se qualcuno cambia un modello Pydantic senza rigenerare i tipi frontend, la build fallisce esplicitamente invece di scoprire il disallineamento a runtime.

## Frontend

1. **Unit/componente — Vitest + React Testing Library**: componenti puri (form, card, liste) testati per rendering condizionale e interazione (click, submit) — mockando solo la rete (React Query in modalità test), mai la logica di dominio (che non vive nel frontend, quindi non c'è nulla da mockare lì).
2. **Draft persistente**: test dedicati per il meccanismo di autosave (doc 10) — simulano refresh/crash (reload del componente con IndexedDB già popolato) e verificano il prompt di ripristino; verificano che il draft venga eliminato **solo** dopo una risposta 2xx dal salvataggio reale, mai prima.
3. **E2E — Playwright**, sui flussi critici, in **viewport mobile** (dato il requisito mobile-first) oltre che desktop:
   - login → creazione pratica completa (con interruzione/ripresa di bozza) → salvataggio;
   - registrazione ritiro → cambio stato → verifica notifica generata (contenuto, non solo invio);
   - registrazione pagamento (entrambi i circuiti) → verifica che il totale mostrato coincida sempre con la somma del ledger;
   - ciclo di cremazione completo fino a riconsegna;
   - offline: creazione pratica senza connessione → riconnessione → verifica che il draft si sia sincronizzato.

## Verifica di migrazione (automatizzata, non manuale)

Riusa direttamente gli script di verifica già previsti in doc 07 (conteggi, catena relazionale Cliente→Pratica→Animale→Ritiro→Cremazione→Riconsegna→Fattura→Pagamento, invarianti strutturali), eseguiti come **suite automatica ripetibile** ogni volta che la migrazione viene rilanciata su una copia fresca di staging durante lo sviluppo — non un controllo manuale una tantum. Il report di discrepanze (doc 07) diventa l'output di questa suite, con soglia "zero discrepanze" come criterio di uscita dalla fase di sviluppo della migrazione.

## Test di non-regressione funzionale verso V1

**DECISION**: prima del taglio finale (doc 07/12), un periodo di **shadow mode** — V2 riceve una copia periodicamente aggiornata dei dati reali (via la stessa pipeline di migrazione, rieseguita su staging) e un set di scenari applicativi reali viene eseguito su entrambi i sistemi in parallelo, confrontando gli output (es. "il totale incassato mostrato per questa pratica è identico in V1 e V2?"). Non richiede traffico live duplicato (nessun dual-write, coerente con la decisione di doc 07) — è un confronto batch tra due sistemi entrambi alimentati dagli stessi dati storici.

- **Perché è possibile senza costi ingegneristici sproporzionati**: a differenza di un vero shadow-traffic in tempo reale (che richiederebbe instradare le richieste reali degli operatori a entrambi i sistemi, complessità non giustificata qui), questo è un confronto **offline** tra due stati derivati dagli stessi dati — sfrutta la stessa infrastruttura di migrazione già costruita, nessun nuovo componente.

## Sicurezza (anticipazione)

- Test automatico che **ogni** endpoint autenticato rifiuta una richiesta senza sessione valida (scansione automatica delle route registrate, non un elenco scritto a mano che può disallinearsi).
- Test che i permessi per ruolo siano applicati coerentemente (matrice ruolo×endpoint testata a tabella, non caso per caso).
- Test di non-esposizione: nessun endpoint di lettura pratiche/pagamenti raggiungibile senza autenticazione (regressione esplicita da evitare, dato che l'audit V1 ha rilevato controlli di permesso scritti a mano e sparsi).

## Performance (anticipazione)

- Test di carico mirato sulle query di lista (archivio pratiche, calendario) con un volume di dati realistico (non il DB vuoto di sviluppo) — verifica che la paginazione reale (doc 10) mantenga tempi di risposta accettabili anche a migliaia di righe, a differenza del pattern "carica tutto" già segnalato come problema in V1.

## Criteri di uscita (prima di considerare V2 pronta per il piano di rilascio)

Tutti i seguenti devono essere veri contemporaneamente, non a scelta:
1. Suite unit+integrazione+API backend verde al 100%, nessun test skippato senza motivazione esplicita documentata.
2. Suite frontend (unit+E2E) verde sui flussi critici elencati sopra, su viewport mobile e desktop.
3. Verifica di migrazione automatica a zero discrepanze sull'ultima esecuzione contro un backup recente di produzione.
4. Shadow mode eseguito almeno una volta su dati reali recenti, con discrepanze indagate e chiuse (non ignorate).
5. Nessuna vulnerabilità nota aperta dai test di sicurezza sopra.
