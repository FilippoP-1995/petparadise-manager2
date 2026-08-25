# 00 — Verifica Backup Render (Punto 7 della richiesta)

> Fonti: documentazione ufficiale Render (`render.com/docs/disks`, `render.com/docs/postgresql-backups`), consultata il 2026-08-25. Citazioni dirette dove indicato. Vedi "Sources" in fondo.

## FACT — Persistent Disk (quello usato OGGI da V1 per il file SQLite)

Il disco su cui vive `pet_paradise.db` oggi è un **Persistent Disk** Render (`render.yaml`: `disk: name: pet-paradise-data, mountPath: /var/data, sizeGB: 1`), **non** un database gestito Render. Per questo prodotto:

- **FACT**: Render crea automaticamente uno snapshot del disco **ogni 24 ore**.
- **FACT**: gli snapshot restano disponibili per **almeno 7 giorni** dalla creazione.
- **FACT**: in caso di perdita/corruzione, è possibile ripristinare l'intero disco a un qualunque snapshot disponibile, dalla dashboard Render.
- **FACT**: il ripristino è **tutto-o-niente** — "se ripristini uno snapshot, tutte le modifiche al disco avvenute *dopo* quello snapshot vengono perse". Non esiste ripristino parziale/selettivo.
- **FACT**: i Persistent Disk sono disponibili solo sui piani **a pagamento** (il piano "starter" usato da questo progetto li include).
- **⚠️ FACT CRITICO, dalla documentazione Render stessa**: *"i database personalizzati non dovrebbero usare gli snapshot del disco per il ripristino, perché questo rischia la corruzione"*. Questo avviso è scritto esplicitamente da Render e si applica **esattamente** al caso di questo progetto: SQLite su Persistent Disk è di fatto un "database personalizzato" agli occhi del meccanismo di snapshot, che non ha alcuna consapevolezza delle transazioni SQLite in corso. Uno snapshot preso a metà di una scrittura può catturare il file `.db` in uno stato incoerente — **combinato con il fatto già documentato nell'audit che questo database non usa la modalità WAL**, il rischio è concreto, non teorico.

**Conclusione FACT**: esiste un meccanismo di recupero automatico, ma **la documentazione dello stesso fornitore lo sconsiglia per l'esatto caso d'uso di questo progetto**. Non è "nessuna rete di sicurezza", ma è una rete di sicurezza che il fornitore stesso dice di non considerare affidabile per un file SQLite.

## FACT — PostgreSQL gestito Render (rilevante per quando la V2 migrerà al database, decisione già presa punto 1)

Se in futuro si userà il **PostgreSQL gestito** di Render (prodotto diverso dal Persistent Disk):

- **FACT**: sui piani gratuiti, **nessun backup/recovery** è fornito.
- **FACT**: sui piani a pagamento, backup logici automatici, conservati **7 giorni** indipendentemente dal piano.
- **FACT**: point-in-time recovery — piano Hobby: **ultimi 3 giorni**; piano Pro o superiore: **ultimi 7 giorni**.
- **FACT**: il ripristino crea una **nuova istanza database** allo stato storico scelto, permettendo di validarla prima di sostituire quella di produzione — pattern corretto, non sovrascrive nulla in place.
- **ASSUMPTION (non verificata dai documenti pubblici)**: se i backup siano geo-replicati o conservati fisicamente separati dal server primario non è specificato nella documentazione pubblica consultata.

## RISPOSTA DIRETTA ALLE TUE DOMANDE

