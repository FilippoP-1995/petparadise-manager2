# 04 — Problemi del Sistema Attuale

> Ogni punto è supportato da prove concrete (file:riga), non impressioni. Dove utile, è indicata la direzione di risoluzione strutturale per la V2 (non un piano dettagliato).

## 1. Duplicazione di logica identica

`cremation_schedule` (11960-12419) e `cremation_schedule_week` (12420-12991) contengono **12 helper duplicati byte-per-byte** (`urn_value`, `urn_html`, `accessory_value`, `accessory_html`, `tags_html`, `animal_name_html`, `practice_url`, `owner_label`, `add_animal_button_html`, `animal_row_html`, `time_field_html`, `add_animal_card_html`, ~120 righe totali). Un fix applicato solo a una delle due copie è già successo in passato (il commento sulle note pratica in card ciclo esiste identico in entrambi i punti, segno che chi l'ha aggiunto ha dovuto ricordarsi di duplicarlo).

Una terza reimplementazione divergente di `practice_url` esiste in `whatsapp_conversations` con firma diversa. Il pattern URL `/pratiche/{id}?return_to=...` ricorre **16 volte** in forme leggermente diverse, mai centralizzato.

`tag_badges` e `tag_controls` duplicano la stessa lista di 14 tuple (colonna/etichetta/classe CSS) per i tag booleani — un tag aggiunto a una lista e dimenticato nell'altra è un bug strutturale latente.

**Direzione V2**: helper di rendering come funzioni di modulo condivise (non closure locali agli handler); costanti di dominio (tag, colori, mapping) definite una sola volta.

## 2. Query duplicate e pattern N+1

`effective_total()` → `calculated_service_total()` apre una **nuova connessione SQLite** ad ogni chiamata (non riceve mai una connessione già aperta). Raggiunta da `received_amount()`/`outstanding_amount()`, chiamate **per ogni riga** dentro il rendering delle tabelle pratiche — su una lista di N pratiche, fino a 2-3 connessioni+query extra per riga, mentre nello stesso metodo altri dati (urne, codici collaboratore) sono correttamente batchati con `IN (...)`. Il pattern corretto esiste già nel codebase, semplicemente non è usato qui.

Query IN() identiche ripetute in almeno 6 punti diversi invece di una funzione condivisa (es. lookup codici collaboratore, lookup urne per pratica).

**Direzione V2**: layer repository con funzioni che batchano per lista di ID, connessione propagata esplicitamente invece di riaperta ad ogni chiamata.

## 3. Colonne/tabelle con dati doppi o legacy (oltre a fatture/pagamenti, già documentati altrove)

- **"Secondo animale" come 5 colonne dedicate** (`animal2_*`) invece di riga aggiuntiva in tabella — limite strutturale: **non è possibile un terzo animale** sulla stessa pratica per costruzione.
- **14 colonne booleane `tag_*`** invece di tabella tag/junction — aggiungere un tag richiede ALTER TABLE + aggiornare le due liste duplicate del punto 1.
- **`urn_id`/`urn_id_2` convivono con `practice_items`** (il modello generico più recente, che supporta N urne/calchi/accessori): entrambi i modelli sono vivi contemporaneamente nel codice attuale, il commento sorgente lo conferma esplicitamente.
- **>200 colonne su `practices` gestite con `ALTER TABLE ADD COLUMN` idempotente ad ogni avvio** — nessun sistema di migrazioni versionate, solo un dizionario di "colonne extra" sempre crescente controllato via `PRAGMA table_info` al boot.

**Direzione V2**: normalizzare animali multipli e tag in tabelle relazionali; consolidare urne su un'unica fonte; adottare migrazioni versionate reali (tipo Alembic).

## 4. Storia di bonifiche dati sovrapposte

Tre script distinti, non uno:
- `balance_migration.py` — ricostruzione one-shot dei movimenti storici nel ledger nuovo, con riconoscimento anomalie dedicato (importi discordanti, pagamenti ambigui).
- `balance_repair.py` — **non è solo uno script manuale**: `repair_duplicate_balance_movements()` viene eseguito **automaticamente ad ogni avvio del server**, dentro l'inizializzazione del database. La bonifica di un problema di integrità del ledger è diventata una routine di riparazione permanente eseguita silenziosamente in produzione ad ogni riavvio, non un fix definitivo alla radice.
- `balance_legacy_repair.py` — corregge pratiche pre-esistenti un vecchio bug di storno (riferimento esplicito a una PR numerata nel codice), con backup automatico e verifica riga-per-riga prima di applicare.

**Impatto**: tre livelli di "toppe" sovrapposte sullo stesso sottosistema economico, incluso un ripara-automaticamente-ad-ogni-boot tuttora attivo in produzione.

**Direzione V2**: vincoli di integrità imposti dal database stesso (constraint, transazioni singole per operazione economica), non bonifiche a runtime.

## 5. Transazionalità

Il connection manager è già stato corretto una volta per un bug reale di leak (commento esplicito nel codice: connessioni mai chiuse su centinaia di call site, causa plausibile di rallentamenti progressivi con l'uptime del server) — bene che sia risolto, ma la nota stessa conferma un problema architetturale serio già vissuto in produzione.

La creazione pratica usa correttamente un solo blocco transazionale con `raise` (non `return`) sugli errori, garantendo rollback pulito — ma questo pattern **dipende dalla disciplina di ogni singolo handler**, non è imposto strutturalmente; non è stato possibile verificarlo su tutti gli ~466 handler nel tempo disponibile per questo audit.

**Direzione V2**: confine di transazione esplicito a livello di service/use-case, non lasciato alla disciplina del singolo handler.

## 6. Validazione sparsa

Esiste una validazione centralizzata per il form pratica (formato importi, campi obbligatori) — positivo. Ma la validazione delle date è sparsa: **47 punti** fanno parsing data con funzioni diverse, **46 blocchi `except ValueError`** distinti gestiscono l'errore in loco invece di una funzione condivisa unica.

**Direzione V2**: funzioni di dominio condivise per parsing/validazione (data, importo), riutilizzate ovunque con un tipo di ritorno esplicito.

## 7. Performance/memoria — nessuna paginazione reale

- L'endpoint principale "Archivio pratiche", con filtri vuoti, interroga **tutte** le pratiche non cancellate **senza LIMIT**; il collasso per mese è solo CSS/JS lato client, non paginazione reale. Cresce senza limite con l'età dell'azienda.
- Un endpoint di riepilogo pagamenti carica **tutte** le pratiche e filtra **in Python**, invece di spingere il filtro in SQL.
- **64 occorrenze** di `SELECT * FROM practices` nel file; la maggioranza è filtrata per id/stato, ma i due casi sopra non hanno alcun limite dimensionale.

Combinato col punto 2 (N+1), il rendering dell'archivio senza filtri è potenzialmente O(N) connessioni extra oltre alla query principale.

**Direzione V2**: paginazione server-side reale (keyset/cursor), aggregazioni finanziarie calcolate in SQL, non riga per riga in Python.

## 8. Sicurezza

- **SQL injection**: verificato sistematicamente ogni f-string dentro `.execute(...)` — nessun caso trovato di valore utente concatenato direttamente senza placeholder. I soli f-string con interpolazione sono segmenti strutturali whitelisted server-side (nomi colonna da un set noto, o `IN (?,?,...)` con tanti placeholder quanti elementi di una lista).
- **Password**: hashing PBKDF2-HMAC-SHA256 210.000 iterazioni + confronto a tempo costante — corretto e moderno.
- **Password di bootstrap debole e non forzata al cambio per l'admin**: l'utente `admin` viene creato con password nota (`"petparadise"`) **senza** l'obbligo di cambiarla al primo accesso, mentre gli operatori bootstrap SONO forzati a cambiarla. L'account con i privilegi più alti è quello meno protetto di default.
- **Nessuna protezione CSRF** su nessun form POST del sistema (nessun token, nessun controllo Origin/Referer).

**Direzione V2**: token CSRF sui form state-changing; forzare il cambio password anche per l'account admin di bootstrap, o generare una password casuale mostrata una sola volta all'installazione.

## 9. Test

`tests/` — **12 file, ~19.075 righe, ~851 funzioni di test** (613 nel solo `test_app.py`, che da sola è più grande, in proporzione, della codebase applicativa che testa). Nessun test risulta marcato skip/xfail. La mole concentrata in un unico file da 13.525 righe è essa stessa un sintomo del monolite non scomposto: test difficili da isolare per modulo perché il codice che testano non è modulare.

## 10. Evidenza quantitativa dello sviluppo "a patch"

Conteggio dei commenti che dichiarano esplicitamente origine da segnalazione utente/bug reale: **55 punti nel codice sorgente** portano questa traccia esplicita (frasi tipo "richiesta esplicita dell'utente", "bug segnalato dall'utente", "bug reale"), più almeno un endpoint diagnostico creato ad hoc per investigare un problema di produzione specifico, e il commento del fix leak-connessioni. Il codice porta le tracce di uno sviluppo guidato quasi interamente da segnalazioni reattive, coerente con ogni altro punto di questo documento.

## Riepilogo per priorità di intervento in V2

| # | Problema | Impatto | Urgenza |
|---|---|---|---|
| 1 | Nessun backup automatico (vedi doc. 01) | Perdita dati irreversibile in caso di guasto disco | **Massima — prima di qualunque migrazione** |
| 2 | FK dichiarate ma incomplete su `practices` (vedi doc. 01/02) | Integrità referenziale non garantita dal DB | Alta — da risolvere nel modello dati V2 |
| 3 | Dualità fatture/pagamenti (vedi doc. 03) | Dati incoerenti già osservati in produzione | Alta — priorità nel modello dati V2 |
| 4 | Nessuna FSM per stati pratica/eventi/cicli (vedi doc. 03) | Transizioni invalide silenziosamente accettate | Alta — dominio centrale V2 |
| 5 | Riparazione automatica ledger ad ogni boot | Sintomo di un problema mai risolto alla radice | Alta |
| 6 | Nessuna paginazione reale | Degrado prestazioni con la crescita dei dati | Media-alta |
| 7 | N+1 query su calcoli finanziari | Rallentamento liste lunghe | Media |
| 8 | Duplicazione helper Cremazioni giorno/settimana | Rischio bugfix parziali | Media |
| 9 | Nessun CSRF, admin senza cambio password forzato | Superficie di rischio sicurezza | Media-alta |
| 10 | Autorizzazione non centralizzata | Rischio di dimenticare un controllo su nuove route | Media |
| 11 | Sessioni senza scadenza server-side | Token compromesso resta valido indefinitamente | Media |
| 12 | Validazione data sparsa in 47 punti | Manutenibilità, non correttezza immediata | Bassa-media |
