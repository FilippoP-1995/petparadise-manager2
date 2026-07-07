# Pet Paradise Manager

Gestionale operativo per Pet Paradise SNC.

La versione attuale può funzionare:

- in locale sul PC;
- online su server cloud, raggiungibile da telefono/PC/tablet senza tenere acceso il PC.

## Avvio

Usare il Python incluso in Codex oppure Python 3.11+:

```powershell
python app.py
```

Aprire `http://127.0.0.1:8080`.

Credenziali iniziali:

- utente: `admin`
- password: `petparadise`

Al primo accesso cambiare la password dall'area utente (funzione prevista nel prossimo incremento).

## Dati

- Database: `data/pet_paradise.db`
- PDF generati: `data/ddt/`
- Modello DCS: `assets/DCS_NUOVO.pdf`

## Avvio online

Il progetto è predisposto per Render tramite:

- `render.yaml`
- `requirements.txt`
- `Procfile`
- `runtime.txt`

In cloud impostare:

```text
PPM_DATA_DIR=/var/data
```

La cartella `/var/data` deve essere un disco persistente, così database e PDF DDT non vengono persi agli aggiornamenti.

Per le istruzioni complete vedere `DEPLOY_RENDER.md`.

## Evoluzione consigliata

Questa configurazione cloud usa SQLite su disco persistente: è la strada più veloce per avere subito un gestionale online.

Quando l'uso quotidiano crescerà, conviene migrare il database a PostgreSQL e aggiungere backup automatici.
