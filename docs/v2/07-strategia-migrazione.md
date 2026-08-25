# 07 — Strategia di Migrazione (SQLite → PostgreSQL)

> Dipende da `06-modello-dati-v2.md` (stabile). Segue il processo a stadi già indicato dall'utente: backup verificato → copia staging → migrazione → verifica automatica → test applicazione/relazioni/funzionale → correzione → nuovo backup → solo dopo pianificazione produzione. **Nessuna migrazione di produzione in questo documento — è un piano, non un'esecuzione.**

## Principio guida

**DECISION**: la migrazione avviene in **due fasi distinte**, non in un unico script che legge da SQLite e scrive direttamente nel modello V2 normalizzato:

```
Fase A — Lift-and-shift fedele          Fase B — Trasformazione
SQLite (produzione, MAI toccata) ──►    schema "v1_raw" in Postgres ──►  schema V2 normalizzato
   sola lettura                            copia 1:1, stesse colonne         (via SQL/Python dentro Postgres)
```

**Perché due fasi e non una**: separare "copiare i byte" da "trasformare la logica" rende ogni fase verificabile ed eseguibile in isolamento. Se la Fase B ha un bug, si corregge e si rilancia **senza mai rileggere di nuovo da SQLite** (più lento, più rischioso) — si riparte dallo schema `v1_raw` già fermo in Postgres, che è a sua volta ricreabile all'infinito da un backup verificato, mai dalla produzione live.

- **RISK mitigato**: la Fase A tocca la produzione **una volta sola per ogni tentativo di migrazione** (sola lettura, mai scrittura), sempre a partire da un backup, mai dal database live in uso dagli operatori.
- **DECISION**: la Fase A NON usa uno strumento generico di terze parti per il collegamento diretto SQLite→Postgres (tipo `pgloader`) come passo con accesso diretto alla produzione — usa invece l'export del **backup verificato** (già prodotto dal sistema di backup indipendente implementato in questa sessione) come unica sorgente. La produzione live non viene mai interrogata direttamente da uno script di migrazione.

## Fase A — Lift-and-shift

1. Prendere l'ultimo backup verificato (`backup_service.py`, già in produzione) — mai un file preso "a mano" con una copia grezza.
2. Ripristinarlo in un ambiente isolato (staging), MAI sullo stesso server della produzione.
3. Copiare ogni tabella SQLite in una tabella Postgres **con lo stesso nome, stesse colonne, stessi tipi il più fedelmente possibile** (schema `v1_raw`, un namespace Postgres dedicato, separato dallo schema `public` che ospiterà il modello V2). Nessuna trasformazione qui — se una colonna SQLite era `TEXT` libero con valori tipo `"120,00"`, resta `TEXT` anche in `v1_raw`, non ancora convertita in centesimi.
4. **Verifica di Fase A** (automatica, bloccante): per ogni tabella, `COUNT(*)` in SQLite deve essere **identico** al `COUNT(*)` in `v1_raw`. Nessuna eccezione.

## Fase B — Trasformazione nel modello V2

Uno script Python (o una serie di migrazioni SQL versionate, vedi sotto) che legge da `v1_raw` e scrive nello schema V2, applicando **esattamente** la tabella di mappatura già documentata in `06-modello-dati-v2.md`. Passi principali, nell'ordine (rispettando le dipendenze FK):

1. `users`, `clients`, `veterinarians` (+`veterinarian_hours`), `collaborators` (+`collaborator_price_tiers`), `company_locations`, `urns`, `articles`, `tags` (popolata con i 14 valori fissi già noti da V1) — nessuna trasformazione strutturale, solo pulizia tipi (importi→centesimi, date libere→`DATE` reale).
2. **Deduplicazione/collegamento clienti** (il passo più delicato, per la decisione "client_id obbligatorio"):
   - Per ogni pratica in `v1_raw.practices` con `client_id` già valorizzato → collegamento diretto.
   - Per ogni pratica **senza** `client_id` ma con `owner_first_name`/`owner_last_name`/`owner_phone`/ecc. valorizzati → riuso dell'algoritmo di dedup **già esistente e collaudato in V1** (`find_client_duplicates`, stessa logica di fuzzy-match su nome/telefono/email) per capire se corrisponde a un cliente già creato da un'altra pratica, altrimenti creazione di un nuovo record `clients`.
   - **ASSUMPTION da verificare sui dati reali** (oggi non misurabile, DB locale vuoto): quante pratiche storiche rientrano in questo caso. La query è già pronta in `05-mappa-relazioni.md`.
   - Ogni collegamento creato in questo passo viene registrato in `audit_log` con `action='migrated_client_link'`, per poter sempre ricostruire "questo collegamento l'ha fatto la migrazione, non un operatore".
