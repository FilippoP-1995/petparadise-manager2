# 12 — Piano di Rilascio (V2)

> Deriva da tutti i documenti precedenti (06→11). Nessuna scadenza fissa (decisione esplicita dell'utente) — questo è un ordine di fasi con criteri di uscita verificabili, non un calendario. Priorità dichiarata e invariata: correttezza → integrità dati → sicurezza → stabilità → performance → funzionalità → UX → velocità di sviluppo.

## Chiarimento preliminare — cosa significa "V1 e V2 devono coesistere"

**ASSUMPTION esplicitata per evitare un fraintendimento architetturale**: l'istruzione dell'utente è interpretata come *"V1 resta pienamente disponibile e usato normalmente durante tutto lo sviluppo di V2"* (facile: non si tocca mai la produzione V1) — **non** come *"V1 e V2 sono entrambi scritti in produzione dagli operatori reali contemporaneamente per un periodo prolungato"* (difficile: richiederebbe sincronizzazione bidirezionale in tempo reale, complessità sproporzionata già scartata esplicitamente in doc 07). Se questa lettura non è quella intesa, va corretta esplicitamente prima della Fase 10 — l'intero piano di parallel-run sotto assume la prima interpretazione.

## Fasi

### Fase 1 — Audit (COMPLETATA)
Documenti 01-05. Inventario DB/backend/frontend/entità/problemi noti.

### Fase 2 — Modello dati V2 (COMPLETATA)
Documento 06, stabile, decisioni confermate dall'utente.

### Fase 3 — Strategia di migrazione, backup/rollback, architetture (COMPLETATA in questa sessione)
Documenti 07, 08, 09, 10, 11 (questo documento è la Fase 3 stessa, il piano di rilascio).

**Criterio di uscita Fase 3**: tutti i documenti 06-12 esistono, sono coerenti tra loro, e l'utente li ha revisionati/approvati (o corretti dove necessario) — **nessun codice applicativo V2 scritto prima di questo criterio**, per esplicita decisione 17.

### Fase 4 — Backend/dominio (setup + primo dominio verticale)
- Setup progetto FastAPI (struttura doc 09), CI (lint, type-check, test), ambiente Postgres di sviluppo/staging separato da V1 (nessun collegamento a V1 se non in lettura per la migrazione).
- Primo dominio implementato end-to-end (API+domain+repository+test) come **prova di architettura**, scelto per rischio basso e valore di validazione alto: **Clienti/Veterinari** (entità più semplici, poche regole di stato, permette di validare l'intera pipeline — auth, permessi, repository, migrazione parziale — senza il rischio delle regole finanziarie).
- **Criterio di uscita**: dominio Clienti/Veterinari completo, testato (doc 11 livelli 1-3), con la propria fetta di migrazione (doc 07) verificata su staging.

### Fase 5 — Domini restanti, in ordine di rischio crescente
Ordine consigliato (dal meno al più rischioso, così gli errori di architettura si scoprono su domini a basso impatto prima di arrivare a quelli finanziari):
1. Sedi/Urne/Articoli (dati di riferimento, quasi statici).
2. Pratiche (senza ancora pagamenti/fatture — solo anagrafica pratica, animali, tag).
3. Ritiro/Riconsegna/Calendario (macchine a stati, notifiche).
4. Ciclo di cremazione.
5. Fatture/Pagamenti (il dominio più delicato — unico punto con la logica finanziaria centralizzata, doc 06 decisione 14).

Ogni dominio segue lo stesso schema della Fase 4: implementazione + test + migrazione parziale verificata su staging, **prima** di passare al successivo.

### Fase 6 — Test automatici completi
Suite completa secondo doc 11 su tutti i domini, non solo i singoli via via completati — verifica di interazioni cross-dominio (es. cancellazione pratica con fattura collegata, doc 06 decisione sul `SET NULL`).

### Fase 7 — Prima sezione frontend
Stessa logica "a basso rischio prima": si parte dalla sezione **Clienti/Veterinari** (stesso dominio a basso rischio scelto in Fase 4), per validare l'intera pipeline React+TanStack Query+contratto OpenAPI end-to-end su un caso semplice prima di affrontare il form Pratica (il più complesso, doc 10).

### Fase 8 — Integrazione frontend completa
Tutte le sezioni frontend, inclusi i flussi critici: creazione pratica con bozza persistente, gestione ciclo di cremazione, pagamenti con circuito esplicito, notifiche push, PWA offline.

### Fase 9 — Migrazione dati verificata (dry-run completo)
Esecuzione completa della migrazione (doc 07, Fase A+B) su un backup recente e reale di produzione V1, con la suite di verifica automatica (doc 11) a zero discrepanze. Ripetuta più volte se necessario finché non è stabile — **nessun limite al numero di tentativi**, dato che non c'è scadenza fissa.

### Fase 10 — Shadow mode / confronto V1↔V2
Come descritto in doc 11: confronto batch offline tra V1 e V2 alimentati dagli stessi dati migrati, su scenari applicativi reali. Discrepanze indagate e chiuse. Questo è il "parallel run" nel senso realmente sostenibile (non dual-write live).

**Criterio di uscita**: tutti i criteri di uscita di doc 11 soddisfatti simultaneamente.

### Fase 11 — Passaggio definitivo
- Comunicazione della finestra di taglio agli operatori (breve, es. 15-30 minuti, doc 07).
- Ultimo backup V1 → ultima migrazione → ultima verifica → switch del traffico operativo a V2.
- **V1 resta installata e raggiungibile** (non spenta) per un periodo di sicurezza post-taglio (es. 2-4 settimane, doc 08) prima di essere considerata dismessa definitivamente.
- Monitoraggio attivo nei primi giorni post-taglio (errori applicativi, tempi di risposta, segnalazioni operatori) — rollback verso V1 resta un'opzione reale finché V1 non viene dismessa.

## Criteri di rollback per fase

Ogni fase (4-10) ha un criterio di rollback banale: si continua a usare V1, si corregge il problema trovato in staging, si ripete la fase — **mai** un rollback che richieda di toccare dati di produzione, perché nessuna fase fino alla 11 tocca la produzione V1.

Solo la Fase 11 ha un rollback non banale (descritto in doc 08, sezione C/D) — per questo è l'unica fase con una finestra temporale comunicata e concordata, non un deploy silenzioso come le precedenti.

## Nota finale — cosa NON è in questo piano

Non è definita qui una data di completamento delle Fasi 4-10, per decisione esplicita dell'utente (nessuna scadenza fissa, priorità a correttezza/integrità dati/sicurezza sopra la velocità). Il piano definisce **l'ordine e i criteri di uscita verificabili di ogni fase**, non un calendario: si passa alla fase successiva quando la precedente è verificata, non quando una data è arrivata.
