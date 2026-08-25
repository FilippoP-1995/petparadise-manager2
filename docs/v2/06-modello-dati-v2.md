# 06 — Modello Dati V2

> Ogni scelta è etichettata FACT (verificato nel codice/DB attuale), DECISION (scelta progettuale V2), ASSUMPTION (ipotesi da verificare) o RISK (rischio introdotto). Base: `02-modello-dati-attuale.md`, `03-censimento-entita-funzionalita.md`, `04-problemi-sistema-attuale.md`, `05-mappa-relazioni.md`. Database target: PostgreSQL (decisione confermata dall'utente).

## Convenzioni generali (valgono per tutte le tabelle, non ripetute oltre)

### Chiavi primarie

- **DECISION**: `BIGINT GENERATED ALWAYS AS IDENTITY`, non UUID.
- **Perché**: la richiesta esplicita dell'utente (fase 1, punto 5) è "mantenere gli ID esistenti dove possibile". Un intero sequenziale permette di **preservare esattamente i valori ID attuali** durante la migrazione (`INSERT ... OVERRIDING SYSTEM VALUE` per riscrivere le righe con lo stesso id di V1), cosa che uno UUID renderebbe impossibile senza una tabella di mapping permanente per OGNI entità. Gli ID interi sono anche già usati in tutta la UI attuale (es. `practice_number` derivato, link diretti `/pratiche/{id}`) — mantenerli riduce drasticamente la superficie di cose da rimappare.
- **RISK**: gli ID sequenziali espongono il volume di dati (es. `/pratiche/1042` rivela "abbiamo fatto almeno 1042 pratiche") — rischio minore per un gestionale interno con autenticazione, non un'API pubblica. Accettato.
- **ASSUMPTION**: per le (poche) entità dove in V1 esistono ID duplicati/conflittuali tra tabelle diverse che in V2 vengono unificate (es. se emergesse la necessità di deduplicare client), servirà comunque una tabella `id_mapping_v1_v2(entity_name, old_id, new_id)` **solo per quei casi specifici** — non come pattern generale. Verificabile solo con accesso ai dati reali di produzione (oggi non disponibili).

### Timestamp e audit di scrittura

- **DECISION**: ogni tabella ha `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` (aggiornato da trigger `BEFORE UPDATE`, non a mano nel codice applicativo come oggi — **FACT**: oggi è scritto manualmente riga per riga in ogni handler, nessun meccanismo automatico), `created_by BIGINT REFERENCES users(id)`, `updated_by BIGINT REFERENCES users(id)`.
- **Perché**: elimina una classe di bug per dimenticanza (un nuovo endpoint che dimentica di aggiornare `updated_at`) spostando la responsabilità dal singolo handler al database.

### Audit trail (storico modifiche) — unificato

- **FACT**: oggi esistono tabelle di storico parallele con la stessa forma per entità diverse (`practice_history`, `calendar_event_history`), ciascuna con le proprie colonne quasi identiche.
- **DECISION**: un'unica tabella `audit_log`:
  ```
  audit_log(
    id BIGINT PK,
    entity_type TEXT NOT NULL,        -- 'practice' | 'calendar_event' | 'invoice' | 'payment' | 'cremation_cycle' | ...
    entity_id BIGINT NOT NULL,
    action TEXT NOT NULL,             -- 'created' | 'field_changed' | 'state_changed' | 'deleted' | 'restored' | ...
    field_name TEXT,                  -- NULL se l'azione non riguarda un singolo campo
    old_value TEXT,
    new_value TEXT,
    user_id BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )
  INDEX (entity_type, entity_id, created_at DESC)
  ```
- **Perché**: risponde direttamente al punto 12 della richiesta originale ("Audit e Storico... per fatture, pagamenti, DDT, pratiche, stati, ritiri, riconsegne, cremazioni") con UNA implementazione, non N implementazioni parallele da mantenere sincronizzate.
- **RISK**: una tabella singola per tutto l'audit cresce più velocemente e senza gli indici mirati che tabelle dedicate potrebbero avere. **Mitigazione**: indice composito su `(entity_type, entity_id)` copre l'accesso principale ("storico di QUESTA pratica"); se in futuro il volume lo richiede, si può partizionare per `entity_type` o per range di data senza cambiare lo schema logico (funzionalità nativa PostgreSQL).

### Denaro

- **FACT**: oggi ogni importo è un campo `TEXT` libero (es. `"120,00"`), e servono funzioni dedicate (`money_value`, `normalize_money_text`) sparse in tutto il codice solo per interpretarlo in modo sicuro — fonte di bug di parsing.
- **DECISION**: `BIGINT` in **centesimi interi** per ogni importo, mai `TEXT`, mai `NUMERIC`/`FLOAT`. Stesso pattern già usato correttamente da `balance_movements.amount_cents` in V1 — **non è un'invenzione nuova, è la generalizzazione di un pattern che nel codice attuale esiste già ed è corretto**.
- **Perché**: elimina ambiguità virgola/punto, elimina errori di arrotondamento in virgola mobile, elimina la necessità delle funzioni di parsing sparse.

### Enum e vocabolari

- **DECISION mista**, non un'unica regola per tutto:
  - **Postgres `ENUM` nativo** per vocabolari chiusi, stabili, con poche decine di valori al massimo, dove un nuovo valore richiede comunque una decisione di business esplicita (es. stato pratica, stato ciclo cremazione, ruolo utente, circuito pagamento W/D). Vantaggio: validato dal database stesso, non solo dall'applicazione.
  - **Tabella lookup** (`tags`, `service_types`, ecc.) dove l'elenco è pensato per crescere senza una modifica di schema (es. nuove etichette/tag operative) — risponde al problema già documentato in V1 ("un tag aggiunto a una lista e dimenticato nell'altra").
- **ASSUMPTION**: la scelta esatta enum-vs-lookup per ogni singolo campo minore (non i 4-5 principali già dettagliati sotto) verrà finalizzata quando si scrive lo schema DDL definitivo, non in questo documento di design.

### Soft delete

- **FACT**: oggi il soft-delete (`deleted_at`) esiste solo su alcune tabelle, e la sua presenza rompe silenziosamente i vincoli `ON DELETE ...` dichiarati (la FK "SET NULL" non scatta mai perché la riga non viene mai davvero cancellata finché non si passa dal Cestino).
- **DECISION**: soft-delete **solo** dove il "Cestino"/ripristino è un requisito di prodotto reale (Pratica, Evento calendario) — con la conseguenza esplicita gestita a livello di dominio (non di FK del database): quando una pratica va nel cestino, un **evento di dominio** (`PracticeTrashed`) aggiorna esplicitamente gli eventi calendario collegati, invece di contare su un vincolo DB che non può funzionare con il soft-delete. Per tutte le altre tabelle (es. tabelle di dettaglio/child come `practice_items`, `calendar_event_animals`): **hard delete reale con `ON DELETE CASCADE`**, perché non serve un "cestino" per una singola riga di dettaglio.

### Integrità referenziale

- **DECISION**: **ogni** colonna che rappresenta concettualmente una relazione ha una clausola `REFERENCES` esplicita con `ON DELETE` dichiarato intenzionalmente (mai omesso "per dimenticanza" come in almeno 8 casi già trovati su `practices` in V1).

---

## Le 5 incoerenze dell'audit — come vengono risolte

### 1. Fatture: fonte unica

```
invoices(
  id BIGINT PK,
  invoice_number TEXT NOT NULL,
  invoice_date DATE,
  total_amount_cents BIGINT NOT NULL,
  channel payment_channel NOT NULL,     -- enum: 'W' | 'D'
  practice_id BIGINT REFERENCES practices(id) ON DELETE SET NULL,
  practice_number_snapshot TEXT NOT NULL,   -- riferimento leggibile anche se la pratica sparisce
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
UNIQUE (invoice_number)   -- vincolo REALE a livello database, non solo applicativo

invoice_payment_links(
  invoice_id BIGINT REFERENCES invoices(id) ON DELETE CASCADE,
  payment_id BIGINT REFERENCES payments(id) ON DELETE CASCADE,
  PRIMARY KEY (invoice_id, payment_id)
)
```
- **DECISION**: eliminate completamente le colonne `invoice_number/invoice_date/invoice_total/invoice_total_manual/make_invoice` da `practices`. Una pratica non "ha" più una fattura come attributo proprio: **ha zero o più fatture**, sempre lette dalla stessa tabella.
- **DECISION (rivista dopo la tua risposta)**: hai indicato di voler permettere comunque la cancellazione di una pratica con fatture emesse. Ho scelto **`ON DELETE SET NULL`** invece di `ON DELETE CASCADE` — non `RESTRICT` (che avrebbe bloccato la cancellazione, contro la tua indicazione), ma **nemmeno CASCADE** (che avrebbe cancellato anche la fattura stessa, un documento fiscale — in contrasto con la tua stessa regola "nessuna perdita di dati"). Con `SET NULL` + `practice_number_snapshot` (stesso pattern già usato correttamente da `balance_movements` in V1): la pratica è cancellabile liberamente, la fattura resta come record fiscale indipendente e leggibile anche dopo. **Se invece intendevi che anche la fattura debba sparire con la pratica, dimmelo esplicitamente e cambio a CASCADE** — non l'ho presunto.

### 2. Pagamenti: un solo ledger, circuito mai ambiguo

- **FACT positivo da preservare**: `balance_movements` in V1 è già un ledger append-only ben progettato (trigger anti-UPDATE, storno invece di modifica, idempotency key).
- **DECISION**: `payment_movements` (la tabella "di dettaglio legacy", di cui il codice V1 stesso dice esplicitamente "non fidarsi mai per i totali") **non esiste in V2**. Il ledger è l'unica tabella di pagamento:
  ```
  payments(
    id BIGINT PK,
    payment_uuid UUID NOT NULL UNIQUE,
    practice_id BIGINT REFERENCES practices(id) ON DELETE SET NULL,
    practice_number_snapshot TEXT NOT NULL,   -- preserva il riferimento leggibile anche se la pratica sparisce, FACT: pattern già presente in V1 e corretto
    movement_date DATE NOT NULL,
    channel payment_channel NOT NULL,          -- enum 'W' | 'D' | 'Collaboratori' — stesso concetto di category oggi
    ledger_section ledger_section NOT NULL,     -- enum 'Entrata' | 'Uscita'
    movement_type TEXT NOT NULL,                -- 'Acconto' | 'Saldo' | 'Incasso completo' | 'Storno' | ...
    amount_cents BIGINT NOT NULL CHECK (amount_cents <> 0),
    payment_method TEXT,
    description TEXT,
    related_payment_id BIGINT REFERENCES payments(id) ON DELETE RESTRICT,  -- storni
    idempotency_key TEXT NOT NULL UNIQUE,
    collaborator_id BIGINT REFERENCES collaborators(id),
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )
  -- Trigger: BEFORE UPDATE → RAISE EXCEPTION (stesso principio append-only di V1, preservato)
  ```
- **Come si risolve l'ambiguità W/D alla radice**: in V1, "quale circuito vince" dipende da QUALE campo testo è valorizzato su `practices` (`total_service` vs `total_text`), e diverse funzioni interpretano questa domanda in modo diverso. **In V2 il circuito non è più dedotto**: ogni riga di preventivo (`practice_line_items`, vedi sotto) dichiara esplicitamente il proprio circuito, e "quanto è stato pagato" per un circuito è **sempre** `SUM(payments.amount_cents) WHERE practice_id = X AND channel = Y` — mai un campo duplicato che può disallinearsi dal ledger. Una pratica **può legittimamente avere sia W che D** (il caso reale già osservato nell'audit, che in V1 causava un bug) perché il modello lo prevede esplicitamente invece di far finta che non possa succedere.
- **RISK**: calcolare sempre "quanto pagato" via `SUM()` invece di leggere un campo pre-calcolato è più costoso a runtime di una lettura diretta. **Mitigazione**: indice su `(practice_id, channel)`, e se necessario una vista materializzata `practice_balances` aggiornata da trigger — ma il valore SORGENTE resta sempre il ledger, la vista è solo una cache, mai una seconda fonte di verità.

