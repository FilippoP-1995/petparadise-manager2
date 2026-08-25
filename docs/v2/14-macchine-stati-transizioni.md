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
| **Qualunque stato → stato precedente (regressione)** | ⚠️ **DECISIONE AZIENDALE NECESSARIA** | — | — | — | — |
| **Salto di più stati in una volta (es. `ritirato`→`consegnato` direttamente)** | ⚠️ **DECISIONE AZIENDALE NECESSARIA** | — | — | — | — |

### Punti che questo documento NON decide (richiedono una tua risposta)

1. **Le regressioni di stato devono essere permesse?** Oggi in V1 sì, senza alcun controllo (doc 03: "chiunque autenticato può far regredire una pratica da Consegnato a Ritirato"). Opzioni possibili: (a) mai permesse (solo avanti); (b) permesse solo per Admin; (c) permesse per chiunque ma sempre con motivo obbligatorio tracciato. **Nessuna delle tre è stata assunta qui.**
2. **I salti (skip di stati intermedi) devono essere permessi?** Es. una pratica con `request_origin='Consegna in sede'` potrebbe non passare mai da `ritirato` in senso stretto — è un caso reale non coperto esplicitamente da nessun documento finora. Serve sapere se per alcune origini/tipologie di pratica il punto di ingresso naturale nel grafo è diverso da `ritirato`.
3. **Chi può eseguire quali transizioni?** V1 non distingue Admin/Operator per nessuna transizione di stato pratica. Se in V2 si vuole introdurre una distinzione (es. solo Admin può far regredire, o solo Admin può saltare `cremato`), va deciso esplicitamente — questo documento non lo assume.
4. **La transizione automatica `in_programma`→`cremato` generata dal completamento ciclo: deve essere reversibile 1:1 con `revert_complete`** (FACT, comportamento già esistente e corretto in V1, doc 03) — la proposta tecnica è preservarlo (il ripristino del ciclo riporta la pratica allo stato precedente noto), ma la lista esatta degli stati "precedenti noti" da cui si può tornare non è stata riverificata riga per riga in questo documento.

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
| `ritirato` → `annullato` | ⚠️ **DECISIONE AZIENDALE NECESSARIA** | Oggi possibile in V1 senza alcun controllo anche con `linked_practice_id` già valorizzato (doc 03: "incoerenza esplicitamente segnalata"). Se permesso in V2, va definito cosa succede alla pratica eventualmente già creata — non deciso qui. |
| `annullato` → qualunque altro stato | ⚠️ **DECISIONE AZIENDALE NECESSARIA** | V1 non lo impedisce oggi, ma non è chiaro se sia un uso reale o solo un'assenza di controllo |

### Punti aperti
1. **`ritirato`→`annullato` con pratica già collegata**: serve una regola esplicita (scollega la pratica? blocca la transizione? richiede conferma con avviso?) — non assunta qui.
2. **Chi può eseguire l'annullamento**: nessuna distinzione di ruolo in V1, non introdotta qui per assunzione.
3. **Cambio di `event_type`** (es. Ritiro → Ritiro in sede): FACT, oggi può resettare silenziosamente `event_status` senza toccare `linked_practice_id` (doc 03) — comportamento da correggere, ma la regola corretta (mantenere lo stato? richiedere ri-conferma?) non è definita da nessun documento.

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
- **Vincolo di capacità**: massimo 2 animali per ciclo (FACT, imposto a livello di codice — **DECISIONE AZIENDALE NECESSARIA se si vuole cambiare questo limite in V2**, non modificato qui).
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

### Punti aperti
1. **Eliminazione ciclo `completato`**: la proposta sopra (vietarla) è una correzione tecnica ragionevole del bug noto in doc 03/04, ma se esiste un caso operativo reale in cui serve davvero eliminare un ciclo già completato (es. errore di registrazione), serve una procedura alternativa esplicita (es. "riapri poi elimina") — **DECISIONE AZIENDALE NECESSARIA** su quale procedura adottare, non assunta qui.
2. **Limite di 2 animali per ciclo**: preservato come FACT, non ridiscusso — se vuoi aumentarlo/rimuoverlo in V2 è una tua decisione, non tecnica.
3. **Chi può completare/ripristinare un ciclo**: nessuna distinzione di ruolo in V1, non introdotta per assunzione.

---

## Riepilogo decisioni aziendali ancora necessarie (da questo documento)

| # | Entità | Decisione richiesta |
|---|---|---|
| 1 | Pratica | Le regressioni di stato sono permesse? Se sì, con quali vincoli/ruoli? |
| 2 | Pratica | I salti di stato (skip di stati intermedi) sono permessi, e per quali `request_origin`/`service_type`? |
| 3 | Pratica | Distinzione di ruolo (Admin vs Operator) su transizioni specifiche? |
| 4 | Ritiro | `ritirato`→`annullato` con pratica già collegata: quale comportamento? |
| 5 | Ritiro | `annullato` è uno stato davvero terminale, o sono ammesse riaperture? |
| 6 | Ritiro | Cambio `event_type` a metà flusso: mantenere o resettare lo stato? |
| 7 | Ciclo cremazione | Procedura alternativa per correggere un ciclo `completato` per errore, dato che l'eliminazione diretta viene ora vietata? |
| 8 | Ciclo cremazione | Il limite di 2 animali per ciclo resta invariato in V2? |

Nessuna di queste 8 decisioni blocca l'implementazione delle parti già chiuse (le transizioni "in avanti" del percorso principale, confermate come DECISIONE TECNICA) — bloccano solo l'implementazione dei casi limite/eccezione corrispondenti, che andranno gestiti con un errore esplicito ("transizione non permessa in V2 allo stato attuale") finché non vengono chiarite, mai con un comportamento silenzioso indovinato.