| Domanda | Risposta |
|---|---|
| Quali backup/snapshot sono disponibili oggi (V1, Persistent Disk)? | Snapshot automatico giornaliero dell'intero disco |
| Con quale frequenza? | Ogni 24 ore (FACT, da documentazione) |
| Per quanto tempo conservati? | Almeno 7 giorni (FACT) |
| Possono essere ripristinati? | Sì, ma solo per intero e solo tutto-o-niente (perdi tutto ciò che è successo dopo lo snapshot scelto) |
| Il piano attuale li supporta? | Sì, il piano "starter" con disco a pagamento li include (FACT) |
| Quali limiti esistono? | Nessun ripristino parziale; **Render stessa sconsiglia di fare affidamento su questo meccanismo per un database "custom" come SQLite**, per rischio di corruzione dello snapshot preso a metà scrittura |
| Cosa succede in caso di perdita completa del servizio? | Il disco persistente è disaccoppiato dal processo web — se il servizio web crasha o viene ricreato, il disco (e i suoi snapshot) sopravvivono, finché non viene esplicitamente eliminato dall'account Render |
| Copre effettivamente il file SQLite? | Sì copre il file (è uno snapshot dell'intero disco, quindi include `pet_paradise.db` e la cartella `ddt/`), **ma con il rischio di incoerenza interna già segnalato da Render stessa per l'assenza di modalità WAL** |

## DECISION richiesta (mia raccomandazione, punto 8 della tua richiesta)

**Lo snapshot Render da solo non è una protezione adeguata per questo progetto**, per due ragioni concrete (non teoriche):
1. Render stessa sconsiglia gli snapshot disco per database "custom" come SQLite.
2. Il database attuale non è in modalità WAL (già verificato nell'audit), il che aumenta il rischio di uno snapshot che cattura una scrittura a metà.

**Raccomandazione**: implementare SUBITO (non è "codice V2", è una misura di sicurezza operativa per V1, a basso rischio, sola-lettura sul dato) un backup indipendente:

1. Una funzione che usa l'API nativa di SQLite pensata apposta per questo (`sqlite3` backup API / `VACUUM INTO`), che produce **sempre** una copia internamente coerente anche se il database è in uso in quel momento — a differenza di una copia file grezza o di uno snapshot disco esterno.
2. Eseguita con una frequenza scelta da te (consiglio: almeno una volta al giorno, magari anche più spesso vista l'operatività quotidiana), come **job schedulato separato** (stesso pattern già esistente del cron WhatsApp, ma indipendente).
3. Il file di backup risultante **copiato fuori dal server applicativo** (mai lasciato solo sullo stesso disco persistente, altrimenti un problema al disco distrugge sia il dato che il suo backup) — serve una destinazione esterna: la opzione più semplice e economica è un bucket di storage oggetti (es. Backblaze B2, o S3-compatibile), con retention (es. mantieni gli ultimi 30 giorni, poi elimina i più vecchi).
4. Verifica di integrità automatica dopo ogni backup (riapertura del file copiato e controllo `PRAGMA integrity_check`).
5. **Un test di ripristino periodico reale** — come giustamente scrivi tu, "un backup mai testato come restore non va considerato affidabile". Questo va pianificato come procedura ricorrente, non solo come script.

**Implementato** (vedi commit successivo): `backup_service.py`, endpoint `/cron/backup`, `cron_backup.py`, servizio cron dedicato in `render.yaml`. Storage consigliato: **Backblaze B2**.

## Perché Backblaze B2

- **FACT** (documentazione ufficiale Backblaze, verificata 2026-08-25): primi 10GB di storage sempre gratuiti; oltre, $6.95/TB/mese (~$0,007/GB); download gratuiti fino a 3 volte lo storage medio mensile; le chiamate API standard sono gratuite.
- **Per questo caso d'uso** (un file compresso di poche centinaia di KB/pochi MB al giorno, conservato ~30 giorni → totale ben sotto 1GB): **il costo mensile atteso è €0**, resta interamente nella soglia gratuita.
- **Compatibile S3**: nessuna libreria proprietaria necessaria, lo script usa `boto3` (standard, ampiamente testato) puntato all'endpoint S3-compatibile di B2.

## Cosa devi fare tu (azioni che non posso compiere per te — creazione account/credenziali)

1. **Crea un account Backblaze** su backblaze.com (o usa un provider S3-compatibile diverso se lo preferisci — AWS S3/Google Cloud Storage funzionano allo stesso modo, basta cambiare l'endpoint).
2. **Crea un bucket privato** (es. `pet-paradise-backups`), regione a tua scelta (consiglio una regione EU per residenza dati in Europa).
3. **Crea una Application Key** con permessi di lettura/scrittura/eliminazione **solo su quel bucket** (mai una chiave con accesso a tutto l'account) — Backblaze genera un `keyID` e una `applicationKey`, mostrata **una sola volta**: salvala subito in un posto sicuro (password manager), non recuperabile dopo.
4. **Annota l'endpoint S3** del tuo bucket (mostrato nella dashboard B2 accanto al bucket, formato tipo `https://s3.<regione>.backblazeb2.com`).
5. **Nel pannello Render**, sul servizio web `pet-paradise-manager`, imposta queste variabili d'ambiente (già dichiarate in `render.yaml` con `sync: false`, quindi Render ti chiederà il valore al primo deploy, non lo leggerà da questo repository):
   - `BACKUP_CRON_SECRET` — una stringa lunga e casuale a tua scelta (es. generata con un password manager), stesso principio del `WHATSAPP_CRON_SECRET` già esistente
   - `BACKUP_S3_ENDPOINT` — l'endpoint del punto 4
   - `BACKUP_S3_KEY_ID` — il `keyID` del punto 3
   - `BACKUP_S3_APPLICATION_KEY` — la `applicationKey` del punto 3
   - `BACKUP_S3_BUCKET` — il nome del bucket del punto 2
6. **Sul nuovo servizio cron** `pet-paradise-backup-cron` (creato automaticamente al prossimo deploy da Blueprint, leggendo `render.yaml`), imposta:
   - `BACKUP_CRON_URL` — l'URL pubblico del tuo servizio web + `/cron/backup` (es. `https://pet-paradise-manager.onrender.com/cron/backup`)
   - `BACKUP_CRON_SECRET` — **lo stesso identico valore** impostato al punto 5

Il backup gira automaticamente ogni notte alle 02:00 UTC (~03:00-04:00 ora italiana a seconda dell'ora legale). Puoi verificare che funzioni controllando i log del servizio cron su Render dopo la prima esecuzione, oppure il Centro notifiche del gestionale (comparirà "Backup completato" o un errore).

**Passo finale, come hai giustamente richiesto tu stesso**: dopo la prima esecuzione riuscita, fai un **test di ripristino reale** (scarica il file dal bucket, decomprimilo, aprilo con un client SQLite qualsiasi e verifica che i dati ci siano) — un backup mai testato come restore non va considerato affidabile.

## Sources
- [Persistent Disks – Render Docs](https://render.com/docs/disks)
- [Render Postgres Recovery and Backups – Render Docs](https://render.com/docs/postgresql-backups)