### 3. Macchine a stati esplicite (vedi documento dedicato più sotto, sezione "Stati")

### 4. Cancellazione pratica coerente

- **DECISION**: mantenere il soft-delete (serve per il Cestino, funzionalità reale usata quotidianamente), ma lo scollegamento degli eventi calendario collegati diventa un **passo esplicito del caso d'uso "Cestina pratica"** nel domain layer (non una FK che si spera scatti), con tanto di riga in `audit_log`. Query di verifica periodica (`eventi calendario collegati a pratiche cestinate`) inclusa nei controlli di integrità continua (vedi doc. 11 Piano di Test).

### 5. Un "secondo animale" e i tag come colonne fisse

```
animals(
  id BIGINT PK,
  practice_id BIGINT NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  name TEXT,
  species TEXT,
  breed TEXT,
  age_years SMALLINT,
  age_months SMALLINT,
  estimated_weight_grams INTEGER,   -- niente più TEXT libero per il peso
  microchip TEXT,
  sort_order SMALLINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```
- **DECISION**: N animali per pratica, senza limite artificiale a 2. Elimina `animal2_*` e ogni futuro `animal3_*` mai scritto ma probabilmente desiderato prima o poi.

```
tags(id BIGINT PK, code TEXT UNIQUE NOT NULL, label TEXT NOT NULL, category TEXT)
practice_tags(practice_id BIGINT REFERENCES practices(id) ON DELETE CASCADE,
              tag_id BIGINT REFERENCES tags(id) ON DELETE CASCADE,
              PRIMARY KEY (practice_id, tag_id))
```
- **DECISION**: un nuovo tag operativo diventa una riga di configurazione, non una migrazione di schema + due liste Python da tenere sincronizzate a mano.

