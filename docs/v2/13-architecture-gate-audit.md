# 13 — Architecture Gate Audit (docs 01-12)

> Richiesto esplicitamente prima della Fase 4. Copre: coerenza incrociata dell'intera progettazione 01-12, un audit di preservazione dati colonna-per-colonna partendo dallo schema V1 reale (ri-estratto live in questa sessione, non copiato da doc02), classificazione dei dati ambigui, verifica del backup, e un verdetto GO/GO-WITH-CONDITIONS/NO-GO esplicito e non automatico. Etichettatura FACT/DECISION/ASSUMPTION/RISK per ogni affermazione, come richiesto.

## 0. Metodo e limiti dichiarati di questo audit

- **FACT**: lo schema V1 è stato ri-estratto live in questa sessione (`PRAGMA table_info` su tutte le 50 tabelle) — combacia esattamente con `02-modello-dati-attuale.md` (50 tabelle, `practices` 163 colonne, `calendar_events` 43 colonne). Nessun drift tra doc02 e lo schema reale.
- **FACT**: per ~30 colonne non ancora esplicitamente mappate ho verificato l'uso reale nel codice (`grep` mirato in `app.py`, non solo il nome colonna) prima di classificarle — non sono supposizioni sul nome.
- **LIMITE dichiarato, non aggirabile da questa sessione**: il database locale contiene **zero righe aziendali** (FACT, già in doc02). La sezione 4 (dati ambigui) può quindi classificare i **tipi** di anomalia e proporre una regola di trattamento, ma **non può fornire conteggi reali** — quelli richiedono l'esecuzione delle query già pronte (doc05) contro un backup di produzione. Questo è dichiarato esplicitamente anche nel verdetto finale, non nascosto.

---

## 1. Coerenza architetturale incrociata

