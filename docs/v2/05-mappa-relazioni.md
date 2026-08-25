# 05 — Mappa delle Relazioni

> 68 relazioni dichiarate come chiave esterna reale nello schema (`PRAGMA foreign_key_list`), più le relazioni concettuali NON dichiarate (elencate a parte). `practices` è il fulcro: riferita da 11 tabelle diverse.

## Entità centrale: PRATICA

```
                         ┌─────────────┐
                         │   clients   │◄──────────────┐ (non-FK: practices.client_id)
                         └─────────────┘                │
                                                          │
┌──────────────────┐    ON DELETE SET NULL    ┌─────────▼─────────┐
│  calendar_events  │───────────────────────► │      practices      │
│  (Ritiro/         │  linked_practice_id      │  (163 colonne)       │
│   Riconsegna)      │  ◄── inerte: mai         │                       │
└──────────────────┘      DELETE reale su      └───┬───┬───┬───┬───┬──┘
                           practices                 │   │   │   │   │
        ON DELETE CASCADE ─────────────────────┬─────┘   │   │   │   │
        ┌───────────────────────────────────────┘         │   │   │   │
        │            ON DELETE CASCADE ─────────────────────┘   │   │   │
        │            │             ON DELETE NO ACTION ─────────┘   │   │
        ▼            ▼             ▼                                 │   │
┌───────────────┐ ┌──────────────────┐ ┌─────────────────┐          │   │
│ practice_items │ │ movement_invoices│ │ practice_history │          │   │
└───────────────┘ └────────┬─────────┘ └──────────────────┘          │   │
                             │ N:N                                     │   │
                             ▼                                         │   │
                   ┌──────────────────────┐  ON DELETE CASCADE         │   │
                   │ movement_invoice_links│◄────────────┐             │   │
                   └──────────────────────┘              │             │   │
                                                            │             │   │
                                              ┌─────────────▼──────┐      │   │
                                              │  payment_movements  │◄─────┘   │
                                              │  ON DELETE CASCADE   │          │
                                              └──────────────────────┘          │
                                                                                 │
        ON DELETE SET NULL ───────────────────────────────────────────────────┘
        ┌────────────────┐   ┌────────────────────────┐   ┌──────────────────┐
        │  urn_movements   │   │ disposal_batch_practices│   │veterinarian_vouchers│
        └────────────────┘   │      ON DELETE CASCADE   │   │  ON DELETE NO ACTION │
                               └────────────────────────┘   └──────────────────┘

        ON DELETE NO ACTION (nessun FK dichiarato → integrità solo applicativa)
        ┌────────────────────┐  ┌───────────────────────┐
        │ whatsapp_messages    │  │whatsapp_inbound_messages│
        └────────────────────┘  └───────────────────────┘

        balance_movements.practice_id → NESSUN FK dichiarato (deliberato:
        preserva lo storico contabile anche se la pratica viene cancellata
        definitivamente — usa practice_number_snapshot come riferimento
        leggibile indipendente).

        notifications.practice_id / notification_group_items.practice_id
        → ON DELETE SET NULL
```

**Colonne di `practices` che rappresentano una relazione ma NON hanno una clausola `REFERENCES` nello schema** (integrità mantenuta solo dal codice applicativo, mai dal database):

| Colonna | Punta concettualmente a | Rischio |
|---|---|---|
| `client_id` | `clients.id` | Un client cancellato lascia pratiche con riferimento pendente non rilevabile dal DB |
| `collaborator_id` | `collaborators.id` | idem |
| `veterinarian_id` | `veterinarians.id` | idem |
| `owner_veterinarian_id` | `veterinarians.id` | idem |
| `origin_veterinarian_id` | `veterinarians.id` | idem |
| `used_voucher_id` | `veterinarian_vouchers.id` | idem |
| `urn_id` | `urns.id` | idem (e convive col modello più recente `practice_items`) |
| `urn_id_2` | `urns.id` | idem |

## Entità centrale: CALENDAR_EVENTS

```
calendar_events
 ├─ client_id ─────────────► clients          (SET NULL)
 ├─ veterinarian_id ───────► veterinarians    (SET NULL)
 ├─ delivery_clinic_id ────► veterinarians    (SET NULL)
 ├─ linked_practice_id ────► practices        (SET NULL — vedi nota sopra: inerte)
 ├─ assigned_user_id ──────► users            (SET NULL)
 ├─ created_by / updated_by / deleted_by ──► users (NO ACTION)
 │
 ├──(CASCADE)──► calendar_event_animals
 ├──(CASCADE)──► calendar_event_comments ──► users (autore/cancellatore)
 ├──(CASCADE)──► calendar_event_estimate_items
 ├──(CASCADE)──► calendar_event_history ──► users
 ├──(CASCADE)──► calendar_event_notifications
 └──(NO ACTION)──► route_plan_stops.event_id
```

