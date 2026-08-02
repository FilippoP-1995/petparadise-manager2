"""Servizio modulare per storico e invio Web Push di Pet Paradise Manager."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from calendar_service import event_type_emoji

ROME_TZ = ZoneInfo("Europe/Rome")


def _rome_now() -> datetime:
    """Ora attuale calcolata esplicitamente sul fuso di Roma, invece di
    affidarsi alla TZ di sistema (che sull'ambiente di deploy puo' restare
    UTC anche con la variabile d'ambiente TZ impostata): restituisce un
    datetime naive il cui valore rappresenta pero' l'ora civile italiana,
    cosi' che promemoria/riepiloghi orari (es. "Riepilogo del giorno" alle
    9:00) scattino davvero all'ora configurata e non con 1-2 ore di ritardo."""
    return datetime.now(ROME_TZ).replace(tzinfo=None)


NOTIFICATION_TYPES = {
    "practice_created": ("Nuova pratica", "🐾"),
    "practice_updated": ("Pratica modificata", "✏️"),
    "pickup_30m": ("Recupero tra 30 minuti", "⏰"),
    "delivery_scheduled": ("Consegna programmata", "📅"),
    "practice_delivered": ("Pratica consegnata", "📦"),
    "payment_received": ("Pagamento ricevuto", "💰"),
    "payment_due": ("Pratica ancora da saldare", "⚠️"),
    "whatsapp_sent": ("WhatsApp inviato", "📲"),
    "whatsapp_error": ("Errore invio WhatsApp", "❌"),
    "thank_you_sent": ("Messaggio di ringraziamento inviato", "💚"),
    "whatsapp_cron_error": ("Errore Cron WhatsApp", "❌"),
    "appointment_created": ("Nuovo appuntamento", "📆"),
    "appointment_reminder": ("Promemoria appuntamenti", "⏰"),
    "backup_completed": ("Backup completato", "✅"),
    "system_error": ("Errori di sistema", "🚨"),
    "push_test": ("Test notifiche push", "🔔"),
    "catalog_sent": ("Catalogo inviato", "📖"),
    "article_ordered": ("Articolo da ordinare", "📦"),
    "calendar_event_created": ("Evento calendario creato", "CAL"),
    "calendar_event_updated": ("Evento calendario modificato", "MOD"),
    "calendar_event_cancelled": ("Evento calendario annullato", "ANN"),
    "calendar_reminder_30m": ("Evento tra 30 minuti", "30M"),
    "calendar_daily_summary": ("Riepilogo calendario giornaliero", "OGGI"),
    "calendar_comment": ("Nuovo commento calendario", "MSG"),
    "daily_summary": ("Riepilogo del giorno", "☀️"),
    "cremation_cycle_waiting": ("Ciclo cremazione in attesa", "🔥"),
}

# iOS Safari (Web Push su PWA installata) non espone alcun modo, via API, di
# mostrare un'icona di categoria separata dall'icona dell'app nel banner
# nativo: ne' il campo "badge" ne' la Badging API renderizzano un simbolo nel
# banner, e la riga "da NomeApp" sotto il titolo e' generata da iOS stesso
# dal manifest, non modificabile via payload (verificato prima di
# implementare, come richiesto). L'unica leva realmente disponibile e'
# anteporre un singolo simbolo al TITOLO del push (mai al corpo, che resta
# sintetico senza emoji come da richiesta precedente) — qui solo per le
# categorie esplicitamente richieste, tutte le altre restano invariate.
NOTIFICATION_TITLE_SYMBOLS = {
    "daily_summary": "🔔",
    "calendar_daily_summary": "🔔",
    "appointment_reminder": "🔔",
    "calendar_reminder_30m": "🔔",
    "practice_updated": "✏️",
    "calendar_event_updated": "✏️",
    "practice_delivered": "📦",
    "delivery_scheduled": "📦",
    "pickup_30m": "🚚",
}


