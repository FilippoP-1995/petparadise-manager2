# 08 — Strategia di Backup e Rollback (V2)

> Deve essere compatibile con `07-strategia-migrazione.md`. Copre due cose distinte: (A) backup/rollback del **dato** una volta che V2 gira su PostgreSQL: (B) backup/rollback del **codice/deploy** V2. Il backup indipendente di V1 (SQLite→B2) è già FACT, implementato e operativo — non viene ripetuto qui.

## A. Backup del dato in produzione V2 (PostgreSQL)

**DECISION**: applicare a Postgres lo stesso principio già validato per V1 — **non fidarsi di un solo livello di backup**, difesa su due strati indipendenti:

1. **Backup gestito Render Postgres** (FACT, da `00-backup-render-verifica.md`): sui piani a pagamento, backup logici automatici giornalieri conservati 7 giorni + point-in-time recovery (3 giorni piano Hobby, 7 giorni Pro+). Copre il caso "ripristina a un'ora precisa di ieri" con un click, senza infrastruttura da mantenere.
2. **Backup indipendente fuori Render**, stesso principio già implementato per V1: `pg_dump --format=custom` schedulato (stesso pattern cron→endpoint HTTP autenticato già collaudato con `cron_backup.py`/`backup_cron`), caricato sullo stesso bucket Backblaze B2 già attivo, con la stessa retention configurabile. **Perché non basta il punto 1**: un backup gestito dallo stesso fornitore che ospita anche l'applicazione non protegge da un problema sull'intero account/fornitore (blocco account, errore di fatturazione, incidente lato Render) — la copia indipendente su un provider diverso è l'unica vera protezione contro quello scenario, esattamente la stessa motivazione già usata per V1.
3. **Verifica di integrità automatica** dopo ogni dump (stesso principio di `verify_backup_integrity`: ripristino su un database temporaneo e controllo che le tabelle principali abbiano righe coerenti col conteggio atteso), non solo "il file esiste".
4. **Test di ripristino reale periodico** (stessa disciplina già dichiarata per V1): trimestrale, non solo alla prima attivazione — un backup mai restorato non è considerato affidabile (principio già espresso dall'utente, riapplicato qui).

**RISK esplicito**: nessuno dei due strati protegge da un errore applicativo che scrive dati sbagliati ma validi (es. un bug che azzera un importo con una UPDATE corretta sintatticamente) — per questo l'`audit_log` (doc 06) resta la difesa complementare: permette di capire *cosa* è cambiato e *quando*, non solo di tornare a uno snapshot precedente perdendo tutto il resto nel frattempo.

## B. Migrazioni dello schema — disciplina "expand/contract"

**DECISION**: ogni cambiamento allo schema V2 in produzione segue il pattern **expand/contract**, non mai una modifica distruttiva in un solo passo:

```
1. EXPAND   — aggiungi la nuova colonna/tabella, senza toccare o rimuovere quella vecchia. Deploy.
2. MIGRATE  — backfill dei dati esistenti nella nuova struttura (script idempotente, ri-eseguibile).
3. SWITCH   — il codice applicativo inizia a leggere/scrivere sulla nuova struttura. Deploy.
4. VERIFY   — periodo di osservazione con entrambe le strutture presenti, la vecchia non più scritta ma ancora leggibile per confronto.
5. CONTRACT — solo dopo verifica, migrazione separata che rimuove la struttura vecchia.
```

**Perché**: è la stessa applicazione, a livello di schema, del principio già dichiarato dall'utente per l'intero progetto — "ogni cambiamento ai dati deve essere reversibile". Con expand/contract, il passo 5 (l'unico realmente distruttivo) avviene solo quando il passo 4 ha dimostrato che tutto funziona, e può essere rimandato indefinitamente se emergono dubbi — non c'è un momento in cui i dati vecchi e quelli nuovi coesistono solo per un istante non osservabile.

**Tooling**: Alembic (stesso strumento della Fase B della migrazione, doc 07) gestisce sia le migrazioni "expand" sia quelle "contract" come migrazioni distinte e separatamente revertibili (`downgrade()` reale, testato, non un placeholder vuoto).

## C. Rollback del codice/deploy

- **DECISION**: Render mantiene lo storico delle build/deploy precedenti — rollback di codice = ripristino alla build precedente (funzionalità nativa Render, un click), sempre disponibile finché non si esegue un CONTRACT che renderebbe il codice precedente incompatibile con lo schema attuale.
- **Regola derivata**: un CONTRACT (passo 5 sopra) **non va mai deployato nello stesso rilascio** di un cambiamento applicativo rischioso — va isolato, cosicché un rollback del codice applicativo resti sempre possibile senza reintrodurre un problema di schema.
- **Health check pre-switch**: il deploy Render è configurato con un health-check endpoint (`/health`, già esistente in V1, da riprendere identico in V2) — un nuovo deploy che non risponde correttamente non riceve mai traffico, il precedente resta attivo automaticamente (comportamento nativo Render, nessuna azione manuale necessaria per questo caso).

## D. Scenario di perdita totale del servizio

Sequenza di ripristino, in ordine, nel caso peggiore (account Render compromesso, servizio cancellato, disco perso):

1. Nuovo servizio Postgres (gestito o self-hosted) da zero.
2. Ripristino dall'ultimo backup verificato su Backblaze B2 (indipendente da Render, quindi sopravvive anche alla perdita totale dell'account Render).
3. Verifica di integrità (stessa suite di controlli del doc 07, non solo "il ripristino non ha dato errori").
4. Redeploy del codice applicativo dall'ultimo commit su GitHub (repository, non su Render — altra ragione per cui il codice sorgente vive su Git e non solo sul server).
5. Riconfigurazione delle variabili d'ambiente/secrets (queste **non** sono nel backup del database né nel repository — vanno documentate a parte in un gestore password, mai committate in chiaro).

**RISK residuo dichiarato**: il punto 5 dipende da una gestione separata e disciplinata dei secrets (già oggi il caso: `BACKUP_S3_*`, `WHATSAPP_CRON_SECRET`, ecc. non sono nel repository) — non introduce un nuovo rischio rispetto a oggi, ma va esplicitamente mantenuto anche in V2.