## Entità centrale: CREMATION_CYCLES

```
cremation_cycles ◄──(NO ACTION)── practices.cremation_cycle_id
```
Relazione debole per design: **nessun CASCADE** — eliminare un ciclo lascia le pratiche con `cremation_cycle_id` pendente (coerente con quanto documentato in 03: `cremation_delete_cycle` scollega esplicitamente via codice applicativo, non conta sul DB).

## Ledger economico (BALANCE_MOVEMENTS)

```
balance_movements
 ├─ related_movement_id ──► balance_movements.id (self-reference, storni, ON DELETE RESTRICT)
 ├─ practice_id ─────────► NESSUN FK (deliberato)
 └─ created_by ───────────► users (implicito, nessun REFERENCES dichiarato — verificare)

payment_movements ──(CASCADE)──► practices
movement_invoices ──(CASCADE)──► practices
movement_invoice_links ──(CASCADE)──► movement_invoices, payment_movements
```

## Percorsi / Route optimization

```
route_plans
 ├─ start_location_id / end_location_id ──► company_locations (NO ACTION)
 └─ created_by ──► users

route_plan_stops
 ├─ route_plan_id ──(CASCADE)──► route_plans
 └─ event_id ──(NO ACTION)──► calendar_events
```

## Notifiche

```
notifications ──(CASCADE)──► users (destinatario)
              ──(SET NULL)──► practices
notification_group_items ──(CASCADE)──► notifications
                          ──(SET NULL)──► practices
notification_delivery_log ──(CASCADE)──► notifications
                           ──(SET NULL)──► push_subscriptions
push_subscriptions ──(CASCADE)──► users
```

## Cosa verificare in fase di migrazione (query di controllo da eseguire su PRODUZIONE, mai sul dev locale che è vuoto)

Poiché diverse relazioni non hanno FK dichiarata, prima della migrazione vanno eseguite query di controllo orfani esplicite, ad esempio:

```sql
-- Pratiche con client_id che punta a un cliente inesistente
SELECT COUNT(*) FROM practices
WHERE client_id IS NOT NULL AND client_id NOT IN (SELECT id FROM clients);

-- Pratiche con veterinarian_id/owner_veterinarian_id/origin_veterinarian_id orfano
SELECT COUNT(*) FROM practices
WHERE veterinarian_id IS NOT NULL AND veterinarian_id NOT IN (SELECT id FROM veterinarians);

-- Pratiche con collaborator_id orfano
SELECT COUNT(*) FROM practices
WHERE collaborator_id IS NOT NULL AND collaborator_id NOT IN (SELECT id FROM collaborators);

-- Pratiche con urn_id / urn_id_2 orfano
SELECT COUNT(*) FROM practices
WHERE (urn_id IS NOT NULL AND urn_id NOT IN (SELECT id FROM urns))
   OR (urn_id_2 IS NOT NULL AND urn_id_2 NOT IN (SELECT id FROM urns));

-- Eventi calendario collegati a pratiche già cestinate (soft-delete) — il caso
-- concreto già documentato: FK SET NULL mai scattata perché la pratica non è
-- mai stata DELETE reale
SELECT COUNT(*) FROM calendar_events ce
JOIN practices p ON p.id = ce.linked_practice_id
WHERE p.deleted_at IS NOT NULL AND p.deleted_at != '';

-- Pratiche con SIA total_service SIA total_text valorizzati (violazione
-- dell'invariante "un solo circuito", documentata in 03)
SELECT COUNT(*) FROM practices
WHERE COALESCE(total_service,'') != '' AND COALESCE(total_text,'') != '';

-- Fatture presenti solo in movement_invoices ma con practices.invoice_number
-- ancora vuoto (il bug del form pratica "vuoto" già corretto in UI, utile
-- per capire quante pratiche storiche ne sono ancora affette)
SELECT COUNT(DISTINCT mi.practice_id) FROM movement_invoices mi
JOIN practices p ON p.id = mi.practice_id
WHERE COALESCE(p.invoice_number,'') = '';
```

Il numero di righe restituito da ciascuna query è la base quantitativa reale su cui dimensionare la strategia di migrazione (documento 07) — oggi sono solo ipotesi qualitative, perché il database locale non contiene dati aziendali.

---

## Addendum — Query definitive di rilevamento anomalie (condizione Architecture Gate, doc 13 §4/§7)

> ⚠️ **NON eseguire queste query contro il database di produzione finché non è stato definito e concordato il processo sicuro di accesso al backup** (doc 13 §6 — restore verificato, ambiente isolato). Sono pronte per essere lanciate su una copia ripristinata da backup (staging), mai contro il database live in uso dagli operatori, anche in sola lettura, salvo tua esplicita autorizzazione. Completano l'elenco già presente sopra con i casi non ancora coperti, individuati dall'audit doc 13 §4.

