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
  originating_pickup_event_id BIGINT REFERENCES calendar_events(id) ON DELETE SET NULL,  -- NUOVO, vedi "Relazione Ritiro -> Pratica" sotto
  destination_branch_id BIGINT NOT NULL REFERENCES company_locations(id),  -- ERA destination_branch TEXT libero, vedi Addendum C
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

### Relazione Ritiro → Pratica (decisione aziendale, aggiornamento post Architecture Gate)

- **DECISION (confermata dall'utente)**: nel modello operativo reale, una pratica rappresenta sostanzialmente il ritiro formalizzato/documentato — **il caso ordinario/di progetto è `RITIRO → PRATICA`**, non "pratica indipendente creata dal nulla". Le due entità **restano tecnicamente distinte** (`calendar_events` per il Ritiro, `practices` per la pratica) — l'utente ha esplicitamente escluso una fusione fisica delle tabelle — ma la relazione tra le due deve essere rappresentata esplicitamente e in entrambe le direzioni, non solo da evento→pratica come oggi.
- **FACT**: in V1 la relazione è oggi **unidirezionale**: `calendar_events.linked_practice_id` punta alla pratica generata, ma non esiste alcuna colonna sul lato `practices` che punti indietro al Ritiro di origine — l'unico modo per risalire dalla pratica al ritiro è una query inversa su `linked_practice_id`.
- **DECISIONE TECNICA**: aggiunta `practices.originating_pickup_event_id BIGINT REFERENCES calendar_events(id) ON DELETE SET NULL` — rende la relazione interrogabile direttamente dal lato pratica (es. "da quale ritiro nasce questa pratica, con che indirizzo/orario/animali era stato registrato"), senza duplicare i dati del ritiro sulla pratica. `ON DELETE SET NULL` (non CASCADE, non RESTRICT) per lo stesso principio già applicato ovunque: la pratica non deve mai sparire né essere bloccata dalla sorte del ritiro che l'ha generata.
- **Come nasce una pratica "ordinaria"**: il caso d'uso di dominio `create_practice_from_pickup(pickup_event_id)` valorizza `originating_pickup_event_id` e **copia** (non collega live) i dati di logistica del ritiro (vedi `pickup_type`/`pickup_location_id`/ecc., Addendum C aggiornato sotto) sulla pratica al momento della creazione — stesso principio già applicato agli animali (Addendum "tabelle concettualmente invariate": la riga non si duplica, ma qui si tratta di campi scalari non di una riga condivisibile, quindi la copia avviene una volta, alla creazione, e da quel momento la pratica è la fonte autoritativa per sé stessa).
- **DECISIONE AZIENDALE CHIUSA (aggiornamento doc 15) — due percorsi di creazione ammessi, entrambi legittimi**:
  - **Percorso A (normale)**: `Ritiro → Pratica`, `originating_pickup_event_id` valorizzato.
  - **Percorso B (diretto)**: pratiche `Collaboratore`/`Consegna in sede` create direttamente, **senza** un ritiro di origine — `originating_pickup_event_id` resta `NULL` legittimamente, non è un caso anomalo o da correggere.
  - **Motivazione aziendale**: quando una pratica `Collaboratore`/`Consegna in sede` viene creata direttamente, l'animale è già fisicamente preso in carico da Pet Paradise nel momento in cui la pratica viene registrata.
  - **Conseguenza sullo stato iniziale**: in **entrambi** i percorsi la pratica nasce nello stato `ritirato` (mai in un altro stato) — dettaglio completo del vincolo, incluso il fatto che lo stato iniziale non è mai un parametro esposto dall'API di creazione, in `docs/v2/14-macchine-stati-transizioni.md` §1.
  - Il campo resta `NULL`able esattamente per rappresentare in modo pulito il Percorso B, non come "buco" nel modello.

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

---

# Addendum — Completamento modello dati (chiusura condizioni Architecture Gate, doc 13)

> Ogni gruppo di colonne trovato non mappato dall'audit (doc 13 §2.1-2.2) è indirizzato qui con: destinazione V2, trasformazione, motivo architetturale, strategia di migrazione, strategia di verifica, possibilità di deprecazione futura. **Nessuna struttura è stata eliminata** solo perché V1 la usa male — dove il comportamento V1 andava comunque ridisegnato (non solo rinominato), è dichiarato esplicitamente. Classificazione per ogni punto: **DECISIONE TECNICA** (presa qui, motivata) o **DECISIONE AZIENDALE NECESSARIA** (non presa, richiede una tua risposta) o **ASSUMPTION** (da validare sui dati reali).

## A. Owner snapshot storico

```
practices(
  ...
  owner_snapshot JSONB,   -- scattato UNA VOLTA alla creazione della pratica, mai più riscritto
  ...
)
```

- **Destinazione V2**: un unico campo `owner_snapshot JSONB` su `practices` (non 16 colonne separate), contenente `{first_name, last_name, phone, phone_2, email, tax_code, address, street, city, province, zip, company, vat, sdi, notes, phone_note}` così come risultavano al momento della creazione/ultima modifica della pratica.
- **Trasformazione**: le 16 colonne `owner_*` di V1 confluiscono in un solo oggetto JSON, non in 16 colonne V2 parallele.
- **Motivo architetturale**: uno snapshot JSON, per costruzione, **non può** essere letto/joinato come se fosse `clients` — questo lo rende impossibile da usare accidentalmente come seconda fonte di verità dell'anagrafica corrente (a differenza di 16 colonne "vive" che inviterebbero a essere aggiornate in parallelo a `clients`, ricreando la stessa dualità già vista per le fatture). `clients` resta l'**unica** fonte per "qual è oggi l'indirizzo di questo cliente"; `owner_snapshot` risponde solo a "qual era l'indirizzo quando fu creata QUESTA pratica" — usato per stampa DDT/documenti storici, mai per calcoli o ricerche.
- **Migrazione**: per ogni pratica V1, costruire il JSON dalle 16 colonne `owner_*` così come sono oggi, indipendentemente dal fatto che `client_id` fosse già collegato o meno.
- **Verifica**: conteggio pratiche con `owner_snapshot` non nullo in V2 = conteggio pratiche V1 con almeno un campo `owner_*` valorizzato; verifica a campione che il JSON contenga esattamente gli stessi valori delle colonne originali.
- **Deprecazione futura**: nessuna prevista — è uno storico, per definizione non decade. Non va rimosso "perché vecchio".
- **Classificazione**: DECISIONE TECNICA (forma dello snapshot — JSON vs colonne — presa da me, motivata sopra). Nessuna decisione aziendale necessaria: rispetta esattamente il vincolo che hai posto ("distinguibile dall'anagrafica corrente, non deve diventare una seconda fonte di verità").

## B. DDT / trasporto / tracciabilità

```
practices(
  ...
  transport_method TEXT,
  vehicle_plate TEXT,
  temperature_mode TEXT,
  package_count INTEGER,
  container_id TEXT,
  lot_number TEXT,
  treatment_method TEXT,
  delivery_at_clinic BOOLEAN NOT NULL DEFAULT false,
  delivery_at_home BOOLEAN NOT NULL DEFAULT false,
  signatory_identity_document_number TEXT,
  signatory_identity_document_date DATE,
  signatory_signing_place TEXT,
  ddt_share_token TEXT UNIQUE,
  original_practice_number TEXT,
  ...
)
```

- **Destinazione V2**: colonne dirette e tipizzate su `practices` (non una tabella separata — sono attributi 1:1 della pratica, non righe ripetute, quindi normalizzarle in una tabella figlia non ridurrebbe alcuna duplicazione reale).
- **Trasformazione**: solo tipizzazione (`package_count` → intero, le due date → `DATE`, `delivery_at_clinic/at_home` da `TEXT` "Si"/vuoto → `BOOLEAN`). Nessuna perdita semantica.
- **Motivo architetturale**: mantenuti 1:1 come richiesto esplicitamente — nessuno di questi campi viene dichiarato "non necessario" da questo documento.
- **Migrazione**: copia diretta con coercizione di tipo, verificata a campione.
- **Verifica**: confronto valore-per-valore su un campione statisticamente significativo, non solo conteggio.
- **`ddt_share_token`**: preservato **esattamente com'è** (incluso il vincolo `UNIQUE`), perché link di condivisione già distribuiti a clienti reali devono continuare a funzionare dopo il cutover.
- **`original_practice_number`**: preservato esattamente com'è, resta parte integrante del meccanismo di cestino/ripristino (vedi anche doc 13 §2.1.2).
- **🔶 VERIFICA NORMATIVA PENDENTE**: `transport_method`, `vehicle_plate`, `temperature_mode`, `container_id`, `lot_number`, `treatment_method` hanno l'aspetto di dati di tracciabilità potenzialmente soggetti a obblighi normativi (trasporto sottoprodotti animali); `signatory_identity_document_*`/`signing_place` sono dati del documento d'identità del firmatario, potenzialmente soggetti a normativa privacy con un proprio periodo di conservazione. **L'utente ha confermato esplicitamente**: non è stata ancora fatta una verifica con un consulente/commercialista su questi obblighi. **Decisione provvisoria confermata dall'utente: conservare tutti questi dati indefinitamente.** Questa è dichiarata esplicitamente **una scelta tecnica prudenziale**, **non** la descrizione di un obbligo normativo verificato — la distinzione va mantenuta anche in ogni documento futuro che tratti questo punto.
- **ASSUMPTION**: `transport_method`/`temperature_mode`/`treatment_method` potrebbero essere vocabolari chiusi (candidati a `ENUM` invece di `TEXT` libero) — da verificare sui valori realmente presenti in produzione prima di finalizzare il DDL, non assunto qui.
- **Deprecazione futura**: nessuna, per scelta prudenziale — resta soggetta a revisione se/quando arriva una verifica normativa reale (VERIFICA NORMATIVA PENDENTE, non chiusa da questo documento).

## C. Provenienza / origine / logistica multi-sede (RIVISTA dopo l'aggiornamento decisioni aziendali — doc 15)

> **Chiusura della decisione aziendale precedentemente aperta**: l'utente ha chiarito il modello reale — Pet Paradise opera su più sedi (oggi Livorno, con l'impianto di cremazione, ed Empoli; l'azienda serve anche zone come Pisa, Firenze, Viareggio); un animale può essere affidato in una sede/zona e la cremazione può avvenire in una sede diversa da quella di affido; la riconsegna può avvenire in ambulatorio o a domicilio, con una propria zona. Questa sezione sostituisce integralmente la versione precedente (basata su un solo campo `origin_type` insufficiente a rappresentare tutto questo).

**DECISIONE TECNICA — principio guida**: **niente viene mai dedotto**. Ogni concetto elencato dall'utente (sede di affido, luogo/zona del ritiro, sede di destinazione, modalità di riconsegna, luogo/zona di riconsegna, sede che esegue la cremazione, veterinario/collaboratore come punto di affido) diventa un campo esplicito, mai un valore inferito da un altro campo — stesso principio già applicato al circuito W/D e già presente nel resto di questo documento. Questo **chiude anche** la decisione aziendale rimasta aperta sulla "logica di fallback": non esiste una logica di fallback automatica da progettare, perché l'operatore sceglie sempre esplicitamente ogni campo — non c'è nulla da dedurre.

### Lato Ritiro/Affido — su `practices` (via `originating_pickup_event_id` per le pratiche nate da un Ritiro, valorizzati comunque per le pratiche create direttamente)

```
CREATE TYPE pickup_type AS ENUM ('sede_aziendale','domicilio','veterinario','collaboratore','altro');

practices(
  ...
  pickup_type pickup_type NOT NULL DEFAULT 'domicilio',
  pickup_location_id BIGINT REFERENCES company_locations(id),   -- valorizzato SOLO se pickup_type='sede_aziendale'
  pickup_zone_id BIGINT REFERENCES calendar_zones(id),           -- valorizzato SOLO se pickup_type='domicilio' (riuso della tabella zone gia' esistente in V1, non una nuova struttura)
  pickup_address TEXT,                                           -- gia' esistente, indirizzo puntuale (domicilio/altro)
  pickup_contact_name TEXT,                                      -- sostituisce origin_first_name+origin_last_name, valorizzato solo per pickup_type='altro'
  -- pickup_type='veterinario'  -> il punto di affido E' origin_veterinarian_id (gia' FK esistente), nessun altro campo lo duplica
  -- pickup_type='collaboratore' -> il punto di affido E' collaborator_id (gia' FK esistente), nessun altro campo lo duplica
  provenance_code TEXT,   -- invariato: alimenta il badge colorato gia' in uso (doc 13 §2.1.3)
  ...
)
```

- **`sede di affido/ritiro`** → `pickup_type='sede_aziendale'` + `pickup_location_id` (FK a `company_locations`, la stessa tabella già esistente in V1 con le sedi Livorno/Empoli — non una nuova struttura). Esempio dell'utente: "animale preso dalla sede di Empoli" → `pickup_type='sede_aziendale'`, `pickup_location_id` = riga Empoli.
- **`luogo/zona del ritiro quando necessario`** → `pickup_type='domicilio'` + `pickup_zone_id` (FK a `calendar_zones`, tabella di zone già esistente in V1 — Addendum M) per la zona geografica, + `pickup_address` per l'indirizzo puntuale quando serve.
- **`veterinario/collaboratore come punto di affido`** → `pickup_type='veterinario'`/`'collaboratore'`, il punto di affido è direttamente `origin_veterinarian_id`/`collaborator_id` (FK già esistenti in questo documento) — **nessun campo aggiuntivo li duplica**, evitando la stessa ambiguità già risolta per i pagamenti (mai due modi di dire la stessa cosa).

### Lato Destinazione/Cremazione — su `practices` e `cremation_cycles`

```
practices(
  ...
  destination_branch_id BIGINT NOT NULL REFERENCES company_locations(id),   -- ERA destination_branch TEXT libero (vedi nota sotto)
  ...
)

cremation_cycles(
  ...
  cremation_location_id BIGINT REFERENCES company_locations(id),   -- NUOVO
  ...
)
```

- **`sede di destinazione`** → `practices.destination_branch_id`, ora **FK reale** verso `company_locations` invece del `TEXT` libero di V1 (che oggi vale sempre e solo "Livorno" o "Empoli", i due valori di `BRANCHES` in app.py — **FACT verificato nel codice**). Rappresenta quale sede aziendale è amministrativamente responsabile della pratica (rilevante oggi anche per il tipo di documento generato: Livorno = "FORNO CREMATORIO", Empoli = "IMPRESA FUNEBRE", **FACT** da `BRANCHES` in app.py).
- **`sede che esegue la cremazione`** → `cremation_cycles.cremation_location_id`, **deliberatamente separata** da `destination_branch_id`: nell'esempio dell'utente, una pratica destinata a Empoli può comunque essere cremata a Livorno (l'unica sede con l'impianto) — le due informazioni possono divergere, quindi non possono essere lo stesso campo. Vive sul **ciclo di cremazione** (non sulla singola pratica) perché è la sede fisica dove avviene l'operazione condivisa da tutti gli animali assegnati a quel ciclo, non un attributo della singola pratica.
- **ASSUMPTION tecnica, non aziendale**: oggi con solo 2 sedi e un solo impianto, `cremation_location_id` coinciderà quasi sempre con l'unica sede "FORNO CREMATORIO" — il campo è comunque modellato esplicitamente (non hardcoded) per non presumere che resti sempre così se in futuro si aprisse un secondo impianto.

### Lato Riconsegna — su `calendar_events` (non su `practices`, coerentemente con Addendum P)

```
CREATE TYPE delivery_type AS ENUM ('ambulatorio','domicilio','sede_aziendale','altro');

calendar_events(
  ...
  delivery_type delivery_type,                                    -- sostituisce delivery_location_type libero
  delivery_veterinarian_id BIGINT REFERENCES veterinarians(id),    -- rinominato da delivery_clinic_id, stesso ruolo, valorizzato solo se delivery_type='ambulatorio'
  delivery_location_id BIGINT REFERENCES company_locations(id),    -- NUOVO, valorizzato solo se delivery_type='sede_aziendale'
  delivery_zone_id BIGINT REFERENCES calendar_zones(id),            -- NUOVO, valorizzato solo se delivery_type='domicilio'
  delivery_address TEXT,                                            -- indirizzo puntuale quando serve
  ...
)
```

- **Perché sulla Riconsegna e non sulla Pratica**: stesso principio già stabilito nell'Addendum P per i campi di pagamento preliminare — la riconsegna è un evento che può non esistere ancora quando la pratica viene creata (la cremazione potrebbe non essere nemmeno iniziata), quindi l'informazione "dove/come verrà riconsegnato" appartiene all'evento Riconsegna, non alla pratica. Aggiungere questi campi anche su `practices` li duplicherebbe, ricreando la stessa classe di problema (due fonti per lo stesso dato) che questo intero documento elimina sistematicamente.
- **`modalità di riconsegna`** → `delivery_type` esplicito (sostituisce il libero `delivery_location_type` di V1).
- **`luogo/zona di riconsegna`** → `delivery_zone_id` (domicilio) o `delivery_location_id` (sede aziendale) o `delivery_veterinarian_id` (ambulatorio), mai dedotti dal tipo.

### Migrazione (per tutti i campi sopra)

- `destination_branch` (TEXT "Livorno"/"Empoli") → risolto a `destination_branch_id` per nome, matching diretto con `company_locations.name` (già seedate con questi due nomi esatti in V1, **FACT**).
- `pickup_address_mode`/`origin_mode`/`transporter_mode`/`origin_text`/`origin_first_name`/`origin_last_name` → mappati a `pickup_type`/`pickup_zone_id`/`pickup_address`/`pickup_contact_name` secondo i valori realmente presenti (tabella di corrispondenza da scrivere in fase di DDL, valori non riconducibili finiscono in `pickup_type='altro'` con `pickup_contact_name`/`pickup_address` popolati dal valore grezzo, **mai scartati**).
- `calendar_events.delivery_location_type`/`delivery_clinic_id`/`delivery_clinic_name`/`delivery_clinic_address`/`delivery_clinic_phone` → mappati a `delivery_type`/`delivery_veterinarian_id`/`delivery_address`.
- **Verifica**: nessuna pratica/evento migrato deve perdere l'informazione di sede/zona/indirizzo che aveva in V1 — stesso principio "mai scartare" già applicato a ogni altro campo in questo documento.

### Classificazione

**DECISIONE TECNICA** per la struttura dei campi (la scelta di quali FK/enum usare) — **motivata direttamente dalla decisione aziendale ricevuta** (multi-sede, affido/destinazione/cremazione distinti, riconsegna in ambulatorio o domicilio). Nessuna regola di fallback da inventare, perché il modello richiede sempre una scelta esplicita dell'operatore, mai un'inferenza automatica.

## D. Override manuali (decisioni operatore, non dati derivati)

```
practices(
  ...
  computed_total_override_cents BIGINT,      -- NULL = usa sempre il totale calcolato da practice_line_items
  computed_total_override_reason TEXT,
  computed_total_override_by BIGINT REFERENCES users(id),
  computed_total_override_at TIMESTAMPTZ,
  to_invoice BOOLEAN NOT NULL DEFAULT false, -- sostituisce make_invoice: "questa pratica deve essere fatturata"
  ...
)
```

- **Destinazione V2**: un blocco esplicito di override sulla pratica, **separato** dal calcolo automatico (mai una sovrascrittura silenziosa dei due).
- **Trasformazione**: `total_service_manual`/`invoice_total_manual` (flag booleani "questo è stato corretto a mano" accoppiati a un valore) diventano un unico meccanismo esplicito: se `computed_total_override_cents` è valorizzato, l'interfaccia mostra **quel** valore come totale ufficiale (con indicazione visiva "valore corretto manualmente da {utente} il {data}: {motivo}"), il totale calcolato da `practice_line_items` resta comunque visibile a fianco per confronto, mai nascosto. `make_invoice` → `to_invoice` (stesso significato, solo rinominato per chiarezza semantica visto che in V2 non convive più con le colonne fattura legacy).
- **Motivo architetturale**: risponde esattamente al vincolo posto ("non deve essere possibile che un ricalcolo automatico sovrascriva silenziosamente una decisione manuale storica") — finché `computed_total_override_cents` non viene esplicitamente azzerato da un operatore (azione dedicata "torna al calcolo automatico", loggata in `audit_log`), nessun ricalcolo lo tocca.
- **Migrazione**: dove `total_service_manual='Si'` in V1, popolare `computed_total_override_cents` col valore che risultava allora; `computed_total_override_reason` viene popolato con un testo standard ("valore manuale migrato da V1") dato che V1 non registrava un motivo esplicito — **limite noto della fonte dati, non introdotto dalla migrazione**.
- **Verifica**: ogni pratica V1 con flag manuale attivo deve avere un corrispondente `computed_total_override_cents` non nullo in V2, con lo stesso importo.
- **Deprecazione futura**: nessuna — il meccanismo di override è strutturale, non un residuo temporaneo.
- **Classificazione**: DECISIONE TECNICA (presa qui, coerente col vincolo esplicito che hai dato).

## E. Workflow operativo

```
practices(
  ...
  send_catalog BOOLEAN NOT NULL DEFAULT false,
  catalog_sent BOOLEAN NOT NULL DEFAULT false,
  send_estremi BOOLEAN NOT NULL DEFAULT false,
  estremi_sent BOOLEAN NOT NULL DEFAULT false,
  voucher_requested BOOLEAN NOT NULL DEFAULT false,
  use_voucher BOOLEAN NOT NULL DEFAULT false,
  whatsapp_thanks_sent_at TIMESTAMPTZ,
  whatsapp_thanks_last_error TEXT,
  no_whatsapp_message BOOLEAN NOT NULL DEFAULT false,
  cremation_registered BOOLEAN NOT NULL DEFAULT false,
  cremation_queued BOOLEAN NOT NULL DEFAULT false,
  ...
)
```

- **Destinazione V2**: colonne dirette su `practices`, stessa semantica di V1, solo tipizzate (`TEXT` "Si"/vuoto → `BOOLEAN`, date libere → `TIMESTAMPTZ`).
- **Trasformazione**: nessuna trasformazione concettuale — sono flag di processo 1:1 per pratica, nessuna normalizzazione necessaria.
- **Motivo architetturale**: nessuna incoerenza trovata in questi campi dall'audit — l'unico problema era l'omissione, non il design. Preservati as-is.
- **Migrazione**: copia diretta con coercizione booleana.
- **Verifica**: conteggio pratiche con ciascun flag attivo, V1 vs V2, deve coincidere esattamente.
- **Deprecazione futura**: nessuna prevista oggi.
- **Classificazione**: DECISIONE TECNICA, a basso rischio.

## F. Collaboratori — fatturazione interna vs documento fiscale

```
practices(
  ...
  collaborator_billing_status collaborator_billing_status_enum NOT NULL DEFAULT 'da_fatturare',  -- enum: 'da_fatturare' | 'fatturato'
  collaborator_billing_invoiced_at TIMESTAMPTZ,
  collaborator_name_fallback TEXT,   -- SOLO per le pratiche storiche che la migrazione non riesce a ricollegare a un collaborator_id reale
  ...
)
```

- **Destinazione V2**: `collaborator_billing_status`/`collaborator_billing_invoiced_at` restano **esplicitamente separati** da `invoices` — è un flag di processo interno ("questo collaboratore va pagato/è stato pagato per questa pratica"), non un documento fiscale. Tenerli fusi dentro `invoices` avrebbe ricreato esattamente la dualità fatture che il resto di questo documento elimina.
- **`collaborator_name`**: **non** portato avanti come colonna live in V2. **Motivo**: V1 stesso già risolve `collaborator_name` → `collaborator_id` quando possibile (app.py:1085, `UPDATE practices SET collaborator_id=? WHERE collaborator_name=? AND collaborator_id IS NULL`) — la migrazione applica la stessa risoluzione una volta per tutte in Fase B. `collaborator_name_fallback` resta **solo** come rete di sicurezza per le (eventuali) pratiche storiche che restano irrisolvibili anche dopo quel tentativo, **non eliminato "perché sembra vecchio"** ma degradato a caso residuale esplicito.
- **Migrazione**: passo 1, risolvere `collaborator_name`→`collaborator_id` con la stessa query già usata da V1; passo 2, per le righe non risolte, popolare `collaborator_name_fallback` col valore originale (**mai scartato**).
- **Verifica**: zero pratiche con `collaborator_id IS NULL AND collaborator_name_fallback IS NULL` se in V1 esisteva un `collaborator_name` valorizzato.
- **Deprecazione futura**: `collaborator_name_fallback` è **candidato a deprecazione** solo dopo aver verificato (query dedicata, sezione query di verifica) che sia sempre NULL su tutto il dataset migrato — non prima, e non per assunzione.
- **Classificazione**: DECISIONE TECNICA sulla forma; nessuna decisione aziendale necessaria (hai già dato la direzione esplicita: "non confondere il workflow interno con il documento fiscale").

## G. Notifica proprietario cremazione

```
practices(
  ...
  owner_notified_status owner_notified_status_enum NOT NULL DEFAULT 'da_avvisare',  -- enum: 'da_avvisare' | 'avvisato'
  owner_notified_at TIMESTAMPTZ,
  owner_notified_by BIGINT REFERENCES users(id),
  ...
)
```

- **Destinazione V2**: colonne dirette su `practices`, identiche nel significato a V1 — **stato corrente interrogabile direttamente** (query "quali pratiche sono da avvisare oggi" resta O(1) su indice, non una scansione di `audit_log`).
- **Trasformazione**: nessuna, solo tipizzazione ed enum invece di `TEXT` libero.
- **Motivo architetturale**: `audit_log` rappresenta lo **storico** (quando è cambiato, chi l'ha cambiato) — ogni transizione di questo stato scrive **anche** una riga in `audit_log` (per la regola di atomicità del doc 09), ma lo stato corrente resta una colonna diretta, non ricavata dal log. Le due cose non si sostituiscono a vicenda, come richiesto.
- **Migrazione**: copia diretta.
- **Verifica**: conteggio per stato, V1 vs V2, deve coincidere.
- **Deprecazione futura**: nessuna.

## H. `practice_line_items.subtype`

```
practice_line_items(
  ...
  subtype TEXT,   -- es. 'naso' | 'polpastrello' | 'zampa' per category='calco'
  ...
)
```

- **Destinazione V2**: aggiunta diretta al modello già presente in questo documento (era un'omissione, non una decisione).
- **Migrazione/Verifica**: copia diretta da `practice_items.subtype`, conteggio per (category, subtype) V1 vs V2.
- **Classificazione**: DECISIONE TECNICA, correzione di un'omissione, nessun impatto su decisioni già prese altrove.

## I. Veterinarian vouchers

```
veterinarian_vouchers(
  id BIGINT PK,
  veterinarian_id BIGINT NOT NULL REFERENCES veterinarians(id),
  practice_id BIGINT REFERENCES practices(id) ON DELETE SET NULL,   -- stesso pattern di invoices/payments: il buono resta anche se la pratica viene cancellata
  practice_number_snapshot TEXT,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  used_at TIMESTAMPTZ,
  note TEXT
)

practices(
  ...
  used_voucher_id BIGINT REFERENCES veterinarian_vouchers(id),   -- ora FK reale, in V1 non lo era
  ...
)
```

- **Destinazione V2**: tabella preservata con lo stesso ruolo, con FK reali dove in V1 mancavano.
- **Motivo architetturale**: stesso principio già applicato a `invoices`/`payments` — un buono è un record di valore economico/contabile, non deve sparire se la pratica collegata viene cancellata.
- **Migrazione**: copia diretta, con collegamento FK esplicito.
- **Verifica**: conteggio + verifica che ogni `used_voucher_id` su `practices` punti a un buono realmente esistente in V2 (oggi non garantito, vedi doc 13 §2.5).
- **Deprecazione futura**: nessuna.

## J. Disposal batches (smaltimento collettivo)

```
disposal_batches(
  id BIGINT PK,
  period_from DATE NOT NULL,
  period_to DATE NOT NULL,
  confirmed_at TIMESTAMPTZ,
  confirmed_by BIGINT REFERENCES users(id),
  total_count INTEGER NOT NULL,
  breakdown_json JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
disposal_batch_practices(
  id BIGINT PK,
  batch_id BIGINT NOT NULL REFERENCES disposal_batches(id) ON DELETE CASCADE,
  practice_id BIGINT NOT NULL REFERENCES practices(id)   -- FK esplicita, non dichiarata in V1
)
```

- **Destinazione V2**: tabelle preservate, con FK esplicite dove mancanti.
- **Motivo architetturale**: rilevanza per tracciabilità/storico dello smaltimento collettivo — stesso tipo di cautela già applicato al Gruppo B (potenziale rilevanza normativa).
- **🔶 VERIFICA NORMATIVA PENDENTE**: se esistano obblighi di conservazione specifici per questi lotti non è stato ancora verificato con un consulente/commercialista. **Decisione provvisoria confermata dall'utente: conservare indefinitamente**, dichiarata esplicitamente come scelta tecnica prudenziale, non come obbligo normativo accertato.
- **Migrazione/Verifica**: copia diretta, conteggio + verifica catena `disposal_batch_practices→practices` non orfana.
- **Deprecazione futura**: nessuna, per scelta prudenziale — soggetta a revisione se/quando arriva una verifica normativa reale.

## K. Ledger reversibility — storni

```
payment_deletions(              -- equivalente V2 di balance_movement_deletions, stesso ruolo
  id BIGINT PK,
  payment_id BIGINT,             -- riferimento informativo, NON FK (il pagamento originale può non esistere più)
  snapshot_json JSONB NOT NULL,  -- copia completa della riga payments prima della cancellazione/storno
  deletion_kind TEXT NOT NULL,
  deleted_by BIGINT REFERENCES users(id),
  deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  restored_at TIMESTAMPTZ,
  restored_by BIGINT REFERENCES users(id)
)
```

- **Destinazione V2**: tabella dedicata, mai omessa — è il meccanismo che rende gli storni realmente ripristinabili, esplicitamente il requisito fondamentale che hai richiamato.
- **Motivo architetturale**: preserva esattamente il pattern V1 (snapshot JSON pre-cancellazione), generalizzato al nuovo nome tabella `payments`. **Questo era il gap più grave trovato dall'audit — ora chiuso a livello di modello.**
- **Migrazione**: copia diretta da `balance_movement_deletions`.
- **Verifica**: conteggio + verifica che ogni riga abbia uno `snapshot_json` valido e parsabile.
- **Deprecazione futura**: nessuna, mai.
- **Classificazione**: DECISIONE TECNICA (presa, in diretta esecuzione della tua istruzione esplicita "NON eliminare questo concetto").

## L. Urn movements

```
urn_movements(
  id BIGINT PK,
  urn_id BIGINT NOT NULL REFERENCES urns(id),
  practice_id BIGINT REFERENCES practices(id) ON DELETE SET NULL,
  user_id BIGINT REFERENCES users(id),
  movement_type TEXT NOT NULL,
  quantity_delta INTEGER NOT NULL,
  old_quantity INTEGER NOT NULL,
  new_quantity INTEGER NOT NULL,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

- **Destinazione V2**: tabella preservata, stesso ruolo (log movimenti di magazzino), con FK esplicite dove mancanti in V1.
- **Migrazione/Verifica**: copia diretta, conteggio + verifica che la sequenza `old_quantity`→`new_quantity` per ciascuna urna resti internamente coerente (nessun salto non spiegato da un `quantity_delta`).
- **Deprecazione futura**: nessuna.

## M. Calendar zones

```
calendar_zones(id BIGINT PK, name TEXT NOT NULL, is_default BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now())
```

- **Destinazione V2**: aggiunta esplicitamente alla lista delle "tabelle che restano concettualmente invariate" (era un'omissione di elenco, non una decisione — l'audit l'aveva segnalato come rischio trascurabile ma comunque da correggere per completezza).

---

## N. Pagamenti — completamento colonne `payments`

```
payments(
  ...  -- struttura invariata rispetto a quanto già definito sopra
  source payment_source_enum NOT NULL DEFAULT 'native',   -- enum: 'native' | 'v1_migration' | 'api' | 'automatic'
  metadata_json JSONB,
  ...
)
```

- **Destinazione V2**: le 2 colonne mancanti (`source`, `metadata_json`, segnalate da doc 13 §2.4) aggiunte esplicitamente allo schema `payments` già definito in questo documento.
- **Motivo architetturale**: `source` rende distinguibile in modo permanente "questo pagamento esisteva già in V1 ed è stato migrato" da "questo è nato nativamente in V2" — proprietà preziosa proprio durante il periodo di verifica post-migrazione (doc 07/11), non solo un dettaglio storico. `metadata_json` preserva qualunque contesto non standardizzato che V1 aveva libertà di scrivere, senza dover anticipare oggi ogni possibile caso d'uso futuro.
- **Migrazione**: ogni riga migrata da `balance_movements` riceve `source='v1_migration'`; ogni riga creata dopo il cutover riceve `source='native'` (o `'api'`/`'automatic'` a seconda dell'origine).
- **Verifica**: conteggio `source='v1_migration'` in V2 = conteggio totale righe migrate da `balance_movements`.
- **Deprecazione futura**: nessuna.

## O. Fatture — riconciliazione importo fattura vs importo incassato

```
invoices(
  ...
  total_amount_cents BIGINT NOT NULL,   -- importo del documento fiscale, immutabile salvo correzione esplicita tracciata in audit_log
  ...
)
-- paid_total_cents, residual_cents, payment_status NON sono colonne memorizzate:
-- sono sempre calcolati a runtime da invoice_payment_links → payments, mai una seconda fonte
```

- **Regola esplicita (come richiesto)**: `invoices.total_amount_cents` rappresenta **l'importo del documento fiscale**, deciso al momento dell'emissione — **non** è la somma dei pagamenti collegati. `payments` rappresenta **quanto è stato effettivamente incassato**. I due importi **non devono essere uguali per definizione**, ma devono essere sempre riconciliabili con un unico calcolo, mai due fonti divergenti:
  - **Totale fattura** = `invoices.total_amount_cents` (valore fisso, del documento).
  - **Totale pagato** = `SUM(payments.amount_cents) attraverso invoice_payment_links` per quella fattura (calcolato live, mai memorizzato — stesso principio già applicato al "pagato" di pratica).
  - **Residuo** = Totale fattura − Totale pagato.
  - **Stato pagamento fattura** (calcolato, enum derivato): `non_pagata` (pagato=0), `parziale` (0 < pagato < fattura), `pagata` (pagato = fattura), `sovrapagata` (pagato > fattura).
  - **Esempio esplicito richiesto**: una fattura da €340 con €120 pagati resta sempre "fattura da €340, €120 pagati, €220 residui" — nessuna delle due cifre viene mai fatta collassare sull'altra.
- **Comportamento in caso di incoerenza (sovrapagamento)**: **mai corretto automaticamente in silenzio**. Lo stato `sovrapagata` è visivamente evidenziato nell'interfaccia e richiede intervento umano esplicito (storno di un pagamento in eccesso o correzione della fattura, entrambi tracciati in `audit_log`) — stessa disciplina già applicata ovunque in questo documento a ogni caso di incoerenza tra fonti.
- **Motivo architetturale**: chiude il gap trovato dall'audit (doc 13 §5) mantenendo il principio "mai una seconda fonte di verità" — `total_amount_cents` è l'unica fonte per "quanto è la fattura", il ledger `payments` è l'unica fonte per "quanto è stato pagato", e la riconciliazione fra i due è sempre un calcolo, mai un terzo valore memorizzato che potrebbe disallinearsi da entrambi.
- **Classificazione**: DECISIONE TECNICA (formalizza esattamente la regola di business che hai già dato esplicitamente in questo turno).

## P. Calendario — Riconsegna senza pratica collegata

```
calendar_events(
  ...
  preliminary_payment_status TEXT,     -- rinominato da payment_status: esplicitamente "preliminare", MAI letto quando linked_practice_id è valorizzato
  preliminary_payment_amount BIGINT,   -- rinominato da payment_amount, stessa logica
  ...
)
```

- **Destinazione V2**: **non eliminate** — rinominate per rendere esplicito nel nome stesso che sono un dato preliminare/pre-collegamento, mai la fonte di verità una volta che la Riconsegna è collegata a una pratica.
- **Regola di funzionamento**:
  1. Finché `linked_practice_id IS NULL`, `preliminary_payment_status`/`preliminary_payment_amount` sono liberamente modificabili dall'operatore e rappresentano una stima/accordo preliminare — esattamente il caso reale verificato nel codice V1 (doc 13 §1).
  2. Nel momento in cui l'evento viene collegato a una pratica (`linked_practice_id` diventa valorizzato), questi due campi diventano **congelati** (mai più scritti dall'interfaccia) — restano come **storico** di cosa era stato stimato prima del collegamento, non vengono cancellati.
  3. Da quel momento, l'unica fonte di verità per lo stato di pagamento della Riconsegna è la pratica collegata (`payments` + `practice_line_items`), come già definito nel resto di questo documento.
- **Regola di riconciliazione al momento del collegamento (nessun dato perso silenziosamente, come richiesto)**: se `preliminary_payment_amount` differisce dal totale calcolato sulla pratica a cui ci si sta per collegare, l'operatore vede un **avviso esplicito** prima di confermare il collegamento ("l'importo preliminare stimato su questo evento [€X] differisce dal totale della pratica [€Y] — confermi comunque il collegamento?") — mai un collegamento silenzioso che fa sparire la discrepanza. L'esito (confermato/annullato) viene scritto in `audit_log`.
- **Motivo architetturale**: risolve l'incoerenza reale trovata dall'audit senza eliminare una capacità operativa oggi effettivamente usata (prefill pagamento preliminare su Riconsegna, doc 03), rispettando al tempo stesso il principio "mai due fonti di verità per lo stesso importo" — i due campi non sono mai autoritativi contemporaneamente al ledger, sono sequenziali nel tempo (prima/dopo il collegamento), mai concorrenti.
- **Migrazione**: copia diretta `payment_status`→`preliminary_payment_status`, `payment_amount`→`preliminary_payment_amount`.
- **Verifica**: nessuna Riconsegna con `linked_practice_id` valorizzato deve mostrare in UI un valore diverso da quello calcolato dalla pratica collegata (test end-to-end dedicato).
- **Deprecazione futura**: nessuna.

---

**Stato di questo addendum**: chiude tutte le condizioni del doc 13 relative al modello dati (sezioni 2, 5, punto sulla riconciliazione calendario) che erano classificate come decisione tecnica. Le decisioni aziendali esplicitamente NON prese qui (retention DDT/trasporto, retention disposal_batches, regola di fallback provenienza) restano elencate nel report di chiusura Architecture Gate, non nascoste.

---

## Aggiornamento — decisioni aziendali chiuse dopo l'Architecture Gate (doc 15)

La sezione **C** sopra è stata riscritta integralmente: chiude la decisione aziendale sulla logica di provenienza/sede, sostituendo il precedente `origin_type` insufficiente con un modello esplicito di logistica multi-sede (affido, destinazione, cremazione, riconsegna). Aggiunta la relazione esplicita Ritiro → Pratica (`originating_pickup_event_id`). Le due voci di retention normativa (DDT/trasporto in Addendum B, disposal_batches in Addendum J) sono state riformulate come **VERIFICA NORMATIVA PENDENTE** con conservazione indefinita dichiarata esplicitamente come scelta tecnica prudenziale, non come obbligo normativo — su richiesta esplicita dell'utente, per non far passare una supposizione come un fatto accertato. Dettaglio completo delle decisioni e del loro stato in `docs/v2/15-decisioni-aziendali-aperte.md`.