---

## PRATICA — struttura completa V2

```
practices(
  id BIGINT PK,
  practice_number TEXT NOT NULL UNIQUE,
  status practice_status NOT NULL DEFAULT 'in_programma',   -- enum, vedi Stati
  request_origin TEXT NOT NULL,             -- 'Privato' | 'Veterinario' | 'Collaboratore' | 'Consegna in sede'
  destination_branch TEXT NOT NULL,
  client_id BIGINT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,   -- DECISION: obbligatorio, vedi sotto
  service_type TEXT NOT NULL,               -- 'Da decidere' | 'Cremazione singola' | 'Cremazione collettiva'
  collaborator_id BIGINT REFERENCES collaborators(id),
  veterinarian_id BIGINT REFERENCES veterinarians(id),
  origin_veterinarian_id BIGINT REFERENCES veterinarians(id),
  cremation_cycle_id BIGINT REFERENCES cremation_cycles(id) ON DELETE SET NULL,
  pickup_date DATE,
  pickup_time TIME,
  pickup_address TEXT,
  microchip TEXT,
  notes TEXT,
  ddt_number INTEGER UNIQUE,
  ddt_date DATE,
  ddt_pdf_path TEXT,
  signature_data TEXT,
  data_complete BOOLEAN NOT NULL DEFAULT false,
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ,
  deleted_by BIGINT REFERENCES users(id)
)

practice_line_items(              -- sostituisce ~30 colonne price_* fisse
  id BIGINT PK,
  practice_id BIGINT NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  category TEXT NOT NULL,          -- 'cremazione' | 'ritiro' | 'riconsegna' | 'urna' | 'calco' | 'accessorio' | 'serale' | 'notturno' | 'festivo' | ...
  description TEXT NOT NULL,
  amount_cents BIGINT NOT NULL,
  channel payment_channel NOT NULL DEFAULT 'W',
  urn_catalog_id BIGINT REFERENCES urns(id),
  sort_order SMALLINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

- **DECISION — `client_id` obbligatorio (`NOT NULL`)**: oggi una pratica può avere i dati del proprietario scritti direttamente come testo, senza mai passare da un vero record `clients`. In V2, ogni pratica **deve** riferire un cliente reale (creato al volo se non esiste, stessa UX di oggi — solo che il collegamento diventa garantito, non opzionale).
  - **RISK di migrazione**: pratiche storiche V1 con `owner_first_name`/`owner_last_name` ecc. scritti a mano ma senza `client_id` collegato **devono** ricevere un cliente creato ad-hoc durante la migrazione (deduplicando per nome+telefono dove possibile, esattamente come fa già `find_client_duplicates` in V1 — stesso algoritmo, riusato in fase di migrazione). Questo è un passo esplicito del documento 07 (Strategia di migrazione), non un dettaglio implicito.
  - **ASSUMPTION**: non è noto oggi quante pratiche storiche in produzione abbiano owner-inline senza `client_id` — va misurato con una query diretta sul DB di produzione prima di finalizzare questa decisione (query già inclusa in `05-mappa-relazioni.md`).

- **DECISION**: `veterinarian_id`/`origin_veterinarian_id`/`collaborator_id`/`urn_*` diventano tutte FK dichiarate esplicitamente (**FACT**: in V1 nessuna di queste ha una clausola REFERENCES).

- **Cosa NON è più su `practices`** (spostato altrove, con motivazione):
  | Rimosso da `practices` | Dove va | Perché |
  |---|---|---|
  | `animal_name`, `species`, `estimated_weight`, `animal2_*` | `animals` | N animali reali, non 1+1 fisso |
  | `invoice_*` | `invoices` | Fonte unica fatture |
  | `deposit`, `deposit_final`, `remaining_balance`, `remaining_final`, `total_service`, `total_text`, `payment_status`, `payment_amount` | Calcolati da `payments` + `practice_line_items`, mai memorizzati | Elimina la possibilità strutturale che "il totale mostrato" diverga dal ledger |
  | `price_*` (~30 colonne) | `practice_line_items` | Numero di voci preventivo non più limitato/fisso |
  | `tag_*` (14 colonne) | `tags` + `practice_tags` | Vocabolario estendibile senza migrazione |
  | `urn_id`, `urn_id_2` | `practice_line_items` (category='urna') | Unica fonte urne, niente doppio modello |
  | `pickup_address_mode`, `origin_mode`, `origin_text`, `transporter_mode` | Consolidati in un blocco "logistica" più semplice sulla pratica stessa (non spostati altrove: la loro esistenza come 4 colonne separate per esprimere sostanzialmente "indirizzo di ritiro, con una delle 3 fonti possibili" verrà semplificata in fase di DDL definitivo — **ASSUMPTION**: la logica esatta di fallback IDEM-SPED/Testo-libero/Veterinario va rivista con l'utente, non è cambiata "silenziosamente" qui) |

---

## Stati e macchine a stati (anticipazione — dettaglio completo nel documento successivo se richiesto)

**DECISION**: introdurre `ENUM` PostgreSQL per ciascuno, validati anche a livello applicativo (domain layer) per le transizioni, non solo per l'appartenenza all'insieme di valori:

```sql
CREATE TYPE practice_status AS ENUM
  ('ritirato','in_programma','cremato','da_consegnare','consegnato','smaltito');