def notification_push_title(notification_type: str, title: str) -> str:
    """Titolo effettivamente mostrato nel banner push (mai quello salvato in
    notifications.title, usato invece dal Centro notifiche in-app): antepone
    il simbolo di categoria solo per i tipi mappati sopra."""
    symbol = NOTIFICATION_TITLE_SYMBOLS.get(notification_type)
    return f"{symbol} {title}" if symbol else title


# Tipi il cui invio push merita suono/vibrazione anche a telefono silenzioso
# in tasca: guasti, cose che bloccano un incasso, e un nuovo evento appena
# inserito in calendario (da notare subito, non solo quando si riapre
# l'app). Tutto il resto resta a priorità normale (visibile solo nel Centro
# notifiche e nel badge).
HIGH_PRIORITY_NOTIFICATION_TYPES = frozenset({
    "payment_due",
    "system_error",
    "whatsapp_error",
    "whatsapp_cron_error",
    "calendar_event_created",
})


def notification_priority(notification_type: str) -> str:
    return "alta" if notification_type in HIGH_PRIORITY_NOTIFICATION_TYPES else "normale"


def push_bullets(*parts) -> str:
    """Unisce solo le parti non vuote con '•', per corpi di notifica push
    sintetici che non mostrano mai un'informazione a valore zero/assente."""
    return " • ".join(str(p) for p in parts if p)


# Per tipo raggruppabile, l'etichetta (singolare, plurale) usata quando più
# occorrenze dello stesso tipo per lo stesso utente arrivano entro
# GROUP_WINDOW_MINUTES l'una dall'altra: invece di N notifiche push separate,
# la riga viene aggiornata sul posto con un riassunto ("5 nuovi ritiri oggi").
# Un tipo non elencato qui usa comunque il fallback generico "<etichetta> × N".
GROUP_WINDOW_MINUTES = 5
# Gli eventi di calendario restano sempre individuali (richiesta esplicita
# dell'utente: "ogni evento deve inviare la sua notifica"): raggrupparli,
# come si fa per altri tipi ad alto volume, faceva "sparire" visivamente
# eventi diversi dentro un'unica notifica riassuntiva con i testi concatenati,
# rendendo poco chiaro quanti e quali eventi fossero davvero arrivati.
NON_GROUPABLE_NOTIFICATION_TYPES = frozenset({
    "calendar_event_created",
    "calendar_event_updated",
    "calendar_event_cancelled",
    "calendar_reminder_30m",
})
NOTIFICATION_GROUP_LABELS = {
    "practice_created": ("nuovo ritiro", "nuovi ritiri"),
    "practice_delivered": ("pratica consegnata", "pratiche consegnate"),
    "payment_received": ("pagamento ricevuto", "pagamenti ricevuti"),
    "payment_due": ("pratica ancora da saldare", "pratiche ancora da saldare"),
    "article_ordered": ("prodotto da ordinare", "prodotti da ordinare"),
}


def _group_summary_text(conn: sqlite3.Connection, notification_type: str, count: int, notification_id: int | None = None) -> str:
    if notification_type == "calendar_event_created" and notification_id:
        # A differenza degli altri tipi raggruppati, qui il fallback generico
        # "Evento calendario creato × N" non diceva nulla di utile: l'utente
        # deve capire subito DI COSA si tratta (tipo, zona/sede, animale) anche
        # quando più eventi vengono creati in rapida successione. Ogni "text"
        # salvato per riga e' gia' il corpo arricchito (titolo + eventuale
        # specie/peso/cremazione), quindi basta elencarli.
        rows = conn.execute(
            "SELECT text FROM notification_group_items WHERE notification_id=? ORDER BY id DESC LIMIT 3",
            (notification_id,),
        ).fetchall()
        items = [row["text"] for row in rows if row["text"]]
        if items:
            items.reverse()
            summary = " • ".join(items)
            extra = count - len(items)
            return f"{summary} +{extra} altri" if extra > 0 else summary
    if notification_type in NOTIFICATION_GROUP_LABELS:
        singular, plural = NOTIFICATION_GROUP_LABELS[notification_type]
        return f"Oggi: {count} {plural if count > 1 else singular}"
    label = NOTIFICATION_TYPES.get(notification_type, ("Notifica", ""))[0]
    return f"{label} × {count}"