```sql
-- 1. Pratiche con ENTRAMBI i circuiti W/D valorizzati (già presente sopra, ripetuta qui per completezza dell'elenco unico)
SELECT COUNT(*) FROM practices
WHERE COALESCE(total_service,'') != '' AND COALESCE(total_text,'') != '';

-- 2. Pratiche con anagrafica proprietario inline ma senza client_id collegato
-- (base quantitativa per la decisione di migrazione sul "client_id obbligatorio", doc 06)
SELECT COUNT(*) FROM practices
WHERE client_id IS NULL
  AND (COALESCE(owner_first_name,'') != '' OR COALESCE(owner_last_name,'') != '' OR COALESCE(owner_phone,'') != '');

-- 3. Fatture duplicate: stesso invoice_number in righe/fonti diverse con importi/date DIVERGENTI
-- (il caso classificato "C - richiede intervento umano" in doc 13 §4; il caso "B" con importi
-- identici è già coperto dalla query esistente sopra su movement_invoices/practices)
SELECT p.invoice_number, p.invoice_total AS practice_total, mi.invoice_total AS movement_total,
       p.invoice_date AS practice_date, mi.invoice_date AS movement_date
FROM practices p
JOIN movement_invoices mi ON mi.invoice_number = p.invoice_number AND mi.practice_id = p.id
WHERE p.invoice_number IS NOT NULL AND TRIM(p.invoice_number) != ''
  AND (p.invoice_total != mi.invoice_total OR p.invoice_date != mi.invoice_date);

-- 4a-d. FK concettuali orfane (client_id, veterinarian_id/owner_veterinarian_id/origin_veterinarian_id,
-- collaborator_id, urn_id/urn_id_2) - già presenti sopra come query singole; aggiunta la quinta orfana
-- non ancora coperta:
SELECT COUNT(*) FROM practices
WHERE used_voucher_id IS NOT NULL AND used_voucher_id NOT IN (SELECT id FROM veterinarian_vouchers);

-- 5. Righe orfane in payment_movements senza corrispondente in balance_movements
-- (nuova query, non presente nell'elenco originale - richiesta esplicitamente dal doc 13 §4)
-- Nota: non esiste una chiave di join diretta e affidabile fra le due tabelle (sono state
-- popolate da percorsi di scrittura paralleli, non da una FK dichiarata) - questa query usa
-- practice_id + importo + data come euristica di corrispondenza approssimata, e va quindi
-- validata a campione manualmente prima di trarre conclusioni quantitative definitive.
SELECT pm.id, pm.practice_id, pm.amount, pm.paid_at, pm.payment_channel
FROM payment_movements pm
WHERE NOT EXISTS (
  SELECT 1 FROM balance_movements bm
  WHERE bm.practice_id = pm.practice_id
    AND bm.amount_cents = CAST(ROUND(pm.amount * 100) AS INTEGER)
    AND substr(bm.movement_date,1,10) = substr(pm.paid_at,1,10)
);

-- 6. Stati incompatibili: ciclo di cremazione completato ma pratica collegata non ancora
-- 'Cremato'/'Da consegnare'/'Consegnato'/'Smaltito' (nuova query, richiesta dal doc 13 §4)
SELECT p.id, p.practice_number, p.status, cc.id AS cycle_id, cc.status AS cycle_status
FROM practices p
JOIN cremation_cycles cc ON cc.id = p.cremation_cycle_id
WHERE cc.status = 'completato'
  AND p.status NOT IN ('Cremato','Da consegnare','Consegnato','Smaltito');

-- 7. Eventi calendario collegati a pratiche cestinate (già presente sopra, ripetuta per completezza)
SELECT COUNT(*) FROM calendar_events ce
JOIN practices p ON p.id = ce.linked_practice_id
WHERE p.deleted_at IS NOT NULL AND p.deleted_at != '';

-- 8. Pratiche con collaborator_name valorizzato ma collaborator_id nullo dopo il tentativo di
-- risoluzione automatica (base quantitativa per l'Addendum F di doc 06 - collaborator_name_fallback)
SELECT COUNT(*) FROM practices
WHERE collaborator_id IS NULL AND COALESCE(collaborator_name,'') != '';
```

**Uso previsto**: eseguire l'intero blocco su una copia ripristinata da backup (mai in produzione), raccogliere i conteggi/righe reali, e usarli per:
- dimensionare quantitativamente la Fase B della migrazione (doc 07);
- assegnare la classificazione A/B/C/D reale (non più solo proposta) a ciascuna anomalia (doc 13 §4);
- decidere se le 8 decisioni aziendali ancora aperte (doc 06 Addendum, doc 14) sono urgenti in base al volume di casi reali che le riguardano, o se il volume è trascurabile e possono essere chiuse con una regola semplice.
