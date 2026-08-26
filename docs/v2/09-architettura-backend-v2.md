# 09 — Architettura Backend V2 (FastAPI)

> Deriva da `06-modello-dati-v2.md`. Decisione già presa dall'utente: Python + FastAPI, ORM scelto e motivato da me. **Nessun codice in questo documento — solo struttura, scelte, motivazioni.**

## Scelta dell'ORM — DECISION: SQLAlchemy 2.0 (async) + Alembic

Confrontato con le alternative mature disponibili oggi:

| Opzione | Perché scartata / accettata |
|---|---|
| **SQLAlchemy 2.0 async + Alembic** | **Scelta.** Standard de-facto Python da 15+ anni, sintassi 2.0 tipizzata (non più la vecchia query-string-style), supporto async nativo (compatibile con FastAPI async), Alembic è lo strumento di migrazione più maturo dell'ecosistema (già scelto in doc 07/08 per le migrazioni schema — usare lo stesso stack per ORM e migrazioni evita di avere due strumenti diversi che devono restare sincronizzati manualmente). Documentazione e community enormi: per un progetto che deve restare mantenibile "per anni" (obiettivo esplicito dell'utente), la maturità dello strumento conta più della sintassi più moderna di un'alternativa più giovane. |
| **SQLModel** | Scartata. Progetto dello stesso autore di FastAPI, sintassi più comoda (un solo modello per ORM+validazione Pydantic), ma **meno maturo**, sviluppo storicamente più lento/meno prevedibile, e la fusione ORM+schema-di-validazione in un'unica classe è comoda per prototipi ma **meno adatta a un'architettura a layer separati** come richiesto esplicitamente dall'utente (modelli DB e schemi API devono poter divergere: es. un campo interno non esposto in API, o una risposta che aggrega più tabelle). |
| **Tortoise ORM** | Scartata. Nato per lo stile "Django-like" async, community più piccola, meno strumenti di migrazione maturi (Aerich è molto meno usato/testato di Alembic). |
| **Query grezze / query builder minimale** | Scartata come scelta di base (rimane un'opzione **puntuale** per query di reportistica complesse dove l'ORM aggiunge overhead senza benefico — SQLAlchemy Core, non stringhe SQL a mano, per restare comunque type-safe e parametrizzato). Usare solo SQL a mano ovunque riprodurrebbe lo stesso problema di V1 (query sparse, nessuna astrazione, nessuna garanzia sui tipi). |

## Struttura a layer

```
backend/
  api/            # routing HTTP puro — riceve richieste, chiama servizi, restituisce schemi. ZERO business logic qui.
    routes/
      practices.py
      pickups.py
      deliveries.py
      cremations.py
      invoices.py
      payments.py
      calendar.py
      auth.py
      ...
    dependencies.py   # auth/permessi/paginazione condivisi, iniettati via Depends()
  domain/         # business logic pura, NESSUNA dipendenza da FastAPI/DB — testabile in isolamento
    practice/
      state_machine.py   # stati/transizioni pratica, validazione, side effect dichiarati (non eseguiti qui)
      rules.py            # regole di business (es. "una pratica non può avere due fatture con lo stesso numero")
    pickup/
    delivery/
    cremation_cycle/
    payment/
      ledger.py            # logica centralizzata "importo pagato = SUM(payments)", channel esplicito W/D
  services/       # orchestrazione: usa domain + repositories per eseguire un caso d'uso completo,
                  # gestisce le transazioni, chiama i side-effect (notifiche, audit_log)
    create_practice.py
    register_payment.py
    transition_pickup_status.py
    ...
  repositories/   # unico punto di accesso a SQLAlchemy per ogni entità — nessuna query SQLAlchemy fuori da qui
    practice_repository.py
    payment_repository.py
    ...
  models/         # modelli SQLAlchemy (mappano 1:1 lo schema V2 di 06-modello-dati-v2.md)
  schemas/        # modelli Pydantic per request/response API — DIVERSI dai models, mai riusati direttamente
  auth/           # sessioni, hashing password, permessi/ruoli
  jobs/           # job schedulati (backup, whatsapp, notifiche, promemoria) — vedi sezione dedicata sotto
  integrations/   # client verso servizi esterni (WhatsApp, Firebase/APNs push, storage B2) — nessuna business logic
  config.py       # pydantic-settings, configurazione tipizzata da env var
  main.py         # crea l'app FastAPI, monta i router, middleware — resta piccolo, MAI un nuovo "app.py" monolitico
tests/
  unit/           # domain/ testato senza DB
  integration/    # repositories/services testati contro un Postgres reale (non mock)
  api/            # routes testate con TestClient/httpx AsyncClient
alembic/
  versions/
```

