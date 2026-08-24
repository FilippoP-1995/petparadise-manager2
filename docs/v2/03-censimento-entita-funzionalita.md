# 03 — Censimento delle Entità e Inventario delle Funzionalità

> Logica di business (stati, transizioni, dipendenze, regole) per le entità principali. Lo schema (colonne/tipi/FK) è documentato in `02-modello-dati-attuale.md`; questo documento si concentra sul COMPORTAMENTO.

## 1. PRATICA

### Stati e transizioni
`STATES = ["Ritirato","In programma","Cremato","Da consegnare","Consegnato","Smaltito"]`, `PAYMENT_STATES = ["Da saldare","Acconto","Pagato"]`.

**Non esiste una macchina a stati (FSM)**: `change_state`/`quick_state` accettano qualunque valore in `STATES` come nuovo stato, senza vincolo di adiacenza né controllo di ruolo — chiunque autenticato può far regredire una pratica da `Consegnato` a `Ritirato`. Unico vincolo: `"Smaltito"` ammesso solo per `Cremazione collettiva`.

- **Side-effect di `Consegnato`**: programma un messaggio WhatsApp di ringraziamento 48h dopo (con guardie: flag disattivato, già inviato, telefono mancante), annullato se si esce dallo stato prima dell'invio.
- **Side-effect di `payment_status→Pagato`**: notifica push. **Nessun blocco sui campi economici**: una pratica "Pagato" resta interamente modificabile — totale/importi possono cambiare dopo, senza riconciliazione forzata.
- **`"Cremato"` esiste nel vocabolario ma il flusso automatico (ciclo di cremazione) non lo attraversa mai** — è raggiungibile solo con un cambio stato manuale. Incoerenza di modello.

### Creazione — due percorsi
1. Diretta (`POST /nuova`).
2. Da evento calendario Ritiro (`GET/POST /nuova?calendar_event_id=N`), con gate rigoroso: l'evento deve esistere, non essere cancellato, essere di tipo Ritiro/Ritiro in sede **e avere `event_status='Ritirato'`**; se già collegato, redirect diretto (niente doppioni). Guardia anti-race (`AND linked_practice_id IS NULL`) sull'update di collegamento.

Nessuna funzione "duplica pratica". La creazione gestisce in un solo giro: dedup cliente fuzzy (nome/telefono/email), numerazione pratica, voci preventivo, voucher veterinario, ed eventuali pagamenti già in fase di creazione.

### Cancellazione — soft-delete, non cascade reale
- `delete_practice`: soft delete (`deleted_at`), mai `DELETE` reale. Rinomina `practice_number` con placeholder, restituisce le urne a magazzino.
- Conseguenza: `calendar_events.linked_practice_id ON DELETE SET NULL` **non scatta mai**, perché la `DELETE` reale non avviene. Un evento calendario può restare puntato a una pratica cestinata, senza riconciliazione.
- `permanent_delete_practice` (solo da Cestino, richiede digitare "ELIMINA DEFINITIVAMENTE") fa una `DELETE` reale — con FK enforcement attivo, questa **fa scattare automaticamente CASCADE** su `payment_movements`/`movement_invoices`/`movement_invoice_links`; il codice cancella "a mano" solo `whatsapp_messages`, `whatsapp_inbound_messages`, `practice_history` (che non hanno FK cascade dichiarata).
- **`balance_movements` non ha FK verso `practices`**: una cancellazione definitiva lascia il ledger intatto con `practice_id` orfano ma `practice_number_snapshot` leggibile — scelta deliberata per non perdere lo storico contabile.

### Validazione
Richiede operatore/servizio/richiesta/anagrafica proprietario, **salvo bypass totale** se `tag_da_richiamare="Si"`, `Cremazione collettiva`, o `request_origin="Collaboratore"`. Lo stesso controllo alimenta il flag `data_complete`, che blocca l'assegnazione del numero DDT finché mancante.

## 2. RITIRO e RICONSEGNA (eventi calendario)

Righe della stessa tabella `calendar_events`, distinte da `event_type`. `PICKUP_STATUSES=("Da confermare","Da ritirare","Ritirato","Annullato")`, `DELIVERY_STATUSES=("In programma","Completato")`.

