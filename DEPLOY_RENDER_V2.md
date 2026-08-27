# Deployment V2 su Render (FastAPI + React) - separato dalla V1

Questa documentazione riguarda ESCLUSIVAMENTE la V2 (`backend/` +
`frontend/`). Per la V1 (root `app.py`, tuttora in produzione) vedi
`DEPLOY_RENDER.md` e `render.yaml` - NON toccati da questo documento.

**V2 NON migra automaticamente dati dalla V1.**
**V1 e V2 utilizzano database distinti** (V1: SQLite su disco persistente;
V2: PostgreSQL separato). Nessuno script di importazione/migrazione dati
V1→V2 esiste oggi in questo repository - è una fase futura separata, che
richiederà un audit dedicato dello schema V1 e dello schema V2 prima di
essere anche solo progettata.

La configurazione di deployment V2 vive in `render-v2.yaml` (root del
repository), deliberatamente non chiamata `render.yaml` così Render non
può individuarla/applicarla automaticamente via Blueprint - va collegata
a un nuovo servizio Render solo quando deciso esplicitamente.

## 1. Installare le dipendenze (locale)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # Windows; su Linux/Mac: source .venv/bin/activate
pip install -r requirements.lock.txt
```

`requirements.lock.txt` è l'elenco esatto (dirette + transitive) verificato
contro l'intera suite di test - preferirlo a `requirements.txt` per
un'installazione riproducibile (in locale e in produzione).

## 2. Generare la build del frontend

```bash
cd frontend
npm ci
npm run build
```

Produce `frontend/dist/index.html` e `frontend/dist/assets/*` (non
tracciati in git - rigenerati ad ogni build). Il backend V2 serve questi
file automaticamente se la cartella esiste (vedi punto 3) - se non esiste
(es. sviluppo locale con solo `npm run dev`), il backend parte comunque e
serve solo `/api/*` e `/health`.

## 3. Avviare il backend

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8001
```

In produzione la porta arriva da Render tramite la variabile `$PORT`
(vedi `render-v2.yaml`, `startCommand`). Il working directory deve essere
`backend/`: gli import interni (`from api.routes import ...`, `from
config import settings`, ecc.) non sono qualificati come `backend.xxx` e
vanno risolti da lì.

## 4. Variabili d'ambiente richieste

| Variabile | Obbligatoria | Descrizione |
|---|---|---|
| `DATABASE_URL` | sì | Connessione al Postgres V2. Formato: `postgresql+asyncpg://utente:password@host:porta/db`. Se il provider fornisce `postgresql://` o `postgres://`, il backend normalizza automaticamente lo schema (vedi `backend/config.py`, `_normalize_database_url`) - **mai il database V1**, che è SQLite e comunque incompatibile. |
| `ENVIRONMENT` | consigliata (default `development`) | Con `production`, i cookie di sessione diventano `Secure` (`backend/api/routes/auth.py`). **Va impostata esplicitamente in produzione** - senza, il default `development` lascia i cookie non-Secure. |
| `SESSION_TTL_DAYS` | no (default `30`) | Durata scorrevole della sessione, rinnovata ad ogni richiesta autenticata. |

Nessuna `SECRET_KEY` è necessaria: le sessioni sono token opachi random
(32 byte, `secrets.token_urlsafe`) salvati lato server in Postgres, non
JWT firmati - non c'è nessuna chiave da generare, ruotare o proteggere.

## 5. Inizializzare il database V2

Contro un Postgres V2 vuoto (mai il database V1):

```bash
cd backend
alembic upgrade head
```

Esegue tutte le migration esistenti (dominio clienti/veterinari, pratiche,
ritiro/riconsegna, cicli di cremazione, sedi/urne/articoli,
fatture/pagamenti, rate-limit login, indici). Il database V2 parte
sempre da zero: non esiste oggi alcuna strategia di importazione dati
dalla V1 (vedi nota in cima a questo documento).

## 6. Eseguire `alembic upgrade head`

Vedi punto 5 - ripetuto qui solo per completezza dell'elenco richiesto.
Va eseguito una volta per ambiente (locale, staging, produzione), non ad
ogni deploy se lo schema non è cambiato - va comunque eseguito ad ogni
deploy che introduce nuove migration.

## 7. Creare il primo admin (production bootstrap)

**Non usare `backend/scripts/seed_dev_admin.py` in produzione** - crea
`admin` / `dev-password-change-me`, una password nota nel codice
sorgente, pensata esplicitamente solo per sviluppo/test.

Per produzione, usare invece `backend/scripts/bootstrap_admin.py`:

```bash
cd backend
PPM_V2_ADMIN_USERNAME=<scegli-uno-username> \
PPM_V2_ADMIN_PASSWORD=<scegli-una-password-forte> \
  python -m scripts.bootstrap_admin
```

- Username e password arrivano solo da variabili d'ambiente: nessuna
  password hardcoded, nessuna password stampata in output/log.
- Esecuzione one-time: se l'utente esiste già, lo script non fa nulla (non
  sovrascrive mai la password esistente) - va eseguito una sola volta,
  manualmente, dopo `alembic upgrade head` e prima del primo login reale.
- Non è un sistema di gestione utenti: V2 non ha ancora (per scelta,
  fuori scope di questa fase) un dominio di amministrazione utenti - solo
  questo bootstrap minimale del primissimo admin.

## 8. Simulare localmente il deployment

```bash
# 1. Postgres V2 locale/test dedicato (MAI il database V1)
#    es. via Docker: postgres:16, oppure un'istanza locale già esistente
#    puntata da DATABASE_URL nel proprio backend/.env

# 2. Dipendenze + migration
cd backend
pip install -r requirements.lock.txt
alembic upgrade head

# 3. Build frontend
cd ../frontend
npm ci
npm run build

# 4. Bootstrap primo admin (una tantum)
cd ../backend
PPM_V2_ADMIN_USERNAME=demo PPM_V2_ADMIN_PASSWORD=demo-password-locale \
  python -m scripts.bootstrap_admin

# 5. Avvio, come in produzione (una singola origin, non Vite dev server)
ENVIRONMENT=production uvicorn main:app --host 0.0.0.0 --port 8001
```

Poi aprire `http://localhost:8001/` nel browser: deve caricare la SPA
React (non il dev server Vite), permettere il login con le credenziali
del bootstrap, e servire correttamente sia `/api/*` sia le route React
(`/pratiche`, `/calendario`, `/fatture/123`, ecc.) anche con refresh
diretto sull'URL.

## 9. Deploy Render V2 separato (quando deciso - non ora)

1. Su Render: "New Blueprint", puntare a questo repository, indicare
   esplicitamente `render-v2.yaml` come file di blueprint (non verrà mai
   individuato automaticamente, essendo diverso da `render.yaml`).
2. Creare un Postgres Render separato e dedicato alla V2 (mai riusare
   quello - inesistente oggi - o il disco della V1).
3. Impostare `DATABASE_URL` (col Postgres V2 appena creato) ed
   `ENVIRONMENT=production` nel dashboard Render, come indicato dai
   commenti in `render-v2.yaml`.
4. Dopo il primo deploy riuscito: eseguire `alembic upgrade head` (via
   Render Shell) e poi `bootstrap_admin.py` (una tantum, via Render Shell,
   passando `PPM_V2_ADMIN_USERNAME`/`PPM_V2_ADMIN_PASSWORD` solo per
   quella esecuzione - non vanno lasciate come variabili d'ambiente
   permanenti del servizio).
5. Verificare l'app sul nuovo URL pubblico del servizio V2 (diverso
   dall'URL pubblico della V1, che resta invariato).

La promozione della V2 a servizio "principale" (sostituzione o
convivenza con la V1) è una decisione separata, non affrontata da questo
documento.
