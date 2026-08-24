# 01 — Architettura Attuale

## Stack e infrastruttura

| Livello | Tecnologia |
|---|---|
| Runtime | Python 3.12.8 (`runtime.txt`) |
| Server HTTP | `http.server.BaseHTTPRequestHandler` **nativo**, nessun framework (no Flask/Django/FastAPI) |
| Database | SQLite, un unico file (`pet_paradise.db`), nessun ORM (query SQL dirette via `sqlite3`) |
| Frontend | Nessuno separato: HTML generato lato server via f-string Python, iniettato in un'unica risposta per richiesta |
| Dipendenze Python | Solo 3: `reportlab==4.2.5`, `pypdf==5.1.0`, `pywebpush==2.0.3` (`requirements.txt`) — tutto il resto è libreria standard |
| Hosting | Render.com, piano "starter", **un solo processo web** (`Procfile: web: python app.py`) + **un cron job separato** ogni 5 minuti per WhatsApp |
| Storage persistente | Un disco Render da 1GB montato su `/var/data` (`render.yaml`), contiene il file SQLite e la cartella `ddt/` (PDF generati) |
| Backup | **Nessuno a livello applicativo** — vedi "Rischi critici" sotto |
| PWA | Installabile (manifest + service worker), notifiche push Web Push con VAPID |

Il file principale (`app.py`) è **18.370 righe**. Di queste, **~5.643 righe (31%) sono CSS+JS inline** (blocco CSS 1.346–2.858, blocco JS 2.860–6.990), iniettate come stringa in ogni risposta HTML — non file statici serviti separatamente, non versionati/cacheabili in modo indipendente dal codice Python.

Moduli satellite (fuori da `app.py`, ciascuno con solo funzioni pure/query, **nessun handler HTTP proprio** — sono tutti richiamati dal dispatcher di `app.py`):

| Modulo | Righe | Maturità |
|---|---|---|
| `balance_service.py` | 1.904 | Alta — dataclass tipizzate, eccezioni dedicate (`BalanceError`, `InvalidMovementError`, `DuplicateMovementError`, `MovementAlreadyReversedError`), validazione centralizzata |
| `route_service.py` | 702 | Alta — algoritmi isolati (haversine, nearest-neighbor, 2-opt), integrazione Google ben incapsulata |
| `notification_service.py` | 578 | Media-alta — funzioni tipizzate, ma logica di raggruppamento/priorità non banale |
| `calendar_service.py` | 423 | Media — schema + normalizzazione, poca business logic (quella vera resta in app.py) |
| `email_service.py` | ~180 | Piccola ma pulita, eccezioni dedicate |
| `pdf_service.py` | ~350 | Generazione DDT via overlay reportlab/pypdf su template prestampati |
| `shift_service.py` | 156 | Piccola, ben strutturata |
| `balance_migration.py` / `balance_repair.py` / `balance_legacy_repair.py` | ~600 tot. | Script di riparazione dati **una tantum**, non routine ricorrenti — la loro stessa esistenza documenta incidenti storici di integrità dati già affrontati con intervento manuale |
| `urn_inventory.py` | 93 | Quasi vuoto, solo costanti |
| `cron_whatsapp.py` | ~40 | Client minimale che sveglia il servizio web via HTTP con secret condiviso |

**Conclusione**: la disciplina architetturale è **fortemente disomogenea**. `balance_service.py` e `route_service.py` mostrano che il team SA scrivere codice ben isolato quando serve; il resto (in particolare Cremazioni, Dashboard, Calendario, Turni) è rimasto dentro il monolite `app.py`, mescolando routing, query SQL, calcolo e generazione HTML nello stesso metodo.

## Routing