3. `practices` (colonne base + `client_id` dal passo 2).
4. `animals` (da `practices.animal_name/species/.../animal2_*` → righe; da `calendar_event_animals` → righe con `calendar_event_id`).
5. `practice_tags` (da 14 colonne booleane → righe, solo dove il valore era "Si").
6. `practice_line_items` (da `practice_items` esistente + dai vecchi campi `price_*` per le pratiche che non erano ancora passate al modello più recente — **entrambe le fonti vanno lette**, esattamente come oggi fa già `calculated_service_total()` in V1).
7. `invoices` + `invoice_payment_links` (unificazione delle due fonti V1 — controllo duplicati per `invoice_number` PRIMA dell'insert, con log esplicito di ogni conflitto trovato per revisione manuale, mai una scelta automatica silenziosa tra le due).
8. `payments` (da `balance_movements`, struttura quasi identica — riconciliata contro `payment_movements` solo per completare eventuali metadati mancanti, mai come fonte di importi).
9. `calendar_events` (senza le colonne `payment_status`/`payment_amount`, ora sempre calcolate dal lato pratica collegata).
10. `cremation_cycles`.
11. Tabelle indipendenti senza redesign (turni, percorsi, notifiche, whatsapp, ecc.) — copia diretta con pulizia tipi.
12. `audit_log` (da `practice_history` + `calendar_event_history` unificate).

**DECISION — tooling**: la Fase B è implementata come **migrazioni Alembic** (lo stesso strumento scelto per l'ORM in doc. 09), non uno script monolitico usa-e-getta. Motivo: ogni passo diventa ripetibile, ordinato, con `upgrade()`/`downgrade()` — permette di rilanciare la trasformazione da zero su una copia fresca di `v1_raw` tutte le volte che serve durante lo sviluppo, senza stato residuo.

## Verifica post-migrazione (non solo conteggi)

Per ogni entità, tre livelli di controllo, tutti automatizzati (non a campione manuale):

1. **Conteggio**: righe V1 = righe V2 corrispondenti (tenendo conto delle trasformazioni 1→N documentate, es. 14 colonne tag → N righe `practice_tags`, il conteggio atteso non è 1:1 ma è comunque calcolabile e verificabile).
2. **Relazioni end-to-end**: lo script scenario esplicito richiesto dall'utente —
   ```
   Cliente → Pratica → Animale → Ritiro → Cremazione → Riconsegna → Fattura → Pagamento
   ```
   Per un campione statisticamente significativo di pratiche (non tutte, su dataset grandi — ma il 100% se il volume lo permette, dato che l'azienda non ha probabilmente milioni di righe), lo script segue la catena di FK partendo da ogni pratica in V1 e verifica che la stessa identica catena esista in V2, con gli stessi valori chiave (non solo che "esista qualcosa", ma che l'importo, la data, lo stato corrispondano secondo la regola di trasformazione documentata).
3. **Invarianti strutturali** (impossibili da violare nel nuovo schema, ma verificati comunque per sicurezza sulla trasformazione): nessuna pratica con più fatture con lo stesso numero, nessun pagamento orfano (`practice_id` che punta a niente e non ha `practice_number_snapshot`), somma dei `payments` per pratica coerente con quanto risultava "incassato" in V1 per la stessa pratica (controllo incrociato tra il vecchio e il nuovo calcolo, non solo interno al nuovo schema).

Lo script di verifica produce un **report** (non solo pass/fail): per ogni pratica con una discrepanza, il dettaglio esatto di cosa non torna — nessuna discrepanza viene "risolta automaticamente silenziosamente" durante la verifica.

## Gestione dei dati modificati durante la migrazione (Fase 6 della richiesta originale)

**DECISION**: **non si tenta la sincronizzazione in tempo reale** tra V1 e V2 durante lo sviluppo (nessun dual-write, nessun CDC/change-data-capture) — è una complessità ingegneristica sproporzionata per il volume di questo progetto e per l'assenza di una scadenza fissa.

Il modello adottato:
- Durante **tutto** lo sviluppo e i test della V2, V1 resta l'unico sistema realmente in produzione, usato normalmente dagli operatori. La migrazione viene eseguita e rieseguita quante volte serve **su copie** (backup verificati), mai sul live.
- Solo quando la V2 è verificata e approvata (tutti i controlli precedenti verdi, test funzionali completi, doc. 11), si pianifica un **taglio finale** con una finestra breve e comunicata agli operatori (es. "per 15-30 minuti, non creare/modificare nulla") durante la quale:
  1. Ultimo backup di V1.
  2. Ultima esecuzione della migrazione (Fase A + B) a partire da quell'ultimo backup.
  3. Ultima verifica automatica (deve essere verde).
  4. Switch del traffico operativo su V2.
- **RISK**: qualunque modifica fatta su V1 **dopo** l'ultimo backup usato per il taglio finale e **prima** dello switch va persa se non gestita. **Mitigazione**: la finestra è annunciata e breve; in alternativa, se si vuole zero-downtime reale, si può eseguire un'ultima mini-migrazione incrementale (solo le righe con `updated_at` più recente dell'ultimo backup) subito prima dello switch — dettaglio da rifinire quando si arriva a questa fase, non bloccante ora.

## Rollback

- **In ogni momento prima del taglio finale**: rollback banale, perché V1 non è mai stata toccata. Si butta via l'ambiente staging e si riparte.
- **Dopo il taglio finale**: se emergono problemi nelle prime ore/giorni di uso reale di V2, il rollback è "si torna a puntare gli operatori su V1" — possibile **solo se** V1 non è stata nel frattempo disattivata/cancellata. **DECISION**: V1 resta **installata e funzionante, non spenta**, per un periodo di sicurezza post-taglio (durata da concordare, es. 2-4 settimane) prima di essere considerata definitivamente dismessa — coerente con "V1 e V2 devono coesistere" e con l'assenza di una scadenza fissa.
