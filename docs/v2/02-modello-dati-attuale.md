# 02 — Modello Dati Attuale

> Estratto in modo autoritativo dallo schema live (`sqlite_master`) del database locale di sviluppo il 2026-08-24. Il database locale (`data/pet_paradise.db`) contiene **zero record aziendali** (0 pratiche, 0 clienti, 0 eventi calendario) — è una copia di sviluppo, non i dati di produzione. I dati reali vivono sul disco persistente di Render (`/var/data/pet_paradise.db`), non raggiungibile da questa sessione. Qualsiasi conteggio/verifica sui DATI (non sullo SCHEMA) andrà rifatto contro la produzione prima della migrazione.

## Sintesi

| Metrica | Valore |
|---|---|
| Tabelle | 50 |
| Indici | 49 |
| Trigger | 1 (`balance_movements_no_update`) |
| Colonne totali | 647 |
| Tabella più grande | `practices` — **163 colonne** |
| Relazioni dichiarate (FK reali) | 68 |
| Enforcement FK | **ON** nella connessione dell'app (`app.py:313`, `PRAGMA foreign_keys=ON`) — ma **OFF di default** per qualunque altro strumento/script che apra il file `.db` senza impostarlo esplicitamente (rischio concreto per script di migrazione/ispezione) |
| Journal mode | `delete` (rollback journal classico) — **non WAL**. Vedi `app.py:16167-16175`: commento che documenta un incidente reale già causato da questo (richieste bloccate a catena durante una chiamata WhatsApp lenta) |
| Backup automatico | **Non esiste** nel codice applicativo. L'unico backup trovato (`balance_legacy_repair.py:406`, `_create_backup()`) è invocato manualmente da uno script di riparazione una tantum, non da una routine schedulata. Il tipo di notifica `backup_completed` esiste nel vocabolario ma non risulta mai emesso da nessun job reale — solo nei test come placeholder generico |

## Il problema strutturale principale: `practices` come "entità Dio"

La tabella `practices` ha **163 colonne** (quasi 4 volte la seconda tabella più grande, `calendar_events` con 43). Contiene, tutte appiattite sullo stesso record:

- anagrafica proprietario (16 campi `owner_*`)
- **un secondo animale intero come colonne dedicate**: `animal2_name`, `animal2_species`, `animal2_breed`, `animal2_weight`, `animal2_microchip` — invece di una riga aggiuntiva in una tabella animali
- **14 colonne di "etichette" booleane separate**: `tag_assistita`, `tag_possibile_assistita`, `tag_assistita_streaming`, `tag_possibile_assistita_streaming`, `tag_saluto`, `tag_calco`, `tag_possibile_calco`, `tag_calco_urna`, `tag_calco_paw`, `tag_possibile_calco_paw`, `tag_calco_nose`, `tag_possibile_calco_nose`, `tag_avvisare`, `tag_da_richiamare` — invece di una tabella tag/junction
- **~30 campi di prezzo/voce preventivo** (`price_cremation`, `price_pickup`, `price_evening`, `price_urn`, `price_delivery`, `price_night`, `price_cast`, `price_holiday`, `price_accessories`, `price_paw_cast`/`_2`/`_3`/`_4`, `price_nose_cast`/`_2`/`_3`/`_4`, ecc.) — parzialmente sostituiti in tempi più recenti dalla tabella generica `practice_items` (urna/calco/accessorio), ma i vecchi campi restano tutti in tabella, popolati per le pratiche storiche
- **doppia fonte fattura**: `invoice_number`/`invoice_date`/`invoice_total`/`invoice_total_manual`/`make_invoice` sulla pratica stessa, **e** una tabella separata `movement_invoices` (fattura per movimento/circuito). Bug reale già riscontrato e corretto in produzione durante questa sessione: il form pratica leggeva solo la colonna legacy, la sezione Fatture leggeva entrambe le fonti — la fattura "spariva" dal form pur esistendo.
- **relazioni non dichiarate come FK**: `client_id`, `collaborator_id`, `veterinarian_id`, `owner_veterinarian_id`, `origin_veterinarian_id`, `used_voucher_id`, `urn_id`, `urn_id_2` sono tutte colonne che puntano concettualmente ad altre tabelle (clients, collaborators, veterinarians, urns) ma **nessuna ha una clausola `REFERENCES` nello schema** — solo `created_by`, `cremation_cycle_id`, `owner_notified_by` sono FK dichiarate su `practices`. Query e codice applicativo mantengono l'integrità "a mano", nulla la garantisce a livello di database.
- **doppio sistema di pagamento su due circuiti (W/D) modellato come coppie di colonne parallele** invece che come entità circuito: `deposit`/`deposit_final`, `remaining_balance`/`remaining_final`, `total_service`/`total_text` — la W è quella "senza suffisso", la D quella con `_final`/`_text`, una convenzione di naming non esplicita nello schema (va dedotta dal codice, vedi `uses_total_d()` in app.py).