Nessun framework: due dispatcher lineari, `_route_get` (app.py:8180-8311, 132 righe) e `_route_post` (app.py:8321-8483, 163 righe), una lunga catena di `if path==...` / `re.fullmatch(...)` valutata in ordine per ogni richiesta (~140 condizioni GET, ~110 POST — costo O(n) per richiesta, non O(1), ma a questo volume di route irrilevante in pratica).

**Inventario completo per dominio** (18 domini, ~140 route totali) — tabella dettagliata nel file di lavoro dell'audit; sintesi per dimensione/complessità:

| Dominio | Route | Funzione più complessa |
|---|---|---|
| Cremazioni | 10 | `cremation_schedule_week` — **572 righe, ~9 query**, la funzione più lunga di tutto il progetto |
| Dashboard | 1 | `dashboard` — 449 righe, ~10 query, genera anche grafici SVG inline |
| Bilanci/Pagamenti | 14 | `balances_page` — 355 righe |
| WhatsApp | 8 | `whatsapp_conversations` — 292 righe |
| Turni | 10 | `shifts_page` — 268 righe |
| Calendario | 12 | `calendar_event_detail` — 250 righe |
| Pratiche/Archivio | ~25 | `edit_submit` — 153 righe |
| Fatture | 3 | — |
| Percorsi | 7 | `route_plan_page` — 155 righe |
| Veterinari, Collaboratori, Clienti, Urne, Notifiche, Impostazioni/Ordini | ~50 combinate | — |

