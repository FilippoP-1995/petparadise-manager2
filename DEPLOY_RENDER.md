# Pubblicazione online su Render

Questa configurazione permette di usare Pet Paradise Manager da PC, telefono e tablet senza tenere acceso il PC aziendale.

## Cosa viene pubblicato

- Web app Pet Paradise Manager
- Database SQLite su disco persistente del server
- Archivio PDF DDT su disco persistente del server
- Login protetto

## Passaggi

1. Creare un account su Render.
2. Caricare questo progetto su GitHub.
3. In Render scegliere "New Blueprint" oppure "New Web Service".
4. Se si usa Blueprint, Render leggerà `render.yaml`.
5. Verificare che il disco persistente sia montato su `/var/data`.
6. Aprire l'indirizzo pubblico generato da Render.

Credenziali iniziali:

- utente: `admin`
- password: `petparadise`

## Importante

Il database e i PDF non vanno salvati nella cartella normale dell'app online, perché sui server cloud può essere cancellata a ogni aggiornamento.

Per questo l'app usa:

```text
PPM_DATA_DIR=/var/data
```

Dentro questa cartella vengono salvati:

- `pet_paradise.db`
- cartella `ddt/`

## Caricamento lento dopo molte ore

Se il servizio resta inutilizzato per diverse ore, Render può impiegare qualche secondo in più al primo accesso. È normale: il servizio si sta riattivando.

Questo non cancella le pratiche. Le pratiche e i PDF restano salvati se il disco persistente è attivo e `PPM_DATA_DIR` punta a `/var/data`.

## Notifiche push

Impostare su Render anche queste variabili:

- `VAPID_PUBLIC_KEY`: chiave pubblica VAPID in formato URL-safe base64;
- `VAPID_PRIVATE_KEY`: chiave privata VAPID (non inserirla mai nei log o nel repository);
- `VAPID_SUBJECT`: contatto del mittente, per esempio `mailto:assistenza@petparadise.it`.

Le chiavi devono appartenere alla stessa coppia e restare stabili: cambiandole, i dispositivi dovranno riabilitare le notifiche.

## Nota per la versione successiva

Questa è la strada più veloce per andare online. Quando il gestionale crescerà con più utenti, più sedi e backup automatici, conviene migrare il database a PostgreSQL.
