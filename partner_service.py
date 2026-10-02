"""Portale Pet Paradise Partners: modello dati e regole di dominio (V1).

Modulo volutamente isolato (nessun import da app.py, nessun uso di
self/HTTP): ogni funzione riceve una connessione sqlite3 gia' aperta, cosi'
e' testabile da solo e portabile su V2.

Principi di progetto
--------------------
* Una richiesta del veterinario NON e' una pratica: crea un evento
  "Ritiro"/"Ritiro in sede" del calendario operativo in stato "Da
  confermare". La pratica nasce poi dal flusso gia' esistente
  "Crea pratica da evento" (calendar_events.linked_practice_id).
* Lo stato mostrato al veterinario (6-7 stati pubblici) e' DERIVATO dalla
  vista ``partner_request_status`` a partire da richiesta + evento +
  pratica: nessuno stato duplicato da tenere sincronizzato.
* La timeline (``partner_events``) e' append-only e si alimenta con trigger
  SQLite sulle tabelle del gestionale: qualunque punto del codice V1 cambi
  lo stato di un evento/pratica (sono piu' di 6) genera l'evento pubblico,
  solo quando lo stato pubblico cambia davvero.
* Ogni lettura e' filtrata per clinic_id: una clinica non puo' leggere,
  annullare o vedere eventi di un'altra.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import calendar_service as cal
from notification_service import PORTAL_BRAND, emit_notification, push_bullets

ROME_TZ = ZoneInfo("Europe/Rome")

MODES = {
    "ritiro_clinica": "Ritiro presso la clinica",
    "ritiro_domicilio": "Ritiro a domicilio",
    "invio_in_sede": "Cliente inviato in sede",
}
SERVICES = ("Cremazione singola", "Cremazione collettiva", "Cremazione da decidere")
# Fascia "tutto il giorno": evento calendario "tutto il giorno".
ALL_DAY = ("00:00", "23:59")
# Stesse sedi accettate da calendar_service.normalize_event per "Ritiro in sede".
BRANCHES = ("Livorno", "Empoli")
ROLES = ("titolare", "staff")

PUBLIC_STATUS_LABELS = {
    "ricevuta": "Richiesta ricevuta",
    "in_congelatore": "In congelatore, in attesa di ritiro",
    "programmato": "Ritiro programmato",
    "ritirato": "Ritirato",
    "in_lavorazione": "In lavorazione",
    "pronto_riconsegna": "Pronto per la riconsegna",
    "completata": "Completata",
    "annullata": "Annullata",
}
# Per chi porta l'animale in sede le parole cambiano, gli stati no.
IN_SEDE_LABELS = {
    "ricevuta": "Richiesta ricevuta, in attesa di arrivo in sede",
    "programmato": "Arrivo in sede confermato",
    "ritirato": "Arrivato in sede",
}
# Eventi pubblici che generano anche una notifica email in coda.
_NO_EMAIL_STATUSES = ("in_lavorazione",)

SYSTEM_USERNAME = "portale-partner"


class PartnerError(ValueError):
    """Errore di validazione/regola di dominio, con messaggio per l'utente."""


