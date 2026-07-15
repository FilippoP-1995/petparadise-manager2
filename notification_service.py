"""Servizio modulare per storico e invio Web Push di Pet Paradise Manager."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path


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
}

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
      payload TEXT NOT NULL DEFAULT '{}'
    );
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
    """Registra uno storico per destinatario e avvia l'invio push senza bloccare."""
    if notification_type not in NOTIFICATION_TYPES:
        raise ValueError(f"Tipo notifica non registrato: {notification_type}")
    payload = dict(payload or {})
    if practice_id:
        payload.setdefault("url", f"/pratiche/{practice_id}")
        payload.setdefault("practice_id", practice_id)
    else:
        payload.setdefault("url", "/notifiche")
    created_at = datetime.now().isoformat(timespec="seconds")
    queued = []
    for user_id in _recipient_ids(conn, practice_id, actor_user_id, target_user_ids):
        if not preference_enabled(conn, user_id, notification_type):
            continue
        cur = conn.execute(
            """INSERT INTO notifications(user_id,actor_user_id,title,text,type,practice_id,created_at,payload)
               VALUES(?,?,?,?,?,?,?,?)""",
            (user_id, actor_user_id, title, text, notification_type, practice_id, created_at,
             json.dumps(payload, ensure_ascii=False)),
        )
        subscriptions = conn.execute(
            "SELECT id,endpoint,p256dh,auth FROM push_subscriptions WHERE user_id=?", (user_id,)
        ).fetchall()
        for subscription in subscriptions:
            queued.append({
                "notification_id": cur.lastrowid,
                "subscription_id": subscription["id"],
                "subscription": {"endpoint": subscription["endpoint"], "keys": {
                    "p256dh": subscription["p256dh"], "auth": subscription["auth"]}},
                "data": {"title": title, "body": text, "icon": "/assets/pwa-192.png", **payload,
                         "badge": "/assets/favicon-32.png", "tag": f"ppm-{notification_type}-{practice_id or cur.lastrowid}",
                         "type": notification_type, "notification_id": cur.lastrowid,
                         "url": f"/notifiche/{cur.lastrowid}/apri"},
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
                     datetime.now().isoformat(timespec="seconds")),
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
    current = datetime.now()
    today = current.date().isoformat()
    rows = conn.execute("""SELECT * FROM practices
                           WHERE (deleted_at IS NULL OR deleted_at='') AND pickup_date=?""", (today,)).fetchall()
    created = 0
    for row in rows:
        owner = " ".join(x for x in (row["owner_first_name"], row["owner_last_name"]) if x).strip()
        base = f'{row["animal_name"] or row["practice_number"]} · {owner or "Cliente non indicato"}'
        time_text = (row["pickup_time"] or "")[:5]
        if time_text and len(time_text) == 5:
            try:
                due = datetime.fromisoformat(f"{today}T{time_text}") - timedelta(minutes=30)
                if due <= current < due + timedelta(minutes=15):
                    created += _scheduled_once(conn, db_path, f"pickup-30m-{row['id']}-{today}-{time_text}", "pickup_30m",
                                               "⏰ Recupero tra 30 minuti", base, row["id"])
            except ValueError:
                pass
    unpaid = conn.execute("""SELECT * FROM practices WHERE (deleted_at IS NULL OR deleted_at='')
                             AND status='Consegnato' AND COALESCE(payment_status,'Da saldare')='Da saldare'""").fetchall()
    for row in unpaid:
        day = current.date().isoformat()
        created += _scheduled_once(conn, db_path, f"payment-due-{row['id']}-{day}", "payment_due",
                                   "⚠️ Pratica ancora da saldare", row["animal_name"] or row["practice_number"], row["id"])
    return created


def process_calendar_notifications(conn, db_path, current=None) -> int:
    """Deliver due calendar reminders and the idempotent 09:00 daily summary."""
    current=current or datetime.now();stamp=current.isoformat(timespec="seconds");created=0
    tables={row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "calendar_events" not in tables:return 0
    conn.execute("""UPDATE calendar_event_notifications SET status='annullato',error='Evento non piu attivo'
      WHERE status='programmato' AND event_id IN (SELECT id FROM calendar_events
      WHERE deleted_at IS NOT NULL OR COALESCE(event_status,'') IN ('Annullato','Completato'))""")
    due=conn.execute("""SELECT n.id,e.id event_id,e.title,e.start_at FROM calendar_event_notifications n
      JOIN calendar_events e ON e.id=n.event_id WHERE n.status='programmato' AND n.scheduled_at<=?
      AND (e.deleted_at IS NULL OR e.deleted_at='') AND COALESCE(e.event_status,'')!='Annullato' ORDER BY n.scheduled_at LIMIT 100""",(stamp,)).fetchall()
    for row in due:
        changed=conn.execute("UPDATE calendar_event_notifications SET status='in_invio' WHERE id=? AND status='programmato'",(row["id"],)).rowcount
        if not changed:continue
        try:
            emit_notification(conn,"calendar_reminder_30m","Evento tra 30 minuti",row["title"],payload={"url":f'/calendario/{row["event_id"]}'},db_path=db_path)
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
                text="\n".join(f'{(row["start_at"] or "")[11:16]} - {row["title"]}' for row in rows[:12])
                emit_notification(conn,"calendar_daily_summary",f"Eventi di oggi: {len(rows)}",text,payload={"url":f"/calendario?data={today}"},db_path=db_path);created+=1
    return created


def _scheduled_once(conn, db_path, key, kind, title, text, practice_id):
    if conn.execute("SELECT 1 FROM scheduled_notification_events WHERE event_key=?", (key,)).fetchone():
        return 0
    conn.execute("INSERT INTO scheduled_notification_events(event_key,created_at) VALUES(?,?)",
                 (key, datetime.now().isoformat(timespec="seconds")))
    emit_notification(conn, kind, title, text, practice_id=practice_id, db_path=db_path)
    return 1