### RITIRO
- Stato non valido → forzato silenziosamente a `"Da confermare"` (nessun errore all'operatore).
- Cambio stato: nessun controllo di ruolo, nessun blocco su regressioni — si può tornare da "Ritirato" a "Annullato" anche con `linked_practice_id` già valorizzato, senza alcun avviso di disallineamento.
- La transizione a "Ritirato" **non crea automaticamente** la pratica: serve un'azione manuale separata ("+ Crea pratica").
- Cambiare `event_type` può resettare silenziosamente `event_status`, senza toccare `linked_practice_id` — potenziale incoerenza tipo/stato/collegamento.

### RICONSEGNA
- **Nessuno stato gestibile dall'utente** (per scelta esplicita): il form forza sempre `event_status="In programma"`. `"Completato"` è nel vocabolario e trattato come valido altrove nel codice, ma **risulta irraggiungibile dalla UI attuale** — stato morto.
- Creazione da pratica esistente precompila il pagamento (Pagato→Pagato+totale; acconto>0→Da saldare+rimanenza; altrimenti Da pagare+totale) — stessa logica **duplicata** (non condivisa) in un secondo punto del codice, rischio di divergenza futura.
- Al collegamento a una pratica, **non verifica se l'evento è già collegato ad un'altra pratica** — a differenza del flusso equivalente per i Ritiri, che invece blocca con errore esplicito il doppio collegamento. Incoerenza di validazione tra i due flussi gemelli.

### Animali futuri non ancora pratica
Animali di cremazione singola in eventi Ritiro non ancora "Ritirato", senza pratica collegata, con data futura/oggi — mostrati solo a scopo informativo in Programma Cremazioni, nessuna azione possibile su di loro (corretto: non esiste ancora una pratica da poter toccare).

## 3. CICLO DI CREMAZIONE

`status CHECK IN ('pianificato','in_attesa','completato')`.

- Colonna `actual_start` esiste nello schema ma **non è mai più scritta** — residuo di un vecchio stato `'in_corso'` ormai rimosso: campo orfano.
- Creazione: valida che la pratica candidata non sia già "Consegnato", sia cremazione singola (le collettive sono escluse dal sistema cicli) e non abbia già un ciclo.
- **Vincolo di capacità: massimo 2 animali per ciclo**, imposto a livello di codice.
- Transizioni: `pianificato→in_attesa` al primo animale assegnato; `in_attesa→pianificato` se l'ultimo viene rimosso; `in_attesa→completato`; reversibile.
- **Completamento ciclo**: porta lo stato pratica direttamente a "Da consegnare", **saltando completamente "Cremato"** — la stessa incoerenza segnalata al punto 1.
- Il ripristino (`revert_complete`) legge lo storico per capire a quale stato tornare, gestendo correttamente pratiche partite da stati diversi — buon pattern.
- **Eliminazione ciclo**: cancellazione reale, scollega le pratiche **senza controllare lo stato del ciclo** — si può eliminare anche un ciclo già "completato", lasciando pratiche marcate come cremate ma senza più un ciclo di riferimento.

## 4. PAGAMENTO — dualità `payment_movements` / `balance_movements`

- **`balance_movements`**: ledger append-only, **fonte di verità reale**. Trigger blocca ogni `UPDATE`; le correzioni avvengono per storno + nuovo movimento. Una `DELETE` reale è comunque possibile in casi limitati (righe tecniche non collegate), sempre loggata e annullabile — quindi "append-only" è accurato per `UPDATE` ma non assoluto per `DELETE`.
- **`payment_movements`**: tabella di dettaglio più vecchia, aggiornata/cancellata con `UPDATE`/`DELETE` reali, **può contenere righe residue non più rappresentative di un incasso reale** (commento esplicito nel codice applicativo: "mai usarla per i totali").
- Registrare un pagamento scrive **sempre entrambe le tabelle**. Rimuoverlo fa uno storno reale sul ledger (preserva la riga originale + storno) **e** una cancellazione fisica sulla tabella di dettaglio — asimmetria voluta ma da portare esplicitamente in V2.

### Regola circuito W/D
- Se il campo "Totale D" è valorizzato, **sostituisce interamente** il calcolo standard basato su "Totale W" per tutti i totali derivati (totale pratica, incassato, rimanenza).
- **Incoerenza architetturale confermata**: nulla impedisce che una pratica abbia valorizzati **entrambi** i totali contemporaneamente — nessun controllo di validazione lo previene. Una funzione di ricalcolo saldi lo riconosce esplicitamente e calcola i due circuiti in modo indipendente, MA le funzioni di "totale pratica" mostrate in testata pagina continuano a far vincere D su W — risultato: la cifra mostrata in alto può non coincidere con la somma dei due circuiti calcolati più sotto nella stessa pagina.
- A livello di singolo movimento (acconto/saldo), l'invariante "un solo circuito alla volta" **è** rispettata correttamente.
- **Due meccanismi di transizione stato-pagamento coesistono con regole diverse**: uno (più vecchio) ammette solo transizioni in avanti; l'altro (il popover Pagamento attuale) permette libero avanti/indietro.

## 5. FATTURA — dualità colonne legacy / `movement_invoices`

- Colonne legacy dirette su `practices` (nessun vincolo UNIQUE a livello DB) + tabella `movement_invoices` (una fattura per movimento/circuito, con link N:N ai movimenti).
- Il controllo duplicati è l'**unico** punto che tratta le due fonti come uno spazio-nomi unico (bug reale già corretto in questa sessione: il form pratica leggeva solo il legacy).
- La pagina Fatture **non deduplica**: concatena righe da entrambe le fonti — una pratica con acconto e saldo fatturati separatamente compare come due righe (comportamento dichiarato "by design" nel commento UI, ma da rivalutare in V2).
- Il promemoria "Da fatturare" guarda **solo** il legacy, ignorando `movement_invoices` — incoerente col filtro "Senza fattura" dell'Archivio, che invece controlla correttamente entrambe le fonti (aggiunto in questa sessione).
- Nessuna scrittura duplicata intenzionale della stessa fattura, ma i due flussi (fattura generica pratica vs fattura per movimento) **coesistono nella stessa richiesta di creazione pratica**, con due controlli duplicati indipendenti che non si parlano semanticamente.
- **Raccomandazione V2**: unificabile in un'unica tabella `invoices` con link opzionale (0..N) ai movimenti di pagamento, con vincolo UNIQUE reale a livello database (oggi l'unicità è solo applicativa, con rischio di race condition tra richieste concorrenti).

