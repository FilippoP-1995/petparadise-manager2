"""Isolated staff-shift-scheduling schema and domain helpers ("Turni operatori")."""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo
import datetime as _dt

ROME_TZ = ZoneInfo("Europe/Rome")


def _rome_now():
    return _dt.datetime.now(ROME_TZ).replace(tzinfo=None)


SHIFT_BRANCHES = ("Livorno", "Empoli")

# Lunedi' fisso usato come riferimento (settimana 0) per calcolare in modo
# deterministico e stabile ai riavvii quale operatore e' di reperibilita'
# in una data settimana, quando non esiste un override manuale salvato.
ONCALL_ROTATION_EPOCH = date(2024, 1, 1)


def ensure_shift_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS shifts (
      id INTEGER PRIMARY KEY,
      operator_name TEXT NOT NULL,
      work_date TEXT NOT NULL,
      branch TEXT NOT NULL,
      start_time TEXT,
      end_time TEXT,
      all_day INTEGER NOT NULL DEFAULT 0,
      created_by INTEGER NOT NULL REFERENCES users(id),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      updated_by INTEGER REFERENCES users(id),
      UNIQUE(operator_name, work_date)
    );
    CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts(work_date);

    CREATE TABLE IF NOT EXISTS shift_vacations (
      id INTEGER PRIMARY KEY,
      operator_name TEXT NOT NULL,
      start_date TEXT NOT NULL,
      end_date TEXT NOT NULL,
      note TEXT,
      created_by INTEGER NOT NULL REFERENCES users(id),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_shift_vacations_range ON shift_vacations(start_date,end_date);

    CREATE TABLE IF NOT EXISTS shift_oncall (
      id INTEGER PRIMARY KEY,
      week_start TEXT NOT NULL UNIQUE,
      operator_name TEXT NOT NULL,
      is_manual INTEGER NOT NULL DEFAULT 0,
      created_by INTEGER NOT NULL REFERENCES users(id),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    """)


def week_monday(d):
    return d - timedelta(days=d.weekday())


def week_bounds(monday):
    return monday, monday + timedelta(days=6)


def upsert_shift(conn, operator_name, work_date, branch, start_time, end_time, all_day, user_id, stamp=None):
    stamp = stamp or _rome_now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO shifts(operator_name,work_date,branch,start_time,end_time,all_day,created_by,created_at,updated_at,updated_by)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(operator_name,work_date) DO UPDATE SET
             branch=excluded.branch, start_time=excluded.start_time, end_time=excluded.end_time,
             all_day=excluded.all_day, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (operator_name, work_date, branch, start_time, end_time, 1 if all_day else 0,
         user_id, stamp, stamp, user_id),
    )


def delete_shift(conn, operator_name, work_date):
    conn.execute("DELETE FROM shifts WHERE operator_name=? AND work_date=?", (operator_name, work_date))


def get_shift(conn, operator_name, work_date):
    return conn.execute(
        "SELECT * FROM shifts WHERE operator_name=? AND work_date=?", (operator_name, work_date)
    ).fetchone()


def shifts_for_range(conn, start_date, end_date):
    """Ritorna {work_date_iso: {branch: [righe]}} per l'intervallo [start_date,end_date] incluso."""
    rows = conn.execute(
        "SELECT * FROM shifts WHERE work_date>=? AND work_date<=? ORDER BY operator_name",
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    by_day = {}
    for row in rows:
        by_day.setdefault(row["work_date"], {}).setdefault(row["branch"], []).append(row)
    return by_day


def vacations_overlapping(conn, start_date, end_date):
    return conn.execute(
        "SELECT * FROM shift_vacations WHERE start_date<=? AND end_date>=? ORDER BY start_date",
        (end_date.isoformat(), start_date.isoformat()),
    ).fetchall()


def create_vacation(conn, operator_name, start_date, end_date, note, user_id, stamp=None):
    stamp = stamp or _rome_now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO shift_vacations(operator_name,start_date,end_date,note,created_by,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?)""",
        (operator_name, start_date, end_date, note, user_id, stamp, stamp),
    )
    return cur.lastrowid


def list_vacations(conn):
    return conn.execute("SELECT * FROM shift_vacations ORDER BY start_date DESC").fetchall()


def delete_vacation(conn, vacation_id):
    conn.execute("DELETE FROM shift_vacations WHERE id=?", (vacation_id,))


def oncall_rotation_operator(monday, operators):
    if not operators:
        return None
    week_index = (monday - ONCALL_ROTATION_EPOCH).days // 7
    return operators[week_index % len(operators)]


def oncall_operator_for_week(conn, monday, operators):
    row = conn.execute("SELECT * FROM shift_oncall WHERE week_start=?", (monday.isoformat(),)).fetchone()
    if row:
        return row["operator_name"], bool(row["is_manual"])
    return oncall_rotation_operator(monday, operators), False


def save_oncall_week(conn, monday, operator_name, user_id, stamp=None):
    stamp = stamp or _rome_now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO shift_oncall(week_start,operator_name,is_manual,created_by,created_at,updated_at)
           VALUES(?,?,1,?,?,?)
           ON CONFLICT(week_start) DO UPDATE SET
             operator_name=excluded.operator_name, is_manual=1, updated_at=excluded.updated_at""",
        (monday.isoformat(), operator_name, user_id, stamp, stamp),
    )