**18 funzioni superano le 150 righe**; le prime 3 (`cremation_schedule_week`, `cremation_schedule`, `dashboard`) totalizzano da sole ~1.480 righe (~8% dell'intero file), concentrate in due soli domini (Cremazioni e Dashboard) — indicano dove la V2 deve investire per prima nello scomporre la logica in domain layer + servizi.

## Autenticazione e autorizzazione

- **Sessioni**: tabella `sessions(token PK, user_id, created_at)`, cookie `ppm_session` (HttpOnly, SameSite=Lax, Max-Age 180 giorni). **Nessuna scadenza lato server** — un token resta valido a tempo indeterminato finché non viene cancellato al logout. Nessuna pulizia periodica delle sessioni vecchie.
- **Password**: PBKDF2-HMAC-SHA256, 210.000 iterazioni, salt casuale 16 byte, verifica a tempo costante (`hmac.compare_digest`) — implementazione corretta, algoritmo adeguato.
- **Ruoli**: `users.role` — `'admin'` / `'operator'`, colonna `TEXT` libera (nessun CHECK/enum a livello DB).
- **Autorizzazione NON centralizzata**: nessun middleware/decorator "richiede ruolo X". Ogni handler sensibile controlla `if user["role"]!="admin": ...` inline — **~20 punti sparsi nel codice**. Rischio concreto: una nuova route sensibile può dimenticare il controllo, e non c'è modo di verificarlo staticamente/automaticamente oggi.
- Alcuni handler non bloccano ma **degradano silenziosamente** il comportamento in base al ruolo (es. forzano `operator_name` se non admin) — logica di autorizzazione a grana fine mescolata nella business logic anziché centralizzata.

## Frontend (HTML/CSS/JS generati server-side)

- **`layout(title, body, user)`** (app.py:8018-8049): unico wrapper HTML per ogni pagina. Fa query dirette al DB per badge notifiche/promemoria, legge preferenze utente, genera sidebar/header/bottom-nav come f-string. Nessun motore di template (no Jinja) — solo concatenazione stringhe con escape manuale (`esc()`).
- **CSS**: un'unica costante `CSS` (1.513 righe), iniettata inline in ogni risposta (mai cacheata come risorsa statica separata). Tema chiaro/scuro tramite classe `.light-theme` con doppio set di regole (non un sistema di design-token centralizzato — molti colori sono hardcoded sia nel tema scuro di default che nella variante chiara). 52 media query per il responsive.
- **JS**: un'unica costante `APP_JS` (4.130 righe) iniettata in coda al `<body>`. Vanilla JS puro, nessun framework. Modello di eventi ibrido: 199 `onclick` inline generati server-side + 171 `addEventListener` per sottosistemi più complessi (autosave, drag&drop, pull-to-refresh). Nessuno store di stato centralizzato: ogni "componente" è una closure indipendente auto-inizializzata su `DOMContentLoaded`.
- **Persistenza client**: `localStorage` per tema, bozze evento calendario (`ppm_calendar_draft_*`), flag "notifiche già richieste"; `sessionStorage` per flag one-shot post-redirect e ripristino posizione scroll.
- **PWA**: manifest + service worker installabili, cache offline **minima** (solo shell statica, non le pagine dati), notifiche push complete (azioni rapide dallo SW senza aprire l'app), banner di aggiornamento SW controllato (non interrompe l'utente a metà operazione — buon pattern).
- **Invio form**: modello dominante è **POST classico con redirect 303** (full page reload) — pattern usato dalla quasi totalità dei form CRUD principali. **55 chiamate `fetch`** coesistono per: ricerche live/autocomplete (con `AbortController`), azioni rapide senza reload (drag&drop cicli cremazione, notifiche), e **due sistemi di autosalvataggio indipendenti e diversi tra loro**:
  1. Form Nuovo evento calendario → bozza **solo client-side** in `localStorage`, nessuna persistenza server finché non si invia.
  2. Form Modifica pratica → autosave **server-side incrementale** via `fetch`, con diff rispetto a un baseline, concorrenza ottimistica (versione/409 su conflitto), stati dirty/saving/saved/error/conflict.
  
  Non esiste un meccanismo di bozza persistente per la creazione di una **nuova** pratica (`new_page`/`create_practice`) — questo è esattamente il gap segnalato dal committente al punto 11 della richiesta V2 ("Bozze e autosalvataggio... la creazione di una nuova pratica deve utilizzare un sistema di BOZZA persistente").
- **Gestione errori submit**: niente SPA-error-handling generalizzato. Round-trip server con form ripopolato dai valori appena inviati (mai svuotato) — pattern corretto nel principio, realizzato in due varianti non unificate: banner generico (`class="flash warning"`, ripetuto 19 volte come frammento identico) ed errore per-campo con scroll/focus automatico (solo nei form pratica).

## Integrazioni esterne

| Integrazione | Provider | Sincrono/Asincrono | Note |
|---|---|---|---|
| WhatsApp | Meta WhatsApp Business Cloud API (diretta, no Twilio) | Invio sincrono verso Meta, ma orchestrato da cron ogni 5 min | Webhook in ingresso per stati e messaggi ricevuti; retry solo manuale dopo fallimento; DB aperto/chiuso a stadi attorno alla chiamata di rete per non bloccare altre pagine (fix di un incidente reale, vedi sotto) |
| Email | SMTP diretto (`smtplib`), nessun provider transazionale terzo | Sincrono | Solo per ordini acqua/materiali; mittente vincolato a un indirizzo fisso |
| Notifiche push | Web Push standard (VAPID) via `pywebpush` | Emissione sincrona, invio in thread separato | Subscription scadute (404/410) auto-cancellate; ogni tentativo loggato |
| PDF/DDT | Locale (reportlab + pypdf su template prestampati) | — | Nessuna chiamata di rete; file su disco persistente Render |
| Geocoding | Nominatim (OSM, gratuito, primario) + Google Geocoding (fallback opzionale se configurata una chiave) | Sincrono, con cache (`geocode_cache`) | Mai un'eccezione bloccante: fallback a "nessuna coordinata" |
| Ottimizzazione percorsi | Google Routes API + Route Matrix API (chiave API semplice, no OAuth) | Sincrono | Fallback locale automatico (nearest-neighbor + 2-opt) se l'API non è disponibile o non configurata — il sistema funziona anche a chiave assente/quota esaurita |

Nessuna integrazione di pagamento/fatturazione elettronica esterna, nessun SMS provider, nessuno storage cloud (i PDF restano su disco Render).

## Transazionalità e gestione errori

- Il context manager `db()` (app.py:298-320) apre una connessione SQLite per `with` block, con commit automatico all'uscita e rollback automatico su eccezione — **corretto e documentato con un commento che racconta un bug reale già risolto**: la vecchia implementazione perdeva connessioni (mai chiuse), causando probabile esaurimento file descriptor nel tempo.
- **203 blocchi `with db() as c:` indipendenti** nel file. Diverse funzioni aprono **più blocchi in sequenza per un'unica operazione logica** (es. `_create_and_send_order`: 5 blocchi; `whatsapp_cron`: 4; `send_whatsapp_message`: 4; `normalized_fields`: 4) — ogni blocco è una transazione a sé, quindi **non c'è atomicità end-to-end**: se il primo blocco scrive con successo e un blocco successivo fallisce, la prima scrittura resta committata. Per WhatsApp/notifiche l'impatto è mitigato da idempotenza esistente altrove; per il dominio ordini (`_create_and_send_order`) è un rischio concreto di stato incoerente da verificare puntualmente.
- Gestore d'eccezioni **globale** al dispatcher aggiunto di recente (commento esplicito: prima non esisteva, un'eccezione profonda chiudeva la connessione senza risposta HTTP, "nessun errore visibile" per l'utente) — buon segnale di consapevolezza, ma conferma l'assenza storica di una rete di sicurezza.
- SQL: **720 chiamate `.execute()`** totali, **74 con costruzione f-string della query** — ispezionate una per una: nessun caso di injection reale trovato (i valori utente vanno sempre in parametri `?`, gli f-string interpolano solo segmenti strutturali fissi/enumerati), ma è una disciplina non imposta da alcuna astrazione — dipende dall'attenzione di chi scrive nuovo codice.

## Rischi critici da affrontare PRIMA di qualunque migrazione (priorità assoluta)

1. **Nessun backup automatico**: cercato in tutto il codice applicativo, non esiste. L'unico backup trovato (`balance_legacy_repair.py`, funzione `_create_backup`) è invocato manualmente da uno script di riparazione una tantum. Il tipo di notifica `backup_completed` esiste nel vocabolario ma non risulta mai emesso da alcuna routine reale. **La sola protezione oggi è l'eventuale snapshot automatico del disco persistente Render**, che va verificato direttamente nel pannello Render (piano "starter") — non è garantito dal codice.
2. **Vincoli di chiave esterna dichiarati ma incompleti**: l'app abilita correttamente `PRAGMA foreign_keys=ON` sulla propria connessione (app.py:313), ma **molte relazioni concettuali non hanno affatto una clausola `REFERENCES`** nello schema (es. `practices.client_id`, `collaborator_id`, `veterinarian_id`, `owner_veterinarian_id`, `origin_veterinarian_id`, `used_voucher_id`, `urn_id`, `urn_id_2` — solo 3 delle relazioni di `practices` sono FK dichiarate, su un totale di almeno 11 colonne che puntano ad altre tabelle). Qualunque script di migrazione/ispezione che apra il file `.db` direttamente (non tramite `app.py`) **non avrà FK enforcement attivo di default** a meno di impostarlo esplicitamente.
3. **Journal mode non-WAL**: `journal_mode=delete` (rollback journal classico, non Write-Ahead Log). Già causa di un incidente reale documentato nel codice (una chiamata WhatsApp lenta bloccava pagine non correlate). Per un gestionale multi-operatore concorrente, WAL è lo standard raccomandato — la V2 (qualunque sia il DB scelto) deve risolvere questo strutturalmente.

Questi tre punti vanno indirizzati nella "Strategia di backup e rollback" e nella "Strategia di migrazione" (documenti successivi), prima di toccare qualunque dato reale.