## 6. UTENTE

- Solo due ruoli (`admin`/`operator`), nessun ruolo intermedio, permessi verificati con controlli sparsi nel codice (~20+ punti), non centralizzati.
- **Nessuna interfaccia per gestire utenti**: creati solo al primo avvio, hardcoded (un admin + 4 operatori con nome fisso), password iniziale identica per tutti con obbligo di cambio al primo accesso. Il flag "attivo" esiste ma non risulta mai impostabile a "disattivo" da nessuna schermata — non c'è modo di disabilitare un dipendente che lascia l'azienda senza intervenire sul database direttamente.
- Hashing password robusto (PBKDF2, 210.000 iterazioni, salt casuale, confronto a tempo costante) — da preservare in V2.
- Sessioni: **non scadono mai lato server**, solo il cookie ha una durata (~180 giorni) — un token rubato/copiato resta valido indefinitamente finché non viene esplicitamente disconnesso.
- Pattern di audit trail (chi ha creato/modificato/eliminato + quando) presente in modo coerente su quasi tutte le entità principali, ma scritto manualmente riga per riga in ogni handler — nessun meccanismo automatico/trigger, quindi affidato alla disciplina di ogni nuovo endpoint.

## Tabella riassuntiva

| Entità | Funziona bene | Problemi noti |
|---|---|---|
| **Pratica** | Creazione anti-doppioni robusta, cestino/ripristino coerente | Nessuna FSM stati; nessun lock post-pagamento; soft-delete non propaga ai collegamenti calendario |
| **Ritiro** | Fallback sicuro su stato non valido, collegamento pratica race-safe | Nessun controllo ruolo/regressione stato; cambio tipo può disallineare stato/collegamento |
| **Riconsegna** | Prefill pagamento coerente da pratica | Stato "Completato" morto (irraggiungibile); nessun controllo doppio collegamento (a differenza del Ritiro) |
| **Ciclo cremazione** | Vincoli di assegnazione chiari e validati, ripristino intelligente | Stato "Cremato" mai raggiunto dal flusso automatico; eliminazione ciclo permessa anche da completato; campo `actual_start` orfano |
| **Fattura** | Controllo duplicati unificato tra le due fonti | Elenco non deduplicato; promemoria "da fatturare" incoerente col filtro archivio; nessun vincolo UNIQUE reale a DB |
| **Pagamento** | Ledger append-only affidabile, idempotenza, storni tracciati | Tabella di dettaglio con righe potenzialmente residue; regola W/D non impedisce doppia valorizzazione; due meccanismi di transizione con regole diverse |
| **Utente** | Hashing password robusto | Nessuna UI di gestione utenti; nessuna disattivazione possibile; sessioni senza scadenza server-side; permessi non centralizzati |

## Priorità per la V2 (derivate da questo censimento)

1. **Macchine a stati esplicite** per Pratica, Ritiro, Riconsegna, Ciclo cremazione — con transizioni dichiarate, non "qualunque valore va bene".
2. **Un solo circuito di pagamento per pratica, imposto dal modello dati** (non solo dedotto da "quale campo è valorizzato").
3. **Fonte unica per le fatture**, con vincolo di unicità reale a livello database.
4. **Cancellazione pratica coerente**: se resta soft-delete, propagare esplicitamente lo scollegamento agli eventi calendario collegati invece di affidarsi a un FK che non scatta mai.
5. **Gestione utenti reale** (creazione, disattivazione, ruoli) invece di bootstrap hardcoded.
6. **Sessioni con scadenza server-side reale**, non solo cookie a lunga durata.