CREATE TYPE pickup_status AS ENUM
  ('da_confermare','da_ritirare','ritirato','annullato');

-- DECISION (confermata dall'utente): 'completato' eliminato dal
-- vocabolario Riconsegna — non esiste nella pratica reale, il flusso
-- attuale (riconsegna considerata gestita quando programmata) resta
-- invariato. Nessun enum delivery_status: una Riconsegna in V2 non ha
-- affatto un campo di stato proprio (colonna eliminata), coerente col
-- fatto che in V1 era comunque sempre forzata a un solo valore fisso.

CREATE TYPE cremation_cycle_status AS ENUM
  ('pianificato','in_attesa','completato');

CREATE TYPE payment_channel AS ENUM ('W','D','Collaboratori');
CREATE TYPE ledger_section AS ENUM ('Entrata','Uscita');
CREATE TYPE user_role AS ENUM ('admin','operator');
```

**FACT → DECISION diretta**: il flusso "completamento ciclo cremazione" in V1 salta lo stato `"cremato"` (va direttamente a `"da_consegnare"`). **DECISION (confermata dall'utente)**: il completamento di un ciclo in V2 porta esplicitamente la pratica a `"cremato"`, e un passo separato (con propria autorizzazione/tracciamento in `audit_log`) porta a `"da_consegnare"`. **RISK accettato consapevolmente**: introduce un click/passaggio operativo in più per gli operatori rispetto a oggi — da comunicare chiaramente in fase di formazione al passaggio V1→V2, non una sorpresa dell'ultimo minuto.

---

## Tabelle che restano concettualmente invariate (nessuna incoerenza rilevata nell'audit)

`clients`, `veterinarians` (+`veterinarian_hours`), `collaborators` (+`collaborator_price_tiers`), `company_locations`, `urns`, `articles`, `shifts`, `shift_vacations`, `shift_oncall`, `route_plans`, `route_plan_stops`, `geocode_cache`, `notifications`, `notification_group_items`, `notification_delivery_log`, `push_subscriptions`, `reminders`, `whatsapp_messages`, `whatsapp_inbound_messages`, `whatsapp_cron_runs`, `email_orders`, `article_orders`, `settings` — portate in V2 con: FK esplicite ovunque mancanti, timestamp/audit standardizzati, `TEXT` liberi sostituiti da tipi propri dove rappresentano un dato strutturato (es. importi, date, orari), enum dove il vocabolario è chiuso. **Nessun redesign strutturale**: l'audit non ha trovato incoerenze concettuali in questi domini.

`calendar_events` + figlie. **DECISION (confermata dall'utente)**: `animals` è un'unica tabella condivisa, con **entrambe** le colonne di collegamento opzionali:
```
animals(
  id BIGINT PK,
  calendar_event_id BIGINT REFERENCES calendar_events(id) ON DELETE CASCADE,
  practice_id BIGINT REFERENCES practices(id) ON DELETE CASCADE,
  name TEXT, species TEXT, breed TEXT, age_years SMALLINT, age_months SMALLINT,
  estimated_weight_grams INTEGER, microchip TEXT, cremation_type TEXT,
  sort_order SMALLINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
CHECK (calendar_event_id IS NOT NULL OR practice_id IS NOT NULL)
```
Un animale nasce collegato solo a `calendar_event_id` (Ritiro non ancora diventato pratica). Quando il Ritiro diventa pratica, la **stessa riga** riceve anche `practice_id` (mai una copia nuova) — questo è esattamente il comportamento richiesto: "nessuna duplicazione/ricopiatura dei dati animale in quel passaggio". Se in futuro un animale dovesse esistere su una pratica senza essere mai passato da un evento Ritiro (es. inserimento diretto), `calendar_event_id` resta semplicemente NULL.

Dettaglio completo del dominio Calendario rimandato al documento architettura backend V2 (09).

---

## Tabella di mappatura V1 → V2 (sintesi, per il documento 07)

| V1 | V2 | Tipo di cambiamento |
|---|---|---|
| `practices.invoice_*` | `invoices` | Spostamento + unificazione fonte |
| `movement_invoices` + `movement_invoice_links` | `invoices` + `invoice_payment_links` | Rinominata, semantica invariata |
| `payment_movements` | *(eliminata)* | I dati utili confluiscono in `payments`, i "residui non affidabili" (per stessa ammissione del codice V1) non vengono migrati come fonte di verità — solo verificati e riconciliati contro il ledger |
| `balance_movements` | `payments` | Rinominata, struttura sostanzialmente preservata (pattern già corretto) |
| `practices.animal_name/species/.../animal2_*` | `animals` | Normalizzazione 1→N |
| `practices.tag_*` (14 colonne) | `tags` + `practice_tags` | Normalizzazione colonne→righe |
| `practices.price_*` (~30 colonne) | `practice_line_items` | Normalizzazione colonne→righe |
| `practices.urn_id/urn_id_2` | `practice_line_items` (category='urna') | Consolidamento su unico modello (già esistente in V1 come `practice_items`) |
| `practice_items` | `practice_line_items` | Rinominata/estesa per includere anche le vecchie colonne price_* |
| `practice_history` + `calendar_event_history` | `audit_log` | Unificazione |
| Colonne implicite non-FK (`client_id`, `veterinarian_id`, ecc.) | Stesse colonne, ora con `REFERENCES` reale | Integrità imposta dal database |

---

## Decisioni confermate dall'utente (chiude le domande aperte della versione precedente di questo documento)

| # | Domanda | Decisione finale |
|---|---|---|
| 1 | `client_id` obbligatorio su `practices`? | **Sì** — richiede passo di migrazione dedicato (creazione/collegamento client per pratiche storiche senza collegamento), vedi doc. 07 |
| 2 | Stato "Cremato" reso raggiungibile davvero? | **Sì** — passaggio operativo reale, un click in più per gli operatori, da comunicare in formazione |
| 3 | Stato "Completato" per Riconsegna? | **Eliminato dal vocabolario** — nessuna colonna di stato su Riconsegna in V2 |
| 4 | Animali Ritiro-non-ancora-pratica: stessa tabella o separata? | **Stessa tabella** `animals`, con `calendar_event_id`/`practice_id` entrambi opzionali (vedi sopra) |
| 5 | Pratica con fatture: cancellabile? | **Sì, cancellabile** — ma la fattura non viene mai cancellata con essa (`SET NULL` + snapshot, non `CASCADE` né `RESTRICT`, vedi nota sopra: da confermare se invece si vuole CASCADE anche sulla fattura) |

Questo documento è considerato **stabile** per procedere al documento 07 (Strategia di migrazione), fatta salva l'eventuale correzione sul punto 5 se la tua intenzione era diversa da quella che ho assunto.