Questa tabella, da sola, è la prova più diretta della descrizione del committente: "un insieme di correzioni successive". La cronologia di estensione è visibile persino nella DDL stessa — la definizione `CREATE TABLE practices` originale (prime ~12 colonne) è seguita da un lunghissimo blocco di colonne aggiunte via `ALTER TABLE ... ADD COLUMN` nel tempo, ancora visibile come suffisso concatenato nello schema dump (`sqlite_master.sql` riporta la history di ALTER come un'unica stringa CREATE con la coda aggiunta).

Lo stesso pattern di "colonne aggiunte via ALTER nel tempo" (visibile come coda `, colonna TEXT, colonna2 TEXT...` dopo la definizione originale) si osserva anche su: `veterinarians`, `collaboratori`, `clients`, `urns`, `cremation_cycles`, `users`, `balance_movement_deletions`, `whatsapp_messages`, `calendar_events`.

## Elenco tabelle per dominio

### Identità e accesso
| Tabella | Colonne | Scopo |
|---|---|---|
| `users` | 7 | Operatori/admin. `role` (`admin`/`operator`), `password_hash`, `must_change_password` |
| `sessions` | 3 | Token di sessione → `user_id`. Nessuna scadenza esplicita nello schema (verificare in codice se il token viene mai invalidato/scaduto) |
| `user_preferences` | 3 | Coppie chiave/valore per utente (tema, ordine sidebar, ordine sezioni dashboard, colonne archivio collassate) |
| `notification_preferences` | 3 | Opt-out per tipo di notifica, per utente |

### Anagrafiche
| Tabella | Colonne | Scopo |
|---|---|---|
| `clients` | 18 | Anagrafica clienti riutilizzabile (proprietari) |
| `veterinarians` | 17 | Cliniche/ambulatori veterinari, con geocoding (`lat`/`lng`, `google_place_id`) e orari (`veterinarian_hours`, tabella separata, 9 colonne, un giorno della settimana per riga) |
| `veterinarian_vouchers` | 7 | Buoni maturati per veterinario, `UNIQUE(practice_id)` — un buono al massimo per pratica |
| `collaborators` | 16 | Collaboratori esterni (es. canili/rifugi convenzionati) |
| `collaborator_price_tiers` | 7 | Scaglioni di prezzo per peso, per collaboratore |
| `company_locations` | 8 | Sedi aziendali (punti di partenza/arrivo percorsi) |
| `urns` | 13 | Catalogo urne/prodotti (`category` distingue Urna da altro) |
| `articles` | 4 | Catalogo articoli ordinabili (es. materiali di consumo) |

### Nucleo operativo (pratiche)
| Tabella | Colonne | Scopo |
|---|---|---|
| `practices` | **163** | Entità centrale — vedi sopra |
| `practice_items` | 10 | Righe generiche urna/calco/accessorio per pratica (sostituto più recente dei campi `price_*` fissi) |
| `practice_history` | 8 | Log eventi testuali per pratica (old_value/new_value liberi, non strutturati) |
| `disposal_batches` / `disposal_batch_practices` | 7 / 3 | Lotti di smaltimento collettivo e pratiche incluse |

### Calendario operativo
| Tabella | Colonne | Scopo |
|---|---|---|
| `calendar_events` | 43 | Ritiri, Riconsegne, Appuntamenti — un'unica tabella polimorfica per `event_type`, con colonne che hanno senso solo per alcuni tipi (es. `payment_status`/`payment_amount` solo per Riconsegna, `location_type` solo per Ritiro) |
| `calendar_event_animals` | 9 | Animali di un evento Ritiro (N per evento) |
| `calendar_event_estimate_items` | 7 | Voci di preventivo libere per evento |
| `calendar_event_comments` | 8 | Commenti (soft-delete con `deleted_at`/`deleted_by`) |
| `calendar_event_history` | 7 | Audit generico per evento |
| `calendar_event_notifications` | 7 | Promemoria/notifiche schedulate per evento, con `UNIQUE(event_id,notification_type,scheduled_at)` |
| `calendar_zones` | 4 | Elenco zone note (autocomplete) |

### Cremazione
| Tabella | Colonne | Scopo |
|---|---|---|
| `cremation_cycles` | 10 | Ciclo di cremazione: `status` CHECK IN (`pianificato`,`in_attesa`,`completato`) |
| `urn_movements` | 10 | Log movimenti di magazzino urne (entrata/uscita, quantità prima/dopo) |

### Denaro
| Tabella | Colonne | Scopo |
|---|---|---|
| `balance_movements` | 18 | **Ledger append-only** (Bilanci) — CHECK su `category` (`W`/`D`/`Collaboratori`), `ledger_section` (`Entrata`/`Uscita`), importo in **centesimi interi**, `idempotency_key` UNIQUE, storni tramite `related_movement_id` invece di update. **Trigger `balance_movements_no_update` blocca ogni UPDATE** — pattern corretto e da preservare in V2 |
| `payment_movements` | 12 | Tabella "di dettaglio" più vecchia, **parallela** a `balance_movements` — può contenere righe legacy/orfane non più rappresentative degli incassi reali (commento nel codice applicativo lo conferma esplicitamente) |
| `movement_invoices` | 9 | Fatture per movimento/circuito (fonte "nuova") |
| `movement_invoice_links` | 3 | N:N fattura ↔ movimenti pagamento |
| `balance_movement_deletions` | 16 | Log di cancellazioni/storni sul ledger, con snapshot JSON per eventuale ripristino |

### Logistica
| Tabella | Colonne | Scopo |
|---|---|---|
| `route_plans` | 23 | Piano percorso giornaliero per operatore, con esito ottimizzazione |
| `route_plan_stops` | 17 | Tappe del piano, collegate a `calendar_events` |
| `geocode_cache` | 4 | Cache indirizzo → lat/lng (chiave primaria = indirizzo testuale) |

### Turni
| Tabella | Colonne | Scopo |
|---|---|---|
| `shifts` | 11 | Turno per operatore/data/sede, `UNIQUE(operator_name,work_date)` |
| `shift_vacations` | 8 | Assenze/ferie per intervallo date |
| `shift_oncall` | 7 | Reperibilità settimanale, `UNIQUE(week_start)` |

### Notifiche e messaggistica
| Tabella | Colonne | Scopo |
|---|---|---|
| `notifications` | 13 | Storico notifiche in-app, con raggruppamento (`group_count`) |
| `notification_group_items` | 6 | Dettaglio dei singoli eventi raggruppati sotto una notifica |
| `notification_delivery_log` | 6 | Esito invio push per sottoscrizione |
| `push_subscriptions` | 11 | Sottoscrizioni Web Push per utente/dispositivo |
| `scheduled_notification_events` | 2 | Deduplicazione idempotente di eventi schedulati (solo chiave + timestamp) |
| `reminders` | 11 | Promemoria operativi con `dedupe_key` UNIQUE, snooze (`snoozed_until`) |
| `whatsapp_messages` | 21 | Coda invii WhatsApp con stato/tentativi/errori |
| `whatsapp_inbound_messages` | 10 | Messaggi WhatsApp ricevuti |
| `whatsapp_cron_runs` | 6 | Log di ogni esecuzione del cron (ogni 5 minuti) |
| `email_orders` | 16 | Ordini via email (es. materiali/acqua), con stato e retry (`attempt_count`) |
| `article_orders` | 4 | Ordini articoli catalogo |

### Configurazione
| Tabella | Colonne | Scopo |
|---|---|---|
| `settings` | 2 | Chiave/valore globali applicazione |

## Vincoli e pattern degni di nota (da preservare in V2)

- **Ledger append-only con trigger di blocco UPDATE** (`balance_movements`) — corretto, preservare il principio (mai mutare un movimento economico, sempre stornare con una riga collegata).
- **Idempotenza esplicita**: `balance_movements.idempotency_key` UNIQUE, `scheduled_notification_events.event_key` PRIMARY KEY, `whatsapp_inbound_messages.wa_message_id` UNIQUE (parziale, solo se non vuoto) — pattern già presente e da mantenere per evitare doppi invii/doppie registrazioni.
- **Soft delete diffuso** via colonna `deleted_at` (+`deleted_by`) su `practices`, `calendar_events`, `calendar_event_comments` — MA non su tutte le tabelle che forse dovrebbero averlo (es. `clients`, `veterinarians`, `collaborators` hanno solo `active` booleano, non uno storico di quando sono stati disattivati).
- **Un solo trigger in tutto il database** (`balance_movements_no_update`) — nessun'altra regola di integrità è imposta a livello DB; tutto il resto (validazioni, stati consistenti, cascata logica) vive nel codice Python applicativo, non nello schema.

## Enum e vocabolari (valori validi impliciti, non CHECK a livello DB salvo dove indicato)

Questi insiemi di valori sono imposti solo in Python (costanti), non da `CHECK` nello schema (eccetto dove segnalato):

- `practices.status`: stati pratica (`STATES` in app.py)
- `practices.payment_status` / `calendar_events.payment_status`: **due vocabolari diversi per lo stesso concetto** — le pratiche usano `PAYMENT_STATES` (Da saldare/Acconto/Pagato), gli eventi calendario Riconsegna usano `PAYMENT_STATUSES` (Da pagare/Da saldare/Pagato) — un disallineamento terminologico già noto
- `calendar_events.event_status`: due vocabolari diversi a seconda di `event_type` — `PICKUP_STATUSES` (Da confermare/Da ritirare/Ritirato/Annullato) per i Ritiri, `DELIVERY_STATUSES` (In programma/Completato) per le Riconsegne, sulla STESSA colonna
- `cremation_cycles.status`: **questo uno È vincolato da CHECK** (`pianificato`/`in_attesa`/`completato`)
- `balance_movements.category`/`ledger_section`: **vincolati da CHECK**

Questo mix (alcuni vocabolari protetti da CHECK, la maggioranza no, e con lo stesso nome-colonna che cambia significato/dominio a seconda del tipo di riga) è uno dei punti su cui la V2 deve decidere esplicitamente (vedi documento "Stati e Workflow").
