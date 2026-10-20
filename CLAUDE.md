# Istruzioni per Claude

## Git / GitHub

L'utente ha autorizzato in modo permanente commit e push diretti su `main` per ogni modifica richiesta in questo repository: non chiedere conferma prima di `git commit` o `git push origin main`.

Restano esclusi da questa autorizzazione (chiedere sempre conferma esplicita prima di):
- `git push --force` o qualsiasi riscrittura della cronologia
- `git reset --hard`, `git clean`, cancellazione di branch o di commit
- Operazioni distruttive in generale

Continuare comunque a verificare le modifiche prima del commit (test pertinenti quando ragionevole) e a scrivere messaggi di commit chiari in italiano, coerenti con lo stile già presente nella cronologia del repository.

Ogni commit termina con la riga `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (o il modello in uso).

## Cos'è il progetto

**Pet Paradise Manager** è il gestionale operativo di Pet Paradise Cremazione Animali (sedi Livorno ed Empoli): pratiche di cremazione, calendario ritiri/riconsegne, programma cremazioni (cicli), clienti e veterinari, urne e prodotti, pagamenti/acconti/fatture, bilanci, turni, smaltimenti, notifiche push, messaggi WhatsApp, assistente AI, DDT e certificati in PDF. Lingua dell'interfaccia, dei dati e dei commit: **italiano**. L'utente è Filippo (titolare), non uno sviluppatore: spiegare in modo semplice, senza gergo inutile.

Sotto-progetti:

- **V1** (`app.py`, branch `main`): il gestionale **in produzione** e l'unico sviluppato attivamente. Dal 2026-09-10 lo sviluppo è concentrato solo su V1.
- **V2** (`backend/` FastAPI + `frontend/` React/TypeScript, branch `v2-development`): riscrittura **in pausa**. Non esiste su `main`. Ogni modifica V1 va annotata in `docs/v2/16-modifiche-v1-da-riportare-v2.md` per un futuro backport.
- **Pet Paradise Partners** (`/partner`): portale PWA B2B per le cliniche veterinarie, costruito dentro V1 (vedi "Stato attuale").

## Stack (V1)

- Python 3.12 (`runtime.txt`), **solo libreria standard per il server**: `http.server.ThreadingHTTPServer`, nessun framework web.
- `app.py` (~22.000 righe) è un file unico: routing nella classe `App`, HTML generato con f-string, CSS e JavaScript come grosse costanti stringa (`CSS`, `APP_JS`). Attenzione ad apici e graffe `{{ }}` dentro le f-string.
- Database **SQLite** (`foreign_keys` attive, connessione per chiamata con `db()`), migrazioni leggere in avvio (`ALTER TABLE ... ADD COLUMN` condizionali).
- Dipendenze (`requirements.txt`): `reportlab`, `pypdf` (PDF DDT/certificati), `pywebpush` (notifiche push), `boto3` (backup su S3 compatibile), `anthropic` (assistente AI).
- Hosting: **Render** (web service Python + 2 cron + disco persistente `/var/data`).

## Struttura delle cartelle (branch `main`)

```
app.py                  server, routing, pagine, JS/CSS, schema DB, logica di business
ai_assistant.py         assistente AI (strumenti di sola lettura sui dati)
partner_portal.py       pagine e route del portale /partner
partner_service.py      schema, regole, sicurezza e notifiche del portale partner
quote_service.py        calcolatore preventivi (usato anche dal portale)
calendar_service.py     regole del calendario eventi
notification_service.py notifiche (push/promemoria) e outbox
balance_*.py            bilanci, riparazioni e migrazioni dei saldi (pagamenti)
shift_service.py        turni collaboratori
pdf_service.py, certificate_service.py   DDT e certificati di cremazione
route_service.py        percorsi e orari (Google Maps)
urn_inventory.py        magazzino urne
email_service.py        invio email SMTP
backup_service.py       backup su S3 compatibile
cron_whatsapp.py, cron_backup.py   entry point dei cron Render (chiamano il web service via HTTP)
assets/                 logo, icone PWA, manifest, service worker (sw.js), modelli DCS/certificato
tests/                  suite unittest (test_app.py è la principale)
docs/v2/                documentazione V2 e registro modifiche V1 da riportare
render.yaml             blueprint Render della V1
DEPLOY_RENDER.md        istruzioni di pubblicazione
.env.example            variabili d'ambiente per lo sviluppo locale
```

Su `v2-development` esistono in più `backend/` (FastAPI, alembic, test pytest), `frontend/` (Vite/React), `render-v2.yaml`, `DEPLOY_RENDER_V2.md`.

## Avvio e test

Avvio locale (dati in `data/`, ignorata da git; il DB nuovo viene creato al primo avvio):

```bash
python app.py                      # http://127.0.0.1:8080  (utente iniziale: vedi README, solo per sviluppo)
PPM_DATA_DIR=/percorso/scratch PPM_PORT=8090 PPM_HOST=127.0.0.1 python app.py   # dati isolati e porta diversa
```

Health check: `GET /health`. Per provare l'interfaccia in un browser automatizzato serve una sessione valida: crearla nel DB scratch e passare il cookie `ppm_session`.

Test (unittest; ogni test usa un DB temporaneo, **non toccano mai** `data/` né la produzione):

```bash
python -m unittest tests.test_app          # suite principale, ~860 test, ~13 minuti: DEVE essere tutta OK
python -m unittest tests.test_partner_portal tests.test_partner_service tests.test_partner_notifications
python -m unittest tests.test_calendar     # baseline nota: 20 FAIL + 1 ERROR (preesistenti)
python -m unittest discover -s tests       # tutti i moduli
```

Baseline preesistente dei moduli extra (confrontare sempre con un worktree pulito di `origin/main` prima di dare la colpa a una modifica): `tests/test_balance_integration.py` 11 ERROR + 2 FAIL, `tests/test_shifts.py` 1 FAIL. La suite lunga va lanciata in background con l'output su file e attesa fino alla fine.

## Variabili d'ambiente

Solo i nomi: **mai** scrivere valori, token o password in chat, commit o documenti. In produzione si impostano nel pannello Render; per avviare e testare in locale non ne serve nessuna (i test creano da soli i dati temporanei). Elenco completo e funzione di ciascuna: `render.yaml` e `.env.example`; riepilogo:

- Server: `PPM_DATA_DIR` (cartella di DB e PDF; in produzione `/var/data`), `PPM_HOST`, `PPM_PORT`, `PORT` (impostata da Render), `TZ`.
- Cron WhatsApp/promemoria: `WHATSAPP_CRON_SECRET`, `WHATSAPP_CRON_URL`.
- WhatsApp Business API: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_GRAPH_VERSION`, `WHATSAPP_TEMPLATE_LANGUAGE`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
- Backup: `BACKUP_CRON_SECRET`, `BACKUP_CRON_URL`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_KEY_ID`, `BACKUP_S3_APPLICATION_KEY`, `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`, `BACKUP_RETENTION_DAYS`.
- Notifiche push: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`.
- Email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `EMAIL_FROM_ADDRESS`, `EMAIL_FROM_NAME`.
- Assistente AI: `ANTHROPIC_API_KEY`, `AI_ASSISTANT_MODEL`.
- Percorsi/orari: `GOOGLE_MAPS_API_KEY`.
- Solo lettura (versione mostrata nell'app): `RENDER_GIT_COMMIT`, `SOURCE_VERSION`.
- V2 (branch `v2-development`): `DATABASE_URL`, `SESSION_TTL_DAYS`, `ENVIRONMENT`, `PPM_V2_ADMIN_USERNAME`, `PPM_V2_ADMIN_PASSWORD`, `PPM_V2_ADMIN_DISPLAY_NAME`.

## Deploy

- Servizio di produzione su Render (`petparadise-manager2.onrender.com`), disco persistente `/var/data` con `pet_paradise.db` e `ddt/`.
- **Claude non fa mai il deploy**: dopo il push su `main` il deploy lo fa l'utente a mano dal pannello Render (Manual Deploy). Chiudere sempre i report scrivendo che il deploy non è stato fatto e quali commit sono in attesa.
- `render.yaml` non specifica `autoDeploy` né `branch`: il comportamento reale dipende dalle impostazioni del servizio nel pannello Render (Settings → Build & Deploy).
- Non esiste ancora un ambiente di prova separato dalla produzione: i dati reali stanno solo su Render. Non usare mai dati o credenziali di produzione in sessioni di sviluppo.

## Regole di lavoro seguite finora

1. **Processo per ogni modifica non banale a V1**: ANALISI (leggere il codice davvero coinvolto, tracciare UI → endpoint → DB → calcoli derivati) → IMPATTO (altre funzioni che usano gli stessi dati) → CONFERMA (se la richiesta implica modifiche correlate, elencarle nel formato *MODIFICA RICHIESTA / IMPATTO RILEVATO / MODIFICA CORRELATA CONSIGLIATA / CONSEGUENZA SE NON LA FACCIAMO* e attendere il via) → IMPLEMENTAZIONE → VERIFICA → REPORT (cosa è cambiato, funzioni coinvolte, test eseguiti, problemi residui, modifiche correlate non fatte).
2. Niente refactor non richiesti né interpretazioni creative; se la richiesta è ambigua, chiedere. Pagamenti, saldi, fatture, stati e dati storici: massima cautela, mai modifiche distruttive ai dati reali senza autorizzazione esplicita.
3. **Completezza**: una modifica è finita solo quando ogni pagina/scenario/esempio nominato dall'utente è implementato **e verificato** (Calendario, Programma Cremazioni, Orari, ecc.), cercando anche le altre implementazioni dello stesso comportamento. Gli esempi dell'utente sono criteri di accettazione da provare esattamente come descritti. Il report mostra criterio / test / risultato.
4. **Test prima del push**: almeno `tests.test_app` completa (deve essere tutta OK) più i moduli pertinenti; per ogni comportamento nuovo aggiungere un test.
5. **Lavorare in un worktree isolato** creato da `origin/main` (`git worktree add ../nome --detach origin/main`), non nella cartella principale se contiene lavoro non committato; push con `git push origin HEAD:main` dopo `git fetch` ed eventuale rebase. Non fare mai rebase, checkout o modifiche in una cartella dove una suite di test è ancora in esecuzione.
6. Messaggi di commit in italiano, descrittivi, con la riga Co-Authored-By.
7. Dopo ogni modifica V1 aggiungere una riga/sezione in `docs/v2/16-modifiche-v1-da-riportare-v2.md` (cosa, perché, commit, stato verso V2: da riportare / non 1:1 / solo V1).
8. Interfaccia mobile-first (iPhone/PWA): ogni novità va pensata per il telefono; nei popup lo scroll non deve mai far scorrere la pagina sotto.
9. Mai segreti nel repository o in chat; mai `git push --force`, `reset --hard`, `clean`, cancellazione di branch senza conferma esplicita.

## Stato attuale (2026-10-20)

- `main` su GitHub; ultimo commit al momento della stesura: `7766bbb` (archivio veloce). **Deploy manuale su Render in attesa** per i commit recenti (verificare in Render quale commit è in produzione).
- Il lavoro V2 non ancora pubblicato è salvato sul ramo `v2-development` (commit `14c931c`), mai su `main`.
- Funzioni recenti di V1: messaggi rapidi WhatsApp (link `wa.me`, modelli con segnaposto `{nome_cliente}` / `{nome_animale}`, **senza emoji** perché arrivavano come simboli rotti; pulsante Copia) da pratiche, calendario e cicli del programma cremazioni; bozza automatica della nuova pratica; carosello dei giorni (`ppmInitDayCarousel`) condiviso da Calendario, Programma Cremazioni e Orari; blocco dello scroll sotto i popup; "Segna consegnata" dalle card di riconsegna (usa `/pratiche/<id>/stato-rapido`); stato pagamento calcolato da `derive_payment_status` (con riparazione all'avvio); veterinario scelto come speditore = sempre lo stesso cliente (`clients.veterinarian_id`); Archivio pratiche con caricamento differito dei mesi vecchi (`ARCHIVE_EAGER_MONTHS`).
- Pet Paradise Partners: portale `/partner` già realizzato (utenti clinica con login utente+password, isolamento per clinica, richieste create come eventi calendario "Ritiro" in stato "Da confermare", trigger SQLite ed eventi pubblici, outbox e notifiche, clinica demo, calcolatore preventivi, pagine staff per richieste e gestione cliniche, promemoria via cron). Test in `tests/test_partner_*.py`.

## Prossimi passi

1. Sviluppo su Claude Code nel cloud: creare un **ambiente di prova** separato dalla produzione (servizio Render "staging" con proprio disco e `PPM_DATA_DIR`, dati finti o copia anonimizzata), così le sessioni cloud non toccano mai i dati reali.
2. Pet Paradise Partners: validare l'adozione con 3-5 cliniche pilota. Decisioni già prese: la richiesta è un evento "Ritiro" (la pratica nasce dopo); "Cremato" non è mostrato ai veterinari (resta "In lavorazione"); la clinica propone una fascia oraria e il flag urgente, lo staff conferma; buoni veterinario solo per cliniche in convenzione, maturano alla creazione della pratica. Ancora aperti: login con codice OTP via email (non link, per le PWA iOS) e "in congelatore, nessuna fretta" per le cremazioni collettive.
3. Archivio: se con gli anni resta lento, paginare anche le righe dentro i mesi molto grandi.
4. Anagrafica: valutare con l'utente l'unione dei clienti-veterinario duplicati storici (nessuna operazione sui dati reali senza conferma).
5. V2: riprendere solo su richiesta esplicita, partendo da `docs/v2/16-modifiche-v1-da-riportare-v2.md`.

## Note pratiche per le sessioni

- Su Windows (PowerShell/Git Bash): attenzione ai percorsi e ai backslash nelle heredoc; usare `python` (non `python3`).
- Dopo il push, riportare all'utente l'esito (hash del commit, test eseguiti) e ricordare che il deploy su Render è manuale.