| Coppia | Esito | Dettaglio |
|---|---|---|
| **Modello dati V2 ↔ Backend** | ✅ Coerente | doc09 (layer domain/repositories) referenzia esattamente le entità di doc06 (`practices`, `payments`, `invoices`, `animals`, `tags`). Nessuna entità inventata in doc09 che non esista in doc06. |
| **Backend ↔ API** | ✅ Coerente | Le route in doc09 sono dichiarate "solo orchestrazione", nessuna logica propria — coerente con lo strato `domain/`+`services/`. |
| **API ↔ Frontend** | ✅ Coerente per meccanismo | doc10 si affida ai tipi generati da OpenAPI (doc09/FastAPI) — nessun tipo scritto a mano in duplicato. **RISK non ancora indirizzato**: nessun documento specifica ancora una politica di **versionamento API** (es. `/api/v1/...`) — se il frontend e il backend non vengono sempre deployati insieme, un cambio di contratto rompe silenziosamente il client finché non rigenera i tipi. Da aggiungere prima della Fase 8 (integrazione frontend), non bloccante per la Fase 4. |
| **Stati ↔ Macchine a stati** | 🟡 **Parzialmente coerente — gap reale** | I *valori* enum (doc06) combaciano con i vocabolari V1 (`practice_status`, `pickup_status`, `cremation_cycle_status`). **Ma**: doc09 dichiara che "ogni macchina a stati ha una tabella esplicita delle transizioni permesse" — **questa tabella non esiste ancora in nessun documento**. Oggi in V1 (doc03, FACT) qualunque transizione è ammessa senza vincoli («chiunque autenticato può far regredire una pratica da Consegnato a Ritirato»). Decidere il *meccanismo* (FSM esplicita) senza aver ancora deciso il *grafo* delle transizioni approvate è un vuoto che blocca l'implementazione reale del dominio Pratica/Ritiro/Riconsegna/Ciclo — **non blocca Clienti/Veterinari** (Fase 4), che non hanno macchine a stati. |
| **Autorizzazioni ↔ Ruoli** | 🟡 Gap minore | doc09 centralizza `require_role()` — risolve il problema di doc01 (~20 controlli sparsi). **Non ancora indirizzato**: doc03 segnala che V1 non ha UI di gestione utenti né disattivazione — nessun documento V2 (06-12) tratta esplicitamente questo come funzionalità da costruire (solo menzionato di striscio in doc09 come cartella `auth/`). Da rendere esplicito prima della fase che tocca Utenti, non prima della Fase 4. |
| **Ledger ↔ Pagamenti** | ✅ Coerente | doc06 risolve l'ambiguità W/D alla radice (channel esplicito per riga, "pagato" sempre `SUM(ledger)`). Nessuna incoerenza trovata. |
| **Fatture ↔ Pratiche** | 🟡 Coerente sulla cancellazione, **gap nuovo trovato sulla riconciliazione importi** — vedi sezione 5. |
| **Notifiche ↔ Eventi** | ✅ Coerente | `notifications` (feed utente) e `audit_log` (storico tecnico) sono concettualmente distinti, nessuna sovrapposizione — chiarito esplicitamente qui perché nessun documento lo diceva in modo esplicito finora. |
| **Calendario ↔ Ritiri/Riconsegne** | 🔴 **Incoerenza reale trovata** | doc07 (Fase B, passo 9) dichiara che `calendar_events` viene migrata "senza le colonne `payment_status`/`payment_amount`, ora sempre calcolate dal lato pratica collegata". **Verificato nel codice (app.py:9986, 11284, 17816)**: un evento Riconsegna **può esistere senza `linked_practice_id` valorizzato** (il codice controlla esplicitamente `event["linked_practice_id"]` prima di leggere il dato "live" dalla pratica, con fallback ai campi propri dell'evento). Se in V2 quelle due colonne vengono davvero eliminate senza un piano alternativo, **una Riconsegna non ancora collegata a una pratica perderebbe la possibilità di mostrare/registrare un importo/stato di pagamento preliminare** — funzionalità oggi realmente usata (prefill pagamento da pratica esistente, doc03). Non è un dettaglio: va deciso esplicitamente PRIMA di implementare il dominio Calendario (Fase 5), non assunto per default. |
| **Audit log ↔ Operazioni sensibili** | 🟡 Gap di specifica | doc09 dice che ogni transizione "dichiara side-effect eseguiti dal service layer" incluso l'audit — ma nessun documento garantisce esplicitamente che la scrittura in `audit_log` avvenga **nella stessa transazione DB** dell'operazione che descrive. Se non lo è, si può avere un cambio di stato committato senza la relativa riga di audit (esattamente il tipo di incoerenza che l'intero progetto V2 vuole eliminare). **Raccomandazione**: aggiungere a doc09 una riga esplicita: "ogni scrittura di dominio e il relativo audit_log avvengono nella stessa transazione, mai in transazioni separate". Correzione a costo zero, va fatta prima della Fase 4 (è una regola trasversale, si applica anche a Clienti/Veterinari). |

---

## 2. Data Preservation Audit — matrice V1 → V2

### 2.1 `practices` (163 colonne) — il caso più importante

**Risultato sintetico**: delle 163 colonne, **circa 74 (45%)** non hanno una destinazione V2 esplicitamente scritta in doc06 (14 hanno una destinazione "ragionevolmente implicita ma mai dichiarata per iscritto", 4 sono già segnalate come ASSUMPTION aperta da doc06 stesso, **56 non sono menzionate in nessun modo**). Questo NON significa che l'informazione sia persa per forza — significa che **nessuno ha ancora deciso dove va**, il che è esattamente il rischio che questo gate doveva far emergere.

#### Colonne già mappate correttamente (confermato, nessuna azione)

| V1 | V2 | Automatizzabile | Rischio |
|---|---|---|---|
| id, practice_number, status, request_origin, destination_branch, service_type, notes, data_complete | `practices` (stessi campi, tipizzati) | Sì | Basso |
| created_at, updated_at, created_by | `practices` (standardizzati) | Sì | Basso |
| deleted_at, deleted_by | `practices` (soft-delete preservato) | Sì | Basso |
| ddt_number, ddt_date, ddt_pdf | `practices.ddt_number/ddt_date/ddt_pdf_path` | Sì | Basso |
| signature_data | `practices.signature_data` | Sì | Basso |
| pickup_date, pickup_time, pickup_address, microchip | `practices` (stessi campi) | Sì | Basso |
| client_id, collaborator_id, veterinarian_id, origin_veterinarian_id, owner_veterinarian_id | `practices.*` ora con FK reale | Sì (per le pratiche già collegate) | Vedi 2.5 per quelle NON collegate |
| cremation_cycle_id | `practices.cremation_cycle_id` | Sì | Basso |
| animal_name, species, breed, age_years, age_months, estimated_weight, animal2_* (5 col.) | `animals` (righe multiple) | Sì | Basso — trasformazione 1→N ben definita |
| tag_* (14 colonne) | `tags` + `practice_tags` | Sì | Basso |
| price_* (20 colonne) | `practice_line_items` | Sì | Basso, vedi 2.1.2 per i descrittori collegati |
| urn_id, urn_id_2 | `practice_line_items` (category='urna') | Sì | Basso |
| invoice_number, invoice_date, invoice_total | `invoices` | Sì (con dedup, vedi doc07) | Medio, vedi sezione 5 |
| deposit, deposit_final, remaining_balance, remaining_final, total_service, total_text, payment_status, payment_amount | Calcolati da `payments`+`practice_line_items`, mai memorizzati | Sì | Vedi sezione 4 (invariante W/D) |

#### 2.1.1 — GRUPPO 1: anagrafica proprietario inline (16 colonne) — ⚠️ NON MAPPATO

`owner_first_name, owner_last_name, owner_phone, owner_phone_2, owner_email, owner_tax_code, owner_address, owner_phone_note, owner_company, owner_vat, owner_notes, owner_street, owner_city, owner_province, owner_zip, owner_sdi`

- **FACT (verificato nel codice, app.py:3346)**: questi campi sono un **form completo e attivamente compilabile**, sincronizzato ma non identico a `clients` — un operatore può cercare un cliente esistente E comunque modificare/sovrascrivere questi campi per quella specifica pratica.
- **Il problema architetturale reale**: doc06 rende `client_id` obbligatorio e presume implicitamente che l'anagrafica proprietario "sparisca" dentro `clients`. Ma se `owner_address`/`owner_phone` ecc. su una pratica storica **differiscono** da quanto oggi risulta su `clients.address`/`clients.phone` (perché il cliente ha traslocato, cambiato numero, ecc. DOPO quella pratica), **collassare tutto su `client_id` senza portare uno snapshot perde l'informazione "qual era l'indirizzo/telefono al momento di QUESTA pratica"** — un dato potenzialmente rilevante per DDT/fatturazione storica.
- **Classificazione**: **NON automatizzabile senza decisione esplicita**. Due strade, entrambe valide, nessuna scelta ancora fatta:
  - (A) Aggiungere a `practices` uno snapshot immutabile minimale (`owner_snapshot_name`, `owner_snapshot_address`, `owner_snapshot_phone` — testo libero, scritto una volta alla creazione, mai più letto per i calcoli, solo per riferimento storico/stampa DDT);
  - (B) Accettare che l'anagrafica-al-momento-della-pratica venga persa e valga solo lo stato attuale del cliente collegato (più semplice, ma è una **perdita di informazione storica reale**, da dichiarare esplicitamente come tale — non da fare passare inosservata).
- **RISCHIO se non deciso**: la migrazione (doc07, passo 2 "deduplicazione clienti") oggi presume implicitamente la strada (B) senza dirlo. Questo è precisamente il tipo di trasformazione che il punto 3 della tua richiesta vieta se non "verificata prima" — va decisa esplicitamente da te, non assunta da me.

#### 2.1.2 — GRUPPO 2: documentazione DDT/trasporto (14 colonne) — ⚠️ NON MAPPATO, rischio più alto del gruppo 1

`transport_method, vehicle_plate, temperature_mode, package_count, container_id, lot_number, treatment_method, delivery_at_clinic, delivery_at_home, identity_document_number, identity_document_date, signing_place, ddt_share_token, original_practice_number`

- **FACT**: nessuna di queste compare nel modello `practices` V2 di doc06 — solo `ddt_number/ddt_date/ddt_pdf_path/signature_data` sono presenti.
- **Perché è più grave del gruppo 1**: `transport_method`, `vehicle_plate`, `temperature_mode`, `package_count`, `container_id`, `lot_number`, `treatment_method` hanno tutta l'aria di **dati di tracciabilità del trasporto di sottoprodotti animali** — un ambito tipicamente soggetto a obblighi normativi in Italia (tracciabilità SISTRI/formulario), non semplici "note operative". `identity_document_number/date` + `signing_place` sono dati del documento d'identità di chi firma il ritiro/consegna — potenzialmente dati personali soggetti a normativa privacy, con un proprio ciclo di conservazione. **Perderli non è solo un problema di UX, potrebbe essere un problema di conformità.**
- **`ddt_share_token`** (con indice `UNIQUE`, FACT verificato): token per link di condivisione pubblico del PDF DDT — un dato di sicurezza, non solo un dettaglio. Se non riportato, ogni link di condivisione DDT già distribuito ai clienti smetterebbe di funzionare dopo il cutover, senza preavviso.
- **`original_practice_number`** (FACT, uso confermato in doc03): usato quando una pratica viene cestinata (il numero pratica reale viene spostato qui e sostituito da un placeholder) — **necessario per un ripristino corretto dal Cestino**. Ometterlo rompe silenziosamente il flusso di ripristino per le pratiche migrate che erano già nel cestino al momento della migrazione.
- **Classificazione**: **C — richiede intervento umano** (decisione esplicita su quali di questi campi sono obblighi normativi da preservare 1:1 in `practices` V2, quali sono davvero superflui). Non decidibile da un'assunzione tecnica.

#### 2.1.3 — GRUPPO 3: modalità origine/provenienza (7 colonne) — parzialmente segnalato, ora completo

`pickup_address_mode, transporter_mode, origin_mode, origin_text, provenance, origin_first_name, origin_last_name`

- doc06 aveva già segnalato **4 di queste** (`pickup_address_mode/origin_mode/origin_text/transporter_mode`) come ASSUMPTION aperta. **`provenance`, `origin_first_name`, `origin_last_name` non erano nemmeno menzionate** — verificate ora nel codice (app.py:2995-2996, 4191, 12082): `provenance` alimenta un badge colorato mostrato nelle card cremazione ("da dove viene questo animale"), campo attivamente usato per la UI operativa, non un residuo morto.
- **Classificazione**: **B — riconciliabile con regola esplicita**, una volta che tu chiarisci la logica di fallback IDEM-SPED/testo-libero/veterinario che oggi vive solo nel codice (come già segnalato da doc06).

#### 2.1.4 — GRUPPO 4: flag di override manuale fatturazione (3 colonne) — ⚠️ NON MAPPATO

`invoice_total_manual, make_invoice, total_service_manual`

- **FACT (app.py:3155-3182, 7459)**: non sono dati derivati, sono **decisioni esplicite dell'operatore** ("questo totale è stato corretto a mano, non ricalcolare automaticamente" / "questa pratica deve essere fatturata"). Sono metadati di intento, non solo numeri.
- **Rischio**: se persi, al primo ricalcolo automatico in V2 una pratica storica con importo corretto manualmente potrebbe essere silenziosamente "ricalcolata" sovrascrivendo una correzione intenzionale dell'operatore — esattamente il tipo di perdita silenziosa che il progetto vuole evitare.
- **Classificazione**: **A — riconciliabile automaticamente**, ma solo se si decide dove vivono in V2 (probabile candidato: un flag `is_manual_override` su `practice_line_items`/`invoices`). Oggi non hanno destinazione.

#### 2.1.5 — GRUPPO 5: milestone di pagamento (3 colonne) — ⚠️ NON MAPPATO

`deposit_paid_at, paid_at, payment_method` (a livello pratica, distinto da `payments.payment_method` per singolo movimento)

- **FACT rilevante (app.py:9478-9508)**: questi campi fanno parte di uno **snapshot esplicito usato per il ripristino (`revert`) di un cambio di stato pagamento** — non sono semplice "quando è successo", sono usati per tornare indietro correttamente. Con il nuovo modello (V2: "pagato" sempre `SUM(payments)`), la data del saldo è già ricavabile da `payments.movement_date`/`created_at` **per le pratiche future** — ma per la migrazione delle pratiche storiche va verificato che `payments.movement_date` migrato coincida davvero con questi timestamp, non assunto.
- **Classificazione**: **A — automatizzabile**, ma la migrazione deve includere un controllo esplicito di coerenza (`deposit_paid_at`/`paid_at` storico vs `MIN(payments.movement_date)` per canale, per la stessa pratica) prima di considerarli "equivalenti e quindi eliminabili".

#### 2.1.6 — GRUPPO 6: flag di workflow operativo (11 colonne) — ⚠️ NON MAPPATO

`send_catalog, catalog_sent, send_estremi, estremi_sent, voucher_requested, use_voucher, whatsapp_thanks_sent_at, whatsapp_thanks_last_error, no_whatsapp_message, cremation_registered, cremation_queued`

- **FACT**: tutti attivamente letti/scritti nel codice attuale (non colonne morte) — `send_estremi`/`estremi_sent` alimentano direttamente il promemoria "Inviare estremi" già visto nella card Dashboard di questa sessione; `whatsapp_thanks_*` traccia lo stato del messaggio di ringraziamento automatico (side-effect dichiarato in doc03); `voucher_requested/use_voucher` guida il flusso buono veterinario.
- **Classificazione**: **A — automatizzabile** una volta scelta la destinazione (candidati ovvi: colonne dirette su `practices` V2 per i flag booleani semplici, oppure un piccolo blocco "stato workflow" — nessuna trasformazione concettuale complessa, è puro lavoro di schema dimenticato in doc06).

#### 2.1.7 — GRUPPO 7: fatturazione collaboratori (2 colonne) — ⚠️ NON MAPPATO

`billing_status, billing_invoiced_at`

- **FACT (app.py:15387-15416)**: workflow **distinto** dalla fattura vera e propria (`invoices`) — usato specificamente per marcare "Da fatturare"/"Fatturato" sulle pratiche con `request_origin='Collaboratore'`, indipendentemente dall'esistenza di un record fattura reale. **Se collassato dentro `invoices` senza attenzione, ricrea esattamente la dualità fatture che la Fase 06 voleva eliminare** — è un caso in cui "unificare tutto sotto invoices" può essere sbagliato, perché concettualmente sono due cose diverse (un flag di processo interno vs un documento fiscale).
- **Classificazione**: **C — richiede intervento umano** (decisione: resta un flag di processo separato su `practices`, o si forza dentro il modello fatture con un tipo speciale?).

#### 2.1.8 — GRUPPO 8: notifica proprietario cremazione (3 colonne) — ⚠️ NON MAPPATO

`owner_notified_status, owner_notified_at, owner_notified_by`

- **FACT (app.py:13029-13305)**: stato corrente interrogabile (badge UI "da avvisare"/"avvisato"), non solo log storico — se collassato semplicemente dentro `audit_log` (che è un log di eventi, non un campo di stato corrente interrogabile efficientemente), si perde la possibilità di fare la query "quali pratiche sono da avvisare oggi" senza una scansione dei log.
- **Classificazione**: **A — automatizzabile**, destinazione naturale: colonne dirette equivalenti su `practices` V2 (stato + timestamp + chi), **in aggiunta** a (non in sostituzione di) la riga in `audit_log` per lo storico.

#### 2.1.9 — GRUPPO 9: descrittori accoppiati alle colonne prezzo (14 colonne) — implicito, da rendere esplicito

`urn_notes, urn_notes_2, accessory_type, accessory_type_2, accessory_detail, accessory_detail_2, nose_cast_type, nose_cast_type_2/3/4, paw_cast_type, paw_cast_type_2/3/4`

- Inferenza ragionevole: confluiscono nel campo `description` di `practice_line_items` insieme al relativo `price_*`. **Non dichiarato esplicitamente in doc06** — rischio basso (la trasformazione è ovvia), ma va scritto nel DDL finale invece di lasciato "implicito", per evitare che chi implementa la Fase 5 debba reinterpretare la logica da zero leggendo il codice V1.
- **Classificazione**: **A — automatizzabile**, azione richiesta: solo documentare esplicitamente, non una vera decisione aperta.

#### 2.1.10 — GRUPPO 10: altro (1 colonna)

`collaborator_name` — **FACT (10+ punti d'uso, es. app.py:7891-7892, 12350)**: override di visualizzazione testuale del nome collaboratore, usato come fallback quando `collaborator_id` non è ancora collegato o per pratiche storiche con nome libero. **Classificazione B** — riconciliabile con regola esplicita (se `collaborator_id` è valorizzato usa quello, altrimenti serve un posto per il testo libero storico — stesso pattern del Gruppo 1 owner).

### 2.2 Tabelle collegate a `practices` — verifica presenza in doc06

| Tabella V1 | Presente in doc06? | Nota |
|---|---|---|
| `practice_items` | Sì → `practice_line_items` | **Gap trovato**: la colonna `subtype` (FACT, uso reale confermato app.py:7103-17280 — distingue es. "naso"/"polpastrello"/"zampa" dentro la categoria "calco") **non compare** nello schema V2 di `practice_line_items` in doc06. Perdita di granularità se non aggiunta. |
| `practice_history` | Sì → `audit_log` | Coerente |
| `veterinarian_vouchers` | **NO — non menzionata in nessun punto di doc06** | Tabella intera (7 colonne) omessa, insieme alle 3 colonne collegate su `practices` (`voucher_requested`, `use_voucher`, `used_voucher_id`, già segnalate al Gruppo 6). L'intera funzionalità "buono veterinario" non ha ancora un modello V2. |
| `disposal_batches` / `disposal_batch_practices` | **NO — non menzionate** | Lotti di smaltimento collettivo — dato potenzialmente rilevante ai fini di tracciabilità/compliance (stesso tipo di rischio del Gruppo 2). Tabelle intere omesse. |
| `balance_movement_deletions` | **NO — non menzionata** | **Il gap più grave di questa sezione**: è il meccanismo che oggi rende gli storni sul ledger davvero verificabili/ripristinabili (snapshot JSON prima della cancellazione). Ometterlo in V2 indebolirebbe proprio il principio "ogni cambiamento ai dati deve essere reversibile" che è la regola assoluta dichiarata da te fin dall'inizio del progetto. |
| `urn_movements` | **NO — non menzionata** (né nella lista "invariate" né altrove) | Log movimenti di magazzino urne — rischio medio-basso (ricostruibile in parte da altre fonti, ma è comunque uno storico che andrebbe preservato esplicitamente, non per omissione). |
| `calendar_zones` | **NO — non menzionata** | Rischio trascurabile (tabella di autocomplete), ma per completezza va comunque elencata esplicitamente in doc06 tra le "invariate", non lasciata fuori per sottinteso. |

### 2.3 `calendar_events` e figlie

Confermato in doc06 come dominio "senza redesign strutturale" salvo `animals` unificata (già ben progettata, vedi doc06 §"tabelle che restano concettualmente invariate"). **Unico problema reale**: la rimozione di `payment_status`/`payment_amount` annunciata in doc07 senza aver risolto il caso "Riconsegna non ancora collegata a una pratica" — vedi sezione 1, riga "Calendario ↔ Ritiri/Riconsegne". Tutte le altre tabelle figlie (`calendar_event_animals`, `calendar_event_comments`, `calendar_event_estimate_items`, `calendar_event_history`→`audit_log`, `calendar_event_notifications`) hanno percorso di migrazione chiaro.

### 2.4 Ledger e fatture — dettaglio colonne

`balance_movements` → `payments` (doc06): confrontando colonna per colonna, **2 colonne V1 non compaiono nella struttura V2**: `source` (origine del movimento — manuale/migrazione/API, utile proprio in fase di migrazione per distinguere "questo pagamento esisteva già in V1" da "questo è stato creato dalla migrazione stessa") e `metadata_json` (blob JSON libero — può contenere contesto non standardizzato ma potenzialmente importante per movimenti specifici, es. dettagli di uno storno). **Classificazione A** (automatizzabile) ma da aggiungere esplicitamente allo schema `payments`, non ometterli per "pulizia".

`movement_invoices`/`movement_invoice_links` → `invoices`/`invoice_payment_links`: mappatura 1:1 pulita, nessun problema trovato.

`payment_movements` → *(eliminata)*: doc06 dichiara esplicitamente che i "residui non affidabili" non vengono migrati come fonte di verità, solo verificati e riconciliati contro il ledger. **Coerente con quanto il codice V1 stesso dichiara** (commento esplicito "mai usarla per i totali", doc02). Nessun problema — ma la riconciliazione va comunque **eseguita e documentata riga per riga durante la migrazione** (quali righe di `payment_movements` NON trovano corrispondenza in `balance_movements`?), non semplicemente scartata come rumore.

### 2.5 Colonne non-FK di `practices` collegate a record potenzialmente orfani

Le 8 colonne già segnalate in doc05 (`client_id`, `collaborator_id`, `veterinarian_id`, `owner_veterinarian_id`, `origin_veterinarian_id`, `used_voucher_id`, `urn_id`, `urn_id_2`) restano il punto più critico per l'integrità **quantitativa** — vedi sezione 4.

---

## 3. Lossless Migration — criteri di verifica per classe di trasformazione

| Classe di trasformazione | Perché necessaria | Come si effettua | Come si verifica | Come si dimostra "nessuna perdita" |
|---|---|---|---|---|
| **1→N (animali, tag, prezzi)** | Elimina limiti fissi (max 2 animali, 14 tag fissi) | Script Fase B, una riga V2 per ogni valore non-nullo in V1 | Conteggio: N atteso = numero di colonne valorizzate, non un numero fisso | Per ogni pratica, ricostruire "quanti animali/tag/voci aveva in V1" da un dump pre-migrazione e confrontare col conteggio righe V2 |
| **Rinomina 1:1 (balance_movements→payments, ecc.)** | Solo naming/tipizzazione, nessuna perdita concettuale | Copia diretta colonna-per-colonna | Conteggio + confronto valori campo per campo su un campione | Hash/checksum riga-per-riga tra V1 e V2 sulle colonne comuni |
| **Consolidamento fonti (fatture, urne)** | Elimina dualità già note come bug | Merge con controllo duplicati esplicito, log di ogni conflitto | Nessun `invoice_number` duplicato residuo dopo il merge (query dedicata) | Report di ogni riga scartata/unita, mai un merge silenzioso |
| **Deduzione → campo esplicito (circuito W/D)** | Elimina l'ambiguità strutturale già causa di un bug reale | Ogni riga preventivo dichiara il proprio canale, mai dedotto | Somma per canale in V2 deve coincidere con l'importo mostrato per lo stesso canale in V1 (dove univocamente interpretabile) | Per le pratiche con **entrambi** i circuiti valorizzati (vedi sezione 4), verifica manuale — non automatizzabile alla cieca |
| **Colonne senza destinazione decisa (i 74 gruppi della sezione 2.1)** | — | **Non ancora eseguibile**: non esiste ancora una regola di trasformazione per queste colonne | — | **Non dimostrabile finché non esiste una destinazione** — per definizione, il criterio "nessuna trasformazione accettata solo perché il dato non serve più" richiesto da te non può essere soddisfatto senza prima decidere dove va ciascuna di queste colonne |

**Principio applicato qui, come richiesto**: nessuna delle 74 colonne di §2.1 è stata dichiarata "non necessaria" da questo audit. Sono tutte marcate come **destinazione da decidere**, non come scartabili.

---

## 4. Dati ambigui — classificazione (tipi, non conteggi reali — vedi limite dichiarato in §0)

| Anomalia | Query di rilevamento | Classificazione proposta | Motivazione |
|---|---|---|---|
| Pratiche con **sia** `total_service` **sia** `total_text` valorizzati | doc05, query dedicata | **C — richiede intervento umano** | Non esiste una regola meccanica per decidere quale dei due importi sia quello "vero" quando entrambi sono presenti — è precisamente il bug reale già corretto in produzione questa sessione; usare una regola automatica (es. "vince D") riprodurrebbe lo stesso comportamento sbagliato in fase di migrazione |
| Pratiche con owner-inline ma senza `client_id` collegato | doc05 §2 (implicita, da formalizzare come query) | **B — riconciliabile con regola esplicita** | L'algoritmo di dedup fuzzy esiste già e funziona (`find_client_duplicates`) — ma i risultati **incerti** (match parziale, ambiguo) di quell'algoritmo devono cadere in **C**, non essere forzati automaticamente | 
| Fatture duplicate (stesso `invoice_number` in più righe/fonti) | doc05, ultima query | **B** se lo stesso numero appare identico in entrambe le fonti (`practices.invoice_number` e `movement_invoices`, stesso importo/data → chiaramente lo stesso evento visto da due tabelle); **C** se importi/date divergono per lo stesso numero |
| Eventi calendario collegati a pratiche cestinate (`linked_practice_id` → pratica con `deleted_at` valorizzato) | doc05, query dedicata | **A — riconciliabile automaticamente** | Comportamento noto e non ambiguo: la relazione resta valida concettualmente (la pratica esiste, solo cestinata), va solo preservata as-is, nessuna decisione da prendere |
| FK concettuali orfane (`client_id`/`veterinarian_id`/`collaborator_id`/`urn_id*` che puntano a righe inesistenti) | doc05, 4 query dedicate | **D — non interpretabile automaticamente** se il conteggio reale (da misurare) è > 0: un riferimento a un record che non esiste più non è "riconciliabile", va deciso caso per caso se annullare il riferimento o ricreare un record segnaposto |
| Righe orfane in `payment_movements` senza corrispondente in `balance_movements` | Nuova query da scrivere (non ancora in doc05) | **C** | Il codice V1 stesso dichiara che questa tabella può contenere righe non rappresentative — ogni riga scoperta va valutata, non scartata né migrata automaticamente |
| Stati incompatibili (es. ciclo di cremazione `completato` ma pratica non ancora `Cremato`/`Da consegnare`) | Nuova query da scrivere | **C** | Sintomo diretto dell'assenza di FSM in V1 (doc03) — richiede giudizio caso per caso su quale stato sia quello corretto |

**Nota metodologica esplicita**: questa tabella classifica i **tipi** di anomalia con una proposta di trattamento. I **conteggi reali** (quante righe ricadono in ciascuna classe) sono sconosciuti fino all'esecuzione contro un backup di produzione — punto già dichiarato come limite in §0, non un'omissione di questo audit.

---

## 5. Fatture — verifica della decisione e della fonte unica

- **Confermato**: `invoices.practice_id ON DELETE SET NULL` + `practice_number_snapshot` è ora la decisione **definitiva** (hai appena riconfermato esplicitamente in questo turno "la fattura non deve mai essere cancellata automaticamente perché una pratica viene eliminata/archiviata... documento fiscale persistente e tracciabile"). Chiude l'unico punto che doc06 aveva lasciato aperto. **Nessuna azione ulteriore richiesta su questo punto.**
- **Verifica fonte unica**: **confermata a livello di tabella** — `invoices` è l'unica tabella fattura in doc06, con `UNIQUE(invoice_number)` reale a database (miglioramento reale rispetto a V1, dove l'unicità era solo applicativa).
- **⚠️ Gap nuovo trovato in questo audit, non presente in doc06**: `invoices.total_amount_cents` è un campo inserito/derivato **senza alcun vincolo dichiarato** che lo tenga sincronizzato con `SUM(invoice_payment_links → payments.amount_cents)`. Se un domani l'importo di una fattura viene modificato (correzione manuale) senza aggiornare i pagamenti collegati, o viceversa, **si ricrea esattamente la stessa classe di bug** che l'intero doc06 §2 ha risolto per i pagamenti (l'importo mostrato che diverge dalla somma reale) — solo spostata dalle pratiche alle fatture. **Raccomandazione**: prima di implementare il dominio Fatture (Fase 5), decidere esplicitamente se `total_amount_cents` è (a) sempre calcolato come `SUM()` dei pagamenti collegati (mai memorizzato, come già fatto per "pagato" sulla pratica), o (b) inserito manualmente con una verifica di coerenza esplicita e visibile se diverge dalla somma dei pagamenti collegati. Non bloccante per la Fase 4.

---

## 6. Backup — stato reale e checklist operativa (NON considerato completato)

Confermo esplicitamente quanto hai scritto: **il backup NON è da considerarsi completato**. Stato reale ad oggi:

| Cosa | Stato |
|---|---|
| Codice (`backup_service.py`, `cron_backup.py`, endpoint, job cron) | ✅ Implementato, testato (13 test unitari/integrazione verdi) |
| Configurazione operativa (account B2, bucket, chiavi, variabili d'ambiente Render) | ❌ **Non completata — richiede azione manuale tua** |
| Prima esecuzione reale in produzione | ❌ Non verificata da questa sessione (non osservabile da qui) |
| **Restore reale testato** | ❌ **Non fatto — condizione esplicitamente bloccante per considerare il backup affidabile, per tua stessa regola** |

### Cosa devi configurare manualmente (checklist, invariata da doc00, ripetuta qui perché è la condizione del gate)

1. Account Backblaze B2 (o altro provider S3-compatibile) → bucket privato dedicato → Application Key con permessi solo su quel bucket.
2. Sul servizio web Render (`pet-paradise-manager`): `BACKUP_CRON_SECRET`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_KEY_ID`, `BACKUP_S3_APPLICATION_KEY`, `BACKUP_S3_BUCKET`.
3. Sul servizio cron Render (`pet-paradise-backup-cron`): `BACKUP_CRON_URL` (=`https://<tuo-dominio>/cron/backup`), `BACKUP_CRON_SECRET` (stesso valore del punto 2).

### Come verificare che il backup sia stato eseguito

- Log del servizio cron su Render dopo l'orario schedulato (02:00 UTC) → deve mostrare l'esecuzione e l'esito.
- Centro notifiche del gestionale → deve comparire una notifica di esito (successo o errore esplicito, mai un silenzio).
- Verifica diretta nel bucket B2 (dashboard Backblaze) → deve comparire un nuovo file `pet_paradise_<timestamp>.db.gz` sotto il prefisso `ppm-backups/`.

### Come eseguire il primo restore reale (obbligatorio prima di considerare il sistema affidabile)

1. Scaricare l'ultimo file `.db.gz` dal bucket.
2. Decomprimerlo (`gunzip`) → ottieni un file `.db` SQLite.
3. Aprirlo con un client SQLite qualunque (es. `sqlite3 pet_paradise_<timestamp>.db "PRAGMA integrity_check;"` — deve rispondere `ok`).
4. Verifica di contenuto reale: contare le righe di 2-3 tabelle chiave (`practices`, `clients`, `balance_movements`) e confrontarle con i conteggi noti in produzione nello stesso momento (dalla UI del gestionale) — non solo "il file si apre", ma "i dati dentro sono quelli giusti".

### Quali verifiche devono passare prima di dichiarare il backup davvero completato

- [ ] Configurazione operativa completata (checklist sopra)
- [ ] Almeno un'esecuzione automatica notturna riuscita, osservata nei log/notifiche
- [ ] Restore reale eseguito con successo (passi sopra)
- [ ] `PRAGMA integrity_check` = `ok` sul file ripristinato
- [ ] Conteggi di almeno 3 tabelle chiave confrontati e coerenti con la produzione al momento del backup

**Fino a quando questi 5 punti non sono tutti verificati, il backup indipendente resta "implementato ma non verificato" — non "completato".**

---

## 7. V1 durante lo sviluppo di V2 — verifica di coerenza con doc07

Confermato: quanto hai riaffermato in questo turno (nessuna sincronizzazione bidirezionale, V1 resta il sistema operativo, gestione esplicita dei dati modificati tra estrazione/sviluppo/migrazione finale) **coincide esattamente** con quanto già documentato in `07-strategia-migrazione.md` §"Gestione dei dati modificati durante la migrazione" e con l'interpretazione esplicitata in `12-piano-rilascio.md` §"Chiarimento preliminare". **Nessuna correzione necessaria** — la tua riconferma chiude definitivamente quel punto, che in doc12 era segnalato come un'assunzione (ora è una conferma esplicita, non più un'assunzione).

---

## 8. ARCHITECTURE GATE RESULT

# 🟡 GO WITH CONDITIONS

**Non è un via libera generico.** È un via libera **specifico e limitato** a quanto segue, con condizioni esplicite e bloccanti per tutto il resto.

### 🟢 GO — puoi iniziare subito, nessuna condizione trovata che lo blocchi
**Fase 4 così come definita in doc12 (dominio Clienti/Veterinari)**: questo audit non ha trovato nessuna incoerenza architetturale né nessuna colonna a rischio di perdita nel dominio Clienti/Veterinari/Sedi (`clients`, `veterinarians`, `veterinarian_hours`, `collaborators`, `collaborator_price_tiers`, `company_locations` — tutte confermate "concettualmente invariate" in doc06 e non toccate dai problemi trovati in questo audit). Con una sola condizione trasversale, a costo zero, da applicare da subito (vedi sotto, condizione 0).

### 🔴 NO-GO — non iniziare il codice applicativo di questi domini finché le condizioni elencate non sono chiuse

| Dominio bloccato | Condizione da chiudere prima |
|---|---|
| **Pratiche** (qualunque fase la tocchi) | Decisione esplicita sui 74/163 campi non mappati di `practices` (sezione 2.1) — almeno i gruppi 1 (owner snapshot), 2 (DDT/trasporto/compliance), 4 (override manuali fatturazione), 7 (billing_status collaboratori) |
| **Ritiro/Riconsegna** | Risolvere l'incoerenza `calendar_events.payment_status/payment_amount` per eventi non ancora collegati a una pratica (sezione 1); definire il grafo delle transizioni ammesse per `pickup_status` (non esiste ancora in nessun documento) |
| **Ciclo di cremazione** | Definire il grafo delle transizioni ammesse per `cremation_cycle_status` (stesso problema) |
| **Fatture/Pagamenti** | Decidere la politica di coerenza `invoices.total_amount_cents` vs somma pagamenti collegati (sezione 5); decidere la destinazione di `veterinarian_vouchers` e delle 3 colonne collegate (sezione 2.2); decidere se/come portare `balance_movement_deletions` in V2 (**il gap più grave trovato in questo audit** — è il meccanismo di reversibilità del ledger) |
| **Qualunque dominio con audit** | Rendere esplicito in doc09 che scrittura di dominio + riga `audit_log` avvengono sempre nella stessa transazione (correzione a costo zero, sezione 1) |

### Condizioni trasversali, da chiudere prima o durante la Fase 4 (non bloccano l'avvio, ma vanno chiuse prima della Fase 5)

0. **Aggiungere a doc09 la regola "audit_log sempre nella stessa transazione dell'operazione che descrive"** — zero costo, applicabile da subito anche a Clienti/Veterinari se in futuro avranno audit.
1. **Completare la configurazione operativa del backup e — soprattutto — eseguire un restore reale verificato** (checklist sezione 6). Non è tecnicamente parte del "modello dati V2", ma è la tua stessa condizione esplicita per procedere in sicurezza, e riguarda la protezione dei dati V1 che restano l'unica fonte di verità per tutto il periodo di sviluppo.
2. **Aggiornare `disposal_batches`, `disposal_batch_practices`, `urn_movements`, `calendar_zones` in doc06** — anche solo per confermarle "invariate" esplicitamente, non per omissione.
3. **Decidere e scrivere il grafo delle transizioni di stato** per Pratica (oltre a quanto già confermato su Cremato/Da consegnare), Ritiro, Riconsegna, Ciclo di cremazione — prerequisito tecnico per implementare qualunque `domain/*/state_machine.py` reale.

### Perché non è NO-GO totale

Il lavoro di doc06-12 è **architetturalmente solido** dove è stato verificato in profondità: il modello ledger/pagamenti (sezione "Ledger ↔ Pagamenti" sopra) è coerente e ben motivato; la strategia di migrazione a due fasi è tecnicamente corretta e verificabile; l'architettura backend/frontend è coerente internamente. Il problema reale trovato da questo audit non è "l'architettura è sbagliata" — è che **il modello dati V2 di `practices` (il cuore del sistema) è stato progettato risolvendo le 5 incoerenze note, ma non è ancora stato verificato colonna-per-colonna contro tutte le 163 colonne reali** — è esattamente il tipo di controllo che un Architecture Gate serve a fare, ed è esattamente quello che ha trovato.

### Perché non è GO totale

Perché procedere alla Fase 5 (Pratiche) con il modello dati attuale di doc06 comporterebbe, con alta probabilità, la perdita silenziosa o la reinterpretazione affrettata di dati reali oggi in produzione (owner snapshot storici, dati di tracciabilità trasporto, override manuali di fatturazione, il meccanismo di reversibilità degli storni) — in diretta violazione della tua regola assoluta "nessuna perdita di dati", che hai posto sopra la velocità di sviluppo.

---

## 9. Regola fondamentale — riconferma

Nessuna delle correzioni proposte in questo audit propone di "replicare come fa V1". Ogni gruppo di colonne non mappate è presentato con la domanda **"qual è il modo corretto di modellare questo processo aziendale in V2"**, non "come lo aveva fatto V1" — dove V1 ha un difetto strutturale (es. `billing_status` che rischia di ricreare una dualità fatture, o `owner_*` che pone la domanda reale "serve uno snapshot storico o no"), questo audit lo segnala come domanda di design aperta, non come requisito di replica.
