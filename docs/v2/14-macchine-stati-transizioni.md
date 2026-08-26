# 14 — Macchine a Stati: Grafi di Transizione (Pratica, Ritiro, Riconsegna, Ciclo di cremazione)

> Richiesto esplicitamente prima di implementare qualunque `domain/*/state_machine.py` (condizione doc 13). **Non è ancora codice** — è la specifica del grafo che il codice dovrà rispettare. Dove il comportamento V1 è ambiguo o puramente accidentale (nessuna regola dichiarata, solo "quello che il codice permette"), questo documento **non inventa** una regola di business al posto tuo: la segnala esplicitamente come **DECISIONE AZIENDALE NECESSARIA**. Le transizioni proposte come DECISIONE TECNICA sono quelle che derivano direttamente da un fatto già confermato altrove (doc 03, doc 06, o le tue conferme esplicite), non da una mia preferenza di design.

## Legenda classificazione

- **FACT**: comportamento verificato nel codice V1 (file:riga).
- **DECISIONE TECNICA**: proposta di questo documento, motivata da un fatto già stabilito — non richiede una tua risposta per essere implementata.
- **DECISIONE AZIENDALE NECESSARIA**: punto che V1 lascia ambiguo/non vincolato — serve una tua risposta esplicita prima di poter implementare quella parte della macchina a stati.

---

## 1. PRATICA — `practice_status`

### Stati (confermati, doc 06)
`ritirato, in_programma, cremato, da_consegnare, consegnato, smaltito`