**Regola vincolante** (diretta applicazione della richiesta esplicita dell'utente "mai un nuovo app.py enorme"): **nessuna riga di business logic dentro `api/routes/*`**. Una route fa solo: valida input (Pydantic, automatico), chiama un servizio, mappa il risultato in uno schema di risposta. Se una route inizia a contenere `if`/logica di stato, è un segnale che quella logica appartiene a `domain/` o `services/` e va spostata.

### Regola vincolante — atomicità modifica di dominio + audit (condizione Architecture Gate, doc 13 §1)

**DECISION (obbligatoria, non derogabile)**: **ogni operazione di dominio che produce una modifica persistente e il relativo `audit_log` devono essere scritti nella stessa transazione database. Non sono ammesse transazioni separate tra modifica di dominio e audit.**

- **Perché**: se la scrittura di dominio e la riga di `audit_log` fossero in due transazioni distinte, un fallimento tra le due produrrebbe esattamente l'incoerenza che l'intero progetto V2 vuole eliminare — un cambio di stato realmente avvenuto ma senza traccia di chi/quando/perché, indistinguibile da un audit_log incompleto per bug.
- **Come si applica in pratica**: nello strato `services/` (mai in `domain/`, che resta puro e senza I/O, né in `api/routes`), ogni caso d'uso che modifica un'entità apre **un solo blocco transazionale** che contiene sia la scrittura sul repository dell'entità sia l'insert su `audit_log`. Se una delle due fallisce, la transazione fa rollback su entrambe — mai un commit parziale.
- **Applicabilità**: regola trasversale, vale per **ogni** entità V2 presente e futura (non solo Pratiche/Pagamenti) — inclusi i domini a basso rischio come Clienti/Veterinari se in futuro sviluppano un proprio audit.
- **Verifica**: un test di integrazione dedicato per ogni service che tocca `audit_log` deve simulare un fallimento a metà operazione (es. vincolo violato sull'insert di dominio) e verificare che **nessuna riga di audit orfana resti committata**.

## Macchine a stati esplicite (Decisione 13 dell'utente)

Ogni entità con stati (Pratica, Ritiro, Riconsegna, Ciclo di cremazione) ha in `domain/<entità>/state_machine.py`:
- L'elenco chiuso degli stati (stesso Enum usato anche come tipo colonna Postgres, vedi doc 06).
- Una tabella esplicita delle transizioni permesse (`{stato_attuale: {evento: stato_successivo}}`), **mai** un `if` sparso che decide implicitamente se una transizione è permessa.
- Per ogni transizione: chi può eseguirla (quale ruolo), quali side-effect dichiara (es. "Riconsegna→Completata genera una riga di audit e chiude il ciclo di cremazione collegato se presente") — dichiarati come dati/callback registrati, eseguiti poi dal service layer dentro la stessa transazione.
- Un tentativo di transizione non presente nella tabella solleva un errore di dominio esplicito (`InvalidTransitionError`), mai un semplice "non succede nulla" silenzioso come spesso avviene oggi in V1.

### Regola vincolante — lo stato iniziale non è mai un parametro di creazione (decisione aziendale, doc 14 §1)

**DECISION (obbligatoria, non derogabile)**: gli schemi Pydantic di **creazione** di un'entità con macchina a stati (`PracticeCreate`, `PickupCreate`, ecc.) **non includono mai un campo `status`/`stato`**. Lo stato iniziale è sempre hardcoded nel service layer (`create_practice` imposta sempre `status='ritirato'`, per entrambi i percorsi di creazione — da Ritiro o diretta, doc 06 §"Relazione Ritiro → Pratica"), mai letto da un valore fornito dal chiamante API.

- **Perché**: chiude alla radice l'esatto comportamento V1 già documentato come FACT in doc 14 (`initial=f.get("status","Ritirato")`, che accetta qualunque valore passato dal form) — in V1 è tecnicamente possibile creare una pratica già in uno stato avanzato semplicemente passando un parametro diverso. In V2 questo non è un problema di validazione da controllare a runtime, è strutturalmente impossibile: il campo non esiste nello schema di input, quindi non c'è alcun valore da validare o rifiutare.
- **Applicabilità**: stessa regola per ogni entità con FSM (Pratica, Ritiro) — nessuna eccezione "per comodità" in nessun endpoint di creazione, incluse eventuali API di importazione/migrazione (che devono usare un percorso esplicitamente diverso e verificato, mai lo stesso endpoint di creazione con un parametro extra).

## Autenticazione — DECISION: sessioni server-side, non JWT

- **Motivazione**: il gestionale è uso interno di un piccolo team, non un'API pubblica multi-tenant. Le sessioni server-side (rivedibili/invalidabili istantaneamente lato server — es. "disconnetti tutti", cambio password che invalida le sessioni esistenti) sono più semplici da ragionare e più sicure operativamente di JWT stateless, il cui vantaggio principale (nessuno stato lato server, scalabilità orizzontale senza sticky session) non è un problema reale per questo carico.
- Tabella `sessions` in Postgres (id opaco, `user_id`, `expires_at`, `created_at`, `last_seen_at`, `ip`, `user_agent`) — **con scadenza reale**, a differenza di V1 dove l'audit ha rilevato che le sessioni non scadono mai lato server. TTL configurabile (es. 30 giorni di inattività), rinnovato ad ogni richiesta autenticata.
- Permessi: **centralizzati** in `auth/permissions.py` con un'unica funzione `require_role(role)` usata come `Depends()` FastAPI su ogni route protetta — elimina i ~20 controlli `role != "admin"` sparsi nel codice già rilevati nell'audit V1 (`01-architettura-attuale.md`).

## Job schedulati — DECISION: APScheduler in-process, non Celery+broker

- **Motivazione**: il volume di job schedulati oggi è piccolo e noto (WhatsApp queue, notifiche/promemoria, backup) — introdurre Celery+Redis (un broker in più da hostare, monitorare, backuppare) sarebbe complessità sproporzionata rispetto al bisogno reale, in diretto contrasto con la regola dell'utente "mai una soluzione più complessa di quella necessaria".
- Con Postgres disponibile (a differenza di V1/SQLite), diventa comunque possibile in futuro passare a un vero task queue (es. basato su tabella Postgres, pattern "job queue in SQL", senza nemmeno bisogno di Redis) se il volume crescesse — opzione lasciata aperta, non implementata ora perché non necessaria oggi.
- **Vincolo Render invariato** (stesso della cron già esistente): se un job resta schedulato come Render Cron esterno (processo separato, senza accesso diretto a risorse del web service), continua a usare il pattern già collaudato "cron → endpoint HTTP autenticato sul servizio web" — **non** un accesso diretto del processo cron al database o a un disco.

## Configurazione — pydantic-settings

Tutte le variabili d'ambiente (DB URL, secrets, chiavi B2, VAPID, ecc.) caricate in un unico `Settings` tipizzato (`config.py`), validato all'avvio (un valore mancante/malformato fa fallire l'avvio subito con un errore chiaro, non un errore a runtime nel mezzo di una richiesta reale) — sostituisce la lettura sparsa di `os.environ.get(...)` con default silenziosi già rilevata come pattern fragile nell'audit V1.

## Testing (anticipazione, dettaglio completo in doc 11)

- `domain/`: unit test puri, nessun DB, nessun mock necessario (funzioni pure su dati in memoria).
- `repositories/`+`services/`: test di integrazione contro un Postgres reale (container Docker dedicato ai test, non SQLite-in-memoria come sostituto — userebbe un dialetto SQL diverso da quello di produzione, rischiando di nascondere bug specifici di Postgres).
- `api/`: `TestClient`/`httpx.AsyncClient` di FastAPI, stesso principio già usato con successo in V1 (`unittest` + client HTTP diretto) ma ora con dipendenze iniettabili/mockabili via `Depends()`.