def _utc_now() -> str:
    return datetime.now(ROME_TZ).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rome_stamp() -> str:
    return datetime.now(ROME_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def _rome_today() -> date:
    return datetime.now(ROME_TZ).date()


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def public_status_label(status: str, mode: str = "") -> str:
    if mode == "invio_in_sede" and status in IN_SEDE_LABELS:
        return IN_SEDE_LABELS[status]
    return PUBLIC_STATUS_LABELS.get(status, status)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS partner_clinics (
  id INTEGER PRIMARY KEY,
  veterinarian_id INTEGER NOT NULL UNIQUE REFERENCES veterinarians(id),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  has_freezer INTEGER NOT NULL DEFAULT 0 CHECK(has_freezer IN (0,1)),
  vouchers_enabled INTEGER NOT NULL DEFAULT 0 CHECK(vouchers_enabled IN (0,1)),
  notify_email TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  is_demo INTEGER NOT NULL DEFAULT 0 CHECK(is_demo IN (0,1))
);
CREATE TABLE IF NOT EXISTS partner_users (
  id INTEGER PRIMARY KEY,
  clinic_id INTEGER NOT NULL REFERENCES partner_clinics(id),
  email TEXT NOT NULL UNIQUE CHECK(email=lower(email)),
  display_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK(role IN ('titolare','staff')),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL,
  last_login_at TEXT,
  username TEXT,
  password_hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_partner_users_clinic ON partner_users(clinic_id);
CREATE TABLE IF NOT EXISTS partner_sessions (
  token_hash TEXT PRIMARY KEY,
  partner_user_id INTEGER NOT NULL REFERENCES partner_users(id),
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_partner_sessions_user ON partner_sessions(partner_user_id);
CREATE TABLE IF NOT EXISTS partner_login_attempts (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL,
  ip TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_partner_login_attempts ON partner_login_attempts(created_at);
CREATE TABLE IF NOT EXISTS partner_requests (
  id INTEGER PRIMARY KEY,
  clinic_id INTEGER NOT NULL REFERENCES partner_clinics(id),
  requested_by INTEGER REFERENCES partner_users(id),
  client_request_id TEXT NOT NULL,
  request_code TEXT NOT NULL UNIQUE,
  mode TEXT NOT NULL CHECK(mode IN ('ritiro_clinica','ritiro_domicilio','invio_in_sede')),
  service_type TEXT NOT NULL CHECK(service_type IN ('Cremazione singola','Cremazione collettiva','Cremazione da decidere')),
  owner_first_name TEXT NOT NULL DEFAULT '',
  owner_last_name TEXT NOT NULL DEFAULT '',
  owner_phone TEXT NOT NULL DEFAULT '',
  animal_name TEXT NOT NULL DEFAULT '',
  species TEXT NOT NULL DEFAULT '',
  weight_text TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  pickup_address TEXT NOT NULL DEFAULT '',
  destination_site TEXT NOT NULL DEFAULT '',
  proposed_date TEXT NOT NULL DEFAULT '',
  proposed_from TEXT NOT NULL DEFAULT '',
  proposed_to TEXT NOT NULL DEFAULT '',
  urgent INTEGER NOT NULL DEFAULT 0 CHECK(urgent IN (0,1)),
  freezer INTEGER NOT NULL DEFAULT 0 CHECK(freezer IN (0,1)),
  reserved_voucher_id INTEGER REFERENCES veterinarian_vouchers(id),
  voucher_released_at TEXT,
  calendar_event_id INTEGER REFERENCES calendar_events(id) ON DELETE SET NULL,
  cancelled_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(clinic_id, client_request_id)
);
CREATE INDEX IF NOT EXISTS idx_partner_requests_clinic ON partner_requests(clinic_id, id);
CREATE INDEX IF NOT EXISTS idx_partner_requests_event ON partner_requests(calendar_event_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_requests_voucher_active
  ON partner_requests(reserved_voucher_id)
  WHERE reserved_voucher_id IS NOT NULL AND voucher_released_at IS NULL;
CREATE TABLE IF NOT EXISTS partner_events (
  id INTEGER PRIMARY KEY,
  clinic_id INTEGER NOT NULL REFERENCES partner_clinics(id),
  request_id INTEGER NOT NULL REFERENCES partner_requests(id),
  kind TEXT NOT NULL CHECK(kind IN ('stato','riprogrammato')),
  public_status TEXT NOT NULL,
  scheduled_start TEXT,
  scheduled_end TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_partner_events_clinic ON partner_events(clinic_id, id);
CREATE INDEX IF NOT EXISTS idx_partner_events_request ON partner_events(request_id, id);
CREATE TABLE IF NOT EXISTS partner_outbox (
  id INTEGER PRIMARY KEY,
  clinic_id INTEGER NOT NULL REFERENCES partner_clinics(id),
  request_id INTEGER NOT NULL REFERENCES partner_requests(id),
  event_id INTEGER NOT NULL UNIQUE REFERENCES partner_events(id),
  channel TEXT NOT NULL DEFAULT 'email',
  status TEXT NOT NULL DEFAULT 'in_attesa' CHECK(status IN ('in_attesa','inviata','fallita')),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_partner_outbox_status ON partner_outbox(status, id);
"""

# Stato pubblico derivato. Ordine dei rami = priorita'. Una pratica ancora in
# "Da ritirare" non decide nulla: vale lo stato dell'evento di ritiro.
_STATUS_VIEW = """
CREATE VIEW partner_request_status AS
SELECT r.id AS request_id,
  CASE
    WHEN r.cancelled_at IS NOT NULL THEN 'annullata'
    WHEN e.id IS NULL THEN CASE WHEN r.freezer=1 THEN 'in_congelatore' ELSE 'ricevuta' END
    WHEN e.deleted_at IS NOT NULL AND e.deleted_at<>'' THEN 'annullata'
    WHEN p.id IS NOT NULL AND p.status IN ('In programma','Cremato') THEN 'in_lavorazione'
    WHEN p.id IS NOT NULL AND p.status='Da consegnare' THEN 'pronto_riconsegna'
    WHEN p.id IS NOT NULL AND p.status IN ('Consegnato','Smaltito') THEN 'completata'
    WHEN p.id IS NOT NULL AND p.status='Ritirato' THEN 'ritirato'
    WHEN e.event_status='Annullato' THEN 'annullata'
    WHEN e.event_status='Ritirato' THEN 'ritirato'
    WHEN e.event_status='Da ritirare' THEN 'programmato'
    ELSE 'ricevuta'
  END AS public_status
FROM partner_requests r
LEFT JOIN calendar_events e ON e.id=r.calendar_event_id
LEFT JOIN practices p ON p.id=e.linked_practice_id AND (p.deleted_at IS NULL OR p.deleted_at='')
"""


def _emit_status_sql(selector: str) -> str:
    """INSERT in partner_events solo se lo stato pubblico e' cambiato."""
    return f"""
    INSERT INTO partner_events(clinic_id,request_id,kind,public_status,scheduled_start,scheduled_end,created_at)
    SELECT r.clinic_id, r.id, 'stato', v.public_status,
           CASE WHEN v.public_status='programmato' THEN e.start_at END,
           CASE WHEN v.public_status='programmato' THEN e.end_at END,
           strftime('%Y-%m-%dT%H:%M:%SZ','now')
    FROM partner_requests r
    JOIN partner_request_status v ON v.request_id=r.id
    LEFT JOIN calendar_events e ON e.id=r.calendar_event_id
    WHERE {selector}
      AND v.public_status <> COALESCE((SELECT x.public_status FROM partner_events x
                                        WHERE x.request_id=r.id ORDER BY x.id DESC LIMIT 1),'');
    """


_RELEASE_VOUCHER_BY_EVENT_SQL = """
    UPDATE partner_requests SET voucher_released_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
    WHERE calendar_event_id=NEW.id AND reserved_voucher_id IS NOT NULL AND voucher_released_at IS NULL;
"""

_TRIGGERS = {
    "partner_events_no_update": """
        CREATE TRIGGER partner_events_no_update BEFORE UPDATE ON partner_events
        BEGIN SELECT RAISE(ABORT,'partner_events e'' append-only'); END""",
    "partner_events_no_delete": """
        CREATE TRIGGER partner_events_no_delete BEFORE DELETE ON partner_events
        BEGIN SELECT RAISE(ABORT,'partner_events e'' append-only'); END""",
    "partner_request_inserted": f"""
        CREATE TRIGGER partner_request_inserted AFTER INSERT ON partner_requests
        BEGIN {_emit_status_sql('r.id=NEW.id')} END""",
    "partner_request_updated": f"""
        CREATE TRIGGER partner_request_updated
        AFTER UPDATE OF cancelled_at, calendar_event_id ON partner_requests
        BEGIN {_emit_status_sql('r.id=NEW.id')} END""",
    "partner_request_cancel_voucher": """
        CREATE TRIGGER partner_request_cancel_voucher
        AFTER UPDATE OF cancelled_at ON partner_requests
        WHEN NEW.cancelled_at IS NOT NULL AND NEW.reserved_voucher_id IS NOT NULL AND NEW.voucher_released_at IS NULL
        BEGIN
          UPDATE partner_requests SET voucher_released_at=NEW.cancelled_at WHERE id=NEW.id;
        END""",
    "partner_event_status": f"""
        CREATE TRIGGER partner_event_status
        AFTER UPDATE OF event_status, linked_practice_id, deleted_at ON calendar_events
        BEGIN {_emit_status_sql('r.calendar_event_id=NEW.id')} END""",
    "partner_event_release_voucher": f"""
        CREATE TRIGGER partner_event_release_voucher
        AFTER UPDATE OF event_status, linked_practice_id, deleted_at ON calendar_events
        WHEN NEW.linked_practice_id IS NOT NULL OR NEW.event_status='Annullato'
             OR (NEW.deleted_at IS NOT NULL AND NEW.deleted_at<>'')
        BEGIN {_RELEASE_VOUCHER_BY_EVENT_SQL} END""",
    # Orario cambiato mentre il ritiro e' gia' "programmato" (e l'ultimo evento
    # mostrato al veterinario ha un orario diverso): evento "riprogrammato".
    "partner_event_rescheduled": """
        CREATE TRIGGER partner_event_rescheduled
        AFTER UPDATE OF start_at, end_at ON calendar_events
        WHEN NEW.start_at IS NOT OLD.start_at OR NEW.end_at IS NOT OLD.end_at
        BEGIN
          INSERT INTO partner_events(clinic_id,request_id,kind,public_status,scheduled_start,scheduled_end,created_at)
          SELECT r.clinic_id, r.id, 'riprogrammato', 'programmato', NEW.start_at, NEW.end_at,
                 strftime('%Y-%m-%dT%H:%M:%SZ','now')
          FROM partner_requests r
          JOIN partner_request_status v ON v.request_id=r.id
          WHERE r.calendar_event_id=NEW.id AND v.public_status='programmato'
            AND (SELECT x.public_status FROM partner_events x WHERE x.request_id=r.id ORDER BY x.id DESC LIMIT 1)='programmato'
            AND (SELECT x.scheduled_start IS NOT NEW.start_at OR x.scheduled_end IS NOT NEW.end_at
                 FROM partner_events x WHERE x.request_id=r.id ORDER BY x.id DESC LIMIT 1);
        END""",
    "partner_practice_status": f"""
        CREATE TRIGGER partner_practice_status
        AFTER UPDATE OF status, deleted_at ON practices
        BEGIN {_emit_status_sql('r.calendar_event_id IN (SELECT id FROM calendar_events WHERE linked_practice_id=NEW.id)')} END""",
    "partner_event_outbox": f"""
        CREATE TRIGGER partner_event_outbox
        AFTER INSERT ON partner_events
        WHEN NEW.public_status NOT IN ({','.join("'" + s + "'" for s in _NO_EMAIL_STATUSES)})
        BEGIN
          INSERT INTO partner_outbox(clinic_id,request_id,event_id,created_at)
          VALUES(NEW.clinic_id,NEW.request_id,NEW.id,NEW.created_at);
        END""",
}


def _requests_table_ddl(name: str) -> str:
    match = re.search(r"CREATE TABLE IF NOT EXISTS partner_requests \((.*?)\n\);", _SCHEMA, re.S)
    return f"CREATE TABLE {name} ({match.group(1)}\n)"


def _migrate_requests_table(conn: sqlite3.Connection) -> None:
    """I database creati prima di "Cremazione da decidere" hanno un CHECK piu' stretto
    su service_type: SQLite non lo modifica con ALTER, quindi si ricostruisce la
    tabella (procedura ufficiale: nuova tabella, copia, drop, rename) conservando
    righe, id e collegamenti. Eventi, coda e vincoli esterni restano intatti."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='partner_requests'").fetchone()
    if not row or "Cremazione da decidere" in (row[0] or ""):
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("DROP VIEW IF EXISTS partner_request_status")
        for name in _TRIGGERS:
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(_requests_table_ddl("partner_requests_new"))
        conn.execute("INSERT INTO partner_requests_new SELECT * FROM partner_requests")
        conn.execute("DROP TABLE partner_requests")
        conn.execute("ALTER TABLE partner_requests_new RENAME TO partner_requests")
        conn.commit()
        conn.executescript(_SCHEMA)  # ricrea gli indici della tabella ricostruita
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def ensure_partner_schema(conn: sqlite3.Connection) -> None:
    """Crea tabelle, vista e trigger. Vista e trigger vengono ricreati a ogni
    avvio cosi' una modifica alla mappa degli stati arriva con il deploy.
    Richiede che le tabelle calendar_events e practices esistano gia'."""
    conn.executescript(_SCHEMA)
    # Database creati dalla fase 1 (senza login/demo): aggiunge le colonne mancanti.
    for table, column, ddl in (
        ("partner_clinics", "is_demo", "INTEGER NOT NULL DEFAULT 0"),
        ("partner_users", "username", "TEXT"),
        ("partner_users", "password_hash", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    _migrate_requests_table(conn)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_users_username "
                 "ON partner_users(lower(username)) WHERE username IS NOT NULL AND username<>''")
    conn.execute("DROP VIEW IF EXISTS partner_request_status")
    for name in _TRIGGERS:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute(_STATUS_VIEW)
    for ddl in _TRIGGERS.values():
        conn.execute(ddl)


# ---------------------------------------------------------------------------
# Cliniche e utenti (attivazione manuale da parte nostra)
# ---------------------------------------------------------------------------

def _normalize_email(value, required=True) -> str:
    email = str(value or "").strip().lower()
    if not email:
        if required:
            raise PartnerError("Indica l'email.")
        return ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 200:
        raise PartnerError("Email non valida.")
    return email


def create_clinic(conn, *, veterinarian_id, has_freezer=False, vouchers_enabled=False, notify_email="") -> int:
    vet = conn.execute("SELECT id FROM veterinarians WHERE id=? AND active=1", (veterinarian_id,)).fetchone()
    if not vet:
        raise PartnerError("Veterinario non trovato nell'anagrafica.")
    if conn.execute("SELECT 1 FROM partner_clinics WHERE veterinarian_id=?", (veterinarian_id,)).fetchone():
        raise PartnerError("Questo veterinario ha gia' il portale attivato.")
    stamp = _utc_now()
    cur = conn.execute(
        """INSERT INTO partner_clinics(veterinarian_id,active,has_freezer,vouchers_enabled,notify_email,created_at,updated_at)
           VALUES(?,1,?,?,?,?,?)""",
        (veterinarian_id, 1 if has_freezer else 0, 1 if vouchers_enabled else 0,
         _normalize_email(notify_email, required=False), stamp, stamp),
    )
    return cur.lastrowid


def update_clinic(conn, clinic_id, *, active=None, has_freezer=None, vouchers_enabled=None, notify_email=None) -> None:
    if not conn.execute("SELECT 1 FROM partner_clinics WHERE id=?", (clinic_id,)).fetchone():
        raise PartnerError("Clinica non trovata.")
    sets, args = [], []
    for column, value in (("active", active), ("has_freezer", has_freezer), ("vouchers_enabled", vouchers_enabled)):
        if value is not None:
            sets.append(f"{column}=?")
            args.append(1 if value else 0)
    if notify_email is not None:
        sets.append("notify_email=?")
        args.append(_normalize_email(notify_email, required=False))
    if not sets:
        return
    sets.append("updated_at=?")
    args.extend([_utc_now(), clinic_id])
    conn.execute(f"UPDATE partner_clinics SET {','.join(sets)} WHERE id=?", args)


def add_partner_user(conn, *, clinic_id, email, display_name="", role="staff") -> int:
    if not conn.execute("SELECT 1 FROM partner_clinics WHERE id=?", (clinic_id,)).fetchone():
        raise PartnerError("Clinica non trovata.")
    if role not in ROLES:
        raise PartnerError("Ruolo non valido.")
    email = _normalize_email(email)
    if conn.execute("SELECT 1 FROM partner_users WHERE email=?", (email,)).fetchone():
        raise PartnerError("Esiste gia' un utente del portale con questa email.")
    cur = conn.execute(
        "INSERT INTO partner_users(clinic_id,email,display_name,role,active,created_at) VALUES(?,?,?,?,1,?)",
        (clinic_id, email, _clean(display_name, 100), role, _utc_now()),
    )
    return cur.lastrowid


def set_partner_user_active(conn, user_id, active: bool) -> None:
    conn.execute("UPDATE partner_users SET active=? WHERE id=?", (1 if active else 0, user_id))


def get_clinic(conn, clinic_id):
    return conn.execute(
        """SELECT pc.*, v.clinic_name, v.short_name, v.city, v.address, v.phone
           FROM partner_clinics pc JOIN veterinarians v ON v.id=pc.veterinarian_id WHERE pc.id=?""",
        (clinic_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Buoni
# ---------------------------------------------------------------------------

def available_vouchers(conn, clinic_id):
    """Buoni 'Maturato' della clinica non riservati da una richiesta aperta."""
    clinic = conn.execute("SELECT veterinarian_id FROM partner_clinics WHERE id=?", (clinic_id,)).fetchone()
    if not clinic:
        return []
    return conn.execute(
        """SELECT vv.* FROM veterinarian_vouchers vv
           WHERE vv.veterinarian_id=? AND vv.status='Maturato'
             AND NOT EXISTS (SELECT 1 FROM partner_requests r
                             WHERE r.reserved_voucher_id=vv.id AND r.voucher_released_at IS NULL)
           ORDER BY vv.created_at, vv.id""",
        (clinic["veterinarian_id"],),
    ).fetchall()


# ---------------------------------------------------------------------------
# Richieste
# ---------------------------------------------------------------------------

def _system_user_id(conn) -> int:
    row = conn.execute("SELECT id FROM users WHERE username=?", (SYSTEM_USERNAME,)).fetchone()
    if row:
        return row["id"]
    # Utente tecnico NON attivo: non puo' accedere (il login filtra active=1) e
    # non compare tra i destinatari delle notifiche; serve solo come autore
    # (created_by) degli eventi creati dal portale.
    cur = conn.execute(
        "INSERT INTO users(username,password_hash,display_name,role,active) VALUES(?,?,?,?,0)",
        (SYSTEM_USERNAME, "!" + secrets.token_hex(16), "Portale partner", "operator"),
    )
    return cur.lastrowid


def _new_request_code(conn) -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    for _ in range(20):
        code = "RP-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not conn.execute("SELECT 1 FROM partner_requests WHERE request_code=?", (code,)).fetchone():
            return code
    raise PartnerError("Impossibile generare il codice richiesta, riprova.")


def _valid_time(value: str) -> bool:
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value or ""))


def _normalize_window(p_date, p_from, p_to, urgent):
    """Valida data + fascia proposte dalla clinica (urgente: data/fascia facoltative)."""
    today = _rome_today()
    if not p_date and urgent:
        p_date = today.isoformat()
    try:
        d = date.fromisoformat(p_date)
    except ValueError:
        raise PartnerError("Indica la data proposta per il ritiro.") from None
    if d < today:
        raise PartnerError("La data proposta non puo' essere nel passato.")
    if p_from or p_to:
        if not (_valid_time(p_from) and _valid_time(p_to)) or p_from >= p_to:
            raise PartnerError("Indica una fascia oraria valida (dalle ... alle ...).")
    elif not urgent:
        raise PartnerError("Indica la fascia oraria proposta per il ritiro.")
    return p_date, p_from, p_to


def _window_text(date_text, time_from, time_to, urgent) -> str:
    parts = []
    if date_text:
        d = date.fromisoformat(date_text)
        parts.append(d.strftime("%d/%m/%Y"))
    if (time_from, time_to) == ALL_DAY:
        parts.append("tutto il giorno")
    elif time_from and time_to:
        parts.append(f"{time_from}-{time_to}")
    text = " ".join(parts)
    return ("URGENTE " + text).strip() if urgent else text


def _split_weight(weight_text: str):
    """Il calendario/pratica vogliono il peso numerico (e aggiungono "kg" da soli):
    "12 kg" -> ("12", ""); una taglia a parole ("media") finisce nelle note."""
    text = _clean(weight_text, 30)
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(?:kg|chili|chilogrammi)?", text, re.IGNORECASE)
    if match:
        return match.group(1).replace(",", "."), ""
    return "", f"Taglia: {text}" if text else ""


def _insert_pickup_event(conn, *, request, clinic, created_by, status, start_date, start_time, end_time,
                         operator_name="", stamp):
    """Crea l'evento calendario del ritiro (+ animale + storico)."""
    in_sede = request["mode"] == "invio_in_sede"
    all_day = 1 if (not (start_time and end_time) or (start_time, end_time) == ALL_DAY) else 0
    start_at = f"{start_date}T{start_time or '00:00'}:00"
    end_at = f"{start_date}T{end_time or '23:59'}:59"
    clinic_label = clinic["short_name"] or clinic["clinic_name"]
    animal_label = request["animal_name"] or request["species"]
    title = " · ".join(part for part in (
        "PORTALE (PROVA)" if clinic["is_demo"] else "PORTALE", "URGENTE" if request["urgent"] else "",
        clinic_label, animal_label) if part)
    window = _window_text(request["proposed_date"], request["proposed_from"], request["proposed_to"], request["urgent"])
    notes = " | ".join(part for part in (
        f"Richiesta portale {request['request_code']}",
        f"Fascia proposta dalla clinica: {window}" if window else "",
        "In congelatore presso la clinica (non urgente)" if request["freezer"] else "",
        "Tipo di cremazione: da decidere" if request["service_type"] == "Cremazione da decidere" else "",
        f"Note: {request['notes']}" if request["notes"] else "",
    ) if part)
    columns = {
        "event_type": "Ritiro in sede" if in_sede else "Ritiro",
        "title": title,
        "zone": "" if in_sede else (clinic["city"] or ""),
        "location_type": "" if in_sede else ("Privato" if request["mode"] == "ritiro_domicilio" else "Veterinario"),
        "address": request["pickup_address"] if request["mode"] == "ritiro_domicilio" else "",
        "phone": request["owner_phone"],
        "veterinarian_id": clinic["veterinarian_id"],
        "veterinarian_name": clinic_label,
        "veterinarian_phone": clinic["phone"] or "",
        "veterinarian_address": clinic["address"] or "",
        "client_first_name": request["owner_first_name"],
        "client_last_name": request["owner_last_name"],
        "client_phone": request["owner_phone"],
        "destination_site": request["destination_site"] if in_sede else "",
        "animal_name": request["animal_name"],
        "start_at": start_at,
        "end_at": end_at,
        "all_day": all_day,
        "event_status": status,
        "payment_status": "",
        "payment_amount": 0,
        "notes": notes[:5000],
        "operator_name": operator_name,
        "created_by": created_by,
        "created_at": stamp,
        "updated_at": stamp,
        "updated_by": created_by,
    }
    cur = conn.execute(
        f"INSERT INTO calendar_events({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        tuple(columns.values()),
    )
    event_id = cur.lastrowid
    weight, size_note = _split_weight(request["weight_text"])
    cal.sync_children(conn, event_id, [{
        "name": request["animal_name"], "species": request["species"], "weight": weight,
        "cremation_type": {"Cremazione collettiva": "Collettiva", "Cremazione singola": "Singola"}.get(request["service_type"], ""),
        "notes": size_note,
    }], [], stamp)
    cal.add_history(conn, event_id, created_by, "Creazione evento", "", title, stamp)
    return event_id


def create_request(conn, *, clinic_id, user_id, client_request_id, mode, service_type,
                   species, weight, owner_first_name="", owner_last_name="", owner_phone="",
                   animal_name="", notes="", pickup_address="", destination_site="",
                   proposed_date="", proposed_from="", proposed_to="",
                   urgent=False, freezer=False, use_voucher=False, db_path=None):
    """Registra una richiesta del veterinario e il relativo evento calendario.

    Restituisce (riga_richiesta, creata). Se lo stesso client_request_id per la
    stessa clinica e' gia' stato inviato (doppio tap, rete lenta) restituisce la
    richiesta esistente con creata=False, senza creare nulla di nuovo.
    Va chiamata dentro una transazione del chiamante (``with db() as c``)."""
    clinic = get_clinic(conn, clinic_id)
    if not clinic or not clinic["active"]:
        raise PartnerError("Clinica non attiva.")
    if user_id is not None and not conn.execute(
            "SELECT 1 FROM partner_users WHERE id=? AND clinic_id=? AND active=1", (user_id, clinic_id)).fetchone():
        raise PartnerError("Utente non abilitato per questa clinica.")
    token = _clean(client_request_id, 80)
    if not token:
        raise PartnerError("Richiesta non valida: ricarica la pagina e riprova.")
    existing = conn.execute(
        "SELECT * FROM partner_requests WHERE clinic_id=? AND client_request_id=?", (clinic_id, token)).fetchone()
    if existing:
        return existing, False

    if mode not in MODES:
        raise PartnerError("Seleziona il tipo di servizio (ritiro in clinica, a domicilio o cliente in sede).")
    if service_type not in SERVICES:
        raise PartnerError("Seleziona il tipo di cremazione (anche \"da decidere\").")
    # Dati del proprietario facoltativi: se il telefono e' scritto deve pero' essere un numero.
    first, last = _clean(owner_first_name, 100), _clean(owner_last_name, 100)
    phone = _clean(owner_phone, 50)
    if phone and len(re.sub(r"\D", "", phone)) < 6:
        raise PartnerError("Il numero di telefono del proprietario non sembra valido (lascialo vuoto se non lo hai).")
    species = _clean(species, 100)
    if not species:
        raise PartnerError("Indica la specie dell'animale.")
    weight = _clean(weight, 30)
    if not weight:
        raise PartnerError("Indica il peso o la taglia dell'animale.")
    address = _clean(pickup_address, 500)
    if mode == "ritiro_domicilio" and not address:
        raise PartnerError("Indica l'indirizzo del ritiro a domicilio.")
    site = _clean(destination_site, 50)
    if mode == "invio_in_sede" and site not in BRANCHES:
        raise PartnerError("Seleziona la sede (Livorno o Empoli).")
    freezer, urgent, use_voucher = bool(freezer), bool(urgent), bool(use_voucher)

    p_date, p_from, p_to = _clean(proposed_date, 10), _clean(proposed_from, 5), _clean(proposed_to, 5)
    if freezer:
        if not clinic["has_freezer"]:
            raise PartnerError("Questa clinica non e' abilitata alla segnalazione del congelatore.")
        if service_type != "Cremazione collettiva":
            raise PartnerError("Il congelatore e' previsto solo per le cremazioni collettive.")
        if mode != "ritiro_clinica":
            raise PartnerError("Il congelatore e' previsto solo per il ritiro presso la clinica.")
        if urgent:
            raise PartnerError("Un animale in congelatore non puo' essere urgente.")
        p_date = p_from = p_to = ""
    else:
        p_date, p_from, p_to = _normalize_window(p_date, p_from, p_to, urgent)

    voucher_id = None
    if use_voucher:
        if service_type != "Cremazione collettiva":
            raise PartnerError("Il buono si puo' usare solo per le cremazioni collettive.")
        if not clinic["vouchers_enabled"]:
            raise PartnerError("Questa clinica non ha i buoni attivi.")
        free = available_vouchers(conn, clinic_id)
        if not free:
            raise PartnerError("Nessun buono disponibile.")
        voucher_id = free[0]["id"]

    stamp_utc = _utc_now()
    code = _new_request_code(conn)
    data = {
        "request_code": code, "mode": mode, "service_type": service_type,
        "owner_first_name": first, "owner_last_name": last, "owner_phone": phone,
        "animal_name": _clean(animal_name, 100), "species": species, "weight_text": weight,
        "notes": _clean(notes, 1000), "pickup_address": address if mode == "ritiro_domicilio" else "",
        "destination_site": site if mode == "invio_in_sede" else "",
        "proposed_date": p_date, "proposed_from": p_from, "proposed_to": p_to,
        "urgent": 1 if urgent else 0, "freezer": 1 if freezer else 0,
    }
    event_id = None
    if not freezer:
        event_id = _insert_pickup_event(
            conn, request=data, clinic=clinic, created_by=_system_user_id(conn), status="Da confermare",
            start_date=p_date, start_time=p_from, end_time=p_to, stamp=_rome_stamp())
    try:
        cur = conn.execute(
            """INSERT INTO partner_requests(clinic_id,requested_by,client_request_id,request_code,mode,service_type,
                 owner_first_name,owner_last_name,owner_phone,animal_name,species,weight_text,notes,pickup_address,
                 destination_site,proposed_date,proposed_from,proposed_to,urgent,freezer,reserved_voucher_id,
                 calendar_event_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (clinic_id, user_id, token, code, mode, service_type, first, last, phone, data["animal_name"], species,
             weight, data["notes"], data["pickup_address"], data["destination_site"], p_date, p_from, p_to,
             data["urgent"], data["freezer"], voucher_id, event_id, stamp_utc, stamp_utc))
    except sqlite3.IntegrityError as exc:
        # Gara con un doppio invio simultaneo o stesso buono preso da un'altra richiesta.
        existing = conn.execute(
            "SELECT * FROM partner_requests WHERE clinic_id=? AND client_request_id=?", (clinic_id, token)).fetchone()
        if existing:
            raise PartnerError("Richiesta gia' inviata, ricarica la pagina.") from exc
        raise PartnerError("Il buono selezionato non e' piu' disponibile, riprova.") from exc
    request = conn.execute("SELECT * FROM partner_requests WHERE id=?", (cur.lastrowid,)).fetchone()

    clinic_label = clinic["short_name"] or clinic["clinic_name"]
    animal_label = request["animal_name"] or request["species"]
    if freezer:
        emit_notification(
            conn, "partner_freezer_update", f"{PORTAL_BRAND} · Animale in congelatore",
            push_bullets(clinic_label, animal_label, "Collettiva", "nessuna fretta"),
            payload={"url": "/portale-partner"}, db_path=db_path)
    else:
        emit_notification(
            conn, "partner_request_urgent" if urgent else "partner_request_created",
            f"{PORTAL_BRAND} · RICHIESTA URGENTE" if urgent else f"{PORTAL_BRAND} · Nuova richiesta di ritiro",
            push_bullets(clinic_label, animal_label, MODES[mode],
                         _window_text(p_date, p_from, p_to, False)),
            payload={"url": f"/calendario/{event_id}"}, db_path=db_path)
    return request, True


def cancel_request(conn, *, clinic_id, request_id, user_id, db_path=None):
    """Annullamento da parte della clinica, possibile solo finche' Pet Paradise
    non ha preso in carico il ritiro (stato pubblico 'ricevuta'/'in_congelatore')."""
    request = get_request(conn, clinic_id, request_id)
    if not request:
        raise PartnerError("Richiesta non trovata.")
    if request["public_status"] not in ("ricevuta", "in_congelatore"):
        raise PartnerError("Il ritiro e' gia' stato preso in carico: contatta Pet Paradise per annullarlo.")
    stamp = _utc_now()
    if request["calendar_event_id"]:
        conn.execute(
            "UPDATE calendar_events SET event_status='Annullato',updated_at=? WHERE id=?",
            (_rome_stamp(), request["calendar_event_id"]))
        cal.add_history(conn, request["calendar_event_id"], _system_user_id(conn),
                        "Annullato dalla clinica (portale)", "", request["request_code"], _rome_stamp())
    conn.execute("UPDATE partner_requests SET cancelled_at=?,updated_at=? WHERE id=?", (stamp, stamp, request_id))
    emit_notification(
        conn, "partner_request_cancelled", f"{PORTAL_BRAND} · Richiesta annullata dalla clinica",
        push_bullets(request["clinic_label"], request["animal_name"] or request["species"], request["request_code"]),
        payload={"url": f"/calendario/{request['calendar_event_id']}" if request["calendar_event_id"] else "/portale-partner"},
        db_path=db_path)
    return get_request(conn, clinic_id, request_id)


def pending_freezer_requests(conn, clinic_id):
    return conn.execute(
        """SELECT * FROM partner_requests
           WHERE clinic_id=? AND freezer=1 AND calendar_event_id IS NULL AND cancelled_at IS NULL
           ORDER BY id""", (clinic_id,)).fetchall()


def plan_freezer_pickup(conn, *, clinic_id, start_date, start_time, end_time, operator_name,
                        staff_user_id, db_path=None):
    """Pianifica lo svuotamento del congelatore: un evento Ritiro (gia'
    'Da ritirare', cioe' confermato dallo staff) per ogni animale in attesa.
    Un evento per animale perche' la pratica si collega a un solo evento."""
    clinic = get_clinic(conn, clinic_id)
    if not clinic:
        raise PartnerError("Clinica non trovata.")
    try:
        date.fromisoformat(start_date)
    except ValueError:
        raise PartnerError("Indica una data valida.") from None
    if not (_valid_time(start_time) and _valid_time(end_time)) or start_time >= end_time:
        raise PartnerError("Indica una fascia oraria valida.")
    operator = _clean(operator_name, 50).title()
    if operator not in cal.CALENDAR_OPERATORS:
        raise PartnerError("Seleziona l'operatore.")
    pending = pending_freezer_requests(conn, clinic_id)
    if not pending:
        raise PartnerError("Nessun animale in congelatore da ritirare per questa clinica.")
    stamp = _rome_stamp()
    event_ids = []
    for request in pending:
        event_id = _insert_pickup_event(
            conn, request=request, clinic=clinic, created_by=staff_user_id, status="Da ritirare",
            start_date=start_date, start_time=start_time, end_time=end_time, operator_name=operator, stamp=stamp)
        conn.execute("UPDATE partner_requests SET calendar_event_id=?,updated_at=? WHERE id=?",
                     (event_id, _utc_now(), request["id"]))
        event_ids.append(event_id)
    emit_notification(
        conn, "partner_freezer_update", f"{PORTAL_BRAND} · Svuotamento congelatore pianificato",
        push_bullets(clinic["short_name"] or clinic["clinic_name"], f"{len(event_ids)} animali", start_date),
        payload={"url": f"/calendario/{event_ids[0]}"}, db_path=db_path)
    return event_ids


# ---------------------------------------------------------------------------
# Letture (sempre filtrate per clinica)
# ---------------------------------------------------------------------------

_REQUEST_SELECT = """
    SELECT r.*, v.public_status,
           COALESCE(NULLIF(vet.short_name,''), vet.clinic_name) AS clinic_label,
           e.start_at AS event_start, e.end_at AS event_end, e.event_status AS event_status,
           e.linked_practice_id AS practice_id
    FROM partner_requests r
    JOIN partner_request_status v ON v.request_id=r.id
    JOIN partner_clinics pc ON pc.id=r.clinic_id
    JOIN veterinarians vet ON vet.id=pc.veterinarian_id
    LEFT JOIN calendar_events e ON e.id=r.calendar_event_id
"""


def get_request(conn, clinic_id, request_id):
    return conn.execute(
        _REQUEST_SELECT + " WHERE r.clinic_id=? AND r.id=?", (clinic_id, request_id)).fetchone()


def list_requests(conn, clinic_id, *, limit=50, before_id=None):
    args = [clinic_id]
    sql = _REQUEST_SELECT + " WHERE r.clinic_id=?"
    if before_id:
        sql += " AND r.id<?"
        args.append(before_id)
    sql += " ORDER BY r.id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 200)))
    return conn.execute(sql, args).fetchall()


def request_timeline(conn, clinic_id, request_id):
    return conn.execute(
        """SELECT ev.* FROM partner_events ev
           JOIN partner_requests r ON r.id=ev.request_id
           WHERE ev.clinic_id=? AND r.clinic_id=? AND ev.request_id=? ORDER BY ev.id""",
        (clinic_id, clinic_id, request_id)).fetchall()


def events_since(conn, clinic_id, after_id=0, *, limit=100):
    """Eventi nuovi per l'aggiornamento periodico del portale (cursore = id)."""
    return conn.execute(
        """SELECT ev.*, r.request_code, r.mode FROM partner_events ev
           JOIN partner_requests r ON r.id=ev.request_id
           WHERE ev.clinic_id=? AND ev.id>? ORDER BY ev.id LIMIT ?""",
        (clinic_id, int(after_id or 0), max(1, min(int(limit), 500)))).fetchall()


def prefill_for_event(conn, event_id) -> dict:
    """Campi da precompilare nel form "Nuova pratica" aperto da un evento
    creato dal portale: buono per le singole delle cliniche in convenzione
    (matura alla creazione della pratica, come oggi in V1, e lo staff puo'
    togliere la spunta) e buono riservato per le collettive."""
    request = conn.execute(
        """SELECT r.*, pc.vouchers_enabled FROM partner_requests r
           JOIN partner_clinics pc ON pc.id=r.clinic_id
           WHERE r.calendar_event_id=? AND r.cancelled_at IS NULL""", (event_id,)).fetchone()
    if not request:
        return {}
    prefill = {}
    if request["service_type"] == "Cremazione singola" and request["vouchers_enabled"]:
        prefill["voucher_requested"] = "Si"
    if request["reserved_voucher_id"] and request["voucher_released_at"] is None:
        status = conn.execute(
            "SELECT status FROM veterinarian_vouchers WHERE id=?", (request["reserved_voucher_id"],)).fetchone()
        if status and status["status"] == "Maturato":
            prefill["use_voucher"] = "Si"
            prefill["used_voucher_id"] = str(request["reserved_voucher_id"])
    return prefill


def clinics_overview(conn):
    return conn.execute(
        """SELECT pc.*, v.clinic_name, v.short_name, v.city,
             (SELECT COUNT(*) FROM partner_users u WHERE u.clinic_id=pc.id AND u.active=1) AS users_count,
             (SELECT COUNT(*) FROM partner_requests r
                JOIN partner_request_status s ON s.request_id=r.id
                WHERE r.clinic_id=pc.id AND s.public_status IN ('ricevuta','programmato','ritirato')) AS open_requests,
             (SELECT COUNT(*) FROM partner_requests r
                WHERE r.clinic_id=pc.id AND r.freezer=1 AND r.calendar_event_id IS NULL AND r.cancelled_at IS NULL) AS freezer_pending,
             (SELECT COUNT(*) FROM veterinarian_vouchers vv
                WHERE vv.veterinarian_id=pc.veterinarian_id AND vv.status='Maturato') AS vouchers_matured
           FROM partner_clinics pc JOIN veterinarians v ON v.id=pc.veterinarian_id
           ORDER BY COALESCE(NULLIF(v.short_name,''), v.clinic_name)""").fetchall()


def recent_requests(conn, *, limit=30):
    """Ultime richieste di tutte le cliniche (solo per la pagina interna dello staff)."""
    return conn.execute(_REQUEST_SELECT + " ORDER BY r.id DESC LIMIT ?", (max(1, min(int(limit), 200)),)).fetchall()


# ---------------------------------------------------------------------------
# Svuotamento congelatore richiesto dalla clinica
# ---------------------------------------------------------------------------

def request_freezer_pickup(conn, *, clinic_id, user_id, proposed_date, proposed_from="", proposed_to="",
                           urgent=False, db_path=None):
    """La clinica ha il congelatore pieno: tutti gli animali in attesa diventano
    richieste di ritiro normali ("Da confermare") con la fascia proposta."""
    clinic = get_clinic(conn, clinic_id)
    if not clinic or not clinic["active"]:
        raise PartnerError("Clinica non attiva.")
    if user_id is not None and not conn.execute(
            "SELECT 1 FROM partner_users WHERE id=? AND clinic_id=? AND active=1", (user_id, clinic_id)).fetchone():
        raise PartnerError("Utente non abilitato per questa clinica.")
    pending = pending_freezer_requests(conn, clinic_id)
    if not pending:
        raise PartnerError("Nessun animale in congelatore da ritirare.")
    p_date, p_from, p_to = _normalize_window(
        _clean(proposed_date, 10), _clean(proposed_from, 5), _clean(proposed_to, 5), bool(urgent))
    stamp = _utc_now()
    system_user = _system_user_id(conn)
    event_ids = []
    for old in pending:
        conn.execute(
            "UPDATE partner_requests SET proposed_date=?,proposed_from=?,proposed_to=?,urgent=?,updated_at=? WHERE id=?",
            (p_date, p_from, p_to, 1 if urgent else 0, stamp, old["id"]))
        request = conn.execute("SELECT * FROM partner_requests WHERE id=?", (old["id"],)).fetchone()
        event_id = _insert_pickup_event(
            conn, request=request, clinic=clinic, created_by=system_user, status="Da confermare",
            start_date=p_date, start_time=p_from, end_time=p_to, stamp=_rome_stamp())
        conn.execute("UPDATE partner_requests SET calendar_event_id=?,updated_at=? WHERE id=?",
                     (event_id, _utc_now(), old["id"]))
        event_ids.append(event_id)
    emit_notification(
        conn, "partner_request_urgent" if urgent else "partner_request_created",
        f"{PORTAL_BRAND} · Congelatore pieno: richiesta di ritiro",
        push_bullets(clinic["short_name"] or clinic["clinic_name"], f"{len(event_ids)} animali",
                     _window_text(p_date, p_from, p_to, False)),
        payload={"url": f"/calendario/{event_ids[0]}"}, db_path=db_path)
    return event_ids


# ---------------------------------------------------------------------------
# Accesso al portale (sessioni lunghe, tentativi limitati)
# ---------------------------------------------------------------------------

SESSION_DAYS = 180
DEMO_SESSION_HOURS = 12
MAX_FAILED_PER_USERNAME = 8
MAX_FAILED_PER_IP = 30
FAILED_WINDOW_MINUTES = 15


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_plus(**delta) -> str:
    return (datetime.now(ZoneInfo("UTC")) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def login_blocked(conn, username, ip) -> bool:
    since = _utc_plus(minutes=-FAILED_WINDOW_MINUTES)
    by_user = conn.execute(
        "SELECT COUNT(*) FROM partner_login_attempts WHERE username=? AND created_at>=?",
        (_clean(username, 100).lower(), since)).fetchone()[0]
    by_ip = conn.execute(
        "SELECT COUNT(*) FROM partner_login_attempts WHERE ip=? AND created_at>=?",
        (_clean(ip, 64), since)).fetchone()[0] if ip else 0
    return by_user >= MAX_FAILED_PER_USERNAME or by_ip >= MAX_FAILED_PER_IP


def authenticate(conn, *, username, password, ip, verify_password, allow_demo=False):
    """Accesso con utente + password. Restituisce (utente, "") oppure (None, messaggio).
    Un account demo funziona solo se ``allow_demo`` (cioe' da un browser con
    sessione staff valida). Messaggi sempre generici: non rivela se l'utente
    esiste. Non solleva eccezioni: il tentativo fallito deve restare registrato
    anche se il chiamante e' dentro una transazione."""
    name = _clean(username, 100).lower()
    if login_blocked(conn, name, ip):
        return None, "Troppi tentativi di accesso. Riprova tra qualche minuto."
    user = conn.execute(
        """SELECT u.*, pc.is_demo FROM partner_users u JOIN partner_clinics pc ON pc.id=u.clinic_id
           WHERE lower(u.username)=? AND u.active=1 AND pc.active=1""", (name,)).fetchone() if name else None
    ok = bool(user and user["password_hash"] and verify_password(str(password or ""), user["password_hash"])
              and (allow_demo or not user["is_demo"]))
    if not ok:
        conn.execute("INSERT INTO partner_login_attempts(username,ip,created_at) VALUES(?,?,?)",
                     (name, _clean(ip, 64), _utc_now()))
        return None, "Credenziali non valide."
    conn.execute("DELETE FROM partner_login_attempts WHERE username=?", (name,))
    conn.execute("UPDATE partner_users SET last_login_at=? WHERE id=?", (_utc_now(), user["id"]))
    return user, ""


def create_session(conn, partner_user_id, *, demo=False) -> str:
    token = secrets.token_urlsafe(32)
    now = _utc_now()
    expires = _utc_plus(hours=DEMO_SESSION_HOURS) if demo else _utc_plus(days=SESSION_DAYS)
    conn.execute(
        "INSERT INTO partner_sessions(token_hash,partner_user_id,created_at,last_seen_at,expires_at) VALUES(?,?,?,?,?)",
        (_hash_token(token), partner_user_id, now, now, expires))
    conn.execute("DELETE FROM partner_sessions WHERE expires_at<?", (now,))
    return token


def session_user(conn, token):
    """Utente + clinica della sessione (None se scaduta/disattivata)."""
    if not token:
        return None
    return conn.execute(
        """SELECT u.id AS user_id, u.clinic_id, u.email, u.display_name, u.role, u.username,
                  pc.veterinarian_id, pc.has_freezer, pc.vouchers_enabled, pc.is_demo,
                  COALESCE(NULLIF(v.short_name,''), v.clinic_name) AS clinic_label,
                  v.clinic_name, v.city
           FROM partner_sessions s
           JOIN partner_users u ON u.id=s.partner_user_id
           JOIN partner_clinics pc ON pc.id=u.clinic_id
           JOIN veterinarians v ON v.id=pc.veterinarian_id
           WHERE s.token_hash=? AND s.expires_at>? AND u.active=1 AND pc.active=1""",
        (_hash_token(token), _utc_now())).fetchone()


def delete_session(conn, token) -> None:
    if token:
        conn.execute("DELETE FROM partner_sessions WHERE token_hash=?", (_hash_token(token),))


# ---------------------------------------------------------------------------
# Clinica di prova (anteprima per lo staff)
# ---------------------------------------------------------------------------

DEMO_USERNAME = "villadeipini"
DEMO_PASSWORD = "pini.vet26"
# Identita' della clinica dimostrativa: nome e dati verosimili (sono inventati), cosi' mostrando
# il portale a un veterinario sembra una clinica vera. L'email usa un dominio riservato
# (.example): nessun messaggio potra' mai arrivare a una persona reale.
DEMO_CLINIC = {
    "clinic_name": "Clinica Veterinaria Villa dei Pini", "short_name": "Villa dei Pini",
    "doctor_name": "Dott.ssa Giulia Ferretti", "address": "Via dei Pini 14", "city": "Livorno", "phone": "",
    "notes": "Clinica fittizia di dimostrazione del Portale Veterinari: puoi eliminarla quando non serve piu'.",
}
DEMO_EMAIL = "segreteria@villadeipini.example"
DEMO_DISPLAY_NAME = "Dott.ssa Giulia Ferretti"
DEMO_IDENTITY_VERSION = "2"
_OLD_DEMO_USERNAMES = ("provavet",)


def _upgrade_demo_identity(conn, hash_password):
    """La clinica di prova creata con il nome provvisorio ("PROVA VET", utente provavet)
    prende la nuova identita' realistica, mantenendo richieste, buoni e storico."""
    names = (DEMO_USERNAME,) + _OLD_DEMO_USERNAMES
    user = conn.execute(
        f"""SELECT u.id, u.clinic_id FROM partner_users u JOIN partner_clinics pc ON pc.id=u.clinic_id
            WHERE pc.is_demo=1 AND lower(u.username) IN ({','.join('?' for _ in names)}) ORDER BY u.id LIMIT 1""", names).fetchone()
    if user:
        vet_id = conn.execute("SELECT veterinarian_id FROM partner_clinics WHERE id=?", (user["clinic_id"],)).fetchone()[0]
        clinic = DEMO_CLINIC
        conn.execute(
            "UPDATE veterinarians SET clinic_name=?,short_name=?,doctor_name=?,phone=?,address=?,city=?,notes=?,updated_at=? WHERE id=?",
            (clinic["clinic_name"], clinic["short_name"], clinic["doctor_name"], clinic["phone"], clinic["address"],
             clinic["city"], clinic["notes"], _rome_stamp(), vet_id))
        conn.execute("UPDATE partner_users SET username=?,password_hash=?,email=?,display_name=? WHERE id=?",
                     (DEMO_USERNAME, hash_password(DEMO_PASSWORD), DEMO_EMAIL, DEMO_DISPLAY_NAME, user["id"]))
        conn.execute("DELETE FROM partner_sessions WHERE partner_user_id=?", (user["id"],))
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('partner_demo_version',?)", (DEMO_IDENTITY_VERSION,))


def ensure_demo_clinic(conn, hash_password):
    """Crea UNA volta la clinica dimostrativa (nome realistico, utente ``villadeipini``):
    congelatore e buoni attivi, due buoni di esempio. Se l'hai gia' creata (o eliminata)
    non la ricrea; una clinica creata con il vecchio nome provvisorio viene aggiornata
    all'identita' attuale. Restituisce l'id clinica creata o None."""
    flag = conn.execute("SELECT value FROM settings WHERE key='partner_demo_seeded'").fetchone()
    if flag and flag["value"] == "1":
        version = conn.execute("SELECT value FROM settings WHERE key='partner_demo_version'").fetchone()
        if not version or version["value"] != DEMO_IDENTITY_VERSION:
            _upgrade_demo_identity(conn, hash_password)
        return None
    if conn.execute("SELECT 1 FROM partner_users WHERE lower(username)=?", (DEMO_USERNAME,)).fetchone():
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('partner_demo_seeded','1')")
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('partner_demo_version',?)", (DEMO_IDENTITY_VERSION,))
        return None
    stamp = _rome_stamp()
    clinic = DEMO_CLINIC
    vet_id = conn.execute(
        """INSERT INTO veterinarians(clinic_name,short_name,doctor_name,phone,address,city,notes,active,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,1,?,?)""",
        (clinic["clinic_name"], clinic["short_name"], clinic["doctor_name"], clinic["phone"], clinic["address"],
         clinic["city"], clinic["notes"], stamp, stamp)).lastrowid
    clinic_id = create_clinic(conn, veterinarian_id=vet_id, has_freezer=True, vouchers_enabled=True)
    conn.execute("UPDATE partner_clinics SET is_demo=1 WHERE id=?", (clinic_id,))
    conn.execute(
        """INSERT INTO partner_users(clinic_id,email,display_name,role,active,created_at,username,password_hash)
           VALUES(?,?,?,?,1,?,?,?)""",
        (clinic_id, DEMO_EMAIL, DEMO_DISPLAY_NAME, "titolare", _utc_now(), DEMO_USERNAME, hash_password(DEMO_PASSWORD)))
    for i in range(2):
        conn.execute(
            "INSERT INTO veterinarian_vouchers(veterinarian_id,status,created_at,note) VALUES(?,?,?,?)",
            (vet_id, "Maturato", stamp, "Buono di esempio (clinica di dimostrazione)"))
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('partner_demo_seeded','1')")
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('partner_demo_version',?)", (DEMO_IDENTITY_VERSION,))
    return clinic_id


# ---------------------------------------------------------------------------
# Richieste da confermare (avvisi per lo staff)
# ---------------------------------------------------------------------------

PENDING_RECENT_DAYS = 90


def pending_requests(conn, *, limit=100):
    """Richieste ancora "da confermare" (stato pubblico 'ricevuta'): urgenti per prime, poi le piu' vecchie."""
    return conn.execute(
        _REQUEST_SELECT + " WHERE v.public_status='ricevuta' ORDER BY r.urgent DESC, r.id LIMIT ?",
        (max(1, min(int(limit), 500)),)).fetchall()


def pending_summary(conn) -> dict:
    """Conteggi per il banner sempre visibile: {"pending", "urgent", "newest_id"}."""
    since = (datetime.now(ROME_TZ) - timedelta(days=PENDING_RECENT_DAYS)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(r.urgent),0) AS u, COALESCE(MAX(r.id),0) AS newest
           FROM partner_requests r JOIN partner_request_status v ON v.request_id=r.id
           WHERE r.cancelled_at IS NULL AND r.created_at>=? AND v.public_status='ricevuta'""", (since,)).fetchone()
    return {"pending": row["n"], "urgent": row["u"], "newest_id": row["newest"]}


def age_minutes(created_at: str) -> int:
    try:
        born = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC"))
    except ValueError:
        return 0
    return max(0, int((datetime.now(ZoneInfo("UTC")) - born).total_seconds() // 60))