### FACT rilevanti da V1
- **Nessun vincolo di adiacenza oggi**: `change_state`/`quick_state` (app.py:17625-18078) accettano qualunque valore in `STATES`, l'unico controllo è `"smaltito"` ammesso solo se `service_type="Cremazione collettiva"` (app.py:17634, 18070).
- **Default alla creazione**: `initial=f.get("status","Ritirato")` (app.py:16945) — "Ritirato" è il default, ma il form può passare qualunque altro valore esplicitamente; **nessun vincolo che impedisca di creare una pratica già in uno stato avanzato**.
- **Side-effect noti e confermati** (da preservare, non da reinventare): ingresso in `"Consegnato"` → programma messaggio WhatsApp di ringraziamento 48h dopo; uscita da `"Consegnato"` → annulla il messaggio se non ancora inviato (app.py:18074-18075, 17644-17648); ingresso in `"Consegnato"` → notifica push `practice_delivered` (app.py:18076).
- **Decisione già confermata da te** (doc 06, decisione utente #2): il completamento del ciclo di cremazione porta la pratica a `"cremato"` esplicitamente (non più direttamente a `"da_consegnare"` come in V1), con un passaggio operativo separato per `"da_consegnare"`.

### Grafo proposto

```
                 ┌────────────────────────────────────────────┐
                 │                                              │
   [creazione] ──┴──► ritirato ──► in_programma ──► cremato ──► da_consegnare ──► consegnato ──► smaltito*
                                                                                                   (*solo se
                                                                                                   service_type=
                                                                                                   'Cremazione
                                                                                                   collettiva')
```

| Transizione | Permessa | Chi può eseguirla | Prerequisiti | Side-effect | Audit |
|---|---|---|---|---|---|
| `[creazione]` → `ritirato` | ✅ **DECISIONE TECNICA** — è il default FACT confermato (app.py:16945) | Operatore/Admin (chiunque crei una pratica) | Nessuno | — | `audit_log action='created'` |
| `ritirato` → `in_programma` | ✅ **DECISIONE TECNICA** — coerente con l'ordine dichiarato in `STATES` e col fatto che "in programma" precede la cremazione | Operatore/Admin | Nessuno | — | `audit_log action='state_changed'` |
| `in_programma` → `cremato` | ✅ **DECISIONE TECNICA** — completamento ciclo di cremazione (decisione utente già confermata in doc 06) | Sistema (side-effect automatico del completamento ciclo) **o** Operatore/Admin manualmente | Ciclo di cremazione collegato in stato `completato`, **oppure** azione manuale esplicita | — | `audit_log`, azione distinta se manuale vs automatica |
| `cremato` → `da_consegnare` | ✅ **DECISIONE TECNICA** — passo operativo separato, come confermato da te in doc 06 | Operatore/Admin (azione esplicita, non automatica) | Stato corrente = `cremato` | — | `audit_log` |
| `da_consegnare` → `consegnato` | ✅ FACT preservato | Operatore/Admin | Stato corrente = `da_consegnare` (proposta, vedi sotto) | Programma WhatsApp ringraziamento 48h; notifica push `practice_delivered` | `audit_log` |
| `consegnato` → `smaltito` | ✅ FACT preservato, con vincolo esistente | Operatore/Admin | `service_type = 'Cremazione collettiva'` | — | `audit_log` |

### Transizioni normali vs correzioni eccezionali — DECISIONE AZIENDALE CHIUSA (aggiornamento doc 15)

L'utente ha chiarito la regola: **non regressioni libere, ma nemmeno stati irreversibili a prescindere**. Il grafo "in avanti" sopra resta il **workflow normale**. Qualunque altra transizione tra due stati validi dell'enum (regressione, salto, o combinazione) è una **correzione eccezionale**, regolata così:

| | Transizione di workflow (il grafo sopra) | Correzione eccezionale (qualunque altra transizione tra stati validi) |
|---|---|---|
| Chi può eseguirla | Operator o Admin | **Solo Admin** |
| Motivo richiesto | No | **Sì, obbligatorio** (testo libero, non vuoto) |
| Audit | `audit_log action='state_changed'` | `audit_log action='state_corrected'`, include vecchio stato, nuovo stato, motivo, utente |
| Vincoli residui | Il grafo stesso | **Restano comunque validi tutti i vincoli di dominio** (es. `smaltito` solo se `service_type='Cremazione collettiva'`) — una correzione non è un bypass delle regole, solo un bypass dell'ordine "in avanti" |
| Transazione | Atomica (regola doc 09) | Atomica (stessa regola, nessuna eccezione per l'Admin) |

- **Perché questo chiude anche il punto "salti di stato" (`request_origin='Consegna in sede'` ecc.)**: qualunque salto che in futuro si rivelasse necessario come caso "normale" per un'origine specifica resta comunque **disponibile fin da subito** come correzione Admin — non è più un blocco che impedisce di lavorare in attesa di una decisione. Se in futuro si volesse promuovere un salto specifico da "correzione" a "transizione normale" per una particolare origine, è un raffinamento successivo, non un prerequisito per iniziare lo sviluppo.
- **L'Admin non ha un bypass incontrollato** (esplicitamente richiesto): anche le correzioni passano da validazione, audit, transazione atomica e motivo obbligatorio — nessuna scorciatoia che salti queste regole.

### Punti tecnici residui (non bloccanti)

1. **La transizione automatica `in_programma`→`cremato` generata dal completamento ciclo: deve essere reversibile 1:1 con `revert_complete`** (FACT, comportamento già esistente e corretto in V1, doc 03) — la proposta tecnica è preservarlo (il ripristino del ciclo riporta la pratica allo stato precedente noto), ma la lista esatta degli stati "precedenti noti" da cui si può tornare non è stata riverificata riga per riga in questo documento.
2. **Punto di ingresso per pratiche non nate da un Ritiro** (`request_origin='Collaboratore'`/`'Consegna in sede'` create direttamente, senza `originating_pickup_event_id`): la decisione aziendale ricevuta conferma che il caso *ordinario* è `Ritiro → Pratica` (ingresso a `ritirato`, doc 06 §"Relazione Ritiro → Pratica"), ma non specifica se questi casi eccezionali debbano sempre iniziare comunque da `ritirato` o possano iniziare altrove — **non assunto**, resta un punto aperto minore, non bloccante grazie al meccanismo di correzione sopra (nel dubbio, si crea a `ritirato` e si corregge se necessario, con motivo tracciato).

---

## 2. RITIRO — `pickup_status`

### Stati (confermati, doc 06)
`da_confermare, da_ritirare, ritirato, annullato`

### FACT rilevanti da V1
- Stato non valido → forzato silenziosamente a `"Da confermare"` (doc 03) — **comportamento da NON preservare silenziosamente in V2**: un valore non valido deve produrre un errore esplicito, non un fallback muto (coerente col principio generale "mai un comportamento degradato senza segnalazione", già applicato ovunque in questo progetto).
- Nessun vincolo di adiacenza oggi, nessun controllo di ruolo (doc 03).
- La transizione a `"Ritirato"` **non crea automaticamente** la pratica — richiede un'azione separata "+ Crea pratica" (FACT, preservato: è un'azione esplicita dell'operatore, non un side-effect automatico, e nessuna indicazione contraria è mai stata data).

### Grafo proposto

```
   [creazione] ──► da_confermare ──► da_ritirare ──► ritirato
                        │                 │
                        └─────────────────┴──────────► annullato
```

| Transizione | Permessa | Note |
|---|---|---|
| `[creazione]` → `da_confermare` | ✅ DECISIONE TECNICA (default FACT) | |
| `da_confermare` → `da_ritirare` | ✅ DECISIONE TECNICA (ordine dichiarato in `PICKUP_STATUSES`) | |
| `da_ritirare` → `ritirato` | ✅ DECISIONE TECNICA | Non crea automaticamente la pratica (FACT preservato) |
| `da_confermare`/`da_ritirare` → `annullato` | ✅ DECISIONE TECNICA (stato terminale raggiungibile da entrambi, coerente con l'uso reale "il cliente ha annullato prima del ritiro") | |
| `ritirato` → `annullato` | ✅ **DECISIONE AZIENDALE CHIUSA** (aggiornamento doc 15) — vedi dettaglio sotto | |
| `annullato` → qualunque altro stato | 🔴 **VIETATO — DECISIONE AZIENDALE CHIUSA**: `annullato` è terminale, nessuna transizione in uscita, mai riapribile | |

### `ritirato` → `annullato` con pratica collegata — dettaglio della decisione chiusa

**A. `annullato` è terminale** (confermato): nessuna transizione in uscita, in nessun caso — non serve una "procedura di riapertura", perché non ne esiste una.

**B. Annullamento del ritiro con pratica collegata — due azioni distinte, non una sola:**

| Azione | Cosa succede alla pratica | Conferma richiesta | Audit |
|---|---|---|---|
| **"Annulla ritiro"** (azione normale) | **Nessuna modifica automatica**. Il sistema segnala in modo evidente che il ritiro annullato ha una pratica associata (badge/avviso nella UI, non un blocco) | Conferma standard | `audit_log` sul Ritiro |
| **"Annulla ritiro e cancella anche la pratica collegata"** (azione distinta, esplicita) | Applica il flusso di **cestinazione** già definito per la pratica (soft-delete, scollegamento eventi calendario collegati, doc 06 §"Cancellazione pratica coerente") — **mai** una `DELETE` diretta | Conferma esplicita separata, che rende chiaro che verrà coinvolta anche la pratica (es. "Verrà cestinata anche la pratica {numero} collegata — confermi?") | Due righe `audit_log`: una sul Ritiro, una sulla Pratica (`action='trashed'`, motivo="ritiro collegato annullato") |

- **Documenti fiscali mai coinvolti automaticamente**: se la pratica cestinata ha fatture, si applica la regola già stabilita in doc 06 (`invoices.practice_id ON DELETE SET NULL` + snapshot) — la fattura non sparisce mai per effetto di questa azione, coerentemente con la regola generale "nessuna perdita di documenti fiscali".
- **Nessuna modifica automatica oltre a quanto sopra**: l'azione normale di annullamento non tocca in alcun modo la pratica; solo l'azione distinta ed esplicita lo fa.

### Punti tecnici residui (non bloccanti)
1. **Chi può eseguire l'annullamento del Ritiro**: non specificato nella decisione ricevuta (che riguardava esplicitamente le correzioni di stato Pratica, non l'autorizzazione ad annullare un Ritiro) — **non esteso per assunzione**. Proposta di default a bassa invasività: stesso livello di chiunque oggi gestisce i Ritiri (Operator+Admin), da confermare se si desidera restringerlo.
2. **Cambio di `event_type`** (es. Ritiro → Ritiro in sede): **già chiuso come decisione tecnica** (doc 15, decisione #6) — mai un reset silenzioso dello stato, l'operatore deve sempre scegliere esplicitamente il nuovo stato, `linked_practice_id` non viene mai toccato da un cambio di tipo.

---

## 3. RICONSEGNA — nessuna macchina a stati

**Confermato (doc 06, decisione utente #3)**: lo stato "Completato" è **eliminato dal vocabolario**, nessuna colonna di stato dedicata su Riconsegna in V2. **Non serve quindi un grafo di transizione per questa entità** — punto chiuso, nessuna decisione aziendale pendente qui. L'unico aspetto ancora aperto per le Riconsegne è quello economico/preliminare, già indirizzato nell'Addendum P di `06-modello-dati-v2.md`.

---

## 4. CICLO DI CREMAZIONE — `cremation_cycle_status`

### Stati (invariati da V1, confermato CHECK a livello DB)
`pianificato, in_attesa, completato`

### FACT rilevanti da V1 (doc 03)
- `pianificato → in_attesa` al primo animale assegnato; `in_attesa → pianificato` se l'ultimo animale viene rimosso (transizioni **automatiche**, derivate dal conteggio animali assegnati, non un'azione diretta dell'operatore sullo stato).
- `in_attesa → completato`: azione esplicita.
- **Reversibile**: `revert_complete` riporta la pratica allo stato corretto leggendo lo storico (FACT, comportamento corretto già oggi — da preservare).
- **Vincolo di capacità**: massimo 2 animali per ciclo (FACT, imposto a livello di codice in V1). **DECISIONE AZIENDALE CHIUSA (doc 15)**: non è una limitazione tecnica accidentale, è una scelta aziendale deliberata — **resta 2 anche in V2**, trattata come regola di dominio (non solo un limite di interfaccia). Il backend deve impedirne il superamento anche tentando di aggirarlo via API diretta, non solo bloccarlo nella UI.
- **Problema noto da correggere**: oggi un ciclo può essere eliminato anche se già `completato`, scollegando le pratiche senza controllare lo stato (doc 03) — comportamento **da non preservare** in V2.

### Grafo proposto

```
   [creazione, 0 animali] ──► pianificato ⇄ in_attesa ──► completato
                                (automatico, in base al conteggio animali assegnati)
```

| Transizione | Permessa | Trigger | Side-effect |
|---|---|---|---|
| `pianificato` → `in_attesa` | ✅ DECISIONE TECNICA (FACT preservato) | Automatico: primo animale assegnato | — |
| `in_attesa` → `pianificato` | ✅ DECISIONE TECNICA (FACT preservato) | Automatico: ultimo animale rimosso | — |
| `in_attesa` → `completato` | ✅ DECISIONE TECNICA (FACT preservato) | Azione esplicita operatore/Admin | Pratiche collegate: `in_programma`→`cremato` (vedi §1) |
| `completato` → `in_attesa` (ripristino) | ✅ DECISIONE TECNICA (FACT preservato, `revert_complete`) | Azione esplicita | Pratiche collegate tornano allo stato precedente noto |
| **Eliminazione del ciclo mentre `completato`** | 🔴 **CORRETTO RISPETTO A V1** — proposta: **vietata** per costruzione in V2 (un ciclo `completato` con pratiche collegate cremate non deve poter sparire lasciando pratiche "cremate senza ciclo") | — | — |

### Punti tecnici residui (non bloccanti)
1. **Correzione di un ciclo `completato` per errore — già chiuso come decisione tecnica** (doc 15, decisione #10): non serve una procedura alternativa dedicata — si usa il percorso di ripristino già esistente e già preservato in questo documento (`completato → in_attesa`, poi correzione, es. riassegnazione animali, poi ri-completamento). Resta vietata solo l'eliminazione diretta del ciclo mentre è `completato`.
2. **Limite di 2 animali per ciclo — chiuso** (vedi sopra): 2, confermato come regola di dominio, non di interfaccia.
3. **Chi può completare/ripristinare un ciclo**: nessuna distinzione di ruolo in V1, non introdotta per assunzione — non toccato dalla decisione ricevuta (che riguardava le correzioni di stato Pratica).

---

## Riepilogo — stato delle decisioni (aggiornato dopo doc 15)

| # | Entità | Decisione | Stato |
|---|---|---|---|
| 1 | Pratica | Regressioni/correzioni di stato | ✅ **Chiusa** — workflow normale (Operator+Admin) vs correzione eccezionale (solo Admin, motivo obbligatorio, audit dedicato) |
| 2 | Pratica | Salti di stato (skip) | ✅ **Chiusa implicitamente** — rientrano nel meccanismo di correzione sopra, nessun blocco residuo |
| 3 | Pratica | Ruoli sulle transizioni | ✅ **Chiusa** — Operator: workflow normale; Admin: anche correzioni, senza bypass delle validazioni/audit/transazioni |
| 4 | Ritiro | `ritirato`→`annullato` con pratica collegata | ✅ **Chiusa** — nessuna modifica automatica alla pratica; azione distinta ed esplicita "Annulla ritiro e cancella anche la pratica" per chi lo desidera |
| 5 | Ritiro | `annullato` terminale o riapribile | ✅ **Chiusa** — terminale, nessuna riapertura |
| 6 | Ritiro | Cambio `event_type` a metà flusso | ✅ **Chiusa** (già in doc 15) — mai reset silenzioso |
| 7 | Ciclo cremazione | Procedura di correzione per ciclo `completato` per errore | ✅ **Chiusa** (già in doc 15) — usa il ripristino `completato→in_attesa` già esistente |
| 8 | Ciclo cremazione | Limite di 2 animali per ciclo | ✅ **Chiusa** — resta 2, regola di dominio, enforcement lato backend obbligatorio |

**Punti tecnici residui non bloccanti** (non richiedono una tua risposta per iniziare lo sviluppo, restano annotati nei rispettivi paragrafi sopra): reversibilità esatta di `in_programma`→`cremato` da ogni stato precedente noto; punto di ingresso per pratiche eccezionali non nate da un Ritiro; chi può annullare un Ritiro; chi può completare/ripristinare un ciclo.

**Tutte le decisioni aziendali di questo documento sono ora chiuse.** Nessuna transizione del workflow normale resta bloccata da un punto aperto.
