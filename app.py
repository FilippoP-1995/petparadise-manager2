from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from pdf_service import generate_ddt


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PPM_DATA_DIR", ROOT / "data"))
DB_PATH = DATA / "pet_paradise.db"
DDT_DIR = DATA / "ddt"
ASSETS = ROOT / "assets"
HOST = os.environ.get("PPM_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("PPM_PORT", "8080")))

STATES = [
    "Ritirato", "In programma", "Da consegnare", "Consegnato",
]

PAYMENT_STATES = [
    "Da saldare", "Acconto", "Pagato",
]

MONEY_FIELDS = {
    "price_cremation":"Cremazione", "price_pickup":"Ritiro", "price_urn":"Urna", "price_urn_2":"Seconda urna",
    "price_delivery":"Riconsegna", "price_cast":"Calco", "price_cast_2":"Secondo calco", "price_evening":"Serale",
    "price_night":"Notturno", "price_holiday":"Festivo", "price_accessories":"Accessori", "price_accessories_2":"Secondi accessori",
    "total_service":"Totale servizio", "total_text":"TOTALE D", "deposit":"Acconto", "remaining_balance":"Rimanenza",
}

COLLABORATORS = {
    "HUMANITAS CROCE VERDE": {
        "street": "VIA ROMANA, 907",
        "zip": "55100",
        "city": "LUCCA",
        "province": "LU",
        "vat": "01762490462",
        "sdi": "M5UXCR1",
    }
}

BRANCHES = {
    "Livorno": {
        "address": "Via dei Materassai, 10 - 57121 Livorno (LI)",
        "plant_type": "FORNO CREMATORIO",
    },
    "Empoli": {
        "address": "Via Renato Fucini, 23 - 50053 Empoli (FI)",
        "plant_type": "IMPRESA FUNEBRE",
    },
}



VETERINARIAN_ANAGRAFICHE = [
    {"code":"DEL PERO", "name":"Ambulatorio Veterinario Andrea Del Pero", "address":"Corso Amedeo, 285, 57125 Livorno LI", "phone":"+39 0586 881022", "city":"Livorno"},
    {"code":"RAZZAUTI", "name":"Studio Associato Veterinario Razzauti Daolio Anguillesi", "address":"Via del Lavoro, 11, 57122 Livorno LI", "phone":"+39 0586 951478", "city":"Livorno"},
    {"code":"LEMMI", "name":"Studio Medico Veterinario Dr. Mario Lemmi", "address":"Viale Ippolito Nievo, 39, 57122 Livorno LI", "phone":"+39 0586 408370", "city":"Livorno"},
    {"code":"AURELIA", "name":"Clinica Veterinaria Aurelia", "address":"Via Aurelia, 136 A, 57014 Stagno LI", "phone":"+39 0586 941089", "city":"Livorno"},
    {"code":"BARSACCHI E SANDRI", "name":"Ambulatorio Veterinario Barsacchi - Sandri", "address":"Via Giotto, 29, 57128 Livorno LI", "phone":"+39 0586 861433", "city":"Livorno"},
    {"code":"CAMPO DI MARTE", "name":"Centro Veterinario Campo Di Marte", "address":"Via dell'Artigianato, 39/c, 57121 Livorno LI", "phone":"+39 0586 405000", "city":"Livorno"},
    {"code":"CARDIOVET", "name":"Clinica Veterinaria Cardio Vet", "address":"Via dei Pelaghi, 100, 57124 Livorno LI", "phone":"+39 0586 839956", "city":"Livorno"},
    {"code":"NESTI", "name":"Dr.ssa Sabrina Nesti presso Ambulatorio Veterinario San Matteo", "address":"Via del Vigna, 290, 57121 Livorno LI", "phone":"+39 0586 423293", "city":"Livorno"},
    {"code":"IL TIRRENO", "name":"Ambulatorio Il Tirreno Veterinario", "address":"Via Fabio Campana, 5, 57124 Livorno LI", "phone":"+39 330 619 816", "city":"Livorno"},
    {"code":"CIMAROSA", "name":"Clinica Veterinaria Cimarosa Livorno", "address":"Via Giovan Battista Lulli, 35, 57124 Livorno LI", "phone":"+39 0586 854494", "city":"Livorno"},
    {"code":"ACQUAVIVA/ACCADEMIA", "name":"Ambulatorio Veterinario Acquaviva", "address":"Via San Jacopo In Acquaviva, 152, 57127 Livorno LI", "phone":"+39 0586 812839", "city":"Livorno"},
    {"code":"LA MARMORA", "name":"Clinica Veterinaria Lamarmora", "address":"Via della Torretta, 61, 57122 Livorno LI", "phone":"+39 0586 890782", "city":"Livorno"},
]

DEFAULT_VETERINARIANS = [
    ("DEL PERO", "LIVORNO"),
    ("RAZZAUTI", "LIVORNO"),
    ("LEMMI", "LIVORNO"),
    ("AURELIA", "LIVORNO"),
    ("BARSACCHI E SANDRI", "LIVORNO"),
    ("CAMPO DI MARTE", "LIVORNO"),
    ("CAMPO D'AVIAZIONE", "VIAREGGIO"),
    ("GLI AMICI DI BLU", "VIAREGGIO"),
    ("FERRANDELLO", "PIETRASANTA"),
    ("VARIGNANO", "VIAREGGIO"),
    ("DANTE DELLE ROSE", "EMPOLI"),
    ("PARLANTI", "EMPOLI"),
    ("CARDIOVET", "LIVORNO"),
    ("NESTI", "LIVORNO"),
    ("IL TIRRENO", "LIVORNO"),
    ("CIMAROSA", "LIVORNO"),
    ("ACQUAVIVA", "LIVORNO"),
    ("LA MARMORA", "LIVORNO"),
    ("ACCADEMIA", "LIVORNO"),
    ("ARDENZA", "LIVORNO"),
    ("SANMINIANIMAL", "EMPOLI"),
    ("GIULIA FRATI", "EMPOLI"),
    ("GENNARI", "EMPOLI"),
    ("BARTOLI", "EMPOLI"),
    ("BELLUCCI", "EMPOLI"),
    ("CROCE AZZURRA", "EMPOLI"),
    ("LA FENICE", "EMPOLI"),
    ("MATTEINI", "EMPOLI"),
    ("FREDIANI", "EMPOLI"),
    ("IL POGGETTO", "FIRENZE"),
    ("COMASSI", "LIVORNO"),
    ("SAN PIERO A GRADO", "PISA"),
    ("BARBARICINA", "PISA"),
    ("LUCY", "EMPOLI"),
    ("ARIOSTO", "FIRENZE"),
    ("COMACCHIO", "LIVORNO"),
]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"{salt.hex()}:{digest.hex()}"


def password_ok(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split(":", 1)
    candidate = password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
    return hmac.compare_digest(candidate, digest_hex)


def init_db():
    DATA.mkdir(exist_ok=True)
    DDT_DIR.mkdir(exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, display_name TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'operator', active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS practices (
          id INTEGER PRIMARY KEY, practice_number TEXT UNIQUE NOT NULL,
          request_origin TEXT NOT NULL, destination_branch TEXT NOT NULL,
          status TEXT NOT NULL, data_complete INTEGER NOT NULL DEFAULT 0,
          owner_first_name TEXT, owner_last_name TEXT, owner_phone TEXT,
          owner_email TEXT, owner_tax_code TEXT, owner_address TEXT,
          pickup_address TEXT, pickup_date TEXT, pickup_time TEXT,
          animal_name TEXT, species TEXT, breed TEXT, age_years TEXT,
          age_months TEXT, estimated_weight TEXT, microchip TEXT,
          service_type TEXT, clinic_name TEXT, veterinarian_name TEXT,
          notes TEXT, ddt_number INTEGER UNIQUE, ddt_date TEXT, ddt_pdf TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by INTEGER REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS clients (
          id INTEGER PRIMARY KEY,
          first_name TEXT,
          last_name TEXT,
          company_name TEXT,
          phone TEXT,
          phone_2 TEXT,
          email TEXT,
          tax_code TEXT,
          vat_number TEXT,
          street TEXT,
          city TEXT,
          province TEXT,
          zip TEXT,
          address TEXT,
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS practice_history (
          id INTEGER PRIMARY KEY, practice_id INTEGER NOT NULL REFERENCES practices(id),
          event_type TEXT NOT NULL, old_value TEXT, new_value TEXT,
          note TEXT, user_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS veterinarians (
          id INTEGER PRIMARY KEY,
          clinic_name TEXT NOT NULL,
          doctor_name TEXT,
          phone TEXT,
          notes TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS veterinarian_vouchers (
          id INTEGER PRIMARY KEY,
          veterinarian_id INTEGER NOT NULL REFERENCES veterinarians(id),
          practice_id INTEGER REFERENCES practices(id),
          status TEXT NOT NULL DEFAULT 'Maturato',
          created_at TEXT NOT NULL,
          used_at TEXT,
          note TEXT,
          UNIQUE(practice_id)
        );
        CREATE TABLE IF NOT EXISTS whatsapp_messages (
          id INTEGER PRIMARY KEY,
          practice_id INTEGER NOT NULL REFERENCES practices(id),
          scheduled_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'programmato',
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          message_id TEXT,
          sent_at TEXT,
          delivered_at TEXT,
          read_at TEXT,
          failed_at TEXT,
          last_attempt_at TEXT,
          template_name TEXT,
          language_code TEXT,
          recipient_phone TEXT,
          payload_json TEXT,
          response_json TEXT,
          manual INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO settings(key,value) VALUES('next_practice_number','1');
        INSERT OR IGNORE INTO settings(key,value) VALUES('next_cr_number','1');
        INSERT OR IGNORE INTO settings(key,value) VALUES('next_sm_number','1');
        INSERT OR IGNORE INTO settings(key,value) VALUES('next_ddt_number','1');
        """)
        status_migrations = {
            "Da ritirare": "Ritirato",
            "Dati da completare": "Ritirato",
            "In cella frigo": "Ritirato",
            "In attesa cremazione": "Ritirato",
            "Ceneri pronte": "Da consegnare",
            "Consegnata": "Consegnato",
            "Messo in programma": "In programma",
            "Cremato": "In programma",
        }
        for old_status, new_status in status_migrations.items():
            c.execute("UPDATE practices SET status=? WHERE status=?", (new_status, old_status))
        extra_columns = {
            "operator_name": "TEXT",
            "transport_method": "TEXT", "vehicle_plate": "TEXT",
            "temperature_mode": "TEXT DEFAULT 'Ambiente'", "package_count": "TEXT DEFAULT '1'",
            "container_id": "TEXT", "lot_number": "TEXT DEFAULT '/'", "treatment_method": "TEXT DEFAULT '/'",
            "price_cremation": "TEXT", "price_pickup": "TEXT", "price_evening": "TEXT",
            "price_urn": "TEXT", "price_delivery": "TEXT", "price_night": "TEXT",
            "urn_notes": "TEXT",
            "price_cast": "TEXT", "price_holiday": "TEXT", "price_accessories": "TEXT",
            "price_urn_2": "TEXT", "urn_notes_2": "TEXT", "price_cast_2": "TEXT",
            "price_accessories_2": "TEXT", "accessory_type": "TEXT", "accessory_type_2": "TEXT",
            "send_catalog": "TEXT",
            "send_estremi": "TEXT",
            "deposit": "TEXT", "remaining_balance": "TEXT", "total_service": "TEXT", "total_text": "TEXT", "identity_document_number": "TEXT",
            "identity_document_date": "TEXT", "signing_place": "TEXT",
            "pickup_address_mode": "TEXT DEFAULT 'Idem sped.'",
            "transporter_mode": "TEXT DEFAULT 'IDEM SPED'",
            "origin_mode": "TEXT DEFAULT 'IDEM SPED'",
            "origin_text": "TEXT",
            "tag_assistita": "TEXT",
            "tag_possibile_assistita": "TEXT",
            "tag_assistita_streaming": "TEXT",
            "tag_saluto": "TEXT",
            "tag_calco": "TEXT",
            "tag_avvisare": "TEXT",
            "tag_da_richiamare": "TEXT",
            "payment_status": "TEXT DEFAULT 'Da saldare'",
            "invoice_number": "TEXT",
            "ddt_share_token": "TEXT",
            "signature_data": "TEXT",
            "owner_phone_2": "TEXT",
            "client_id": "INTEGER",
            "owner_veterinarian_id": "INTEGER",
            "owner_company": "TEXT",
            "owner_vat": "TEXT",
            "owner_notes": "TEXT",
            "owner_street": "TEXT",
            "owner_city": "TEXT",
            "owner_province": "TEXT",
            "owner_zip": "TEXT",
            "collaborator_name": "TEXT",
            "animal2_name": "TEXT",
            "animal2_species": "TEXT",
            "animal2_breed": "TEXT",
            "animal2_weight": "TEXT",
            "animal2_microchip": "TEXT",
            "veterinarian_id": "INTEGER",
            "voucher_requested": "TEXT",
            "use_voucher": "TEXT",
            "used_voucher_id": "INTEGER",
            "whatsapp_thanks_sent_at": "TEXT",
            "whatsapp_thanks_last_error": "TEXT",
            "no_whatsapp_message": "TEXT",
            "deleted_at": "TEXT",
            "deleted_by": "INTEGER"
        }
        existing = {row["name"] for row in c.execute("PRAGMA table_info(practices)")}
        for name, definition in extra_columns.items():
            if name not in existing:
                c.execute(f"ALTER TABLE practices ADD COLUMN {name} {definition}")
        vet_existing = {row["name"] for row in c.execute("PRAGMA table_info(veterinarians)")}
        for name, definition in {"short_name":"TEXT", "address":"TEXT", "city":"TEXT"}.items():
            if name not in vet_existing:
                c.execute(f"ALTER TABLE veterinarians ADD COLUMN {name} {definition}")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_practices_ddt_share_token ON practices(ddt_share_token)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_due ON whatsapp_messages(status, scheduled_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_practice ON whatsapp_messages(practice_id, created_at)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_messages_one_active ON whatsapp_messages(practice_id) WHERE status IN ('programmato','in_invio')")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(last_name, first_name)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_phone ON clients(phone)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_email ON clients(email)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_tax ON clients(tax_code)")
        c.execute("UPDATE practices SET payment_status='Da saldare' WHERE payment_status IS NULL OR payment_status=''")
        c.execute("UPDATE practices SET payment_status='Pagato', status='Consegnato' WHERE status='Pagato'")
        c.execute("UPDATE veterinarian_vouchers SET status='Maturato' WHERE status='Disponibile'")
        stamp = now()
        for clinic, city in DEFAULT_VETERINARIANS:
            exists = c.execute(
                "SELECT 1 FROM veterinarians WHERE (UPPER(clinic_name)=UPPER(?) OR UPPER(short_name)=UPPER(?)) AND active=1",
                (clinic, clinic),
            ).fetchone()
            if not exists:
                for item in VETERINARIAN_ANAGRAFICHE:
                    aliases={normalize_name(item["code"]), normalize_name(item["name"])}
                    aliases.update(normalize_name(part) for part in re.split(r"[/|]+", item["code"]) if normalize_name(part))
                    if normalize_name(clinic) in aliases:
                        exists = c.execute("SELECT 1 FROM veterinarians WHERE (UPPER(clinic_name)=UPPER(?) OR UPPER(short_name)=UPPER(?)) AND active=1",(item["name"], item["code"])).fetchone()
                        if exists:
                            break
            if not exists:
                c.execute(
                    "INSERT INTO veterinarians(clinic_name,doctor_name,phone,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (clinic, "", "", f"Comune: {city}", stamp, stamp),
                )
        for item in VETERINARIAN_ANAGRAFICHE:
            existing_vet = c.execute("SELECT id FROM veterinarians WHERE UPPER(clinic_name)=UPPER(?) OR UPPER(short_name)=UPPER(?)", (item["name"], item["code"])).fetchone()
            if existing_vet:
                c.execute("UPDATE veterinarians SET short_name=?, clinic_name=?, address=?, city=?, phone=?, updated_at=? WHERE id=?", (item["code"], item["name"], item["address"], item["city"], item["phone"], stamp, existing_vet["id"]))
            else:
                c.execute("INSERT INTO veterinarians(clinic_name,doctor_name,phone,notes,active,created_at,updated_at,short_name,address,city) VALUES(?,?,?,?,?,?,?,?,?,?)", (item["name"], "", item["phone"], "", 1, stamp, stamp, item["code"], item["address"], item["city"]))
        vets_for_cleanup=c.execute("SELECT * FROM veterinarians WHERE active=1").fetchall()
        groups={}
        for vet in vets_for_cleanup:
            keys={normalize_name(vet["clinic_name"]), normalize_name(vet["short_name"])}
            for item in VETERINARIAN_ANAGRAFICHE:
                code=normalize_name(item["code"]); full=normalize_name(item["name"])
                parts={normalize_name(part) for part in re.split(r"[/|]+", item["code"]) if normalize_name(part)}
                if code in keys or full in keys or any(part in keys for part in parts):
                    keys.add(code); keys.add(full)
                    keys.update(parts)
            canonical=sorted(k for k in keys if k)
            if not canonical:
                continue
            key=canonical[0]
            for candidate in canonical:
                if candidate in groups:
                    key=candidate
                    break
            groups.setdefault(key, [])
            if not any(v["id"] == vet["id"] for v in groups[key]):
                groups[key].append(vet)
            for candidate in canonical:
                groups[candidate]=groups[key]
        cleaned_ids=set()
        for group in list({id(vs):vs for vs in groups.values()}.values()):
            unique=[]
            seen=set()
            for vet in group:
                if vet["id"] not in seen:
                    unique.append(vet); seen.add(vet["id"])
            if len(unique) < 2:
                continue
            def vet_score(v):
                return (3 if v["address"] else 0) + (3 if v["phone"] else 0) + (2 if v["clinic_name"] and normalize_name(v["clinic_name"]) != normalize_name(v["short_name"]) else 0) + (1 if v["short_name"] else 0) + (1 if v["city"] else 0)
            keeper=max(unique, key=vet_score)
            for duplicate in unique:
                if duplicate["id"] == keeper["id"] or duplicate["id"] in cleaned_ids:
                    continue
                c.execute("UPDATE veterinarian_vouchers SET veterinarian_id=? WHERE veterinarian_id=?",(keeper["id"],duplicate["id"]))
                c.execute("UPDATE practices SET veterinarian_id=? WHERE veterinarian_id=?",(keeper["id"],duplicate["id"]))
                if not keeper["short_name"] and duplicate["short_name"]:
                    c.execute("UPDATE veterinarians SET short_name=? WHERE id=?",(duplicate["short_name"],keeper["id"]))
                c.execute("DELETE FROM veterinarians WHERE id=?",(duplicate["id"],))
                cleaned_ids.add(duplicate["id"])
                print(f"[VETERINARI] duplicato eliminato id={duplicate['id']} -> mantenuto id={keeper['id']}", flush=True)
        practice_clients = c.execute("""SELECT id, owner_first_name, owner_last_name, owner_company, owner_phone, owner_phone_2, owner_email, owner_tax_code, owner_vat, owner_street, owner_city, owner_province, owner_zip, owner_address, owner_notes
                                        FROM practices
                                        WHERE client_id IS NULL
                                          AND COALESCE(owner_first_name,'')||COALESCE(owner_last_name,'')||COALESCE(owner_phone,'')||COALESCE(owner_email,'')||COALESCE(owner_tax_code,'')<>''""").fetchall()
        for pcli in practice_clients:
            phone_digits=only_digits(pcli["owner_phone"])
            dup=None
            if pcli["owner_tax_code"]:
                dup=c.execute("SELECT id FROM clients WHERE UPPER(tax_code)=UPPER(?) LIMIT 1",(pcli["owner_tax_code"],)).fetchone()
            if not dup and pcli["owner_vat"]:
                dup=c.execute("SELECT id FROM clients WHERE UPPER(vat_number)=UPPER(?) LIMIT 1",(pcli["owner_vat"],)).fetchone()
            if not dup and pcli["owner_email"]:
                dup=c.execute("SELECT id FROM clients WHERE UPPER(email)=UPPER(?) LIMIT 1",(pcli["owner_email"],)).fetchone()
            if not dup and phone_digits:
                dup=c.execute("SELECT id FROM clients WHERE REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'+',''),'-',''),'.','') LIKE ? LIMIT 1",(f"%{phone_digits[-8:]}",)).fetchone()
            if not dup and (pcli["owner_first_name"] or pcli["owner_last_name"]):
                dup=c.execute("SELECT id FROM clients WHERE UPPER(first_name)=UPPER(?) AND UPPER(last_name)=UPPER(?) LIMIT 1",(pcli["owner_first_name"] or "", pcli["owner_last_name"] or "")).fetchone()
            if dup:
                c.execute("UPDATE practices SET client_id=? WHERE id=?",(dup["id"],pcli["id"]))
            else:
                cur=c.execute("""INSERT INTO clients(first_name,last_name,company_name,phone,phone_2,email,tax_code,vat_number,street,city,province,zip,address,notes,created_at,updated_at)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (pcli["owner_first_name"],pcli["owner_last_name"],pcli["owner_company"],pcli["owner_phone"],pcli["owner_phone_2"],pcli["owner_email"],pcli["owner_tax_code"],pcli["owner_vat"],pcli["owner_street"],pcli["owner_city"],pcli["owner_province"],pcli["owner_zip"],pcli["owner_address"],pcli["owner_notes"],stamp,stamp))
                c.execute("UPDATE practices SET client_id=? WHERE id=?",(cur.lastrowid,pcli["id"]))
        if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            c.execute(
                "INSERT INTO users(username,password_hash,display_name,role) VALUES(?,?,?,?)",
                ("admin", password_hash("petparadise"), "Amministratore", "admin"),
            )


def esc(value):
    return html.escape(str(value or ""), quote=True)


def now():
    return datetime.now().isoformat(timespec="seconds")


def compact_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def only_digits(value):
    return re.sub(r"\D+", "", str(value or ""))


def date_it(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return value


def normalize_name(value):
    text=compact_text(value).upper()
    text=re.sub(r"[^A-Z0-9]+"," ",text)
    return compact_text(text)


def safe_pdf_filename(name, fallback="pratica"):
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or fallback).strip()).strip("_")
    return f"{base or fallback}.pdf"


def persistence_warning():
    if not os.environ.get("PPM_DATA_DIR"):
        return "ATTENZIONE: archivio non persistente. Su Render imposta PPM_DATA_DIR=/var/data e collega un Persistent Disk, altrimenti le pratiche possono sparire al riavvio."
    if not DATA.exists() or not os.access(DATA, os.W_OK):
        return f"ATTENZIONE: la cartella dati {DATA} non è scrivibile. Le pratiche potrebbero non rimanere salvate."
    return ""


def next_number(conn, key, prefix=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    value = int(row["value"])
    conn.execute("UPDATE settings SET value=? WHERE key=?", (str(value + 1), key))
    return f"{prefix}{value:06d}" if prefix else value


def practice_code_prefix(service_type):
    if service_type == "Cremazione singola":
        return "CR-", "next_cr_number"
    if service_type == "Cremazione collettiva":
        return "SM-", "next_sm_number"
    return "PP-", "next_practice_number"


def next_practice_code(conn, service_type):
    prefix, key = practice_code_prefix(service_type)
    return next_number(conn, key, prefix)


CSS = r"""
:root{--ink:#24312c;--muted:#6e7b75;--brand:#a74045;--brand2:#7f3035;--paper:#fff;--bg:#f4f1ed;--line:#ded8d1;--green:#39745b;--gold:#a87926;--safe-top:env(safe-area-inset-top,0px);--safe-bottom:env(safe-area-inset-bottom,0px);--safe-left:env(safe-area-inset-left,0px);--safe-right:env(safe-area-inset-right,0px)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:inherit;text-decoration:none}.top{height:68px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:18px;padding:0 28px;position:sticky;top:0;z-index:5}.brand{font-weight:800;font-size:19px;color:var(--brand)}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:1.5px}.nav{display:flex;gap:8px;margin-left:auto}.nav a{padding:9px 12px;border-radius:9px}.nav a:hover{background:#f3eeea}.wrap{max-width:1280px;margin:0 auto;padding:28px}.titlebar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px}h1{margin:0;font-size:28px}h2{font-size:18px;margin:0 0 15px}.sub{color:var(--muted)}.btn{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:10px;background:var(--brand);color:white;padding:11px 16px;font-weight:700;cursor:pointer}.btn:hover{background:var(--brand2)}.btn.ghost{background:white;color:var(--ink);border:1px solid var(--line)}.grid{display:grid;gap:16px}.stats{grid-template-columns:repeat(3,1fr)}.card{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:20px;box-shadow:0 3px 15px #4b39260a}.stat{display:flex;justify-content:space-between;align-items:center}.stat b{font-size:32px;color:var(--brand)}.badge{display:inline-flex;padding:5px 9px;border-radius:99px;background:#eee9e3;font-size:12px;font-weight:700}.tag-red{background:#e53935;color:white}.tag-orange{background:#fb8c00;color:white}.tag-outline-orange{background:white;color:#fb8c00;border:2px solid #fb8c00}.tag-purple{background:#7e57c2;color:white}.tag-yellow,.pay-yellow{background:#fdd835;color:#3b3100}.tag-pink{background:#f06292;color:white}.tag-blue,.pay-blue{background:#1e88e5;color:white}.tag-green,.pay-green{background:#43a047;color:white}.status-stack{display:flex;gap:5px;flex-wrap:wrap}.form-grid{grid-template-columns:repeat(2,1fr)}.wide{grid-column:1/-1}.section{background:#fff;border:1px solid var(--line);border-radius:15px;padding:20px}.fields{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}.field{display:flex;flex-direction:column;gap:6px}.field.full{grid-column:1/-1}label{font-weight:650;font-size:13px}input,select,textarea{width:100%;border:1px solid #cfc8c0;border-radius:9px;padding:11px 12px;background:white;color:var(--ink);font:inherit}input[type=checkbox]{width:auto;min-height:auto}textarea{min-height:90px;resize:vertical}input:focus,select:focus,textarea:focus{outline:3px solid #a7404520;border-color:var(--brand)}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:13px;border-bottom:1px solid var(--line)}th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}.tablebox{overflow:auto;-webkit-overflow-scrolling:touch;background:white;border:1px solid var(--line);border-radius:15px}.actions{display:flex;gap:10px;flex-wrap:wrap}.flash{padding:13px 16px;border-radius:10px;background:#e5f2eb;color:#285b45;margin-bottom:16px}.warning{background:#fff1d8;color:#765315}.login{max-width:410px;margin:10vh auto;background:white;padding:34px;border-radius:18px;border:1px solid var(--line)}.timeline{border-left:2px solid var(--line);margin-left:7px;padding-left:20px}.event{padding:0 0 18px;position:relative}.event:before{content:'';position:absolute;width:10px;height:10px;border-radius:50%;background:var(--brand);left:-26px;top:5px}.kvs{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.kv{background:#faf8f5;border-radius:10px;padding:12px}.kv small{display:block;color:var(--muted)}.signature-pad{width:100%;height:260px;border:2px dashed var(--line);border-radius:14px;background:white;touch-action:none}
body{background:radial-gradient(circle at top left,#fff8f3 0,#f4f1ed 34%,#ece5dd 100%)}.top{backdrop-filter:saturate(1.2) blur(10px);box-shadow:0 8px 28px #4b392612}.brand{letter-spacing:.2px}.nav a{font-weight:650}.nav a.btn{box-shadow:0 8px 20px #a7404524}.wrap{animation:ppmFade .18s ease-out}.titlebar h1{letter-spacing:-.03em}.section,.card,.tablebox,.login{box-shadow:0 10px 30px #4b39260d}.section{transition:box-shadow .15s ease, transform .15s ease}.card{transition:transform .15s ease,box-shadow .15s ease}.card:hover{transform:translateY(-2px);box-shadow:0 14px 34px #4b392617}.btn{box-shadow:0 6px 16px #a740451f}.btn.ghost{box-shadow:none}.kv{border:1px solid #eee6df}.tablebox table tr:hover td{background:#fffaf6}input,select,textarea{transition:border-color .15s ease,box-shadow .15s ease}.danger{border-width:1px}.trash-note{background:#fff7e8;border:1px solid #f0cf9d;color:#765315;border-radius:12px;padding:12px 14px;margin-bottom:16px}.empty-state{text-align:center;padding:32px;color:var(--muted)}@keyframes ppmFade{from{opacity:.78;transform:translateY(3px)}to{opacity:1;transform:none}}
.practice-layout{grid-template-columns:2fr 1fr}@media(max-width:800px){html,body{width:100%;max-width:100%;overflow-x:hidden}body{font-size:16px}.wrap{padding:14px}.top{height:auto;min-height:64px;padding:10px 12px;align-items:flex-start}.brand{font-size:17px}.nav{gap:4px;flex-wrap:wrap}.nav a{padding:8px 9px}.nav a span{display:none}.btn{width:100%;min-height:46px}.actions{width:100%}.actions .btn,.actions form{flex:1 1 100%}.stats,.form-grid,.fields,.kvs,.practice-layout{grid-template-columns:1fr}.section{padding:16px;border-radius:13px}.titlebar{align-items:flex-start;flex-direction:column}.wide{grid-column:auto}input,select,textarea{font-size:16px;min-height:46px}th:nth-child(4),td:nth-child(4){display:none}.badge{margin:2px 2px 2px 0}}
.danger{border-color:#e2a5a5;background:#fff7f7}.btn.danger-btn{background:#b42323;color:white}.btn.danger-btn:hover{background:#8f1d1d}.danger-note{color:#8f1d1d;font-weight:700}
.home-logo{width:118px;height:118px;object-fit:contain;border-radius:24px;background:white;padding:10px;border:1px solid var(--line);box-shadow:0 8px 24px #4b392614}
.month-block{margin-bottom:18px}.month-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.hidden{display:none!important}
.practice-code-cr{color:#1e88e5}.practice-code-sm{color:#111}
.lookup{position:relative}.lookup-results{position:absolute;left:0;right:0;top:100%;z-index:20;background:white;border:1px solid var(--line);border-radius:12px;margin-top:6px;box-shadow:0 10px 30px #4b392626;max-height:340px;overflow:auto}.lookup-item{display:block;width:100%;border:0;background:white;text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);cursor:pointer;color:var(--ink)}.lookup-item:hover,.lookup-item:focus{background:#f7f2ee;outline:none}.lookup-item b{display:block}.lookup-item small{display:block;color:var(--muted);white-space:normal}.lookup-state{padding:10px 12px;color:var(--muted);font-size:13px}.selected-box{border:1px solid #b8d7c8;background:#edf7f2;color:#285b45;border-radius:10px;padding:12px;margin-top:10px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}.selected-box .btn{width:auto}
/* Dark professional interface */
:root{--ink:#f5f7fb;--muted:#9ca7b8;--brand:#e9475b;--brand2:#ff6377;--paper:#111722;--bg:#090d14;--line:#293140;--green:#35c98a;--gold:#f5b83d}
html{color-scheme:dark}body{background:radial-gradient(circle at 78% -10%,#31121e 0,transparent 32%),linear-gradient(135deg,#090d14,#0d121b 55%,#090d14);min-height:100dvh;color:var(--ink);font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.top{position:fixed;left:0;top:0;bottom:0;width:226px;height:100dvh;display:flex;flex-direction:column;align-items:stretch;padding:24px 16px;background:#0c111acc;border:0;border-right:1px solid var(--line);backdrop-filter:blur(18px);box-shadow:16px 0 50px #0003}
.brand{display:flex;align-items:center;gap:11px;padding:4px 6px 24px;color:#ff6678;font-size:18px}.brand-logo{width:48px;height:48px;object-fit:contain}.brand-copy small{margin-top:2px;color:#c1c8d3}
.nav{margin:8px 0 0;display:flex;flex:1;flex-direction:column;width:100%;gap:7px}.nav a{display:flex;align-items:center;gap:11px;padding:11px 13px;color:#c5ccd7;border:1px solid transparent;border-radius:12px;font-weight:650}.nav a:hover{color:white;background:#181f2b;border-color:#2d3645}.nav a:first-child{color:#ff697b;background:linear-gradient(90deg,#381922,#1c151d);border-color:#53212e}.nav-icon{width:20px;text-align:center;font-size:17px}.nav .btn{margin-top:10px;color:white;background:linear-gradient(135deg,#ff526a,#cc2946);box-shadow:0 10px 26px #e9475b35}.nav .logout{margin-top:auto}.wrap{max-width:1500px;margin-left:226px;padding:34px 38px;animation:ppmFade .22s ease-out}
h1{font-size:30px;letter-spacing:-.035em}h2{color:#eef1f6}.sub{color:var(--muted)}
.section,.card,.tablebox,.login{background:linear-gradient(145deg,#131a26,#0f151f);border:1px solid var(--line);box-shadow:0 18px 50px #0003}.card{position:relative;overflow:hidden}.card:after{content:"";position:absolute;inset:auto -35px -50px auto;width:110px;height:110px;border-radius:50%;background:#e9475b12;filter:blur(4px)}.card:hover{border-color:#4a3340;box-shadow:0 20px 48px #0006,0 0 0 1px #e9475b12}.stat b{color:#ff6175}.btn{background:linear-gradient(135deg,#f05267,#c92d49);box-shadow:0 8px 24px #e9475b30}.btn:hover{background:linear-gradient(135deg,#ff6679,#df3652)}.btn.ghost{background:#171e2a;color:#e9edf3;border-color:#303948}.btn.ghost:hover{background:#202938}
input,select,textarea{background:#0c121b;border-color:#323c4b;color:#f3f5f8}input:focus,select:focus,textarea:focus{outline:3px solid #e9475b22;border-color:#e9475b}.kv{background:#0c121b;border-color:#252e3b}.tablebox,table{background:#101620}th,td{border-color:#252d39}th{color:#8f9bad}.tablebox table tr:hover td{background:#171f2b}.lookup-results,.lookup-item{background:#131a25;border-color:#2b3544;color:#f5f7fb}.lookup-item:hover,.lookup-item:focus{background:#202938}.selected-box{background:#10261f;border-color:#245a46;color:#7ce0b7}
.badge{background:#252d39;color:#dfe4eb}.tag-outline-orange{background:#271c10}.pay-yellow,.tag-yellow{background:#5a4610;color:#ffe28a}.login{margin-left:auto;margin-right:auto}.home-logo{background:#070a0f;border-color:#303948;box-shadow:0 12px 34px #0006;padding:7px}.practice-code-sm{color:#f3f5f8}.practice-code-cr{color:#6fa8ff}.danger{background:#291318;border-color:#6b2734}.trash-note,.warning{background:#302412;border-color:#624c23;color:#f6d58e}.flash{background:#102a20;color:#8be3bb}.signature-pad{background:#fff}
.install-btn{display:none}.install-btn.ready{display:flex}.install-hint{position:fixed;right:22px;bottom:22px;z-index:50;max-width:340px;padding:16px;background:#141b27;border:1px solid #353f4f;border-radius:16px;box-shadow:0 20px 60px #0008}.install-hint b{display:block;color:#ff6679;margin-bottom:5px}.install-hint button{margin-top:10px}
@media(max-width:900px){.top{position:sticky;width:100%;height:auto;min-height:66px;bottom:auto;flex-direction:row;align-items:center;padding:8px 12px}.brand{padding:0}.brand-logo{width:42px;height:42px}.brand-copy{display:none}.nav{margin:0 0 0 auto;flex-direction:row;align-items:center;width:auto;overflow-x:auto}.nav a{padding:9px}.nav a span:not(.nav-icon){display:none}.nav-icon{display:inline-block!important}.nav .btn{margin:0}.nav .btn span:not(.nav-icon){display:none}.nav .logout{margin:0}.wrap{margin-left:0;padding:18px 14px}.stats{grid-template-columns:1fr 1fr}.home-logo{width:82px;height:82px}.tablebox th,.tablebox td{display:table-cell!important;white-space:nowrap}.install-hint{left:14px;right:14px;bottom:14px}.titlebar{gap:12px}}
@media(max-width:560px){.stats{grid-template-columns:1fr}.brand-logo{width:38px;height:38px}.nav a{font-size:0}.nav-icon{font-size:18px}.nav .btn{width:auto;min-height:42px}.wrap{padding-top:14px}h1{font-size:25px}}
/* Premium dashboard layout */
body{background:#111827;color:#f8fafc}.icon{width:20px;height:20px;flex:0 0 20px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.skip-link{position:fixed;top:8px;left:8px;z-index:200;transform:translateY(-150%);padding:10px 14px;border-radius:9px;background:#fff;color:#111827}.skip-link:focus{transform:none}
.top{width:238px;padding:20px 14px;background:#0b1220;border-color:#263246}.brand{padding:0 8px 20px}.brand-logo{width:50px;height:50px}.brand-copy{font-size:17px}.nav{gap:3px;overflow-y:auto;padding-right:3px}.nav a,.nav button{min-height:42px;padding:9px 11px;border-radius:10px}.nav a:first-child{background:linear-gradient(90deg,#4a1826,#241523);border-color:#642239}.nav .install-btn{margin-top:8px}.nav .logout{margin-top:12px}
.app-header{position:fixed;left:238px;right:0;top:0;height:76px;z-index:40;display:flex;align-items:center;justify-content:space-between;gap:20px;padding:14px 30px;background:#111827e8;border-bottom:1px solid #263246;backdrop-filter:blur(16px)}.header-search{width:min(420px,40vw);display:flex;align-items:center;gap:9px;padding:0 13px;border:1px solid #334155;border-radius:11px;background:#172033}.header-search input{min-height:42px;padding:8px 0;background:transparent;border:0}.header-search input:focus{outline:0}.header-actions{display:flex;align-items:center;gap:9px}.icon-btn{display:inline-grid;place-items:center;width:42px;height:42px;padding:0;border:1px solid #334155;border-radius:11px;background:#172033;color:#cbd5e1;cursor:pointer}.icon-btn:hover{color:#fff;border-color:#ef405f}.header-new{gap:7px}.header-actions time{min-width:104px;padding:6px 10px;border:1px solid #334155;border-radius:10px;text-align:center;font-weight:700;background:#172033}.header-actions time small{display:block;color:#94a3b8;font-size:10px;text-transform:capitalize}.wrap{max-width:none;margin-left:238px;padding:106px 30px 42px}
.dashboard-wrap{max-width:1500px}.welcome{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.welcome h1{font-size:30px}.welcome p{margin:7px 0 0;color:#94a3b8}.dashboard-heading{margin:24px 0 12px;font-size:15px;color:#dce4ef}.dashboard-states{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.metric-card,.payment-card{position:relative;min-height:126px;display:flex;align-items:center;justify-content:space-between;gap:15px;padding:20px;border:1px solid #334155;border-radius:14px;background:#1f2937;overflow:hidden;box-shadow:0 14px 36px #03071235;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.metric-card:before,.payment-card:before{content:"";position:absolute;inset:0;background:linear-gradient(120deg,var(--card-glow),transparent 62%);pointer-events:none}.metric-card:hover,.payment-card:hover{transform:translateY(-3px);border-color:#56657a;box-shadow:0 20px 44px #03071260}.metric-copy,.payment-card>span:first-child{position:relative;display:flex;flex-direction:column}.metric-card small,.payment-card small{font-style:normal;color:#e2e8f0}.metric-card strong,.payment-card strong{margin-top:4px;font-size:30px;line-height:1.05}.metric-card em,.payment-card em{margin-top:9px;color:#94a3b8;font-size:12px;font-style:normal}.metric-icon,.activity-icon{position:relative;display:grid;place-items:center;width:46px;height:46px;border-radius:12px;background:var(--icon-bg);color:var(--icon-color);box-shadow:0 8px 22px var(--icon-shadow)}.state-red{--card-glow:#83184375;--icon-bg:#881337;--icon-color:#fb7185;--icon-shadow:#e11d4840}.state-blue{--card-glow:#17255480;--icon-bg:#172554;--icon-color:#60a5fa;--icon-shadow:#2563eb40}.state-purple{--card-glow:#3b076480;--icon-bg:#3b0764;--icon-color:#c084fc;--icon-shadow:#9333ea40}.state-green{--card-glow:#052e2b85;--icon-bg:#064e3b;--icon-color:#4ade80;--icon-shadow:#16a34a40}
.dashboard-payments{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.payment-card{min-height:116px}.payment-due{--card-glow:#713f123d;--icon-bg:#573713;--icon-color:#fbbf24;--icon-shadow:#f59e0b35}.payment-deposit{--card-glow:#17255465;--icon-bg:#172554;--icon-color:#60a5fa;--icon-shadow:#2563eb35}.payment-paid{--card-glow:#052e2b75;--icon-bg:#064e3b;--icon-color:#4ade80;--icon-shadow:#16a34a35}
.dashboard-lower{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(350px,.8fr);gap:16px;margin-top:24px}.dashboard-panel{min-height:350px;padding:20px;border:1px solid #334155;border-radius:15px;background:#1f2937;box-shadow:0 18px 48px #03071235}.dashboard-panel>header{display:flex;align-items:flex-start;justify-content:space-between;gap:15px}.dashboard-panel h2{margin:0;font-size:16px}.dashboard-panel header p{margin:8px 0 0;color:#94a3b8}.dashboard-panel header p strong{color:#fff;font-size:21px}.dashboard-panel header a{color:#fb7185;font-size:13px}.income-chart{display:block;width:100%;height:auto;margin-top:14px}.chart-grid line{stroke:#334155;stroke-width:1}.chart-grid text,.chart-dates text{fill:#94a3b8;font-size:11px}.chart-area{fill:url(#incomeArea)}.chart-line{fill:none;stroke:#ef405f;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;filter:drop-shadow(0 5px 8px #ef405f55)}.income-chart circle{fill:#fb7185;stroke:#1f2937;stroke-width:2}
.activity-list{display:flex;flex-direction:column;margin-top:14px}.activity-item{display:grid;grid-template-columns:42px minmax(0,1fr) auto;align-items:center;gap:11px;padding:12px 0;border-bottom:1px solid #334155}.activity-item:last-child{border-bottom:0}.activity-item b,.activity-item small{display:block}.activity-item b{font-size:13px}.activity-item small{margin-top:3px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.activity-item time{color:#94a3b8;font-size:11px}.activity-icon{width:38px;height:38px;--icon-bg:#243244;--icon-color:#5eead4;--icon-shadow:transparent}.activity-1 .activity-icon{--icon-bg:#422006;--icon-color:#fbbf24}.activity-2 .activity-icon{--icon-bg:#3b0764;--icon-color:#c084fc}.activity-3 .activity-icon{--icon-bg:#4c0519;--icon-color:#fb7185}.activity-empty{padding:40px 10px;color:#94a3b8;text-align:center}
.bottom-nav,.more-menu,.more-backdrop{display:none}.tag-red{background:#7f1d2d;color:#fecdd3}.tag-orange{background:#7c2d12;color:#fed7aa}.tag-outline-orange{background:#3b1d0c;color:#fdba74;border-color:#f97316}.tag-purple{background:#4c1d95;color:#e9d5ff}.tag-yellow{background:#713f12;color:#fef08a}.tag-pink{background:#831843;color:#fbcfe8}.tag-blue{background:#1e3a8a;color:#bfdbfe}.tag-green{background:#14532d;color:#bbf7d0}
.search-after-results{margin-top:32px}.search-after-results>h2{margin-bottom:12px}.search-after-results .section{box-shadow:0 12px 34px #0307122e}
.dashboard-recent{margin-top:26px}.dashboard-recent .titlebar{margin-bottom:12px}.dashboard-recent .titlebar a{color:#fb7185}.load-previous-month{display:flex;justify-content:center;padding:8px 0 24px}.load-previous-month .btn{width:auto;min-width:240px}.budget-add{align-self:end;width:auto!important;min-height:42px;margin-top:auto}
*:focus-visible{outline:3px solid #fb7185!important;outline-offset:3px}.light-theme{background:#eef2f7;color:#111827}.light-theme .app-header,.light-theme .top{background:#fff;color:#111827}.light-theme .dashboard-panel,.light-theme .metric-card,.light-theme .payment-card,.light-theme .section,.light-theme .tablebox{background:#fff;color:#111827}.light-theme .header-search,.light-theme .icon-btn,.light-theme .header-actions time{background:#f8fafc;color:#111827}.light-theme .welcome p,.light-theme .metric-card em,.light-theme .payment-card em,.light-theme .activity-item small,.light-theme .activity-item time{color:#64748b}
@media(max-width:1100px){.dashboard-states{grid-template-columns:repeat(2,1fr)}.dashboard-lower{grid-template-columns:1fr}.header-actions time{display:none}}
@media(max-width:900px){body{min-height:100dvh;padding-bottom:calc(82px + var(--safe-bottom))}#main-content{min-height:100dvh;padding-left:var(--safe-left);padding-right:var(--safe-right)}.top{position:fixed;left:var(--safe-left);right:var(--safe-right);top:0;width:auto;height:calc(64px + var(--safe-top));min-height:calc(64px + var(--safe-top));padding:calc(7px + var(--safe-top)) 14px 7px;border-right:0;border-bottom:1px solid #263246}.top .nav{display:none}.brand-copy{display:inline}.brand-logo{width:42px;height:42px}.app-header{position:fixed;left:auto;right:calc(10px + var(--safe-right));top:calc(7px + var(--safe-top));width:auto;height:50px;padding:0;background:transparent;border:0;backdrop-filter:none}.header-search,.header-actions time,.header-new span{display:none}.header-actions{gap:7px}.header-new{width:42px;height:42px;padding:0}.wrap{margin-left:0;padding:calc(88px + var(--safe-top)) 14px 22px}.bottom-nav{position:fixed;display:grid;grid-template-columns:repeat(5,1fr);align-items:end;left:0;right:0;bottom:0;z-index:90;height:calc(72px + var(--safe-bottom));padding:6px max(8px,var(--safe-right)) calc(5px + var(--safe-bottom)) max(8px,var(--safe-left));background:#0b1220ed;border-top:1px solid #334155;backdrop-filter:blur(18px)}.bottom-nav a,.bottom-nav button{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;border:0;background:transparent;color:#94a3b8;font-size:10px}.bottom-nav .icon{width:21px;height:21px}.bottom-nav a:first-child{color:#fb7185}.bottom-nav .bottom-new{align-self:center;width:52px;height:52px;margin:-18px auto 0;border-radius:50%;background:linear-gradient(135deg,#fb4c67,#d9284c);color:#fff;box-shadow:0 8px 28px #ef405f70}.bottom-new span{display:none}.more-backdrop{position:fixed;display:block;inset:0;z-index:94;background:#020617aa;opacity:0;pointer-events:none;transition:opacity .2s}.more-menu{position:fixed;display:flex;flex-direction:column;gap:5px;left:calc(14px + var(--safe-left));right:calc(14px + var(--safe-right));bottom:calc(82px + var(--safe-bottom));z-index:95;max-height:72dvh;padding:16px;border:1px solid #334155;border-radius:18px;background:#111827;box-shadow:0 25px 80px #0009;overflow:auto;transform:translateY(120%);opacity:0;transition:transform .22s ease,opacity .22s}.more-menu a{display:flex;align-items:center;gap:11px;padding:11px;border-radius:10px;color:#e2e8f0}.more-menu a:hover{background:#1f2937}.more-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px}.more-title .icon-btn{font-size:24px}.more-open .more-menu{transform:none;opacity:1}.more-open .more-backdrop{opacity:1;pointer-events:auto}.install-hint{left:calc(14px + var(--safe-left));right:calc(14px + var(--safe-right));bottom:calc(14px + var(--safe-bottom))}.skip-link{top:calc(8px + var(--safe-top));left:calc(8px + var(--safe-left))}.light-theme .bottom-nav,.light-theme .more-menu{background:#fff}.dashboard-lower{grid-template-columns:1fr}}
@media(max-width:620px){.brand-copy{display:none}.dashboard-states,.dashboard-payments{grid-template-columns:1fr}.metric-card,.payment-card{min-height:104px}.dashboard-panel{padding:15px;min-height:0}.welcome h1{font-size:24px}.dashboard-lower{margin-top:18px}.income-chart{min-width:0}.activity-item{grid-template-columns:38px minmax(0,1fr)}.activity-item time{display:none}}
.income-panel{display:block;color:inherit;transition:transform .18s ease,border-color .18s ease}.income-panel:hover{transform:translateY(-2px);border-color:#fb7185}.panel-link{color:#fb7185;font-size:12px;font-weight:700}.balance-total{min-width:210px;padding:14px 18px;border:1px solid #334155;border-radius:14px;background:#1f2937;text-align:right}.balance-total small,.balance-total strong{display:block}.balance-total small{color:#94a3b8}.balance-total strong{margin-top:3px;color:#fb7185;font-size:25px}.balance-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:20px}.balance-card{display:flex;flex-direction:column;gap:7px;padding:17px;border:1px solid #334155;border-radius:13px;background:#1f2937;transition:transform .15s,border-color .15s}.balance-card:hover,.balance-card.active{transform:translateY(-2px);border-color:#fb7185}.balance-card small{color:#94a3b8}.balance-card strong{font-size:20px}.balance-table{margin-top:4px}.light-theme .balance-card,.light-theme .balance-total{background:#fff;color:#111827}
@media(max-width:620px){.balance-grid{grid-template-columns:1fr 1fr}.balance-total{width:100%;text-align:left}.balances-wrap .titlebar{align-items:stretch}.panel-link{display:none}}
.conversation-list{display:flex;flex-direction:column;gap:12px}.conversation-card{display:grid;grid-template-columns:minmax(280px,1.2fr) minmax(420px,1fr) auto;align-items:center;gap:20px;padding:18px;border:1px solid #334155;border-radius:15px;background:#1f2937;box-shadow:0 12px 34px #0307122e}.conversation-main{display:grid;grid-template-columns:46px minmax(0,1fr);align-items:center;gap:13px}.conversation-avatar{display:grid;place-items:center;width:46px;height:46px;border-radius:13px;background:#064e3b;color:#4ade80}.conversation-main h2{margin:0 0 5px;font-size:16px}.conversation-main p{margin:3px 0;color:#94a3b8;font-size:13px}.conversation-main p b,.conversation-main a{color:#e2e8f0}.conversation-message{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.conversation-card dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin:0}.conversation-card dl div{min-width:0}.conversation-card dt{color:#94a3b8;font-size:10px;text-transform:uppercase;letter-spacing:.05em}.conversation-card dd{margin:4px 0 0;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.conversation-action{text-align:right}.whatsapp-open{background:linear-gradient(135deg,#22c55e,#15803d);white-space:nowrap}.message-accettato_da_meta{background:#1e3a8a;color:#bfdbfe}.message-consegnato{background:#14532d;color:#bbf7d0}.message-letto{background:#164e63;color:#a5f3fc}.message-fallito{background:#7f1d1d;color:#fecaca}.pagination{display:flex;align-items:center;justify-content:center;gap:18px;margin:20px 0;color:#94a3b8}.pagination a,.page-disabled{padding:9px 13px;border:1px solid #334155;border-radius:10px}.pagination a{color:#f8fafc;background:#1f2937}.page-disabled{opacity:.45}.light-theme .conversation-card{background:#fff;color:#111827}.light-theme .conversation-main p{color:#64748b}
@media(max-width:1150px){.conversation-card{grid-template-columns:1fr 1fr}.conversation-action{grid-column:1/-1;text-align:left}}
@media(max-width:700px){.conversation-card{grid-template-columns:1fr;gap:14px}.conversation-card dl{grid-template-columns:1fr 1fr}.conversation-action{grid-column:auto}.conversation-action .btn{width:100%}.pagination{gap:8px;justify-content:space-between}.pagination span{font-size:11px;text-align:center}.conversation-message{white-space:normal}.conversations-wrap .titlebar h1{font-size:24px}}
"""

APP_JS = r"""
<script>
document.addEventListener('change', function(e){
  if(e.target && e.target.name === 'request_origin'){
    const method = document.querySelector('input[name="transport_method"]');
    const transporter = document.querySelector('select[name="transporter_mode"]');
    if(e.target.value === 'Consegna in sede'){
      if(method) method.value = 'MEZZO PROPRIO';
      if(transporter) transporter.value = 'IDEM SPED';
    }
    if(e.target.value === 'Veterinario' || e.target.value === 'Privato' || e.target.value === 'Collaboratore'){
      if(transporter) transporter.value = 'DATI PET PARADISE';
    }
    toggleCollaboratorBox();
  }
  if(e.target && e.target.name === 'service_type'){
    toggleCollectiveVetMode();
    refreshUseVoucherBox();
  }
  if(e.target && e.target.name === 'collaborator_name'){
    const collaborators = {
      'HUMANITAS CROCE VERDE': {first:'HUMANITAS', last:'CROCE VERDE', street:'VIA ROMANA, 907', zip:'55100', city:'LUCCA', province:'LU', tax:'01762490462'}
    };
    const data = collaborators[e.target.value];
    if(data){
      const set=(name,value)=>{const field=document.querySelector(`[name="${name}"]`); if(field) field.value=value;};
      set('owner_first_name', data.first); set('owner_last_name', data.last); set('owner_street', data.street);
      set('owner_zip', data.zip); set('owner_city', data.city); set('owner_province', data.province); set('owner_tax_code', data.tax);
    }
  }
  if(e.target && e.target.name === 'veterinarian_id'){
    const opt = e.target.selectedOptions && e.target.selectedOptions[0];
    const clinic = document.querySelector('input[name="clinic_name"]');
    const originMode = document.querySelector('select[name="origin_mode"]');
    const originText = document.querySelector('input[name="origin_text"]');
    if(opt && opt.value){
      const fullName = opt.dataset.fullname || opt.textContent.trim();
      const address = opt.dataset.address || '';
      const city = opt.dataset.city || '';
      if(clinic) clinic.value = fullName;
      if(originMode) originMode.value = 'Testo libero';
      if(originText) originText.value = address ? `${fullName} - ${address}` : fullName;
      const service = document.querySelector('select[name="service_type"]');
      if(service && service.value === 'Cremazione collettiva'){
        const set=(name,value)=>{const field=document.querySelector(`[name="${name}"]`); if(field) field.value=value;};
        set('owner_first_name', fullName);
        set('owner_last_name', '');
        set('owner_street', address ? `${fullName} - ${address}` : fullName);
        set('owner_city', city);
      }
    }
    toggleCollectiveVetMode();
    refreshUseVoucherBox();
  }
  if(e.target && e.target.name === 'owner_veterinarian_id'){
    const opt = e.target.selectedOptions && e.target.selectedOptions[0];
    if(opt && opt.value){
      const set=(name,value)=>{const field=document.querySelector(`[name="${name}"]`); if(field) field.value=value || '';};
      const fullName = opt.dataset.fullname || opt.textContent.trim();
      const shortName = opt.dataset.shortname || fullName;
      const address = opt.dataset.address || '';
      const city = opt.dataset.city || '';
      set('owner_first_name', fullName);
      set('owner_last_name', '');
      set('owner_company', fullName);
      set('owner_phone', opt.dataset.phone || '');
      set('owner_street', address);
      set('owner_city', city);
      const zipMatch = address.match(/\b(\d{5})\b/);
      if(zipMatch) set('owner_zip', zipMatch[1]);
      const provMatch = address.match(/\b([A-Z]{2})\b\s*$/);
      if(provMatch) set('owner_province', provMatch[1]);
      set('origin_text', shortName);
      const originMode=document.querySelector('select[name="origin_mode"]');
      if(originMode) originMode.value='Testo libero';
    }
  }
  if(e.target && e.target.name === 'use_voucher'){
    const pay=document.querySelector('select[name="payment_status"]');
    if(e.target.checked && pay) pay.value='Pagato';
    refreshUseVoucherBox();
  }
  if(e.target && e.target.id === 'transport_method_quick'){
    const field = document.querySelector('input[name="transport_method"]');
    if(field && e.target.value){ field.value = e.target.value; field.dispatchEvent(new Event('input', {bubbles:true})); }
  }
  if(e.target && e.target.id === 'container_id_quick'){
    const field = document.querySelector('input[name="container_id"]');
    if(field && e.target.value){ field.value = e.target.value; field.dispatchEvent(new Event('input', {bubbles:true})); }
  }
});
function ppmNumber(value){
  value = String(value || '').replace(',', '.').replace(/[^0-9.\-]/g, '');
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : 0;
}
function ppmFormat(value){
  if(!value) return '';
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace('.', ',');
}
function updatePreventivoTotal(){
  const fields = document.querySelectorAll('[data-preventivo-sum="1"]');
  if(!fields.length) return;
  let total = 0;
  fields.forEach(function(field){ total += ppmNumber(field.value); });
  const target = document.querySelector('input[name="total_service"]');
  if(target){ target.value = ppmFormat(total); }
  updateRemainingBalance();
}
function updateRemainingBalance(){
  const totalField = document.querySelector('input[name="total_service"]');
  const definitiveField = document.querySelector('input[name="total_text"]');
  const depositField = document.querySelector('input[name="deposit"]');
  const remainingField = document.querySelector('input[name="remaining_balance"]');
  if(!remainingField) return;
  const total = definitiveField && definitiveField.value.trim() ? ppmNumber(definitiveField.value) : ppmNumber(totalField ? totalField.value : 0);
  const remaining = total - ppmNumber(depositField ? depositField.value : 0);
  remainingField.value = ppmFormat(remaining);
}
function setupNumericBudgetFields(){
  const names=['price_cremation','price_pickup','price_urn','price_urn_2','price_delivery','price_cast','price_cast_2','price_evening','price_night','price_holiday','price_accessories','price_accessories_2','total_service','total_text','deposit','remaining_balance'];
  names.forEach(function(name){
    const field=document.querySelector(`input[name="${name}"]`);
    if(!field) return;
    field.inputMode='decimal';
    field.pattern='[0-9]+([,.][0-9]{1,2})?';
    field.placeholder='0,00';
    field.dataset.moneyOnly='1';
    field.addEventListener('beforeinput',function(event){
      if(event.inputType.startsWith('delete') || event.inputType.startsWith('history')) return;
      if(event.data && !/^[0-9,.]+$/.test(event.data)) event.preventDefault();
    });
    field.addEventListener('input',function(){
      let value=field.value.replace(/[^0-9,.]/g,'');
      const separator=Math.max(value.lastIndexOf(','),value.lastIndexOf('.'));
      if(separator>=0){
        const integer=value.slice(0,separator).replace(/[,.]/g,'');
        const decimals=value.slice(separator+1).replace(/[,.]/g,'').slice(0,2);
        value=integer+((decimals || value.endsWith(',') || value.endsWith('.')) ? ','+decimals : '');
      }
      if(field.value!==value) field.value=value;
    });
  });
}
document.addEventListener('input', function(e){
  if(e.target && e.target.name === 'owner_city'){
    const provinceField = document.querySelector('input[name="owner_province"]');
    if(provinceField){
      const map = {
        'livorno':'LI','collesalvetti':'LI','rosignano marittimo':'LI','cecina':'LI','bibona':'LI','castagneto carducci':'LI','san vincenzo':'LI','campiglia marittima':'LI','piombino':'LI','portoferraio':'LI',
        'empoli':'FI','firenze':'FI','capraia e limite':'FI','cerreto guidi':'FI','certaldo':'FI','fucecchio':'FI','gambassi terme':'FI','montaione':'FI','montelupo fiorentino':'FI','montespertoli':'FI','vinci':'FI','scandicci':'FI','sesto fiorentino':'FI',
        'pisa':'PI','cascina':'PI','pontedera':'PI','san miniato':'PI','volterra':'PI','ponsacco':'PI','calci':'PI','vicopisano':'PI','calcinaia':'PI','crespina lorenzana':'PI','fauglia':'PI',
        'lucca':'LU','viareggio':'LU','camaiore':'LU','capannori':'LU','altopascio':'LU','porcari':'LU','massarosa':'LU','pietrasanta':'LU','forte dei marmi':'LU',
        'grosseto':'GR','follonica':'GR','castiglione della pescaia':'GR','massa marittima':'GR','orbetello':'GR',
        'siena':'SI','poggibonsi':'SI','colle di val d elsa':'SI','monteriggioni':'SI','san gimignano':'SI',
        'arezzo':'AR','montevarchi':'AR','san giovanni valdarno':'AR','cortona':'AR',
        'prato':'PO','montemurlo':'PO','poggio a caiano':'PO','carmignano':'PO',
        'pistoia':'PT','montecatini terme':'PT','pescia':'PT','quarrata':'PT',
        'massa':'MS','carrara':'MS','aulla':'MS','pontremoli':'MS'
      };
      const key = e.target.value.trim().toLowerCase();
      if(map[key]) provinceField.value = map[key];
    }
  }
  if(e.target && e.target.matches('[data-preventivo-sum="1"]')) updatePreventivoTotal();
  if(e.target && (e.target.name === 'deposit' || e.target.name === 'total_service' || e.target.name === 'total_text')) updateRemainingBalance();
});
function setupZipLookup(){
  const street=document.querySelector('input[name="owner_street"]');
  const city=document.querySelector('input[name="owner_city"]');
  const province=document.querySelector('input[name="owner_province"]');
  const zip=document.querySelector('input[name="owner_zip"]');
  if(!street || !city || !province || !zip) return;
  let lastQuery='';
  const lookup=ppmDebounce(async function(){
    const address=street.value.trim(), municipality=city.value.trim(), prov=province.value.trim().toUpperCase();
    if(!address || !municipality || prov.length !== 2) return;
    const query=[address, municipality, prov].join('|');
    if(query === lastQuery) return;
    lastQuery=query;
    try{
      const params=new URLSearchParams({indirizzo:address, comune:municipality, provincia:prov});
      const response=await fetch(`/api/cap?${params.toString()}`, {headers:{'Accept':'application/json'}});
      const data=await response.json();
      if(data.ok && data.zip && (!zip.value.trim() || zip.dataset.autoFilled === '1')){
        zip.value=data.zip;
        zip.dataset.autoFilled='1';
      }
    }catch(error){}
  }, 900);
  [street,city,province].forEach(function(field){ field.addEventListener('input', lookup); field.addEventListener('blur', lookup); });
  zip.addEventListener('input', function(){ if(zip.dataset.autoFilled === '1') zip.dataset.autoFilled='0'; });
  lookup();
}
function setupUrnNotesField(){
  const hidden=document.querySelector('input[name="urn_notes"]');
  const price=document.querySelector('input[name="price_urn"]');
  if(!hidden || !price) return;
  hidden.type='text';
  hidden.placeholder='Descrizione o note libere sull urna';
  const field=document.createElement('div');
  field.className='field';
  const label=document.createElement('label');
  label.textContent='Urna - testo libero';
  field.appendChild(label);
  field.appendChild(hidden);
  const priceField=price.closest('.field');
  priceField.parentNode.insertBefore(field, priceField.nextSibling);
}
function setupBudgetExtras(){
  const fields=document.querySelector('.section input[name="price_cremation"]')?.closest('.fields');
  if(!fields) return;
  const wrapField=(element,label,after,hidden=false)=>{
    element.type='text';
    const wrap=document.createElement('div'); wrap.className='field'+(hidden?' hidden':'');
    const lab=document.createElement('label'); lab.textContent=label; wrap.append(lab,element);
    after.parentNode.insertBefore(wrap,after.nextSibling); return wrap;
  };
  const addButton=(label,after,targets)=>{
    const button=document.createElement('button'); button.type='button'; button.className='btn ghost budget-add'; button.textContent=label;
    after.parentNode.insertBefore(button,after.nextSibling);
    button.onclick=()=>{targets.forEach(target=>target.classList.remove('hidden'));button.remove();};
    if(targets.some(target=>target.querySelector('input,select')?.value)) button.click();
    return button;
  };
  const totalArea=document.querySelector('textarea[name="total_text"]');
  if(totalArea){
    const input=document.createElement('input'); input.name='total_text'; input.value=totalArea.value; input.inputMode='decimal';
    totalArea.replaceWith(input); input.closest('.field').querySelector('label').textContent='TOTALE D €';
  }
  const totalService=document.querySelector('input[name="total_service"]'); if(totalService){totalService.readOnly=true;totalService.closest('.field').querySelector('label').textContent='Totale calcolato €';}
  const urn=document.querySelector('input[name="price_urn"]')?.closest('.field');
  const urn2=wrapField(document.querySelector('input[name="price_urn_2"]'),'Seconda urna €',urn,true); urn2.querySelector('input').dataset.preventivoSum='1';
  const urnNotes2=wrapField(document.querySelector('input[name="urn_notes_2"]'),'Seconda urna - testo libero',urn2,true);
  addButton('+ Aggiungi altra urna',urnNotes2,[urn2,urnNotes2]);
  const cast=document.querySelector('input[name="price_cast"]')?.closest('.field');
  const cast2=wrapField(document.querySelector('input[name="price_cast_2"]'),'Secondo calco €',cast,true); cast2.querySelector('input').dataset.preventivoSum='1';
  const castButton=addButton('+ Aggiungi altro calco',cast2,[cast2]);
  const accessoryPrice=document.querySelector('input[name="price_accessories"]')?.closest('.field');
  const makeAccessorySelect=(hidden,name)=>{const select=document.createElement('select');select.name=name;['','Calco naso','Collana','Braccialetto','Calco inchiostro','Altro'].forEach(value=>{const option=new Option(value||'Seleziona accessorio',value);select.add(option)});select.value=hidden.value;hidden.replaceWith(select);return select;};
  const accessoryTypeHidden=document.querySelector('input[name="accessory_type"]'); const accessoryType=makeAccessorySelect(accessoryTypeHidden,'accessory_type');
  const accessoryTypeWrap=document.createElement('div');accessoryTypeWrap.className='field';accessoryTypeWrap.innerHTML='<label>Tipo accessorio</label>';accessoryTypeWrap.append(accessoryType);
  cast.parentNode.insertBefore(accessoryTypeWrap,cast.nextSibling); accessoryTypeWrap.parentNode.insertBefore(accessoryPrice,accessoryTypeWrap.nextSibling);
  if(castButton.isConnected) cast.parentNode.insertBefore(castButton,cast.nextSibling);
  const accessoryType2Hidden=document.querySelector('input[name="accessory_type_2"]'); const accessoryType2=makeAccessorySelect(accessoryType2Hidden,'accessory_type_2');
  const accessoryType2Wrap=document.createElement('div');accessoryType2Wrap.className='field hidden';accessoryType2Wrap.innerHTML='<label>Secondo accessorio</label>';accessoryType2Wrap.append(accessoryType2);
  accessoryPrice.parentNode.insertBefore(accessoryType2Wrap,accessoryPrice.nextSibling);
  const accessory2=wrapField(document.querySelector('input[name="price_accessories_2"]'),'Secondi accessori €',accessoryType2Wrap,true); accessory2.querySelector('input').dataset.preventivoSum='1';
  addButton('+ Aggiungi altri accessori',accessory2,[accessoryType2Wrap,accessory2]);
}
document.addEventListener('DOMContentLoaded', function(){ setupBudgetExtras(); setupNumericBudgetFields(); updatePreventivoTotal(); updateRemainingBalance(); setupZipLookup(); setupUrnNotesField(); });
function toggleCollaboratorBox(){
  const origin = document.querySelector('select[name="request_origin"]');
  const box = document.getElementById('collaboratorBox');
  if(box && origin){ box.classList.toggle('hidden', origin.value !== 'Collaboratore'); }
}
document.addEventListener('DOMContentLoaded', toggleCollaboratorBox);
function toggleCollectiveVetMode(){
  const service = document.querySelector('select[name="service_type"]');
  const vet = document.querySelector('select[name="veterinarian_id"]');
  const exempt = !!(service && vet && service.value === 'Cremazione collettiva' && vet.value);
  ['owner_first_name','owner_last_name','owner_phone','owner_tax_code','owner_street','owner_city','owner_province','owner_zip'].forEach(function(name){
    const field=document.querySelector(`[name="${name}"]`);
    if(field){ field.required = !exempt; }
  });
}
document.addEventListener('DOMContentLoaded', function(){
  setupClientLookup();
  setupVetLookup();
  toggleCollectiveVetMode();
  refreshUseVoucherBox();
});
function ppmDebounce(fn, delay){
  let timer=null;
  return function(){ const args=arguments; clearTimeout(timer); timer=setTimeout(()=>fn.apply(this,args), delay); };
}
function lookupHtmlState(text){ return `<div class="lookup-state">${text}</div>`; }
function setupClientLookup(){
  const input=document.getElementById('clientSearch');
  const results=document.getElementById('clientResults');
  const selected=document.getElementById('clientSelected');
  const selectedText=document.getElementById('clientSelectedText');
  const clearBtn=document.getElementById('clearClientSelection');
  const clientId=document.querySelector('input[name="client_id"]');
  if(!input || !results) return;
  function setField(name,value){ const field=document.querySelector(`[name="${name}"]`); if(field) field.value=value || ''; }
  function showSelected(label){
    if(selectedText) selectedText.textContent = label ? `Cliente selezionato: ${label}` : '';
    if(selected) selected.classList.toggle('hidden', !label);
  }
  if(clientId && clientId.value){
    const first=document.querySelector('[name="owner_first_name"]')?.value || '';
    const last=document.querySelector('[name="owner_last_name"]')?.value || '';
    const company=document.querySelector('[name="owner_company"]')?.value || '';
    showSelected(`${first} ${last}`.trim() || company);
  }
  const search=ppmDebounce(async function(){
    const q=input.value.trim();
    if(q.length < 2){ results.innerHTML=lookupHtmlState('Scrivi almeno 2 caratteri'); results.classList.remove('hidden'); return; }
    results.innerHTML=lookupHtmlState('Ricerca in corso...');
    results.classList.remove('hidden');
    try{
      const res=await fetch(`/api/clienti/search?q=${encodeURIComponent(q)}`, {headers:{'Accept':'application/json'}});
      const data=await res.json();
      if(!data.ok) throw new Error(data.error || 'Errore');
      if(!data.results.length){ results.innerHTML=lookupHtmlState('Nessun risultato'); return; }
      results.innerHTML=data.results.map(function(c){
        const label=c.display || c.company_name || 'Cliente';
        const subtitle=c.subtitle || '';
        const meta=`ID ${c.id}${c.practice_count ? ' - '+c.practice_count+' pratiche' : ''}${c.last_practice ? ' - ultima '+c.last_practice : ''}`;
        return `<button type="button" class="lookup-item" data-client='${JSON.stringify(c).replace(/'/g,'&#39;')}'><b>${label}</b><small>${subtitle}</small><small>${meta}</small></button>`;
      }).join('');
    }catch(err){
      results.innerHTML=lookupHtmlState('Errore di rete durante la ricerca');
    }
  }, 300);
  input.addEventListener('input', search);
  results.addEventListener('click', function(e){
    const btn=e.target.closest('.lookup-item');
    if(!btn) return;
    const c=JSON.parse(btn.getAttribute('data-client'));
    if(clientId) clientId.value=c.id || '';
    setField('owner_first_name', c.first_name);
    setField('owner_last_name', c.last_name);
    setField('owner_company', c.company_name);
    setField('owner_phone', c.phone);
    setField('owner_phone_2', c.phone_2);
    setField('owner_email', c.email);
    setField('owner_tax_code', c.tax_code);
    setField('owner_vat', c.vat_number);
    setField('owner_street', c.street || c.address);
    setField('owner_city', c.city);
    setField('owner_province', c.province);
    setField('owner_zip', c.zip);
    setField('owner_notes', c.notes);
    showSelected(c.display || c.company_name || `ID ${c.id}`);
    input.value='';
    results.classList.add('hidden');
  });
  if(clearBtn){
    clearBtn.addEventListener('click', function(){
      if(clientId) clientId.value='';
      showSelected('');
      input.value='';
      results.classList.add('hidden');
    });
  }
  document.addEventListener('click', function(e){ if(!e.target.closest('.lookup')) results.classList.add('hidden'); });
}
function setupVetLookup(){
  const input=document.getElementById('vetSearch');
  const results=document.getElementById('vetResults');
  const select=document.querySelector('select[name="veterinarian_id"]');
  const clearBtn=document.getElementById('clearVetSelection');
  if(!input || !results || !select) return;
  function chooseVet(v){
    let option=Array.from(select.options).find(o=>o.value===String(v.id));
    if(!option){
      option=new Option(v.display || v.clinic_name, v.id);
      select.appendChild(option);
    }
    option.dataset.fullname=v.clinic_name || v.display || '';
    option.dataset.address=v.address || '';
    option.dataset.city=v.city || '';
    select.value=String(v.id);
    select.dispatchEvent(new Event('change', {bubbles:true}));
    input.value=v.display || v.clinic_name || '';
    results.classList.add('hidden');
  }
  const search=ppmDebounce(async function(){
    const q=input.value.trim();
    if(q.length < 2){ results.innerHTML=lookupHtmlState('Scrivi almeno 2 caratteri'); results.classList.remove('hidden'); return; }
    results.innerHTML=lookupHtmlState('Ricerca in corso...');
    results.classList.remove('hidden');
    try{
      const res=await fetch(`/api/veterinari/search?q=${encodeURIComponent(q)}`, {headers:{'Accept':'application/json'}});
      const data=await res.json();
      if(!data.ok) throw new Error(data.error || 'Errore');
      if(!data.results.length){ results.innerHTML=lookupHtmlState('Nessun risultato'); return; }
      results.innerHTML=data.results.map(function(v){
        return `<button type="button" class="lookup-item" data-vet='${JSON.stringify(v).replace(/'/g,'&#39;')}'><b>${v.display}</b><small>${v.subtitle || ''}</small><small>ID ${v.id}</small></button>`;
      }).join('');
    }catch(err){
      results.innerHTML=lookupHtmlState('Errore di rete durante la ricerca');
    }
  }, 300);
  input.addEventListener('input', search);
  results.addEventListener('click', function(e){
    const btn=e.target.closest('.lookup-item');
    if(!btn) return;
    chooseVet(JSON.parse(btn.getAttribute('data-vet')));
  });
  if(clearBtn){
    clearBtn.addEventListener('click', function(){
      select.value='';
      select.dispatchEvent(new Event('change', {bubbles:true}));
      input.value='';
      results.classList.add('hidden');
    });
  }
  document.addEventListener('click', function(e){ if(!e.target.closest('.lookup')) results.classList.add('hidden'); });
}
async function refreshUseVoucherBox(){
  const checkbox=document.querySelector('input[name="use_voucher"]');
  const box=document.getElementById('useVoucherBox');
  const select=document.querySelector('select[name="used_voucher_id"]');
  const status=document.getElementById('useVoucherStatus');
  const vet=document.querySelector('select[name="veterinarian_id"]');
  const service=document.querySelector('select[name="service_type"]');
  if(!checkbox || !box || !select || !status) return;
  const enabled=checkbox.checked;
  box.classList.toggle('hidden', !enabled);
  if(!enabled){ select.value=''; return; }
  if(service && service.value !== 'Cremazione collettiva'){
    status.textContent='USA BUONO è pensato per le cremazioni collettive.';
    select.classList.add('hidden');
    return;
  }
  if(!vet || !vet.value){
    status.textContent='Seleziona prima un veterinario.';
    select.classList.add('hidden');
    return;
  }
  status.textContent='Carico buoni maturati...';
  select.classList.add('hidden');
  try{
    const res=await fetch(`/api/veterinari/${encodeURIComponent(vet.value)}/buoni`, {headers:{'Accept':'application/json'}});
    const data=await res.json();
    if(!data.ok) throw new Error(data.error || 'Errore');
    if(!data.results.length){
      status.textContent='Questo veterinario non ha buoni maturati disponibili.';
      select.innerHTML='<option value="">Nessun buono disponibile</option>';
      return;
    }
    select.innerHTML='<option value="">Seleziona il buono da usare</option>'+data.results.map(function(b){
      const label=[b.created_at, b.animal, b.species, b.practice_number].filter(Boolean).join(' - ');
      return `<option value="${b.id}">${label}</option>`;
    }).join('');
    if(select.dataset.current && Array.from(select.options).some(o=>o.value===select.dataset.current)) select.value=select.dataset.current;
    status.textContent='Seleziona il buono maturato da usare per questa collettiva.';
    select.classList.remove('hidden');
  }catch(err){
    status.textContent='Errore durante il caricamento dei buoni.';
    select.classList.add('hidden');
  }
}
document.addEventListener('click', function(e){
  if(e.target && e.target.id === 'showSecondAnimal'){
    const box=document.getElementById('secondAnimalBox');
    if(box){ box.style.display='block'; e.target.style.display='none'; }
  }
});
async function sharePracticePdf(url, title){
  const absoluteUrl = new URL(url, window.location.href).toString();
  const filename = arguments.length > 2 && arguments[2] ? arguments[2] : (absoluteUrl.includes('ddt-bozza') ? 'DCS-bozza.pdf' : 'DDT-pratica.pdf');
  try{
    if(navigator.share){
      try{
        const response = await fetch(absoluteUrl);
        const blob = await response.blob();
        const file = new File([blob], filename, {type:'application/pdf'});
        if(navigator.canShare && navigator.canShare({files:[file]})){
          await navigator.share({title:title, text:title, files:[file]});
          return;
        }
      }catch(fileError){}
      await navigator.share({title:title, text:title, url:absoluteUrl});
      return;
    }
  }catch(error){}
  try{
    await navigator.clipboard.writeText(absoluteUrl);
    alert('Link PDF copiato. Puoi incollarlo in WhatsApp, email o messaggio.');
  }catch(error){
    window.open(absoluteUrl, '_blank');
  }
}
let deferredInstallPrompt=null;
window.addEventListener('beforeinstallprompt', function(event){
  event.preventDefault();
  deferredInstallPrompt=event;
  document.querySelectorAll('.install-btn').forEach(button=>button.classList.add('ready'));
});
window.addEventListener('appinstalled', function(){
  deferredInstallPrompt=null;
  document.querySelectorAll('.install-btn').forEach(button=>button.classList.remove('ready'));
});
document.addEventListener('DOMContentLoaded',function(){
  const standalone=window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone===true;
  if(!standalone) document.querySelectorAll('.install-btn').forEach(button=>button.classList.add('ready'));
});
async function installPetParadise(){
  if(deferredInstallPrompt){
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt=null;
    document.querySelectorAll('.install-btn').forEach(button=>button.classList.remove('ready'));
    return;
  }
  const isiOS=/iphone|ipad|ipod/i.test(navigator.userAgent);
  const hint=document.createElement('div');
  hint.className='install-hint';
  hint.innerHTML=isiOS ? '<b>Installa su iPhone o iPad</b>Apri il menu Condividi di Safari e scegli “Aggiungi alla schermata Home”.<button class="btn ghost" type="button">Ho capito</button>' : '<b>Installa Pet Paradise Manager</b>Apri il menu del browser e scegli “Installa app” o “Aggiungi a schermata Home”.<button class="btn ghost" type="button">Ho capito</button>';
  hint.querySelector('button').onclick=()=>hint.remove();
  document.body.appendChild(hint);
}
function toggleTheme(){
  document.body.classList.toggle('light-theme');
  localStorage.setItem('ppm-theme',document.body.classList.contains('light-theme')?'light':'dark');
}
function toggleMoreMenu(force){
  const open=typeof force==='boolean' ? force : !document.body.classList.contains('more-open');
  document.body.classList.toggle('more-open',open);
}
document.addEventListener('DOMContentLoaded',function(){
  if(localStorage.getItem('ppm-theme')==='light') document.body.classList.add('light-theme');
  document.addEventListener('keydown',function(event){
    if(event.key==='Escape') toggleMoreMenu(false);
    if(event.key==='/' && !/input|textarea|select/i.test(document.activeElement.tagName)){
      event.preventDefault(); document.getElementById('globalSearch')?.focus();
    }
  });
});
if('serviceWorker' in navigator){
  window.addEventListener('load',()=>navigator.serviceWorker.register('/sw.js').catch(error=>console.warn('Service worker non registrato',error)));
}
</script>
"""


LUCIDE_PATHS = {
    "home": '<path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/>',
    "archive": '<rect width="18" height="4" x="3" y="3" rx="1"/><path d="M5 7v13h14V7M9 11h6"/>',
    "message": '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="M8 9h8M8 13h5"/>',
    "stethoscope": '<path d="M11 2v2M5 2v2M5 3H3v4a6 6 0 0 0 12 0V3h-2"/><circle cx="18" cy="16" r="3"/><path d="M15 16H9a4 4 0 0 1-4-4v-1"/>',
    "clipboard": '<rect width="16" height="18" x="4" y="4" rx="2"/><path d="M9 4V2h6v2M8 9h8M8 13h6"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "paw": '<circle cx="11" cy="17" r="4"/><circle cx="5" cy="10" r="2"/><circle cx="10" cy="6" r="2"/><circle cx="16" cy="7" r="2"/><circle cx="19" cy="12" r="2"/>',
    "wallet": '<path d="M20 7V5a2 2 0 0 0-2-2H5a3 3 0 0 0 0 6h15v12H5a3 3 0 0 1-3-3V6"/><path d="M16 13h2"/>',
    "receipt": '<path d="M4 2v20l3-2 3 2 2-2 3 2 2-2 3 2V2l-3 2-3-2-2 2-3-2-2 2Z"/><path d="M8 9h8M8 13h6"/>',
    "chart": '<path d="M3 3v18h18"/><path d="m7 16 4-5 4 3 5-7"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1v.1h-4v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1-.4h-.1v-4H3A1.7 1.7 0 0 0 4.6 8.5a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1v-.1h4V3a1.7 1.7 0 0 0 1.1 1.6 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.15.37.37.7.6 1 .28.25.63.39 1 .4h.1v4H21a1.7 1.7 0 0 0-1.6.6Z"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 1 1 5.2 2c-.9.8-2.3 1.4-2.3 3M12 18h.01"/>',
    "calendar": '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
}


def lucide(name, label=""):
    path=LUCIDE_PATHS.get(name,LUCIDE_PATHS["menu"])
    aria=f' aria-label="{esc(label)}"' if label else ' aria-hidden="true"'
    return f'<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"{aria}>{path}</svg>'


def money_value(value):
    text=str(value or "").strip().replace("€","").replace(" ","")
    if "," in text and "." in text:
        text=text.replace(".","").replace(",",".")
    else:
        text=text.replace(",",".")
    match=re.search(r"-?\d+(?:\.\d+)?",text)
    try:
        return float(match.group(0)) if match else 0.0
    except ValueError:
        return 0.0


def money_it(value):
    return f"€ {value:,.2f}".replace(",","X").replace(".",",").replace("X",".")


def effective_total(practice):
    keys=practice.keys() if hasattr(practice,"keys") else practice
    definitive=practice["total_text"] if "total_text" in keys else ""
    return money_value(definitive) if str(definitive or "").strip() else money_value(practice["total_service"] if "total_service" in keys else "")


def income_chart(values,labels):
    width,height=660,240; left,right,top,bottom=56,18,20,42
    plot_w,plot_h=width-left-right,height-top-bottom
    maximum=max(max(values,default=0),1)
    points=[]
    for index,value in enumerate(values):
        x=left+(plot_w*index/max(len(values)-1,1)); y=top+plot_h-(value/maximum*plot_h)
        points.append((x,y))
    line=" ".join(f"{x:.1f},{y:.1f}" for x,y in points)
    area=f"M {left},{top+plot_h} L "+" L ".join(f"{x:.1f},{y:.1f}" for x,y in points)+f" L {left+plot_w},{top+plot_h} Z"
    grid=[]
    for step in range(4):
        y=top+plot_h-(plot_h*step/3); amount=maximum*step/3
        grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}"/><text x="{left-8}" y="{y+4:.1f}" text-anchor="end">€ {amount:,.0f}</text>')
    dates=''.join(f'<text x="{points[i][0]:.1f}" y="{height-12}" text-anchor="middle">{esc(label)}</text>' for i,label in enumerate(labels))
    dots=''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4"><title>{esc(labels[i])}: {money_it(values[i])}</title></circle>' for i,(x,y) in enumerate(points))
    return f'''<svg class="income-chart" viewBox="0 0 {width} {height}" role="img" aria-label="Entrate giornaliere degli ultimi sette giorni"><defs><linearGradient id="incomeArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ef405f" stop-opacity=".5"/><stop offset="1" stop-color="#ef405f" stop-opacity="0"/></linearGradient></defs><g class="chart-grid">{''.join(grid)}</g><path class="chart-area" d="{area}"/><polyline class="chart-line" points="{line}"/>{dots}<g class="chart-dates">{dates}</g></svg>'''


def layout(title, body, user=None):
    nav = ""; app_header=""; mobile_nav=""
    if user:
        links=[
            ("/","home","Dashboard"),("/pratiche","archive","Archivio"),("/conversazioni-whatsapp","message","Conversazioni WhatsApp"),("/veterinari","stethoscope","Veterinari"),
            ("/archivio/pratiche","clipboard","Gestionale"),("/archivio/clienti","users","Clienti"),("/archivio/pratiche","paw","Animali"),
            ("/archivio/pratiche?pagamento=Da%20saldare","wallet","Pagamenti"),("/archivio/pratiche?pagamento=Pagato","receipt","Fatture"),
            ("/bilanci","chart","Report"),("/diagnostica","settings","Impostazioni"),("mailto:assistenza@petparadise.it","help","Assistenza"),
        ]
        nav_links=''.join(f'<a href="{href}">{lucide(icon)}<span>{label}</span></a>' for href,icon,label in links)
        nav=f'''<nav class="nav" aria-label="Menu principale">{nav_links}<button class="btn ghost install-btn" type="button" onclick="installPetParadise()">{lucide("plus")}<span>Installa App</span></button><a class="logout" href="/logout">{lucide("menu")}<span>Esci</span></a></nav>'''
        today=datetime.now(); date_label=today.strftime("%d/%m/%Y"); weekday=["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"][today.weekday()]
        app_header=f'''<header class="app-header"><form class="header-search" action="/archivio/pratiche" method="get" role="search">{lucide("search")}<label class="sr-only" for="globalSearch">Cerca nel gestionale</label><input id="globalSearch" name="q" placeholder="Cerca pratica, cliente, animale..." autocomplete="off"></form><div class="header-actions"><a class="icon-btn" href="/archivio/pratiche?promemoria=estremi" aria-label="Notifiche">{lucide("bell")}</a><button class="icon-btn" type="button" onclick="toggleTheme()" aria-label="Cambia tema">{lucide("sun")}</button><a class="btn header-new" href="/nuova">{lucide("plus")}<span>Nuova pratica</span></a><time datetime="{today.date().isoformat()}">{date_label}<small>{weekday}</small></time></div></header>'''
        drawer_links=''.join(f'<a href="{href}">{lucide(icon)}<span>{label}</span></a>' for href,icon,label in links)
        mobile_nav=f'''<nav class="bottom-nav" aria-label="Navigazione mobile"><a href="/">{lucide("home")}<span>Dashboard</span></a><a href="/bilanci">{lucide("chart")}<span>Bilanci</span></a><a class="bottom-new" href="/nuova" aria-label="Nuova pratica">{lucide("plus")}</a><a href="/pratiche">{lucide("archive")}<span>Archivio</span></a><button type="button" onclick="toggleMoreMenu()">{lucide("menu")}<span>Altro</span></button></nav><div class="more-backdrop" onclick="toggleMoreMenu(false)"></div><aside class="more-menu" aria-label="Altre funzioni"><div class="more-title"><b>Menu</b><button class="icon-btn" onclick="toggleMoreMenu(false)" aria-label="Chiudi">×</button></div>{drawer_links}<button class="btn ghost install-btn" type="button" onclick="installPetParadise()">Installa App</button></aside>'''
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#e9475b"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="PP Manager"><meta name="application-name" content="Pet Paradise Manager"><meta name="format-detection" content="telephone=no"><link rel="manifest" href="/manifest.json"><link rel="apple-touch-icon" href="/assets/apple-touch-icon.png"><link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png"><title>{esc(title)} - Pet Paradise Manager</title><style>{CSS}</style></head><body><a class="skip-link" href="#main-content">Vai al contenuto</a><aside class="top"><a class="brand" href="/"><img class="brand-logo" src="/assets/company_logo.png" alt="Pet Paradise"><span class="brand-copy">Pet Paradise <small>MANAGER</small></span></a>{nav}</aside>{app_header}<div id="main-content">{body}</div>{mobile_nav}{APP_JS}</body></html>'''


class App(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_html(self, content, status=200):
        data = content.encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def send_text(self, content, status=200):
        data = content.encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def send_png(self, path):
        data = path.read_bytes()
        self.send_response(200); self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def send_static(self,path,content_type,cache="public, max-age=86400"):
        data=path.read_bytes()
        self.send_response(200); self.send_header("Content-Type",content_type)
        self.send_header("Cache-Control",cache); self.send_header("Content-Length",str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def error_page(self, title, message, back="/"):
        body=f'''<main class="wrap"><section class="section"><h1>{esc(title)}</h1><p class="sub">{esc(message)}</p><div class="actions" style="margin-top:18px"><a class="btn" href="{esc(back)}">Torna indietro</a></div></section></main>'''
        self.send_html(layout(title,body,self.user()),500)

    def pdf_error_page(self, exc, back):
        print(traceback.format_exc())
        detail = f"{type(exc).__name__}: {exc}"
        asset_status = []
        for name in ("DCS_NUOVO.pdf", "DCS_LIVORNO.pdf", "DCS_EMPOLI.pdf"):
            path = ASSETS / name
            if path.exists():
                asset_status.append(f"{name}: OK ({path.stat().st_size} byte)")
            else:
                asset_status.append(f"{name}: MANCANTE")
        message = (
            f"Errore tecnico: {detail}\n"
            f"Cartella assets: {ASSETS}\n"
            f"Modelli: {' | '.join(asset_status)}\n"
            f"Cartella dati: {DATA}\n"
            f"Cartella DDT: {DDT_DIR}"
        )
        return self.error_page("PDF non generato", message, back)

    def redirect(self, path):
        self.send_response(303); self.send_header("Location", path); self.end_headers()

    def form(self):
        size = int(self.headers.get("Content-Length", 0))
        return {k: v[-1] for k, v in parse_qs(self.rfile.read(size).decode()).items()}

    def user(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", "")); morsel = jar.get("ppm_session")
        if not morsel: return None
        with db() as c:
            return c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1", (morsel.value,)).fetchone()

    def require_user(self):
        user = self.user()
        if not user: self.redirect("/login")
        return user

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health": return self.send_text("ok")
        if path == "/cron/whatsapp": return self.whatsapp_cron()
        if path == "/webhook/whatsapp": return self.whatsapp_webhook_verify()
        if path == "/manifest.json": return self.send_static(ASSETS / "manifest.json","application/manifest+json; charset=utf-8", "no-cache")
        if path == "/sw.js": return self.send_static(ASSETS / "sw.js","application/javascript; charset=utf-8", "no-cache")
        static_assets={
            "/assets/company_logo.png":"company_logo.png",
            "/assets/pwa-192.png":"pwa-192.png",
            "/assets/pwa-512.png":"pwa-512.png",
            "/assets/apple-touch-icon.png":"apple-touch-icon.png",
            "/assets/favicon-32.png":"favicon-32.png",
        }
        if path in static_assets and (ASSETS / static_assets[path]).exists(): return self.send_static(ASSETS / static_assets[path],"image/png")
        match = re.fullmatch(r"/pubblici/ddt/([A-Za-z0-9_-]+)\.pdf", path)
        if match: return self.public_ddt(match.group(1))
        if path == "/login": return self.login_page()
        if path == "/logout": return self.logout()
        user = self.require_user()
        if not user: return
        if path == "/": return self.dashboard(user)
        if path == "/bilanci": return self.balances(user)
        if path == "/conversazioni-whatsapp": return self.whatsapp_conversations(user)
        if path == "/diagnostica": return self.diagnostics(user)
        if path == "/whatsapp-diagnostica": return self.whatsapp_diagnostics(user)
        if path == "/api/clienti/search": return self.api_clients_search(user)
        if path == "/api/cap": return self.api_zip_lookup(user)
        if path == "/api/veterinari/search": return self.api_veterinarians_search(user)
        match = re.fullmatch(r"/api/veterinari/(\d+)/buoni", path)
        if match: return self.api_veterinarian_vouchers(user, int(match.group(1)))
        if path == "/nuova": return self.new_page(user)
        if path == "/pratiche": return self.archive_home(user)
        if path == "/archivio/pratiche": return self.archive(user)
        if path == "/archivio/clienti": return self.clients_archive(user)
        if path == "/cestino": return self.trash_page(user)
        if path == "/database-mesi": return self.redirect("/pratiche")
        if path == "/veterinari": return self.veterinarians_page(user)
        match = re.fullmatch(r"/veterinari/(\d+)", path)
        if match: return self.veterinarian_detail(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)", path)
        if match: return self.practice(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/modifica", path)
        if match: return self.edit_page(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/elimina", path)
        if match: return self.delete_warning_page(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/ddt\.pdf", path)
        if match: return self.download_ddt(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/ddt-download\.pdf", path)
        if match: return self.download_ddt(user, int(match.group(1)), attachment=True)
        match = re.fullmatch(r"/pratiche/(\d+)/ddt-bozza\.pdf", path)
        if match: return self.draft_ddt(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/ddt-bozza-download\.pdf", path)
        if match: return self.draft_ddt(user, int(match.group(1)), attachment=True)
        match = re.fullmatch(r"/pratiche/(\d+)/firma", path)
        if match: return self.signature_page(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/whatsapp-conferma", path)
        if match: return self.whatsapp_confirm_page(user, int(match.group(1)))
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/cron/whatsapp": return self.whatsapp_cron()
        if path == "/webhook/whatsapp": return self.whatsapp_webhook_receive()
        if path == "/login": return self.login_submit()
        user = self.require_user()
        if not user: return
        if path == "/nuova": return self.create_practice(user)
        if path == "/veterinari": return self.save_veterinarian(user)
        match = re.fullmatch(r"/veterinari/(\d+)/elimina", path)
        if match: return self.delete_veterinarian(user, int(match.group(1)))
        match = re.fullmatch(r"/veterinari/(\d+)/buoni", path)
        if match: return self.save_manual_voucher(user, int(match.group(1)))
        match = re.fullmatch(r"/buoni/(\d+)/modifica", path)
        if match: return self.edit_voucher(user, int(match.group(1)))
        match = re.fullmatch(r"/buoni/(\d+)/elimina", path)
        if match: return self.delete_voucher(user, int(match.group(1)))
        match = re.fullmatch(r"/veterinari/(\d+)/buono-usato", path)
        if match: return self.use_voucher(user, int(match.group(1)))
        match = re.fullmatch(r"/buoni/(\d+)/usato", path)
        if match: return self.use_specific_voucher(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/stato", path)
        if match: return self.change_state(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/whatsapp", path)
        if match: return self.resend_whatsapp(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/whatsapp-annulla", path)
        if match: return self.cancel_whatsapp_manual(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/modifica", path)
        if match: return self.edit_submit(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/ddt", path)
        if match: return self.assign_ddt(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/firma", path)
        if match: return self.signature_submit(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/elimina", path)
        if match: return self.delete_practice(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/ripristina", path)
        if match: return self.restore_practice(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/elimina-definitiva", path)
        if match: return self.permanent_delete_practice(user, int(match.group(1)))
        self.send_error(404)

    def login_page(self, error=""):
        body=f'''<main class="login"><h1>Pet Paradise Manager</h1><p class="sub">Accedi alla gestione operativa.</p>{f'<div class="flash warning">{esc(error)}</div>' if error else ''}<form method="post"><div class="field"><label>Utente</label><input name="username" autofocus required></div><div class="field" style="margin-top:12px"><label>Password</label><input type="password" name="password" required></div><button class="btn" style="width:100%;margin-top:20px">Accedi</button></form></main>'''
        self.send_html(layout("Accesso",body))

    def login_submit(self):
        f=self.form()
        with db() as c: user=c.execute("SELECT * FROM users WHERE username=? AND active=1",(f.get("username",""),)).fetchone()
        if not user or not password_ok(f.get("password",""), user["password_hash"]): return self.login_page("Credenziali non valide.")
        token=secrets.token_urlsafe(32)
        with db() as c: c.execute("INSERT INTO sessions VALUES(?,?,?)",(token,user["id"],now()))
        self.send_response(303); self.send_header("Set-Cookie",f"ppm_session={token}; HttpOnly; SameSite=Lax; Path=/"); self.send_header("Location","/"); self.end_headers()

    def logout(self):
        jar=cookies.SimpleCookie(self.headers.get("Cookie","")); m=jar.get("ppm_session")
        if m:
            with db() as c: c.execute("DELETE FROM sessions WHERE token=?",(m.value,))
        self.send_response(303); self.send_header("Set-Cookie","ppm_session=; Max-Age=0; Path=/"); self.send_header("Location","/login"); self.end_headers()

    def dashboard(self,user):
        today=datetime.now().date(); days=[today-timedelta(days=offset) for offset in range(6,-1,-1)]
        with db() as c:
            active_where="deleted_at IS NULL OR deleted_at=''"
            counts={r["status"]:r["n"] for r in c.execute(f"SELECT status,count(*) n FROM practices WHERE {active_where} GROUP BY status")}
            payment_counts={r["payment_status"]:r["n"] for r in c.execute(f"SELECT COALESCE(payment_status,'Da saldare') payment_status,count(*) n FROM practices WHERE {active_where} GROUP BY COALESCE(payment_status,'Da saldare')")}
            payment_rows=c.execute(f"SELECT *,COALESCE(payment_status,'Da saldare') normalized_payment_status FROM practices WHERE {active_where}").fetchall()
            income_rows=c.execute(f"SELECT *,date(COALESCE(NULLIF(pickup_date,''),created_at)) day FROM practices WHERE ({active_where}) AND payment_status='Pagato' AND date(COALESCE(NULLIF(pickup_date,''),created_at))>=date(?)",(days[0].isoformat(),)).fetchall()
            recent=c.execute(f"SELECT * FROM practices WHERE {active_where} ORDER BY date(COALESCE(NULLIF(pickup_date,''),created_at)) DESC,id DESC LIMIT 10").fetchall()
            activity=c.execute("""SELECT h.event_type,h.new_value,h.created_at,p.id practice_id,p.practice_number
                                  FROM practice_history h JOIN practices p ON p.id=h.practice_id
                                  WHERE p.deleted_at IS NULL OR p.deleted_at=''
                                  ORDER BY h.created_at DESC,h.id DESC LIMIT 6""").fetchall()
            incomplete=c.execute(f"SELECT count(*) n FROM practices WHERE ({active_where}) AND data_complete=0 AND status!='Consegnata'").fetchone()["n"]
        state_cards=[]
        state_specs=[("Ritirato","Ritirati","archive","state-red"),("In programma","In programma","calendar","state-blue"),("Da consegnare","Da consegnare","clipboard","state-purple"),("Consegnato","Consegnati","home","state-green")]
        for state,label,icon,cls in state_specs:
            state_cards.append(f'<a class="metric-card {cls}" href="/archivio/pratiche?stato={quote(state)}"><span class="metric-copy"><small>{label}</small><strong>{counts.get(state,0)}</strong><em>{"Nessuna pratica" if not counts.get(state,0) else "Apri elenco"}</em></span><span class="metric-icon">{lucide(icon)}</span></a>')
        payment_totals={
            "Da saldare":sum(effective_total(row) for row in payment_rows if row["normalized_payment_status"]=="Da saldare"),
            "Acconto":sum(money_value(row["deposit"]) for row in payment_rows),
            "Pagato":sum(effective_total(row) for row in payment_rows if row["normalized_payment_status"]=="Pagato"),
        }
        payment_counts["Acconto"]=sum(1 for row in payment_rows if money_value(row["deposit"])>0)
        payment_specs=[("Da saldare","Da saldare","wallet","payment-due","/archivio/pratiche?pagamento=Da%20saldare"),("Acconto","Acconti","receipt","payment-deposit","/archivio/pratiche?con_acconto=1"),("Pagato","Pagati","chart","payment-paid","/archivio/pratiche?pagamento=Pagato")]
        payment_cards=''.join(f'<a class="payment-card {cls}" href="{href}"><span><small>{label}</small><strong>{payment_counts.get(state,0)}</strong><em>{money_it(payment_totals[state])}</em></span><span class="metric-icon">{lucide(icon)}</span></a>' for state,label,icon,cls,href in payment_specs)
        income_by_day={day.isoformat():0.0 for day in days}
        for row in income_rows:
            if row["day"] in income_by_day: income_by_day[row["day"]]+=effective_total(row)
        income_values=[income_by_day[day.isoformat()] for day in days]; income_total=sum(income_values)
        chart=income_chart(income_values,[day.strftime("%d/%m") for day in days])
        timeline=[]
        for index,event in enumerate(activity):
            label=event["event_type"] or "Aggiornamento pratica"; detail=event["new_value"] or event["practice_number"] or ""
            when=(event["created_at"] or "").replace("T"," ")[:16]
            timeline.append(f'<a class="activity-item activity-{index%4}" href="/pratiche/{event["practice_id"]}"><span class="activity-icon">{lucide("clipboard")}</span><span><b>{esc(label)}</b><small>{esc(detail)}</small></span><time>{esc(when)}</time></a>')
        if not timeline: timeline.append('<div class="activity-empty">Le nuove attività compariranno qui.</div>')
        hour=datetime.now().hour; greeting="Buongiorno" if hour < 13 else "Buon pomeriggio" if hour < 18 else "Buonasera"
        body=f'''<main class="wrap dashboard-wrap"><section class="welcome"><div><h1>{greeting}, Pet Paradise <span aria-hidden="true">👋</span></h1><p>Panoramica aggiornata dell'attività</p></div></section>{f'<div class="flash warning">{incomplete} pratiche hanno dati ancora da completare.</div>' if incomplete else ''}<h2 class="dashboard-heading">Pratiche</h2><section class="dashboard-states">{''.join(state_cards)}</section><h2 class="dashboard-heading">Pagamenti</h2><section class="dashboard-payments">{payment_cards}</section><section class="dashboard-lower"><a class="dashboard-panel income-panel" href="/bilanci" aria-label="Apri Bilanci: entrate degli ultimi sette giorni"><header><div><h2>Entrate ultimi 7 giorni</h2><p>Totale: <strong>{money_it(income_total)}</strong></p></div><span class="panel-link">Apri Bilanci →</span></header>{chart}</a><article class="dashboard-panel activity-panel"><header><h2>Attività recenti</h2><a href="/archivio/pratiche">Vedi tutte</a></header><div class="activity-list">{''.join(timeline)}</div></article></section><section class="dashboard-recent"><div class="titlebar"><h2>Ultime 10 pratiche per data recupero</h2><a href="/archivio/pratiche">Apri archivio</a></div><div class="tablebox"><table><thead><tr><th>Data recupero</th><th>Codice pratica</th><th>Animale</th><th>Proprietario</th><th>Veterinario</th><th>Sede</th><th>Etichetta</th><th>Note</th><th>Urna</th><th>Totale calcolato</th><th>TOTALE D</th><th>Acconto</th><th>Rimanenza</th><th>Stato</th></tr></thead><tbody>{self.practice_rows(recent,True)}</tbody></table></div></section></main>'''
        self.send_html(layout("Dashboard",body,user))

    def balances(self,user):
        q=parse_qs(urlparse(self.path).query); today=datetime.now().date()
        date_from=(q.get("dal") or [(today-timedelta(days=6)).isoformat()])[0].strip()
        date_to=(q.get("al") or [today.isoformat()])[0].strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from): date_from=(today-timedelta(days=6)).isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to): date_to=today.isoformat()
        categories=[
            ("price_cremation","Cremazione",("price_cremation",)),("price_pickup","Ritiro",("price_pickup",)),("price_urn","Urna",("price_urn","price_urn_2")),
            ("price_delivery","Riconsegna",("price_delivery",)),("price_cast","Calco",("price_cast","price_cast_2")),("price_evening","Serale",("price_evening",)),
            ("price_night","Notturno",("price_night",)),("price_holiday","Festivo",("price_holiday",)),("price_accessories","Accessori",("price_accessories","price_accessories_2")),
            ("da_entrare","Da entrare",()),
        ]
        category_map={key:label for key,label,_ in categories}; category_fields={key:fields for key,_,fields in categories}; selected=(q.get("voce") or [""])[0].strip()
        if selected not in category_map: selected=""
        with db() as c:
            rows=c.execute("""SELECT * FROM practices
                              WHERE (deleted_at IS NULL OR deleted_at='')
                                AND date(COALESCE(NULLIF(pickup_date,''),created_at)) BETWEEN date(?) AND date(?)
                              ORDER BY date(COALESCE(NULLIF(pickup_date,''),created_at)) DESC,id DESC""",(date_from,date_to)).fetchall()
        breakdown={key:sum(sum(money_value(row[field]) for field in fields) for row in rows) for key,_,fields in categories if fields}
        breakdown["da_entrare"]=sum(effective_total(row) for row in rows if (row["payment_status"] or "Da saldare")=="Da saldare")
        grand_total=sum(effective_total(row) for row in rows if row["payment_status"]=="Pagato")
        shown_total=breakdown[selected] if selected else grand_total
        cards=''.join(f'<a class="balance-card {"active" if selected==key else ""}" href="/bilanci?dal={quote(date_from)}&al={quote(date_to)}&voce={quote(key)}"><small>{label}</small><strong>{money_it(breakdown[key])}</strong></a>' for key,label,_ in categories)
        table_rows=[]
        for row in rows:
            if selected=="da_entrare":
                if (row["payment_status"] or "Da saldare")!="Da saldare": continue
                amount=effective_total(row)
            elif selected:
                amount=sum(money_value(row[field]) for field in category_fields[selected])
                if amount==0: continue
            else:
                if row["payment_status"]!="Pagato": continue
                amount=effective_total(row)
            owner=((row["owner_first_name"] or "")+" "+(row["owner_last_name"] or "")).strip()
            effective_date=(row["pickup_date"] or row["created_at"] or "")[:10]
            table_rows.append(f'<tr><td>{esc(date_it(effective_date))}</td><td><a href="/pratiche/{row["id"]}"><b>{esc(row["practice_number"])}</b></a></td><td>{esc(owner)}</td><td>{esc(category_map.get(selected,"Totale pratica"))}</td><td><b>{money_it(amount)}</b></td></tr>')
        table_body=''.join(table_rows) or '<tr><td colspan="5" class="sub">Nessuna entrata nel periodo selezionato.</td></tr>'
        options='<option value="">Entrate pagate</option>'+''.join(f'<option value="{key}" {"selected" if selected==key else ""}>{label}</option>' for key,label,_ in categories)
        subtitle="Pratiche contenenti la voce selezionata" if selected else "Entrate delle pratiche pagate"
        body=f'''<main class="wrap balances-wrap"><div class="titlebar"><div><h1>Bilanci</h1><p class="sub">{subtitle} dal {esc(date_it(date_from))} al {esc(date_it(date_to))}</p></div><div class="balance-total"><small>{esc(category_map.get(selected,"Entrate totali"))}</small><strong>{money_it(shown_total)}</strong></div></div><section class="balance-grid">{cards}</section><section class="tablebox balance-table"><table><thead><tr><th>Data</th><th>Pratica</th><th>Cliente</th><th>Voce</th><th>Importo</th></tr></thead><tbody>{table_body}</tbody></table></section><section class="search-after-results"><h2>Filtra bilanci</h2><form class="section" method="get"><div class="fields"><div class="field"><label>Dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field full"><label>Voce</label><select name="voce">{options}</select></div></div><button class="btn" style="margin-top:12px">Applica filtri</button><a class="btn ghost" style="margin-top:12px" href="/bilanci">Ultimi 7 giorni</a></form></section></main>'''
        self.send_html(layout("Bilanci",body,user))

    def whatsapp_conversations(self,user):
        q=parse_qs(urlparse(self.path).query)
        term=(q.get("q") or [""])[0].strip(); date_from=(q.get("dal") or [""])[0].strip(); date_to=(q.get("al") or [""])[0].strip()
        message_status=(q.get("stato_messaggio") or [""])[0].strip(); practice_status=(q.get("stato_pratica") or [""])[0].strip()
        allowed_message_statuses=["accettato_da_meta","consegnato","letto","fallito"]
        if message_status not in allowed_message_statuses: message_status=""
        if practice_status not in STATES: practice_status=""
        try: page=max(1,int((q.get("pagina") or ["1"])[0]))
        except ValueError: page=1
        per_page=20
        event_date="COALESCE(NULLIF(wm.sent_at,''),NULLIF(wm.last_attempt_at,''),NULLIF(wm.scheduled_at,''),wm.created_at)"
        where=["wm.manual=0","(wm.sent_at IS NOT NULL OR wm.status IN ('accettato_da_meta','consegnato','letto','fallito'))"]
        args=[]
        if term:
            like=f"%{term}%"; where.append("(COALESCE(p.owner_first_name,'')||' '||COALESCE(p.owner_last_name,'') LIKE ? OR COALESCE(p.owner_company,'') LIKE ? OR COALESCE(p.animal_name,'') LIKE ? OR COALESCE(wm.recipient_phone,'') LIKE ?)"); args.extend([like]*4)
        if date_from and re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from): where.append(f"date({event_date})>=date(?)"); args.append(date_from)
        else: date_from=""
        if date_to and re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to): where.append(f"date({event_date})<=date(?)"); args.append(date_to)
        else: date_to=""
        if message_status: where.append("wm.status=?"); args.append(message_status)
        if practice_status: where.append("p.status=?"); args.append(practice_status)
        where_sql=" AND ".join(where)
        with db() as c:
            total=c.execute(f"SELECT count(*) n FROM whatsapp_messages wm JOIN practices p ON p.id=wm.practice_id WHERE {where_sql}",args).fetchone()["n"]
            pages=max(1,(total+per_page-1)//per_page); page=min(page,pages); offset=(page-1)*per_page
            rows=c.execute(f"""SELECT wm.*,p.practice_number,p.owner_first_name,p.owner_last_name,p.owner_company,p.owner_phone,p.animal_name,p.status practice_status,{event_date} event_at
                               FROM whatsapp_messages wm JOIN practices p ON p.id=wm.practice_id
                               WHERE {where_sql} ORDER BY event_at DESC,wm.id DESC LIMIT ? OFFSET ?""",args+[per_page,offset]).fetchall()
        status_labels={"accettato_da_meta":"Inviato","consegnato":"Consegnato","letto":"Letto","fallito":"Fallito"}
        cards=[]
        for row in rows:
            client=" ".join(x for x in [row["owner_first_name"],row["owner_last_name"]] if x).strip() or row["owner_company"] or "Cliente non indicato"
            phone=only_digits(row["recipient_phone"] or row["owner_phone"]); status=status_labels.get(row["status"],self.whatsapp_status_label(row["status"]))
            last_message=(f'Errore: {compact_text(row["last_error"])}' if row["status"]=="fallito" and row["last_error"] else f'Ringraziamento automatico · {row["template_name"]}' if row["template_name"] else "Ringraziamento automatico")
            whatsapp_action=f'<a class="btn whatsapp-open" href="https://wa.me/{phone}" target="_blank" rel="noopener noreferrer">Apri chat WhatsApp</a>' if phone else '<span class="sub">Numero non disponibile</span>'
            cards.append(f'''<article class="conversation-card"><div class="conversation-main"><div class="conversation-avatar">{lucide("message")}</div><div><h2>{esc(client)}</h2><p><b>{esc(row["animal_name"] or "Animale non indicato")}</b> · pratica <a href="/pratiche/{row["practice_id"]}">{esc(row["practice_number"])}</a></p><p class="conversation-message">{esc(last_message[:180])}</p></div></div><dl><div><dt>WhatsApp</dt><dd>{('+'+esc(phone)) if phone else '-'}</dd></div><div><dt>Inviato</dt><dd>{esc((row["event_at"] or "").replace("T"," ")[:16])}</dd></div><div><dt>Pratica</dt><dd><span class="badge">{esc(row["practice_status"])}</span></dd></div><div><dt>Messaggio</dt><dd><span class="badge message-{esc(row["status"])}">{esc(status)}</span></dd></div></dl><div class="conversation-action">{whatsapp_action}</div></article>''')
        results=''.join(cards) or '<section class="section empty-state">Nessuna conversazione trovata.</section>'
        def page_link(number,label,disabled=False):
            if disabled: return f'<span class="page-disabled">{label}</span>'
            params={"q":term,"dal":date_from,"al":date_to,"stato_messaggio":message_status,"stato_pratica":practice_status,"pagina":number}
            return f'<a href="/conversazioni-whatsapp?{urlencode({k:v for k,v in params.items() if v not in (None,"")})}">{label}</a>'
        pagination=f'<nav class="pagination" aria-label="Paginazione">{page_link(page-1,"← Precedente",page<=1)}<span>Pagina {page} di {pages} · {total} conversazioni</span>{page_link(page+1,"Successiva →",page>=pages)}</nav>'
        message_options='<option value="">Tutti gli stati messaggio</option>'+''.join(f'<option value="{key}" {"selected" if message_status==key else ""}>{label}</option>' for key,label in status_labels.items())
        practice_options='<option value="">Tutti gli stati pratica</option>'+''.join(f'<option {"selected" if practice_status==state else ""}>{esc(state)}</option>' for state in STATES)
        filters=f'''<section class="search-after-results"><h2>Ricerca e filtri</h2><form class="section" method="get"><div class="fields"><div class="field full"><label>Cliente, animale o numero WhatsApp</label><input name="q" value="{esc(term)}" placeholder="Cerca conversazione"></div><div class="field"><label>Dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field"><label>Stato messaggio</label><select name="stato_messaggio">{message_options}</select></div><div class="field"><label>Stato pratica</label><select name="stato_pratica">{practice_options}</select></div></div><button class="btn" style="margin-top:12px">Applica filtri</button><a class="btn ghost" style="margin-top:12px" href="/conversazioni-whatsapp">Pulisci filtri</a></form></section>'''
        body=f'''<main class="wrap conversations-wrap"><div class="titlebar"><div><h1>Conversazioni WhatsApp</h1><p class="sub">Storico dei messaggi automatici di ringraziamento, dal più recente.</p></div></div><section class="conversation-list">{results}</section>{pagination}{filters}</main>'''
        self.send_html(layout("Conversazioni WhatsApp",body,user))

    def diagnostics(self,user):
        asset_rows = []
        for name in ("DCS_NUOVO.pdf", "DCS_LIVORNO.pdf", "DCS_EMPOLI.pdf"):
            path = ASSETS / name
            status = "OK" if path.exists() else "MANCANTE"
            size = f"{path.stat().st_size} byte" if path.exists() else "-"
            asset_rows.append(f"<tr><td>{esc(name)}</td><td>{status}</td><td>{size}</td></tr>")
        data_ok = DATA.exists()
        ddt_ok = DDT_DIR.exists()
        writable = os.access(DATA, os.W_OK) if data_ok else False
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Diagnostica</h1><div class="sub">Controllo rapido per PDF e cartelle online.</div></div></div><section class="section"><h2>Modelli PDF</h2><div class="tablebox"><table><thead><tr><th>File</th><th>Stato</th><th>Dimensione</th></tr></thead><tbody>{''.join(asset_rows)}</tbody></table></div></section><section class="section" style="margin-top:16px"><h2>Cartelle dati</h2><p><b>Assets:</b> {esc(ASSETS)}</p><p><b>DATA:</b> {esc(DATA)} - {'OK' if data_ok else 'MANCANTE'} - scrittura {'OK' if writable else 'NO'}</p><p><b>DDT:</b> {esc(DDT_DIR)} - {'OK' if ddt_ok else 'MANCANTE'}</p></section></main>'''
        self.send_html(layout("Diagnostica",body,user))

    def tag_badges(self,r):
        tags = [
            ("tag_assistita", "ASSISTITA", "tag-red"),
            ("tag_possibile_assistita", "POSSIBILE ASSISTITA", "tag-red"),
            ("tag_assistita_streaming", "ASSISTITA STREAMING", "tag-orange"),
            ("tag_saluto", "SALUTO", "tag-purple"),
            ("tag_calco", "CALCO", "tag-yellow"),
            ("tag_avvisare", "AVVISARE", "tag-pink"),
            ("tag_da_richiamare", "DA RICHIAMARE", "tag-blue"),
            ("send_catalog", "INVIARE CATALOGO", "tag-outline-orange"),
            ("send_estremi", "INVIARE ESTREMI", "tag-outline-orange"),
        ]
        html_badges = ''.join(f'<span class="badge {cls}">{label}</span> ' for key,label,cls in tags if key in r.keys() and r[key])
        return html_badges or '<span class="sub">-</span>'

    def status_badges(self,r):
        payment = r["payment_status"] if "payment_status" in r.keys() and r["payment_status"] else "Da saldare"
        pay_cls = {"Da saldare":"pay-yellow","Acconto":"pay-blue","Pagato":"pay-green"}.get(payment,"")
        invoice = f'<small>Fatt. {esc(r["invoice_number"])}</small>' if "invoice_number" in r.keys() and r["invoice_number"] else ""
        return f'<div class="status-stack"><span class="badge">{esc(r["status"])}</span><span class="badge {pay_cls}">{esc(payment)}</span>{invoice}</div>'

    def practice_rows(self,rows,show_financials=False):
        if not rows:return f'<tr><td colspan="{14 if show_financials else 10}" class="sub">Nessuna pratica presente.</td></tr>'
        html=[]
        for r in rows:
            code=str(r['practice_number'] or '')
            code_cls='practice-code-cr' if code.startswith('CR-') else 'practice-code-sm' if code.startswith('SM-') else ''
            if (r['service_type'] or '') == 'Cremazione collettiva':
                animal_cell='/'
            else:
                animal_meta=esc(r['species']) + ((' - '+esc(r['estimated_weight'])+' kg') if r['estimated_weight'] else '')
                animal_cell=f'{esc(r["animal_name"] or "Da inserire")}<br><small>{animal_meta}</small>'
            owner=esc((r['owner_first_name'] or '')+' '+(r['owner_last_name'] or ''))
            vet_label=esc(r['clinic_name'] if 'clinic_name' in r.keys() and r['clinic_name'] else '-')
            recovery_date=date_it(r['pickup_date'] if 'pickup_date' in r.keys() and r['pickup_date'] else r['created_at'])
            notes_preview=compact_text(r["notes"]) if "notes" in r.keys() else ""
            notes_cell=esc(notes_preview[:70])+("..." if len(notes_preview)>70 else "") if notes_preview else '<span class="sub">-</span>'
            urn_notes=compact_text(r["urn_notes"]) if "urn_notes" in r.keys() else ""
            urn_prices=[compact_text(r[key]) for key in ("price_urn","price_urn_2") if key in r.keys() and r[key]]
            urn_price=" + ".join(urn_prices)
            urn_cell='<br>'.join(x for x in [esc(urn_notes), f'<small>{esc(urn_price)} €</small>' if urn_price else ''] if x) or '<span class="sub">-</span>'
            financial_cells=''
            if show_financials:
                total_d=(r["total_text"] or "").strip() if "total_text" in r.keys() else ""
                financial_cells=f'<td>{money_it(money_value(r["total_service"]))}</td><td>{money_it(money_value(total_d)) if total_d else "-"}</td><td>{money_it(money_value(r["deposit"]))}</td><td>{money_it(money_value(r["remaining_balance"]))}</td>'
            html.append(f'<tr><td>{esc(recovery_date)}</td><td><a href="/pratiche/{r["id"]}"><b class="{code_cls}">{esc(code)}</b></a></td><td>{animal_cell}</td><td>{owner}<br><small>{esc(r["owner_phone"])}</small></td><td>{vet_label}</td><td>{esc(r["destination_branch"])}</td><td>{self.tag_badges(r)}</td><td>{notes_cell}</td><td>{urn_cell}</td>{financial_cells}<td>{self.status_badges(r)}</td></tr>')
        return ''.join(html)

    def archive_home(self,user):
        body='''<main class="wrap"><div class="titlebar"><div><h1>ARCHIVIO</h1><div class="sub">Scegli cosa vuoi consultare.</div></div></div><section class="grid stats"><a class="card stat" href="/archivio/pratiche"><span>Pratiche</span><b>-</b></a><a class="card stat" href="/archivio/clienti"><span>Anagrafica clienti</span><b>-</b></a><a class="card stat" href="/cestino"><span>Cestino</span><b>-</b></a></section></main>'''
        self.send_html(layout("Archivio",body,user))

    def clients_archive(self,user):
        q=parse_qs(urlparse(self.path).query)
        term=q.get("q",[""])[0].strip()
        sql="SELECT owner_first_name, owner_last_name, owner_phone, owner_phone_2, owner_email, owner_tax_code, owner_address, owner_city, owner_province, owner_zip, COUNT(*) n, MAX(created_at) last_date FROM practices WHERE (deleted_at IS NULL OR deleted_at='') AND COALESCE(owner_first_name,'')||COALESCE(owner_last_name,'')||COALESCE(owner_phone,'')<>''"
        args=[]
        if term:
            like=f"%{term}%"
            sql+=" AND (owner_first_name LIKE ? OR owner_last_name LIKE ? OR owner_first_name||' '||owner_last_name LIKE ? OR owner_phone LIKE ? OR owner_phone_2 LIKE ? OR owner_email LIKE ? OR owner_tax_code LIKE ? OR owner_address LIKE ? OR owner_city LIKE ?)"
            args=[like]*9
        sql+=" GROUP BY owner_first_name, owner_last_name, owner_phone ORDER BY last_date DESC"
        with db() as c:
            rows=c.execute(sql,args).fetchall()
        body_rows=''.join(f'''<tr><td>{esc((r['owner_first_name'] or '')+' '+(r['owner_last_name'] or ''))}</td><td>{esc(r['owner_phone'])}<br><small>{esc(r['owner_phone_2'])}</small></td><td>{esc(r['owner_email'])}</td><td>{esc(r['owner_tax_code'])}</td><td>{esc(r['owner_address'])}</td><td>{r['n']}</td><td>{esc((r['last_date'] or '')[:10])}</td></tr>''' for r in rows) or '<tr><td colspan="7" class="sub">Nessun cliente trovato.</td></tr>'
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Anagrafica clienti</h1><div class="sub">{len(rows)} risultati</div></div><a class="btn ghost" href="/pratiche">Archivio</a></div><form class="section" method="get" style="margin-bottom:18px"><div class="fields"><div class="field full"><label>Ricerca cliente</label><input name="q" value="{esc(term)}" placeholder="Nome, telefono, email, codice fiscale, indirizzo"></div></div><button class="btn" style="margin-top:12px">Cerca</button><a class="btn ghost" style="margin-top:12px" href="/archivio/clienti">Pulisci</a></form><div class="tablebox"><table><thead><tr><th>Cliente</th><th>Telefono</th><th>Email</th><th>Codice fiscale</th><th>Indirizzo</th><th>Pratiche</th><th>Ultima pratica</th></tr></thead><tbody>{body_rows}</tbody></table></div></main>'''
        self.send_html(layout("Anagrafica clienti",body,user))

    def api_clients_search(self,user):
        q=(parse_qs(urlparse(self.path).query).get("q",[""])[0] or "").strip()
        if len(q) < 2:
            return self.send_json({"ok":True,"query":q,"too_short":True,"results":[]})
        tokens=[t for t in re.split(r"\s+", q) if len(t) >= 2][:5]
        if not tokens:
            return self.send_json({"ok":True,"query":q,"too_short":True,"results":[]})
        searchable="COALESCE(first_name,'')||' '||COALESCE(last_name,'')||' '||COALESCE(company_name,'')||' '||COALESCE(phone,'')||' '||COALESCE(phone_2,'')||' '||COALESCE(email,'')||' '||COALESCE(tax_code,'')||' '||COALESCE(vat_number,'')||' '||COALESCE(street,'')||' '||COALESCE(city,'')||' '||COALESCE(address,'')"
        where=[]; args=[]
        for token in tokens:
            where.append(f"{searchable} LIKE ? COLLATE NOCASE")
            args.append(f"%{token}%")
        digits=only_digits(q)
        try:
            with db() as c:
                rows=c.execute(f"""SELECT id, first_name, last_name, company_name, phone, phone_2, email, tax_code, vat_number, street, city, province, zip, address, notes,
                                           (SELECT COUNT(*) FROM practices p WHERE p.client_id=clients.id) AS practice_count,
                                           (SELECT MAX(created_at) FROM practices p WHERE p.client_id=clients.id) AS last_practice
                                    FROM clients
                                    WHERE {' AND '.join(where)}
                                    ORDER BY CASE
                                        WHEN UPPER(COALESCE(first_name,'')||' '||COALESCE(last_name,''))=UPPER(?) THEN 0
                                        WHEN ?<>'' AND REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(phone,''),' ',''),'+',''),'-',''),'.','') LIKE ? THEN 1
                                        WHEN email LIKE ? COLLATE NOCASE THEN 2
                                        WHEN tax_code LIKE ? COLLATE NOCASE THEN 3
                                        ELSE 9 END,
                                        COALESCE(last_practice, updated_at) DESC
                                    LIMIT 15""", args+[q,digits,f"%{digits}%",f"%{q}%",f"%{q}%"]).fetchall()
            results=[]
            for r in rows:
                display=" ".join(x for x in [r["first_name"], r["last_name"]] if x).strip() or r["company_name"] or "Cliente senza nome"
                subtitle=" - ".join(x for x in [r["company_name"], r["phone"], r["email"], r["city"] or r["address"]] if x)
                results.append({"id":r["id"],"first_name":r["first_name"] or "","last_name":r["last_name"] or "","company_name":r["company_name"] or "","phone":r["phone"] or "","phone_2":r["phone_2"] or "","email":r["email"] or "","tax_code":r["tax_code"] or "","vat_number":r["vat_number"] or "","street":r["street"] or "","city":r["city"] or "","province":r["province"] or "","zip":r["zip"] or "","address":r["address"] or "","notes":r["notes"] or "","display":display,"subtitle":subtitle,"practice_count":r["practice_count"] or 0,"last_practice":(r["last_practice"] or "")[:10]})
            return self.send_json({"ok":True,"query":q,"results":results})
        except Exception as exc:
            print(f"[CLIENT_SEARCH] errore tipo={type(exc).__name__} lunghezza_query={len(q)}", flush=True)
            return self.send_json({"ok":False,"error":"Errore durante la ricerca clienti"},500)

    def api_zip_lookup(self,user):
        q=parse_qs(urlparse(self.path).query)
        address=(q.get("indirizzo",[""])[0] or "").strip()
        city=(q.get("comune",[""])[0] or "").strip()
        province=(q.get("provincia",[""])[0] or "").strip().upper()
        if not address or not city or not re.fullmatch(r"[A-Z]{2}", province):
            return self.send_json({"ok":True,"zip":""})
        params=urlencode({"street":address,"city":city,"county":province,"country":"Italia","countrycodes":"it","format":"jsonv2","addressdetails":"1","limit":"1"})
        req=urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{params}",headers={"Accept":"application/json","User-Agent":"PetParadiseManager/1.0 (CAP lookup)"},method="GET")
        try:
            with urllib.request.urlopen(req,timeout=8) as response:
                payload=json.loads(response.read().decode("utf-8","replace"))
            postcode=((payload[0].get("address") or {}).get("postcode") if payload else "") or ""
            match=re.search(r"\b(\d{5})\b", postcode)
            return self.send_json({"ok":True,"zip":match.group(1) if match else ""})
        except Exception as exc:
            print(f"[CAP_LOOKUP] {type(exc).__name__}: {exc}",flush=True)
            return self.send_json({"ok":True,"zip":""})

    def api_veterinarians_search(self,user):
        q=(parse_qs(urlparse(self.path).query).get("q",[""])[0] or "").strip()
        if len(q) < 2:
            return self.send_json({"ok":True,"query":q,"too_short":True,"results":[]})
        tokens=[t for t in re.split(r"\s+", q) if len(t) >= 2][:5]
        if not tokens:
            return self.send_json({"ok":True,"query":q,"too_short":True,"results":[]})
        searchable="COALESCE(short_name,'')||' '||COALESCE(clinic_name,'')||' '||COALESCE(doctor_name,'')||' '||COALESCE(phone,'')||' '||COALESCE(address,'')||' '||COALESCE(city,'')"
        where=[]; args=[]
        for token in tokens:
            where.append(f"{searchable} LIKE ? COLLATE NOCASE")
            args.append(f"%{token}%")
        try:
            with db() as c:
                rows=c.execute(f"""SELECT id, short_name, clinic_name, doctor_name, phone, address, city
                                   FROM veterinarians
                                   WHERE active=1 AND {' AND '.join(where)}
                                   ORDER BY CASE WHEN short_name LIKE ? COLLATE NOCASE THEN 0 WHEN clinic_name LIKE ? COLLATE NOCASE THEN 1 ELSE 9 END,
                                            COALESCE(short_name, clinic_name), clinic_name
                                   LIMIT 15""", args+[f"{q}%", f"%{q}%"]).fetchall()
            results=[{"id":r["id"],"short_name":r["short_name"] or "","clinic_name":r["clinic_name"] or "","doctor_name":r["doctor_name"] or "","phone":r["phone"] or "","address":r["address"] or "","city":r["city"] or "","display":r["short_name"] or r["clinic_name"] or "Veterinario","subtitle":" - ".join(x for x in [r["clinic_name"], r["address"], r["city"]] if x)} for r in rows]
            return self.send_json({"ok":True,"query":q,"results":results})
        except Exception as exc:
            print(f"[VET_SEARCH] errore tipo={type(exc).__name__} lunghezza_query={len(q)}", flush=True)
            return self.send_json({"ok":False,"error":"Errore durante la ricerca veterinari"},500)

    def api_veterinarian_vouchers(self,user,vet_id):
        try:
            with db() as c:
                vet=c.execute("SELECT id FROM veterinarians WHERE id=? AND active=1",(vet_id,)).fetchone()
                if not vet:
                    return self.send_json({"ok":False,"error":"Veterinario non trovato"},404)
                rows=c.execute("""SELECT vv.id, vv.created_at, vv.note, p.animal_name, p.species, p.practice_number
                                  FROM veterinarian_vouchers vv
                                  LEFT JOIN practices p ON p.id=vv.practice_id
                                  WHERE vv.veterinarian_id=? AND vv.status='Maturato'
                                  ORDER BY vv.created_at ASC
                                  LIMIT 50""",(vet_id,)).fetchall()
            results=[]
            for r in rows:
                animal=(r["animal_name"] or (r["note"] or "").replace("Manuale:","").strip() or "Buono senza animale").strip()
                results.append({"id":r["id"],"created_at":(r["created_at"] or "")[:10],"animal":animal,"species":r["species"] or "","practice_number":r["practice_number"] or ""})
            return self.send_json({"ok":True,"results":results})
        except Exception as exc:
            print(f"[VOUCHER_SEARCH] errore tipo={type(exc).__name__} vet_id={vet_id}", flush=True)
            return self.send_json({"ok":False,"error":"Errore durante la lettura buoni"},500)

    def archive(self,user):
        q=parse_qs(urlparse(self.path).query)
        term=q.get("q",[""])[0].strip()
        animal=q.get("animale",[""])[0].strip()
        service=q.get("servizio",[""])[0].strip()
        vet=q.get("veterinario",[""])[0].strip()
        collaborator=q.get("collaboratore",[""])[0].strip()
        spesa_min=q.get("spesa_min",[""])[0].strip().replace(",",".")
        spesa_max=q.get("spesa_max",[""])[0].strip().replace(",",".")
        date_from=q.get("dal",[""])[0].strip()
        date_to=q.get("al",[""])[0].strip()
        state=q.get("stato",[""])[0].strip()
        payment=q.get("pagamento",[""])[0].strip()
        with_deposit=q.get("con_acconto",[""])[0].strip()=="1"
        promemoria=q.get("promemoria",[""])[0].strip()
        selected_month=q.get("mese",[""])[0].strip()
        sql="SELECT * FROM practices WHERE (deleted_at IS NULL OR deleted_at='')"; args=[]
        if term:
            like=f"%{term}%"
            sql+=" AND (practice_number LIKE ? OR animal_name LIKE ? OR owner_first_name||' '||owner_last_name LIKE ? OR owner_phone LIKE ? OR owner_phone_2 LIKE ? OR microchip LIKE ? OR clinic_name LIKE ? OR veterinarian_name LIKE ? OR collaborator_name LIKE ? OR CAST(ddt_number AS TEXT) LIKE ?)"
            args += [like]*10
        if animal:
            sql += " AND animal_name LIKE ?"; args.append(f"%{animal}%")
        if service:
            sql += " AND service_type=?"; args.append(service)
        if vet:
            like=f"%{vet}%"; sql += " AND (clinic_name LIKE ? OR veterinarian_name LIKE ?)"; args += [like,like]
        if collaborator:
            sql += " AND collaborator_name LIKE ?"; args.append(f"%{collaborator}%")
        if spesa_min:
            sql += " AND CAST(REPLACE(COALESCE(NULLIF(total_text,''),total_service), ',', '.') AS REAL) >= ?"; args.append(float(spesa_min) if re.match(r"^-?\d+(\.\d+)?$", spesa_min) else 0)
        if spesa_max:
            sql += " AND CAST(REPLACE(COALESCE(NULLIF(total_text,''),total_service), ',', '.') AS REAL) <= ?"; args.append(float(spesa_max) if re.match(r"^-?\d+(\.\d+)?$", spesa_max) else 999999999)
        if date_from:
            sql += " AND date(COALESCE(NULLIF(pickup_date,''), created_at))>=date(?)"; args.append(date_from)
        if date_to:
            sql += " AND date(COALESCE(NULLIF(pickup_date,''), created_at))<=date(?)"; args.append(date_to)
        if state:
            sql += " AND status=?"; args.append(state)
        if payment:
            sql += " AND COALESCE(payment_status,'Da saldare')=?"; args.append(payment)
        if with_deposit:
            sql += " AND CAST(REPLACE(COALESCE(NULLIF(deposit,''),'0'), ',', '.') AS REAL)>0"
        if promemoria == "catalogo":
            sql += " AND send_catalog='Si' AND status!='Consegnato'"
        if promemoria == "estremi":
            sql += " AND send_estremi='Si' AND status!='Consegnato'"
        sql += " ORDER BY date(COALESCE(NULLIF(pickup_date,''), created_at)) DESC, id DESC"
        with db() as c:
            rows=c.execute(sql,args).fetchall()
        available_months=sorted({((row["pickup_date"] or row["created_at"] or "")[:7]) or "Senza data" for row in rows},reverse=True)
        if selected_month not in available_months: selected_month=available_months[0] if available_months else ""
        visible_rows=[row for row in rows if (((row["pickup_date"] or row["created_at"] or "")[:7]) or "Senza data")==selected_month]
        opts='<option value="">Tutti gli stati</option>'+''.join(f'<option {"selected" if state==s else ""}>{esc(s)}</option>' for s in STATES)
        pay_opts='<option value="">Tutti i pagamenti</option>'+''.join(f'<option {"selected" if payment==s else ""}>{esc(s)}</option>' for s in PAYMENT_STATES)
        service_opts=''.join(f'<option value="{esc(x)}" {"selected" if service==x else ""}>{esc(x or "Tutti i servizi")}</option>' for x in ["","Da decidere","Cremazione singola","Cremazione collettiva"])
        promemoria_label = " - Pratiche con acconto" if with_deposit else " - Promemoria catalogo" if promemoria=="catalogo" else " - Promemoria estremi" if promemoria=="estremi" else ""
        month_names=["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno","Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
        groups={}
        for r in visible_rows:
            key=((r["pickup_date"] or r["created_at"] or "")[:7]) or "Senza data"
            groups.setdefault(key,[]).append(r)
        blocks=[]
        for key,items in groups.items():
            title=key
            if key != "Senza data":
                try:
                    y,m=key.split("-"); title=f"{month_names[int(m)]} {y}"
                except Exception:
                    pass
            blocks.append(f'''<section class="month-block"><div class="month-title"><h2>{esc(title)}</h2><span class="badge">{len(items)} pratiche</span></div><div class="tablebox"><table><thead><tr><th>Data recupero</th><th>Codice pratica</th><th>Animale</th><th>Proprietario</th><th>Veterinario</th><th>Sede</th><th>Etichetta</th><th>Note</th><th>Urna</th><th>Stato</th></tr></thead><tbody>{self.practice_rows(items)}</tbody></table></div></section>''')
        results_html=''.join(blocks) if blocks else '<section class="section"><p class="sub">Nessuna pratica trovata.</p></section>'
        if selected_month in available_months and available_months.index(selected_month)+1<len(available_months):
            previous_month=available_months[available_months.index(selected_month)+1]
            month_params={"q":term,"animale":animal,"servizio":service,"veterinario":vet,"collaboratore":collaborator,"spesa_min":spesa_min,"spesa_max":spesa_max,"dal":date_from,"al":date_to,"stato":state,"pagamento":payment,"con_acconto":"1" if with_deposit else "","promemoria":promemoria,"mese":previous_month}
            previous_label=previous_month
            if previous_month!="Senza data":
                y,m=previous_month.split("-"); previous_label=f"{month_names[int(m)]} {y}"
            results_html+=f'<div class="load-previous-month"><a class="btn ghost" href="/archivio/pratiche?{urlencode({k:v for k,v in month_params.items() if v})}">Apri {esc(previous_label)}</a></div>'
        filters_html=f'''<section class="search-after-results"><h2>Ricerca e filtri</h2><form class="section" method="get"><div class="fields"><div class="field"><label>Ricerca generale</label><input name="q" value="{esc(term)}" placeholder="Proprietario, telefono, microchip, pratica, DDT"></div><div class="field"><label>Nome animale</label><input name="animale" value="{esc(animal)}"></div><div class="field"><label>Tipo cremazione</label><select name="servizio">{service_opts}</select></div><div class="field"><label>Veterinario</label><input name="veterinario" value="{esc(vet)}" placeholder="Clinica o medico"></div><div class="field"><label>Collaboratore</label><input name="collaboratore" value="{esc(collaborator)}"></div><div class="field"><label>Spesa minima</label><input type="number" min="0" step="0.01" name="spesa_min" value="{esc(spesa_min)}" inputmode="decimal" placeholder="Es. 100"></div><div class="field"><label>Spesa massima</label><input type="number" min="0" step="0.01" name="spesa_max" value="{esc(spesa_max)}" inputmode="decimal" placeholder="Es. 350"></div><div class="field"><label>Periodo dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Periodo al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field"><label>Stato pratica</label><select name="stato">{opts}</select></div><div class="field"><label>Pagamento</label><select name="pagamento">{pay_opts}</select></div></div><button class="btn" style="margin-top:12px">Cerca</button><a class="btn ghost" style="margin-top:12px" href="/archivio/pratiche">Pulisci filtri</a></form></section>'''
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>ARCHIVIO</h1><div class="sub">{len(visible_rows)} pratiche nel mese visualizzato{promemoria_label}</div></div></div>{results_html}{filters_html}</main>'''
        body=body.replace('<label>Servizio</label><select name="servizio">','<label>Tipo cremazione</label><select name="servizio">')
        self.send_html(layout("Archivio",body,user))

    def veterinarians_page(self,user):
        q=parse_qs(urlparse(self.path).query)
        term=q.get("q",[""])[0].strip()
        voucher_filter=q.get("buoni",[""])[0]
        with db() as c:
            sql=("SELECT v.*, "
                 "COALESCE(SUM(CASE WHEN vv.status='Maturato' THEN 1 ELSE 0 END),0) available_vouchers, "
                 "COALESCE(SUM(CASE WHEN vv.status='Usato' THEN 1 ELSE 0 END),0) used_vouchers, "
                 "COUNT(vv.id) total_vouchers "
                 "FROM veterinarians v LEFT JOIN veterinarian_vouchers vv ON vv.veterinarian_id=v.id WHERE v.active=1")
            args=[]
            if term:
                like=f"%{term}%"
                sql += " AND (v.short_name LIKE ? OR v.clinic_name LIKE ? OR v.doctor_name LIKE ? OR v.address LIKE ? OR v.city LIKE ? OR v.phone LIKE ?)"
                args += [like]*6
            sql += " GROUP BY v.id"
            if voucher_filter == "Maturati": sql += " HAVING available_vouchers>0"
            elif voucher_filter == "Usati": sql += " HAVING used_vouchers>0"
            elif voucher_filter == "Senza buoni": sql += " HAVING available_vouchers=0"
            sql += " ORDER BY v.city, v.short_name, v.clinic_name"
            vets=c.execute(sql,args).fetchall()
        rows=[]
        for v in vets:
            available=int(v["available_vouchers"] or 0); used=int(v["used_vouchers"] or 0)
            available_badge = f'<span class="badge tag-green">{available} maturati</span>' if available else '<span class="badge tag-blue">0 maturati</span>'
            rows.append(f'''<tr><td><a href="/veterinari/{v['id']}"><b>{esc(v['short_name'] or v['clinic_name'])}</b></a><br><small>{esc(v['clinic_name'])}</small></td><td>{esc(v['address'])}<br><small>{esc(v['city'])}</small></td><td>{esc(v['phone'])}</td><td>{available_badge} <span class="badge tag-red">{used} usati</span></td><td><a class="btn ghost" href="/veterinari/{v['id']}">Apri</a></td></tr>''')
        rows_html=''.join(rows) or '<tr><td colspan="5" class="sub">Nessun veterinario trovato.</td></tr>'
        filter_opts=''.join(f'<option {"selected" if voucher_filter==x else ""}>{x}</option>' for x in ["","Maturati","Usati","Senza buoni"])
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Veterinari</h1><div class="sub">Anagrafiche strutture veterinarie e buoni.</div></div></div><form class="section" method="get"><div class="fields"><div class="field"><label>Ricerca veterinario</label><input name="q" value="{esc(term)}" placeholder="Nome, indirizzo, comune, telefono"></div><div class="field"><label>Filtro buoni</label><select name="buoni">{filter_opts}</select></div></div><button class="btn" style="margin-top:12px">Filtra</button></form><div style="height:14px"></div><section class="section"><h2>LISTA VETERINARI</h2><div class="tablebox"><table><thead><tr><th>Veterinario</th><th>Indirizzo</th><th>Telefono</th><th>Buoni</th><th>Azione</th></tr></thead><tbody>{rows_html}</tbody></table></div></section><div style="height:14px"></div><section class="section"><h2>Aggiungi veterinario</h2><form method="post"><div class="fields"><div class="field"><label>Nome breve</label><input name="short_name" placeholder="Es. DEL PERO"></div><div class="field"><label>Nome completo</label><input name="clinic_name"></div><div class="field full"><label>Indirizzo</label><input name="address"></div><div class="field"><label>Comune</label><input name="city"></div><div class="field"><label>Telefono</label><input name="phone"></div><div class="field"><label>Medico</label><input name="doctor_name"></div><div class="field full"><label>Note</label><input name="notes"></div></div><button class="btn" style="margin-top:12px">Aggiungi veterinario</button></form></section></main>'''
        self.send_html(layout("Veterinari",body,user))

    def veterinarian_detail(self,user,vet_id):
        with db() as c:
            v=c.execute("SELECT * FROM veterinarians WHERE id=? AND active=1",(vet_id,)).fetchone()
            vouchers=c.execute("SELECT vv.*, p.animal_name, p.species, p.practice_number FROM veterinarian_vouchers vv LEFT JOIN practices p ON p.id=vv.practice_id WHERE vv.veterinarian_id=? ORDER BY vv.created_at DESC",(vet_id,)).fetchall()
        if not v: return self.send_error(404)
        rows=[]
        for b in vouchers:
            animal=(b['animal_name'] or (b['note'] or '').replace('Manuale:','').strip()).strip()
            species=b['species'] or ''
            status_opts=''.join(f'<option {"selected" if b["status"]==x else ""}>{x}</option>' for x in ["Maturato","Usato"])
            rows.append(f'''<tr><form method="post" action="/buoni/{b['id']}/modifica"><td><input type="date" name="created_at" value="{esc((b['created_at'] or '')[:10])}"></td><td><input name="animal_name" value="{esc(animal)}" placeholder="Nome animale"></td><td><input name="species" value="{esc(species)}" placeholder="Specie"></td><td><select name="status">{status_opts}</select></td><td><button class="btn ghost">Salva</button></form><form method="post" action="/buoni/{b['id']}/elimina" onsubmit="return confirm('Eliminare questo buono?')"><button class="btn ghost">Elimina</button></form></td></tr>''')
        voucher_rows=''.join(rows) or '<tr><td colspan="5" class="sub">Nessun buono presente.</td></tr>'
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>{esc(v['short_name'] or v['clinic_name'])}</h1><div class="sub">{esc(v['clinic_name'])}</div></div><a class="btn ghost" href="/veterinari">Torna alla lista</a></div><section class="section"><h2>Anagrafica</h2><form method="post" action="/veterinari"><input type="hidden" name="id" value="{v['id']}"><div class="fields"><div class="field"><label>Nome breve</label><input name="short_name" value="{esc(v['short_name'])}"></div><div class="field"><label>Nome completo</label><input name="clinic_name" value="{esc(v['clinic_name'])}"></div><div class="field full"><label>Indirizzo</label><input name="address" value="{esc(v['address'])}"></div><div class="field"><label>Comune</label><input name="city" value="{esc(v['city'])}"></div><div class="field"><label>Telefono</label><input name="phone" value="{esc(v['phone'])}"></div><div class="field"><label>Medico veterinario</label><input name="doctor_name" value="{esc(v['doctor_name'])}"></div><div class="field full"><label>Note</label><input name="notes" value="{esc(v['notes'])}"></div></div><button class="btn" style="margin-top:12px">Salva anagrafica</button></form><form method="post" action="/veterinari/{v['id']}/elimina" onsubmit="return confirm('Eliminare questo veterinario dalla lista?')"><button class="btn ghost" style="margin-top:12px">Elimina veterinario</button></form></section><div style="height:14px"></div><section class="section"><h2>Aggiungi buono manuale</h2><form method="post" action="/veterinari/{v['id']}/buoni"><div class="fields"><div class="field"><label>Data maturazione</label><input type="date" name="created_at" value="{datetime.now().strftime('%Y-%m-%d')}"></div><div class="field"><label>Nome animale</label><input name="animal_name"></div><div class="field"><label>Specie</label><input name="species"></div><div class="field"><label>Stato</label><select name="status"><option>Maturato</option><option>Usato</option></select></div></div><button class="btn" style="margin-top:12px">Aggiungi buono</button></form></section><div style="height:14px"></div><section class="section"><h2>Buoni</h2><div class="tablebox"><table><thead><tr><th>Data</th><th>Animale</th><th>Specie</th><th>Stato</th><th>Azione</th></tr></thead><tbody>{voucher_rows}</tbody></table></div></section></main>'''
        self.send_html(layout("Veterinario",body,user))

    def save_veterinarian(self,user):
        f=self.form(); stamp=now()
        clinic=f.get("clinic_name","").strip() or f.get("short_name","").strip() or "Veterinario senza nome"
        data=(f.get("short_name","").strip(), clinic, f.get("doctor_name","").strip(), f.get("phone","").strip(), f.get("address","").strip(), f.get("city","").strip(), f.get("notes","").strip())
        with db() as c:
            if f.get("id"):
                c.execute("UPDATE veterinarians SET short_name=?,clinic_name=?,doctor_name=?,phone=?,address=?,city=?,notes=?,updated_at=? WHERE id=?",data+(stamp,int(f["id"])))
                vet_id=int(f["id"])
            else:
                cur=c.execute("INSERT INTO veterinarians(short_name,clinic_name,doctor_name,phone,address,city,notes,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",data+(1,stamp,stamp))
                vet_id=cur.lastrowid
        self.redirect(f"/veterinari/{vet_id}")

    def delete_veterinarian(self,user,vet_id):
        with db() as c:
            c.execute("UPDATE veterinarians SET active=0, updated_at=? WHERE id=?",(now(),vet_id))
        self.redirect("/veterinari")

    def save_manual_voucher(self,user,vet_id):
        f=self.form(); stamp=(f.get("created_at","").strip() or now()[:10]) + "T00:00:00"
        status=f.get("status","Maturato") if f.get("status") in ("Maturato","Usato") else "Maturato"
        note="Manuale: " + " | ".join(x for x in [f.get("animal_name","").strip(), f.get("species","").strip()] if x)
        used_at=now() if status=="Usato" else None
        with db() as c:
            c.execute("INSERT INTO veterinarian_vouchers(veterinarian_id,practice_id,status,created_at,used_at,note) VALUES(?,?,?,?,?,?)",(vet_id,None,status,stamp,used_at,note))
        self.redirect(f"/veterinari/{vet_id}")

    def edit_voucher(self,user,voucher_id):
        f=self.form(); status=f.get("status","Maturato") if f.get("status") in ("Maturato","Usato") else "Maturato"
        stamp=(f.get("created_at","").strip() or now()[:10]) + "T00:00:00"
        note="Manuale: " + " | ".join(x for x in [f.get("animal_name","").strip(), f.get("species","").strip()] if x)
        used_at=now() if status=="Usato" else None
        with db() as c:
            row=c.execute("SELECT veterinarian_id FROM veterinarian_vouchers WHERE id=?",(voucher_id,)).fetchone()
            if not row: return self.send_error(404)
            c.execute("UPDATE veterinarian_vouchers SET status=?, created_at=?, used_at=?, note=? WHERE id=?",(status,stamp,used_at,note,voucher_id))
            vet_id=row["veterinarian_id"]
        self.redirect(f"/veterinari/{vet_id}")

    def delete_voucher(self,user,voucher_id):
        with db() as c:
            row=c.execute("SELECT veterinarian_id FROM veterinarian_vouchers WHERE id=?",(voucher_id,)).fetchone()
            if not row: return self.send_error(404)
            c.execute("DELETE FROM veterinarian_vouchers WHERE id=?",(voucher_id,))
            vet_id=row["veterinarian_id"]
        self.redirect(f"/veterinari/{vet_id}")

    def use_voucher(self,user,vet_id):
        with db() as c:
            voucher=c.execute("SELECT id FROM veterinarian_vouchers WHERE veterinarian_id=? AND status='Maturato' ORDER BY created_at LIMIT 1",(vet_id,)).fetchone()
            if voucher:
                c.execute("UPDATE veterinarian_vouchers SET status='Usato', used_at=? WHERE id=?",(now(),voucher["id"]))
        self.redirect(f"/veterinari/{vet_id}")

    def use_specific_voucher(self,user,voucher_id):
        with db() as c:
            row=c.execute("SELECT veterinarian_id FROM veterinarian_vouchers WHERE id=?",(voucher_id,)).fetchone()
            c.execute("UPDATE veterinarian_vouchers SET status='Usato', used_at=? WHERE id=? AND status='Maturato'",(now(),voucher_id))
        self.redirect(f"/veterinari/{row['veterinarian_id']}" if row else "/veterinari")

    def fields_html(self,p=None):
        val=lambda k: esc(p[k] if p and k in p.keys() else "")
        raw=lambda k,default="": (p[k] if p and k in p.keys() and p[k] not in (None,"") else default)
        selected=lambda k,v,default="": "selected" if str(raw(k,default))==v else ""
        tag_select=lambda name,label,cls: f'''<div class="field"><label><input type="checkbox" name="{name}" value="Si" {"checked" if raw(name)=="Si" else ""}> <span class="badge {cls}">{label}</span></label></div>'''
        with db() as c:
            vets=c.execute("SELECT * FROM veterinarians WHERE active=1 ORDER BY COALESCE(short_name, clinic_name), clinic_name").fetchall()
        vet_option=lambda v, selected_id: f'<option value="{v["id"]}" data-shortname="{esc(v["short_name"] or v["clinic_name"])}" data-fullname="{esc(v["clinic_name"])}" data-address="{esc(v["address"])}" data-city="{esc(v["city"])}" data-phone="{esc(v["phone"])}" {"selected" if str(selected_id)==str(v["id"]) else ""}>{esc(v["short_name"] or v["clinic_name"])}{(" - "+esc(v["clinic_name"])) if v["short_name"] else ""}</option>'
        vet_options='<option value="">Nessun veterinario selezionato</option>'+''.join(vet_option(v, raw("veterinarian_id")) for v in vets)
        owner_vet_options='<option value="">Compilazione manuale</option>'+''.join(vet_option(v, raw("owner_veterinarian_id")) for v in vets)
        voucher_checked='checked' if raw('voucher_requested')=="Si" else ''
        use_voucher_checked='checked' if raw('use_voucher')=="Si" else ''
        catalog_checked='checked' if raw('send_catalog')=="Si" else ''
        estremi_checked='checked' if raw('send_estremi')=="Si" else ''
        return f'''<section class="section"><h2>Operatore</h2><div class="fields"><div class="field"><label>Operatore *</label><select name="operator_name" required><option value="">Seleziona operatore</option><option {selected('operator_name','SERENA')}>SERENA</option><option {selected('operator_name','ALESSIO')}>ALESSIO</option><option {selected('operator_name','FILIPPO')}>FILIPPO</option></select></div></div></section>
        <input type="hidden" name="urn_notes" value="{val('urn_notes')}">
        <input type="hidden" name="price_urn_2" value="{val('price_urn_2')}"><input type="hidden" name="urn_notes_2" value="{val('urn_notes_2')}"><input type="hidden" name="price_cast_2" value="{val('price_cast_2')}"><input type="hidden" name="price_accessories_2" value="{val('price_accessories_2')}"><input type="hidden" name="accessory_type" value="{val('accessory_type')}"><input type="hidden" name="accessory_type_2" value="{val('accessory_type_2')}">
        <section class="section"><h2>Richiesta</h2><div class="fields"><div class="field"><label>Servizio</label><select name="service_type"><option {selected('service_type','Da decidere')}>Da decidere</option><option {selected('service_type','Cremazione singola')}>Cremazione singola</option><option {selected('service_type','Cremazione collettiva')}>Cremazione collettiva</option></select></div><div class="field"><label>Origine richiesta *</label><select name="request_origin" required><option {selected('request_origin','Veterinario')}>Veterinario</option><option {selected('request_origin','Privato')}>Privato</option><option {selected('request_origin','Consegna in sede')}>Consegna in sede</option><option {selected('request_origin','Collaboratore')}>Collaboratore</option></select></div><div class="field {'hidden' if raw('request_origin')!='Collaboratore' else ''}" id="collaboratorBox"><label>Collaboratore</label><select name="collaborator_name"><option value="">Nessun collaboratore</option><option {selected('collaborator_name','HUMANITAS CROCE VERDE')}>HUMANITAS CROCE VERDE</option></select></div><div class="field"><label>Sede di destinazione</label><select name="destination_branch"><option {selected('destination_branch','Livorno')}>Livorno</option><option {selected('destination_branch','Empoli')}>Empoli</option></select></div><div class="field"><label>Data recupero</label><input type="date" name="pickup_date" value="{val('pickup_date')}"></div></div></section>
        <section class="section"><h2>SPEDITORE</h2><div class="fields"><input type="hidden" name="client_id" value="{val('client_id')}"><div class="field full lookup"><label>Cerca cliente in anagrafica</label><input id="clientSearch" autocomplete="off" placeholder="Scrivi nome, telefono, email, codice fiscale, città..."><div id="clientResults" class="lookup-results hidden"></div><div id="clientSelected" class="selected-box hidden"><span id="clientSelectedText"></span><button class="btn ghost" type="button" id="clearClientSelection">Cancella selezione</button></div><small class="sub">Se scegli un cliente, i campi vengono compilati automaticamente. Se li modifichi, l'anagrafica non viene aggiornata senza conferma.</small></div><div class="field full"><label>Usa veterinario come speditore</label><select name="owner_veterinarian_id">{owner_vet_options}</select><small class="sub">Compila automaticamente i dati dello speditore. Sul DDT, nel Luogo di origine, verra scritto solo il nome breve del veterinario.</small></div><div class="field"><label>Nome *</label><input name="owner_first_name" value="{val('owner_first_name')}" required></div><div class="field"><label>Cognome *</label><input name="owner_last_name" value="{val('owner_last_name')}" required></div><div class="field"><label>Ragione sociale</label><input name="owner_company" value="{val('owner_company')}"></div><div class="field"><label>Telefono *</label><input type="tel" inputmode="numeric" name="owner_phone" value="{val('owner_phone')}" required></div><div class="field"><label>Secondo telefono</label><input type="tel" inputmode="numeric" name="owner_phone_2" value="{val('owner_phone_2')}"></div><div class="field"><label>Email</label><input type="email" name="owner_email" value="{val('owner_email')}"></div><div class="field"><label>Codice fiscale *</label><input name="owner_tax_code" value="{val('owner_tax_code')}" required></div><div class="field"><label>Partita IVA</label><input name="owner_vat" value="{val('owner_vat')}"></div><div class="field full"><label>Indirizzo *</label><input name="owner_street" value="{val('owner_street') or val('owner_address')}" required></div><div class="field"><label>Comune *</label><input name="owner_city" value="{val('owner_city')}" required></div><div class="field"><label>Provincia *</label><input name="owner_province" value="{val('owner_province')}" maxlength="2" placeholder="Si compila dal comune" required></div><div class="field"><label>CAP *</label><input name="owner_zip" value="{val('owner_zip')}" inputmode="numeric" required></div><div class="field full"><label>Note cliente</label><textarea name="owner_notes" placeholder="Note anagrafiche utili">{val('owner_notes')}</textarea></div></div></section>
        <section class="section"><h2>DESTINATARIO E LUOGO DI DESTINAZIONE</h2><p class="sub">Compilati automaticamente in base alla sede selezionata: Livorno oppure Empoli.</p></section>
        <section class="section"><h2>LUOGO DI ORIGINE</h2><div class="fields"><div class="field"><label>Luogo di origine</label><select name="origin_mode"><option {selected('origin_mode','IDEM SPED','IDEM SPED')}>IDEM SPED</option><option {selected('origin_mode','Testo libero','IDEM SPED')}>Testo libero</option></select></div><div class="field full"><label>Testo libero / indirizzo diverso</label><input name="origin_text" value="{val('origin_text') or (val('pickup_address') if raw('pickup_address_mode')=='Altro indirizzo' else '')}" placeholder="Scrivi qui solo se il luogo non è IDEM SPED"></div></div></section>
        <section class="section"><h2>Animale</h2><div class="fields"><div class="field"><label>Nome</label><input name="animal_name" value="{val('animal_name')}"></div><div class="field"><label>Specie</label><input name="species" value="{val('species')}"></div><div class="field"><label>Peso stimato (kg)</label><input name="estimated_weight" value="{val('estimated_weight')}"></div><div class="field"><label>Età - anni</label><input name="age_years" value="{val('age_years')}"></div><div class="field"><label>Età - mesi</label><input name="age_months" value="{val('age_months')}"></div><div class="field"><label>Microchip</label><input name="microchip" value="{val('microchip')}"></div><div class="field full"><label>Razza</label><input name="breed" value="{val('breed')}"></div></div><button class="btn ghost" type="button" id="showSecondAnimal" style="margin-top:12px;{'display:none' if raw('animal2_name') else ''}">+ Aggiungi altro animale</button><div id="secondAnimalBox" style="display:{'block' if raw('animal2_name') else 'none'};margin-top:14px"><h2>Secondo animale</h2><div class="fields"><div class="field"><label>Nome</label><input name="animal2_name" value="{val('animal2_name')}"></div><div class="field"><label>Specie</label><input name="animal2_species" value="{val('animal2_species')}"></div><div class="field"><label>Peso stimato (kg)</label><input name="animal2_weight" value="{val('animal2_weight')}"></div><div class="field"><label>Microchip</label><input name="animal2_microchip" value="{val('animal2_microchip')}"></div><div class="field full"><label>Razza</label><input name="animal2_breed" value="{val('animal2_breed')}"></div></div></div></section>
        <section class="section"><h2>AMBULATORIO VETERINARIO</h2><div class="fields"><div class="field full lookup"><label>VETERINARIO</label><input id="vetSearch" autocomplete="off" placeholder="Scrivi per cercare il veterinario"><div id="vetResults" class="lookup-results hidden"></div><select name="veterinarian_id">{vet_options}</select><input type="hidden" name="clinic_name" value="{val('clinic_name')}"><button class="btn ghost" type="button" id="clearVetSelection" style="margin-top:8px">Cancella veterinario</button></div><div class="field"><label>MEDICO VETERINARIO</label><input name="veterinarian_name" value="{val('veterinarian_name')}"></div><div class="field"><label><input type="checkbox" name="voucher_requested" value="Si" {voucher_checked}> BUONO</label><small class="sub">Spunta per assegnare un buono al veterinario selezionato.</small></div></div></section>
        <section class="section"><h2>TRASPORTATORE</h2><div class="fields"><div class="field"><label>Dati trasportatore</label><select name="transporter_mode"><option {selected('transporter_mode','IDEM SPED','IDEM SPED')}>IDEM SPED</option><option {selected('transporter_mode','DATI PET PARADISE','IDEM SPED')}>DATI PET PARADISE</option></select></div><div class="field"><label>Scelta rapida mezzo</label><select id="transport_method_quick"><option value="">Seleziona se serve</option><option value="MEZZO PROPRIO">MEZZO PROPRIO</option></select></div><div class="field"><label>Mezzo di trasporto</label><input name="transport_method" value="{val('transport_method')}"></div><div class="field"><label>Targa automezzo</label><input name="vehicle_plate" value="{val('vehicle_plate')}"></div><div class="field"><label>Temperatura</label><select name="temperature_mode"><option {selected('temperature_mode','Ambiente','Ambiente')}>Ambiente</option><option {selected('temperature_mode','Refrigerato','Ambiente')}>Refrigerato</option><option {selected('temperature_mode','Congelato','Ambiente')}>Congelato</option></select></div><div class="field"><label>Numero colli</label><input name="package_count" value="{val('package_count') or '1'}"></div><div class="field"><label>ID contenitore</label><select name="container_id"><option value="">Seleziona ID contenitore</option><option {selected('container_id','03/2021')}>03/2021</option><option {selected('container_id','04/2021')}>04/2021</option></select></div><div class="field"><label>Numero lotto</label><input name="lot_number" value="{val('lot_number') or '/'}"></div><div class="field"><label>Metodo trattamento</label><input name="treatment_method" value="{val('treatment_method') or '/'}"></div></div></section>
        <section class="section"><h2>Preventivo</h2><div class="fields"><div class="field"><label>Cremazione €</label><input name="price_cremation" value="{val('price_cremation')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Ritiro €</label><input name="price_pickup" value="{val('price_pickup')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Urna €</label><input name="price_urn" value="{val('price_urn')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label><input type="checkbox" name="send_catalog" value="Si" {catalog_checked} style="width:auto"> INVIARE CATALOGO</label></div><div class="field"><label>Riconsegna €</label><input name="price_delivery" value="{val('price_delivery')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Calco €</label><input name="price_cast" value="{val('price_cast')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Serale €</label><input name="price_evening" value="{val('price_evening')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Notturno €</label><input name="price_night" value="{val('price_night')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Festivo €</label><input name="price_holiday" value="{val('price_holiday')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Accessori €</label><input name="price_accessories" value="{val('price_accessories')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Totale servizio €</label><input name="total_service" value="{val('total_service')}" placeholder="Numero o testo libero"></div><div class="field"><label><input type="checkbox" name="use_voucher" value="Si" {use_voucher_checked} style="width:auto"> USA BUONO</label><div id="useVoucherBox" class="selected-box hidden"><span id="useVoucherStatus">Seleziona il veterinario e spunta USA BUONO.</span><select name="used_voucher_id" data-current="{val('used_voucher_id')}" class="hidden"><option value="">Seleziona buono</option></select></div></div><div class="field"><label><input type="checkbox" name="send_estremi" value="Si" {estremi_checked} style="width:auto"> INVIARE ESTREMI</label></div><div class="field"><label>Acconto €</label><input name="deposit" value="{val('deposit')}" placeholder="Numero o testo libero"></div><div class="field"><label>Rimanenza €</label><input name="remaining_balance" value="{val('remaining_balance')}" readonly></div><div class="field full"><label>TOTALE</label><textarea name="total_text" placeholder="Testo libero per note sul totale">{val('total_text')}</textarea></div><div class="field full"><label>Note operative</label><textarea name="notes">{val('notes')}</textarea></div></div></section>
        <section class="section"><h2>Etichette operative</h2><div class="fields">{tag_select('tag_assistita','ASSISTITA','tag-red')}{tag_select('tag_possibile_assistita','POSSIBILE ASSISTITA','tag-red')}{tag_select('tag_assistita_streaming','ASSISTITA STREAMING','tag-orange')}{tag_select('tag_saluto','SALUTO','tag-purple')}{tag_select('tag_calco','CALCO','tag-yellow')}{tag_select('tag_avvisare','AVVISARE','tag-pink')}{tag_select('tag_da_richiamare','DA RICHIAMARE','tag-blue')}</div></section>
        <section class="section"><h2>Documento e accettazione</h2><div class="fields"><div class="field"><label>Numero documento</label><input name="identity_document_number" value="{val('identity_document_number')}"></div><div class="field"><label>Data rilascio</label><input type="date" name="identity_document_date" value="{val('identity_document_date')}"></div><div class="field full"><label>Luogo firma</label><input name="signing_place" value="{val('signing_place') or val('destination_branch')}"></div></div></section>'''

    def new_page(self,user):
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Nuova pratica</h1><div class="sub">Inserisci subito i dati disponibili; potrai completarli in seguito.</div></div><div class="actions"><button class="btn" form="practiceForm">Crea pratica</button></div></div><form method="post" id="practiceForm"><div class="grid form-grid">{self.fields_html()}</div><div class="actions" style="margin-top:18px"><button class="btn">Crea pratica</button><a class="btn ghost" href="/">Annulla</a></div></form></main>'''
        self.send_html(layout("Nuova pratica",body,user))

    def normalized_fields(self,f):
        keys=["client_id","owner_veterinarian_id","operator_name","request_origin","collaborator_name","destination_branch","owner_first_name","owner_last_name","owner_company","owner_phone","owner_phone_2","owner_email","owner_tax_code","owner_vat","owner_notes","owner_address","owner_street","owner_city","owner_province","owner_zip","pickup_address_mode","pickup_address","origin_mode","origin_text","pickup_date","animal_name","species","breed","estimated_weight","age_years","age_months","microchip","animal2_name","animal2_species","animal2_breed","animal2_weight","animal2_microchip","service_type","veterinarian_id","voucher_requested","use_voucher","used_voucher_id","clinic_name","veterinarian_name","notes","transporter_mode","transport_method","vehicle_plate","temperature_mode","package_count","container_id","lot_number","treatment_method","tag_assistita","tag_possibile_assistita","tag_assistita_streaming","tag_saluto","tag_calco","tag_avvisare","tag_da_richiamare","payment_status","price_cremation","price_pickup","price_evening","price_urn","send_catalog","send_estremi","price_delivery","price_night","price_cast","price_holiday","price_accessories","deposit","remaining_balance","total_service","total_text","identity_document_number","identity_document_date","signing_place"]
        data = {k:f.get(k,"").strip() for k in keys}
        data["urn_notes"] = f.get("urn_notes","").strip()
        for key in ("price_urn_2","urn_notes_2","price_cast_2","price_accessories_2","accessory_type","accessory_type_2"):
            data[key]=f.get(key,"").strip()
        for key in MONEY_FIELDS:
            value=data.get(key,"").replace(",",".")
            data[key]=value
        if data["total_text"]:
            data["total_service"]=data["total_text"]
        allowed_accessories={"","Calco naso","Collana","Braccialetto","Calco inchiostro","Altro"}
        if data["accessory_type"] not in allowed_accessories: data["accessory_type"]="Altro"
        if data["accessory_type_2"] not in allowed_accessories: data["accessory_type_2"]="Altro"
        if not data["payment_status"] or data["payment_status"] not in PAYMENT_STATES:
            data["payment_status"] = "Da saldare"
        data["send_catalog"] = "Si" if data["send_catalog"] == "Si" else ""
        data["send_estremi"] = "Si" if data["send_estremi"] == "Si" else ""
        data["use_voucher"] = "Si" if data["use_voucher"] == "Si" else ""
        data["used_voucher_id"] = data["used_voucher_id"] or None
        for key in ("tag_assistita","tag_possibile_assistita","tag_assistita_streaming","tag_saluto","tag_calco","tag_avvisare","tag_da_richiamare"):
            data[key] = "Si" if data[key] == "Si" else ""
        data["voucher_requested"] = "Si" if data["voucher_requested"] == "Si" else ""
        data["client_id"] = data["client_id"] or None
        data["owner_veterinarian_id"] = data["owner_veterinarian_id"] or None
        if data["use_voucher"] == "Si":
            data["payment_status"] = "Pagato"
        data["veterinarian_id"] = data["veterinarian_id"] or None
        if data["owner_veterinarian_id"]:
            with db() as c:
                owner_vet = c.execute("SELECT * FROM veterinarians WHERE id=? AND active=1", (data["owner_veterinarian_id"],)).fetchone()
            if owner_vet:
                full_name = owner_vet["clinic_name"] or owner_vet["short_name"] or ""
                short_name = owner_vet["short_name"] or full_name
                address = owner_vet["address"] or ""
                data["owner_first_name"] = full_name
                data["owner_last_name"] = ""
                data["owner_company"] = full_name
                data["owner_phone"] = owner_vet["phone"] or data["owner_phone"]
                data["owner_street"] = address or data["owner_street"]
                data["owner_city"] = owner_vet["city"] or data["owner_city"]
                m = re.search(r"\b(\d{5})\b", address)
                if m and not data["owner_zip"]:
                    data["owner_zip"] = m.group(1)
                m = re.search(r"\b([A-Z]{2})\b\s*$", address)
                if m and not data["owner_province"]:
                    data["owner_province"] = m.group(1)
                data["origin_mode"] = "Testo libero"
                data["origin_text"] = short_name
        if data["veterinarian_id"]:
            with db() as c:
                vet = c.execute("SELECT * FROM veterinarians WHERE id=? AND active=1", (data["veterinarian_id"],)).fetchone()
            if vet:
                data["clinic_name"] = data["clinic_name"] or vet["clinic_name"]
                if data["service_type"] == "Cremazione collettiva":
                    data["owner_first_name"] = vet["clinic_name"]
                    data["owner_last_name"] = ""
                    data["owner_street"] = " - ".join(x for x in [vet["clinic_name"], vet["address"]] if x)
                    data["owner_city"] = vet["city"] or ""
                    data["owner_address"] = " - ".join(x for x in [vet["clinic_name"], vet["address"]] if x)
                if not data["origin_text"]:
                    data["origin_text"] = vet["short_name"] or vet["clinic_name"] or " - ".join(x for x in [vet["clinic_name"], vet["address"]] if x)
                if data["origin_text"]:
                    data["origin_mode"] = "Testo libero"
        if data["collaborator_name"] == "HUMANITAS CROCE VERDE":
            collab=COLLABORATORS["HUMANITAS CROCE VERDE"]
            data["owner_first_name"] = data["owner_first_name"] or "HUMANITAS"
            data["owner_last_name"] = data["owner_last_name"] or "CROCE VERDE"
            data["owner_street"] = data["owner_street"] or collab["street"]
            data["owner_zip"] = data["owner_zip"] or collab["zip"]
            data["owner_city"] = data["owner_city"] or collab["city"]
            data["owner_province"] = data["owner_province"] or collab["province"]
            data["owner_tax_code"] = data["owner_tax_code"] or collab["vat"]
        data["owner_province"] = data["owner_province"].upper()
        city_line = " ".join(x for x in [data["owner_zip"], data["owner_city"], f'({data["owner_province"]})' if data["owner_province"] else ""] if x).strip()
        composed_address = " - ".join(x for x in [data["owner_street"], city_line] if x)
        if composed_address:
            data["owner_address"] = composed_address
        if not data["origin_mode"]:
            data["origin_mode"] = "IDEM SPED"
        if not data["transporter_mode"]:
            data["transporter_mode"] = "IDEM SPED"
        if data["request_origin"] == "Consegna in sede":
            data["transport_method"] = data["transport_method"] or "MEZZO PROPRIO"
            data["transporter_mode"] = "IDEM SPED"
        elif data["request_origin"] in ("Veterinario","Privato","Collaboratore"):
            data["transporter_mode"] = "DATI PET PARADISE"
        if data["origin_mode"] == "IDEM SPED":
            data["pickup_address"] = data["owner_address"]
            data["pickup_address_mode"] = "Idem sped."
        else:
            data["pickup_address"] = data["origin_text"]
            data["pickup_address_mode"] = "Altro indirizzo"
        return data

    def is_complete(self,d):
        if d.get("service_type") == "Cremazione collettiva" and d.get("veterinarian_id"):
            return 1
        required=["operator_name","request_origin","owner_first_name","owner_last_name","owner_phone","owner_tax_code","owner_street","owner_city","owner_province","owner_zip"]
        return int(all(d.get(k) for k in required))

    def validation_error(self,d):
        invalid_money=[label for key,label in MONEY_FIELDS.items() if d.get(key) and not re.fullmatch(r"\d+(?:\.\d{1,2})?",d[key])]
        if invalid_money:
            return "Nel Preventivo sono ammessi solo numeri, con al massimo due decimali: " + ", ".join(invalid_money)
        if d.get("service_type") == "Cremazione collettiva" and d.get("veterinarian_id"):
            return ""
        labels={
            "operator_name":"Operatore","request_origin":"Richiesta","owner_first_name":"Nome",
            "owner_last_name":"Cognome","owner_phone":"Telefono",
            "owner_tax_code":"Codice fiscale","owner_street":"Indirizzo",
            "owner_city":"Comune","owner_province":"Provincia","owner_zip":"CAP",
        }
        missing=[label for key,label in labels.items() if not d.get(key)]
        return "Campi obbligatori mancanti: " + ", ".join(missing) if missing else ""

    def masked_whatsapp_token(self, token):
        if not token:
            return "MANCANTE"
        if len(token) <= 12:
            return token[:2] + "***" + token[-2:]
        return token[:6] + "..." + token[-6:]

    def whatsapp_language_code(self):
        return os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE", "it").strip() or "it"

    def whatsapp_template_name(self, p):
        branch=(p["destination_branch"] or "Livorno").strip().lower()
        return "ringraziamento_empoli" if branch == "empoli" else "ringraziamento_livorno"

    def whatsapp_client_name(self, p):
        name=" ".join(x for x in [(p["owner_first_name"] or "").strip(), (p["owner_last_name"] or "").strip()] if x).strip()
        return name or "cliente"

    def whatsapp_animal_name(self, p):
        return (p["animal_name"] or "").strip() or "il vostro compagno"

    def whatsapp_normalized_phone(self, p):
        phone=re.sub(r"\D+","",p["owner_phone"] or "")
        if phone.startswith("00"):
            phone=phone[2:]
        if phone and not phone.startswith("39"):
            phone="39"+phone
        return phone

    def whatsapp_payload_for_practice(self, p):
        template=self.whatsapp_template_name(p)
        language=self.whatsapp_language_code()
        nome_cliente=self.whatsapp_client_name(p)
        nome_animale=self.whatsapp_animal_name(p)
        return {
            "messaging_product":"whatsapp",
            "to":self.whatsapp_normalized_phone(p),
            "type":"template",
            "template":{
                "name":template,
                "language":{"code":language},
                "components":[{
                    "type":"body",
                    "parameters":[
                        {"type":"text","text":nome_cliente},
                        {"type":"text","text":nome_animale},
                    ],
                }],
            },
        }

    def whatsapp_meta_config(self):
        token=os.environ.get("WHATSAPP_ACCESS_TOKEN","").strip()
        phone_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID","").strip()
        version=os.environ.get("WHATSAPP_GRAPH_VERSION","v20.0").strip()
        endpoint=f"https://graph.facebook.com/{version}/{phone_id}/messages" if phone_id else ""
        return token, phone_id, version, endpoint

    def whatsapp_status_label(self, status):
        return {
            "programmato":"Programmato",
            "in_invio":"In invio",
            "accettato_da_meta":"Accettato da Meta",
            "consegnato":"Consegnato",
            "letto":"Letto",
            "fallito":"Fallito",
            "annullato":"Annullato",
        }.get(status or "", status or "Non programmato")

    def whatsapp_next_retry_at(self, attempts):
        minutes = [10, 30, 120]
        idx=max(0, min(int(attempts or 1)-1, len(minutes)-1))
        return (datetime.now() + timedelta(minutes=minutes[idx])).isoformat(timespec="seconds")

    def whatsapp_get_meta(self, endpoint, token):
        req=urllib.request.Request(endpoint,headers={"Authorization":f"Bearer {token}"},method="GET")
        try:
            with urllib.request.urlopen(req,timeout=12) as resp:
                body=resp.read().decode("utf-8","replace")
                print(f"[WHATSAPP_DIAGNOSTICA] GET {endpoint} token={self.masked_whatsapp_token(token)} http={resp.status} risposta={body}", flush=True)
                return resp.status, body
        except urllib.error.HTTPError as exc:
            body=exc.read().decode("utf-8","replace")
            print(f"[WHATSAPP_DIAGNOSTICA] GET {endpoint} token={self.masked_whatsapp_token(token)} http={exc.code} risposta={body}", flush=True)
            return exc.code, body
        except Exception as exc:
            body=str(exc)
            print(f"[WHATSAPP_DIAGNOSTICA] GET {endpoint} token={self.masked_whatsapp_token(token)} errore={body}", flush=True)
            return "ERRORE", body

    def whatsapp_id_hint(self, text):
        lower=str(text or "").lower()
        if "unsupported post request" in lower or "object with id" in lower or "does not exist" in lower:
            return '<div class="flash warning"><b>WHATSAPP_PHONE_NUMBER_ID non valido:</b> probabilmente hai inserito l’ID profilo telefono e non il Phone Number ID API.</div>'
        return ""

    def whatsapp_diagnostics(self,user):
        if user["role"] != "admin":
            return self.send_error(403)
        token=os.environ.get("WHATSAPP_ACCESS_TOKEN","").strip()
        phone_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID","").strip()
        version=os.environ.get("WHATSAPP_GRAPH_VERSION","v20.0").strip()
        token_status="PRESENTE: " + self.masked_whatsapp_token(token) if token else "MANCANTE"
        phone_endpoint=f"https://graph.facebook.com/{version}/{phone_id}" if phone_id else ""
        me_endpoint=f"https://graph.facebook.com/{version}/me?fields=id,name"
        if token and phone_id:
            phone_status, phone_body = self.whatsapp_get_meta(phone_endpoint, token)
        else:
            phone_status, phone_body = "NON ESEGUITO", "Token o WHATSAPP_PHONE_NUMBER_ID mancante"
        if token:
            me_status, me_body = self.whatsapp_get_meta(me_endpoint, token)
        else:
            me_status, me_body = "NON ESEGUITO", "Token mancante"
        hint=self.whatsapp_id_hint(phone_body)
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Diagnostica WhatsApp</h1><div class="sub">Controllo configurazione WhatsApp Business Cloud API.</div></div><a class="btn ghost" href="/">Dashboard</a></div>{hint}<section class="section"><h2>Variabili Render</h2><div class="kvs"><div class="kv"><small>WHATSAPP_GRAPH_VERSION</small><b>{esc(version)}</b></div><div class="kv"><small>WHATSAPP_ACCESS_TOKEN</small><b>{esc(token_status)}</b></div><div class="kv"><small>WHATSAPP_PHONE_NUMBER_ID</small><b>{esc(phone_id or "MANCANTE")}</b></div></div></section><div style="height:14px"></div><section class="section"><h2>GET Phone Number ID</h2><p><b>Endpoint:</b> {esc(phone_endpoint or "NON DISPONIBILE")}</p><p><b>HTTP:</b> {esc(phone_status)}</p><pre style="white-space:pre-wrap;background:#f7f4f0;padding:12px;border-radius:10px;overflow:auto">{esc(phone_body)}</pre></section><div style="height:14px"></div><section class="section"><h2>GET /me</h2><p><b>Endpoint:</b> {esc(me_endpoint)}</p><p><b>HTTP:</b> {esc(me_status)}</p><pre style="white-space:pre-wrap;background:#f7f4f0;padding:12px;border-radius:10px;overflow:auto">{esc(me_body)}</pre></section></main>'''
        self.send_html(layout("Diagnostica WhatsApp",body,user))

    def schedule_whatsapp_thanks(self,c,pid,user_id=None):
        p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p:
            return False, "Pratica non trovata"
        if "no_whatsapp_message" in p.keys() and p["no_whatsapp_message"] == "Si":
            msg="NO MESSAGGIO attivo: WhatsApp non programmato"
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(msg,pid))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"WhatsApp ringraziamento",msg,user_id,now()))
            print(f"[WHATSAPP] pratica={pid} esito=SKIP programmazione motivo=no_whatsapp_message", flush=True)
            return False, msg
        if "whatsapp_thanks_sent_at" in p.keys() and p["whatsapp_thanks_sent_at"]:
            msg="WhatsApp già inviato in precedenza: nuova programmazione saltata"
            print(f"[WHATSAPP] pratica={pid} esito=SKIP programmazione dettaglio={msg}", flush=True)
            return False, msg
        existing=c.execute("SELECT * FROM whatsapp_messages WHERE practice_id=? AND status IN ('programmato','in_invio','accettato_da_meta','consegnato','letto') ORDER BY created_at DESC LIMIT 1",(pid,)).fetchone()
        if existing:
            msg=f"WhatsApp già presente con stato {existing['status']}"
            print(f"[WHATSAPP] pratica={pid} esito=SKIP programmazione dettaglio={msg}", flush=True)
            return False, msg
        phone=self.whatsapp_normalized_phone(p)
        if not phone:
            msg="Telefono speditore mancante: WhatsApp non programmato"
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(msg,pid))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"WhatsApp ringraziamento",msg,user_id,now()))
            return False, msg
        scheduled_at=(datetime.now()+timedelta(hours=48)).isoformat(timespec="seconds")
        template=self.whatsapp_template_name(p)
        language=self.whatsapp_language_code()
        stamp=now()
        c.execute("""INSERT INTO whatsapp_messages(practice_id,scheduled_at,status,attempts,template_name,language_code,recipient_phone,manual,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",(pid,scheduled_at,"programmato",0,template,language,phone,0,stamp,stamp))
        c.execute("UPDATE practices SET whatsapp_thanks_last_error='' WHERE id=?",(pid,))
        c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"WhatsApp programmato",f"Invio programmato per {scheduled_at} a +{phone} con template {template}",user_id,stamp))
        print(f"[WHATSAPP] pratica={pid} esito=PROGRAMMATO scheduled_at={scheduled_at} destinatario=+{phone} template={template} lingua={language}", flush=True)
        return True, scheduled_at

    def cancel_whatsapp_scheduled(self,c,pid,user_id=None,reason="Invio programmato annullato"):
        stamp=now()
        rows=c.execute("UPDATE whatsapp_messages SET status='annullato', last_error=?, updated_at=? WHERE practice_id=? AND status IN ('programmato','in_invio')",(reason,stamp,pid)).rowcount
        if rows:
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"WhatsApp annullato",reason,user_id,stamp))
            print(f"[WHATSAPP] pratica={pid} esito=ANNULLATO righe={rows} motivo={reason}", flush=True)
        return rows

    def send_whatsapp_message(self,c,msg_id,manual=False,user_id=None):
        msg=c.execute("SELECT * FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()
        if not msg:
            return False, "Invio WhatsApp non trovato"
        p=c.execute("SELECT * FROM practices WHERE id=?",(msg["practice_id"],)).fetchone()
        if not p:
            return False, "Pratica non trovata"
        if not manual and "no_whatsapp_message" in p.keys() and p["no_whatsapp_message"] == "Si":
            self.cancel_whatsapp_scheduled(c,p["id"],user_id,"NO MESSAGGIO attivo prima dell'invio")
            return False, "NO MESSAGGIO attivo"
        token, phone_id, version, endpoint = self.whatsapp_meta_config()
        payload_obj=self.whatsapp_payload_for_practice(p)
        phone=payload_obj["to"]
        template=payload_obj["template"]["name"]
        language=payload_obj["template"]["language"]["code"]
        if not phone:
            error="Telefono speditore mancante"
            c.execute("UPDATE whatsapp_messages SET status='fallito', last_error=?, attempts=attempts+1, last_attempt_at=?, updated_at=? WHERE id=?",(error,now(),now(),msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(error,p["id"]))
            return False,error
        if not token or not phone_id:
            error="Config WhatsApp mancante: imposta WHATSAPP_ACCESS_TOKEN e WHATSAPP_PHONE_NUMBER_ID su Render"
            c.execute("UPDATE whatsapp_messages SET status='fallito', last_error=?, attempts=attempts+1, last_attempt_at=?, updated_at=? WHERE id=?",(error,now(),now(),msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(error,p["id"]))
            return False,error
        payload=json.dumps(payload_obj,ensure_ascii=False).encode("utf-8")
        print(f"[WHATSAPP] POST pratica_id={p['id']} message_row={msg_id} endpoint={endpoint} phone_number_id={phone_id} token={self.masked_whatsapp_token(token)} destinatario=+{phone} template={template} lingua={language} scheduled_at={msg['scheduled_at']} payload={json.dumps(payload_obj,ensure_ascii=False)}", flush=True)
        req=urllib.request.Request(endpoint,data=payload,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},method="POST")
        attempt_stamp=now()
        try:
            with urllib.request.urlopen(req,timeout=18) as resp:
                response_body=resp.read().decode("utf-8","replace")
                http_status=resp.status
            response_json=json.loads(response_body) if response_body else {}
            message_id=""
            if isinstance(response_json,dict) and response_json.get("messages"):
                message_id=response_json["messages"][0].get("id","")
            sent_at=now()
            c.execute("""UPDATE whatsapp_messages SET status='accettato_da_meta', attempts=attempts+1, last_error='', message_id=?, sent_at=?, last_attempt_at=?, template_name=?, language_code=?, recipient_phone=?, payload_json=?, response_json=?, updated_at=? WHERE id=?""",(message_id,sent_at,attempt_stamp,template,language,phone,json.dumps(payload_obj,ensure_ascii=False),response_body,sent_at,msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_sent_at=?, whatsapp_thanks_last_error='' WHERE id=?",(sent_at,p["id"]))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(p["id"],"WhatsApp ringraziamento",f"Accettato da Meta {sent_at} a +{phone} - template {template} - message_id {message_id}",user_id,sent_at))
            print(f"[WHATSAPP] pratica_id={p['id']} message_row={msg_id} esito=ACCETTATO_DA_META http={http_status} message_id={message_id} risposta={response_body}", flush=True)
            if manual:
                self.cancel_whatsapp_scheduled(c,p["id"],user_id,"Annullato perché è stato inviato manualmente")
                c.execute("UPDATE whatsapp_messages SET status='accettato_da_meta', updated_at=? WHERE id=?",(sent_at,msg_id))
            return True, f"Accettato da Meta. Message ID: {message_id or 'non restituito'}"
        except urllib.error.HTTPError as exc:
            detail=exc.read().decode("utf-8","replace")
            error=f"Meta API HTTP {exc.code}: {detail}"
            attempts=int(msg["attempts"] or 0)+1
            next_status="fallito" if attempts >= 3 or manual else "programmato"
            retry_at=self.whatsapp_next_retry_at(attempts) if next_status=="programmato" else msg["scheduled_at"]
            c.execute("""UPDATE whatsapp_messages SET status=?, scheduled_at=?, attempts=?, last_error=?, last_attempt_at=?, template_name=?, language_code=?, recipient_phone=?, payload_json=?, response_json=?, updated_at=? WHERE id=?""",(next_status,retry_at,attempts,error,attempt_stamp,template,language,phone,json.dumps(payload_obj,ensure_ascii=False),detail,now(),msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(error,p["id"]))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(p["id"],"WhatsApp ringraziamento",f"Errore: {error}",user_id,now()))
            print(f"[WHATSAPP] pratica_id={p['id']} message_row={msg_id} esito=ERRORE http={exc.code} tentativi={attempts} prossimo_stato={next_status} endpoint={endpoint} destinatario=+{phone} template={template} lingua={language} risposta={detail}", flush=True)
            return False,error
        except Exception as exc:
            error=str(exc)
            attempts=int(msg["attempts"] or 0)+1
            next_status="fallito" if attempts >= 3 or manual else "programmato"
            retry_at=self.whatsapp_next_retry_at(attempts) if next_status=="programmato" else msg["scheduled_at"]
            c.execute("""UPDATE whatsapp_messages SET status=?, scheduled_at=?, attempts=?, last_error=?, last_attempt_at=?, template_name=?, language_code=?, recipient_phone=?, payload_json=?, updated_at=? WHERE id=?""",(next_status,retry_at,attempts,error,attempt_stamp,template,language,phone,json.dumps(payload_obj,ensure_ascii=False),now(),msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(error,p["id"]))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(p["id"],"WhatsApp ringraziamento",f"Errore: {error}",user_id,now()))
            print(f"[WHATSAPP] pratica_id={p['id']} message_row={msg_id} esito=ERRORE tentativi={attempts} prossimo_stato={next_status} endpoint={endpoint} destinatario=+{phone} template={template} lingua={language} errore={error}", flush=True)
            return False,error

    def process_whatsapp_queue(self,limit=20):
        results=[]
        with db() as c:
            stale=(datetime.now()-timedelta(minutes=30)).isoformat(timespec="seconds")
            c.execute("UPDATE whatsapp_messages SET status='programmato', scheduled_at=?, updated_at=? WHERE status='in_invio' AND attempts<3 AND (last_attempt_at IS NULL OR last_attempt_at<=?)",(now(),now(),stale))
            due=c.execute("SELECT id FROM whatsapp_messages WHERE status='programmato' AND scheduled_at<=? ORDER BY scheduled_at LIMIT ?",(now(),limit)).fetchall()
            for row in due:
                stamp=now()
                changed=c.execute("UPDATE whatsapp_messages SET status='in_invio', updated_at=? WHERE id=? AND status='programmato'",(stamp,row["id"])).rowcount
                if not changed:
                    continue
                ok,msg=self.send_whatsapp_message(c,row["id"],manual=False,user_id=None)
                results.append({"id":row["id"],"ok":ok,"message":msg})
        return results

    def whatsapp_cron_authorized(self):
        secret=os.environ.get("WHATSAPP_CRON_SECRET","").strip()
        if not secret:
            return False, "variabile ambiente WHATSAPP_CRON_SECRET assente o vuota"
        qs=parse_qs(urlparse(self.path).query)
        query_secret=((qs.get("secret") or [""])[0] or "").strip()
        header_secret=(self.headers.get("X-Cron-Secret","") or "").strip()
        provided=query_secret or header_secret
        source="query" if query_secret else ("header" if header_secret else "nessuna")
        if not provided:
            return False, "parametro ?secret= mancante e header X-Cron-Secret mancante"
        if provided in ("<WHATSAPP_CRON_SECRET>", "WHATSAPP_CRON_SECRET"):
            return False, "nel Cron Job è rimasto il placeholder <WHATSAPP_CRON_SECRET>: devi sostituirlo con il valore reale della variabile"
        if not hmac.compare_digest(provided,secret):
            return False, f"secret errato ricevuto da {source}: lunghezza_ricevuta={len(provided)} lunghezza_attesa={len(secret)}"
        return True, f"secret corretto ricevuto da {source}"

    def whatsapp_cron(self):
        ok,reason=self.whatsapp_cron_authorized()
        if not ok:
            print(f"[WHATSAPP_CRON] 403 {reason}", flush=True)
            return self.send_json({"ok":False,"error":reason},403)
        print(f"[WHATSAPP_CRON] autorizzato: {reason}", flush=True)
        results=self.process_whatsapp_queue()
        print(f"[WHATSAPP_CRON] completato processed={len(results)} results={json.dumps(results,ensure_ascii=False)}", flush=True)
        return self.send_json({"ok":True,"processed":len(results),"results":results})

    def whatsapp_webhook_verify(self):
        qs=parse_qs(urlparse(self.path).query)
        mode=(qs.get("hub.mode") or [""])[0]
        token=(qs.get("hub.verify_token") or [""])[0]
        challenge=(qs.get("hub.challenge") or [""])[0]
        expected=os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN","").strip()
        if mode=="subscribe" and expected and hmac.compare_digest(token,expected):
            print("[WHATSAPP_WEBHOOK] verifica ok", flush=True)
            return self.send_text(challenge)
        print(f"[WHATSAPP_WEBHOOK] verifica fallita mode={mode} token_presente={bool(token)}", flush=True)
        return self.send_text("forbidden",403)

    def whatsapp_webhook_receive(self):
        size=int(self.headers.get("Content-Length",0))
        raw=self.rfile.read(size).decode("utf-8","replace")
        print(f"[WHATSAPP_WEBHOOK] payload={raw}", flush=True)
        try:
            data=json.loads(raw or "{}")
        except Exception as exc:
            return self.send_json({"ok":False,"error":str(exc)},400)
        updates=[]
        with db() as c:
            for entry in data.get("entry",[]):
                for change in entry.get("changes",[]):
                    value=change.get("value",{})
                    for st in value.get("statuses",[]):
                        message_id=st.get("id","")
                        status=st.get("status","")
                        mapped={"sent":"accettato_da_meta","delivered":"consegnato","read":"letto","failed":"fallito"}.get(status)
                        if not message_id or not mapped:
                            continue
                        err=json.dumps(st.get("errors",""),ensure_ascii=False) if st.get("errors") else ""
                        stamp=now()
                        fields={"consegnato":"delivered_at","letto":"read_at","fallito":"failed_at"}.get(mapped)
                        if fields:
                            c.execute(f"UPDATE whatsapp_messages SET status=?, {fields}=?, last_error=?, response_json=?, updated_at=? WHERE message_id=?",(mapped,stamp,err,json.dumps(st,ensure_ascii=False),stamp,message_id))
                        else:
                            c.execute("UPDATE whatsapp_messages SET status=?, last_error=?, response_json=?, updated_at=? WHERE message_id=?",(mapped,err,json.dumps(st,ensure_ascii=False),stamp,message_id))
                        row=c.execute("SELECT practice_id FROM whatsapp_messages WHERE message_id=?",(message_id,)).fetchone()
                        if row:
                            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,created_at) VALUES(?,?,?,?)",(row["practice_id"],"WhatsApp stato",f"{self.whatsapp_status_label(mapped)} - message_id {message_id}",stamp))
                        updates.append({"message_id":message_id,"status":mapped})
                        print(f"[WHATSAPP_WEBHOOK] message_id={message_id} meta_status={status} stato={mapped} errore={err}", flush=True)
        return self.send_json({"ok":True,"updates":updates})

    def sync_voucher(self,c,pid,d):
        existing=c.execute("SELECT * FROM veterinarian_vouchers WHERE practice_id=?",(pid,)).fetchone()
        wants_voucher = d.get("voucher_requested") == "Si" and d.get("veterinarian_id")
        if d.get("voucher_requested") == "Si" and not d.get("veterinarian_id") and d.get("clinic_name"):
            clinic=d.get("clinic_name","").strip()
            doctor=d.get("veterinarian_name","").strip()
            vet=c.execute("SELECT id FROM veterinarians WHERE UPPER(clinic_name)=UPPER(?) AND COALESCE(doctor_name,'')=? AND active=1",(clinic,doctor)).fetchone()
            if not vet:
                stamp=now()
                cur=c.execute("INSERT INTO veterinarians(clinic_name,doctor_name,phone,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",(clinic,doctor,"","Inserito automaticamente da testo libero",stamp,stamp))
                d["veterinarian_id"]=cur.lastrowid
            else:
                d["veterinarian_id"]=vet["id"]
            wants_voucher = True
        if wants_voucher:
            vet_id=int(d["veterinarian_id"])
            if existing:
                if existing["status"] in ("Disponibile","Maturato"):
                    c.execute("UPDATE veterinarian_vouchers SET veterinarian_id=?, note=? WHERE id=?",(vet_id,"Buono da pratica",existing["id"]))
            else:
                c.execute("INSERT INTO veterinarian_vouchers(veterinarian_id,practice_id,status,created_at,note) VALUES(?,?,?,?,?)",(vet_id,pid,"Maturato",now(),"Buono da pratica"))
        elif existing and existing["status"] in ("Disponibile","Maturato"):
            c.execute("DELETE FROM veterinarian_vouchers WHERE id=?",(existing["id"],))

    def apply_used_voucher(self,c,pid,d,user_id=None):
        voucher_id=d.get("used_voucher_id")
        if not (d.get("use_voucher") == "Si" and d.get("service_type") == "Cremazione collettiva" and d.get("veterinarian_id") and voucher_id):
            return
        row=c.execute("SELECT * FROM veterinarian_vouchers WHERE id=? AND veterinarian_id=?",(voucher_id,d["veterinarian_id"])).fetchone()
        if not row:
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Uso buono veterinario","Buono non disponibile o già usato",user_id,now()))
            return
        if row["status"] != "Maturato":
            if row["status"] == "Usato" and (str(pid) in (row["note"] or "") or row["practice_id"] == pid):
                return
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Uso buono veterinario","Buono già usato su altra pratica",user_id,now()))
            return
        stamp=now()
        existing_for_practice=c.execute("SELECT id FROM veterinarian_vouchers WHERE practice_id=? AND id<>?",(pid,voucher_id)).fetchone()
        linked_practice = row["practice_id"] or (None if existing_for_practice else pid)
        c.execute("UPDATE veterinarian_vouchers SET status='Usato', used_at=?, practice_id=?, note=? WHERE id=?",(stamp,linked_practice,f"Usato per pratica {pid}",voucher_id))
        c.execute("UPDATE practices SET used_voucher_id=?, use_voucher='Si' WHERE id=?",(voucher_id,pid))
        c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Uso buono veterinario",f"Usato buono ID {voucher_id}",user_id,stamp))

    def find_client_duplicates(self,c,d):
        checks=[]; args=[]
        if d.get("owner_tax_code"):
            checks.append("UPPER(tax_code)=UPPER(?)"); args.append(d["owner_tax_code"])
        if d.get("owner_vat"):
            checks.append("UPPER(vat_number)=UPPER(?)"); args.append(d["owner_vat"])
        if d.get("owner_email"):
            checks.append("UPPER(email)=UPPER(?)"); args.append(d["owner_email"])
        phone_digits=only_digits(d.get("owner_phone"))
        if phone_digits:
            checks.append("REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(phone,''),' ',''),'+',''),'-',''),'.','') LIKE ?"); args.append(f"%{phone_digits[-8:]}")
        if d.get("owner_first_name") and d.get("owner_last_name"):
            checks.append("(UPPER(first_name)=UPPER(?) AND UPPER(last_name)=UPPER(?))"); args += [d["owner_first_name"], d["owner_last_name"]]
        if not checks:
            return []
        return c.execute(f"""SELECT id, first_name, last_name, company_name, phone, email, tax_code, vat_number, city, address
                             FROM clients WHERE {' OR '.join(checks)} ORDER BY updated_at DESC LIMIT 10""",args).fetchall()

    def create_client_from_practice_data(self,c,d):
        stamp=now()
        cur=c.execute("""INSERT INTO clients(first_name,last_name,company_name,phone,phone_2,email,tax_code,vat_number,street,city,province,zip,address,notes,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (d.get("owner_first_name"),d.get("owner_last_name"),d.get("owner_company"),d.get("owner_phone"),d.get("owner_phone_2"),d.get("owner_email"),d.get("owner_tax_code"),d.get("owner_vat"),d.get("owner_street"),d.get("owner_city"),d.get("owner_province"),d.get("owner_zip"),d.get("owner_address"),d.get("owner_notes"),stamp,stamp))
        return cur.lastrowid

    def duplicate_client_page(self,user,d,duplicates):
        rows=''.join(f'''<tr><td>{esc(((r["first_name"] or "")+" "+(r["last_name"] or "")).strip() or r["company_name"])}</td><td>{esc(r["phone"])}</td><td>{esc(r["email"])}</td><td>{esc(r["tax_code"] or r["vat_number"])}</td><td>{esc(r["city"] or r["address"])}</td><td>ID {r["id"]}</td></tr>''' for r in duplicates)
        hidden=''.join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k,v in d.items() if v is not None)
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Possibile cliente già presente</h1><div class="sub">Prima di creare una nuova anagrafica, controlla questi possibili duplicati.</div></div></div><section class="section"><div class="flash warning">Abbiamo trovato clienti con nome, telefono, email, codice fiscale o partita IVA simili. Puoi tornare alla pratica e usare la ricerca cliente, oppure confermare esplicitamente la creazione di un nuovo cliente.</div><div class="tablebox"><table><thead><tr><th>Cliente</th><th>Telefono</th><th>Email</th><th>CF / P.IVA</th><th>Città / indirizzo</th><th>ID</th></tr></thead><tbody>{rows}</tbody></table></div><div class="actions" style="margin-top:18px"><a class="btn ghost" href="/nuova">Torna e usa cliente esistente</a><form method="post" action="/nuova">{hidden}<input type="hidden" name="confirm_new_client" value="SI"><button class="btn">Conferma nuovo cliente</button></form></div></section></main>'''
        self.send_html(layout("Possibile duplicato cliente",body,user),409)

    def create_practice(self,user):
        f=self.form(); d=self.normalized_fields(f); stamp=now()
        error=self.validation_error(d)
        if error: return self.send_error(400, error)
        initial="Ritirato"
        with db() as c:
            if d.get("client_id"):
                exists=c.execute("SELECT id FROM clients WHERE id=?",(d["client_id"],)).fetchone()
                if not exists:
                    d["client_id"]=None
            if not d.get("client_id"):
                duplicates=self.find_client_duplicates(c,d)
                if duplicates and f.get("confirm_new_client") != "SI":
                    return self.duplicate_client_page(user,d,duplicates)
                d["client_id"]=self.create_client_from_practice_data(c,d)
            number=next_practice_code(c,d["service_type"])
            cols=list(d)+["practice_number","status","data_complete","created_at","updated_at","created_by"]
            values=list(d.values())+[number,initial,self.is_complete(d),stamp,stamp,user["id"]]
            marks=','.join('?' for _ in cols)
            cur=c.execute(f"INSERT INTO practices({','.join(cols)}) VALUES({marks})",values); pid=cur.lastrowid
            self.sync_voucher(c,pid,d)
            self.apply_used_voucher(c,pid,d,user["id"])
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Creazione pratica",initial,user["id"],stamp))
        self.redirect(f"/pratiche/{pid}")

    def practice(self,user,pid):
        with db() as c:
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            history=c.execute("SELECT h.*,u.display_name FROM practice_history h LEFT JOIN users u ON u.id=h.user_id WHERE practice_id=? ORDER BY h.created_at DESC",(pid,)).fetchall()
            whatsapp_msg=c.execute("SELECT * FROM whatsapp_messages WHERE practice_id=? ORDER BY created_at DESC LIMIT 1",(pid,)).fetchone()
        if not p:return self.send_error(404)
        options=''.join(f'<option {"selected" if s==p["status"] else ""}>{esc(s)}</option>' for s in STATES)
        payment_value = p["payment_status"] if "payment_status" in p.keys() and p["payment_status"] else "Da saldare"
        payment_cls = {"Da saldare":"pay-yellow","Acconto":"pay-blue","Pagato":"pay-green"}.get(payment_value,"")
        catalog_value = "Si" if "send_catalog" in p.keys() and p["send_catalog"] else "No"
        invoice_value = p["invoice_number"] if "invoice_number" in p.keys() and p["invoice_number"] else ""
        no_whatsapp_checked = "checked" if "no_whatsapp_message" in p.keys() and p["no_whatsapp_message"] == "Si" else ""
        no_whatsapp_note = '<div class="flash warning">Invio automatico WhatsApp disattivato per questa pratica.</div>' if no_whatsapp_checked else ''
        whatsapp_sent = p["whatsapp_thanks_sent_at"] if "whatsapp_thanks_sent_at" in p.keys() and p["whatsapp_thanks_sent_at"] else ""
        whatsapp_error = p["whatsapp_thanks_last_error"] if "whatsapp_thanks_last_error" in p.keys() and p["whatsapp_thanks_last_error"] else ""
        current_payload=self.whatsapp_payload_for_practice(p)
        latest_accepted = bool(whatsapp_msg and whatsapp_msg["status"] in ("accettato_da_meta","consegnato","letto"))
        whatsapp_button = "REINVIA WHATSAPP" if latest_accepted or whatsapp_sent else "INVIA WHATSAPP SUBITO"
        if whatsapp_msg:
            scheduled = whatsapp_msg["scheduled_at"] or ""
            status_label = self.whatsapp_status_label(whatsapp_msg["status"])
            template_show = whatsapp_msg["template_name"] or current_payload["template"]["name"]
            recipient_show = whatsapp_msg["recipient_phone"] or current_payload["to"]
            last_attempt = whatsapp_msg["last_attempt_at"] or ""
            message_id_show = whatsapp_msg["message_id"] or ""
            msg_error = whatsapp_msg["last_error"] or whatsapp_error
        else:
            scheduled = ""
            status_label = "Non programmato"
            template_show = current_payload["template"]["name"]
            recipient_show = current_payload["to"]
            last_attempt = ""
            message_id_show = ""
            msg_error = whatsapp_error
        cancel_form = f'''<form method="post" action="/pratiche/{pid}/whatsapp-annulla" onsubmit="return confirm('Annullare l invio WhatsApp programmato?')"><button class="btn ghost">Annulla invio programmato</button></form>''' if whatsapp_msg and whatsapp_msg["status"]=="programmato" else ""
        whatsapp_block = f'''<div class="section"><h2>WhatsApp ringraziamento</h2><div class="kvs"><div class="kv"><small>Invio programmato per</small>{esc(scheduled) if scheduled and status_label=="Programmato" else '<span class="sub">Non programmato</span>'}</div><div class="kv"><small>Stato attuale</small><b>{esc(status_label)}</b></div><div class="kv"><small>Template</small>{esc(template_show)}</div><div class="kv"><small>Destinatario</small>{('+'+esc(recipient_show)) if recipient_show else '<span class="sub">Telefono mancante</span>'}</div><div class="kv"><small>Ultimo tentativo</small>{esc(last_attempt) or '<span class="sub">Nessuno</span>'}</div><div class="kv"><small>Message ID</small>{esc(message_id_show) or '<span class="sub">Non disponibile</span>'}</div></div>{f'<div class="flash warning">{esc(msg_error)}</div>' if msg_error else ''}<div class="actions" style="margin-top:14px"><a class="btn" href="/pratiche/{pid}/whatsapp-conferma">{whatsapp_button}</a>{cancel_form}</div></div>'''
        animal2_block = f'<div class="kv"><small>Secondo animale</small>{esc(p["animal2_name"])}<br>{esc(p["animal2_species"])} {esc(p["animal2_weight"])} kg</div>' if "animal2_name" in p.keys() and p["animal2_name"] else ""
        payment_options=''.join(f'<option {"selected" if s==payment_value else ""}>{esc(s)}</option>' for s in PAYMENT_STATES)
        hist=''.join(f'<div class="event"><b>{esc(h["event_type"])}</b><br><span>{esc(h["new_value"])}</span><br><small class="sub">{esc(h["created_at"].replace("T"," "))} - {esc(h["display_name"])}</small></div>' for h in history)
        ddt=f'DDT n. {p["ddt_number"]} del {esc(p["ddt_date"])}' if p["ddt_number"] else 'Numero DDT non ancora assegnato'
        if p['ddt_pdf']:
            share_token = p["ddt_share_token"] if "ddt_share_token" in p.keys() and p["ddt_share_token"] else secrets.token_urlsafe(18)
            if not ("ddt_share_token" in p.keys() and p["ddt_share_token"]):
                with db() as c:
                    c.execute("UPDATE practices SET ddt_share_token=? WHERE id=?",(share_token,pid))
            share_url = f"/pubblici/ddt/{share_token}.pdf"
            pdf_filename = safe_pdf_filename(p["animal_name"] or p["practice_number"], "pratica")
            pdf_block = f'<div class="flash">Il PDF definitivo e stato archiviato.</div><div class="actions"><a class="btn" href="/pratiche/{pid}/ddt.pdf">Apri / stampa DDT</a><a class="btn ghost" href="/pratiche/{pid}/ddt-download.pdf">Salva PDF sul dispositivo</a><button class="btn ghost" type="button" onclick="sharePracticePdf(\'{share_url}\', \'DDT pratica {esc(p["practice_number"])}\', \'{esc(pdf_filename)}\')">Condividi PDF</button><button class="btn ghost" type="button" onclick="navigator.clipboard.writeText(new URL(\'{share_url}\', window.location.href).toString()).then(()=>alert(\'Link pubblico PDF copiato\'))">Copia link PDF</button></div><p class="sub">Puoi scaricare il documento sul PC o sul telefono. Il link condiviso apre solo questo PDF, non il gestionale.</p>'
        else:
            final_action = f'<form method="post" action="/pratiche/{pid}/ddt"><button class="btn">Assegna numero e genera PDF definitivo</button></form>' if p['data_complete'] else '<div class="flash warning">Pratica salvata. Potrai assegnare il numero DDT e generare il PDF definitivo quando avrai completato i dati obbligatori.</div>'
            draft_filename = safe_pdf_filename((p["animal_name"] or p["practice_number"]) + "_BOZZA", "bozza")
            pdf_block = f'<div class="actions"><a class="btn ghost" href="/pratiche/{pid}">Salva pratica</a><a class="btn ghost" href="/pratiche/{pid}/ddt-bozza.pdf">Apri bozza PDF</a><a class="btn ghost" href="/pratiche/{pid}/ddt-bozza-download.pdf">Salva bozza sul dispositivo</a><button class="btn ghost" type="button" onclick="sharePracticePdf(\'/pratiche/{pid}/ddt-bozza.pdf\', \'Bozza DCS pratica {esc(p["practice_number"])}\', \'{esc(draft_filename)}\')">Condividi bozza PDF</button>{final_action}</div><p class="sub">La pratica resta salvata in archivio. Il DDT numerato puo essere generato anche in un secondo momento, per esempio alla fine della pratica.</p>'
        body=f"""
        <main class="wrap">
          <div class="titlebar"><div><h1>{esc(p['practice_number'])} - {esc(p['animal_name'] or 'Animale da inserire')}</h1><div class="sub">Creata il {esc(p['created_at'].replace('T',' '))}</div></div><div class="actions"><a class="btn ghost" href="/pratiche/{pid}/modifica">Modifica dati</a><a class="btn ghost" href="/pratiche/{pid}/firma">Firma su telefono</a></div></div>
          {'' if p['data_complete'] else '<div class="flash warning">Questa pratica contiene ancora dati da completare.</div>'}
          <section class="grid practice-layout">
            <div class="grid">
              <div class="section"><h2>Riepilogo</h2><div class="kvs"><div class="kv"><small>Stato</small><b>{esc(p['status'])}</b><br><span class="badge {payment_cls}">{esc(payment_value)}</span></div><div class="kv"><small>Speditore</small>{esc((p['owner_first_name'] or '')+' '+(p['owner_last_name'] or ''))}<br>{esc(p['owner_phone'])}{('<br>'+esc(p['owner_phone_2'])) if 'owner_phone_2' in p.keys() and p['owner_phone_2'] else ''}</div><div class="kv"><small>Animale</small>{esc(p['species'])} - {esc(p['breed'])}<br>{esc(p['estimated_weight'])} kg</div>{animal2_block}<div class="kv"><small>Sede</small><b>{esc(p['destination_branch'])}</b></div><div class="kv"><small>Origine</small><b>{esc(p['request_origin'])}</b></div><div class="kv"><small>Veterinario</small>{esc(p['clinic_name'])}<br>{esc(p['veterinarian_name'])}</div><div class="kv"><small>Catalogo urna</small><b>{esc(catalog_value)}</b></div><div class="kv"><small>Fattura</small>{esc(invoice_value) or '<span class="sub">Non inserita</span>'}</div></div></div>
              <div class="section"><h2>Firma proprietario</h2><p class="sub">{'Firma salvata.' if p['signature_data'] else 'Firma non ancora salvata.'}</p><a class="btn ghost" href="/pratiche/{pid}/firma">Apri firma</a></div>
              <div class="section"><h2>Stati pratica</h2>{no_whatsapp_note}<form method="post" action="/pratiche/{pid}/stato"><div class="fields"><div class="field"><label>Avanzamento</label><select name="status">{options}</select></div><div class="field"><label><input type="checkbox" name="no_whatsapp_message" value="Si" {no_whatsapp_checked} style="width:auto"> NO MESSAGGIO</label><small class="sub">Se spuntato, quando la pratica passa a Consegnato non parte il WhatsApp automatico.</small></div><div class="field"><label>Pagamento</label><select name="payment_status">{payment_options}</select></div><div class="field"><label>Numero fattura</label><input name="invoice_number" value="{esc(invoice_value)}" placeholder="Da inserire quando risulta pagato"></div></div><button class="btn" style="margin-top:12px">Aggiorna stati</button></form></div>
              {whatsapp_block}
              <div class="section"><h2>Documento DCS / DDT</h2><p>{ddt}</p>{pdf_block}</div>
              <div class="section"><h2>Note</h2><p>{esc(p['notes']) or '<span class="sub">Nessuna nota.</span>'}</p></div>
              <div class="section danger"><h2>Sposta nel Cestino</h2><p class="danger-note">La pratica non verra cancellata definitivamente: verra nascosta da Dashboard e Archivio e potrai ripristinarla dal Cestino.</p><form method="post" action="/pratiche/{pid}/elimina" onsubmit="return confirm('Spostare questa pratica nel Cestino? Potrai ripristinarla in seguito.')"><button class="btn danger-btn" style="margin-top:12px">Sposta nel Cestino</button></form></div>
            </div>
            <aside class="section"><h2>Storico</h2><div class="timeline">{hist}</div></aside>
          </section>
        </main>"""
        self.send_html(layout(p["practice_number"],body,user))

    def edit_page(self,user,pid):
        with db() as c:p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p:return self.send_error(404)
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Modifica {esc(p['practice_number'])}</h1><div class="sub">Completa o correggi i dati della pratica.</div></div><div class="actions"><button class="btn" form="practiceForm">Salva modifiche</button></div></div><form method="post" id="practiceForm"><div class="grid form-grid">{self.fields_html(p)}</div><div class="actions" style="margin-top:18px"><button class="btn">Salva modifiche</button><a class="btn ghost" href="/pratiche/{pid}">Annulla</a></div></form></main>'''
        self.send_html(layout("Modifica pratica",body,user))

    def signature_page(self,user,pid):
        with db() as c:p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p:return self.send_error(404)
        owner=esc(((p["owner_first_name"] or "")+" "+(p["owner_last_name"] or "")).strip())
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Firma proprietario</h1><div class="sub">{owner} - pratica {esc(p['practice_number'])}</div></div></div><section class="section"><p class="sub">Fai firmare qui il proprietario con il dito. La firma verrà salvata nella pratica e inserita nel PDF DDT.</p><form method="post" id="signatureForm"><canvas class="signature-pad" id="pad"></canvas><input type="hidden" name="signature_data" id="signatureData"><div class="actions" style="margin-top:14px"><button class="btn" type="submit">Salva firma</button><button class="btn ghost" type="button" id="clearPad">Cancella</button><a class="btn ghost" href="/pratiche/{pid}">Annulla</a></div></form></section><script>
const canvas=document.getElementById('pad'),ctx=canvas.getContext('2d');let drawing=false,last=null;
function resize(){{const r=canvas.getBoundingClientRect(),d=window.devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);ctx.lineWidth=3;ctx.lineCap='round';ctx.strokeStyle='#1f1f1f';}}
function pos(e){{const r=canvas.getBoundingClientRect(),t=e.touches?e.touches[0]:e;return {{x:t.clientX-r.left,y:t.clientY-r.top}};}}
function start(e){{drawing=true;last=pos(e);e.preventDefault();}}
function move(e){{if(!drawing)return;const p=pos(e);ctx.beginPath();ctx.moveTo(last.x,last.y);ctx.lineTo(p.x,p.y);ctx.stroke();last=p;e.preventDefault();}}
function end(e){{drawing=false;e.preventDefault();}}
resize();window.addEventListener('resize',resize);canvas.addEventListener('mousedown',start);canvas.addEventListener('mousemove',move);canvas.addEventListener('mouseup',end);canvas.addEventListener('mouseleave',end);canvas.addEventListener('touchstart',start,{{passive:false}});canvas.addEventListener('touchmove',move,{{passive:false}});canvas.addEventListener('touchend',end,{{passive:false}});
document.getElementById('clearPad').onclick=()=>ctx.clearRect(0,0,canvas.width,canvas.height);
document.getElementById('signatureForm').onsubmit=()=>{{document.getElementById('signatureData').value=canvas.toDataURL('image/png');}};
</script></main>'''
        self.send_html(layout("Firma proprietario",body,user))

    def signature_submit(self,user,pid):
        signature=self.form().get("signature_data","")
        if not signature.startswith("data:image/png;base64,"):
            return self.send_error(400, "Firma non valida")
        with db() as c:
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            if not p: return self.send_error(404)
            c.execute("UPDATE practices SET signature_data=?,updated_at=? WHERE id=?",(signature,now(),pid))
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            if p["ddt_pdf"]:
                generate_ddt(p, ASSETS / "DCS_NUOVO.pdf", DDT_DIR / p["ddt_pdf"])
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Firma proprietario","Firma salvata",user["id"],now()))
        self.redirect(f"/pratiche/{pid}")

    def edit_submit(self,user,pid):
        d=self.normalized_fields(self.form()); stamp=now(); assignments=','.join(f'{k}=?' for k in d)
        error=self.validation_error(d)
        if error: return self.send_error(400, error)
        with db() as c:
            c.execute(f"UPDATE practices SET {assignments},data_complete=?,updated_at=? WHERE id=?",list(d.values())+[self.is_complete(d),stamp,pid])
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            wanted_prefix,_=practice_code_prefix(d["service_type"])
            current_number=p["practice_number"] or ""
            if not p["ddt_number"] and wanted_prefix in ("CR-","SM-") and not current_number.startswith(wanted_prefix):
                new_number=next_practice_code(c,d["service_type"])
                c.execute("UPDATE practices SET practice_number=? WHERE id=?",(new_number,pid))
                c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Cambio codice pratica",current_number,new_number,user["id"],stamp))
            self.sync_voucher(c,pid,d)
            self.apply_used_voucher(c,pid,d,user["id"])
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Dati aggiornati","Pratica modificata",user["id"],stamp))
        self.redirect(f"/pratiche/{pid}")

    def change_state(self,user,pid):
        f=self.form(); new=f.get("status",""); payment=f.get("payment_status","Da saldare"); invoice=f.get("invoice_number","").strip(); no_whatsapp="Si" if f.get("no_whatsapp_message")=="Si" else ""
        if new not in STATES or payment not in PAYMENT_STATES:return self.send_error(400)
        with db() as c:
            old=c.execute("SELECT status,payment_status,invoice_number,no_whatsapp_message FROM practices WHERE id=?",(pid,)).fetchone()
            if not old:return self.send_error(404)
            old_payment=old["payment_status"] or "Da saldare"
            c.execute("UPDATE practices SET status=?,payment_status=?,invoice_number=?,no_whatsapp_message=?,updated_at=? WHERE id=?",(new,payment,invoice,no_whatsapp,now(),pid))
            new_value=f'{new} + {payment}' + (f' - Fattura {invoice}' if invoice else '') + (" - NO MESSAGGIO" if no_whatsapp else "")
            old_value=f'{old["status"]} + {old_payment}' + (f' - Fattura {old["invoice_number"]}' if old["invoice_number"] else '') + (" - NO MESSAGGIO" if old["no_whatsapp_message"]=="Si" else "")
            c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Cambio stati",old_value,new_value,user["id"],now()))
            if no_whatsapp:
                self.cancel_whatsapp_scheduled(c,pid,user["id"],"NO MESSAGGIO selezionato")
            elif old["status"] == "Consegnato" and new != "Consegnato":
                self.cancel_whatsapp_scheduled(c,pid,user["id"],"Pratica spostata da Consegnato a un altro stato")
            elif old["status"] != "Consegnato" and new == "Consegnato":
                self.schedule_whatsapp_thanks(c,pid,user["id"])
        self.redirect(f"/pratiche/{pid}")

    def resend_whatsapp(self,user,pid):
        if user["role"] != "admin":
            return self.send_error(403)
        f=self.form()
        if f.get("confirm_send") != "SI":
            return self.send_error(400, "Conferma invio WhatsApp mancante")
        with db() as c:
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            if not p: return self.send_error(404)
            active=c.execute("SELECT * FROM whatsapp_messages WHERE practice_id=? AND status IN ('programmato','in_invio') ORDER BY created_at DESC LIMIT 1",(pid,)).fetchone()
            if active:
                msg_id=active["id"]
                c.execute("UPDATE whatsapp_messages SET status='in_invio', manual=1, updated_at=? WHERE id=?",(now(),msg_id))
            else:
                payload=self.whatsapp_payload_for_practice(p)
                stamp=now()
                cur=c.execute("""INSERT INTO whatsapp_messages(practice_id,scheduled_at,status,attempts,template_name,language_code,recipient_phone,payload_json,manual,created_at,updated_at)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(pid,stamp,"in_invio",0,payload["template"]["name"],payload["template"]["language"]["code"],payload["to"],json.dumps(payload,ensure_ascii=False),1,stamp,stamp))
                msg_id=cur.lastrowid
            ok,msg=self.send_whatsapp_message(c,msg_id,manual=True,user_id=user["id"])
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Invio WhatsApp manuale",msg,user["id"],now()))
        self.redirect(f"/pratiche/{pid}")

    def cancel_whatsapp_manual(self,user,pid):
        with db() as c:
            if not c.execute("SELECT id FROM practices WHERE id=?",(pid,)).fetchone():
                return self.send_error(404)
            self.cancel_whatsapp_scheduled(c,pid,user["id"],"Invio programmato annullato manualmente")
        self.redirect(f"/pratiche/{pid}")

    def whatsapp_confirm_page(self,user,pid):
        if user["role"] != "admin":
            return self.send_error(403)
        with db() as c:
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            latest=c.execute("SELECT * FROM whatsapp_messages WHERE practice_id=? AND status IN ('accettato_da_meta','consegnato','letto') ORDER BY COALESCE(sent_at,created_at) DESC LIMIT 1",(pid,)).fetchone()
        if not p: return self.send_error(404)
        payload=self.whatsapp_payload_for_practice(p)
        phone=payload["to"] or "Telefono mancante"
        template=payload["template"]["name"]
        nome_cliente=payload["template"]["components"][0]["parameters"][0]["text"]
        nome_animale=payload["template"]["components"][0]["parameters"][1]["text"]
        already = latest is not None
        warning = '<div class="flash warning"><b>Attenzione:</b> questo cliente ha già ricevuto o potrebbe aver già ricevuto il messaggio. Conferma solo se vuoi reinviarlo.</div>' if already else ''
        btn = "REINVIA WHATSAPP" if already else "INVIA WHATSAPP SUBITO"
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>{btn}</h1><div class="sub">Conferma invio template WhatsApp per la pratica {esc(p['practice_number'])}</div></div><a class="btn ghost" href="/pratiche/{pid}">Torna alla pratica</a></div>{warning}<section class="section"><h2>Dati invio</h2><div class="kvs"><div class="kv"><small>Destinatario</small><b>+{esc(phone)}</b></div><div class="kv"><small>Template</small><b>{esc(template)}</b></div><div class="kv"><small>Lingua</small><b>{esc(payload['template']['language']['code'])}</b></div><div class="kv"><small>Nome cliente</small><b>{esc(nome_cliente)}</b></div><div class="kv"><small>Nome animale</small><b>{esc(nome_animale)}</b></div></div><form method="post" action="/pratiche/{pid}/whatsapp" onsubmit="return confirm('Confermi invio WhatsApp a +{esc(phone)} con template {esc(template)}?')"><input type="hidden" name="confirm_send" value="SI"><button class="btn" style="margin-top:18px">{btn}</button></form></section></main>'''
        self.send_html(layout("Conferma WhatsApp",body,user))

    def public_ddt(self,token):
        with db() as c:
            p=c.execute("SELECT ddt_pdf,animal_name,practice_number FROM practices WHERE ddt_share_token=?",(token,)).fetchone()
        if not p or not p["ddt_pdf"]: return self.send_error(404)
        path=DDT_DIR / p["ddt_pdf"]
        if not path.exists(): return self.send_error(404)
        return self.send_pdf(path, safe_pdf_filename(p["animal_name"] or p["practice_number"], "pratica"))

    def delete_warning_page(self,user,pid):
        with db() as c:
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p:
            return self.error_page("Pratica non trovata", "La pratica richiesta non esiste o e gia stata eliminata definitivamente.", "/pratiche")
        if "deleted_at" in p.keys() and p["deleted_at"]:
            return self.redirect("/cestino")
        body=f'''<main class="wrap"><section class="section danger"><h1>Sposta pratica nel Cestino</h1><p class="danger-note">La pratica {esc(p["practice_number"])} non verra cancellata definitivamente. Sara nascosta da Dashboard e Archivio e potra essere ripristinata dal Cestino.</p><div class="actions"><form method="post" action="/pratiche/{pid}/elimina" onsubmit="return confirm('Spostare questa pratica nel Cestino?')"><button class="btn danger-btn">Sposta nel Cestino</button></form><a class="btn ghost" href="/pratiche/{pid}">Annulla</a></div></section></main>'''
        self.send_html(layout("Sposta nel Cestino", body, user))

    def trash_page(self,user):
        with db() as c:
            rows=c.execute("SELECT * FROM practices WHERE deleted_at IS NOT NULL AND deleted_at<>'' ORDER BY deleted_at DESC, id DESC").fetchall()
        if rows:
            body_rows=[]
            for r in rows:
                animal='/' if (r['service_type'] or '') == 'Cremazione collettiva' else esc(r['animal_name'] or 'Da inserire')
                owner=esc(((r['owner_first_name'] or '')+' '+(r['owner_last_name'] or '')).strip())
                body_rows.append(f'''<tr><td>{esc(date_it(r["pickup_date"] or r["created_at"]))}</td><td><a href="/pratiche/{r["id"]}"><b>{esc(r["practice_number"])}</b></a><br><small>Cestinata il {esc((r["deleted_at"] or "").replace("T"," "))}</small></td><td>{animal}</td><td>{owner}</td><td>{esc(r["clinic_name"] or "-")}</td><td><div class="actions"><form method="post" action="/pratiche/{r["id"]}/ripristina" onsubmit="return confirm('Ripristinare questa pratica?')"><button class="btn ghost">Ripristina</button></form><form method="post" action="/pratiche/{r["id"]}/elimina-definitiva" onsubmit="return confirm('Sei sicuro di voler eliminare definitivamente questa pratica?') && confirm('Questa operazione e irreversibile.')"><input type="hidden" name="confirm_delete" value="ELIMINA DEFINITIVAMENTE"><button class="btn danger-btn">Elimina definitivamente</button></form></div></td></tr>''')
            content=f'''<div class="trash-note">Le pratiche nel Cestino non compaiono in Dashboard e Archivio. Ripristinale se sono state eliminate per errore.</div><div class="tablebox"><table><thead><tr><th>Data recupero</th><th>Pratica</th><th>Animale</th><th>Speditore</th><th>Veterinario</th><th>Azioni</th></tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>'''
        else:
            content='<section class="section empty-state">Il Cestino e vuoto.</section>'
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Cestino</h1><div class="sub">Pratiche eliminate ma ancora recuperabili.</div></div><a class="btn ghost" href="/archivio/pratiche">Torna all archivio</a></div>{content}</main>'''
        self.send_html(layout("Cestino", body, user))

    def delete_practice(self,user,pid):
        stamp=now()
        try:
            with db() as c:
                p=c.execute("SELECT id,deleted_at FROM practices WHERE id=?",(pid,)).fetchone()
                if not p:
                    return self.error_page("Pratica non trovata", "La pratica non esiste o e gia stata eliminata definitivamente.", "/pratiche")
                if p["deleted_at"]:
                    return self.redirect("/cestino")
                c.execute("UPDATE practices SET deleted_at=?, deleted_by=?, updated_at=? WHERE id=?",(stamp,user["id"],stamp,pid))
                c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,note,user_id,created_at) VALUES(?,?,?,?,?,?,?)",(pid,"Cestino","Attiva","Cestinata","Pratica spostata nel Cestino",user["id"],stamp))
            self.redirect("/cestino")
        except Exception as exc:
            print("[DELETE_PRACTICE] errore spostamento cestino", flush=True)
            print(traceback.format_exc(), flush=True)
            return self.error_page("Errore eliminazione", f"Non sono riuscito a spostare la pratica nel Cestino. Nessun dato e stato cancellato. Dettaglio: {type(exc).__name__}: {exc}", f"/pratiche/{pid}")

    def restore_practice(self,user,pid):
        stamp=now()
        try:
            with db() as c:
                p=c.execute("SELECT id,deleted_at FROM practices WHERE id=?",(pid,)).fetchone()
                if not p:
                    return self.error_page("Pratica non trovata", "La pratica non esiste o e stata eliminata definitivamente.", "/cestino")
                c.execute("UPDATE practices SET deleted_at=NULL, deleted_by=NULL, updated_at=? WHERE id=?",(stamp,pid))
                c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,note,user_id,created_at) VALUES(?,?,?,?,?,?,?)",(pid,"Ripristino","Cestinata","Attiva","Pratica ripristinata dal Cestino",user["id"],stamp))
            self.redirect(f"/pratiche/{pid}")
        except Exception as exc:
            print("[RESTORE_PRACTICE] errore ripristino", flush=True)
            print(traceback.format_exc(), flush=True)
            return self.error_page("Errore ripristino", f"Non sono riuscito a ripristinare la pratica. Dettaglio: {type(exc).__name__}: {exc}", "/cestino")

    def permanent_delete_practice(self,user,pid):
        f=self.form()
        if f.get("confirm_delete","").strip().upper() != "ELIMINA DEFINITIVAMENTE":
            return self.error_page("Conferma mancante", "Per eliminare definitivamente serve la conferma corretta.", "/cestino")
        pdf_names=[]
        try:
            with db() as c:
                p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
                if not p:
                    return self.redirect("/cestino")
                if not ("deleted_at" in p.keys() and p["deleted_at"]):
                    return self.error_page("Operazione non consentita", "La pratica deve prima essere spostata nel Cestino.", f"/pratiche/{pid}")
                if p["ddt_pdf"]:
                    pdf_names.append(p["ddt_pdf"])
                if p["practice_number"]:
                    pdf_names.append(f"DCS-BOZZA-{p['practice_number']}.pdf")
                c.execute("DELETE FROM whatsapp_messages WHERE practice_id=?",(pid,))
                c.execute("UPDATE veterinarian_vouchers SET practice_id=NULL, note=COALESCE(note,'') || ' - pratica eliminata definitivamente' WHERE practice_id=?",(pid,))
                c.execute("DELETE FROM practice_history WHERE practice_id=?",(pid,))
                c.execute("DELETE FROM practices WHERE id=?",(pid,))
            for name in pdf_names:
                try:
                    path=(DDT_DIR / name).resolve()
                    if str(path).startswith(str(DDT_DIR.resolve())) and path.exists():
                        path.unlink()
                except Exception:
                    print(f"[PERMANENT_DELETE] PDF non eliminato: {name}", flush=True)
                    print(traceback.format_exc(), flush=True)
            self.redirect("/cestino")
        except Exception as exc:
            print("[PERMANENT_DELETE] errore cancellazione definitiva", flush=True)
            print(traceback.format_exc(), flush=True)
            return self.error_page("Errore cancellazione definitiva", f"La cancellazione definitiva non e stata completata. Il database e stato riportato allo stato precedente. Dettaglio: {type(exc).__name__}: {exc}", "/cestino")

    def assign_ddt(self,user,pid):
        stamp=now(); date=datetime.now().date().isoformat()
        with db() as c:
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            if not p:return self.send_error(404)
            if p["ddt_number"]:return self.redirect(f"/pratiche/{pid}")
            if not p["data_complete"]: return self.error_page("Dati mancanti", "Completa i dati obbligatori prima di assegnare il numero DDT definitivo.", f"/pratiche/{pid}")
            number=next_number(c,"next_ddt_number")
            pdf_name=f"DDT-{number:06d}-{p['practice_number']}.pdf"
            share_token = secrets.token_urlsafe(18)
            c.execute("UPDATE practices SET ddt_number=?,ddt_date=?,ddt_pdf=?,ddt_share_token=?,updated_at=? WHERE id=?",(number,date,pdf_name,share_token,stamp,pid))
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            try:
                generate_ddt(p, ASSETS / "DCS_NUOVO.pdf", DDT_DIR / pdf_name)
            except Exception as exc:
                c.execute("UPDATE practices SET ddt_number=NULL,ddt_date=NULL,ddt_pdf=NULL WHERE id=?",(pid,))
                c.execute("UPDATE settings SET value=? WHERE key='next_ddt_number'",(str(number),))
                return self.pdf_error_page(exc, f"/pratiche/{pid}")
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Numero DDT assegnato",str(number),user["id"],stamp))
        self.redirect(f"/pratiche/{pid}")

    def download_ddt(self,user,pid,attachment=False):
        with db() as c: p=c.execute("SELECT ddt_pdf,animal_name,practice_number FROM practices WHERE id=?",(pid,)).fetchone()
        if not p or not p["ddt_pdf"]: return self.send_error(404)
        path=DDT_DIR / p["ddt_pdf"]
        if not path.exists(): return self.send_error(404)
        return self.send_pdf(path, safe_pdf_filename(p["animal_name"] or p["practice_number"], "pratica"), attachment=attachment)

    def draft_ddt(self,user,pid,attachment=False):
        with db() as c: p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p: return self.send_error(404)
        draft=dict(p)
        draft["ddt_number"]=""
        draft["ddt_date"]=draft["ddt_date"] or datetime.now().date().isoformat()
        path=DDT_DIR / f"DCS-BOZZA-{p['practice_number']}.pdf"
        try:
            generate_ddt(draft, ASSETS / "DCS_NUOVO.pdf", path)
        except Exception as exc:
            return self.pdf_error_page(exc, f"/pratiche/{pid}")
        return self.send_pdf(path, safe_pdf_filename((p["animal_name"] or p["practice_number"]) + "_BOZZA", "bozza"), attachment=attachment)

    def send_pdf(self,path, filename=None, attachment=False):
        payload=path.read_bytes()
        self.send_response(200); self.send_header("Content-Type","application/pdf")
        disposition="attachment" if attachment else "inline"
        self.send_header("Content-Disposition",f'{disposition}; filename="{filename or path.name}"')
        self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)


if __name__ == "__main__":
    init_db()
    print(f"Pet Paradise Manager: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), App).serve_forever()
