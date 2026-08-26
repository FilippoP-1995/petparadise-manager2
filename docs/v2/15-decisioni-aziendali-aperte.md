# 15 — Decisioni Aziendali Aperte (Architecture Gate, condizioni da chiudere)

> Trasforma le 11 decisioni aperte segnalate dopo l'audit (doc 13/14) in decisioni operative, con opzioni motivate. Classificazione per ciascuna: **A** decisione aziendale necessaria da te, **B** decisione tecnica presa autonomamente, **C** richiede verifica sui dati reali, **D** possibili implicazioni normative/fiscali/privacy, richiede cautela. Solo le decisioni A e D restano da rispondere — le B sono già risolte e non richiedono nulla da te.

---

## AGGIORNAMENTO — decisioni chiuse dopo la tua risposta

Tutte le decisioni sotto sono ora **chiuse**, tranne le due normative (#7, #9) che restano **VERIFICA NORMATIVA PENDENTE** con una decisione provvisoria prudenziale confermata. Il dettaglio tecnico completo di ogni chiusura vive nei documenti collegati (doc 06 per il modello dati, doc 14 per le macchine a stati) — qui sotto solo l'esito, non ripetuto due volte.

| # | Decisione | Esito | Dettaglio in |
|---|---|---|---|
| 1 | Regressioni stato Pratica | ✅ Chiusa — workflow normale vs correzione eccezionale (solo Admin, motivo, audit) | doc 14 §1 |
| 2 | Salti di stato Pratica | ✅ Chiusa implicitamente — coperti dal meccanismo di correzione | doc 14 §1 |
| 3 | Ruoli su transizioni Pratica | ✅ Chiusa — Operator: workflow; Admin: anche correzioni, senza bypass | doc 14 §1 |
| 4 | Ritiro annullato con pratica collegata | ✅ Chiusa — nessuna modifica automatica; azione distinta "Annulla ritiro e cancella anche la pratica" | doc 14 §2 |
| 5 | Ritiro "annullato" terminale/riapribile | ✅ Chiusa — terminale, mai riapribile | doc 14 §2 |
| 6 | Cambio `event_type` a metà flusso | ✅ Già chiusa in questo documento (tecnica) | doc 14 §2 |
| 7 | DDT/trasporto — retention normativa | 🔶 **VERIFICA NORMATIVA PENDENTE** — decisione provvisoria: conservare tutto, scelta tecnica prudenziale, non obbligo accertato | doc 06 Addendum B |
| 8 | Provenienza/origine — logica di fallback | ✅ Chiusa — modello multi-sede esplicito (affido/destinazione/cremazione/riconsegna), nessuna logica di fallback: tutto è scelto esplicitamente dall'operatore | doc 06 Addendum C (riscritto) |
| 9 | Disposal batches — retention normativa | 🔶 **VERIFICA NORMATIVA PENDENTE** — stessa decisione provvisoria di #7 | doc 06 Addendum J |
| 10 | Correzione ciclo completato | ✅ Già chiusa in questo documento (tecnica) | doc 14 §4 |
| 11 | Limite 2 animali per ciclo | ✅ Chiusa — resta 2, regola di dominio deliberata, enforcement backend obbligatorio | doc 14 §4 |
| — | **NUOVA**: relazione Ritiro → Pratica | ✅ Chiusa — il caso ordinario è `Ritiro → Pratica`; le due entità restano tecnicamente distinte, relazione resa esplicita e bidirezionale (`originating_pickup_event_id`) | doc 06 §"Relazione Ritiro → Pratica" |
| — | **NUOVA (round 3)**: chi può annullare un Ritiro | ✅ Chiusa — Operator o Admin, non riservata all'Admin, per entrambe le varianti (annullamento semplice e "annulla e cancella anche la pratica") | doc 14 §2 |
| — | **NUOVA (round 3)**: pratiche create direttamente (senza Ritiro di origine) | ✅ Chiusa — due percorsi ammessi (A: da Ritiro, B: diretto per Collaboratore/Consegna in sede), **entrambi** entrano sempre a stato `ritirato`; lo stato iniziale non è mai un parametro esposto dall'API di creazione | doc 06 §"Relazione Ritiro → Pratica", doc 14 §1, doc 09 §"lo stato iniziale non è mai un parametro di creazione" |

**Le sezioni di dettaglio sotto restano come riferimento storico** (mostrano il ragionamento/le opzioni originarie) — lo stato aggiornato di ciascuna è quello della tabella sopra, non quello scritto nella sezione "DECISIONE RICHIESTA DA ME" originale.

---

## DECISIONE #1 — Regressioni di stato Pratica
**Classificazione: A**

**FACT**
`change_state`/`quick_state` (app.py:17625-18078) accettano qualunque valore in `STATES` come nuovo stato, senza controllo di adiacenza né di ruolo.

**PROBLEMA**
Se in V2 la macchina a stati è chiusa (solo le transizioni dichiarate sono valide), va deciso se una pratica può tornare a uno stato precedente (es. da "Consegnato" a "Ritirato") e a quali condizioni.

**COMPORTAMENTO ATTUALE V1**
Chiunque autenticato può far regredire qualunque pratica a qualunque stato, senza traccia del motivo (solo old/new value generico in `practice_history`).

**IMPATTO V2**
Con una FSM esplicita, se le regressioni non sono elencate diventano impossibili by design — cambierebbe il comportamento reale rispetto a oggi.

**OPZIONE A — Vietare le regressioni (solo avanti)**
Vantaggi: massima integrità, elimina errori accidentali.
Svantaggi: un errore di battitura richiede intervento admin/DB per essere corretto.
Impatto sui dati storici: nessuno (riguarda solo pratiche future).
Impatto operativo: gli operatori che oggi "tornano indietro" per correggere un errore devono usare una procedura diversa.

**OPZIONE B — Regressioni permesse solo ad Admin, motivo obbligatorio**
Vantaggi: mantiene flessibilità per correggere errori reali, introduce tracciabilità che oggi manca.
Svantaggi: dipendenza dall'admin per correzioni che oggi un operatore fa da solo — possibile collo di bottiglia.
Impatto sui dati storici: nessuno.
Impatto operativo: operatori devono chiedere ad admin.

**EVENTUALI ALTRE OPZIONI**
Opzione C — Regressioni permesse a chiunque, ma sempre con motivo obbligatorio tracciato in `audit_log` (nessuna restrizione di ruolo). Vantaggi: nessun impatto sulla velocità operativa attuale, introduce audit reale dove oggi non c'è. Svantaggi: non impedisce l'errore, solo lo rende tracciabile dopo.

**TUA RACCOMANDAZIONE TECNICA**
Opzione C come base minima: è il livello minimo per rispettare "audit richiesto per ogni operazione sensibile" già deciso nel progetto, senza toccare la libertà operativa che il team ha oggi. Se le regressioni "sbagliate" sono un problema reale vissuto, l'opzione B è più sicura, ma solo tu sai come lavora concretamente il tuo team.

**DECISIONE RICHIESTA DA ME**
Le pratiche possono tornare a uno stato precedente? Se sì, chi può farlo (chiunque con motivo tracciato, o solo Admin)?

---

## DECISIONE #2 — Salti di stato Pratica per origini/tipologie particolari
**Classificazione: A**

**FACT**
Il default alla creazione è `"Ritirato"` (app.py:16945, `initial=f.get("status","Ritirato")`), ma il form può impostare qualunque altro valore esplicitamente, senza distinzione per `request_origin`/`service_type`.

**PROBLEMA**
Pratiche con origine diversa da un Ritiro reale (es. `Collaboratore`, `Consegna in sede`) potrebbero non passare mai realisticamente da "Ritirato" — va capito se serve un punto di ingresso diverso per questi casi.

**COMPORTAMENTO ATTUALE V1**
Nessuna regola dichiarata: qualunque stato iniziale è accettato dal form.

**IMPATTO V2**
Se la FSM impone un solo punto di ingresso (`ritirato`), le pratiche che oggi nascono realisticamente in uno stato diverso potrebbero non poter essere create correttamente.

**OPZIONE A — Un solo punto di ingresso (`ritirato`) per tutte le pratiche**
Vantaggi: FSM semplice, un solo caso da testare.
Svantaggi: può essere macchinoso/irrealistico per pratiche che oggi nascono già "avanti" nel processo.
Impatto sui dati storici: nessuno.
Impatto operativo: possibile passaggio extra per gli operatori su alcune tipologie di pratica.

**OPZIONE B — Punti di ingresso multipli, dichiarati per `request_origin`/`service_type`**
Vantaggi: riflette la realtà operativa.
Svantaggi: FSM più complessa, richiede sapere esattamente quali origini iniziano dove.
Impatto sui dati storici: nessuno.
Impatto operativo: nessun cambiamento per gli operatori rispetto a oggi.

**TUA RACCOMANDAZIONE TECNICA**
Opzione B, ma serve da te l'elenco reale — non è deducibile dal codice V1, che permette tutto senza distinguere.

**DECISIONE RICHIESTA DA ME**
Esistono tipologie di pratica che oggi nascono realisticamente in uno stato diverso da "Ritirato"? Se sì, quali e in che stato?

---

## DECISIONE #3 — Ruoli su transizioni stato Pratica
**Classificazione: A**

**FACT**
Nessuna distinzione Admin/Operator su nessuna transizione di stato pratica in V1.

**PROBLEMA**
Se introdurre restrizioni di ruolo su alcune transizioni (specialmente quelle "sensibili" risultanti dalle decisioni #1/#2).

**COMPORTAMENTO ATTUALE V1**
Operator e Admin hanno lo stesso potere su tutte le transizioni.

**IMPATTO V2**
doc09 introduce già un meccanismo centralizzato di permessi (`require_role`), tecnicamente pronto — ma nessuna distinzione è ancora stata decisa per questo dominio.

**OPZIONE A — Nessuna distinzione (comportamento invariato)**
Vantaggi: zero attrito operativo aggiuntivo.
Svantaggi: nessuno rispetto a oggi.
Impatto sui dati storici: nessuno. Impatto operativo: nessuno.

**OPZIONE B — Solo Admin sulle transizioni "sensibili" (regressioni/salti)**
Vantaggi: maggior controllo su operazioni a rischio.
Svantaggi: possibile collo di bottiglia se serve spesso.
Impatto sui dati storici: nessuno. Impatto operativo: dipende dalla frequenza reale di queste operazioni.

**TUA RACCOMANDAZIONE TECNICA**
Opzione A come default — introdurre restrizioni senza un bisogno operativo dimostrato aggiungerebbe attrito senza beneficio provato; si può sempre aggiungere in seguito se emerge un caso reale.

**DECISIONE RICHIESTA DA ME**
Oltre a quanto deciderai per #1/#2, vuoi restrizioni di ruolo aggiuntive sulle transizioni di stato Pratica?

---

## DECISIONE #4 — Ritiro "ritirato"→"annullato" con pratica già collegata
**Classificazione: A**

**FACT**
V1 lo permette senza alcun controllo, anche con `linked_practice_id` valorizzato (doc 03).

**PROBLEMA**
Cosa deve succedere alla pratica collegata quando il Ritiro viene annullato dopo che la pratica esiste già.

**COMPORTAMENTO ATTUALE V1**
L'evento passa ad "Annullato", la pratica collegata resta esattamente com'era — nessuna notifica, nessuno scollegamento.

**IMPATTO V2**
Senza una regola esplicita, la pratica rischia di restare concettualmente "orfana" (collegata a un ritiro annullato) senza che nessuno se ne accorga.

**OPZIONE A — Bloccare la transizione se esiste già una pratica collegata**
Vantaggi: impossibile creare l'incoerenza.
Svantaggi: può bloccare un caso legittimo (ritiro davvero annullato dopo pratica creata per errore).
Impatto sui dati storici: nessuno. Impatto operativo: un operatore deve prima gestire/scollegare la pratica.

**OPZIONE B — Permettere la transizione con avviso esplicito, nessuna azione automatica sulla pratica**
Vantaggi: flessibile, nessun dato perso, coerente col pattern già scelto per la Riconsegna (doc 06 Addendum P).
Svantaggi: la pratica resta collegata a un evento annullato finché qualcuno non interviene manualmente.
Impatto sui dati storici: nessuno. Impatto operativo: minimo, un solo click di conferma in più.

**TUA RACCOMANDAZIONE TECNICA**
Opzione B — stesso pattern già stabilito per la Riconsegna (avviso esplicito, mai un blocco rigido né un'azione automatica silenziosa), coerente con l'intero impianto del progetto.

**DECISIONE RICHIESTA DA ME**
Quando un Ritiro con pratica collegata viene annullato, la pratica deve restare invariata (solo avviso), oppure vuoi un'altra azione (es. scollegamento automatico, blocco totale)?

---

## DECISIONE #5 — Ritiro: "annullato" terminale o riapribile
**Classificazione: A**

**FACT**
Nessun vincolo in V1 — nulla impedisce di tornare da "annullato" a un altro stato.

**PROBLEMA**
Se in pratica capita di dover riaprire un ritiro annullato per errore.

**COMPORTAMENTO ATTUALE V1**
Possibile senza restrizioni.

**IMPATTO V2**
Se "annullato" è dichiarato terminale, riaprirlo richiede creare un nuovo evento invece di correggere quello esistente.

**OPZIONE A — Terminale (nessuna transizione in uscita)**
Vantaggi: semantica pulita, "annullato" significa davvero chiuso.
Svantaggi: perdita di comodità se l'annullamento per errore è frequente.
Impatto sui dati storici: nessuno. Impatto operativo: un evento va ricreato da zero se annullato per errore.

**OPZIONE B — Riapribile verso `da_confermare`, tracciato in audit_log**
Vantaggi: comodo per correggere errori operativi.
Svantaggi: indebolisce la semantica di "annullato" come stato definitivo.
Impatto sui dati storici: nessuno. Impatto operativo: nessuno, resta come oggi.

**TUA RACCOMANDAZIONE TECNICA**
Opzione A (terminale) — semantica più pulita e più facile da ragionare; se gli annullamenti per errore sono frequenti nella pratica quotidiana, l'opzione B è ragionevole, ma solo la tua esperienza diretta lo sa.

**DECISIONE RICHIESTA DA ME**
"Annullato" deve essere uno stato definitivo, o capita di doverlo riaprire nella pratica quotidiana?

---

## DECISIONE #6 — Cambio `event_type` a metà flusso
**Classificazione: B — RISOLTA COME DECISIONE TECNICA, nessuna azione richiesta da te**

**FACT**
Oggi il cambio di `event_type` può resettare silenziosamente `event_status` senza toccare `linked_practice_id` (doc 03) — comportamento accidentale, non una regola mai dichiarata.

**DECISIONE TECNICA PRESA**
Il cambio di `event_type` **non azzera mai silenziosamente lo stato**. Se il nuovo tipo non ammette lo stato corrente, l'operatore deve scegliere esplicitamente il nuovo stato prima di confermare (nessun default automatico); `linked_practice_id` non viene mai toccato da un cambio di tipo.

**Motivazione**: applica lo stesso principio già usato ovunque in questo progetto — mai un comportamento silenzioso/degradato — senza introdurre alcuna nuova regola di *business*, solo rendendo esplicito ciò che oggi accade per caso. Non è una decisione che cambia il significato dei dati aziendali, quindi presa autonomamente.

---

## DECISIONE #7 — DDT/trasporto/tracciabilità: obblighi di conservazione
**Classificazione: D — possibili implicazioni normative, richiede cautela**

**FACT TECNICO** (verificato)
`transport_method`, `vehicle_plate`, `temperature_mode`, `package_count`, `container_id`, `lot_number`, `treatment_method`, `identity_document_number`, `identity_document_date`, `signing_place` esistono e sono attivamente usati nel form pratica V1 (doc 13 §2.1.2).

**REQUISITO NORMATIVO**
**NON verificabile da questa sessione.** Non ho accesso a una fonte normativa italiana specifica su tracciabilità del trasporto di sottoprodotti/animali deceduti né su conservazione di documenti d'identità raccolti in un DDT. **Non affermo che esista un obbligo** — affermo solo che il *tipo* di dato (targa veicolo, lotto, temperatura, documento d'identità) è tipicamente soggetto a regolamentazione in ambiti simili (trasporto rifiuti speciali/sottoprodotti di origine animale). Questa è un'ipotesi, non un fatto.

**ASSUNZIONE**
Nessuna presa. Punto esplicitamente lasciato aperto, come richiesto.

**IMPATTO V2**
Se esiste un obbligo di conservazione minima, questi campi non potranno mai essere cancellati prima di quel termine, nemmeno su richiesta di cancellazione pratica (oggi gestita con `SET NULL` sulle sole fatture — andrebbe eventualmente estesa anche a questi campi).

**OPZIONE A — Conservazione indefinita (default già scelto in doc 06)**
Nessuna cancellazione automatica finché non arriva una risposta certa.
Impatto sui dati storici: nessuno, tutto preservato.
Impatto operativo: nessuno.

**OPZIONE B — Verifica con consulente/commercialista, poi regola di retention esplicita**
Da fare quando disponibile una risposta certa.

**NOTA COLLEGATA (categoria C, verifica sui dati reali)**: `transport_method`/`temperature_mode`/`treatment_method` potrebbero essere vocabolari chiusi (pochi valori ricorrenti) invece di testo libero — da verificare sui valori realmente presenti in produzione prima di finalizzare se diventano `ENUM` o restano `TEXT`.

**TUA RACCOMANDAZIONE TECNICA**
Opzione A come default operativo immediato — è la scelta più sicura finché non c'è una risposta normativa certa, ed è già quella in vigore in doc 06. Non prendo posizione su un obbligo legale che non sono in grado di verificare.

**DECISIONE RICHIESTA DA ME** *(non tecnica — verifica di conformità)*
Hai già verificato con un commercialista/consulente se questi dati hanno un obbligo di conservazione specifico? Se sì, qual è?

---

## DECISIONE #8 — Provenienza/origine: logica di fallback reale
**Classificazione: A**

**FACT**
`pickup_address_mode`/`origin_mode`/`origin_text`/`transporter_mode` oggi vivono solo nel codice, nessuna regola dichiarata in alcun documento (doc 06 Addendum C).

**PROBLEMA**
Qual è la vera logica di business per determinare indirizzo/contatto di origine di un ritiro.

**COMPORTAMENTO ATTUALE V1**
4 colonne parzialmente sovrapposte, "quale vince" dipende da quale è valorizzata — stesso pattern ambiguo già visto (e già corretto) per il circuito W/D.

**IMPATTO V2**
Il campo `origin_type` proposto (doc 06 Addendum C) è pronto ad accogliere la regola, ma resta un guscio vuoto senza la regola reale.

**OPZIONE A — Tu descrivi la logica reale, la traduco in regola**
Vantaggi: veloce, affidabile — rappresenta il processo *corretto*, non solo quello che il codice fa oggi.
Svantaggi: nessuno.

**OPZIONE B — Ricostruire la logica leggendo tutto il codice che tocca questi 4 campi**
Vantaggi: non richiede il tuo tempo.
Svantaggi: più lento, rischio concreto di dedurre "cosa fa il codice" invece di "cosa è corretto che accada" — esattamente l'errore che questo intero progetto vuole evitare (V1 come riferimento funzionale, mai come modello architetturale).

**TUA RACCOMANDAZIONE TECNICA**
Opzione A — più veloce e più affidabile: tu conosci il processo reale, il codice V1 può solo confermare "cosa succede oggi".

**DECISIONE RICHIESTA DA ME**
Puoi descrivermi in 3-4 frasi come si determina oggi, nella pratica reale, l'indirizzo/contatto di origine di un ritiro (domicilio cliente / veterinario / collaboratore / altro)?

---

## DECISIONE #9 — Disposal batches: rilevanza per tracciabilità
**Classificazione: D — possibili implicazioni normative, richiede cautela**

**FACT TECNICO**
`disposal_batches`/`disposal_batch_practices` registrano lotti di smaltimento collettivo con `breakdown_json` (doc 13 §2.2).

**REQUISITO NORMATIVO**
Stesso discorso della Decisione #7: **non verificabile da questa sessione**. Ipotesi non confermata di rilevanza compliance (smaltimento rifiuti speciali/sottoprodotti animali), non un fatto.

**ASSUNZIONE**: nessuna presa.

**OPZIONE A — Conservazione indefinita (default)**
**OPZIONE B — Verifica con consulente, poi regola di retention esplicita**

**TUA RACCOMANDAZIONE TECNICA**
Stessa della Decisione #7 — opzione A come default sicuro.

**DECISIONE RICHIESTA DA ME** *(non tecnica — verifica di conformità)*
Hai verificato obblighi di conservazione specifici per i lotti di smaltimento collettivo?

---

## DECISIONE #10 — Procedura di correzione per un ciclo di cremazione "completato" per errore
**Classificazione: B — RISOLTA COME DECISIONE TECNICA, nessuna azione richiesta da te**

**FACT**
doc 14 preserva già `completato → in_attesa` come transizione tecnica valida (`revert_complete`, comportamento V1 già corretto — riporta anche le pratiche collegate allo stato precedente noto).

**DECISIONE TECNICA PRESA**
Non serve una procedura "alternativa" separata: la correzione di un ciclo completato per errore avviene tramite il percorso di ripristino **già esistente e già preservato** — `completato → in_attesa`, poi correzione (es. riassegnazione animali), poi ri-completamento. L'unica cosa vietata resta l'**eliminazione diretta** del ciclo mentre è `completato` (il bug noto in V1, doc 03/04) — che ora richiede prima il ripristino esplicito.

**Motivazione**: il meccanismo di correzione richiesto esiste già nel comportamento V1 corretto e già confermato preservabile — non serve inventare nulla di nuovo, solo vietare la scorciatoia pericolosa (cancellazione diretta). Non cambia il significato dei dati né il funzionamento aziendale, quindi presa autonomamente.

---

## DECISIONE #11 — Limite di 2 animali per ciclo di cremazione
**Classificazione: A**

**FACT**
Imposto a livello di codice in V1 (doc 03), nessun vincolo fisico dichiarato nel database.

**PROBLEMA**
Se il limite riflette un vincolo fisico reale (capacità del forno) o è una scelta software che potrebbe cambiare.

**COMPORTAMENTO ATTUALE V1**
Hard-coded a 2, nessuna configurabilità.

**IMPATTO V2**
Se è un vincolo fisico, va mantenuto come regola di dominio; se è arbitrario, potrebbe convenire renderlo configurabile.

**OPZIONE A — Mantenere 2 come limite fisso**
Vantaggi: nessun cambiamento, zero rischio.
Svantaggi: nessuna flessibilità futura (es. nuova sede con forno diverso).
Impatto sui dati storici: nessuno. Impatto operativo: nessuno.

**OPZIONE B — Renderlo configurabile (es. per sede)**
Vantaggi: pronto per scenari futuri (sedi con forni di capacità diversa).
Svantaggi: complessità aggiuntiva non necessaria oggi se il numero non cambierà mai.
Impatto sui dati storici: nessuno. Impatto operativo: nessuno nell'immediato.

**TUA RACCOMANDAZIONE TECNICA**
Opzione A come default a basso rischio — se il limite è legato alla capacità fisica reale e non cambierà, non c'è motivo di aggiungere complessità ora; si generalizza più avanti solo se emerge un bisogno concreto.

**DECISIONE RICHIESTA DA ME**
Il limite di 2 animali riflette la capacità fisica reale del forno, o è un numero scelto in altro modo? Resta 2 anche in V2?

---

## Rappresentazione delle transizioni interessate dalle decisioni aperte

> Formato richiesto: STATO ATTUALE → AZIONE → STATO SUCCESSIVO, con chi può eseguirla, prerequisiti, effetti, audit, possibilità di annullamento. Solo le transizioni toccate dalle decisioni #1-#5 — il grafo completo resta in `14-macchine-stati-transizioni.md`.

```
PRATICA — decisioni #1, #2, #3

  [qualunque stato avanzato]
        │
        │ AZIONE: "torna allo stato precedente" ────── ⚠️ decisione #1: permessa? da chi?
        ▼
  [stato precedente]
        Prerequisiti: nessuno definito (dipende dalla tua risposta #1)
        Effetti: nessuno automatico previsto
        Audit: SEMPRE richiesto (audit_log, riga 09 doc09), indipendentemente dalla risposta
        Annullabile: sì, è essa stessa un "annullamento" di una transizione precedente

  [creazione pratica]
        │
        │ AZIONE: "assegna stato iniziale" ───────────── ⚠️ decisione #2: sempre 'ritirato'?
        ▼
  ritirato  (default oggi, FACT)  oppure  [stato alternativo da definire per origine/tipo]


RITIRO — decisioni #4, #5

  ritirato ──(pratica collegata: sì)──► AZIONE: "annulla ritiro" ⚠️ decisione #4
        │
        ▼
  annullato
        Prerequisiti: nessuno oggi; proposta tecnica = avviso esplicito se pratica collegata
        Effetti sulla pratica collegata: DA DECIDERE (#4) — proposta: nessuno automatico
        Audit: sempre richiesto
        Annullabile: ⚠️ decisione #5 — "annullato" è terminale o riapribile?
```

---

## Riepilogo classificazione finale (aggiornato)

| # | Decisione | Classe | Stato |
|---|---|---|---|
| 1 | Regressioni stato Pratica | A | ✅ **Chiusa** — doc 14 §1 |
| 2 | Salti di stato Pratica | A | ✅ **Chiusa implicitamente** — doc 14 §1 |
| 3 | Ruoli su transizioni Pratica | A | ✅ **Chiusa** — doc 14 §1 |
| 4 | Ritiro annullato con pratica collegata | A | ✅ **Chiusa** — doc 14 §2 |
| 5 | Ritiro "annullato" terminale/riapribile | A | ✅ **Chiusa** — doc 14 §2 |
| 6 | Cambio `event_type` a metà flusso | B | ✅ **Chiusa** — decisione tecnica presa |
| 7 | DDT/trasporto — retention normativa | D | 🔶 **VERIFICA NORMATIVA PENDENTE** — decisione provvisoria: conservare tutto (scelta tecnica prudenziale, non obbligo accertato) |
| 8 | Provenienza/origine — logica di fallback | A | ✅ **Chiusa** — doc 06 Addendum C (riscritto) |
| 9 | Disposal batches — retention normativa | D | 🔶 **VERIFICA NORMATIVA PENDENTE** — stessa decisione provvisoria di #7 |
| 10 | Correzione ciclo completato | B | ✅ **Chiusa** — decisione tecnica presa |
| 11 | Limite 2 animali per ciclo | A | ✅ **Chiusa** — doc 14 §4 |
| — | Relazione Ritiro → Pratica (nuova) | A | ✅ **Chiusa** — doc 06 §"Relazione Ritiro → Pratica" |
| — | Chi può annullare un Ritiro (nuova, round 3) | A | ✅ **Chiusa** — doc 14 §2 |
| — | Pratiche create direttamente, stato iniziale (nuova, round 3) | A | ✅ **Chiusa** — doc 06, doc 14 §1, doc 09 |

**Tutte le decisioni sono ora chiuse**, tranne le due normative (#7, #9) che restano esplicitamente **VERIFICA NORMATIVA PENDENTE** — non bloccanti per lo sviluppo (la decisione provvisoria "conservare tutto" è già operativa), ma non vanno mai descritte come un obbligo di legge accertato finché non arriva una verifica reale con un consulente/commercialista.