def ensure_notification_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS notifications (
      id INTEGER PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      actor_user_id INTEGER REFERENCES users(id),
      title TEXT NOT NULL,
      text TEXT NOT NULL,
      type TEXT NOT NULL,
      practice_id INTEGER REFERENCES practices(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL,
      read_at TEXT,
      is_read INTEGER NOT NULL DEFAULT 0,
      payload TEXT NOT NULL DEFAULT '{}',
      group_count INTEGER NOT NULL DEFAULT 1,
      archived_at TEXT
    );
    CREATE TABLE IF NOT EXISTS notification_group_items (
      id INTEGER PRIMARY KEY,
      notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      text TEXT NOT NULL,
      practice_id INTEGER REFERENCES practices(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notification_group_items_notification ON notification_group_items(notification_id);
    CREATE TABLE IF NOT EXISTS notification_preferences (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      type TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(user_id,type)
    );
    CREATE TABLE IF NOT EXISTS push_subscriptions (
      id INTEGER PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      endpoint TEXT UNIQUE NOT NULL,
      p256dh TEXT NOT NULL,
      auth TEXT NOT NULL,
      user_agent TEXT,
      device_name TEXT,
      platform TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      last_error TEXT
    );
    CREATE TABLE IF NOT EXISTS notification_delivery_log (
      id INTEGER PRIMARY KEY,
      notification_id INTEGER REFERENCES notifications(id) ON DELETE CASCADE,
      subscription_id INTEGER REFERENCES push_subscriptions(id) ON DELETE SET NULL,
      success INTEGER NOT NULL,
      error TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS scheduled_notification_events (
      event_key TEXT PRIMARY KEY,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications(user_id,is_read,created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_notifications_practice ON notifications(practice_id,created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id);
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(push_subscriptions)")}
    for name in ("device_name", "platform"):
        if name not in columns:
            conn.execute(f"ALTER TABLE push_subscriptions ADD COLUMN {name} TEXT")
    notification_columns = {row[1] for row in conn.execute("PRAGMA table_info(notifications)")}
    if "group_count" not in notification_columns:
        conn.execute("ALTER TABLE notifications ADD COLUMN group_count INTEGER NOT NULL DEFAULT 1")
    if "archived_at" not in notification_columns:
        conn.execute("ALTER TABLE notifications ADD COLUMN archived_at TEXT")


def preference_enabled(conn: sqlite3.Connection, user_id: int, notification_type: str) -> bool:
    row = conn.execute(
        "SELECT enabled FROM notification_preferences WHERE user_id=? AND type=?",
        (user_id, notification_type),
    ).fetchone()
    return row is None or bool(row["enabled"])


def _recipient_ids(conn, practice_id=None, actor_user_id=None, target_user_ids=None):
    if target_user_ids is not None:
        recipients = {int(value) for value in target_user_ids if value}
    else:
        recipients = {row["id"] for row in conn.execute("SELECT id FROM users WHERE active=1 AND role='admin'")}
        if practice_id:
            row = conn.execute("SELECT created_by FROM practices WHERE id=?", (practice_id,)).fetchone()
            if row and row["created_by"]:
                recipients.add(row["created_by"])
        if actor_user_id:
            recipients.add(int(actor_user_id))
    if not recipients:
        recipients = {row["id"] for row in conn.execute("SELECT id FROM users WHERE active=1")}
    return sorted(recipients)


def emit_notification(
    conn: sqlite3.Connection,
    notification_type: str,
    title: str,
    text: str,
    practice_id: int | None = None,
    actor_user_id: int | None = None,
    payload: dict | None = None,
    target_user_ids=None,
    db_path: str | Path | None = None,
):
    """Registra uno storico per destinatario e avvia l'invio push senza bloccare.

    Se più occorrenze dello stesso tipo per lo stesso destinatario arrivano
    entro GROUP_WINDOW_MINUTES l'una dall'altra, non viene inserita una nuova
    riga: quella già aperta viene aggiornata sul posto con un riassunto
    ("5 nuovi ritiri oggi") e il push riusa lo stesso tag, cosicché l'OS
    sostituisca visivamente la notifica precedente invece di accatastarne
    una nuova. Ogni occorrenza individuale resta comunque consultabile in
    notification_group_items (il "espandi" nel Centro notifiche).
    """
    if notification_type not in NOTIFICATION_TYPES:
        raise ValueError(f"Tipo notifica non registrato: {notification_type}")
    payload = dict(payload or {})
    if practice_id:
        payload.setdefault("url", f"/pratiche/{practice_id}")
        payload.setdefault("practice_id", practice_id)
    else:
        payload.setdefault("url", "/notifiche")
    action_url = payload.pop("action_url", None)
    action_label = payload.pop("action_label", None)
    created_at = _rome_now().isoformat(timespec="seconds")
    window_start = (_rome_now() - timedelta(minutes=GROUP_WINDOW_MINUTES)).isoformat(timespec="seconds")
    priority = notification_priority(notification_type)
    queued = []
    for user_id in _recipient_ids(conn, practice_id, actor_user_id, target_user_ids):
        if not preference_enabled(conn, user_id, notification_type):
            continue
        existing = None if notification_type in NON_GROUPABLE_NOTIFICATION_TYPES else conn.execute(
            """SELECT id,group_count FROM notifications
               WHERE user_id=? AND type=? AND is_read=0 AND created_at>=?
               ORDER BY id DESC LIMIT 1""",
            (user_id, notification_type, window_start),
        ).fetchone()
        if existing:
            notification_id = existing["id"]
            group_count = existing["group_count"] + 1
            conn.execute(
                """INSERT INTO notification_group_items(notification_id,title,text,practice_id,created_at)
                   VALUES(?,?,?,?,?)""",
                (notification_id, title, text, practice_id, created_at),
            )
            grouped_text = _group_summary_text(conn, notification_type, group_count, notification_id)
            conn.execute(
                """UPDATE notifications SET title=?,text=?,created_at=?,group_count=?,payload=?
                   WHERE id=?""",
                (title, grouped_text, created_at, group_count,
                 json.dumps(payload, ensure_ascii=False), notification_id),
            )
            push_text = grouped_text
        else:
            cur = conn.execute(
                """INSERT INTO notifications(user_id,actor_user_id,title,text,type,practice_id,created_at,payload)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (user_id, actor_user_id, title, text, notification_type, practice_id, created_at,
                 json.dumps(payload, ensure_ascii=False)),
            )
            notification_id = cur.lastrowid
            group_count = 1
            conn.execute(
                """INSERT INTO notification_group_items(notification_id,title,text,practice_id,created_at)
                   VALUES(?,?,?,?,?)""",
                (notification_id, title, text, practice_id, created_at),
            )
            push_text = text
        subscriptions = conn.execute(
            "SELECT id,endpoint,p256dh,auth FROM push_subscriptions WHERE user_id=?", (user_id,)
        ).fetchall()
        # un'azione rapida ha senso solo su un'occorrenza singola: una volta
        # raggruppata, non è più chiaro a quale elemento si applicherebbe.
        push_data = {"title": notification_push_title(notification_type, title), "body": push_text, "icon": "/assets/pwa-192.png", **payload,
                     "badge": "/assets/favicon-32.png", "tag": f"ppm-group-{notification_id}",
                     "type": notification_type, "notification_id": notification_id,
                     "priority": priority,
                     "url": f"/notifiche/{notification_id}/apri"}
        if group_count == 1 and action_url and action_label:
            push_data["action_url"] = action_url
            push_data["action_label"] = action_label
        for subscription in subscriptions:
            queued.append({
                "notification_id": notification_id,
                "subscription_id": subscription["id"],
                "subscription": {"endpoint": subscription["endpoint"], "keys": {
                    "p256dh": subscription["p256dh"], "auth": subscription["auth"]}},
                "data": push_data,
            })
    if queued and db_path:
        threading.Thread(target=_deliver_batch, args=(str(db_path), queued), daemon=True).start()
    return [item["notification_id"] for item in queued]


def _deliver_batch(db_path: str, queued: list[dict]) -> None:
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    subject = os.environ.get("VAPID_SUBJECT", "mailto:assistenza@petparadise.it").strip()
    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        WebPushException = Exception
        webpush = None
    for item in queued:
        success = 0
        error = ""
        remove_subscription = False
        try:
            if not private_key:
                raise RuntimeError("VAPID_PRIVATE_KEY non configurata")
            if webpush is None:
                raise RuntimeError("dipendenza pywebpush non disponibile")
            webpush(
                subscription_info=item["subscription"],
                data=json.dumps(item["data"], ensure_ascii=False),
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
                timeout=10,
            )
            success = 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            error = f"Web Push HTTP {status}" if status else f"Web Push {type(exc).__name__}"
            remove_subscription = status in (404, 410)
        except Exception as exc:
            error = f"Web Push {type(exc).__name__}: {str(exc)[:180]}"
        try:
            with sqlite3.connect(db_path, timeout=15) as conn:
                conn.execute(
                    "INSERT INTO notification_delivery_log(notification_id,subscription_id,success,error,created_at) VALUES(?,?,?,?,?)",
                    (item["notification_id"], item["subscription_id"], success, error,
                     _rome_now().isoformat(timespec="seconds")),
                )
                if remove_subscription:
                    conn.execute("DELETE FROM push_subscriptions WHERE id=?", (item["subscription_id"],))
                elif error:
                    conn.execute("UPDATE push_subscriptions SET last_error=? WHERE id=?", (error, item["subscription_id"]))
                else:
                    conn.execute("UPDATE push_subscriptions SET last_error='' WHERE id=?", (item["subscription_id"],))
        except Exception as exc:
            print(f"[PUSH] log invio non salvato: {type(exc).__name__}", flush=True)


def process_scheduled_notifications(conn, db_path) -> int:
    """Crea una sola volta i promemoria imminenti e i saldi attualmente dovuti."""
    current = _rome_now()
    today = current.date().isoformat()
    rows = conn.execute("""SELECT * FROM practices
                           WHERE (deleted_at IS NULL OR deleted_at='') AND pickup_date=?""", (today,)).fetchall()
    created = 0
    for row in rows:
        owner = " ".join(x for x in (row["owner_first_name"], row["owner_last_name"]) if x).strip()
        base = push_bullets(row["animal_name"] or row["practice_number"], owner or None, row["destination_branch"] or None)
        time_text = (row["pickup_time"] or "")[:5]
        if time_text and len(time_text) == 5:
            try:
                due = datetime.fromisoformat(f"{today}T{time_text}") - timedelta(minutes=30)
                if due <= current < due + timedelta(minutes=15):
                    created += _scheduled_once(conn, db_path, f"pickup-30m-{row['id']}-{today}-{time_text}", "pickup_30m",
                                               "Ritiro tra 30 minuti", base, row["id"])
            except ValueError:
                pass
    unpaid = conn.execute("""SELECT * FROM practices WHERE (deleted_at IS NULL OR deleted_at='')
                             AND status='Consegnato' AND COALESCE(payment_status,'Da saldare')='Da saldare'""").fetchall()
    for row in unpaid:
        day = current.date().isoformat()
        urgent_body = push_bullets(row["practice_number"], f'Ritiro {row["destination_branch"]}' if row["destination_branch"] else None)
        created += _scheduled_once(conn, db_path, f"payment-due-{row['id']}-{day}", "payment_due",
                                   "Pratica urgente", urgent_body, row["id"])
    return created


def process_calendar_notifications(conn, db_path, current=None) -> int:
    """Deliver due calendar reminders and the idempotent 09:00 daily summary."""
    current=current or _rome_now();stamp=current.isoformat(timespec="seconds");created=0
    tables={row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "calendar_events" not in tables:return 0
    conn.execute("""UPDATE calendar_event_notifications SET status='annullato',error='Evento non piu attivo'
      WHERE status='programmato' AND event_id IN (SELECT id FROM calendar_events
      WHERE deleted_at IS NOT NULL OR COALESCE(event_status,'') IN ('Annullato','Completato'))""")
    due=conn.execute("""SELECT n.id,e.id event_id,e.title,e.start_at,e.event_type FROM calendar_event_notifications n
      JOIN calendar_events e ON e.id=n.event_id WHERE n.status='programmato' AND n.scheduled_at<=?
      AND (e.deleted_at IS NULL OR e.deleted_at='') AND COALESCE(e.event_status,'')!='Annullato' ORDER BY n.scheduled_at LIMIT 100""",(stamp,)).fetchall()
    for row in due:
        changed=conn.execute("UPDATE calendar_event_notifications SET status='in_invio' WHERE id=? AND status='programmato'",(row["id"],)).rowcount
        if not changed:continue
        try:
            emit_notification(conn,"calendar_reminder_30m",f"{event_type_emoji(row['event_type'])} Evento tra 30 minuti",row["title"],payload={"url":f'/calendario/{row["event_id"]}'},db_path=db_path)
            conn.execute("UPDATE calendar_event_notifications SET status='inviato',sent_at=?,error='' WHERE id=?",(stamp,row["id"]));created+=1
        except Exception as exc:
            conn.execute("UPDATE calendar_event_notifications SET status='fallito',error=? WHERE id=?",(f"{type(exc).__name__}: {exc}"[:500],row["id"]))
    today=current.date().isoformat()
    if current.hour==9:
        rows=conn.execute("""SELECT id,title,start_at FROM calendar_events WHERE start_at<=? AND end_at>=?
          AND (deleted_at IS NULL OR deleted_at='') AND COALESCE(event_status,'') NOT IN ('Annullato','Completato') ORDER BY start_at""",(today+"T23:59:59",today+"T00:00:00")).fetchall()
        if rows:
            key=f"calendar-daily-summary-{today}"
            if not conn.execute("SELECT 1 FROM scheduled_notification_events WHERE event_key=?",(key,)).fetchone():
                conn.execute("INSERT INTO scheduled_notification_events(event_key,created_at) VALUES(?,?)",(key,stamp))
                if len(rows)==1:
                    text=push_bullets((rows[0]["start_at"] or "")[11:16],rows[0]["title"])
                else:
                    first_time=(rows[0]["start_at"] or "")[11:16]
                    text=push_bullets(f"{len(rows)} eventi",f"dalle {first_time}" if first_time else None)
                emit_notification(conn,"calendar_daily_summary","Eventi di oggi",text,payload={"url":f"/calendario?data={today}"},db_path=db_path);created+=1
    return created


def _scheduled_once(conn, db_path, key, kind, title, text, practice_id):
    if conn.execute("SELECT 1 FROM scheduled_notification_events WHERE event_key=?", (key,)).fetchone():
        return 0
    conn.execute("INSERT INTO scheduled_notification_events(event_key,created_at) VALUES(?,?)",
                 (key, _rome_now().isoformat(timespec="seconds")))
    emit_notification(conn, kind, title, text, practice_id=practice_id, db_path=db_path)
    return 1


def _daily_summary_counts(conn, today: str) -> tuple[int, int, int, int]:
    ritiri = conn.execute(
        """SELECT count(*) n FROM practices WHERE (deleted_at IS NULL OR deleted_at='')
           AND status='In programma' AND pickup_date=?""", (today,),
    ).fetchone()["n"]
    consegne = conn.execute(
        """SELECT count(*) n FROM practices WHERE (deleted_at IS NULL OR deleted_at='')
           AND status='Consegnato' AND date(updated_at)=?""", (today,),
    ).fetchone()["n"]
    incomplete = conn.execute(
        """SELECT count(*) n FROM practices WHERE (deleted_at IS NULL OR deleted_at='')
           AND data_complete=0 AND status!='Consegnato'""",
    ).fetchone()["n"]
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "reminders" in tables:
        other_reminders = conn.execute(
            "SELECT count(*) n FROM reminders WHERE completed_at IS NULL AND reminder_type!='practice_incomplete'",
        ).fetchone()["n"]
    else:
        other_reminders = 0
    return ritiri, consegne, incomplete, other_reminders


def _format_daily_summary(ritiri: int, consegne: int, incomplete: int, other_reminders: int) -> str:
    """Solo le categorie con almeno un elemento compaiono nel corpo, separate
    da '•': un riepilogo a zero voci non deve elencare zeri irrilevanti."""
    def plural(n, singular, plural_form):
        return f"{n} {singular if n == 1 else plural_form}"
    parts = []
    if ritiri:
        parts.append(plural(ritiri, "ritiro", "ritiri"))
    if consegne:
        parts.append(plural(consegne, "consegna", "consegne"))
    if incomplete:
        parts.append(plural(incomplete, "pratica da completare", "pratiche da completare"))
    if other_reminders:
        parts.append(plural(other_reminders, "promemoria", "promemoria"))
    return push_bullets(*parts) if parts else "Nessuna attività da segnalare"


TIME_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def process_daily_summaries(conn, db_path, current=None) -> int:
    """Invia a ciascun operatore il proprio 'Riepilogo del giorno', una sola
    volta al giorno, all'orario che ha impostato in Personalizza. Se non ha
    attivato esplicitamente l'interruttore, non riceve nulla. Controllato
    dallo stesso poll ogni 5 minuti del cron WhatsApp: una finestra di 10
    minuti assorbe l'imprecisione di quel poll senza mai inviarne due."""
    current = current or _rome_now()
    today = current.date().isoformat()
    rows = conn.execute(
        """SELECT user_id,
                  MAX(CASE WHEN key='daily_summary_enabled' THEN value END) enabled,
                  MAX(CASE WHEN key='daily_summary_time' THEN value END) time_value
           FROM user_preferences
           WHERE key IN ('daily_summary_enabled','daily_summary_time')
           GROUP BY user_id""",
    ).fetchall()
    created = 0
    for row in rows:
        if row["enabled"] != "1":
            continue
        time_value = (row["time_value"] or "").strip()
        if not TIME_HHMM_RE.match(time_value):
            continue
        try:
            configured = datetime.fromisoformat(f"{today}T{time_value}:00")
        except ValueError:
            continue
        if not (configured <= current < configured + timedelta(minutes=10)):
            continue
        key = f"daily-summary-{row['user_id']}-{today}"
        if conn.execute("SELECT 1 FROM scheduled_notification_events WHERE event_key=?", (key,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO scheduled_notification_events(event_key,created_at) VALUES(?,?)",
            (key, current.isoformat(timespec="seconds")),
        )
        ritiri, consegne, incomplete, other_reminders = _daily_summary_counts(conn, today)
        text = _format_daily_summary(ritiri, consegne, incomplete, other_reminders)
        emit_notification(
            conn, "daily_summary", "Riepilogo di oggi", text,
            target_user_ids=[row["user_id"]], payload={"url": "/"}, db_path=db_path,
        )
        created += 1
    return created


def archive_old_notifications(conn, current=None) -> int:
    """Sposta in archivio (non più nell'elenco principale di /notifiche, ma
    recuperabile col filtro 'Mostra archiviate') le notifiche lette da più
    di 30 giorni, per non far crescere all'infinito l'elenco principale."""
    current = current or _rome_now()
    cutoff = (current - timedelta(days=30)).isoformat(timespec="seconds")
    changed = conn.execute(
        """UPDATE notifications SET archived_at=?
           WHERE archived_at IS NULL AND is_read=1 AND read_at IS NOT NULL AND read_at<=?""",
        (current.isoformat(timespec="seconds"), cutoff),
    ).rowcount
    return changed
