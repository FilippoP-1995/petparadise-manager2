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
from urllib.parse import parse_qs, quote, urlparse

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
            "price_cast": "TEXT", "price_holiday": "TEXT", "price_accessories": "TEXT",
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
            "whatsapp_thanks_sent_at": "TEXT",
            "whatsapp_thanks_last_error": "TEXT",
            "no_whatsapp_message": "TEXT"
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
                "SELECT 1 FROM veterinarians WHERE UPPER(clinic_name)=UPPER(?) AND COALESCE(notes,'') LIKE ?",
                (clinic, f"%{city}%"),
            ).fetchone()
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
:root{--ink:#24312c;--muted:#6e7b75;--brand:#a74045;--brand2:#7f3035;--paper:#fff;--bg:#f4f1ed;--line:#ded8d1;--green:#39745b;--gold:#a87926}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:inherit;text-decoration:none}.top{height:68px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:18px;padding:0 28px;position:sticky;top:0;z-index:5}.brand{font-weight:800;font-size:19px;color:var(--brand)}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:1.5px}.nav{display:flex;gap:8px;margin-left:auto}.nav a{padding:9px 12px;border-radius:9px}.nav a:hover{background:#f3eeea}.wrap{max-width:1280px;margin:0 auto;padding:28px}.titlebar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px}h1{margin:0;font-size:28px}h2{font-size:18px;margin:0 0 15px}.sub{color:var(--muted)}.btn{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:10px;background:var(--brand);color:white;padding:11px 16px;font-weight:700;cursor:pointer}.btn:hover{background:var(--brand2)}.btn.ghost{background:white;color:var(--ink);border:1px solid var(--line)}.grid{display:grid;gap:16px}.stats{grid-template-columns:repeat(3,1fr)}.card{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:20px;box-shadow:0 3px 15px #4b39260a}.stat{display:flex;justify-content:space-between;align-items:center}.stat b{font-size:32px;color:var(--brand)}.badge{display:inline-flex;padding:5px 9px;border-radius:99px;background:#eee9e3;font-size:12px;font-weight:700}.tag-red{background:#e53935;color:white}.tag-orange{background:#fb8c00;color:white}.tag-outline-orange{background:white;color:#fb8c00;border:2px solid #fb8c00}.tag-purple{background:#7e57c2;color:white}.tag-yellow,.pay-yellow{background:#fdd835;color:#3b3100}.tag-pink{background:#f06292;color:white}.tag-blue,.pay-blue{background:#1e88e5;color:white}.tag-green,.pay-green{background:#43a047;color:white}.status-stack{display:flex;gap:5px;flex-wrap:wrap}.form-grid{grid-template-columns:repeat(2,1fr)}.wide{grid-column:1/-1}.section{background:#fff;border:1px solid var(--line);border-radius:15px;padding:20px}.fields{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}.field{display:flex;flex-direction:column;gap:6px}.field.full{grid-column:1/-1}label{font-weight:650;font-size:13px}input,select,textarea{width:100%;border:1px solid #cfc8c0;border-radius:9px;padding:11px 12px;background:white;color:var(--ink);font:inherit}input[type=checkbox]{width:auto;min-height:auto}textarea{min-height:90px;resize:vertical}input:focus,select:focus,textarea:focus{outline:3px solid #a7404520;border-color:var(--brand)}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:13px;border-bottom:1px solid var(--line)}th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}.tablebox{overflow:auto;background:white;border:1px solid var(--line);border-radius:15px}.actions{display:flex;gap:10px;flex-wrap:wrap}.flash{padding:13px 16px;border-radius:10px;background:#e5f2eb;color:#285b45;margin-bottom:16px}.warning{background:#fff1d8;color:#765315}.login{max-width:410px;margin:10vh auto;background:white;padding:34px;border-radius:18px;border:1px solid var(--line)}.timeline{border-left:2px solid var(--line);margin-left:7px;padding-left:20px}.event{padding:0 0 18px;position:relative}.event:before{content:'';position:absolute;width:10px;height:10px;border-radius:50%;background:var(--brand);left:-26px;top:5px}.kvs{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.kv{background:#faf8f5;border-radius:10px;padding:12px}.kv small{display:block;color:var(--muted)}.signature-pad{width:100%;height:260px;border:2px dashed var(--line);border-radius:14px;background:white;touch-action:none}
.practice-layout{grid-template-columns:2fr 1fr}@media(max-width:800px){body{font-size:16px}.wrap{padding:14px}.top{height:auto;min-height:64px;padding:10px 12px;align-items:flex-start}.brand{font-size:17px}.nav{gap:4px;flex-wrap:wrap}.nav a{padding:8px 9px}.nav a span{display:none}.btn{width:100%;min-height:46px}.actions{width:100%}.actions .btn,.actions form{flex:1 1 100%}.stats,.form-grid,.fields,.kvs,.practice-layout{grid-template-columns:1fr}.section{padding:16px;border-radius:13px}.titlebar{align-items:flex-start;flex-direction:column}.wide{grid-column:auto}input,select,textarea{font-size:16px;min-height:46px}th:nth-child(4),td:nth-child(4){display:none}.badge{margin:2px 2px 2px 0}}
.danger{border-color:#e2a5a5;background:#fff7f7}.btn.danger-btn{background:#b42323;color:white}.btn.danger-btn:hover{background:#8f1d1d}.danger-note{color:#8f1d1d;font-weight:700}
.home-logo{width:118px;height:118px;object-fit:contain;border-radius:24px;background:white;padding:10px;border:1px solid var(--line);box-shadow:0 8px 24px #4b392614}
.month-block{margin-bottom:18px}.month-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.hidden{display:none!important}
.practice-code-cr{color:#1e88e5}.practice-code-sm{color:#111}
.lookup{position:relative}.lookup-results{position:absolute;left:0;right:0;top:100%;z-index:20;background:white;border:1px solid var(--line);border-radius:12px;margin-top:6px;box-shadow:0 10px 30px #4b392626;max-height:340px;overflow:auto}.lookup-item{display:block;width:100%;border:0;background:white;text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);cursor:pointer;color:var(--ink)}.lookup-item:hover,.lookup-item:focus{background:#f7f2ee;outline:none}.lookup-item b{display:block}.lookup-item small{display:block;color:var(--muted);white-space:normal}.lookup-state{padding:10px 12px;color:var(--muted);font-size:13px}.selected-box{border:1px solid #b8d7c8;background:#edf7f2;color:#285b45;border-radius:10px;padding:12px;margin-top:10px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}.selected-box .btn{width:auto}
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
  const depositField = document.querySelector('input[name="deposit"]');
  const remainingField = document.querySelector('input[name="remaining_balance"]');
  if(!remainingField) return;
  const remaining = ppmNumber(totalField ? totalField.value : 0) - ppmNumber(depositField ? depositField.value : 0);
  remainingField.value = ppmFormat(remaining);
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
  if(e.target && (e.target.name === 'deposit' || e.target.name === 'total_service')) updateRemainingBalance();
});
document.addEventListener('DOMContentLoaded', function(){ updatePreventivoTotal(); updateRemainingBalance(); });
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
</script>
"""


def layout(title, body, user=None):
    nav = ""
    if user:
        nav = f'''<nav class="nav"><a href="/">Dashboard</a><a href="/pratiche">Archivio</a><a href="/veterinari">Veterinari</a><a href="/nuova" class="btn">+ Nuova pratica</a><a href="/logout">Esci</a></nav>'''
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} - Pet Paradise Manager</title><style>{CSS}</style></head><body><header class="top"><a class="brand" href="/">Pet Paradise <small>MANAGER</small></a>{nav}</header>{body}{APP_JS}</body></html>'''


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
        if path == "/assets/company_logo.png" and (ASSETS / "company_logo.png").exists(): return self.send_png(ASSETS / "company_logo.png")
        match = re.fullmatch(r"/pubblici/ddt/([A-Za-z0-9_-]+)\.pdf", path)
        if match: return self.public_ddt(match.group(1))
        if path == "/login": return self.login_page()
        if path == "/logout": return self.logout()
        user = self.require_user()
        if not user: return
        if path == "/": return self.dashboard(user)
        if path == "/diagnostica": return self.diagnostics(user)
        if path == "/whatsapp-diagnostica": return self.whatsapp_diagnostics(user)
        if path == "/api/clienti/search": return self.api_clients_search(user)
        if path == "/api/veterinari/search": return self.api_veterinarians_search(user)
        if path == "/nuova": return self.new_page(user)
        if path == "/pratiche": return self.archive_home(user)
        if path == "/archivio/pratiche": return self.archive(user)
        if path == "/archivio/clienti": return self.clients_archive(user)
        if path == "/database-mesi": return self.redirect("/pratiche")
        if path == "/veterinari": return self.veterinarians_page(user)
        match = re.fullmatch(r"/veterinari/(\d+)", path)
        if match: return self.veterinarian_detail(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)", path)
        if match: return self.practice(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/modifica", path)
        if match: return self.edit_page(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/ddt\.pdf", path)
        if match: return self.download_ddt(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/ddt-bozza\.pdf", path)
        if match: return self.draft_ddt(user, int(match.group(1)))
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
        with db() as c:
            counts={r["status"]:r["n"] for r in c.execute("SELECT status,count(*) n FROM practices GROUP BY status")}
            payment_counts={r["payment_status"]:r["n"] for r in c.execute("SELECT COALESCE(payment_status,'Da saldare') payment_status,count(*) n FROM practices GROUP BY COALESCE(payment_status,'Da saldare')")}
            catalog_count=c.execute("SELECT count(*) n FROM practices WHERE send_catalog='Si' AND status!='Consegnato'").fetchone()["n"]
            estremi_count=c.execute("SELECT count(*) n FROM practices WHERE send_estremi='Si' AND status!='Consegnato'").fetchone()["n"]
            recent=c.execute("SELECT * FROM practices ORDER BY updated_at DESC LIMIT 8").fetchall()
            incomplete=c.execute("SELECT count(*) n FROM practices WHERE data_complete=0 AND status!='Consegnata'").fetchone()["n"]
        cards=''.join(f'<a class="card stat" href="/archivio/pratiche?stato={quote(s)}"><span>{esc(s)}</span><b>{counts.get(s,0)}</b></a>' for s in STATES)
        pay_card_cls={"Da saldare":"pay-yellow","Acconto":"pay-blue","Pagato":"pay-green"}
        payment_cards=''.join(f'<a class="card stat" href="/archivio/pratiche?pagamento={quote(s)}"><span class="badge {pay_card_cls.get(s,"")}">{esc(s)}</span><b>{payment_counts.get(s,0)}</b></a>' for s in PAYMENT_STATES)
        promemoria_cards=f'''<a class="card stat" href="/archivio/pratiche?promemoria=catalogo"><span class="badge tag-outline-orange">Inviare catalogo</span><b>{catalog_count}</b></a><a class="card stat" href="/archivio/pratiche?promemoria=estremi"><span class="badge tag-outline-orange">Inviare estremi</span><b>{estremi_count}</b></a>'''
        rows=self.practice_rows(recent)
        hour=datetime.now().hour
        greeting="Buongiorno" if hour < 13 else "Buon pomeriggio" if hour < 18 else "Buonasera"
        logo='<img class="home-logo" src="/assets/company_logo.png" alt="Pet Paradise">' if (ASSETS / "company_logo.png").exists() else ''
        body=f'''<main class="wrap"><div class="titlebar"><div style="display:flex;gap:18px;align-items:center">{logo}<div><h1>{greeting}, {esc(user['display_name'])}</h1><div class="sub">Situazione operativa aggiornata</div></div></div></div>{f'<div class="flash warning">{incomplete} pratiche hanno dati ancora da completare.</div>' if incomplete else ''}<h2>Avanzamento pratiche</h2><section class="grid stats">{cards}</section><div style="height:20px"></div><h2>Pagamenti</h2><section class="grid stats">{payment_cards}</section><div style="height:20px"></div><h2>Promemoria</h2><section class="grid stats">{promemoria_cards}</section><div style="height:24px"></div><div class="titlebar"><h2>Attività recenti</h2><a href="/pratiche">Vedi archivio</a></div><div class="tablebox"><table><thead><tr><th>Data</th><th>Pratica</th><th>Animale</th><th>Proprietario</th><th>Sede</th><th>Etichette</th><th>Stato</th></tr></thead><tbody>{rows}</tbody></table></div></main>'''
        self.send_html(layout("Dashboard",body,user))

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
        ]
        html_badges = ''.join(f'<span class="badge {cls}">{label}</span> ' for key,label,cls in tags if key in r.keys() and r[key])
        return html_badges or '<span class="sub">-</span>'

    def status_badges(self,r):
        payment = r["payment_status"] if "payment_status" in r.keys() and r["payment_status"] else "Da saldare"
        pay_cls = {"Da saldare":"pay-yellow","Acconto":"pay-blue","Pagato":"pay-green"}.get(payment,"")
        invoice = f'<small>Fatt. {esc(r["invoice_number"])}</small>' if "invoice_number" in r.keys() and r["invoice_number"] else ""
        return f'<div class="status-stack"><span class="badge">{esc(r["status"])}</span><span class="badge {pay_cls}">{esc(payment)}</span>{invoice}</div>'

    def practice_rows(self,rows):
        if not rows:return '<tr><td colspan="7" class="sub">Nessuna pratica presente.</td></tr>'
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
            html.append(f'<tr><td>{esc((r["created_at"] or "")[:10])}</td><td><a href="/pratiche/{r["id"]}"><b class="{code_cls}">{esc(code)}</b></a></td><td>{animal_cell}</td><td>{owner}<br><small>{esc(r["owner_phone"])}</small></td><td>{esc(r["destination_branch"])}</td><td>{self.tag_badges(r)}</td><td>{self.status_badges(r)}</td></tr>')
        return ''.join(html)

    def archive_home(self,user):
        body='''<main class="wrap"><div class="titlebar"><div><h1>ARCHIVIO</h1><div class="sub">Scegli cosa vuoi consultare.</div></div></div><section class="grid stats"><a class="card stat" href="/archivio/pratiche"><span>Pratiche</span><b>-</b></a><a class="card stat" href="/archivio/clienti"><span>Anagrafica clienti</span><b>-</b></a></section></main>'''
        self.send_html(layout("Archivio",body,user))

    def clients_archive(self,user):
        q=parse_qs(urlparse(self.path).query)
        term=q.get("q",[""])[0].strip()
        sql="SELECT owner_first_name, owner_last_name, owner_phone, owner_phone_2, owner_email, owner_tax_code, owner_address, owner_city, owner_province, owner_zip, COUNT(*) n, MAX(created_at) last_date FROM practices WHERE COALESCE(owner_first_name,'')||COALESCE(owner_last_name,'')||COALESCE(owner_phone,'')<>''"
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
        promemoria=q.get("promemoria",[""])[0].strip()
        sql="SELECT * FROM practices WHERE 1=1"; args=[]
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
            sql += " AND CAST(REPLACE(total_service, ',', '.') AS REAL) >= ?"; args.append(float(spesa_min) if re.match(r"^-?\d+(\.\d+)?$", spesa_min) else 0)
        if spesa_max:
            sql += " AND CAST(REPLACE(total_service, ',', '.') AS REAL) <= ?"; args.append(float(spesa_max) if re.match(r"^-?\d+(\.\d+)?$", spesa_max) else 999999999)
        if date_from:
            sql += " AND date(created_at)>=date(?)"; args.append(date_from)
        if date_to:
            sql += " AND date(created_at)<=date(?)"; args.append(date_to)
        if state:
            sql += " AND status=?"; args.append(state)
        if payment:
            sql += " AND COALESCE(payment_status,'Da saldare')=?"; args.append(payment)
        if promemoria == "catalogo":
            sql += " AND send_catalog='Si' AND status!='Consegnato'"
        if promemoria == "estremi":
            sql += " AND send_estremi='Si' AND status!='Consegnato'"
        sql += " ORDER BY created_at DESC"
        with db() as c:
            rows=c.execute(sql,args).fetchall()
        opts='<option value="">Tutti gli stati</option>'+''.join(f'<option {"selected" if state==s else ""}>{esc(s)}</option>' for s in STATES)
        pay_opts='<option value="">Tutti i pagamenti</option>'+''.join(f'<option {"selected" if payment==s else ""}>{esc(s)}</option>' for s in PAYMENT_STATES)
        service_opts=''.join(f'<option value="{esc(x)}" {"selected" if service==x else ""}>{esc(x or "Tutti i servizi")}</option>' for x in ["","Da decidere","Cremazione singola","Cremazione collettiva"])
        promemoria_label = " - Promemoria catalogo" if promemoria=="catalogo" else " - Promemoria estremi" if promemoria=="estremi" else ""
        month_names=["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno","Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
        groups={}
        for r in rows:
            key=(r["created_at"] or "")[:7] or "Senza data"
            groups.setdefault(key,[]).append(r)
        blocks=[]
        for key,items in groups.items():
            title=key
            if key != "Senza data":
                try:
                    y,m=key.split("-"); title=f"{month_names[int(m)]} {y}"
                except Exception:
                    pass
            blocks.append(f'''<section class="month-block"><div class="month-title"><h2>{esc(title)}</h2><span class="badge">{len(items)} pratiche</span></div><div class="tablebox"><table><thead><tr><th>Data</th><th>Pratica</th><th>Animale</th><th>Proprietario</th><th>Sede</th><th>Etichette</th><th>Stato</th></tr></thead><tbody>{self.practice_rows(items)}</tbody></table></div></section>''')
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>ARCHIVIO</h1><div class="sub">{len(rows)} risultati{promemoria_label} - pratiche divise per mese</div></div></div><form class="section" method="get" style="margin-bottom:18px"><div class="fields"><div class="field"><label>Ricerca generale</label><input name="q" value="{esc(term)}" placeholder="Proprietario, telefono, microchip, pratica, DDT"></div><div class="field"><label>Nome animale</label><input name="animale" value="{esc(animal)}"></div><div class="field"><label>Servizio</label><select name="servizio">{service_opts}</select></div><div class="field"><label>Veterinario</label><input name="veterinario" value="{esc(vet)}" placeholder="Clinica o medico"></div><div class="field"><label>Collaboratore</label><input name="collaboratore" value="{esc(collaborator)}"></div><div class="field"><label>Spesa minima</label><input name="spesa_min" value="{esc(spesa_min)}" inputmode="decimal" placeholder="Es. 100"></div><div class="field"><label>Spesa massima</label><input name="spesa_max" value="{esc(spesa_max)}" inputmode="decimal" placeholder="Es. 350"></div><div class="field"><label>Periodo dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Periodo al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field"><label>Stato pratica</label><select name="stato">{opts}</select></div><div class="field"><label>Pagamento</label><select name="pagamento">{pay_opts}</select></div></div><button class="btn" style="margin-top:12px">Cerca</button><a class="btn ghost" style="margin-top:12px" href="/archivio/pratiche">Pulisci filtri</a></form>{''.join(blocks) if blocks else '<section class="section"><p class="sub">Nessuna pratica trovata.</p></section>'}</main>'''
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
        vet_options='<option value="">Nessun veterinario selezionato</option>'+''.join(f'<option value="{v["id"]}" data-fullname="{esc(v["clinic_name"])}" data-address="{esc(v["address"])}" data-city="{esc(v["city"])}" {"selected" if str(raw("veterinarian_id"))==str(v["id"]) else ""}>{esc(v["short_name"] or v["clinic_name"])}{(" - "+esc(v["clinic_name"])) if v["short_name"] else ""}</option>' for v in vets)
        voucher_checked='checked' if raw('voucher_requested')=="Si" else ''
        catalog_checked='checked' if raw('send_catalog')=="Si" else ''
        estremi_checked='checked' if raw('send_estremi')=="Si" else ''
        return f'''<section class="section"><h2>Operatore</h2><div class="fields"><div class="field"><label>Operatore *</label><select name="operator_name" required><option value="">Seleziona operatore</option><option {selected('operator_name','SERENA')}>SERENA</option><option {selected('operator_name','ALESSIO')}>ALESSIO</option><option {selected('operator_name','FILIPPO')}>FILIPPO</option></select></div></div></section>
        <section class="section"><h2>Richiesta</h2><div class="fields"><div class="field"><label>Servizio</label><select name="service_type"><option {selected('service_type','Da decidere')}>Da decidere</option><option {selected('service_type','Cremazione singola')}>Cremazione singola</option><option {selected('service_type','Cremazione collettiva')}>Cremazione collettiva</option></select></div><div class="field"><label>Origine richiesta *</label><select name="request_origin" required><option {selected('request_origin','Veterinario')}>Veterinario</option><option {selected('request_origin','Privato')}>Privato</option><option {selected('request_origin','Consegna in sede')}>Consegna in sede</option><option {selected('request_origin','Collaboratore')}>Collaboratore</option></select></div><div class="field {'hidden' if raw('request_origin')!='Collaboratore' else ''}" id="collaboratorBox"><label>Collaboratore</label><select name="collaborator_name"><option value="">Nessun collaboratore</option><option {selected('collaborator_name','HUMANITAS CROCE VERDE')}>HUMANITAS CROCE VERDE</option></select></div><div class="field"><label>Sede di destinazione</label><select name="destination_branch"><option {selected('destination_branch','Livorno')}>Livorno</option><option {selected('destination_branch','Empoli')}>Empoli</option></select></div><div class="field"><label>Data recupero</label><input type="date" name="pickup_date" value="{val('pickup_date')}"></div></div></section>
        <section class="section"><h2>SPEDITORE</h2><div class="fields"><input type="hidden" name="client_id" value="{val('client_id')}"><div class="field full lookup"><label>Cerca cliente in anagrafica</label><input id="clientSearch" autocomplete="off" placeholder="Scrivi nome, telefono, email, codice fiscale, città..."><div id="clientResults" class="lookup-results hidden"></div><div id="clientSelected" class="selected-box hidden"><span id="clientSelectedText"></span><button class="btn ghost" type="button" id="clearClientSelection">Cancella selezione</button></div><small class="sub">Se scegli un cliente, i campi vengono compilati automaticamente. Se li modifichi, l'anagrafica non viene aggiornata senza conferma.</small></div><div class="field"><label>Nome *</label><input name="owner_first_name" value="{val('owner_first_name')}" required></div><div class="field"><label>Cognome *</label><input name="owner_last_name" value="{val('owner_last_name')}" required></div><div class="field"><label>Ragione sociale</label><input name="owner_company" value="{val('owner_company')}"></div><div class="field"><label>Telefono *</label><input type="tel" inputmode="numeric" name="owner_phone" value="{val('owner_phone')}" required></div><div class="field"><label>Secondo telefono</label><input type="tel" inputmode="numeric" name="owner_phone_2" value="{val('owner_phone_2')}"></div><div class="field"><label>Email</label><input type="email" name="owner_email" value="{val('owner_email')}"></div><div class="field"><label>Codice fiscale *</label><input name="owner_tax_code" value="{val('owner_tax_code')}" required></div><div class="field"><label>Partita IVA</label><input name="owner_vat" value="{val('owner_vat')}"></div><div class="field full"><label>Indirizzo *</label><input name="owner_street" value="{val('owner_street') or val('owner_address')}" required></div><div class="field"><label>Comune *</label><input name="owner_city" value="{val('owner_city')}" required></div><div class="field"><label>Provincia *</label><input name="owner_province" value="{val('owner_province')}" maxlength="2" placeholder="Si compila dal comune" required></div><div class="field"><label>CAP *</label><input name="owner_zip" value="{val('owner_zip')}" inputmode="numeric" required></div><div class="field full"><label>Note cliente</label><textarea name="owner_notes" placeholder="Note anagrafiche utili">{val('owner_notes')}</textarea></div></div></section>
        <section class="section"><h2>DESTINATARIO E LUOGO DI DESTINAZIONE</h2><p class="sub">Compilati automaticamente in base alla sede selezionata: Livorno oppure Empoli.</p></section>
        <section class="section"><h2>LUOGO DI ORIGINE</h2><div class="fields"><div class="field"><label>Luogo di origine</label><select name="origin_mode"><option {selected('origin_mode','IDEM SPED','IDEM SPED')}>IDEM SPED</option><option {selected('origin_mode','Testo libero','IDEM SPED')}>Testo libero</option></select></div><div class="field full"><label>Testo libero / indirizzo diverso</label><input name="origin_text" value="{val('origin_text') or (val('pickup_address') if raw('pickup_address_mode')=='Altro indirizzo' else '')}" placeholder="Scrivi qui solo se il luogo non è IDEM SPED"></div></div></section>
        <section class="section"><h2>Animale</h2><div class="fields"><div class="field"><label>Nome</label><input name="animal_name" value="{val('animal_name')}"></div><div class="field"><label>Specie</label><input name="species" value="{val('species')}"></div><div class="field"><label>Peso stimato (kg)</label><input name="estimated_weight" value="{val('estimated_weight')}"></div><div class="field"><label>Età - anni</label><input name="age_years" value="{val('age_years')}"></div><div class="field"><label>Età - mesi</label><input name="age_months" value="{val('age_months')}"></div><div class="field"><label>Microchip</label><input name="microchip" value="{val('microchip')}"></div><div class="field full"><label>Razza</label><input name="breed" value="{val('breed')}"></div></div><button class="btn ghost" type="button" id="showSecondAnimal" style="margin-top:12px;{'display:none' if raw('animal2_name') else ''}">+ Aggiungi altro animale</button><div id="secondAnimalBox" style="display:{'block' if raw('animal2_name') else 'none'};margin-top:14px"><h2>Secondo animale</h2><div class="fields"><div class="field"><label>Nome</label><input name="animal2_name" value="{val('animal2_name')}"></div><div class="field"><label>Specie</label><input name="animal2_species" value="{val('animal2_species')}"></div><div class="field"><label>Peso stimato (kg)</label><input name="animal2_weight" value="{val('animal2_weight')}"></div><div class="field"><label>Microchip</label><input name="animal2_microchip" value="{val('animal2_microchip')}"></div><div class="field full"><label>Razza</label><input name="animal2_breed" value="{val('animal2_breed')}"></div></div></div></section>
        <section class="section"><h2>AMBULATORIO VETERINARIO</h2><div class="fields"><div class="field full lookup"><label>VETERINARIO</label><input id="vetSearch" autocomplete="off" placeholder="Scrivi per cercare il veterinario"><div id="vetResults" class="lookup-results hidden"></div><select name="veterinarian_id">{vet_options}</select><input type="hidden" name="clinic_name" value="{val('clinic_name')}"><button class="btn ghost" type="button" id="clearVetSelection" style="margin-top:8px">Cancella veterinario</button></div><div class="field"><label>MEDICO VETERINARIO</label><input name="veterinarian_name" value="{val('veterinarian_name')}"></div><div class="field"><label><input type="checkbox" name="voucher_requested" value="Si" {voucher_checked}> BUONO</label><small class="sub">Spunta per assegnare un buono al veterinario selezionato.</small></div></div></section>
        <section class="section"><h2>TRASPORTATORE</h2><div class="fields"><div class="field"><label>Dati trasportatore</label><select name="transporter_mode"><option {selected('transporter_mode','IDEM SPED','IDEM SPED')}>IDEM SPED</option><option {selected('transporter_mode','DATI PET PARADISE','IDEM SPED')}>DATI PET PARADISE</option></select></div><div class="field"><label>Scelta rapida mezzo</label><select id="transport_method_quick"><option value="">Seleziona se serve</option><option value="MEZZO PROPRIO">MEZZO PROPRIO</option></select></div><div class="field"><label>Mezzo di trasporto</label><input name="transport_method" value="{val('transport_method')}"></div><div class="field"><label>Targa automezzo</label><input name="vehicle_plate" value="{val('vehicle_plate')}"></div><div class="field"><label>Temperatura</label><select name="temperature_mode"><option {selected('temperature_mode','Ambiente','Ambiente')}>Ambiente</option><option {selected('temperature_mode','Refrigerato','Ambiente')}>Refrigerato</option><option {selected('temperature_mode','Congelato','Ambiente')}>Congelato</option></select></div><div class="field"><label>Numero colli</label><input name="package_count" value="{val('package_count') or '1'}"></div><div class="field"><label>ID contenitore</label><select name="container_id"><option value="">Seleziona ID contenitore</option><option {selected('container_id','03/2021')}>03/2021</option><option {selected('container_id','04/2021')}>04/2021</option></select></div><div class="field"><label>Numero lotto</label><input name="lot_number" value="{val('lot_number') or '/'}"></div><div class="field"><label>Metodo trattamento</label><input name="treatment_method" value="{val('treatment_method') or '/'}"></div></div></section>
        <section class="section"><h2>Preventivo</h2><div class="fields"><div class="field"><label>Cremazione €</label><input name="price_cremation" value="{val('price_cremation')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Ritiro €</label><input name="price_pickup" value="{val('price_pickup')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Urna €</label><input name="price_urn" value="{val('price_urn')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label><input type="checkbox" name="send_catalog" value="Si" {catalog_checked} style="width:auto"> INVIARE CATALOGO</label></div><div class="field"><label>Riconsegna €</label><input name="price_delivery" value="{val('price_delivery')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Calco €</label><input name="price_cast" value="{val('price_cast')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Serale €</label><input name="price_evening" value="{val('price_evening')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Notturno €</label><input name="price_night" value="{val('price_night')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Festivo €</label><input name="price_holiday" value="{val('price_holiday')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Accessori €</label><input name="price_accessories" value="{val('price_accessories')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Totale servizio €</label><input name="total_service" value="{val('total_service')}" placeholder="Numero o testo libero"></div><div class="field"><label><input type="checkbox" name="send_estremi" value="Si" {estremi_checked} style="width:auto"> INVIARE ESTREMI</label></div><div class="field"><label>Acconto €</label><input name="deposit" value="{val('deposit')}" placeholder="Numero o testo libero"></div><div class="field"><label>Rimanenza €</label><input name="remaining_balance" value="{val('remaining_balance')}" readonly></div><div class="field full"><label>TOTALE</label><textarea name="total_text" placeholder="Testo libero per note sul totale">{val('total_text')}</textarea></div><div class="field full"><label>Note operative</label><textarea name="notes">{val('notes')}</textarea></div></div></section>
        <section class="section"><h2>Etichette operative</h2><div class="fields">{tag_select('tag_assistita','ASSISTITA','tag-red')}{tag_select('tag_possibile_assistita','POSSIBILE ASSISTITA','tag-red')}{tag_select('tag_assistita_streaming','ASSISTITA STREAMING','tag-orange')}{tag_select('tag_saluto','SALUTO','tag-purple')}{tag_select('tag_calco','CALCO','tag-yellow')}{tag_select('tag_avvisare','AVVISARE','tag-pink')}{tag_select('tag_da_richiamare','DA RICHIAMARE','tag-blue')}</div></section>
        <section class="section"><h2>Documento e accettazione</h2><div class="fields"><div class="field"><label>Numero documento</label><input name="identity_document_number" value="{val('identity_document_number')}"></div><div class="field"><label>Data rilascio</label><input type="date" name="identity_document_date" value="{val('identity_document_date')}"></div><div class="field full"><label>Luogo firma</label><input name="signing_place" value="{val('signing_place') or val('destination_branch')}"></div></div></section>'''

    def new_page(self,user):
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Nuova pratica</h1><div class="sub">Inserisci subito i dati disponibili; potrai completarli in seguito.</div></div></div><form method="post"><div class="grid form-grid">{self.fields_html()}</div><div class="actions" style="margin-top:18px"><button class="btn">Crea pratica</button><a class="btn ghost" href="/">Annulla</a></div></form></main>'''
        self.send_html(layout("Nuova pratica",body,user))

    def normalized_fields(self,f):
        keys=["client_id","operator_name","request_origin","collaborator_name","destination_branch","owner_first_name","owner_last_name","owner_company","owner_phone","owner_phone_2","owner_email","owner_tax_code","owner_vat","owner_notes","owner_address","owner_street","owner_city","owner_province","owner_zip","pickup_address_mode","pickup_address","origin_mode","origin_text","pickup_date","animal_name","species","breed","estimated_weight","age_years","age_months","microchip","animal2_name","animal2_species","animal2_breed","animal2_weight","animal2_microchip","service_type","veterinarian_id","voucher_requested","clinic_name","veterinarian_name","notes","transporter_mode","transport_method","vehicle_plate","temperature_mode","package_count","container_id","lot_number","treatment_method","tag_assistita","tag_possibile_assistita","tag_assistita_streaming","tag_saluto","tag_calco","tag_avvisare","tag_da_richiamare","payment_status","price_cremation","price_pickup","price_evening","price_urn","send_catalog","send_estremi","price_delivery","price_night","price_cast","price_holiday","price_accessories","deposit","remaining_balance","total_service","total_text","identity_document_number","identity_document_date","signing_place"]
        data = {k:f.get(k,"").strip() for k in keys}
        if not data["payment_status"] or data["payment_status"] not in PAYMENT_STATES:
            data["payment_status"] = "Da saldare"
        data["send_catalog"] = "Si" if data["send_catalog"] == "Si" else ""
        data["send_estremi"] = "Si" if data["send_estremi"] == "Si" else ""
        for key in ("tag_assistita","tag_possibile_assistita","tag_assistita_streaming","tag_saluto","tag_calco","tag_avvisare","tag_da_richiamare"):
            data[key] = "Si" if data[key] == "Si" else ""
        data["voucher_requested"] = "Si" if data["voucher_requested"] == "Si" else ""
        data["client_id"] = data["client_id"] or None
        data["veterinarian_id"] = data["veterinarian_id"] or None
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
                    data["origin_text"] = " - ".join(x for x in [vet["clinic_name"], vet["address"]] if x)
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
            pdf_block = f'<div class="flash">Il PDF definitivo e stato archiviato.</div><div class="actions"><a class="btn" href="/pratiche/{pid}/ddt.pdf">Apri / stampa DDT</a><button class="btn ghost" type="button" onclick="sharePracticePdf(\'{share_url}\', \'DDT pratica {esc(p["practice_number"])}\', \'{esc(pdf_filename)}\')">Condividi PDF</button><button class="btn ghost" type="button" onclick="navigator.clipboard.writeText(new URL(\'{share_url}\', window.location.href).toString()).then(()=>alert(\'Link pubblico PDF copiato\'))">Copia link PDF</button></div><p class="sub">Il link condiviso apre solo questo PDF, non il gestionale.</p>'
        else:
            final_action = f'<form method="post" action="/pratiche/{pid}/ddt"><button class="btn">Assegna numero e genera PDF definitivo</button></form>' if p['data_complete'] else '<div class="flash warning">Pratica salvata. Potrai assegnare il numero DDT e generare il PDF definitivo quando avrai completato i dati obbligatori.</div>'
            draft_filename = safe_pdf_filename((p["animal_name"] or p["practice_number"]) + "_BOZZA", "bozza")
            pdf_block = f'<div class="actions"><a class="btn ghost" href="/pratiche/{pid}">Salva pratica</a><a class="btn ghost" href="/pratiche/{pid}/ddt-bozza.pdf">Apri bozza PDF</a><button class="btn ghost" type="button" onclick="sharePracticePdf(\'/pratiche/{pid}/ddt-bozza.pdf\', \'Bozza DCS pratica {esc(p["practice_number"])}\', \'{esc(draft_filename)}\')">Condividi bozza PDF</button>{final_action}</div><p class="sub">La pratica resta salvata in archivio. Il DDT numerato puo essere generato anche in un secondo momento, per esempio alla fine della pratica.</p>'
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
              <div class="section danger"><h2>Elimina pratica</h2><p class="danger-note">Attenzione: questa azione cancella definitivamente pratica, storico e PDF collegati.</p><form method="post" action="/pratiche/{pid}/elimina" onsubmit="return confirm('Confermi la cancellazione definitiva della pratica?')"><div class="field"><label>Per confermare scrivi ELIMINA</label><input name="confirm_delete" autocomplete="off" required></div><button class="btn danger-btn" style="margin-top:12px">Elimina definitivamente</button></form></div>
            </div>
            <aside class="section"><h2>Storico</h2><div class="timeline">{hist}</div></aside>
          </section>
        </main>"""
        self.send_html(layout(p["practice_number"],body,user))

    def edit_page(self,user,pid):
        with db() as c:p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p:return self.send_error(404)
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Modifica {esc(p['practice_number'])}</h1><div class="sub">Completa o correggi i dati della pratica.</div></div></div><form method="post"><div class="grid form-grid">{self.fields_html(p)}</div><div class="actions" style="margin-top:18px"><button class="btn">Salva modifiche</button><a class="btn ghost" href="/pratiche/{pid}">Annulla</a></div></form></main>'''
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

    def delete_practice(self,user,pid):
        f=self.form()
        if f.get("confirm_delete","").strip().upper() != "ELIMINA":
            return self.send_error(400, "Per eliminare la pratica devi scrivere ELIMINA")
        with db() as c:
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            if not p:return self.send_error(404)
            pdf_names = []
            if p["ddt_pdf"]:
                pdf_names.append(p["ddt_pdf"])
            if p["practice_number"]:
                pdf_names.append(f"DCS-BOZZA-{p['practice_number']}.pdf")
            c.execute("DELETE FROM veterinarian_vouchers WHERE practice_id=? AND status IN ('Disponibile','Maturato')",(pid,))
            c.execute("DELETE FROM practice_history WHERE practice_id=?",(pid,))
            c.execute("DELETE FROM practices WHERE id=?",(pid,))
        for name in pdf_names:
            path = (DDT_DIR / name).resolve()
            try:
                if str(path).startswith(str(DDT_DIR.resolve())) and path.exists():
                    path.unlink()
            except OSError:
                pass
        self.redirect("/pratiche")

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

    def download_ddt(self,user,pid):
        with db() as c: p=c.execute("SELECT ddt_pdf,animal_name,practice_number FROM practices WHERE id=?",(pid,)).fetchone()
        if not p or not p["ddt_pdf"]: return self.send_error(404)
        path=DDT_DIR / p["ddt_pdf"]
        if not path.exists(): return self.send_error(404)
        return self.send_pdf(path, safe_pdf_filename(p["animal_name"] or p["practice_number"], "pratica"))

    def draft_ddt(self,user,pid):
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
        return self.send_pdf(path, safe_pdf_filename((p["animal_name"] or p["practice_number"]) + "_BOZZA", "bozza"))

    def send_pdf(self,path, filename=None):
        payload=path.read_bytes()
        self.send_response(200); self.send_header("Content-Type","application/pdf")
        self.send_header("Content-Disposition",f'inline; filename="{filename or path.name}"')
        self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)


if __name__ == "__main__":
    init_db()
    print(f"Pet Paradise Manager: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), App).serve_forever()
