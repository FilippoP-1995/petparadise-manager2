from __future__ import annotations

import hashlib
import hmac
import html
import base64
import json
import os
import re
import secrets
import sqlite3
import threading
import traceback
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse
from zoneinfo import ZoneInfo

from email_service import EmailConfigurationError, EmailDeliveryError, send_email
from calendar_service import (
    EVENT_TYPES, PICKUP_STATUSES, DELIVERY_STATUSES, PAYMENT_STATUSES, CALENDAR_OPERATORS,
    add_history as calendar_add_history,
    ensure_calendar_schema, event_color_class, event_type_dot_class, normalize_event, overlap_rows,
    parse_items as calendar_parse_items, period_bounds as calendar_period_bounds,
    schedule_event_notifications, sync_children as calendar_sync_children,
)
from pdf_service import generate_ddt
from notification_service import (
    NOTIFICATION_TYPES,
    emit_notification,
    ensure_notification_schema,
    process_scheduled_notifications, process_calendar_notifications,
)
from urn_inventory import DEFAULT_URNS


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PPM_DATA_DIR", ROOT / "data"))
DB_PATH = DATA / "pet_paradise.db"
DDT_DIR = DATA / "ddt"
ASSETS = ROOT / "assets"
HOST = os.environ.get("PPM_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("PPM_PORT", "8080")))
ROME_TZ = ZoneInfo("Europe/Rome")


def _compute_app_version():
    commit = os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("SOURCE_VERSION")
    if commit:
        return commit[:12]
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    except OSError:
        return "dev"


APP_VERSION = _compute_app_version()

STATES = [
    "Ritirato", "In programma", "Cremato", "Da consegnare", "Consegnato", "Smaltito",
]

PAYMENT_STATES = [
    "Da saldare", "Acconto", "Pagato",
]

PAYMENT_METHODS = [
    "", "Pos", "Contanti", "Bonifico",
]

MONEY_FIELDS = {
    "price_cremation":"Cremazione", "price_pickup":"Ritiro", "price_urn":"Urna", "price_urn_2":"Seconda urna",
    "price_delivery":"Riconsegna", "price_cast":"Calco", "price_cast_2":"Secondo calco", "price_paw_cast":"Calco polpastrello", "price_paw_cast_2":"Secondo calco polpastrello", "price_paw_cast_3":"Altro calco polpastrello", "price_paw_cast_4":"Altro calco polpastrello", "price_nose_cast":"Calco naso", "price_nose_cast_2":"Secondo calco naso", "price_nose_cast_3":"Altro calco naso", "price_nose_cast_4":"Altro calco naso", "price_evening":"Serale",
    "price_night":"Notturno", "price_holiday":"Festivo", "price_accessories":"Accessori", "price_accessories_2":"Secondi accessori",
    "total_service":"Totale W", "total_text":"TOTALE D", "deposit":"Acconto", "remaining_balance":"Rimanenza",
    "deposit_final":"Acconto D", "remaining_final":"Rimanenza D",
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
        CREATE TABLE IF NOT EXISTS whatsapp_cron_runs (
          id INTEGER PRIMARY KEY,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          status TEXT NOT NULL DEFAULT 'in_corso',
          processed INTEGER NOT NULL DEFAULT 0,
          error TEXT
        );
        CREATE TABLE IF NOT EXISTS articles (
          id INTEGER PRIMARY KEY,
          name TEXT UNIQUE NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS article_orders (
          id INTEGER PRIMARY KEY,
          article_id INTEGER NOT NULL REFERENCES articles(id),
          ordered_by INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS email_orders (
          id INTEGER PRIMARY KEY,
          order_type TEXT NOT NULL DEFAULT 'water',
          quantity INTEGER NOT NULL,
          notes TEXT,
          recipient TEXT NOT NULL,
          subject TEXT NOT NULL,
          body TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'Bozza',
          error_message TEXT,
          operator_id INTEGER NOT NULL REFERENCES users(id),
          parent_order_id INTEGER REFERENCES email_orders(id),
          attempt_count INTEGER NOT NULL DEFAULT 0,
          sent_at TEXT,
          archived_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS urns (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          material TEXT,
          internal_code TEXT UNIQUE,
          price TEXT NOT NULL DEFAULT '',
          quantity INTEGER NOT NULL DEFAULT 0,
          low_stock_threshold INTEGER NOT NULL DEFAULT 3,
          image_path TEXT,
          notes TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS urn_movements (
          id INTEGER PRIMARY KEY,
          urn_id INTEGER REFERENCES urns(id),
          practice_id INTEGER REFERENCES practices(id) ON DELETE SET NULL,
          user_id INTEGER REFERENCES users(id),
          movement_type TEXT NOT NULL,
          quantity_delta INTEGER NOT NULL DEFAULT 0,
          old_quantity INTEGER NOT NULL DEFAULT 0,
          new_quantity INTEGER NOT NULL DEFAULT 0,
          note TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_movements (
          id INTEGER PRIMARY KEY,
          practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
          payment_type TEXT NOT NULL,
          payment_channel TEXT NOT NULL,
          amount REAL NOT NULL,
          paid_at TEXT NOT NULL,
          user_id INTEGER REFERENCES users(id),
          notes TEXT,
          created_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO settings(key,value) VALUES('next_practice_number','1');
        INSERT OR IGNORE INTO settings(key,value) VALUES('next_cr_number','1');
        INSERT OR IGNORE INTO settings(key,value) VALUES('next_sm_number','1');
        INSERT OR IGNORE INTO settings(key,value) VALUES('next_ddt_number','1');
        INSERT OR IGNORE INTO settings(key,value) VALUES('order_recipient_email','[QUI INSERIRÒ IL MIO INDIRIZZO EMAIL]');
        INSERT OR IGNORE INTO settings(key,value) VALUES('order_email_subject','Ordine boccioni acqua - Pet Paradise');
        INSERT OR IGNORE INTO settings(key,value) VALUES('order_email_template','Buongiorno,\n\ndesideriamo ordinare {{quantita}} boccioni di acqua.\n\nVi chiediamo gentilmente di confermare disponibilità e consegna.\n\n{{note_predefinite}}\n\nGrazie.');
        INSERT OR IGNORE INTO settings(key,value) VALUES('order_email_signature','Pet Paradise');
        INSERT OR IGNORE INTO settings(key,value) VALUES('order_sender_name','Pet Paradise');
        INSERT OR IGNORE INTO settings(key,value) VALUES('order_phone','');
        INSERT OR IGNORE INTO settings(key,value) VALUES('order_default_notes','');
        """)
        for article_name in ("Sacchi per ritiro", "Boccette pelo", "Certificati", "Sacchetti riconsegna", "Sacchetti ceneri", "Cerniere e viti urne"):
            c.execute("INSERT OR IGNORE INTO articles(name,created_at) VALUES(?,?)", (article_name, now()))
        if not c.execute("SELECT 1 FROM urns LIMIT 1").fetchone():
            for index,(urn_name,material,quantity,price) in enumerate(DEFAULT_URNS,1):
                stamp=now(); code=f"INV-{index:03d}"
                cur=c.execute("""INSERT INTO urns(name,material,internal_code,price,quantity,low_stock_threshold,notes,created_at,updated_at)
                                 VALUES(?,?,?,?,?,?,?,?,?)""",(urn_name,material,code,price,quantity,3,"Importata da INVENTARIO URNE.pdf",stamp,stamp))
                urn_id=cur.lastrowid
                c.execute("""INSERT INTO urn_movements(urn_id,movement_type,quantity_delta,old_quantity,new_quantity,note,created_at)
                             VALUES(?,?,?,?,?,?,?)""",(urn_id,"Importazione inventario",quantity,0,quantity,"Importata da INVENTARIO URNE.pdf",stamp))
        status_migrations = {
            "Da ritirare": "Ritirato",
            "Dati da completare": "Ritirato",
            "In cella frigo": "Ritirato",
            "In attesa cremazione": "Ritirato",
            "Ceneri pronte": "Da consegnare",
            "Consegnata": "Consegnato",
            "Messo in programma": "In programma",
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
            "price_paw_cast": "TEXT", "price_nose_cast": "TEXT",
            "price_urn_2": "TEXT", "urn_notes_2": "TEXT", "price_cast_2": "TEXT",
            "price_accessories_2": "TEXT", "accessory_type": "TEXT", "accessory_type_2": "TEXT",
            "send_catalog": "TEXT",
            "catalog_sent": "TEXT",
            "send_estremi": "TEXT",
            "deposit": "TEXT", "remaining_balance": "TEXT", "total_service": "TEXT", "total_text": "TEXT", "identity_document_number": "TEXT",
            "identity_document_date": "TEXT", "signing_place": "TEXT",
            "pickup_address_mode": "TEXT DEFAULT 'Idem sped.'",
            "transporter_mode": "TEXT DEFAULT 'IDEM SPED'",
            "origin_mode": "TEXT DEFAULT 'IDEM SPED'",
            "origin_text": "TEXT",
            "provenance": "TEXT",
            "tag_assistita": "TEXT",
            "tag_possibile_assistita": "TEXT",
            "tag_assistita_streaming": "TEXT",
            "tag_possibile_assistita_streaming": "TEXT",
            "tag_saluto": "TEXT",
            "tag_calco": "TEXT",
            "tag_possibile_calco": "TEXT",
            "tag_calco_urna": "TEXT",
            "tag_calco_paw": "TEXT",
            "tag_possibile_calco_paw": "TEXT",
            "tag_calco_nose": "TEXT",
            "tag_possibile_calco_nose": "TEXT",
            "tag_avvisare": "TEXT",
            "tag_da_richiamare": "TEXT",
            "price_paw_cast_2": "TEXT",
            "price_nose_cast_2": "TEXT",
            "price_nose_cast_3": "TEXT", "price_nose_cast_4": "TEXT",
            "price_paw_cast_3": "TEXT", "price_paw_cast_4": "TEXT",
            "nose_cast_type": "TEXT", "nose_cast_type_2": "TEXT", "nose_cast_type_3": "TEXT", "nose_cast_type_4": "TEXT",
            "paw_cast_type": "TEXT", "paw_cast_type_2": "TEXT", "paw_cast_type_3": "TEXT", "paw_cast_type_4": "TEXT",
            "accessory_detail": "TEXT", "accessory_detail_2": "TEXT",
            "owner_phone_note": "TEXT",
            "estremi_sent": "TEXT",
            "deposit_final": "TEXT", "remaining_final": "TEXT",
            "payment_status": "TEXT DEFAULT 'Da saldare'",
            "payment_amount": "TEXT",
            "origin_veterinarian_id": "INTEGER",
            "invoice_number": "TEXT",
            "invoice_date": "TEXT",
            "invoice_total": "TEXT",
            "make_invoice": "TEXT",
            "payment_method": "TEXT",
            "original_practice_number": "TEXT",
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
            "deleted_by": "INTEGER",
            "urn_id": "INTEGER",
            "urn_id_2": "INTEGER",
            "deposit_paid_at": "TEXT",
            "paid_at": "TEXT",
            "cremation_registered": "TEXT",
        }
        existing = {row["name"] for row in c.execute("PRAGMA table_info(practices)")}
        for name, definition in extra_columns.items():
            if name not in existing:
                c.execute(f"ALTER TABLE practices ADD COLUMN {name} {definition}")
        vet_existing = {row["name"] for row in c.execute("PRAGMA table_info(veterinarians)")}
        for name, definition in {"short_name":"TEXT", "address":"TEXT", "city":"TEXT"}.items():
            if name not in vet_existing:
                c.execute(f"ALTER TABLE veterinarians ADD COLUMN {name} {definition}")
        urn_existing = {row["name"] for row in c.execute("PRAGMA table_info(urns)")}
        for name, definition in {"category": "TEXT NOT NULL DEFAULT 'Urna'"}.items():
            if name not in urn_existing:
                c.execute(f"ALTER TABLE urns ADD COLUMN {name} {definition}")
        users_existing = {row["name"] for row in c.execute("PRAGMA table_info(users)")}
        if "must_change_password" not in users_existing:
            c.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS user_preferences (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          key TEXT NOT NULL, value TEXT NOT NULL,
          PRIMARY KEY(user_id, key)
        );
        """)
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_practices_ddt_share_token ON practices(ddt_share_token)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_practices_invoice ON practices(invoice_number,invoice_date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_due ON whatsapp_messages(status, scheduled_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_practice ON whatsapp_messages(practice_id, created_at)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_messages_one_active ON whatsapp_messages(practice_id) WHERE status IN ('programmato','in_invio')")
        c.execute("CREATE INDEX IF NOT EXISTS idx_whatsapp_cron_runs_started ON whatsapp_cron_runs(started_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(last_name, first_name)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_phone ON clients(phone)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_email ON clients(email)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_clients_tax ON clients(tax_code)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_urns_search ON urns(active,material,name)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_urn_movements_urn ON urn_movements(urn_id,created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_payment_movements_paid ON payment_movements(paid_at,payment_channel)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_payment_movements_practice ON payment_movements(practice_id,paid_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_practices_dashboard ON practices(status,pickup_date,deleted_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_history_status_events ON practice_history(practice_id,event_type,created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_email_orders_status_date ON email_orders(status,created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_email_orders_active ON email_orders(archived_at,created_at DESC)")
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
        for op_username, op_display_name in (
            ("serena", "Serena"), ("alessio", "Alessio"),
            ("filippo", "Filippo"), ("gianluca", "Gianluca"),
        ):
            if not c.execute("SELECT 1 FROM users WHERE username=?", (op_username,)).fetchone():
                c.execute(
                    "INSERT INTO users(username,password_hash,display_name,role,must_change_password) VALUES(?,?,?,?,1)",
                    (op_username, password_hash("petparadise"), op_display_name, "operator"),
                )
        legacy_payments=c.execute("""SELECT * FROM practices p
                                     WHERE NOT EXISTS(SELECT 1 FROM payment_movements m WHERE m.practice_id=p.id)""").fetchall()
        for practice in legacy_payments:
            due=effective_total(practice); deposit=min(due,max(0.0,money_value(practice["deposit"])))
            channel="D" if uses_total_d(practice) else "ordinario"
            economic_at=practice["updated_at"] or practice["created_at"] or now()
            if deposit>0:
                c.execute("""INSERT INTO payment_movements(practice_id,payment_type,payment_channel,amount,paid_at,user_id,notes,created_at)
                             VALUES(?,?,?,?,?,?,?,?)""",(practice["id"],"acconto_d" if channel=="D" else "acconto_ordinario",channel,deposit,economic_at,practice["created_by"],"Migrazione dati esistenti: data ricavata dall'ultimo aggiornamento",now()))
                c.execute("UPDATE practices SET deposit_paid_at=COALESCE(deposit_paid_at,?) WHERE id=?",(economic_at,practice["id"]))
            if (practice["payment_status"] or "") == "Pagato" and due>deposit:
                balance=due-deposit
                c.execute("""INSERT INTO payment_movements(practice_id,payment_type,payment_channel,amount,paid_at,user_id,notes,created_at)
                             VALUES(?,?,?,?,?,?,?,?)""",(practice["id"],"saldo_d" if channel=="D" else "saldo_ordinario",channel,balance,economic_at,practice["created_by"],"Migrazione dati esistenti: data ricavata dall'ultimo aggiornamento",now()))
            if (practice["payment_status"] or "") == "Pagato":
                c.execute("UPDATE practices SET paid_at=COALESCE(paid_at,?) WHERE id=?",(economic_at,practice["id"]))
        ensure_notification_schema(c)
        ensure_calendar_schema(c)


def esc(value):
    return html.escape(str(value or ""), quote=True)


def now():
    return datetime.now().isoformat(timespec="seconds")


def whatsapp_datetime(value=None):
    """Return a timezone-aware Europe/Rome datetime for WhatsApp scheduling."""
    value = value or datetime.now(ROME_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=ROME_TZ)
    return value.astimezone(ROME_TZ)


def whatsapp_now(value=None):
    # Existing SQLite values are local, offset-free ISO strings. Keep that format
    # for safe lexical comparisons, but always derive it explicitly in Rome time.
    return whatsapp_datetime(value).replace(tzinfo=None).isoformat(timespec="seconds")


def compact_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


ORDER_EMAIL_SUBJECT = "Ordine boccioni acqua - Pet Paradise"
DEFAULT_ORDER_EMAIL_TEMPLATE = """Buongiorno,

desideriamo ordinare {{quantita}} boccioni di acqua.

Vi chiediamo gentilmente di confermare disponibilità e consegna.

{{note_predefinite}}

Grazie."""


def valid_email_address(value):
    value=str(value or "").strip()
    return bool(len(value)<=254 and "\n" not in value and "\r" not in value and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",value))


def veterinarian_provenance(*names):
    label=" ".join(str(name or "") for name in names).upper().replace("’", "'")
    groups={
        "V":("VARIGNANO","CAMPO D'AVIAZIONE","CAMPO D AVIAZIONE","GLI AMICI DI BLU","AMICI DI BLU"),
        "E":("LUCY","FREDIANI","MATTEINI","LA FENICE","FENICE","CROCE AZZURRA","BELLUCCI","BARTOLI","GENNARI","GIULIA FRATI","SANMINIANIMAL","PARLANTI","DANTE DELLE ROSE"),
        "F":("IL POGGETTO","POGGETTO","ARIOSTO"),
        "P":("BARBARICINA",),
    }
    for code,needles in groups.items():
        if any(needle in label for needle in needles):return code
    return "L" if label.strip() else ""


PROVENANCE_LABELS = {"L": "Livorno", "E": "Empoli", "V": "Viareggio", "F": "Firenze", "P": "Pisa"}


def order_email_settings(conn):
    defaults={
        "order_recipient_email":"[QUI INSERIRÒ IL MIO INDIRIZZO EMAIL]",
        "order_email_subject":ORDER_EMAIL_SUBJECT,
        "order_email_template":DEFAULT_ORDER_EMAIL_TEMPLATE,
        "order_email_signature":"Pet Paradise",
        "order_sender_name":"Pet Paradise",
        "order_phone":"",
        "order_default_notes":"",
    }
    keys=tuple(defaults);marks=','.join('?' for _ in keys)
    saved={row["key"]:row["value"] for row in conn.execute(f"SELECT key,value FROM settings WHERE key IN ({marks})",keys)}
    defaults.update(saved)
    return defaults


def render_order_email(quantity,settings,notes=""):
    default_notes=str(settings.get("order_default_notes") or "").strip()
    extra_notes=str(notes or "").strip()
    notes_text="\n".join(value for value in (default_notes,extra_notes) if value)
    body=str(settings.get("order_email_template") or DEFAULT_ORDER_EMAIL_TEMPLATE)
    template_tokens=set(re.findall(r"\{\{[a-z_]+\}\}",body))
    replacements={
        "{{quantita}}":str(int(quantity)),
        "{{note_predefinite}}":notes_text,
        "{{firma}}":str(settings.get("order_email_signature") or "").strip(),
        "{{nome_mittente}}":str(settings.get("order_sender_name") or "").strip(),
        "{{telefono}}":str(settings.get("order_phone") or "").strip(),
    }
    for token,value in replacements.items():body=body.replace(token,value)
    body=re.sub(r"\n{3,}","\n\n",body.strip())
    signature=str(settings.get("order_email_signature") or "").strip()
    phone=str(settings.get("order_phone") or "").strip()
    footer=[]
    if signature and "{{firma}}" not in template_tokens:footer.append(signature)
    if phone and "{{telefono}}" not in template_tokens:footer.append(f"Tel. {phone}")
    if footer:body=f"{body}\n\n"+"\n".join(footer)
    return str(settings.get("order_email_subject") or ORDER_EMAIL_SUBJECT).strip(),body


def water_order_body(quantity,notes=""):
    _,body=render_order_email(quantity,{"order_email_template":DEFAULT_ORDER_EMAIL_TEMPLATE,"order_email_signature":"Pet Paradise"},notes)
    return body


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


def safe_return_path(value, fallback="/"):
    parsed=urlparse(str(value or ""))
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return fallback
    return parsed.path+(f"?{parsed.query}" if parsed.query else "")


def load_preferences(user_id):
    with db() as c:
        return {row["key"]: row["value"] for row in c.execute("SELECT key,value FROM user_preferences WHERE user_id=?", (user_id,))}


def parse_preference_list(value, max_len=40):
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data[:max_len] if isinstance(item, (str, int, float))]


def reorder_by_saved(default_items, saved_keys, key_fn):
    keys_present = {key_fn(item) for item in default_items}
    ordered_keys = [k for k in saved_keys if k in keys_present]
    seen = set(ordered_keys)
    ordered_keys += [key_fn(item) for item in default_items if key_fn(item) not in seen]
    by_key = {key_fn(item): item for item in default_items}
    return [by_key[k] for k in ordered_keys]


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


def sequence_code_parts(number):
    match=re.fullmatch(r"(CR|SM)-(\d+)",str(number or ""))
    return (match.group(1),int(match.group(2)),len(match.group(2))) if match else None


def format_sequence_code(prefix,value,width=6):
    return f"{prefix}-{value:0{max(6,width)}d}"


CSS = r"""
:root{--ink:#24312c;--muted:#6e7b75;--brand:#a74045;--brand2:#7f3035;--paper:#fff;--bg:#f4f1ed;--line:#ded8d1;--green:#39745b;--gold:#a87926;--safe-top:env(safe-area-inset-top,0px);--safe-bottom:env(safe-area-inset-bottom,0px);--safe-left:env(safe-area-inset-left,0px);--safe-right:env(safe-area-inset-right,0px)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:inherit;text-decoration:none}.top{height:68px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:18px;padding:0 28px;position:sticky;top:0;z-index:5}.brand{font-weight:800;font-size:19px;color:var(--brand)}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:1.5px}.nav{display:flex;gap:8px;margin-left:auto}.nav a{padding:9px 12px;border-radius:9px}.nav a:hover{background:#f3eeea}.wrap{max-width:1280px;margin:0 auto;padding:28px}.titlebar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px}h1{margin:0;font-size:28px}h2{font-size:18px;margin:0 0 15px}.sub{color:var(--muted)}.btn{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:10px;background:var(--brand);color:white;padding:11px 16px;font-weight:700;cursor:pointer}.btn:hover{background:var(--brand2)}.btn.ghost{background:white;color:var(--ink);border:1px solid var(--line)}.grid{display:grid;gap:16px}.stats{grid-template-columns:repeat(3,1fr)}.card{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:20px;box-shadow:0 3px 15px #4b39260a}.stat{display:flex;justify-content:space-between;align-items:center}.stat b{font-size:32px;color:var(--brand)}.badge{display:inline-flex;padding:5px 9px;border-radius:99px;background:#eee9e3;font-size:12px;font-weight:700}.tag-red{background:#e53935;color:white}.tag-orange{background:#fb8c00;color:white}.tag-outline-orange{background:white;color:#fb8c00;border:2px solid #fb8c00}.tag-purple{background:#7e57c2;color:white}.tag-yellow,.pay-yellow{background:#fdd835;color:#3b3100}.tag-pink{background:#f06292;color:white}.tag-blue,.pay-blue{background:#1e88e5;color:white}.tag-green,.pay-green{background:#43a047;color:white}.status-stack{display:flex;gap:5px;flex-wrap:wrap}.form-grid{grid-template-columns:repeat(2,1fr)}.wide{grid-column:1/-1}.section{background:#fff;border:1px solid var(--line);border-radius:15px;padding:20px}.fields{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}.field{display:flex;flex-direction:column;gap:6px}.field.full{grid-column:1/-1}label{font-weight:650;font-size:13px}input,select,textarea{width:100%;border:1px solid #cfc8c0;border-radius:9px;padding:11px 12px;background:white;color:var(--ink);font:inherit}input[type=checkbox]{width:auto;min-height:auto}textarea{min-height:90px;resize:vertical}input:focus,select:focus,textarea:focus{outline:3px solid #a7404520;border-color:var(--brand)}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:13px;border-bottom:1px solid var(--line)}th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}.tablebox{overflow:auto;-webkit-overflow-scrolling:touch;background:white;border:1px solid var(--line);border-radius:15px}.actions{display:flex;gap:10px;flex-wrap:wrap}.flash{padding:13px 16px;border-radius:10px;background:#e5f2eb;color:#285b45;margin-bottom:16px}.warning{background:#fff1d8;color:#765315}.login{max-width:410px;margin:10vh auto;background:white;padding:34px;border-radius:18px;border:1px solid var(--line)}.timeline{border-left:2px solid var(--line);margin-left:7px;padding-left:20px}.event{padding:0 0 18px;position:relative}.event:before{content:'';position:absolute;width:10px;height:10px;border-radius:50%;background:var(--brand);left:-26px;top:5px}.kvs{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.kv{background:#faf8f5;border-radius:10px;padding:12px}.kv small{display:block;color:var(--muted)}.signature-pad{width:100%;height:260px;border:2px dashed var(--line);border-radius:14px;background:white;touch-action:none}
body{background:radial-gradient(circle at top left,#fff8f3 0,#f4f1ed 34%,#ece5dd 100%)}.top{backdrop-filter:saturate(1.2) blur(10px);box-shadow:0 8px 28px #4b392612}.brand{letter-spacing:.2px}.nav a{font-weight:650}.nav a.btn{box-shadow:0 8px 20px #a7404524}.wrap{animation:ppmFade .18s ease-out}.titlebar h1{letter-spacing:-.03em}.section,.card,.tablebox,.login{box-shadow:0 10px 30px #4b39260d}.section{transition:box-shadow .15s ease, transform .15s ease}.card{transition:transform .15s ease,box-shadow .15s ease}.card:hover{transform:translateY(-2px);box-shadow:0 14px 34px #4b392617}.btn{box-shadow:0 6px 16px #a740451f}.btn.ghost{box-shadow:none}.kv{border:1px solid #eee6df}.tablebox table tr:hover td{background:#fffaf6}input,select,textarea{transition:border-color .15s ease,box-shadow .15s ease}.danger{border-width:1px}.trash-note{background:#fff7e8;border:1px solid #f0cf9d;color:#765315;border-radius:12px;padding:12px 14px;margin-bottom:16px}.empty-state{text-align:center;padding:32px;color:var(--muted)}@keyframes ppmFade{from{opacity:.78;transform:translateY(3px)}to{opacity:1;transform:none}}
.practice-layout{grid-template-columns:2fr 1fr}@media(max-width:800px){html,body{width:100%;max-width:100%;overflow-x:hidden}body{font-size:16px}.wrap{padding:14px}.top{height:auto;min-height:64px;padding:10px 12px;align-items:flex-start}.brand{font-size:17px}.nav{gap:4px;flex-wrap:wrap}.nav a{padding:8px 9px}.nav a span{display:none}.btn{width:100%;min-height:46px}.actions{width:100%}.actions .btn,.actions form{flex:1 1 100%}.stats,.form-grid,.fields,.kvs,.practice-layout{grid-template-columns:1fr}.section{padding:16px;border-radius:13px}.titlebar{align-items:flex-start;flex-direction:column}.wide{grid-column:auto}input,select,textarea{font-size:16px;min-height:46px}th:nth-child(4),td:nth-child(4){display:none}.badge{margin:2px 2px 2px 0}}
.danger{border-color:#e2a5a5;background:#fff7f7}.btn.danger-btn{background:#b42323;color:white}.btn.danger-btn:hover{background:#8f1d1d}.danger-note{color:#8f1d1d;font-weight:700}
.home-logo{width:118px;height:118px;object-fit:contain;border-radius:24px;background:white;padding:10px;border:1px solid var(--line);box-shadow:0 8px 24px #4b392614}
.month-block{margin-bottom:18px}.month-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.month-heading{display:flex;align-items:center;gap:10px}.month-toggle{width:34px;height:34px;border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--brand);font-size:22px;font-weight:800;line-height:1;cursor:pointer}.month-content[hidden]{display:none}.dashboard-table-scroll{overflow-x:scroll;scrollbar-gutter:stable;padding-bottom:8px;scrollbar-color:var(--brand) #eee7e0;scrollbar-width:auto}.dashboard-table-scroll table{min-width:1650px}.dashboard-table-scroll::-webkit-scrollbar{height:13px}.dashboard-table-scroll::-webkit-scrollbar-track{background:#eee7e0;border-radius:99px}.dashboard-table-scroll::-webkit-scrollbar-thumb{background:var(--brand);border:3px solid #eee7e0;border-radius:99px}
.hidden{display:none!important}
.practice-code-cr{color:#1e88e5}.practice-code-sm{color:#111}
.lookup{position:relative}.lookup-results{position:absolute;left:0;right:0;top:100%;z-index:20;background:white;border:1px solid var(--line);border-radius:12px;margin-top:6px;box-shadow:0 10px 30px #4b392626;max-height:340px;overflow:auto}.lookup-item{display:block;width:100%;border:0;background:white;text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);cursor:pointer;color:var(--ink)}.lookup-item:hover,.lookup-item:focus{background:#f7f2ee;outline:none}.lookup-item b{display:block}.lookup-item small{display:block;color:var(--muted);white-space:normal}.lookup-item-urn{display:flex;align-items:center;gap:10px}.lookup-item-thumb{width:36px;height:36px;flex:0 0 36px;border-radius:8px;object-fit:cover;background:#e2e8f0}.lookup-state{padding:10px 12px;color:var(--muted);font-size:13px}.selected-box{border:1px solid #b8d7c8;background:#edf7f2;color:#285b45;border-radius:10px;padding:12px;margin-top:10px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}.selected-box .btn{width:auto}
/* Dark professional interface */
:root{--ink:#f5f7fb;--muted:#9ca7b8;--brand:#e9475b;--brand2:#ff6377;--paper:#111722;--bg:#090d14;--line:#293140;--green:#35c98a;--gold:#f5b83d}
html{color-scheme:dark}body{background:radial-gradient(circle at 78% -10%,#31121e 0,transparent 32%),linear-gradient(135deg,#090d14,#0d121b 55%,#090d14);min-height:100dvh;color:var(--ink);font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.top{position:fixed;left:0;top:0;bottom:0;width:226px;height:100dvh;display:flex;flex-direction:column;align-items:stretch;padding:24px 16px;background:#0c111acc;border:0;border-right:1px solid var(--line);backdrop-filter:blur(18px);box-shadow:16px 0 50px #0003}
.brand{display:flex;align-items:center;gap:11px;padding:4px 6px 24px;color:#ff6678;font-size:18px}.brand-logo{width:48px;height:48px;object-fit:contain}.brand-logo-light{display:none}.light-theme .brand-logo-dark{display:none}.light-theme .brand-logo-light{display:block}.brand-copy small{margin-top:2px;color:#c1c8d3}
.nav{margin:8px 0 0;display:flex;flex:1;flex-direction:column;width:100%;gap:7px}.nav a{display:flex;align-items:center;gap:11px;padding:11px 13px;color:#c5ccd7;border:1px solid transparent;border-radius:12px;font-weight:650}.nav a:hover{color:white;background:#181f2b;border-color:#2d3645}.nav a:first-child{color:#ff697b;background:linear-gradient(90deg,#381922,#1c151d);border-color:#53212e}.nav-icon{width:20px;text-align:center;font-size:17px}.nav .btn{margin-top:10px;color:white;background:linear-gradient(135deg,#ff526a,#cc2946);box-shadow:0 10px 26px #e9475b35}.nav .logout{margin-top:auto}.wrap{max-width:1500px;margin-left:226px;padding:34px 38px;animation:ppmFade .22s ease-out}
h1{font-size:30px;letter-spacing:-.035em}h2{color:#eef1f6}.sub{color:var(--muted)}
.section,.card,.tablebox,.login{background:linear-gradient(145deg,#131a26,#0f151f);border:1px solid var(--line);box-shadow:0 18px 50px #0003}.card{position:relative;overflow:hidden}.card:after{content:"";position:absolute;inset:auto -35px -50px auto;width:110px;height:110px;border-radius:50%;background:#e9475b12;filter:blur(4px)}.card:hover{border-color:#4a3340;box-shadow:0 20px 48px #0006,0 0 0 1px #e9475b12}.stat b{color:#ff6175}.btn{background:linear-gradient(135deg,#f05267,#c92d49);box-shadow:0 8px 24px #e9475b30}.btn:hover{background:linear-gradient(135deg,#ff6679,#df3652)}.btn.ghost{background:#171e2a;color:#e9edf3;border-color:#303948}.btn.ghost:hover{background:#202938}
.section-tone-blue{--section-accent:#4f8fdc}.section-tone-teal{--section-accent:#35a89a}.section-tone-violet{--section-accent:#8b72cf}.section-tone-amber{--section-accent:#c79343}.section-tone-slate{--section-accent:#70829b}.section[class*="section-tone-"]{border-top:3px solid var(--section-accent);background:linear-gradient(145deg,color-mix(in srgb,var(--section-accent) 8%,#131a26),#0f151f 72%)}.section[class*="section-tone-"]>h2,.section-heading-row>h2{color:color-mix(in srgb,var(--section-accent) 58%,#f4f7fb)}.section-heading-row{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:12px}.section-heading-row h2{margin:0}.section-header-flag{flex:0 0 auto}.section-header-flag .field{margin:0}.section-header-flag label{margin:0}.section-header-flag .badge{font-size:11px;letter-spacing:.04em}.light-theme .section[class*="section-tone-"]{background:linear-gradient(145deg,color-mix(in srgb,var(--section-accent) 7%,#fff),#fff 74%)}.light-theme .section[class*="section-tone-"]>h2,.light-theme .section-heading-row>h2{color:color-mix(in srgb,var(--section-accent) 72%,#1f2937)}
input,select,textarea{background:#0c121b;border-color:#323c4b;color:#f3f5f8}input:focus,select:focus,textarea:focus{outline:3px solid #e9475b22;border-color:#e9475b}.kv{background:#0c121b;border-color:#252e3b}.tablebox,table{background:#101620}th,td{border-color:#252d39}th{color:#8f9bad}.tablebox table tr:hover td{background:#171f2b}.lookup-results,.lookup-item{background:#131a25;border-color:#2b3544;color:#f5f7fb}.lookup-item:hover,.lookup-item:focus{background:#202938}.selected-box{background:#10261f;border-color:#245a46;color:#7ce0b7}
.badge{background:#252d39;color:#dfe4eb}.tag-outline-orange{background:#271c10}.pay-yellow,.tag-yellow{background:#5a4610;color:#ffe28a}.login{margin-left:auto;margin-right:auto}.home-logo{background:#070a0f;border-color:#303948;box-shadow:0 12px 34px #0006;padding:7px}.practice-code-sm{color:#f3f5f8}.practice-code-cr{color:#6fa8ff}.danger{background:#291318;border-color:#6b2734}.trash-note,.warning{background:#302412;border-color:#624c23;color:#f6d58e}.flash{background:#102a20;color:#8be3bb}.signature-pad{background:#fff}
.install-btn{display:none}.install-btn.ready{display:flex}.install-hint{position:fixed;right:22px;bottom:22px;z-index:50;max-width:340px;padding:16px;background:#141b27;border:1px solid #353f4f;border-radius:16px;box-shadow:0 20px 60px #0008}.install-hint b{display:block;color:#ff6679;margin-bottom:5px}.install-hint button{margin-top:10px}
@media(max-width:900px){.top{position:sticky;width:100%;height:auto;min-height:66px;bottom:auto;flex-direction:row;align-items:center;padding:8px 12px}.brand{padding:0}.brand-logo{width:42px;height:42px}.brand-copy{display:none}.nav{margin:0 0 0 auto;flex-direction:row;align-items:center;width:auto;overflow-x:auto}.nav a{padding:9px}.nav a span:not(.nav-icon){display:none}.nav-icon{display:inline-block!important}.nav .btn{margin:0}.nav .btn span:not(.nav-icon){display:none}.nav .logout{margin:0}.wrap{margin-left:0;padding:18px 14px}.stats{grid-template-columns:1fr 1fr}.home-logo{width:82px;height:82px}.tablebox th,.tablebox td{display:table-cell!important;white-space:nowrap}.install-hint{left:14px;right:14px;bottom:14px}.titlebar{gap:12px}}
@media(max-width:560px){.stats{grid-template-columns:1fr}.brand-logo{width:38px;height:38px}.nav a{font-size:0}.nav-icon{font-size:18px}.nav .btn{width:auto;min-height:42px}.wrap{padding-top:14px}h1{font-size:25px}}
/* Premium dashboard layout */
body{background:#111827;color:#f8fafc}.icon{width:20px;height:20px;flex:0 0 20px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.skip-link{position:fixed;top:8px;left:8px;z-index:200;transform:translateY(-150%);padding:10px 14px;border-radius:9px;background:#fff;color:#111827}.skip-link:focus{transform:none}
.top{width:212px;padding:20px 14px;background:#0b1220;border-color:#263246}.brand{padding:0 8px 20px}.brand-logo{width:50px;height:50px}.brand-copy{font-size:17px}.nav{gap:3px;overflow-y:auto;padding-right:3px}.nav a,.nav button{min-height:42px;padding:9px 11px;border-radius:10px}.nav a:first-child{background:linear-gradient(90deg,#4a1826,#241523);border-color:#642239}.nav .install-btn{margin-top:8px}.nav .logout{margin-top:12px}
.app-header{position:fixed;left:212px;right:0;top:0;height:76px;z-index:40;display:flex;align-items:center;justify-content:flex-end;gap:20px;padding:14px 30px;background:#111827e8;border-bottom:1px solid #263246;backdrop-filter:blur(16px)}.header-search{width:min(640px,48vw);display:flex;align-items:center;gap:9px;padding:0 13px;border:1px solid #334155;border-radius:11px;background:#172033}.header-search input{min-height:42px;padding:8px 0;background:transparent;border:0}.header-search input:focus{outline:0}.header-actions{display:flex;align-items:center;justify-content:flex-end;gap:9px;width:100%}.icon-btn{display:inline-grid;place-items:center;width:42px;height:42px;padding:0;border:1px solid #334155;border-radius:11px;background:#172033;color:#cbd5e1;cursor:pointer}.icon-btn:hover{color:#fff;border-color:#ef405f}.phone-action-btn{width:30px;height:30px;border-radius:9px;vertical-align:middle}.phone-action-btn .icon{width:15px;height:15px}.phone-action-btn.call-btn{background:linear-gradient(135deg,#fb4c67,#d9284c);color:#fff;border-color:transparent}.phone-action-btn.call-btn:hover{color:#fff;border-color:transparent;filter:brightness(1.1)}.phone-action-btn.whatsapp-btn{background:linear-gradient(135deg,#22c55e,#15803d);color:#fff;border-color:transparent}.phone-action-btn.whatsapp-btn:hover{color:#fff;border-color:transparent;filter:brightness(1.1)}.header-new{gap:7px}.header-actions time{min-width:104px;padding:6px 10px;border:1px solid #334155;border-radius:10px;text-align:center;font-weight:700;background:#172033}.header-actions time small{display:block;color:#94a3b8;font-size:10px;text-transform:capitalize}.wrap{max-width:1600px;margin-left:212px;margin-right:auto;padding:106px 30px 42px}
.dashboard-wrap{max-width:1500px}.welcome{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}.welcome h1{font-size:30px}.welcome p{margin:7px 0 0;color:#94a3b8}.dashboard-heading{margin:24px 0 12px;font-size:15px;color:#dce4ef}.dashboard-states{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.metric-card,.payment-card{position:relative;min-height:126px;display:flex;align-items:center;justify-content:space-between;gap:15px;padding:20px;border:1px solid #334155;border-radius:14px;background:#1f2937;overflow:hidden;box-shadow:0 14px 36px #03071235;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.metric-card:before,.payment-card:before{content:"";position:absolute;inset:0;background:linear-gradient(120deg,var(--card-glow),transparent 62%);pointer-events:none}.metric-card:hover,.payment-card:hover{transform:translateY(-3px);border-color:#56657a;box-shadow:0 20px 44px #03071260}.metric-copy,.payment-card>span:first-child{position:relative;display:flex;flex-direction:column}.metric-card small,.payment-card small{font-style:normal;color:#e2e8f0}.metric-card strong,.payment-card strong{margin-top:4px;font-size:30px;line-height:1.05}.metric-card em,.payment-card em{margin-top:9px;color:#94a3b8;font-size:12px;font-style:normal}.metric-icon,.activity-icon{position:relative;display:grid;place-items:center;width:46px;height:46px;border-radius:12px;background:var(--icon-bg);color:var(--icon-color);box-shadow:0 8px 22px var(--icon-shadow)}.state-red{--card-glow:#83184375;--icon-bg:#881337;--icon-color:#fb7185;--icon-shadow:#e11d4840}.state-blue{--card-glow:#17255480;--icon-bg:#172554;--icon-color:#60a5fa;--icon-shadow:#2563eb40}.state-purple{--card-glow:#3b076480;--icon-bg:#3b0764;--icon-color:#c084fc;--icon-shadow:#9333ea40}.state-green{--card-glow:#052e2b85;--icon-bg:#064e3b;--icon-color:#4ade80;--icon-shadow:#16a34a40}
.dashboard-payments{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.payment-card{min-height:116px}.payment-due{--card-glow:#713f123d;--icon-bg:#573713;--icon-color:#fbbf24;--icon-shadow:#f59e0b35}.payment-deposit{--card-glow:#17255465;--icon-bg:#172554;--icon-color:#60a5fa;--icon-shadow:#2563eb35}.payment-paid{--card-glow:#052e2b75;--icon-bg:#064e3b;--icon-color:#4ade80;--icon-shadow:#16a34a35}
.dashboard-lower{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(350px,.8fr);gap:16px;margin-top:24px}.dashboard-panel{min-height:350px;padding:20px;border:1px solid #334155;border-radius:15px;background:#1f2937;box-shadow:0 18px 48px #03071235}.dashboard-panel>header{display:flex;align-items:flex-start;justify-content:space-between;gap:15px}.dashboard-panel h2{margin:0;font-size:16px}.dashboard-panel header p{margin:8px 0 0;color:#94a3b8}.dashboard-panel header p strong{color:#fff;font-size:21px}.dashboard-panel header a{color:#fb7185;font-size:13px}.income-chart{display:block;width:100%;height:auto;margin-top:14px}.chart-grid line{stroke:#334155;stroke-width:1}.chart-grid text,.chart-dates text{fill:#94a3b8;font-size:11px}.chart-area{fill:url(#incomeArea)}.chart-line{fill:none;stroke:#ef405f;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;filter:drop-shadow(0 5px 8px #ef405f55)}.income-chart circle{fill:#fb7185;stroke:#1f2937;stroke-width:2}
.activity-list{display:flex;flex-direction:column;margin-top:14px}.activity-item{display:grid;grid-template-columns:42px minmax(0,1fr) auto;align-items:center;gap:11px;padding:12px 0;border-bottom:1px solid #334155}.activity-item:last-child{border-bottom:0}.activity-item b,.activity-item small{display:block}.activity-item b{font-size:13px}.activity-item small{margin-top:3px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.activity-item time{color:#94a3b8;font-size:11px}.activity-icon{width:38px;height:38px;--icon-bg:#243244;--icon-color:#5eead4;--icon-shadow:transparent}.activity-1 .activity-icon{--icon-bg:#422006;--icon-color:#fbbf24}.activity-2 .activity-icon{--icon-bg:#3b0764;--icon-color:#c084fc}.activity-3 .activity-icon{--icon-bg:#4c0519;--icon-color:#fb7185}.activity-empty{padding:40px 10px;color:#94a3b8;text-align:center}
.bottom-nav,.more-menu,.more-backdrop{display:none}.tag-red{background:#7f1d2d;color:#fecdd3}.tag-orange{background:#7c2d12;color:#fed7aa}.tag-outline-orange{background:#3b1d0c;color:#fdba74;border-color:#f97316}.tag-purple{background:#4c1d95;color:#e9d5ff}.tag-yellow{background:#713f12;color:#fef08a}.tag-pink{background:#831843;color:#fbcfe8}.tag-blue{background:#1e3a8a;color:#bfdbfe}.tag-green{background:#14532d;color:#bbf7d0}
.search-after-results{margin-top:32px}.search-after-results>h2{display:none}.search-after-results .section{box-shadow:0 12px 34px #0307122e}.advanced-search{margin:16px 0 24px;border:1px solid var(--line);border-radius:14px;background:#202c3d;overflow:hidden}.advanced-search summary{display:flex;align-items:center;justify-content:space-between;min-height:48px;padding:12px 16px;cursor:pointer;font-weight:600;list-style:none}.advanced-search summary::-webkit-details-marker{display:none}.advanced-search summary:after{content:'+';font-size:22px;line-height:1}.advanced-search[open] summary:after{content:'−'}.advanced-search form{margin:0;border:0;border-top:1px solid var(--line);border-radius:0;box-shadow:none!important}.light-theme .advanced-search{background:#fff}
.dashboard-recent{margin-top:26px}.dashboard-recent .titlebar{margin-bottom:12px}.dashboard-recent .titlebar a{color:#fb7185}.load-previous-month{display:flex;justify-content:center;padding:8px 0 24px}.load-previous-month .btn{width:auto;min-width:240px}.budget-add{align-self:end;width:auto!important;min-height:42px;margin-top:auto}.budget-layout{display:block}.budget-workspace{display:grid;gap:12px}.budget-row{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(270px,.75fr);gap:16px;padding:12px;border:1px solid #334155;border-radius:13px;background:#11192566}.budget-cell{display:grid;align-content:start;gap:10px;min-width:0}.budget-cell-right .modern-check{min-height:42px}.budget-cell-right .field{min-width:0}.budget-cell:empty{display:none}.economic-estimate{margin-top:20px;padding-top:18px;border-top:1px solid #3b4658}.economic-estimate h3{margin:0 0 12px;font-size:15px}.catalog-summary-form{display:grid;gap:8px;margin-top:9px}.catalog-summary-form .modern-check{min-height:40px;padding:8px 10px}.light-theme .budget-row{border-color:#cbd5e1;background:#f8fafc}.light-theme .economic-estimate{border-color:#cbd5e1}
*:focus-visible{outline:3px solid #fb7185!important;outline-offset:3px}.light-theme{background:#eef2f7;color:#111827}.light-theme .app-header,.light-theme .top{background:#fff;color:#111827}.light-theme .dashboard-panel,.light-theme .metric-card,.light-theme .payment-card,.light-theme .section,.light-theme .tablebox{background:#fff;color:#111827}.light-theme .header-search,.light-theme .icon-btn,.light-theme .header-actions time{background:#f8fafc;color:#111827}.light-theme .welcome p,.light-theme .metric-card em,.light-theme .payment-card em,.light-theme .activity-item small,.light-theme .activity-item time{color:#64748b}
@media(max-width:1100px){.dashboard-states{grid-template-columns:repeat(2,1fr)}.dashboard-lower{grid-template-columns:1fr}.header-actions time{display:none}}
@media(max-width:900px){body{min-height:100dvh;padding-bottom:calc(82px + var(--safe-bottom))}#main-content{min-height:100dvh;padding-left:var(--safe-left);padding-right:var(--safe-right)}.top{position:fixed;left:var(--safe-left);right:var(--safe-right);top:0;width:auto;height:calc(64px + var(--safe-top));min-height:calc(64px + var(--safe-top));padding:calc(7px + var(--safe-top)) 14px 7px;border-right:0;border-bottom:1px solid #263246}.top .nav{display:none}.brand-copy{display:inline}.brand-logo{width:42px;height:42px}.app-header{position:fixed;left:auto;right:calc(10px + var(--safe-right));top:calc(7px + var(--safe-top));width:auto;height:50px;padding:0;background:transparent;border:0;backdrop-filter:none}.header-search,.header-actions time,.header-new span{display:none}.header-actions{gap:7px}.header-new{width:42px;height:42px;padding:0}.wrap{margin-left:0;padding:calc(88px + var(--safe-top)) 14px 22px}.bottom-nav{position:fixed;display:grid;grid-template-columns:repeat(5,1fr);align-items:end;left:0;right:0;bottom:0;z-index:90;height:calc(72px + var(--safe-bottom));padding:6px max(8px,var(--safe-right)) calc(5px + var(--safe-bottom)) max(8px,var(--safe-left));background:#0b1220ed;border-top:1px solid #334155;backdrop-filter:blur(18px)}.bottom-nav a,.bottom-nav button{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;border:0;background:transparent;color:#94a3b8;font-size:10px;transition:transform .1s ease,background-color .1s ease}.bottom-nav a:active,.bottom-nav button:active{background:#1d2938;transform:scale(.94)}.bottom-nav .bottom-new:active{transform:scale(.92)}.light-theme .bottom-nav a:active,.light-theme .bottom-nav button:active{background:#f1f5f9}.bottom-nav .icon{width:21px;height:21px}.bottom-nav a:first-child{color:#fb7185}.bottom-nav .bottom-new{align-self:center;width:52px;height:52px;margin:-18px auto 0;border-radius:50%;background:linear-gradient(135deg,#fb4c67,#d9284c);color:#fff;box-shadow:0 8px 28px #ef405f70}.bottom-new span{display:none}.more-backdrop{position:fixed;display:block;inset:0;z-index:94;background:#020617aa;opacity:0;pointer-events:none;transition:opacity .2s}.more-menu{position:fixed;display:flex;flex-direction:column;gap:5px;left:calc(14px + var(--safe-left));right:calc(14px + var(--safe-right));bottom:calc(82px + var(--safe-bottom));z-index:95;max-height:72dvh;padding:16px;border:1px solid #334155;border-radius:18px;background:#111827;box-shadow:0 25px 80px #0009;overflow:auto;transform:translateY(120%);opacity:0;transition:transform .22s ease,opacity .22s}.more-menu a{display:flex;align-items:center;gap:11px;padding:11px;border-radius:10px;color:#e2e8f0}.more-menu a:hover{background:#1f2937}.more-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px}.more-title .icon-btn{font-size:24px}.more-open .more-menu{transform:none;opacity:1}.more-open .more-backdrop{opacity:1;pointer-events:auto}.install-hint{left:calc(14px + var(--safe-left));right:calc(14px + var(--safe-right));bottom:calc(14px + var(--safe-bottom))}.skip-link{top:calc(8px + var(--safe-top));left:calc(8px + var(--safe-left))}.light-theme .bottom-nav,.light-theme .more-menu{background:#fff}.dashboard-lower{grid-template-columns:1fr}}
@media(max-width:900px){.app-header .header-search{position:fixed;display:flex;left:calc(14px + var(--safe-left));right:calc(14px + var(--safe-right));top:calc(70px + var(--safe-top));width:auto;height:44px;z-index:41;background:#172033;box-shadow:0 8px 24px #02061740}.app-header .header-search input{min-width:0;height:42px}.app-header .header-actions{width:auto}.wrap{padding-top:calc(136px + var(--safe-top))}.light-theme .app-header .header-search{background:#fff;border-color:#cbd5e1;box-shadow:0 8px 24px #64748b20}}
@media(max-width:620px){.brand-copy{display:none}.dashboard-states,.dashboard-payments{grid-template-columns:1fr}.metric-card,.payment-card{min-height:104px}.dashboard-panel{padding:15px;min-height:0}.welcome h1{font-size:24px}.dashboard-lower{margin-top:18px}.income-chart{min-width:0}.activity-item{grid-template-columns:38px minmax(0,1fr)}.activity-item time{display:none}}
.income-panel{display:block;color:inherit;transition:transform .18s ease,border-color .18s ease}.income-panel:hover{transform:translateY(-2px);border-color:#fb7185}.panel-link{color:#fb7185;font-size:12px;font-weight:700}.balance-total{min-width:210px;padding:14px 18px;border:1px solid #334155;border-radius:14px;background:#1f2937;text-align:right}.balance-total small,.balance-total strong{display:block}.balance-total small{color:#94a3b8}.balance-total strong{margin-top:3px;color:#fb7185;font-size:25px}.balance-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:20px}.balance-card{display:flex;flex-direction:column;gap:7px;padding:17px;border:1px solid #334155;border-radius:13px;background:#1f2937;transition:transform .15s,border-color .15s}.balance-card:hover,.balance-card.active{transform:translateY(-2px);border-color:#fb7185}.balance-card small{color:#94a3b8}.balance-card strong{font-size:20px}.balance-table{margin-top:4px}.light-theme .balance-card,.light-theme .balance-total{background:#fff;color:#111827}
.section-collapse-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:12px}.section-collapse-head h2{margin:0}.collapse-toggle{display:grid;place-items:center;width:32px;height:32px;padding:0;border:1px solid #334155;border-radius:9px;background:#172033;color:#e7ecf3;font-size:18px;font-weight:700;line-height:1;cursor:pointer}.collapse-toggle:hover{border-color:#ef405f;color:#fff}.collapsible-body[hidden]{display:none}.light-theme .collapse-toggle{background:#fff;border-color:#cbd5e1;color:#111827}
@media(max-width:620px){.balance-grid{grid-template-columns:1fr 1fr}.balance-total{width:100%;text-align:left}.balances-wrap .titlebar{align-items:stretch}.panel-link{display:none}}
.conversation-list{display:flex;flex-direction:column;gap:12px}.conversation-card{display:grid;grid-template-columns:minmax(280px,1.2fr) minmax(420px,1fr) auto;align-items:center;gap:20px;padding:18px;border:1px solid #334155;border-radius:15px;background:#1f2937;box-shadow:0 12px 34px #0307122e}.conversation-main{display:grid;grid-template-columns:46px minmax(0,1fr);align-items:center;gap:13px}.conversation-avatar{display:grid;place-items:center;width:46px;height:46px;border-radius:13px;background:#064e3b;color:#4ade80}.conversation-main h2{margin:0 0 5px;font-size:16px}.conversation-main p{margin:3px 0;color:#94a3b8;font-size:13px}.conversation-main p b,.conversation-main a{color:#e2e8f0}.conversation-message{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.conversation-card dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin:0}.conversation-card dl div{min-width:0}.conversation-card dt{color:#94a3b8;font-size:10px;text-transform:uppercase;letter-spacing:.05em}.conversation-card dd{margin:4px 0 0;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.conversation-action{text-align:right}.whatsapp-open{background:linear-gradient(135deg,#22c55e,#15803d);white-space:nowrap}.message-accettato_da_meta{background:#1e3a8a;color:#bfdbfe}.message-consegnato{background:#14532d;color:#bbf7d0}.message-letto{background:#164e63;color:#a5f3fc}.message-fallito{background:#7f1d1d;color:#fecaca}.pagination{display:flex;align-items:center;justify-content:center;gap:18px;margin:20px 0;color:#94a3b8}.pagination a,.page-disabled{padding:9px 13px;border:1px solid #334155;border-radius:10px}.pagination a{color:#f8fafc;background:#1f2937}.page-disabled{opacity:.45}.light-theme .conversation-card{background:#fff;color:#111827}.light-theme .conversation-main p{color:#64748b}
.practice-row-link{cursor:pointer;outline:0}.practice-row-link:focus{outline:2px solid #fb7185;outline-offset:-2px}.practice-row-link.row-selected td{background:#ef405f26;box-shadow:inset 0 0 0 2px #ef405f}.light-theme .practice-row-link.row-selected td{background:#ef405f1a}.cremation-row-done td{color:#22c55e}.cremation-row-done .badge,.cremation-row-done .sub{color:inherit}.cremation-check{display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-weight:700;font-size:12px;white-space:nowrap}.cremation-check input{width:auto}.tag-outline-green{background:#052e2b;color:#86efac;border:2px solid #22c55e}.light-theme{color-scheme:light;--ink:#111827;--muted:#526174;--paper:#fff;--bg:#eef2f7;--line:#cbd5e1}.light-theme h1,.light-theme h2,.light-theme label,.light-theme td,.light-theme .activity-item b,.light-theme .metric-card small,.light-theme .payment-card small,.light-theme .dashboard-panel header p strong,.light-theme .conversation-main p b,.light-theme .conversation-main a,.light-theme .pagination a{color:#111827}.light-theme input,.light-theme select,.light-theme textarea,.light-theme .lookup-results,.light-theme .lookup-item,.light-theme .kv,.light-theme table,.light-theme .login{background:#fff;color:#111827;border-color:#cbd5e1}.light-theme input::placeholder,.light-theme textarea::placeholder{color:#64748b}.light-theme th,.light-theme .sub,.light-theme .kv small,.light-theme .conversation-card dt,.light-theme .pagination{color:#526174}.light-theme th,.light-theme td,.light-theme .activity-item{border-color:#d7dee8}.light-theme .tablebox table tr:hover td,.light-theme .practice-row-link:focus td,.light-theme .lookup-item:hover,.light-theme .lookup-item:focus{background:#f1f5f9}.light-theme .btn.ghost,.light-theme .pagination a{background:#fff;color:#111827;border-color:#cbd5e1}.light-theme .badge{background:#e2e8f0;color:#1e293b}.light-theme .tag-red{background:#fee2e2;color:#991b1b}.light-theme .tag-orange{background:#ffedd5;color:#9a3412}.light-theme .tag-purple{background:#f3e8ff;color:#6b21a8}.light-theme .tag-yellow,.light-theme .pay-yellow{background:#fef9c3;color:#713f12}.light-theme .tag-pink{background:#fce7f3;color:#9d174d}.light-theme .tag-blue,.light-theme .pay-blue{background:#dbeafe;color:#1e40af}.light-theme .tag-green,.light-theme .pay-green{background:#dcfce7;color:#166534}.light-theme .tag-outline-orange{background:#fff7ed;color:#c2410c}.light-theme .tag-outline-green{background:#f0fdf4;color:#166534;border-color:#22c55e}.light-theme .selected-box{background:#ecfdf5;color:#166534;border-color:#86efac}.light-theme .nav a{color:#334155}.light-theme .nav a:hover{background:#f1f5f9;color:#111827}.light-theme .nav a:first-child{background:#fff1f2;color:#be123c;border-color:#fecdd3}.light-theme .more-menu a{color:#334155}.light-theme .more-menu a:hover{background:#f1f5f9}.light-theme .chart-grid line{stroke:#cbd5e1}.light-theme .chart-grid text,.light-theme .chart-dates text{fill:#526174}.light-theme .income-chart circle{stroke:#fff}.light-theme .install-hint{background:#fff;color:#111827;border-color:#cbd5e1}.light-theme .danger{background:#fff1f2}.light-theme .warning,.light-theme .trash-note{background:#fff7ed;color:#7c2d12}.light-theme .flash:not(.warning){background:#ecfdf5;color:#166534}.light-theme .conversation-main p b,.light-theme .conversation-main a{color:#111827}
.practice-status{background:transparent!important;border:2px solid currentColor}.practice-status-blue{color:#60a5fa!important;border-color:#3b82f6}.practice-status-red{color:#fb7185!important;border-color:#ef4444}.practice-status-yellow{color:#fde047!important;border-color:#eab308}.practice-status-green{color:#4ade80!important;border-color:#22c55e}.light-theme .practice-status-blue{color:#1d4ed8!important}.light-theme .practice-status-red{color:#b91c1c!important}.light-theme .practice-status-yellow{color:#854d0e!important}.light-theme .practice-status-green{color:#15803d!important}
.modern-check{display:flex;align-items:center;gap:10px;min-height:46px;padding:10px 13px;border:1px solid #3b4658;border-radius:12px;background:linear-gradient(145deg,#182130,#111925);color:#e8edf5;cursor:pointer;transition:border-color .16s,transform .16s,box-shadow .16s}.modern-check:hover{transform:translateY(-1px);border-color:#fb7185;box-shadow:0 8px 22px #02061745}.modern-check input[type=checkbox]{width:20px;height:20px;margin:0;accent-color:#ef405f}.modern-check span{font-size:12px;font-weight:800;letter-spacing:.025em}.light-theme .modern-check{background:linear-gradient(145deg,#fff,#f1f5f9);color:#172033;border-color:#cbd5e1}.invoice-inline{display:grid;gap:8px}.invoice-inline input{min-width:0}.invoice-inline .btn{width:100%}
.pay-green{border:2px solid #22c55e!important}.pay-yellow{border:2px solid #eab308!important}.pay-blue{border:2px solid #3b82f6!important}.notification-badge{position:absolute;display:grid;place-items:center;min-width:19px;height:19px;padding:0 5px;border-radius:99px;background:#dc2626;color:#fff;font:700 11px/1 system-ui;transform:translate(13px,-13px);box-shadow:0 0 0 2px #111827}.nav-notification{position:relative}.notification-center{display:grid;gap:10px}.notification-item{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:13px;align-items:center;padding:15px;border:1px solid #334155;border-radius:13px;background:#1f2937}.notification-item.unread{border-left:4px solid #ef405f}.notification-icon{display:grid;place-items:center;width:42px;height:42px;border-radius:12px;background:#172033;font-size:21px}.notification-copy b,.notification-copy small{display:block}.notification-copy p{margin:4px 0;color:#cbd5e1}.notification-copy small{color:#94a3b8}.notification-actions{display:flex;gap:8px;align-items:center}.toggle-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.toggle-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border:1px solid #334155;border-radius:11px}.toggle-row input{width:22px;height:22px}.permission-prompt{position:fixed;right:20px;bottom:20px;z-index:150;max-width:390px;padding:18px;border:1px solid #475569;border-radius:16px;background:#172033;color:#fff;box-shadow:0 24px 70px #000a}.permission-prompt p{color:#cbd5e1}.sw-update-banner{position:fixed;left:14px;right:14px;bottom:calc(14px + var(--safe-bottom));z-index:160;display:flex;align-items:center;justify-content:space-between;gap:14px;max-width:420px;margin:0 auto;padding:12px 16px;border:1px solid #475569;border-radius:14px;background:#172033;color:#fff;box-shadow:0 20px 60px #000a;animation:ppmFade .2s ease-out}.sw-update-banner button{border:0;border-radius:9px;padding:8px 14px;font-weight:700;background:var(--brand);color:#fff;cursor:pointer}.light-theme .sw-update-banner{background:#fff;color:#111827;border-color:#cbd5e1}.quick-payment{display:flex;gap:7px;align-items:center}.quick-payment select,.quick-payment input{min-width:110px}.quick-payment .btn{width:auto}.light-theme .notification-item,.light-theme .toggle-row,.light-theme .permission-prompt{background:#fff;color:#111827;border-color:#cbd5e1}.light-theme .notification-copy p{color:#334155}
.practice-list-table{min-width:1500px}.practice-list-table th:first-child,.practice-list-table td:first-child{position:sticky;left:0;z-index:3;min-width:215px;background:#101620;box-shadow:8px 0 14px #02061735}.practice-list-table th:first-child{z-index:4}.light-theme .practice-list-table th:first-child,.light-theme .practice-list-table td:first-child{background:#fff}.inline-statuses{display:grid;gap:8px;min-width:170px}.inline-state-select{min-height:38px;padding:7px 32px 7px 10px;border-width:2px;font-weight:800}.payment-popover{position:fixed;inset:0;z-index:180;display:grid;place-items:center;padding:18px;background:#020617b8}.payment-popover[hidden]{display:none}.payment-dialog{width:min(620px,100%);max-height:90dvh;overflow:auto;padding:20px;border:1px solid #475569;border-radius:16px;background:#172033;box-shadow:0 28px 90px #000c}.payment-dialog h2{margin-bottom:6px}.payment-dialog .fields{margin-top:16px}.light-theme .payment-dialog{background:#fff;color:#111827}.message-programmato{background:#4c1d95;color:#ede9fe}.message-in_invio{background:#78350f;color:#fef3c7}.message-annullato{background:#334155;color:#cbd5e1}.conversation-error{grid-column:1/-1}.conversation-error dd{white-space:normal;color:#fca5a5}.conversation-action.actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.conversation-action form{margin:0}
@media(max-width:620px){.practice-list-table th:first-child,.practice-list-table td:first-child{box-sizing:border-box;width:132px;min-width:132px;max-width:132px;padding-left:12px;padding-right:10px;white-space:normal!important}}
@media(max-width:1150px){.conversation-card{grid-template-columns:1fr 1fr}.conversation-action{grid-column:1/-1;text-align:left}}
@media(max-width:700px){.conversation-card{grid-template-columns:1fr;gap:14px}.conversation-card dl{grid-template-columns:1fr 1fr}.conversation-action{grid-column:auto}.conversation-action.actions{justify-content:stretch}.conversation-action form,.conversation-action .btn{width:100%}.pagination{gap:8px;justify-content:space-between}.pagination span{font-size:11px;text-align:center}.conversation-message{white-space:normal}.conversations-wrap .titlebar h1{font-size:24px}}
@media(max-width:700px){.practice-layout{display:block!important}.practice-layout>.grid,.practice-layout>aside{width:100%;min-width:0}.practice-layout>aside{margin-top:16px}.practice-layout .kvs{grid-template-columns:1fr}.practice-layout .section{max-width:100%;overflow-wrap:anywhere}.toggle-list{grid-template-columns:1fr}.notification-item{grid-template-columns:40px minmax(0,1fr)}.notification-actions{grid-column:1/-1}.notification-actions .btn{width:100%}.permission-prompt{left:14px;right:14px;bottom:calc(84px + var(--safe-bottom));max-width:none}.quick-payment{min-width:430px}}
.balance-chart{min-height:0;margin:0 0 20px}.article-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.article-card{display:flex;flex-direction:column;gap:16px;min-height:170px;padding:20px;border:1px solid #334155;border-radius:15px;background:#1f2937}.article-card h2{margin:0;font-size:18px}.article-card p{margin:0;color:#94a3b8}.article-card form{margin-top:auto}.article-card .btn{width:100%}.light-theme .article-card{background:#fff;color:#111827;border-color:#cbd5e1}
.urn-stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:20px}.urn-stat,.urn-card{padding:17px;border:1px solid #334155;border-radius:14px;background:#1f2937}.urn-stat small,.urn-meta{color:#94a3b8}.urn-stat strong{display:block;margin-top:5px;font-size:24px}.urn-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px}.urn-card{display:flex;flex-direction:column;gap:12px;min-width:0}.urn-card img,.urn-placeholder{width:100%;aspect-ratio:4/3;border-radius:10px;object-fit:cover;background:#111827}.urn-placeholder{display:grid;place-items:center;color:#64748b;font-weight:700}.urn-card h2{margin:0}.urn-card .actions{margin-top:auto}.stock-good{color:#86efac}.stock-low{color:#fdba74}.stock-out{color:#fca5a5}.light-theme .urn-stat,.light-theme .urn-card{background:#fff;color:#111827;border-color:#cbd5e1}.light-theme .urn-placeholder{background:#e2e8f0}.urn-filter{margin-bottom:20px}.urn-detail{grid-template-columns:minmax(280px,.7fr) minmax(0,1.3fr)}
@media(max-width:900px){.app-header{position:fixed;left:0;right:0}.app-header .header-actions{position:absolute;right:calc(10px + var(--safe-right));top:calc(7px + var(--safe-top))}.app-header .header-search{position:fixed;left:calc(14px + var(--safe-left));right:calc(14px + var(--safe-right));top:calc(70px + var(--safe-top))}.article-grid{grid-template-columns:1fr 1fr}}
@media(max-width:900px){.urn-stats{grid-template-columns:repeat(2,1fr)}.urn-grid{grid-template-columns:1fr 1fr}.urn-detail{grid-template-columns:1fr}}
@media(max-width:620px){.article-grid,.urn-grid{grid-template-columns:1fr}.urn-stats{grid-template-columns:1fr 1fr}}
@media(max-width:900px){.top{z-index:42}.top .brand{width:48px;padding:0}.top .brand-copy{display:none}.app-header{left:calc(64px + var(--safe-left));right:var(--safe-right);top:0;z-index:43;width:auto;height:calc(64px + var(--safe-top));padding:calc(7px + var(--safe-top)) 8px 7px 0;background:transparent;border:0;backdrop-filter:none}.app-header .header-actions{position:static;display:flex;width:100%;height:100%;gap:5px}.app-header .header-search{position:static;display:flex;flex:1 1 auto;min-width:0;width:auto;height:42px;padding:0 9px;background:#172033;box-shadow:none}.app-header .header-search input{min-width:0;height:40px;min-height:40px;font-size:16px}.app-header .header-search .icon{width:17px;height:17px;flex-basis:17px}.app-header .icon-btn,.app-header .header-new{flex:0 0 38px;width:38px;height:38px;min-height:38px;padding:0}.app-header .header-actions time,.app-header .header-new span{display:none}.wrap{padding-top:calc(88px + var(--safe-top))}.light-theme .app-header .header-search{background:#f8fafc;border-color:#cbd5e1;box-shadow:none}}
@media(max-width:390px){.app-header{left:calc(58px + var(--safe-left));padding-right:5px}.app-header .header-actions{gap:4px}.app-header .header-search{padding:0 7px}.app-header .icon-btn,.app-header .header-new{flex-basis:35px;width:35px;height:35px;min-height:35px}.top{padding-left:10px;padding-right:10px}.top .brand-logo{width:38px;height:38px}}
/* Cleaner, lighter dark theme */
body{background:#172131;color:#e7ecf3;font-weight:400}.top{background:#111a29;border-color:#344156}.app-header{background:#172131ed;border-color:#344156}.section,.card,.tablebox,.login{background:linear-gradient(145deg,#202c3d,#1b2636);border-color:#38475c;box-shadow:0 12px 34px #080d162b}.section[class*="section-tone-"]{background:linear-gradient(145deg,color-mix(in srgb,var(--section-accent) 5%,#202c3d),#1b2636 76%)}.dashboard-panel,.metric-card,.payment-card,.balance-card,.balance-total,.conversation-card,.article-card,.urn-stat,.urn-card{background:#202c3d;border-color:#3a495e;box-shadow:0 12px 32px #080d1626}.tablebox,table{background:#1b2636}.practice-list-table th:first-child,.practice-list-table td:first-child{background:#1b2636}.kv,input,select,textarea,.header-search,.icon-btn,.header-actions time{background:#182334;border-color:#3b4a5f}.tablebox table tr:hover td{background:#253247}.lookup-results,.lookup-item{background:#202c3d;border-color:#3b4a5f}.lookup-item:hover,.lookup-item:focus{background:#29384d}h1{font-weight:650}h2{font-weight:600}b,strong{font-weight:600}label{font-weight:550}.nav a,.nav button{font-weight:500}.btn{font-weight:600}.badge,.inline-state-select{font-weight:600}th{font-weight:600;letter-spacing:.035em}.metric-card strong,.payment-card strong,.stat b{font-weight:650}.light-theme .practice-list-table th:first-child,.light-theme .practice-list-table td:first-child{background:#fff}
.dashboard-section-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin:24px 0 12px}.dashboard-section-head .dashboard-heading{margin:0}.period-selector{display:inline-grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:3px;padding:3px;border:1px solid #3b4a5f;border-radius:12px;background:#182334}.period-selector a{display:grid;place-items:center;min-width:88px;min-height:38px;padding:8px 12px;border-radius:9px;color:#aeb9c8;font-size:13px;font-weight:600}.period-selector a:hover{color:#fff;background:#253248}.period-selector a.active{color:#fff;background:#ef405f;box-shadow:0 5px 14px #ef405f40}.dashboard-chart-only{margin-top:24px}.dashboard-chart-only .dashboard-panel{display:block;min-height:0}.light-theme .period-selector{background:#f1f5f9;border-color:#cbd5e1}.light-theme .period-selector a{color:#526174}.light-theme .period-selector a.active{color:#fff}.state-yellow{--card-glow:#713f124f;--icon-bg:#573713;--icon-color:#fde047;--icon-shadow:#eab30840}.inline-save-note{min-height:16px;color:#86efac;font-size:11px}.inline-save-note.error{color:#fca5a5}
.water-order-card{max-width:620px;margin:0 auto;padding:28px;text-align:center}.water-order-card h1{margin-bottom:6px}.water-order-card .sub{margin-bottom:24px}.quantity-stepper{display:grid;grid-template-columns:64px minmax(100px,160px) 64px;justify-content:center;align-items:center;gap:12px}.quantity-stepper button{width:64px;height:64px;border-radius:18px;font-size:32px}.quantity-stepper input{height:76px;text-align:center;font-size:34px;font-weight:650;padding:8px}.quick-quantities{display:flex;justify-content:center;gap:9px;flex-wrap:wrap;margin:16px 0 22px}.quick-quantities button{min-height:44px}.order-now{width:min(100%,420px);min-height:58px;font-size:17px}.last-order{margin:18px 0 0}.order-secondary-actions{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:18px}.recent-orders{margin-top:22px}.order-preview{margin:0;padding:16px;border:1px solid #3b4a5f;border-radius:12px;background:#182334;color:#e7ecf3;font:400 14px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}.light-theme .order-preview{background:#f8fafc;color:#24312c;border-color:#cbd5e1}.orders-wrap .tablebox{margin-top:14px}.order-modal[hidden]{display:none}.order-modal{position:fixed;inset:0;z-index:1200;display:grid;place-items:center;padding:20px;background:rgba(4,10,20,.72)}.order-modal-card{width:min(560px,100%);max-height:min(720px,calc(100dvh - 40px));overflow:auto;padding:24px;border:1px solid var(--line);border-radius:18px;background:#202c3d;box-shadow:0 24px 70px rgba(0,0,0,.45)}.light-theme .order-modal-card{background:#fff}.order-modal-card .order-preview{max-height:210px;overflow:auto}.modal-open{overflow:hidden}.admin-order-settings{max-width:820px}.admin-order-settings textarea{min-height:240px}
@media(max-width:620px){.dashboard-section-head{align-items:stretch;flex-direction:column;gap:9px}.period-selector{width:100%}.period-selector a{min-width:0;min-height:44px;padding:8px 5px}.dashboard-chart-only{margin-top:18px}.dashboard-wrap{padding-bottom:calc(28px + var(--safe-bottom))}.water-order-card{padding:22px 15px}.quantity-stepper{grid-template-columns:58px minmax(86px,130px) 58px;gap:8px}.quantity-stepper button{width:58px;height:58px}.quantity-stepper input{height:68px;font-size:30px}.order-now{width:100%}.orders-wrap .tablebox{max-width:100%;overflow-x:auto}.orders-wrap{padding-bottom:calc(92px + var(--safe-bottom))}.order-modal{padding:12px 12px calc(78px + var(--safe-bottom))}.order-modal-card{padding:18px;max-height:calc(100dvh - 110px - var(--safe-bottom))}}
.calendar-wrap{max-width:1700px}.calendar-toolbar,.calendar-nav,.calendar-view-switch,.calendar-quick-actions{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.calendar-quick-actions .btn{flex:1}.calendar-toolbar{justify-content:space-between;margin-bottom:16px}.calendar-view-switch{padding:4px;border:1px solid #3b4a5f;border-radius:13px;background:#182334}.calendar-view-switch a{min-height:40px;padding:9px 12px;border-radius:9px;color:#aeb9c8}.calendar-view-switch a.active{background:#ef405f;color:#fff}.calendar-board{display:grid;gap:12px}.calendar-day-list{display:grid;gap:10px;min-height:260px}.calendar-event-shell{position:relative;overflow:hidden;border-radius:14px;touch-action:pan-y}.calendar-swipe-actions{position:absolute;inset:0;display:flex;align-items:stretch;justify-content:space-between;pointer-events:none}.calendar-swipe-action{display:flex;align-items:center;justify-content:center;min-width:112px;padding:0 18px;font-weight:800;letter-spacing:.01em;color:#fff;opacity:.96}.calendar-swipe-action.complete{background:#059669}.calendar-swipe-action.cancel{background:#dc2626}.calendar-event{position:relative;z-index:2;display:grid;grid-template-columns:72px minmax(0,1fr);gap:13px;align-items:center;padding:13px 15px;border:1px solid #3a495e;border-left:4px solid currentColor;border-radius:14px;background:#202c3d;box-shadow:0 8px 24px #080d1626;transition:transform .18s ease,box-shadow .18s ease;will-change:transform}.calendar-event.swiping{transition:none;box-shadow:0 12px 30px #05080f55}.calendar-event-time{align-self:stretch;display:flex;align-items:center;justify-content:center;padding-right:12px;border-right:1px solid #3a495e;color:#aeb9c8;font-size:13px;font-weight:750}.calendar-event-main{display:block;min-width:0;color:inherit}.calendar-event h3{margin:0 0 4px;font-size:15px;color:currentColor}.calendar-event p{margin:2px 0;color:#aeb9c8;font-size:12px}.calendar-red{color:#fb7185}.calendar-yellow{color:#fde047}.calendar-green{color:#4ade80}.calendar-blue{color:#60a5fa}.calendar-purple{color:#c084fc}.calendar-dark{color:#94a3b8}.calendar-week{display:grid;grid-template-columns:repeat(7,minmax(170px,1fr));gap:8px;min-width:1190px}.calendar-week-scroll{overflow-x:auto;padding-bottom:8px}.calendar-day-column{min-height:520px;padding:10px;border:1px solid #3a495e;border-radius:14px;background:#1b2636}.calendar-day-column>header{position:sticky;top:0;padding:8px;background:#1b2636;z-index:2}.calendar-day-column .calendar-event{grid-template-columns:1fr;padding:10px;margin-top:8px}.calendar-day-column .calendar-event-time{font-weight:600}.calendar-month{display:grid;grid-template-columns:repeat(7,minmax(105px,1fr));gap:5px}.calendar-month-day{min-height:128px;padding:8px;border:1px solid #3a495e;border-radius:11px;background:#1b2636;overflow:hidden}.calendar-month-day.selected{outline:2px solid #ef405f}.calendar-month-day>a{display:block;font-weight:600;margin-bottom:6px}.calendar-band{display:block;margin:4px 0;padding:4px 6px;border-left:3px solid currentColor;border-radius:6px;background:#253247;color:inherit;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.calendar-dots{display:flex;gap:4px;flex-wrap:wrap}.calendar-dot{width:7px;height:7px;border-radius:50%;background:currentColor}.calendar-mixed{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(320px,.7fr);gap:16px}.calendar-form{max-width:980px;margin:auto}.calendar-steps{display:flex;justify-content:center;gap:8px;margin-bottom:18px}.calendar-steps button{display:grid;place-items:center;width:36px;height:36px;padding:0;border:1px solid #475569;border-radius:50%;background:#253247;color:#e7ecf3;font-weight:750;cursor:pointer}.calendar-steps button.active{border-color:#ef405f;background:#ef405f;color:#fff}.calendar-steps button:hover{border-color:#fb7185}.calendar-type-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.calendar-type-option{display:flex;min-height:92px;padding:14px;border:1px solid #3b4a5f;border-radius:15px;background:#202c3d;cursor:pointer}.calendar-type-option input{width:auto}.calendar-type-option:has(input:checked){border-color:#ef405f;box-shadow:0 0 0 2px #ef405f35}.calendar-form-step[hidden]{display:none}.calendar-repeat-list{display:grid;gap:10px}.calendar-repeat-row{display:grid;grid-template-columns:repeat(5,minmax(0,1fr)) auto;gap:8px}.calendar-tabs{display:flex;gap:5px;border-bottom:1px solid #3b4a5f;margin-bottom:16px}.calendar-tabs a{padding:10px 13px}.calendar-tabs a.active{color:#fb7185;border-bottom:2px solid #fb7185}.calendar-detail-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr);gap:16px}.calendar-comment{padding:12px;border:1px solid #3b4a5f;border-radius:12px;background:#182334}.calendar-comment+.calendar-comment{margin-top:8px}.calendar-fab{position:fixed;right:30px;bottom:30px;z-index:30;width:58px;height:58px;border-radius:50%;font-size:27px}.calendar-status-modal{max-width:430px;text-align:center}.calendar-status-modal-icon{display:grid;place-items:center;width:58px;height:58px;margin:0 auto 14px;border-radius:18px;background:#ef405f;color:#fff;font-size:30px;font-weight:900}.calendar-status-modal .actions{justify-content:center;margin-top:18px}.calendar-status-modal .btn{min-width:140px}.light-theme .calendar-event,.light-theme .calendar-day-column,.light-theme .calendar-month-day,.light-theme .calendar-type-option{background:#fff;border-color:#cbd5e1}.light-theme .calendar-subblock{border-color:#cbd5e1}.light-theme .calendar-day-column>header{background:#fff}.light-theme .calendar-event p,.light-theme .calendar-event-time{color:#526174}
@media(max-width:900px){.calendar-wrap{padding-bottom:calc(100px + var(--safe-bottom))}.calendar-toolbar{align-items:stretch;flex-direction:column}.calendar-view-switch{display:grid;grid-template-columns:repeat(3,1fr);overflow:auto}.calendar-view-switch a{text-align:center;white-space:nowrap}.calendar-mixed,.calendar-detail-grid{grid-template-columns:1fr}.calendar-month{grid-template-columns:repeat(7,minmax(43px,1fr));gap:3px}.calendar-month-day{min-height:76px;padding:5px}.calendar-month-day .calendar-band{display:none}.calendar-month-day>a{font-size:12px}.calendar-type-grid{grid-template-columns:1fr}.calendar-repeat-row{grid-template-columns:1fr 1fr}.calendar-repeat-row .full-mobile{grid-column:1/-1}.calendar-event{grid-template-columns:58px minmax(0,1fr);padding:12px 13px}.calendar-event-time{padding-right:9px}.calendar-swipe-action{min-width:96px;padding:0 14px;font-size:13px}.calendar-fab{right:18px;bottom:calc(88px + var(--safe-bottom))}.calendar-form{padding-bottom:calc(90px + var(--safe-bottom))}.header-event-new{display:none!important}}
.calendar-date-nav{display:grid;grid-template-columns:48px minmax(0,1fr) 48px;gap:8px;width:min(560px,100%);margin:0 auto 16px}.calendar-date-nav .calendar-date-title{position:relative;display:grid;place-items:center;min-height:46px;border:1px solid #3b4a5f;border-radius:13px;background:#182334;font-weight:700;cursor:pointer}.calendar-date-title input{position:absolute;inset:0;opacity:0;cursor:pointer}.calendar-today{grid-column:1/-1;width:100%}.calendar-settings-link{white-space:nowrap}.calendar-dot{width:8px;height:8px}.calendar-dot-red{color:#fb7185}.calendar-dot-yellow{color:#fde047}.calendar-dot-blue{color:#60a5fa}.calendar-dot-cyan{color:#22d3ee}.calendar-dot-purple{color:#c084fc}.calendar-dot-gray{color:#94a3b8}.calendar-month-day .calendar-dots{margin:5px 0}.calendar-form-step{animation:calendarStepIn .2s ease both;touch-action:pan-y}.calendar-form-step.step-back{animation-name:calendarStepBack}@keyframes calendarStepIn{from{opacity:0;transform:translateX(18px)}to{opacity:1;transform:none}}@keyframes calendarStepBack{from{opacity:0;transform:translateX(-18px)}to{opacity:1;transform:none}}.calendar-first-operator{max-width:340px;margin-bottom:18px}.calendar-title-zone-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,.8fr);gap:13px;grid-column:1/-1}.calendar-time-control{display:grid;grid-template-columns:minmax(0,1fr) 44px;gap:7px}.calendar-time-control button{width:44px;min-height:44px;padding:0}.calendar-native-time{position:absolute!important;width:1px!important;height:1px!important;opacity:0;pointer-events:none}.calendar-zone-results{display:grid;position:absolute;left:0;right:0;top:100%;z-index:30;margin-top:5px;padding:6px;border:1px solid #475569;border-radius:12px;background:#182334;box-shadow:0 18px 45px #0008}.calendar-zone-field{position:relative}.calendar-zone-results button{padding:10px;border:0;border-radius:8px;background:transparent;color:#e7ecf3;text-align:left}.calendar-zone-results button:hover{background:#253247}.calendar-estimate-preset{display:flex;align-items:center;padding:0 10px;font-weight:650}.calendar-other-description{grid-column:span 3}.calendar-wizard-error{min-height:20px;color:#fca5a5;font-size:12px}.calendar-validation{margin-bottom:14px}.calendar-form [aria-invalid="true"]{border-color:#fb7185;box-shadow:0 0 0 3px #fb718533}.calendar-animal-title{grid-column:1/-1;color:#fda4af;font-size:12px;letter-spacing:.06em}.lookup-kind{display:inline-flex;margin-right:7px;padding:2px 6px;border-radius:99px;background:#334155;color:#f8fafc;font-size:10px}.create-sheet-backdrop{position:fixed;display:block;inset:0;z-index:96;background:#020617aa;opacity:0;pointer-events:none;transition:opacity .2s}.create-sheet{position:fixed;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;left:50%;bottom:calc(82px + var(--safe-bottom));z-index:97;width:min(390px,calc(100% - 24px));padding:10px;border:1px solid #7f1d2d;border-radius:16px;background:#172131;box-shadow:0 20px 55px #0009;opacity:0;pointer-events:none;transform:translate(-50%,18px) scale(.97);transition:opacity .18s,transform .2s cubic-bezier(.2,.8,.2,1)}.create-sheet a{display:flex;align-items:center;justify-content:center;gap:8px;min-height:46px;padding:9px 10px;border-radius:11px;background:linear-gradient(135deg,#fb4c67,#d9284c);color:#fff;font-size:12px;font-weight:750;text-align:center}.create-sheet .icon{width:18px;height:18px;color:#fff}.create-menu-open .create-sheet{opacity:1;pointer-events:auto;transform:translate(-50%,0) scale(1)}.create-menu-open .create-sheet-backdrop{opacity:1;pointer-events:auto}.autosave-status{position:sticky;top:76px;z-index:4;display:flex;align-items:center;gap:8px;width:max-content;max-width:100%;margin:0 0 14px auto;padding:7px 11px;border:1px solid #334155;border-radius:99px;background:#111827e8;color:#cbd5e1;font-size:12px;backdrop-filter:blur(12px)}.autosave-status[data-state="saving"]{color:#fde68a}.autosave-status[data-state="saved"]{color:#86efac}.autosave-status[data-state="error"],.autosave-status[data-state="conflict"]{color:#fca5a5}.autosave-retry{border:0;background:transparent;color:inherit;text-decoration:underline;cursor:pointer}.light-theme .calendar-date-title,.light-theme .calendar-zone-results,.light-theme .create-sheet,.light-theme .autosave-status{background:#fff}.light-theme .calendar-zone-results button{color:#111827}.light-theme .create-sheet a{color:#fff}
@media(max-width:900px){.calendar-view-switch{display:none}.calendar-toolbar{margin-bottom:8px}.calendar-quick-actions{display:grid;grid-template-columns:minmax(0,1fr) auto auto;width:100%;gap:8px}.calendar-quick-actions .btn{width:auto;min-height:38px;padding:8px 10px;font-size:12px}.calendar-title-zone-row{grid-template-columns:minmax(0,1fr) minmax(110px,.8fr)}.calendar-date-nav{grid-template-columns:44px minmax(0,1fr) 44px}.calendar-date-nav .btn{width:auto;min-height:44px;padding:8px}.calendar-month-day{min-height:82px}.calendar-form .titlebar{display:grid;grid-template-columns:minmax(0,1fr) auto}.calendar-form .titlebar .btn{width:42px;min-height:42px;padding:0;border-radius:50%}.calendar-type-option{min-height:72px;padding:12px}.calendar-first-operator{max-width:none}.calendar-repeat-row{grid-template-columns:1fr 1fr}.calendar-estimate-row{grid-template-columns:minmax(90px,1fr) minmax(90px,.8fr)}.calendar-estimate-row .calendar-other-description{grid-column:1/-1}.calendar-estimate-row button{display:none}}
/* Calendar operational UI: faithful dark mobile layout based on the approved reference. */
.calendar-wrap{max-width:1180px}.calendar-main-title{margin-bottom:10px}.calendar-main-title h1{font-size:25px}.calendar-quick-actions{gap:7px}.calendar-date-nav{width:100%;max-width:none;grid-template-columns:42px minmax(0,1fr) 42px;margin-bottom:10px}.calendar-date-nav .calendar-date-title{min-height:42px;border:0;background:transparent;font-size:17px}.calendar-date-nav .btn{border:0;background:transparent;box-shadow:none;font-size:22px}.calendar-today{display:none}.calendar-day-timeline{position:relative;min-height:var(--timeline-height);margin-top:8px;padding-left:66px;background:transparent}.calendar-timeline-grid,.calendar-timeline-events{position:absolute;inset:0}.calendar-timeline-line{position:absolute;left:0;right:0;height:1px;display:grid;grid-template-columns:58px 1fr;align-items:center}.calendar-timeline-line time{transform:translateY(-50%);color:#7f8b9d;font-size:11px;text-align:left}.calendar-timeline-line span{height:1px;background:#263243}.calendar-timeline-events{left:66px}.calendar-timeline-event{position:absolute;left:8px;right:8px}.calendar-timeline-event.lane-1{left:34%;right:8px}.calendar-timeline-event .calendar-event{min-height:46px}.calendar-event{grid-template-columns:1fr;gap:2px;min-height:48px;padding:8px 11px;border-width:1px;border-left-width:3px;border-radius:8px;background:#172231;box-shadow:none}.calendar-event-time{display:block;position:absolute;top:8px;left:10px;height:auto;padding:0;border:0;color:currentColor;font-size:11px}.calendar-event-main{padding-left:47px}.calendar-event h3{margin:0 0 3px;font-size:13px;line-height:1.15}.calendar-event p{margin:0;font-size:10.5px;line-height:1.25}.calendar-week-scroll{border-top:1px solid #263243}.calendar-week{display:grid;grid-template-columns:42px repeat(7,minmax(116px,1fr));gap:0;min-width:854px;background:#111a27}.calendar-week:before{display:none!important;content:none}.calendar-week-time-column{width:42px;min-width:42px}.calendar-week-time-column>header{padding:12px 2px;font-size:9px;text-align:center}.calendar-week-axis time{right:4px;font-size:10px}.calendar-day-column{position:relative;min-height:640px;padding:0;border:0;border-right:1px solid #263243;border-radius:0;background:transparent}.calendar-day-column>header{height:46px;padding:9px 6px;border-bottom:1px solid #263243;background:#111a27;text-align:center}.calendar-day-column .calendar-event{margin:7px 5px;padding:7px 8px}.calendar-day-column .calendar-event-main{padding-left:0}.calendar-day-column .calendar-event-time{position:static;margin-bottom:3px}.calendar-month-composition{display:grid;gap:14px}.calendar-month{gap:0;border-top:1px solid #263243;border-left:1px solid #263243;background:#0f1723}.calendar-month-day{min-height:72px;padding:7px;border:0;border-right:1px solid #263243;border-bottom:1px solid #263243;border-radius:0;background:transparent}.calendar-month-day.selected{outline:0;background:#16322d}.calendar-month-day.selected>a{display:grid;place-items:center;width:28px;height:28px;margin:-2px auto 4px;border-radius:50%;background:#12a36f;color:#fff}.calendar-month-day>a{text-align:center;font-size:12px}.calendar-month-day .calendar-dots{justify-content:center}.calendar-month-agenda{padding:12px;border-radius:13px;background:#121c29}.calendar-month-agenda>header{display:flex;align-items:center;justify-content:space-between;margin-bottom:9px}.calendar-month-agenda h2{margin:0;font-size:16px}.calendar-month-agenda .calendar-event{margin-bottom:7px}.calendar-type-grid{grid-template-columns:1fr;gap:7px}.calendar-type-option{align-items:center;min-height:64px;padding:11px 13px;border-radius:10px;background:#151f2d}.calendar-type-option input{appearance:none;width:18px;height:18px;border:2px solid currentColor;border-radius:50%}.calendar-type-option input:checked{box-shadow:inset 0 0 0 4px #151f2d;background:currentColor}.calendar-type-option span{display:grid;gap:2px}.calendar-type-option b{font-size:15px}.calendar-type-option small{font-size:11px}.calendar-steps{position:relative;justify-content:space-between;max-width:360px;margin:0 auto 24px}.calendar-steps:before{content:'';position:absolute;top:16px;left:22px;right:22px;height:1px;background:#344154}.calendar-steps button{z-index:1;width:32px;height:32px;background:#0f1723}.calendar-steps button.active{background:#0ea66f;border-color:#0ea66f}.calendar-form-step{padding:18px;border-radius:13px}.calendar-form-step h2{font-size:17px}.calendar-title-zone-row{align-items:end}.calendar-tabs{justify-content:center}.calendar-comment{border:0;background:#192434}.calendar-comment:nth-child(even){margin-left:32px}.calendar-fab{background:#0ea66f}.calendar-settings-link,.calendar-quick-actions .icon-btn{border:0;background:transparent}.calendar-settings-link:hover,.calendar-quick-actions .icon-btn:hover{background:#1d2938}.calendar-status-modal-icon{background:#0ea66f}
@media(max-width:900px){.calendar-wrap{padding:18px 12px calc(94px + var(--safe-bottom));margin-left:0}.calendar-main-title{display:none}.calendar-date-nav{position:sticky;top:76px;z-index:12;padding:5px 0;background:#0d1521}.calendar-day-timeline{margin-left:0}.calendar-timeline-event.lane-1{left:8px}.calendar-week-scroll{margin:0 -12px;padding:0 12px 8px}.calendar-week{min-width:760px}.calendar-month{margin:0 -4px}.calendar-month-day{min-height:54px;padding:5px 2px}.calendar-month-day>a{font-size:11px}.calendar-month-day.selected>a{width:25px;height:25px}.calendar-title-zone-row{grid-template-columns:minmax(0,1fr) minmax(112px,.8fr)}.calendar-form{padding:16px 12px calc(90px + var(--safe-bottom));margin-left:0}.calendar-form .titlebar h1{font-size:22px}.calendar-form-step{padding:14px}.calendar-type-option{min-height:60px}.calendar-event h3{font-size:14px}.calendar-event p{font-size:11px}}

/* Operational calendar refinements */
.calendar-wrap{max-width:1420px}.calendar-main-title{align-items:center}.calendar-main-title .calendar-quick-actions{display:flex;width:auto;gap:8px}.calendar-event{min-height:58px;padding:7px 11px;border:2px solid currentColor;border-left-width:5px;border-radius:12px;box-shadow:none}.calendar-event-time{font-size:12px}.calendar-event h3{font-size:18px;line-height:1.08;margin-bottom:3px}.calendar-event p{font-size:11px;line-height:1.25;margin:1px 0}.calendar-day-column{min-height:360px;padding:7px}.calendar-day-column .calendar-event{min-height:54px;padding:7px;margin-top:6px}.calendar-day-column .calendar-event-time{display:block;padding:0 0 3px;border:0;justify-content:flex-start}.calendar-day-column .calendar-event h3{font-size:16px}.calendar-week{grid-template-columns:repeat(7,minmax(145px,1fr));min-width:1015px}.calendar-type-option b{font-size:17px;line-height:1.12}.calendar-detail-status{display:grid;grid-template-columns:minmax(180px,.7fr) minmax(180px,1fr) auto;align-items:end;gap:10px;padding:14px;border:1px solid #ef405f;border-radius:14px;background:#ef405f0f}.calendar-detail-status label{grid-column:1/-1}.calendar-day-timeline{position:relative;min-height:var(--timeline-height);margin-top:8px;padding-left:58px}.calendar-timeline-grid,.calendar-timeline-events{position:absolute;inset:0 0 0 0}.calendar-timeline-line{position:absolute;left:0;right:0;height:1px;display:grid;grid-template-columns:52px minmax(0,1fr);align-items:center}.calendar-timeline-line time{padding-right:8px;color:#94a3b8;font-size:11px;text-align:right;transform:translateY(-50%)}.calendar-timeline-line span{display:block;border-top:1px solid #334155}.calendar-timeline-events{left:62px}.calendar-timeline-event{position:absolute;left:0;right:0;height:54px}.calendar-timeline-event.lane-1{left:51%}.calendar-timeline-event.lane-0:has(+.lane-1){right:51%}.calendar-timeline-event .calendar-event{height:54px;min-height:54px;grid-template-columns:1fr;overflow:hidden}.calendar-timeline-event .calendar-event-time{display:none}.calendar-timeline-event .calendar-event-main{overflow:hidden}.calendar-timeline-event .calendar-event h3,.calendar-timeline-event .calendar-event p{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.calendar-mixed>*{min-width:0}.calendar-mixed .calendar-week-scroll{width:100%;max-width:100%}.calendar-mixed-week{grid-template-columns:minmax(0,1fr)}
@media(max-width:900px){.calendar-wrap{padding-left:18px;padding-right:18px}.calendar-main-title{display:flex;align-items:center;justify-content:space-between;text-align:left;margin-top:-8px}.calendar-main-title h1{font-size:25px}.calendar-main-title .sub{font-size:13px}.calendar-main-title .calendar-quick-actions{display:flex;grid-template-columns:none;width:auto}.calendar-main-title .icon-btn{width:38px;height:38px}.calendar-day-timeline{padding-left:46px}.calendar-timeline-line{grid-template-columns:40px minmax(0,1fr)}.calendar-timeline-events{left:48px}.calendar-timeline-event{height:50px}.calendar-timeline-event .calendar-event{height:50px;min-height:50px;padding:6px 9px}.calendar-event h3{font-size:17px}.calendar-event p{font-size:10px}.calendar-week{grid-template-columns:repeat(7,minmax(132px,1fr));min-width:924px}.calendar-day-column{min-height:330px}.calendar-mixed-week .calendar-day-timeline{min-height:min(var(--timeline-height),720px);overflow:hidden}.calendar-detail-status{grid-template-columns:1fr}.calendar-detail-status label{grid-column:auto}.calendar-type-option b{font-size:18px}}

/* Operational calendar layout: compact timeline inspired by the approved mobile reference. */
.calendar-wrap{width:min(100%,1180px);max-width:1180px;overflow:visible}.calendar-main-title{margin-bottom:4px}.calendar-main-title h1{font-size:22px}.calendar-main-title .sub{margin:2px 0 0;font-size:12px}.calendar-toolbar{display:block;margin:0 0 12px}.calendar-view-switch{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));width:min(100%,420px);margin:0 auto;padding:0;border:0;border-bottom:1px solid #2c394b;border-radius:0;background:transparent;overflow:visible}.calendar-view-switch a{position:relative;display:grid;place-items:center;min-width:0;min-height:38px;padding:8px 6px;border-radius:0;color:#8592a6;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;white-space:nowrap}.calendar-view-switch a.active{background:transparent;color:#f8fafc;box-shadow:none}.calendar-view-switch a.active:after{content:"";position:absolute;right:20%;bottom:-1px;left:20%;height:2px;border-radius:99px;background:#ef405f}.calendar-date-nav{grid-template-columns:36px minmax(0,1fr) 36px;margin-bottom:8px}.calendar-date-nav .calendar-date-title{min-height:38px;font-size:14px}.calendar-date-nav .btn{min-height:38px;padding:0;font-size:19px}.calendar-day-timeline{box-sizing:border-box;width:100%;min-height:var(--timeline-height);padding-left:50px;margin-top:4px;overflow:visible}.calendar-timeline-grid{right:0}.calendar-timeline-line{grid-template-columns:44px minmax(0,1fr)}.calendar-timeline-line time{padding-right:7px;color:#718096;font-size:10px}.calendar-timeline-line span{border-color:#293648}.calendar-timeline-events{left:50px;right:0}.calendar-timeline-event{left:calc((100% / var(--event-lanes)) * var(--event-lane) + 3px);right:auto;width:calc((100% / var(--event-lanes)) - 6px);height:var(--event-height);min-height:44px}.calendar-timeline-event .calendar-event{width:100%;height:100%;min-height:44px}.calendar-event{display:block;min-width:0;min-height:54px;padding:8px 10px;border:1px solid color-mix(in srgb,currentColor 52%,#334155);border-left:3px solid currentColor;border-radius:9px;background:color-mix(in srgb,currentColor 8%,#15202f);overflow:hidden}.calendar-event-time{position:static;display:block;margin:0 0 3px;padding:0;border:0;color:currentColor;font-size:10px;font-weight:700;line-height:1}.calendar-event-main{display:grid;grid-template-columns:24px minmax(0,1fr);gap:7px;align-items:start;padding:0;color:inherit}.calendar-event-icon{display:grid;place-items:center;width:24px;height:24px;border-radius:7px;background:color-mix(in srgb,currentColor 16%,transparent)}.calendar-event-icon .icon{width:14px;height:14px}.calendar-event-copy{min-width:0}.calendar-event h3{margin:0 0 2px;font-size:12px;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.calendar-event p{margin:1px 0;color:#a9b5c5;font-size:9.5px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.calendar-week-scroll{max-width:100%;margin:0;overflow-x:auto;overscroll-behavior-inline:contain;border:1px solid #293648;border-radius:12px;background:#101925;scrollbar-width:thin}.calendar-week{display:grid;grid-template-columns:46px repeat(7,minmax(112px,1fr));gap:0;min-width:830px;background:#101925}.calendar-week-time-column,.calendar-day-column{min-height:0;padding:0;border:0;border-right:1px solid #293648;border-radius:0;background:transparent}.calendar-week-time-column>header,.calendar-day-column>header{position:sticky;top:0;z-index:4;display:grid;place-items:center;height:42px;padding:5px;border-bottom:1px solid #293648;background:#101925;color:#7f8b9d;font-size:9px;text-align:center}.calendar-day-column>header b{display:block;color:#cbd5e1;font-size:10px;text-transform:uppercase}.calendar-day-column.is-selected>header b{color:#fb7185}.calendar-week-axis,.calendar-week-day-body{position:relative;height:var(--timeline-height)}.calendar-week-axis time{position:absolute;right:7px;color:#718096;font-size:9px;transform:translateY(-50%)}.calendar-week-day-body{overflow:hidden}.calendar-week-grid-line{position:absolute;right:0;left:0;border-top:1px solid #263243}.calendar-week-events{position:absolute;inset:0}.calendar-week-event{position:absolute;left:3px;right:3px;height:var(--event-height);min-height:38px}.calendar-week-event .calendar-event{width:100%;height:100%;min-height:38px;padding:5px 6px;border-radius:7px}.calendar-week-event .calendar-event-time{font-size:8px}.calendar-week-event .calendar-event-main{grid-template-columns:18px minmax(0,1fr);gap:4px}.calendar-week-event .calendar-event-icon{width:18px;height:18px;border-radius:5px}.calendar-week-event .calendar-event-icon .icon{width:11px;height:11px}.calendar-week-event .calendar-event h3{font-size:9px}.calendar-week-event .calendar-event p{font-size:7.5px}.calendar-month-composition{gap:10px}.calendar-month{width:100%;grid-template-columns:repeat(7,minmax(0,1fr));border-color:#293648;border-radius:11px;overflow:hidden}.calendar-month-day{min-width:0;min-height:70px;padding:5px 3px;border-color:#293648}.calendar-month-day>a{font-size:11px}.calendar-month-day .calendar-dots{gap:3px}.calendar-dot{width:6px;height:6px}.calendar-month-agenda{padding:10px}.calendar-month-agenda .calendar-day-list{min-height:0}.calendar-month-agenda .calendar-event{margin-bottom:6px}.light-theme .calendar-view-switch{border-color:#cbd5e1}.light-theme .calendar-week-scroll,.light-theme .calendar-week,.light-theme .calendar-week-time-column>header,.light-theme .calendar-day-column>header{background:#fff;border-color:#d7dee8}.light-theme .calendar-event{background:color-mix(in srgb,currentColor 6%,#fff)}
@media(max-width:900px){.calendar-wrap{width:100%;padding:calc(82px + var(--safe-top)) 10px calc(96px + var(--safe-bottom));overflow:visible}.calendar-main-title{display:flex;margin:0 2px 2px}.calendar-main-title h1{font-size:18px}.calendar-main-title .sub{display:none}.calendar-main-title .icon-btn{width:34px;height:34px}.calendar-toolbar{margin-bottom:7px}.calendar-view-switch{position:sticky;top:calc(66px + var(--safe-top));z-index:13;width:100%;background:#172131f2;backdrop-filter:blur(12px)}.calendar-view-switch a{min-height:36px;font-size:10px}.calendar-date-nav{position:static;padding:0;background:transparent}.calendar-day-timeline{padding-left:42px}.calendar-timeline-line{grid-template-columns:37px minmax(0,1fr)}.calendar-timeline-events{left:42px}.calendar-timeline-event{left:3px;width:calc(100% - 6px)}.calendar-timeline-event .calendar-event{padding:6px 8px}.calendar-event-main{grid-template-columns:22px minmax(0,1fr);gap:6px}.calendar-event-icon{width:22px;height:22px}.calendar-event h3{font-size:11px}.calendar-event p{font-size:8.5px}.calendar-week-scroll{margin:0}.calendar-week{min-width:795px}.calendar-month{margin:0}.calendar-month-day{min-height:52px}.calendar-month-agenda{border-radius:11px}.calendar-main-title+.calendar-date-nav{margin-top:0}}
@media(max-width:390px){.calendar-wrap{padding-right:8px;padding-left:8px}.calendar-view-switch a{padding-right:3px;padding-left:3px}.calendar-date-nav{grid-template-columns:32px minmax(0,1fr) 32px}.calendar-day-timeline{padding-left:38px}.calendar-timeline-line{grid-template-columns:34px minmax(0,1fr)}.calendar-timeline-events{left:38px}.calendar-event-icon{display:none}.calendar-event-main{grid-template-columns:minmax(0,1fr)}}

.calendar-week-event{left:calc((100% / var(--event-lanes)) * var(--event-lane) + 2px);right:auto;width:calc((100% / var(--event-lanes)) - 4px)}
.calendar-week-scroll{max-height:calc(100dvh - 210px);overflow:auto;-webkit-overflow-scrolling:touch}.calendar-week-time-column>header,.calendar-day-column>header{top:0;z-index:8;box-shadow:0 1px 0 #293648}
@media(max-width:900px){.calendar-week-scroll{max-height:max(360px,calc(100dvh - 250px - var(--safe-top) - var(--safe-bottom)))}}
@media(max-width:900px){.calendar-timeline-event{left:calc((100% / var(--event-lanes)) * var(--event-lane) + 2px);right:auto;width:calc((100% / var(--event-lanes)) - 4px)}}
.calendar-form [data-calendar-types][hidden],.calendar-form .calendar-zone-results[hidden]{display:none!important}.calendar-type-option{position:relative;align-items:center;gap:12px}.calendar-type-option>input[type=radio]{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}.calendar-event-type-icon{display:grid;place-items:center;flex:0 0 38px;width:38px;height:38px;border-radius:11px;background:#273447;color:#cbd5e1}.calendar-event-type-icon .icon{width:20px;height:20px}.calendar-type-option:has(input:checked) .calendar-event-type-icon{background:#ef405f;color:#fff}.calendar-form .fields,.calendar-form .field,.calendar-form .calendar-title-zone-row{min-width:0}.calendar-form input,.calendar-form select,.calendar-form textarea{min-width:0;max-width:100%}.calendar-form [data-pickup-location][hidden]{display:none!important}.calendar-subblock{margin-top:20px;padding-top:16px;border-top:1px solid #3b4a5f}.calendar-subblock:first-child{margin-top:0;padding-top:0;border-top:0}.calendar-subblock h3{margin:0 0 10px;font-size:13px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}.calendar-use-client-address{margin-top:8px}.calendar-client-missing{display:inline-flex;vertical-align:middle;margin-left:6px;color:#facc15}.calendar-client-missing .icon{width:14px;height:14px}
@media(max-width:900px){.calendar-form{padding:calc(88px + var(--safe-top)) 12px calc(94px + var(--safe-bottom))!important}.calendar-form .fields,.calendar-form .calendar-title-zone-row{grid-template-columns:minmax(0,1fr)}.calendar-form .calendar-title-zone-row{gap:13px}.calendar-form-step{width:100%;min-width:0}.calendar-form-step>.fields>.field,.calendar-form .calendar-title-zone-row>.field{grid-column:1;width:100%}.calendar-form input[type=date]{display:block;width:100%;min-width:0}.calendar-form .calendar-time-control{grid-template-columns:minmax(0,1fr) 44px}.calendar-form .calendar-type-option{min-height:68px}.calendar-form .calendar-event-type-icon{flex-basis:36px;width:36px;height:36px}}
.calendar-datetime-stack{display:grid;grid-column:1/-1;gap:8px;margin-top:2px}.calendar-datetime-row{position:relative;display:grid;grid-template-columns:72px minmax(170px,1fr) 104px;gap:8px;align-items:center;min-width:0;padding:9px 0;border-bottom:1px solid #334155}.calendar-datetime-row>label{margin:0;font-size:14px}.calendar-date-compact,.calendar-time-entry{min-width:0!important;min-height:40px!important;height:40px;padding:7px 10px!important;border-radius:10px!important;font-size:14px!important;text-align:center}.calendar-time-slot{min-width:0}.calendar-time-entry{font-variant-numeric:tabular-nums;font-weight:700;font-size:16px!important}.calendar-created-celebration{position:fixed;inset:0;z-index:120;display:grid;place-items:center;pointer-events:none;animation:calendarCelebrateFade 1.8s ease forwards}.calendar-created-celebration-card{display:grid;place-items:center;gap:8px;padding:22px 28px;border:1px solid #34d399;border-radius:22px;background:#101a28ee;box-shadow:0 22px 70px #02061799;animation:calendarCelebratePop .55s cubic-bezier(.2,.9,.25,1.25)}.calendar-created-celebration-icon{display:grid;place-items:center;width:64px;height:64px;border-radius:50%;background:#059669;color:#fff;font-size:34px;font-weight:900;animation:calendarCheckPulse .5s ease .5s}.calendar-created-check{width:38px;height:38px}.calendar-created-check circle{stroke:#fff;stroke-width:3;stroke-dasharray:151;stroke-dashoffset:151;animation:calendarCheckCircle .5s ease forwards}.calendar-created-check path{stroke:#fff;stroke-width:4;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:36;stroke-dashoffset:36;animation:calendarCheckMark .35s ease .45s forwards}.calendar-created-confetti{position:absolute;inset:0;overflow:hidden}.calendar-created-confetti i{position:absolute;top:-12px;width:8px;height:18px;border-radius:3px;background:var(--confetti);animation:calendarConfettiFall 1.7s ease-in var(--delay,0s) forwards;transform:rotate(var(--rotate))}@keyframes calendarCelebratePop{from{opacity:0;transform:scale(.72)}to{opacity:1;transform:scale(1)}}@keyframes calendarCelebrateFade{0%,70%{opacity:1}100%{opacity:0;visibility:hidden}}@keyframes calendarConfettiFall{to{transform:translate(var(--drift),105vh) rotate(620deg)}}@keyframes calendarCheckCircle{to{stroke-dashoffset:0}}@keyframes calendarCheckMark{to{stroke-dashoffset:0}}@keyframes calendarCheckPulse{0%{transform:scale(1)}50%{transform:scale(1.12)}100%{transform:scale(1)}}.calendar-time-wheel{position:relative;display:grid;grid-template-columns:minmax(76px,1fr) 20px minmax(76px,1fr);grid-column:2/-1;align-items:center;width:min(100%,360px);height:168px;margin:5px auto 3px;padding:0 18px;border:1px solid #3b4a5f;border-radius:15px;background:#111a27;box-shadow:0 18px 45px #02061770;overflow:hidden}.calendar-time-wheel[hidden]{display:none!important}.calendar-time-wheel:after{content:"";position:absolute;right:10px;left:10px;top:50%;height:40px;border-radius:9px;background:#263244;transform:translateY(-50%);pointer-events:none}.calendar-wheel-column{position:relative;z-index:1;height:168px;padding:64px 0;overflow-y:auto;scroll-snap-type:y mandatory;scrollbar-width:none;overscroll-behavior:contain}.calendar-wheel-column::-webkit-scrollbar{display:none}.calendar-wheel-option{display:grid;place-items:center;width:100%;height:40px;padding:0;border:0;background:transparent;color:#6f7c8f;font:600 20px/1 system-ui;scroll-snap-align:center;cursor:pointer;transition:color .14s,transform .14s}.calendar-wheel-option.active{color:#34d399;transform:scale(1.08)}.calendar-wheel-separator{position:relative;z-index:2;color:#34d399;font-size:22px;font-weight:700;text-align:center}.light-theme .calendar-time-wheel{background:#fff;border-color:#cbd5e1}.light-theme .calendar-time-wheel:after{background:#e2e8f0}.light-theme .calendar-wheel-option{color:#94a3b8}.light-theme .calendar-wheel-option.active{color:#059669}
@media(max-width:900px){.calendar-datetime-row{grid-template-columns:52px minmax(0,1fr) 88px;gap:7px}.calendar-datetime-row>label{font-size:13px}.calendar-date-compact{height:38px;min-height:38px!important;padding:6px 7px!important;font-size:13px!important}.calendar-time-entry{height:38px;min-height:38px!important;padding:6px 7px!important;font-size:16px!important}.calendar-time-wheel{grid-column:1/-1;width:min(100%,310px);height:158px}.calendar-wheel-column{height:158px;padding:59px 0}.calendar-wheel-option{height:40px;font-size:19px}}
@media(max-width:800px){.budget-row{grid-template-columns:1fr;padding:10px}.budget-cell-right{padding-top:2px}.catalog-summary-form button{width:100%}}
"""

APP_JS = r"""
<script>
function setProvenanceFromVeterinarian(option){
  const field=document.querySelector('select[name="provenance"]');
  if(field && option?.value && option.dataset.provenance)field.value=option.dataset.provenance;
}
document.addEventListener('change', function(e){
  if(e.target && e.target.name === 'catalog_sent' && e.target.checked){
    const sendCatalog=e.target.form?.querySelector('[name="send_catalog"]')||document.querySelector('[name="send_catalog"]');
    if(sendCatalog)sendCatalog.checked=false;
  }
  if(e.target && e.target.name === 'send_catalog' && e.target.checked){
    const catalogSent=e.target.form?.querySelector('[name="catalog_sent"]')||document.querySelector('[name="catalog_sent"]');
    if(catalogSent)catalogSent.checked=false;
  }
  if(e.target && e.target.name === 'request_origin'){
    const method = document.querySelector('[name="transport_method"]');
    const transporter = document.querySelector('select[name="transporter_mode"]');
    if(e.target.value === 'Consegna in sede'){
      if(method) method.value = 'Mezzo proprio';
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
  if(e.target && e.target.name === 'tag_da_richiamare'){
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
      setProvenanceFromVeterinarian(opt);
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
      setProvenanceFromVeterinarian(opt);
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
  if(e.target && e.target.name === 'origin_veterinarian_id'){
    const opt=e.target.selectedOptions && e.target.selectedOptions[0];
    const mode=document.querySelector('select[name="origin_mode"]'),text=document.querySelector('input[name="origin_text"]');
    if(opt && opt.value){setProvenanceFromVeterinarian(opt);if(mode) mode.value='Veterinario';if(text) text.value=opt.dataset.shortname||opt.dataset.fullname||opt.textContent.trim();}
  }
  if(e.target && e.target.name === 'use_voucher'){
    const pay=document.querySelector('select[name="payment_status"]');
    if(e.target.checked && pay) pay.value='Pagato';
    refreshUseVoucherBox();
  }
  if(e.target && e.target.id === 'transport_method_quick'){
    const field = document.querySelector('[name="transport_method"]');
    const plate = document.querySelector('input[name="vehicle_plate"]');
    const vehicles={'Fiat Fiorino':'GP793KP','Renault Captur':'GV932LL','Dr PK8':'GV041FX'};
    if(field && e.target.value){ field.value = e.target.value; field.dispatchEvent(new Event('input', {bubbles:true})); }
    if(plate && vehicles[e.target.value]) plate.value=vehicles[e.target.value];
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
function ppmFormatInvoiceTotal(value){
  const number = typeof value === 'number' ? value : ppmNumber(value);
  return number.toFixed(2).replace('.', ',');
}
function updatePreventivoTotal(){
  const fields = document.querySelectorAll('[data-preventivo-sum="1"]');
  if(!fields.length) return;
  let total = 0;
  fields.forEach(function(field){ total += ppmNumber(field.value); });
  const target = document.querySelector('input[name="total_service"]');
  if(target){ target.value = ppmFormat(total); target.readOnly = true; }
  updateRemainingBalance();
}
function updateRemainingBalance(){
  const totalField = document.querySelector('input[name="total_service"]');
  const definitiveField = document.querySelector('[name="total_text"]');
  const depositField = document.querySelector('input[name="deposit"]');
  const remainingField = document.querySelector('input[name="remaining_balance"]');
  const definitive = definitiveField ? ppmNumber(definitiveField.value) : 0;
  const serviceTotal = ppmNumber(totalField ? totalField.value : 0);
  if(remainingField){
    const remaining = Math.max(0, serviceTotal - ppmNumber(depositField ? depositField.value : 0));
    remainingField.value = ppmFormat(remaining);
  }
  const invoiceTotal=document.querySelector('input[name="invoice_total"]');
  if(invoiceTotal?.dataset.autoFilled==='1'){
    const seedTotal = definitive > 0 ? definitive : serviceTotal;
    invoiceTotal.value=ppmFormatInvoiceTotal(seedTotal);
  }
  const depositFinalField = document.querySelector('input[name="deposit_final"]');
  const remainingFinalField = document.querySelector('input[name="remaining_final"]');
  if(remainingFinalField){
    const remainingFinal = Math.max(0, definitive - ppmNumber(depositFinalField ? depositFinalField.value : 0));
    remainingFinalField.value = ppmFormat(remainingFinal);
  }
}
function setupNumericBudgetFields(){
  const names=['price_cremation','price_pickup','price_urn','price_urn_2','price_delivery','price_cast','price_cast_2','price_paw_cast','price_paw_cast_2','price_paw_cast_3','price_paw_cast_4','price_nose_cast','price_nose_cast_2','price_nose_cast_3','price_nose_cast_4','price_evening','price_night','price_holiday','price_accessories','price_accessories_2','total_service','total_text','deposit','deposit_final','remaining_balance','remaining_final'];
  names.forEach(function(name){
    const field=document.querySelector(`[name="${name}"]`);
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
    const original=e.target.value;
    if(original){
      const capitalized=original.charAt(0).toLocaleUpperCase('it')+original.slice(1);
      if(capitalized!==original){const cursor=e.target.selectionStart;e.target.value=capitalized;if(cursor!==null)e.target.setSelectionRange(cursor,cursor);}
    }
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
  if(e.target && (e.target.name === 'deposit' || e.target.name === 'total_service' || e.target.name === 'total_text' || e.target.name === 'deposit_final')) updateRemainingBalance();
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
function normalizeUrnSearch(value){
  return String(value||'').toLocaleLowerCase('it').normalize('NFD').replace(/[\u0300-\u036f]/g,'').trim();
}
function urnMatchesWords(option,query){
  const words=normalizeUrnSearch(query).split(/\s+/).filter(Boolean);
  const name=normalizeUrnSearch(option?.dataset?.name||option?.textContent||'');
  return words.every(word=>name.includes(word));
}
function markCastForFrameUrn(option){
  const name=normalizeUrnSearch(option?.dataset?.name||option?.textContent||'');
  if(!name.includes('doppia cornice'))return;
  const tag=document.querySelector('input[name="tag_calco_urna"]');
  if(tag){tag.checked=true;tag.value='Si';}
}
function setupUrnNotesField(){
  const hidden=document.querySelector('input[name="urn_notes"]');
  const price=document.querySelector('input[name="price_urn"]');
  if(!hidden || !price) return;
  const catalog=document.querySelector('select[name="urn_id"]');
  if(catalog){
    const warning=document.getElementById('urnStockWarning');
    const priceField=price.closest('.field');
    priceField.querySelector('label').textContent='Prezzo urna €';
    const field=document.createElement('div'); field.className='field full lookup';
    const label=document.createElement('label'); label.textContent='Urna';
    const search=document.createElement('input'); search.type='text'; search.autocomplete='off'; search.placeholder='Scrivi per cercare oppure inserisci testo libero';
    const results=document.createElement('div'); results.className='lookup-results hidden';
    field.append(label,search,results,warning); priceField.parentNode.insertBefore(field,priceField);
    const selectedOption=catalog.options[catalog.selectedIndex];
    search.value=selectedOption&&selectedOption.value?(selectedOption.dataset.name||''):hidden.value;
    if(selectedOption?.value)markCastForFrameUrn(selectedOption);
    const apply=(option)=>{
      if(!option || !option.value) return;
      catalog.value=option.value; search.value=option.dataset.name||option.textContent.trim();
      price.value=option.dataset.price||'';
      hidden.value=option.dataset.name||option.textContent.trim();
      price.readOnly=false;
      markCastForFrameUrn(option);
      if(warning){
        const quantity=Number(option.dataset.quantity||0);
        warning.textContent=quantity<=0?'Magazzino esaurito - disponibilità 0':`Disponibilità attuale: ${quantity}`;
        warning.classList.toggle('warning',quantity<=0);
        warning.classList.remove('hidden');
      }
      results.classList.add('hidden'); results.innerHTML='';
      updatePreventivoTotal();
    };
    const showMatches=()=>{
      const query=search.value.trim(); hidden.value=query; catalog.value=''; price.readOnly=false;
      if(warning) warning.classList.add('hidden');
      const matches=[...catalog.options].filter(option=>option.value&&urnMatchesWords(option,query)).slice(0,12);
      results.innerHTML='';
      matches.forEach(option=>{const button=document.createElement('button');button.type='button';button.className='lookup-item lookup-item-urn';const thumb=option.dataset.image?`<img class="lookup-item-thumb" src="${option.dataset.image}" alt="">`:'';button.innerHTML=`${thumb}<span><b>${option.dataset.name||option.textContent}</b><small>${option.textContent.replace(option.dataset.name||'','').replace(/^\s*·\s*/,'')}</small></span>`;button.onclick=()=>apply(option);results.append(button)});
      results.classList.toggle('hidden',matches.length===0);
    };
    search.addEventListener('input',showMatches); search.addEventListener('focus',showMatches);
    document.addEventListener('click',event=>{if(!field.contains(event.target))results.classList.add('hidden')});
    return;
  }
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
function setupSecondUrnCatalog(){
  const catalog=document.querySelector('select[name="urn_id_2"]');
  const notes=document.querySelector('input[name="urn_notes_2"]');
  const price=document.querySelector('input[name="price_urn_2"]');
  const priceField=price?.closest('.field');
  if(!catalog || !notes || !price || !priceField) return null;
  const field=document.createElement('div');field.className='field full lookup hidden';
  const label=document.createElement('label');label.textContent='Seconda urna';
  const search=document.createElement('input');search.type='text';search.autocomplete='off';search.placeholder='Scrivi per cercare oppure inserisci testo libero';
  const results=document.createElement('div');results.className='lookup-results hidden';
  const warning=document.getElementById('urnStockWarning2');
  field.append(label,search,results);if(warning)field.append(warning);
  priceField.parentNode.insertBefore(field,priceField);
  const selected=catalog.options[catalog.selectedIndex];
  search.value=selected&&selected.value?(selected.dataset.name||''):notes.value;
  if(selected?.value)markCastForFrameUrn(selected);
  const apply=(option)=>{
    if(!option || !option.value)return;
    catalog.value=option.value;search.value=option.dataset.name||option.textContent.trim();notes.value=search.value;
    price.value=option.dataset.price||'';price.readOnly=false;
    markCastForFrameUrn(option);
    if(warning){const quantity=Number(option.dataset.quantity||0);warning.textContent=quantity<=0?'Magazzino esaurito - disponibilita 0':`Disponibilita attuale: ${quantity}`;warning.classList.toggle('warning',quantity<=0);warning.classList.remove('hidden');}
    results.classList.add('hidden');results.innerHTML='';updatePreventivoTotal();
  };
  const showMatches=()=>{
    const query=search.value.trim();notes.value=query;catalog.value='';price.readOnly=false;
    if(warning)warning.classList.add('hidden');
    const matches=[...catalog.options].filter(option=>option.value&&urnMatchesWords(option,query)).slice(0,12);
    results.innerHTML='';matches.forEach(option=>{const button=document.createElement('button');button.type='button';button.className='lookup-item lookup-item-urn';const thumb=option.dataset.image?`<img class="lookup-item-thumb" src="${option.dataset.image}" alt="">`:'';button.innerHTML=`${thumb}<span><b>${option.dataset.name||option.textContent}</b><small>${option.textContent.replace(option.dataset.name||'','')}</small></span>`;button.onclick=()=>apply(option);results.append(button)});
    results.classList.toggle('hidden',matches.length===0);
  };
  search.addEventListener('input',showMatches);search.addEventListener('focus',showMatches);document.addEventListener('click',event=>{if(!field.contains(event.target))results.classList.add('hidden')});
  return field;
}
function reorderSenderFields(){
  const section=[...document.querySelectorAll('.section')].find(item=>item.querySelector('h2')?.textContent.trim()==='SPEDITORE');
  const fields=section?.querySelector('.fields');
  if(!fields) return;
  const selectors=['[name="owner_veterinarian_id"]','#clientSearch','[name="owner_first_name"]','[name="owner_last_name"]','[name="owner_phone"]','[name="owner_phone_2"]','[name="owner_phone_note"]','[name="owner_street"]','[name="owner_city"]','[name="owner_province"]','[name="owner_zip"]','[name="owner_tax_code"]','[name="owner_email"]','[name="owner_company"]','[name="owner_vat"]','[name="owner_notes"]'];
  const ordered=selectors.map(selector=>fields.querySelector(selector)?.closest('.field')).filter(Boolean);
  const remaining=[...fields.children].filter(item=>item.classList.contains('field')&&!ordered.includes(item));
  [...ordered,...remaining].forEach(field=>fields.appendChild(field));
  const secondPhone=fields.querySelector('[name="owner_phone_2"]');
  if(secondPhone){secondPhone.type='text';secondPhone.removeAttribute('inputmode');secondPhone.placeholder='Numero oppure testo libero';}
}
function placeCallBackFlag(){
  const input=document.querySelector('input[name="tag_da_richiamare"]');
  const field=input?.closest('.field');
  const section=[...document.querySelectorAll('.section')].find(item=>item.querySelector(':scope > h2')?.textContent.trim()==='SPEDITORE');
  const heading=section?.querySelector(':scope > h2');
  if(!field || !section || !heading) return;
  const row=document.createElement('div');row.className='section-heading-row';
  const flag=document.createElement('div');flag.className='section-header-flag';
  heading.replaceWith(row);row.append(heading,flag);flag.append(field);
}
function decoratePracticeSections(){
  const tones=['section-tone-blue','section-tone-teal','section-tone-violet','section-tone-amber','section-tone-slate'];
  document.querySelectorAll('.section:not(.danger)').forEach((section,index)=>section.classList.add(tones[index%tones.length]));
}
function setupBudgetExtras(){
  const fields=document.querySelector('.section input[name="price_cremation"]')?.closest('.fields');
  if(!fields) return;
  const modernizeCheck=(input)=>{const label=input?.closest('label');if(label)label.classList.add('modern-check');return input?.closest('.field');};
  const insertControl=(control,label,after)=>{
    control.classList.remove('hidden');
    const wrap=document.createElement('div');wrap.className='field';
    const lab=document.createElement('label');lab.textContent=label;wrap.append(lab,control);
    fields.insertBefore(wrap,after?.nextSibling||fields.firstElementChild);return wrap;
  };
  const insertCheck=(control,label,after)=>{
    const wasChecked=control.value==='Si';control.type='checkbox';control.value='Si';control.checked=wasChecked;control.classList.remove('hidden');
    const wrap=document.createElement('div');wrap.className='field';
    const lab=document.createElement('label');lab.className='modern-check';lab.append(control,document.createTextNode(label));wrap.append(lab);
    fields.insertBefore(wrap,after ? after.nextSibling : fields.firstElementChild);return wrap;
  };
  const voucherField=modernizeCheck(document.querySelector('input[name="use_voucher"]'));
  if(voucherField)fields.insertBefore(voucherField,fields.firstElementChild);
  insertControl(document.querySelector('select[name="payment_method"]'),'Metodo di pagamento',voucherField);
  const remainingFinalField=document.querySelector('input[name="remaining_final"]')?.closest('.field');
  insertControl(document.querySelector('select[name="payment_status"]'),'Pagamento',remainingFinalField);
  const sendCatalogField=modernizeCheck(document.querySelector('input[name="send_catalog"]'));
  insertCheck(document.querySelector('input[name="catalog_sent"]'),'CATALOGO INVIATO',sendCatalogField);
  const sendEstremiField=modernizeCheck(document.querySelector('input[name="send_estremi"]'));
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
  const totalService=document.querySelector('input[name="total_service"]'); if(totalService){totalService.readOnly=true;totalService.closest('.field').querySelector('label').textContent='Totale W €';}
  const depositField_=document.querySelector('input[name="deposit"]'); if(depositField_){depositField_.closest('.field').querySelector('label').textContent='Acconto W €';}
  const remainingBalanceField_=document.querySelector('input[name="remaining_balance"]'); if(remainingBalanceField_){remainingBalanceField_.closest('.field').querySelector('label').textContent='Rimanenza W €';}
  const urn=document.querySelector('input[name="price_urn"]')?.closest('.field');
  const urn2=wrapField(document.querySelector('input[name="price_urn_2"]'),'Seconda urna €',urn,true); urn2.querySelector('input').dataset.preventivoSum='1';
  const urnNotes2=wrapField(document.querySelector('input[name="urn_notes_2"]'),'Seconda urna - testo libero',urn2,true);
  const urnCatalog2=setupSecondUrnCatalog();
  addButton('+ Aggiungi altra urna',urnNotes2,[urnCatalog2,urn2,urnNotes2].filter(Boolean));
  const cast=document.querySelector('input[name="price_cast"]')?.closest('.field');
  const cast2=wrapField(document.querySelector('input[name="price_cast_2"]'),'Secondo calco €',cast,true); cast2.querySelector('input').dataset.preventivoSum='1';
  const castButton=addButton('+ Aggiungi altro calco',cast2,[cast2]);
  const setupExpandableCast=(config)=>{
    const priceInput=document.querySelector(`input[name="${config.primaryPriceName}"]`);
    if(!priceInput) return;
    const priceWrap=priceInput.closest('.field');
    const buildSelect=(hidden,name,label,priceField)=>{
      const select=document.createElement('select');select.name=name;
      select.add(new Option('Seleziona tipo',''));
      config.options.forEach(([optLabel,price])=>{const opt=new Option(optLabel,optLabel);opt.dataset.price=price;select.add(opt)});
      select.value=hidden.value||'';
      hidden.replaceWith(select);
      select.addEventListener('change',()=>{
        const opt=select.selectedOptions[0];
        priceField.value=(opt&&opt.value)?ppmFormat(Number(opt.dataset.price)):'';
        priceField.dispatchEvent(new Event('input',{bubbles:true}));
      });
      const wrap=document.createElement('div');wrap.className='field';
      const lab=document.createElement('label');lab.textContent=label;wrap.append(lab,select);
      return wrap;
    };
    const hiddenType=document.querySelector(`input[name="${config.primaryTypeName}"]`);
    const primaryTypeWrap=buildSelect(hiddenType,config.primaryTypeName,config.primaryTypeLabel,priceInput);
    priceWrap.parentNode.insertBefore(primaryTypeWrap,priceWrap);
    let anchor=priceWrap;
    const entries=[];
    config.extraSuffixes.forEach((suffix,idx)=>{
      const typeName=config.typeFieldBase+suffix, priceName=config.priceFieldBase+suffix;
      const hiddenTypeExtra=document.querySelector(`input[name="${typeName}"]`);
      const hiddenPriceExtra=document.querySelector(`input[name="${priceName}"]`);
      if(!hiddenTypeExtra||!hiddenPriceExtra) return;
      const labels=idx===0?config.secondLabels:config.moreLabels;
      hiddenPriceExtra.type='text';hiddenPriceExtra.dataset.preventivoSum='1';
      const priceWrapExtra=document.createElement('div');priceWrapExtra.className='field hidden';
      const priceLab=document.createElement('label');priceLab.textContent=labels.price;priceWrapExtra.append(priceLab,hiddenPriceExtra);
      const typeWrapExtra=buildSelect(hiddenTypeExtra,typeName,labels.type,hiddenPriceExtra);
      typeWrapExtra.classList.add('hidden');
      anchor.parentNode.insertBefore(typeWrapExtra,anchor.nextSibling);
      anchor.parentNode.insertBefore(priceWrapExtra,typeWrapExtra.nextSibling);
      const btn=document.createElement('button');btn.type='button';btn.className='btn ghost budget-add hidden';
      btn.textContent=idx===0?config.addFirstLabel:config.addMoreLabel;
      priceWrapExtra.parentNode.insertBefore(btn,priceWrapExtra.nextSibling);
      entries.push({typeWrapExtra,priceWrapExtra,btn,hiddenTypeExtra,hiddenPriceExtra});
      anchor=btn;
    });
    entries.forEach((entry,idx)=>{
      const reveal=()=>{
        entry.typeWrapExtra.classList.remove('hidden');
        entry.priceWrapExtra.classList.remove('hidden');
        entry.btn.remove();
        const next=entries[idx+1];
        if(next) next.btn.classList.remove('hidden');
      };
      entry.btn.onclick=reveal;
      if(idx===0) entry.btn.classList.remove('hidden');
      if(entry.hiddenTypeExtra.value||entry.hiddenPriceExtra.value) reveal();
    });
  };
  const NOSE_CAST_OPTIONS=[['Bronzo S',220],['Bronzo M',260],['Bronzo G',300],['Argento S',300],['Argento M',380],['Argento G',500]];
  const PAW_CAST_OPTIONS=[['Argento',200]];
  setupExpandableCast({
    primaryPriceName:'price_nose_cast', primaryTypeName:'nose_cast_type', primaryTypeLabel:'Tipo calco naso',
    typeFieldBase:'nose_cast_type', priceFieldBase:'price_nose_cast', extraSuffixes:['_2','_3','_4'], options:NOSE_CAST_OPTIONS,
    addFirstLabel:'+ Aggiungi calco naso', addMoreLabel:'+ Aggiungi altro calco naso',
    secondLabels:{type:'Tipo secondo calco naso',price:'Secondo calco naso €'},
    moreLabels:{type:'Tipo altro calco naso',price:'Altro calco naso €'},
  });
  setupExpandableCast({
    primaryPriceName:'price_paw_cast', primaryTypeName:'paw_cast_type', primaryTypeLabel:'Tipo calco polpastrello',
    typeFieldBase:'paw_cast_type', priceFieldBase:'price_paw_cast', extraSuffixes:['_2','_3','_4'], options:PAW_CAST_OPTIONS,
    addFirstLabel:'+ Aggiungi calco polpastrello', addMoreLabel:'+ Aggiungi altro calco polpastrello',
    secondLabels:{type:'Tipo di calco polpastrello',price:'Secondo calco polpastrello €'},
    moreLabels:{type:'Tipo altro calco polpastrello',price:'Altro calco polpastrello €'},
  });
  const accessoryPrice=document.querySelector('input[name="price_accessories"]')?.closest('.field');
  const makeAccessorySelect=(hidden,name)=>{const select=document.createElement('select');select.name=name;const options=['','Braccialetto','Collana','Calco inchiostro'];if(hidden.value&&!options.includes(hidden.value))options.push(hidden.value);options.forEach(value=>{const option=new Option(value||'Seleziona accessorio',value);select.add(option)});select.value=hidden.value;hidden.replaceWith(select);return select;};
  const makeAccessoryDetail=(hidden,select,label)=>{
    hidden.type='text';hidden.placeholder='Dettaglio (facoltativo)';
    const wrap=document.createElement('div');wrap.className='field hidden';
    const lab=document.createElement('label');lab.textContent=label;wrap.append(lab,hidden);
    const sync=()=>wrap.classList.toggle('hidden',!['Collana','Braccialetto'].includes(select.value));
    select.addEventListener('change',sync);sync();
    return wrap;
  };
  const accessoryTypeHidden=document.querySelector('input[name="accessory_type"]'); const accessoryType=makeAccessorySelect(accessoryTypeHidden,'accessory_type');
  const accessoryTypeWrap=document.createElement('div');accessoryTypeWrap.className='field';accessoryTypeWrap.innerHTML='<label>Tipo accessorio</label>';accessoryTypeWrap.append(accessoryType);
  cast.parentNode.insertBefore(accessoryTypeWrap,cast.nextSibling); accessoryTypeWrap.parentNode.insertBefore(accessoryPrice,accessoryTypeWrap.nextSibling);
  const accessoryDetailWrap=makeAccessoryDetail(document.querySelector('input[name="accessory_detail"]'),accessoryType,'Note accessorio');
  accessoryPrice.parentNode.insertBefore(accessoryDetailWrap,accessoryPrice.nextSibling);
  if(castButton.isConnected) cast.parentNode.insertBefore(castButton,cast.nextSibling);
  const accessoryType2Hidden=document.querySelector('input[name="accessory_type_2"]'); const accessoryType2=makeAccessorySelect(accessoryType2Hidden,'accessory_type_2');
  const accessoryType2Wrap=document.createElement('div');accessoryType2Wrap.className='field hidden';accessoryType2Wrap.innerHTML='<label>Tipo secondo accessorio</label>';accessoryType2Wrap.append(accessoryType2);
  accessoryDetailWrap.parentNode.insertBefore(accessoryType2Wrap,accessoryDetailWrap.nextSibling);
  const accessory2=wrapField(document.querySelector('input[name="price_accessories_2"]'),'Altro accessorio €',accessoryType2Wrap,true); accessory2.querySelector('input').dataset.preventivoSum='1';
  const accessoryDetail2Wrap=makeAccessoryDetail(document.querySelector('input[name="accessory_detail_2"]'),accessoryType2,'Note altro accessorio');
  accessory2.parentNode.insertBefore(accessoryDetail2Wrap,accessory2.nextSibling);
  addButton('+ Aggiungi altri accessori',accessoryDetail2Wrap,[accessoryType2Wrap,accessory2,accessoryDetail2Wrap]);
  const invoiceNumber=document.querySelector('input[name="invoice_number"]');invoiceNumber.type='text';invoiceNumber.placeholder='Numero fattura';
  invoiceNumber.addEventListener('input',()=>{const makeInvoice=document.querySelector('input[name="make_invoice"]');if(makeInvoice&&invoiceNumber.value.trim())makeInvoice.checked=false;});
  const invoiceField=document.createElement('div');invoiceField.className='field';invoiceField.innerHTML='<label>Numero fattura</label>';invoiceField.append(invoiceNumber);fields.append(invoiceField);
  const invoiceDate=document.querySelector('input[name="invoice_date"]');invoiceDate.type='date';
  const invoiceDateField=document.createElement('div');invoiceDateField.className='field';invoiceDateField.innerHTML='<label>Data fattura</label>';invoiceDateField.append(invoiceDate);fields.append(invoiceDateField);
  const invoiceTotal=document.querySelector('input[name="invoice_total"]');invoiceTotal.type='text';invoiceTotal.inputMode='decimal';invoiceTotal.placeholder='Totale fattura';
  const invoiceTotalField=document.createElement('div');invoiceTotalField.className='field';invoiceTotalField.innerHTML='<label>Totale fattura €</label>';invoiceTotalField.append(invoiceTotal);fields.append(invoiceTotalField);
  if(!invoiceTotal.value.trim()){invoiceTotal.dataset.autoFilled='1';const seed=(document.querySelector('[name="total_text"]')?.value||totalService?.value||'').trim();invoiceTotal.value=seed?ppmFormatInvoiceTotal(seed):'';}
  else{invoiceTotal.value=ppmFormatInvoiceTotal(invoiceTotal.value);}
  invoiceTotal.addEventListener('input',()=>invoiceTotal.dataset.autoFilled='0');
  invoiceTotal.addEventListener('blur',()=>{if(invoiceTotal.value.trim())invoiceTotal.value=ppmFormatInvoiceTotal(invoiceTotal.value);});
  if(sendEstremiField)fields.append(sendEstremiField);
  const estremiSentField=insertCheck(document.querySelector('input[name="estremi_sent"]'),'ESTREMI INVIATI',sendEstremiField||fields.lastElementChild);
  insertCheck(document.querySelector('input[name="make_invoice"]'),'FARE FATTURA',estremiSentField||sendEstremiField||fields.lastElementChild);
}
function arrangeBudgetLayout(){
  const fields=document.querySelector('.section input[name="price_cremation"]')?.closest('.fields');
  if(!fields || fields.classList.contains('budget-layout'))return;
  const original=[...fields.children];
  const used=new Set();
  const field=(name)=>fields.querySelector(`[name="${name}"]`)?.closest('.field');
  const button=(text)=>original.find(node=>node.matches?.('button')&&node.textContent.includes(text));
  const buttons=(text)=>original.filter(node=>node.matches?.('button')&&node.textContent.includes(text));
  const priceUrn=field('price_urn'),priceUrn2=field('price_urn_2');
  const urnSearch=priceUrn?.previousElementSibling?.classList.contains('lookup')?priceUrn.previousElementSibling:null;
  const urnSearch2=priceUrn2?.previousElementSibling?.classList.contains('lookup')?priceUrn2.previousElementSibling:null;
  const workspace=document.createElement('div');workspace.className='budget-workspace';
  const addRow=(left,right=[])=>{
    const clean=(items)=>items.filter(node=>node&&original.includes(node)&&!used.has(node));
    const leftItems=clean(left),rightItems=clean(right);
    if(!leftItems.length&&!rightItems.length)return;
    const row=document.createElement('div');row.className='budget-row';
    const leftCell=document.createElement('div');leftCell.className='budget-cell budget-cell-left';
    const rightCell=document.createElement('div');rightCell.className='budget-cell budget-cell-right';
    leftItems.forEach(node=>{used.add(node);leftCell.append(node)});
    rightItems.forEach(node=>{used.add(node);rightCell.append(node)});
    row.append(leftCell,rightCell);workspace.append(row);
  };
  addRow([field('use_voucher')]);
  addRow([field('price_cremation')]);
  addRow([field('price_pickup')],[field('payment_method')]);
  addRow([urnSearch,priceUrn,urnSearch2,priceUrn2,field('urn_notes_2')],[field('send_catalog'),field('catalog_sent'),button('altra urna')]);
  addRow([field('price_cast'),field('price_cast_2')],[button('altro calco')]);
  addRow([field('nose_cast_type'),field('price_nose_cast'),field('nose_cast_type_2'),field('price_nose_cast_2'),field('nose_cast_type_3'),field('price_nose_cast_3'),field('nose_cast_type_4'),field('price_nose_cast_4')],buttons('calco naso'));
  addRow([field('paw_cast_type'),field('price_paw_cast'),field('paw_cast_type_2'),field('price_paw_cast_2'),field('paw_cast_type_3'),field('price_paw_cast_3'),field('paw_cast_type_4'),field('price_paw_cast_4')],buttons('calco polpastrello'));
  addRow([field('price_delivery')]);
  addRow([field('price_holiday')]);
  addRow([field('price_evening')]);
  addRow([field('price_night')]);
  addRow([field('price_accessories'),field('price_accessories_2')],[field('accessory_type'),field('accessory_detail'),field('accessory_type_2'),field('accessory_detail_2'),button('altri accessori')]);
  addRow([field('total_service'),field('deposit'),field('remaining_balance')],[field('send_estremi'),field('estremi_sent')]);
  addRow([field('invoice_number'),field('invoice_date'),field('invoice_total')],[field('make_invoice')]);
  addRow([field('total_text'),field('deposit_final'),field('remaining_final')]);
  addRow([field('payment_status')]);
  addRow([field('notes')]);
  original.filter(node=>!used.has(node)).forEach(node=>addRow([node]));
  fields.replaceChildren(workspace);fields.classList.add('budget-layout');
}
document.addEventListener('DOMContentLoaded', function(){
  reorderSenderFields(); placeCallBackFlag(); setupBudgetExtras(); decoratePracticeSections(); setupNumericBudgetFields(); updatePreventivoTotal(); updateRemainingBalance(); setupZipLookup(); setupUrnNotesField();arrangeBudgetLayout();
  const plate=document.querySelector('input[name="vehicle_plate"]');
  if(plate) plate.readOnly=false;
});
document.addEventListener('DOMContentLoaded', function(){
  const file=document.getElementById('urnImageFile'), target=document.querySelector('input[name="image_data"]');
  if(file && target) file.addEventListener('change',()=>{
    const selected=file.files&&file.files[0]; if(!selected){target.value='';return;}
    if(selected.size>3*1024*1024){alert('Immagine troppo grande. Massimo 3 MB.');file.value='';return;}
    const reader=new FileReader(); reader.onload=()=>{target.value=reader.result||''}; reader.readAsDataURL(selected);
  });
});
function toggleCollaboratorBox(){
  const origin = document.querySelector('select[name="request_origin"]');
  const box = document.getElementById('collaboratorBox');
  if(box && origin){ box.classList.toggle('hidden', origin.value !== 'Collaboratore'); }
}
document.addEventListener('DOMContentLoaded', toggleCollaboratorBox);
function toggleCollectiveVetMode(){
  const service = document.querySelector('select[name="service_type"]');
  const vet = document.querySelector('select[name="veterinarian_id"]');
  const callBack = document.querySelector('input[name="tag_da_richiamare"]');
  const exempt = !!(callBack?.checked || (service && service.value === 'Cremazione collettiva'));
  ['operator_name','request_origin','owner_first_name','owner_last_name','owner_phone','owner_tax_code','owner_street','owner_city','owner_province','owner_zip'].forEach(function(name){
    const field=document.querySelector(`[name="${name}"]`);
    if(field){ field.required = !exempt; }
  });
  const collective=!!(service && service.value==='Cremazione collettiva');
  const smaltito=document.querySelector('select[name="status"] option[data-collective-only="1"]');
  if(smaltito){smaltito.hidden=!collective;smaltito.disabled=!collective;if(!collective && smaltito.selected) smaltito.parentElement.value='Ritirato';}
}

document.addEventListener('DOMContentLoaded', function(){
  setupClientLookup();
  setupVetLookup();
  setupOriginVetLookup();
  setupOwnerVetLookup();
  toggleCollectiveVetMode();
  refreshUseVoucherBox();
});
function ppmDebounce(fn, delay){
  let timer=null;
  return function(){ const args=arguments; clearTimeout(timer); timer=setTimeout(()=>fn.apply(this,args), delay); };
}
const ppmLookupPanels=new Set();
function ppmCloseLookupPanel(panel){
  if(!panel)return;
  panel.classList.add('hidden');
  if('hidden' in panel)panel.hidden=true;
  panel.innerHTML='';
}
function ppmOpenLookupPanel(panel){
  if(!panel)return;
  panel.classList.remove('hidden');
  if('hidden' in panel)panel.hidden=false;
}
function ppmRegisterLookupPanel(input,panel){
  if(!input||!panel)return;
  ppmLookupPanels.add({input,panel});
}
document.addEventListener('click',function(e){
  ppmLookupPanels.forEach(entry=>{
    if(!entry.input.isConnected){ppmLookupPanels.delete(entry);return;}
    if(entry.input===e.target||entry.input.contains(e.target)||entry.panel.contains(e.target))return;
    ppmCloseLookupPanel(entry.panel);
  });
},true);
function ppmBindLookupEmptyClose(input,panel,fetcher){
  input.addEventListener('input',function(){
    if(!input.value.trim()){
      if(fetcher)fetcher.cancel();
      ppmCloseLookupPanel(panel);
    }
  });
}
function ppmLookupFetcher(){
  let seq=0,controller=null;
  return {
    start(){seq++;if(controller)controller.abort();controller=new AbortController();return {token:seq,signal:controller.signal};},
    stale(token){return token!==seq;},
    cancel(){seq++;if(controller){controller.abort();controller=null;}}
  };
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
  const fetcher=ppmLookupFetcher();
  ppmRegisterLookupPanel(input,results);
  ppmBindLookupEmptyClose(input,results,fetcher);
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
    if(!q){ ppmCloseLookupPanel(results); return; }
    if(q.length < 2){ results.innerHTML=lookupHtmlState('Scrivi almeno 2 caratteri'); ppmOpenLookupPanel(results); return; }
    results.innerHTML=lookupHtmlState('Ricerca in corso...');
    ppmOpenLookupPanel(results);
    const {token,signal}=fetcher.start();
    try{
      const res=await fetch(`/api/clienti/search?q=${encodeURIComponent(q)}`, {headers:{'Accept':'application/json'}, signal});
      const data=await res.json();
      if(fetcher.stale(token) || input.value.trim()!==q) return;
      if(!data.ok) throw new Error(data.error || 'Errore');
      if(!data.results.length){ results.innerHTML=lookupHtmlState('Nessun risultato'); return; }
      results.innerHTML=data.results.map(function(c){
        const label=c.display || c.company_name || 'Cliente';
        const subtitle=c.subtitle || '';
        const meta=`ID ${c.id}${c.practice_count ? ' - '+c.practice_count+' pratiche' : ''}${c.last_practice ? ' - ultima '+c.last_practice : ''}`;
        return `<button type="button" class="lookup-item" data-client='${JSON.stringify(c).replace(/'/g,'&#39;')}'><b>${label}</b><small>${subtitle}</small><small>${meta}</small></button>`;
      }).join('');
    }catch(err){
      if(err.name==='AbortError' || fetcher.stale(token)) return;
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
    ppmCloseLookupPanel(results);
  });
  if(clearBtn){
    clearBtn.addEventListener('click', function(){
      if(clientId) clientId.value='';
      ['owner_first_name','owner_last_name','owner_company','owner_phone','owner_phone_2','owner_email','owner_tax_code','owner_vat','owner_street','owner_city','owner_province','owner_zip','owner_notes'].forEach(name=>setField(name,''));
      showSelected('');
      input.value='';
      ppmCloseLookupPanel(results);
    });
  }
}
function setupVetLookup(){
  const input=document.getElementById('vetSearch');
  const results=document.getElementById('vetResults');
  const select=document.querySelector('select[name="veterinarian_id"]');
  const clearBtn=document.getElementById('clearVetSelection');
  if(!input || !results || !select) return;
  const fetcher=ppmLookupFetcher();
  ppmRegisterLookupPanel(input,results);
  ppmBindLookupEmptyClose(input,results,fetcher);
  function chooseVet(v){
    let option=Array.from(select.options).find(o=>o.value===String(v.id));
    if(!option){
      option=new Option(v.display || v.clinic_name, v.id);
      select.appendChild(option);
    }
    option.dataset.fullname=v.clinic_name || v.display || '';
    option.dataset.shortname=v.short_name || v.display || '';
    option.dataset.address=v.address || '';
    option.dataset.city=v.city || '';
    option.dataset.phone=v.phone || '';
    option.dataset.provenance=v.provenance || '';
    select.value=String(v.id);
    select.dispatchEvent(new Event('change', {bubbles:true}));
    input.value=v.display || v.clinic_name || '';
    ppmCloseLookupPanel(results);
  }
  const search=ppmDebounce(async function(){
    const q=input.value.trim();
    if(!q){ ppmCloseLookupPanel(results); return; }
    if(q.length < 2){ results.innerHTML=lookupHtmlState('Scrivi almeno 2 caratteri'); ppmOpenLookupPanel(results); return; }
    results.innerHTML=lookupHtmlState('Ricerca in corso...');
    ppmOpenLookupPanel(results);
    const {token,signal}=fetcher.start();
    try{
      const res=await fetch(`/api/veterinari/search?q=${encodeURIComponent(q)}`, {headers:{'Accept':'application/json'}, signal});
      const data=await res.json();
      if(fetcher.stale(token) || input.value.trim()!==q) return;
      if(!data.ok) throw new Error(data.error || 'Errore');
      if(!data.results.length){ results.innerHTML=lookupHtmlState('Nessun risultato'); return; }
      results.innerHTML=data.results.map(function(v){
        return `<button type="button" class="lookup-item" data-vet='${JSON.stringify(v).replace(/'/g,'&#39;')}'><b>${v.display}</b><small>${v.subtitle || ''}</small><small>ID ${v.id}</small></button>`;
      }).join('');
    }catch(err){
      if(err.name==='AbortError' || fetcher.stale(token)) return;
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
      ppmCloseLookupPanel(results);
    });
  }
}
function setupOwnerVetLookup(){
  const input=document.getElementById('ownerVetSearch');
  const results=document.getElementById('ownerVetResults');
  const select=document.querySelector('select[name="owner_veterinarian_id"]');
  if(!input || !results || !select) return;
  const fetcher=ppmLookupFetcher();
  ppmRegisterLookupPanel(input,results);
  ppmBindLookupEmptyClose(input,results,fetcher);
  function chooseVet(v){
    let option=Array.from(select.options).find(o=>o.value===String(v.id));
    if(!option){
      option=new Option(v.display || v.clinic_name, v.id);
      select.appendChild(option);
    }
    option.dataset.fullname=v.clinic_name || v.display || '';
    option.dataset.shortname=v.short_name || v.display || '';
    option.dataset.address=v.address || '';
    option.dataset.city=v.city || '';
    option.dataset.phone=v.phone || '';
    option.dataset.provenance=v.provenance || '';
    select.value=String(v.id);
    select.dispatchEvent(new Event('change', {bubbles:true}));
    input.value=v.display || v.clinic_name || '';
    ppmCloseLookupPanel(results);
  }
  const search=ppmDebounce(async function(){
    const q=input.value.trim();
    if(!q){ ppmCloseLookupPanel(results); return; }
    if(q.length < 2){ results.innerHTML=lookupHtmlState('Scrivi almeno 2 caratteri'); ppmOpenLookupPanel(results); return; }
    results.innerHTML=lookupHtmlState('Ricerca in corso...');
    ppmOpenLookupPanel(results);
    const {token,signal}=fetcher.start();
    try{
      const res=await fetch(`/api/veterinari/search?q=${encodeURIComponent(q)}`, {headers:{'Accept':'application/json'}, signal});
      const data=await res.json();
      if(fetcher.stale(token) || input.value.trim()!==q) return;
      if(!data.ok) throw new Error(data.error || 'Errore');
      if(!data.results.length){ results.innerHTML=lookupHtmlState('Nessun risultato'); return; }
      results.innerHTML=data.results.map(function(v){
        return `<button type="button" class="lookup-item" data-vet='${JSON.stringify(v).replace(/'/g,'&#39;')}'><b>${v.display}</b><small>${v.subtitle || ''}</small><small>ID ${v.id}</small></button>`;
      }).join('');
    }catch(err){
      if(err.name==='AbortError' || fetcher.stale(token)) return;
      results.innerHTML=lookupHtmlState('Errore di rete durante la ricerca');
    }
  }, 300);
  input.addEventListener('input', search);
  results.addEventListener('click', function(e){
    const btn=e.target.closest('.lookup-item');
    if(!btn) return;
    chooseVet(JSON.parse(btn.getAttribute('data-vet')));
  });
}
function setupOriginVetLookup(){
  const input=document.getElementById('originVetSearch');
  const results=document.getElementById('originVetResults');
  const select=document.querySelector('select[name="origin_veterinarian_id"]');
  const mode=document.querySelector('select[name="origin_mode"]');
  const text=document.querySelector('input[name="origin_text"]');
  if(!input || !results || !select || !mode || !text)return;
  const fetcher=ppmLookupFetcher();
  ppmRegisterLookupPanel(input,results);
  ppmBindLookupEmptyClose(input,results,fetcher);
  const selected=select.options[select.selectedIndex];
  if(selected?.value)input.value=selected.dataset.shortname||selected.textContent.trim();
  else input.value=text.value;
  const search=ppmDebounce(async function(){
    const q=input.value.trim();select.value='';mode.value='Testo libero';text.value=q;
    if(!q){ppmCloseLookupPanel(results);return;}
    if(q.length<2){results.innerHTML=lookupHtmlState('Scrivi almeno 2 caratteri oppure continua con testo libero');ppmOpenLookupPanel(results);return;}
    results.innerHTML=lookupHtmlState('Ricerca in corso...');ppmOpenLookupPanel(results);
    const {token,signal}=fetcher.start();
    try{
      const response=await fetch(`/api/veterinari/search?q=${encodeURIComponent(q)}`,{headers:{'Accept':'application/json'},signal});
      const data=await response.json();
      if(fetcher.stale(token) || input.value.trim()!==q)return;
      if(!data.ok)throw new Error(data.error||'Errore');
      if(!data.results.length){results.innerHTML=lookupHtmlState('Nessun veterinario: il testo resta comunque utilizzabile');return;}
      results.innerHTML=data.results.map(v=>`<button type="button" class="lookup-item" data-origin-vet='${JSON.stringify(v).replace(/'/g,'&#39;')}'><b>${v.display}</b><small>${v.subtitle||''}</small></button>`).join('');
    }catch(error){
      if(error.name==='AbortError' || fetcher.stale(token))return;
      results.innerHTML=lookupHtmlState('Errore durante la ricerca');
    }
  },300);
  input.addEventListener('input',search);
  results.addEventListener('click',function(event){
    const button=event.target.closest('.lookup-item');if(!button)return;
    const vet=JSON.parse(button.getAttribute('data-origin-vet'));
    let option=[...select.options].find(item=>item.value===String(vet.id));
    if(!option){option=new Option(vet.display||vet.clinic_name,vet.id);option.dataset.provenance=vet.provenance||'';select.append(option);}
    select.value=String(vet.id);setProvenanceFromVeterinarian(option);mode.value='Veterinario';input.value=vet.display||vet.clinic_name||'';text.value=vet.short_name||vet.display||vet.clinic_name||'';ppmCloseLookupPanel(results);
  });
}
function openPaymentPopover(select){
  const target=document.getElementById(select.dataset.paymentPopover);if(!target)return;
  const status=target.querySelector('select[name="payment_status"]');if(status)status.value=select.value;
  target.hidden=false;document.body.style.overflow='hidden';
}
function closePaymentPopover(button){
  const target=button.closest('.payment-popover');if(target)target.hidden=true;
  document.body.style.overflow='';
}
function practiceStatusCss(status){
  return {'Ritirato':'practice-status-yellow','In programma':'practice-status-red','Cremato':'practice-status-blue','Da consegnare':'practice-status-yellow','Consegnato':'practice-status-green'}[status]||'';
}
async function savePracticeState(form,event){
  if(event)event.preventDefault();
  if(!form || form.dataset.saving==='1')return false;
  const select=form.querySelector('select[name="status"]');
  const note=form.querySelector('.inline-save-note');
  const row=form.closest('tr');
  const previous=select.dataset.savedValue||select.value;
  form.dataset.saving='1';select.disabled=true;if(note){note.textContent='Salvataggio...';note.classList.remove('error');}
  try{
    const payload=new FormData(form);payload.set('status',select.value);payload.set('ajax','1');
    const response=await fetch(form.action,{method:'POST',body:new URLSearchParams(payload),headers:{'Accept':'application/json'},credentials:'same-origin'});
    const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'Salvataggio non riuscito');
    select.dataset.savedValue=data.status;select.classList.remove('practice-status-blue','practice-status-red','practice-status-yellow','practice-status-green');
    const cls=practiceStatusCss(data.status);if(cls)select.classList.add(cls);
    if(note)note.textContent='Salvato';
    const activeFilter=new URLSearchParams(location.search).get('stato');
    if(row&&activeFilter&&activeFilter!==data.status){row.style.opacity='0';setTimeout(()=>row.remove(),180);}
    else if(note)setTimeout(()=>{note.textContent='';},1400);
  }catch(error){select.value=previous;if(note){note.textContent=error.message;note.classList.add('error');}}
  finally{select.disabled=false;form.dataset.saving='';}
  return false;
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
  const theme=document.body.classList.contains('light-theme')?'light':'dark';
  localStorage.setItem('ppm-theme',theme);
  if(document.body.dataset.hasSession==='1'){
    fetch('/il-mio-profilo/salva',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'theme='+theme}).catch(()=>{});
  }
}
function toggleCollapsibleSection(button){
  const body=button.closest('.tablebox,.section')?.querySelector('.collapsible-body');
  if(!body)return;
  const collapsed=!body.hidden;
  body.hidden=collapsed;
  button.textContent=collapsed?'+':'−';
  button.setAttribute('aria-expanded',String(!collapsed));
}
function practiceRowSelect(row,event){
  if(event.target.closest('a,button,input,select,textarea,label,form'))return;
  const already=row.classList.contains('row-selected');
  document.querySelectorAll('tr.practice-row-link.row-selected').forEach(other=>{if(other!==row)other.classList.remove('row-selected');});
  row.classList.toggle('row-selected',!already);
}
function practiceRowOpen(url){location.href=url;}
async function toggleCremationRegistered(input){
  const row=input.closest('tr');const checked=input.checked;
  try{
    const response=await fetch(`/pratiche/${input.dataset.practiceId}/cremazione-inserita`,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'value='+(checked?'Si':'')});
    if(!response.ok)throw new Error('save failed');
    if(row)row.classList.toggle('cremation-row-done',checked);
  }catch(error){
    input.checked=!checked;
    alert('Impossibile salvare, riprova.');
  }
}
let pendingOrderForm=null;
function normalizeOrderQuantity(input,value){
  const quantity=Math.max(1,Math.min(999,Math.trunc(Number(value)||1)));
  input.value=quantity;return quantity;
}
function adjustOrderQuantity(form,delta){
  const input=form.querySelector('[name="quantity"]');
  normalizeOrderQuantity(input,Number(input.value||1)+delta);
}
function setOrderQuantity(form,value){normalizeOrderQuantity(form.querySelector('[name="quantity"]'),value);}
function adjustUrnQuantity(form,delta){
  const input=form.querySelector('[name="quantity"]');
  input.value=Math.max(0,Math.trunc(Number(input.value||0)+delta));
}
function openOrderConfirmation(form,event){
  event?.preventDefault();
  const input=form.querySelector('[name="quantity"]');const quantity=normalizeOrderQuantity(input,input.value);
  const modal=document.getElementById('orderConfirmModal');if(!modal)return false;
  modal.querySelector('[data-order-confirm="quantity"]').textContent=`${quantity} boccioni`;
  modal.querySelector('[data-order-confirm="recipient"]').textContent=form.dataset.recipient||'Non configurato';
  modal.querySelector('[data-order-confirm="subject"]').textContent=form.dataset.subject||'';
  modal.querySelector('[data-order-confirm="preview"]').textContent=(form.dataset.preview||'').replaceAll('__QUANTITY__',quantity);
  pendingOrderForm=form;modal.hidden=false;document.body.classList.add('modal-open');
  modal.querySelector('[data-order-confirm-button]')?.focus();return false;
}
function closeOrderConfirmation(){
  const modal=document.getElementById('orderConfirmModal');if(modal)modal.hidden=true;
  pendingOrderForm=null;document.body.classList.remove('modal-open');
}
function confirmAndSubmitOrder(){
  if(!pendingOrderForm)return;
  pendingOrderForm.querySelector('[name="confirm_send"]').value='SI';
  const form=pendingOrderForm;closeOrderConfirmation();form.submit();
}
function toggleMoreMenu(force){
  const open=typeof force==='boolean' ? force : !document.body.classList.contains('more-open');
  document.body.classList.toggle('more-open',open);
}
document.addEventListener('DOMContentLoaded',function(){
  if(document.body.dataset.serverTheme!=='1' && localStorage.getItem('ppm-theme')==='light') document.body.classList.add('light-theme');
  document.addEventListener('keydown',function(event){
    if(event.key==='Escape') toggleMoreMenu(false);
    if(event.key==='/' && !/input|textarea|select/i.test(document.activeElement.tagName)){
      event.preventDefault(); document.getElementById('globalSearch')?.focus();
    }
  });
  const tax=document.querySelector('input[name="owner_tax_code"]');
  if(tax) tax.addEventListener('input',()=>{tax.value=tax.value.toUpperCase();});
  const globalSearch=document.getElementById('globalSearch'),globalSearchResults=document.getElementById('globalSearchResults');
  if(globalSearch&&globalSearchResults){ppmRegisterLookupPanel(globalSearch,globalSearchResults);globalSearch.addEventListener('input',()=>calendarLookup(globalSearch,'/api/calendario/pratiche/search',globalSearchResults,item=>{location.href=`/pratiche/${item.practice_id}`;}));}
  initializePushNotifications();
  formatVisibleDates();
  new MutationObserver(records=>{if(records.some(record=>record.addedNodes.length))formatVisibleDates();}).observe(document.body,{childList:true,subtree:true});
});
let calendarWizardHistoryReady=false,calendarWizardAllowExit=false;
function calendarStep(step,direction='forward',historyMode='push'){const form=document.getElementById('calendarEventForm');if(!form)return;document.querySelectorAll('.calendar-form-step').forEach(el=>{const show=Number(el.dataset.step)===step;el.hidden=!show;if(show)el.classList.toggle('step-back',direction==='back');});document.querySelectorAll('.calendar-steps button').forEach((el,i)=>{el.classList.toggle('active',i+1===step);el.setAttribute('aria-current',i+1===step?'step':'false');});form.dataset.currentStep=step;if(calendarWizardHistoryReady&&historyMode==='push'&&Number(history.state?.calendarWizardStep)!==step)history.pushState({calendarWizardStep:step},'',location.href);if(calendarWizardHistoryReady&&historyMode==='replace')history.replaceState({calendarWizardStep:step},'',location.href);scrollTo({top:0,behavior:'smooth'});}
function calendarStepFromIndicator(step){const form=document.getElementById('calendarEventForm');if(!form)return;const current=Number(form.dataset.currentStep||1);if(step>1&&(!form.operator_name?.value||!form.event_type?.value)){const error=form.querySelector('[data-operator-error]');if(error)error.textContent='Seleziona operatore e tipo di evento prima di continuare.';calendarStep(1,'back');return;}calendarTypeChanged();calendarStep(step,step<current?'back':'forward');}
function calendarAutoTitle(force=false){const form=document.getElementById('calendarEventForm');if(!form)return;const type=form.event_type?.value||'';const zone=(form.zone?.value||'').trim().toUpperCase();const site=(form.destination_site?.value||'').trim().toUpperCase();const animal=(form.animal_name?.value||form.querySelector('[data-calendar-list="animal"] [data-key="name"]')?.value||'').trim().toUpperCase();const field=form.title;if(!field)return;if(type==='Appuntamento'){return;}let title='';if(type==='Ritiro')title=`RITIRO ${zone}`;if(type==='Ritiro in sede')title=`RITIRO IN SEDE ${site}`;if(type==='Riconsegna')title=`RICONSEGNA ${animal}`;if(type==='Riconsegna in sede')title=`RICONSEGNA ${animal} IN SEDE ${site}`;if(force||!field.dataset.manual)field.value=title.trim();}
function calendarTypeChanged(){const form=document.getElementById('calendarEventForm');if(!form)return;const type=form.event_type.value;form.querySelectorAll('[data-calendar-types]').forEach(el=>{const hide=!el.dataset.calendarTypes.split('|').includes(type);el.hidden=hide;el.querySelectorAll('input,select,textarea').forEach(input=>input.disabled=hide);});if(form.zone)form.zone.required=type==='Ritiro';if(form.destination_site)form.destination_site.required=['Ritiro in sede','Riconsegna in sede'].includes(type);if(form.animal_name)form.animal_name.required=['Riconsegna','Riconsegna in sede'].includes(type);const title=form.title;if(title&&type==='Appuntamento'){title.dataset.manual='1';if(!title.value||/^PROMEMORIA/i.test(title.value))title.value='';}else if(title){delete title.dataset.manual;}calendarAutoTitle(true);}
function calendarTypeSelected(input){const form=input.form,error=form.querySelector('[data-operator-error]');if(error)error.textContent='';calendarTypeChanged();setTimeout(()=>calendarStep(2),80);}
function calendarAllDayChanged(box){const form=box.form;form.querySelectorAll('[data-calendar-time]').forEach(el=>{el.hidden=box.checked;el.querySelectorAll('input').forEach(input=>input.required=!box.checked&&input.name==='start_time');});form.querySelectorAll('[data-time-wheel]').forEach(wheel=>wheel.hidden=true);}
function calendarHtml(value){return String(value||'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));}
function calendarDateIt(value){const match=String(value||'').match(/^(\d{4})-(\d{2})-(\d{2})/);return match?`${match[3]}/${match[2]}/${match[1]}`:String(value||'');}
function formatVisibleDates(root=document.body){const excluded=new Set(['SCRIPT','STYLE','NOSCRIPT','TEXTAREA','INPUT','SELECT','OPTION','CODE','PRE']);const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);nodes.forEach(node=>{if(!node.parentElement||excluded.has(node.parentElement.tagName))return;node.nodeValue=node.nodeValue.replace(/\b(\d{4})-(\d{2})-(\d{2})(?=T|\b)/g,(_,year,month,day)=>`${day}/${month}/${year}`);});}
function calendarZoneMatches(value){const query=value.trim().toLocaleLowerCase('it');return (window.CALENDAR_ZONES||[]).filter(zone=>zone.toLocaleLowerCase('it').includes(query)).slice(0,8);}
function calendarZoneInput(input){const results=input.parentElement.querySelector('.calendar-zone-results');const matches=calendarZoneMatches(input.value);if(!input.value.trim()||!matches.length){ppmCloseLookupPanel(results);}else{results.innerHTML=matches.map(zone=>`<button type="button" data-zone="${calendarHtml(zone)}">${calendarHtml(zone)}</button>`).join('');ppmOpenLookupPanel(results);}results.onclick=e=>{const button=e.target.closest('[data-zone]');if(!button)return;input.value=button.dataset.zone;ppmCloseLookupPanel(results);calendarAutoTitle();};calendarAutoTitle();}
function calendarZoneOffer(input){setTimeout(()=>{const results=input.parentElement.querySelector('.calendar-zone-results');if(results)ppmCloseLookupPanel(results);const value=input.value.trim();if(!value)return;const known=(window.CALENDAR_ZONES||[]).some(zone=>zone.toLocaleLowerCase('it')===value.toLocaleLowerCase('it'));const save=input.closest('.field')?.querySelector('input[name="save_zone"]');if(!known&&save&&!save.checked&&confirm(`Vuoi aggiungere ${value} ai suggerimenti?`))save.checked=true;},150);}
function calendarTimeParts(input){const digits=input.value.replace(/\D/g,'').slice(0,4);if(!digits)return {hour:0,minute:0,digits};if(digits.length<=2)return {hour:Math.min(23,Number(digits)||0),minute:0,digits};const hour=digits.length===3?Number(digits[0]):Number(digits.slice(0,2)),minute=Number(digits.slice(-2));return {hour:Math.min(23,hour||0),minute:Math.min(59,minute||0),digits};}
function calendarSetWheelTime(wheel,hour,minute,notify=true){const input=wheel.closest('.calendar-datetime-row')?.querySelector('[data-time-entry]');if(!input)return;input.value=`${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}`;input.dataset.timeDigits=`${String(hour).padStart(2,'0')}${String(minute).padStart(2,'0')}`;calendarSyncTimeWheel(input,false);if(notify)input.dispatchEvent(new Event('change',{bubbles:true}));}
function calendarSyncTimeWheel(input,smooth=true){const wheel=input.closest('.calendar-datetime-row')?.querySelector('[data-time-wheel]');if(!wheel)return;const {hour,minute}=calendarTimeParts(input),wheelMinute=Math.max(0,Math.min(55,Math.round(minute/5)*5));wheel.dataset.syncing='1';wheel.querySelectorAll('.calendar-wheel-option').forEach(button=>button.classList.toggle('active',Number(button.dataset.timeValue)===(button.closest('[data-wheel-part="hour"]')?hour:wheelMinute)));wheel.querySelectorAll('.calendar-wheel-column').forEach(column=>{const active=column.querySelector('.active');if(active)column.scrollTo({top:active.offsetTop-(column.clientHeight-active.offsetHeight)/2,behavior:smooth?'smooth':'auto'});});clearTimeout(wheel._syncTimer);wheel._syncTimer=setTimeout(()=>delete wheel.dataset.syncing,420);}
function calendarInitTimeWheel(wheel){if(!wheel||wheel.dataset.ready)return;wheel.dataset.ready='1';wheel.querySelectorAll('.calendar-wheel-option').forEach(button=>button.addEventListener('click',()=>{const parts=calendarTimeParts(wheel.closest('.calendar-datetime-row').querySelector('[data-time-entry]')),roundedMinute=Math.min(55,Math.round(parts.minute/5)*5);calendarSetWheelTime(wheel,button.closest('[data-wheel-part="hour"]')?Number(button.dataset.timeValue):parts.hour,button.closest('[data-wheel-part="minute"]')?Number(button.dataset.timeValue):roundedMinute);}));wheel.querySelectorAll('.calendar-wheel-column').forEach(column=>column.addEventListener('scroll',()=>{if(wheel.dataset.syncing)return;clearTimeout(column._wheelTimer);column._wheelTimer=setTimeout(()=>{const center=column.scrollTop+column.clientHeight/2;const options=[...column.querySelectorAll('.calendar-wheel-option')];const nearest=options.reduce((best,item)=>Math.abs(item.offsetTop+item.offsetHeight/2-center)<Math.abs(best.offsetTop+best.offsetHeight/2-center)?item:best,options[0]);if(!nearest)return;const parts=calendarTimeParts(wheel.closest('.calendar-datetime-row').querySelector('[data-time-entry]')),roundedMinute=Math.min(55,Math.round(parts.minute/5)*5);calendarSetWheelTime(wheel,column.dataset.wheelPart==='hour'?Number(nearest.dataset.timeValue):parts.hour,column.dataset.wheelPart==='minute'?Number(nearest.dataset.timeValue):roundedMinute);},90);},{passive:true}));}
function calendarTimeRenderDigits(input,digits){digits=String(digits||'').replace(/\D/g,'').slice(0,4);input.dataset.timeDigits=digits;input.dataset.timeEditing='1';input.dataset.timeComplete=digits.length===4?'1':'0';input.value=digits.length<=2?digits:digits.slice(0,2)+':'+digits.slice(2);try{input.setSelectionRange(input.value.length,input.value.length);}catch(_error){}if(digits.length===4){const hour=Number(digits.slice(0,2)),minute=Number(digits.slice(2));if(hour<24&&minute<60)calendarSyncTimeWheel(input,true);}}
function calendarTimeBeforeInput(input,event){if(!event)return;const type=event.inputType||'';if(type==='deleteContentBackward'){event.preventDefault();let digits=(input.dataset.timeEditing==='1'?input.dataset.timeDigits:input.value.replace(/\D/g,'')).slice(0,-1);calendarTimeRenderDigits(input,digits);return;}if(!type.startsWith('insert')||!event.data)return;const incoming=(event.data.match(/\d/g)||[]).join('');if(!incoming)return;event.preventDefault();const selected=input.selectionStart!==input.selectionEnd;let digits=(input.dataset.timeEditing==='1'&&!selected?input.dataset.timeDigits||'':'');for(const digit of incoming){if(digits.length>=4)digits='';digits+=digit;}calendarTimeRenderDigits(input,digits);}
function calendarTimeInput(input){if(input.dataset.timeEditing==='1')return;let digits=input.value.replace(/\D/g,'').slice(0,4);input.dataset.timeDigits=digits;input.dataset.timeComplete='0';if(digits.length===4){const hour=Number(digits.slice(0,2)),minute=Number(digits.slice(2));if(hour<24&&minute<60){input.value=`${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}`;input.dataset.timeComplete='1';calendarSyncTimeWheel(input,true);}}}
function calendarTimeFocus(input){document.querySelectorAll('[data-time-wheel]').forEach(wheel=>{if(!input.closest('.calendar-datetime-row').contains(wheel))wheel.hidden=true;});input.dataset.timeEditing='0';input.dataset.timeDigits=input.value.replace(/\D/g,'').slice(0,4);const wheel=input.closest('.calendar-datetime-row')?.querySelector('[data-time-wheel]');if(wheel){calendarInitTimeWheel(wheel);wheel.hidden=false;calendarSyncTimeWheel(input,false);}}
function calendarTimeBlur(input){let digits=(input.dataset.timeDigits||input.value.replace(/\D/g,'')).slice(0,4);input.dataset.timeEditing='0';if(!digits){input.value='';input.dispatchEvent(new Event('change',{bubbles:true}));return;}if(digits.length<=2){const hour=Number(digits);input.value=hour<24?`${String(hour).padStart(2,'0')}:00`:'';}else if(digits.length===3){const hour=Number(digits.slice(0,2)),minute=Number(digits.slice(2))*10;input.value=hour<24&&minute<60?`${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}`:'';}else{const hour=Number(digits.slice(0,2)),minute=Number(digits.slice(2));input.value=hour<24&&minute<60?`${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}`:'';}input.dataset.timeDigits=input.value.replace(/\D/g,'');input.dataset.timeComplete=input.value?'1':'0';if(input.value)calendarSyncTimeWheel(input,false);input.dispatchEvent(new Event('change',{bubbles:true}));}
function calendarOpenTimePicker(button){const native=button.parentElement.querySelector('.calendar-native-time'),text=button.parentElement.querySelector('[data-time-entry]');native.value=/^\d{2}:\d{2}$/.test(text.value)?text.value:'';native.onchange=()=>{text.value=native.value;calendarTimeInput(text);};if(native.showPicker)native.showPicker();else native.click();}
function calendarRenumberAnimals(){document.querySelectorAll('[data-calendar-list="animal"] .calendar-repeat-row').forEach((row,index)=>{const title=row.querySelector('.calendar-animal-title');if(title)title.textContent=`ANIMALE ${index+1}`;const remove=row.querySelector('[data-remove-animal]');if(remove)remove.hidden=index===0;});}
function calendarAddRow(kind,data={}){const list=document.querySelector(`[data-calendar-list="${kind}"]`);if(!list)return;const row=document.createElement('div');row.className=`calendar-repeat-row ${kind==='estimate'?'calendar-estimate-row':''}`;if(kind==='animal')row.innerHTML=`<strong class="calendar-animal-title"></strong><select data-key="species" aria-label="Specie animale"><option value="">Specie</option><option ${data.species==='Cane'?'selected':''}>Cane</option><option ${data.species==='Gatto'?'selected':''}>Gatto</option><option ${data.species==='Altro'?'selected':''}>Altro</option></select><input inputmode="decimal" placeholder="Peso kg" data-key="weight" value="${data.weight||''}"><select data-key="cremation_type" aria-label="Tipo di cremazione"><option value="">Tipo di cremazione</option><option ${data.cremation_type==='Singola'?'selected':''}>Singola</option><option ${data.cremation_type==='Collettiva'?'selected':''}>Collettiva</option></select><input placeholder="Nome facoltativo" data-key="name" value="${data.name||''}"><input class="full-mobile" placeholder="Note" data-key="notes" value="${data.notes||''}"><button class="btn ghost" data-remove-animal type="button" onclick="this.parentElement.remove();calendarSerialize()">×</button>`;else if(data.preset==='Altro')row.innerHTML=`<span class="calendar-estimate-preset">Altro</span><input class="calendar-other-description" placeholder="Descrizione" data-key="description" value="${data.description||''}"><input inputmode="decimal" placeholder="Importo €" data-key="amount" value="${data.amount||''}">`;else if(data.preset)row.innerHTML=`<span class="calendar-estimate-preset">${data.preset}</span><input type="hidden" data-key="description" value="${data.preset}"><input inputmode="decimal" placeholder="Importo €" data-key="amount" value="${data.amount||''}">`;else row.innerHTML=`<input class="full-mobile" placeholder="Descrizione" data-key="description" value="${data.description||''}"><input inputmode="decimal" placeholder="Importo €" data-key="amount" value="${data.amount||''}"><button class="btn ghost" type="button" onclick="this.parentElement.remove();calendarSerialize()">×</button>`;list.append(row);row.querySelectorAll('input,select').forEach(input=>input.addEventListener('input',()=>{input.form.dataset.dirty='1';calendarSerialize();}));calendarSerialize();}
function calendarSerialize(){['animal','estimate'].forEach(kind=>{const hidden=document.querySelector(`input[name="${kind==='animal'?'animals_json':'estimate_json'}"]`);const list=document.querySelector(`[data-calendar-list="${kind}"]`);if(!hidden||!list)return;const values=[...list.children].map(row=>Object.fromEntries([...row.querySelectorAll('[data-key]')].map(input=>[input.dataset.key,input.value])));hidden.value=JSON.stringify(values);if(kind==='estimate'){const total=values.reduce((sum,item)=>sum+(Number(String(item.amount||0).replace(',','.'))||0),0);const output=document.querySelector('[data-estimate-total]');if(output)output.textContent=total.toLocaleString('it-IT',{style:'currency',currency:'EUR'});}});calendarRenumberAnimals();calendarAutoTitle();}
async function calendarLookup(input,endpoint,results,select){
  const q=input.value.trim();
  const fetcher=input._ppmFetcher||(input._ppmFetcher=ppmLookupFetcher());
  if(!q){fetcher.cancel();ppmCloseLookupPanel(results);return;}
  if(q.length<2){ppmCloseLookupPanel(results);return;}
  const {token,signal}=fetcher.start();
  try{
    const response=await fetch(`${endpoint}?q=${encodeURIComponent(q)}`,{signal});
    const data=await response.json();
    if(fetcher.stale(token)||input.value.trim()!==q)return;
    results.innerHTML=(data.results||[]).map(item=>`<button type="button" class="lookup-item" data-value='${JSON.stringify(item).replace(/'/g,'&#39;')}'><b>${item.display||item.name||''}</b><small>${item.subtitle||item.phone||''}</small></button>`).join('')||'<div class="lookup-state">Nessun risultato: i dati restano solo nell evento.</div>';
    ppmOpenLookupPanel(results);
    results.onclick=e=>{const button=e.target.closest('[data-value]');if(!button)return;select(JSON.parse(button.dataset.value));ppmCloseLookupPanel(results);};
  }catch(error){
    if(error.name==='AbortError'||fetcher.stale(token))return;
    results.innerHTML='<div class="lookup-state">Ricerca temporaneamente non disponibile.</div>';ppmOpenLookupPanel(results);
  }
}
function calendarSelectVeterinarian(form,item){form.veterinarian_id.value=item.id||'';form.veterinarian_name.value=item.clinic_name||item.display||'';form.veterinarian_contact.value=item.doctor_name||'';form.veterinarian_phone.value=item.phone||'';form.veterinarian_address.value=item.address||'';form.veterinarian_hours.value=item.notes||'';if(form.venue_name)form.venue_name.value=item.clinic_name||item.display||'';if(form.address)form.address.value=[item.address,item.city].filter(Boolean).join(' - ');form.phone.value=item.phone||'';const vetInput=document.getElementById('calendarVetSearch');if(vetInput)vetInput.value=item.clinic_name||item.display||'';}
function calendarSyncPickupLocation(select){const form=select.form;form.querySelectorAll('[data-pickup-location]').forEach(el=>{const show=el.dataset.pickupLocation.split('|').includes(select.value);el.hidden=!show;el.querySelectorAll('input,select').forEach(input=>input.disabled=!show);});const addressField=form.elements.namedItem('address');if(addressField)addressField.required=!!select.value;calendarUpdateClientAddressButton(form);}
function calendarPickupLocationChanged(select){const form=select.form;if(select.dataset.prevValue&&select.dataset.prevValue!==select.value){if(form.address)form.address.value='';if(form.venue_name)form.venue_name.value='';if(form.veterinarian_id)form.veterinarian_id.value='';const vetInput=document.getElementById('calendarVetSearch');if(vetInput)vetInput.value='';}select.dataset.prevValue=select.value;calendarSyncPickupLocation(select);}
function calendarUpdateClientAddressButton(form){const btn=form.querySelector('[data-use-client-address]');if(!btn)return;const address=form.client_id?.dataset.clientAddress||'';btn.hidden=!(form.location_type?.value==='Privato'&&address);}
function calendarUseClientAddress(button){const form=button.form;const address=form.client_id?.dataset.clientAddress||'';if(address&&form.address)form.address.value=address;}
function calendarUpdateClientEmptyHint(form){const hint=form.querySelector('[data-client-empty-hint]');if(!hint)return;const empty=!form.client_first_name?.value.trim()&&!form.client_last_name?.value.trim()&&!form.client_id?.value;hint.hidden=!empty;}
async function calendarPickupClientLookup(input,results){
  const q=input.value.trim();
  const fetcher=input._ppmFetcher||(input._ppmFetcher=ppmLookupFetcher());
  if(!q){fetcher.cancel();ppmCloseLookupPanel(results);return;}
  if(q.length<2){ppmCloseLookupPanel(results);return;}
  const {token,signal}=fetcher.start();
  try{
    const response=await fetch(`/api/clienti/search?q=${encodeURIComponent(q)}`,{signal});
    const data=await response.json();
    if(fetcher.stale(token)||input.value.trim()!==q)return;
    const items=data.results||[];
    results.innerHTML=items.map((item,index)=>`<button type="button" class="lookup-item" data-pickup-client-index="${index}"><span><b>${calendarHtml(item.display||'')}</b><small>${calendarHtml(item.subtitle||item.phone||'')}</small></span></button>`).join('')||'<div class="lookup-state">Nessun cliente trovato: i dati inseriti resteranno solo nell evento.</div>';
    ppmOpenLookupPanel(results);
    results.onclick=e=>{
      const button=e.target.closest('[data-pickup-client-index]');if(!button)return;
      const item=items[Number(button.dataset.pickupClientIndex)],form=input.form;
      form.client_id.value=item.id||'';
      form.client_first_name.value=item.first_name||'';
      form.client_last_name.value=item.last_name||'';
      form.client_phone.value=item.phone||'';
      form.client_id.dataset.clientAddress=[item.street||item.address,item.city].filter(Boolean).join(', ');
      calendarUpdateClientAddressButton(form);calendarUpdateClientEmptyHint(form);
      input.value='';ppmCloseLookupPanel(results);
    };
  }catch(error){
    if(error.name==='AbortError'||fetcher.stale(token))return;
    results.innerHTML='<div class="lookup-state">Ricerca temporaneamente non disponibile.</div>';ppmOpenLookupPanel(results);
  }
}
function calendarConfirmPickupStatus(form){const select=form.elements.namedItem('status');if(select&&select.value==='Ritirato'&&form.dataset.clientEmpty==='1')return confirm('Cliente non inserito: vuoi completarlo ora prima di generare la pratica? Annulla per compilarlo, OK per procedere comunque.');return true;}
function calendarSelectDeliveryClinic(form,item){form.delivery_clinic_id.value=item.id||'';form.delivery_clinic_name.value=item.clinic_name||item.display||'';form.delivery_clinic_address.value=item.address||'';form.delivery_clinic_phone.value=item.phone||'';const input=document.getElementById('calendarDeliveryClinicSearch');if(input)input.value=item.clinic_name||item.display||'';}
async function calendarDeliveryAnimalLookup(input,results){
  const q=input.value.trim();
  const fetcher=input._ppmFetcher||(input._ppmFetcher=ppmLookupFetcher());
  if(!q){fetcher.cancel();ppmCloseLookupPanel(results);results.onclick=null;return;}
  if(q.length<2){ppmCloseLookupPanel(results);results.onclick=null;return;}
  const {token,signal}=fetcher.start();
  try{
    const response=await fetch(`/api/calendario/animali/search?q=${encodeURIComponent(q)}`,{signal});
    const data=await response.json();
    if(fetcher.stale(token)||input.value.trim()!==q)return;
    const items=data.results||[];
    results.innerHTML=items.map((item,index)=>`<button type="button" class="lookup-item" data-delivery-animal-index="${index}"><span><b>${calendarHtml(item.animal_name||'Animale senza nome')}</b><small>${calendarHtml([item.owner_name,item.species,item.pickup_date?`recupero ${calendarDateIt(item.pickup_date)}`:'',item.practice_number].filter(Boolean).join(' · '))}</small><small>${calendarHtml(item.payment_summary||'')}</small></span></button>`).join('')||'<div class="lookup-state">Nessun animale o proprietario trovato.</div>';
    ppmOpenLookupPanel(results);
    results.onclick=e=>{const button=e.target.closest('[data-delivery-animal-index]');if(!button)return;const item=items[Number(button.dataset.deliveryAnimalIndex)],form=input.form;input.value=item.animal_name||'';form.linked_practice_id.value=item.practice_id||'';form.payment_status.value=item.calendar_payment_status||'Da pagare';form.payment_amount.value=Number(item.calendar_payment_amount||0).toFixed(2).replace('.',',');if(form.delivery_address)form.delivery_address.value=item.owner_address||'';const detail=form.querySelector('[data-delivery-payment-detail]');if(detail)detail.value=item.payment_summary||'';calendarAutoTitle(true);ppmCloseLookupPanel(results);results.onclick=null;};
  }catch(error){
    if(error.name==='AbortError'||fetcher.stale(token))return;
    results.innerHTML='<div class="lookup-state">Ricerca temporaneamente non disponibile.</div>';ppmOpenLookupPanel(results);
  }
}
async function calendarLinkPracticeLookup(input,results){
  const q=input.value.trim();
  const fetcher=input._ppmFetcher||(input._ppmFetcher=ppmLookupFetcher());
  if(!q){fetcher.cancel();ppmCloseLookupPanel(results);return;}
  if(q.length<2){ppmCloseLookupPanel(results);return;}
  const {token,signal}=fetcher.start();
  try{
    const response=await fetch(`/api/calendario/pratiche/search?q=${encodeURIComponent(q)}`,{signal});
    const data=await response.json();
    if(fetcher.stale(token)||input.value.trim()!==q)return;
    const items=data.results||[];
    results.innerHTML=items.map((item,index)=>`<button type="button" class="lookup-item" data-link-practice-index="${index}"><span><b>${calendarHtml(item.practice_number||'')} · ${calendarHtml(item.animal_name||'Senza nome')}</b><small>${calendarHtml([item.owner_name,item.species,item.veterinarian_name,item.pickup_date?`recupero ${calendarDateIt(item.pickup_date)}`:'',item.status].filter(Boolean).join(' · '))}</small></span></button>`).join('')||'<div class="lookup-state">Nessuna pratica trovata.</div>';
    ppmOpenLookupPanel(results);
    results.onclick=e=>{
      const button=e.target.closest('[data-link-practice-index]');if(!button)return;
      const item=items[Number(button.dataset.linkPracticeIndex)];
      if(!confirm(`Collegare la pratica ${item.practice_number} a questo evento?`))return;
      const form=document.createElement('form');
      form.method='post';form.action=`/calendario/${input.dataset.eventId}/collega-pratica`;
      const hidden=document.createElement('input');hidden.type='hidden';hidden.name='practice_id';hidden.value=item.practice_id;
      form.append(hidden);document.body.append(form);form.submit();
    };
  }catch(error){
    if(error.name==='AbortError'||fetcher.stale(token))return;
    results.innerHTML='<div class="lookup-state">Ricerca temporaneamente non disponibile.</div>';ppmOpenLookupPanel(results);
  }
}
function calendarInitLookups(){
  const vet=document.getElementById('calendarVetSearch'),vetResults=document.getElementById('calendarVetResults');
  if(vet){ppmRegisterLookupPanel(vet,vetResults);vet.addEventListener('input',()=>calendarLookup(vet,'/api/veterinari/search',vetResults,item=>calendarSelectVeterinarian(vet.form,item)));}
  const deliveryAnimal=document.getElementById('calendarDeliveryAnimalSearch'),deliveryResults=document.getElementById('calendarDeliveryAnimalResults');
  if(deliveryAnimal){ppmRegisterLookupPanel(deliveryAnimal,deliveryResults);deliveryAnimal.addEventListener('input',()=>calendarDeliveryAnimalLookup(deliveryAnimal,deliveryResults));}
  const linkPractice=document.getElementById('calendarLinkPracticeSearch'),linkPracticeResults=document.getElementById('calendarLinkPracticeResults');
  if(linkPractice){ppmRegisterLookupPanel(linkPractice,linkPracticeResults);linkPractice.addEventListener('input',()=>calendarLinkPracticeLookup(linkPractice,linkPracticeResults));}
  const deliveryClinic=document.getElementById('calendarDeliveryClinicSearch'),deliveryClinicResults=document.getElementById('calendarDeliveryClinicResults');
  if(deliveryClinic){ppmRegisterLookupPanel(deliveryClinic,deliveryClinicResults);deliveryClinic.addEventListener('input',()=>calendarLookup(deliveryClinic,'/api/veterinari/search',deliveryClinicResults,item=>calendarSelectDeliveryClinic(deliveryClinic.form,item)));}
  document.querySelectorAll('.calendar-zone-field input[name="zone"]').forEach(input=>{
    const panel=input.parentElement.querySelector('.calendar-zone-results');
    if(panel)ppmRegisterLookupPanel(input,panel);
  });
  const pickupClient=document.getElementById('calendarClientSearch'),pickupClientResults=document.getElementById('calendarClientResults');
  if(pickupClient){
    ppmRegisterLookupPanel(pickupClient,pickupClientResults);
    pickupClient.addEventListener('input',()=>calendarPickupClientLookup(pickupClient,pickupClientResults));
    const form=pickupClient.form;
    calendarUpdateClientEmptyHint(form);
    calendarUpdateClientAddressButton(form);
    ['client_first_name','client_last_name'].forEach(name=>form.elements.namedItem(name)?.addEventListener('input',()=>calendarUpdateClientEmptyHint(form)));
  }
  const pickupLocation=document.querySelector('select[name="location_type"]');
  if(pickupLocation)calendarSyncPickupLocation(pickupLocation);
}
function calendarSwipeNavigation(){document.querySelectorAll('[data-calendar-swipe]').forEach(board=>{let x=0,y=0,startedInteractive=false;board.addEventListener('touchstart',e=>{startedInteractive=!!e.target.closest('a,button,input,select,textarea,.calendar-event');x=e.touches[0].clientX;y=e.touches[0].clientY},{passive:true});board.addEventListener('touchend',e=>{if(startedInteractive)return;const dx=e.changedTouches[0].clientX-x,dy=e.changedTouches[0].clientY-y;if(Math.abs(dx)>75&&Math.abs(dx)>Math.abs(dy)*1.45){const link=document.querySelector(dx<0?'[data-calendar-next]':'[data-calendar-prev]');if(link)location.href=link.href;}},{passive:true});});}
function calendarWizardDirty(form){return form.dataset.dirty==='1'||[...form.elements].some(input=>input.dataset.initialValue!==undefined&&input.value!==input.dataset.initialValue);}
function calendarConfirmExit(event,href){const form=document.getElementById('calendarEventForm');if(form&&calendarWizardDirty(form)&&!confirm('Vuoi uscire? Le modifiche non salvate andranno perse.')){event?.preventDefault();return false;}calendarWizardAllowExit=true;if(href){event?.preventDefault();location.href=href;}return true;}
function calendarSubmit(form){calendarSerialize();form.querySelectorAll('[aria-invalid="true"]').forEach(el=>el.removeAttribute('aria-invalid'));const invalid=[...form.elements].find(input=>!input.disabled&&!input.checkValidity());if(invalid){invalid.setAttribute('aria-invalid','true');calendarStep(Number(invalid.closest('[data-step]')?.dataset.step||1),'back','replace');invalid.reportValidity();return false;}try{if(new URL(form.action,location.href).pathname==='/calendario/nuovo')sessionStorage.setItem('ppm_calendar_created','1');}catch(error){}calendarWizardAllowExit=true;return true;}
// history.back() is intentionally intercepted so the wizard returns to the previous step.
function calendarWizardSwipe(){const form=document.getElementById('calendarEventForm');if(!form)return;calendarWizardHistoryReady=true;history.replaceState({calendarWizardStep:Number(form.dataset.currentStep||1)},'',location.href);form.querySelectorAll('input,select,textarea').forEach(input=>{input.dataset.initialValue=input.value;input.addEventListener('change',()=>form.dataset.dirty='1');input.addEventListener('input',()=>form.dataset.dirty='1');});let x=0,y=0,backGesture=false;form.addEventListener('touchstart',e=>{x=e.touches[0].clientX;y=e.touches[0].clientY;backGesture=x<18},{passive:true});form.addEventListener('touchmove',e=>{if(backGesture&&e.touches[0].clientX-x>16)e.preventDefault()},{passive:false});form.addEventListener('touchend',e=>{const dx=e.changedTouches[0].clientX-x,dy=e.changedTouches[0].clientY-y,step=Number(form.dataset.currentStep||1);if(backGesture&&dx>80&&dx>Math.abs(dy)*1.5&&step>1){e.preventDefault();calendarStep(step-1,'back','replace');}backGesture=false;},{passive:false});window.addEventListener('popstate',e=>{const step=Number(e.state?.calendarWizardStep||1);calendarStep(step,'back','none');});window.addEventListener('beforeunload',e=>{if(!calendarWizardAllowExit&&calendarWizardDirty(form)){e.preventDefault();e.returnValue='';}});}
function setupPracticeAutosave(){const form=document.getElementById('practiceForm');if(!form?.dataset.autosaveUrl)return;const status=document.getElementById('practiceAutosaveStatus'),label=status?.querySelector('[data-autosave-label]'),time=status?.querySelector('[data-autosave-time]'),retry=status?.querySelector('[data-autosave-retry]');let timer=null,inflight=false,queued=false,failed=false,allowExit=false,version=form.dataset.updatedAt||'';const ignored=new Set(['return_to','save_and_return','status']);const value=input=>input.type==='checkbox'?(input.checked?input.value:''):input.value;const baseline=new Map([...form.elements].filter(i=>i.name&&!ignored.has(i.name)&&i.type!=='file').map(i=>[i.name,value(i)]));const changed=()=>Object.fromEntries([...form.elements].filter(i=>i.name&&!ignored.has(i.name)&&i.type!=='file'&&baseline.get(i.name)!==value(i)).map(i=>[i.name,value(i)]));const show=(state,text)=>{if(!status)return;status.dataset.state=state;label.textContent=text;retry.hidden=state!=='error';};const save=async()=>{if(inflight){queued=true;return;}const changes=changed();if(!Object.keys(changes).length){failed=false;show('saved','Salvato');return;}inflight=true;failed=false;show('saving','Salvataggio automatico…');try{const response=await fetch(form.dataset.autosaveUrl,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8','Accept':'application/json'},body:new URLSearchParams({updated_at:version,changes_json:JSON.stringify(changes)})});const data=await response.json();if(!response.ok)throw Object.assign(new Error(data.error||'Salvataggio non riuscito'),{conflict:response.status===409});Object.entries(changes).forEach(([key,sent])=>{const input=form.elements.namedItem(key);if(input&&value(input)===sent)baseline.set(key,sent);});version=data.updated_at||version;form.dataset.updatedAt=version;show('saved','Salvato');time.textContent=`Ultimo salvataggio: ${data.saved_at||new Date().toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'})}`;}catch(error){failed=true;show(error.conflict?'conflict':'error',error.conflict?'Conflitto: la pratica è stata modificata altrove. Ricarica la pagina.':'Errore di salvataggio');}finally{inflight=false;if(queued){queued=false;save();}}};const schedule=()=>{clearTimeout(timer);show('dirty','Modifiche non salvate');timer=setTimeout(save,1800);};form.addEventListener('input',schedule);form.addEventListener('change',schedule);form.addEventListener('submit',()=>{allowExit=true;clearTimeout(timer)});retry?.addEventListener('click',save);window.addEventListener('beforeunload',e=>{if(!allowExit&&(inflight||failed||Object.keys(changed()).length)){e.preventDefault();e.returnValue='';}});}
function toggleCreateMenu(force){
  const open=typeof force==='boolean'?force:!document.body.classList.contains('create-menu-open');
  document.body.classList.toggle('create-menu-open',open);
  if(open){
    const link=document.querySelector('[data-calendar-new-event]');
    if(link){
      const params=location.pathname==='/calendario'?new URLSearchParams(location.search):null;
      const data=params?params.get('data'):null;
      link.href=data?`/calendario/nuovo?data=${encodeURIComponent(data)}`:'/calendario/nuovo';
    }
  }
}
function calendarInitDateTimeSync(){
  const form=document.getElementById('calendarEventForm');
  if(!form||!form.start_date||!form.end_date)return;
  const markManual=input=>{if(!input)return;const mark=()=>{input.dataset.manualEdit='1';};input.addEventListener('input',mark);input.addEventListener('change',mark);};
  markManual(form.end_date);markManual(form.end_time);
  // Editing an existing event: its saved end date/time were chosen on purpose, so changing
  // the start must never silently overwrite them (the fields stay fully editable by hand).
  if((form.dataset.draftKey||'').includes('_edit_')){
    if(form.end_date)form.end_date.dataset.manualEdit='1';
    if(form.end_time&&form.end_time.value)form.end_time.dataset.manualEdit='1';
  }
  const sync=()=>{
    if(form.end_date&&!form.end_date.dataset.manualEdit)form.end_date.value=form.start_date.value;
    if(form.end_time&&form.start_time&&!form.end_time.dataset.manualEdit){
      form.end_time.value=form.start_time.value;
      form.end_time.dataset.timeDigits=form.start_time.dataset.timeDigits||'';
      calendarSyncTimeWheel(form.end_time,false);
    }
  };
  form.start_date.addEventListener('change',sync);
  if(form.start_time)form.start_time.addEventListener('change',sync);
}
function setupCalendarDraftAutosave(form){
  if(!form)return;
  const key=form.dataset.draftKey;
  if(!key)return;
  const status=document.getElementById('calendarDraftStatus'),label=status?.querySelector('[data-draft-label]');
  const show=(state,text)=>{if(!status)return;status.hidden=false;status.dataset.state=state;if(label)label.textContent=text;};
  const skipField=name=>{const input=form.elements.namedItem(name);return !input||input.type==='password'||/token|session/i.test(name);};
  const fieldValue=input=>input.type==='checkbox'?(input.checked?input.value:''):input.value;
  const serialize=()=>{const data={};[...form.elements].forEach(el=>{if(!el.name||el.disabled||skipField(el.name))return;if(el.type==='radio'){if(el.checked)data[el.name]=el.value;return;}data[el.name]=fieldValue(el);});return data;};
  const restore=()=>{
    let raw;try{raw=localStorage.getItem(key);}catch(error){return;}
    if(!raw)return;
    let data;try{data=JSON.parse(raw);}catch(error){try{localStorage.removeItem(key);}catch(_error){}return;}
    Object.entries(data).forEach(([name,value])=>{
      if(name==='animals_json'||name==='estimate_json')return;
      const input=form.elements.namedItem(name);if(!input)return;
      if(input.type==='checkbox'){input.checked=value!==''&&input.value===value;return;}
      if(input.type==='radio'){const radio=[...form.querySelectorAll(`[name="${name}"]`)].find(el=>el.value===value);if(radio)radio.checked=true;return;}
      input.value=value;
    });
    ['animal','estimate'].forEach(kind=>{
      const rawList=data[kind==='animal'?'animals_json':'estimate_json'];if(!rawList)return;
      let items;try{items=JSON.parse(rawList);}catch(error){return;}
      if(!Array.isArray(items)||!items.length)return;
      const list=form.querySelector(`[data-calendar-list="${kind}"]`);if(list)list.innerHTML='';
      items.forEach(item=>calendarAddRow(kind,item));
    });
    calendarTypeChanged();calendarSerialize();
    show('saved','Bozza ripristinata');
  };
  const save=ppmDebounce(()=>{
    try{localStorage.setItem(key,JSON.stringify(serialize()));}catch(error){return;}
    show('saved','Bozza salvata');
  },1800);
  form.addEventListener('input',()=>{show('saving','Salvataggio…');save();});
  form.addEventListener('change',()=>{show('saving','Salvataggio…');save();});
  form.addEventListener('submit',()=>{try{localStorage.removeItem(key);}catch(error){}});
  restore();
}
document.addEventListener('DOMContentLoaded',()=>{calendarInitLookups();calendarSwipeNavigation();calendarWizardSwipe();calendarSerialize();setupPracticeAutosave();calendarInitDateTimeSync();setupCalendarDraftAutosave(document.getElementById('calendarEventForm'));document.addEventListener('pointerdown',event=>{if(!event.target.closest('.calendar-datetime-row'))document.querySelectorAll('[data-time-wheel]').forEach(wheel=>wheel.hidden=true);});});
function showSwUpdateBanner(onConfirm){
  if(document.querySelector('.sw-update-banner'))return;
  const bar=document.createElement('div');bar.className='sw-update-banner';
  bar.innerHTML='<span>Nuova versione disponibile</span><button type="button">Aggiorna ora</button>';
  bar.querySelector('button').addEventListener('click',()=>{onConfirm();bar.remove();});
  document.body.appendChild(bar);
}
function applySwUpdateWhenSafe(worker){
  let applied=false;
  const activate=()=>{if(applied)return;applied=true;document.removeEventListener('visibilitychange',onHidden);document.querySelector('.sw-update-banner')?.remove();worker.postMessage({type:'SKIP_WAITING'});};
  const onHidden=()=>{if(document.visibilityState==='hidden')activate();};
  if(document.visibilityState==='hidden'){activate();return;}
  showSwUpdateBanner(activate);
  document.addEventListener('visibilitychange',onHidden);
}
if('serviceWorker' in navigator){
  window.addEventListener('load',()=>{
    navigator.serviceWorker.register('/sw.js').then(registration=>{
      let reloading=false;
      navigator.serviceWorker.addEventListener('controllerchange',()=>{
        if(reloading)return;reloading=true;location.reload();
      });
      const freshLoad=!navigator.serviceWorker.controller;
      if(registration.waiting){
        if(freshLoad)registration.waiting.postMessage({type:'SKIP_WAITING'});
        else applySwUpdateWhenSafe(registration.waiting);
      }
      registration.addEventListener('updatefound',()=>{
        const worker=registration.installing;
        if(!worker)return;
        worker.addEventListener('statechange',()=>{
          if(worker.state==='installed' && navigator.serviceWorker.controller) applySwUpdateWhenSafe(worker);
        });
      });
    }).catch(error=>console.warn('Service worker non registrato',error));
  });
}
function urlBase64ToUint8Array(value){
  value=(value||'').trim().replace(/^['\"]|['\"]$/g,'');
  const padding='='.repeat((4-value.length%4)%4),base64=(value+padding).replace(/-/g,'+').replace(/_/g,'/');
  return Uint8Array.from(atob(base64),c=>c.charCodeAt(0));
}
function pushDiagnostic(name,value,bad=false){
  const node=document.querySelector(`[data-push-diagnostic="${name}"]`);
  if(node){node.textContent=value;node.classList.toggle('warning',bad);}
}
function pushError(error){
  const text=error instanceof Error?error.message:String(error||'Errore sconosciuto');
  pushDiagnostic('lastError',text,true);
  const box=document.getElementById('pushVisibleError');
  if(box){box.textContent=text;box.classList.remove('hidden');}
  return text;
}
function waitForActiveWorker(registration,timeout=15000){
  if(registration.active)return Promise.resolve(registration.active);
  const worker=registration.installing||registration.waiting;
  if(!worker)return navigator.serviceWorker.ready.then(ready=>ready.active);
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Il service worker non è diventato attivo entro 15 secondi.')),timeout);
    const check=()=>{if(worker.state==='activated'){clearTimeout(timer);resolve(worker);}else if(worker.state==='redundant'){clearTimeout(timer);reject(new Error('Installazione del service worker non riuscita.'));}};
    worker.addEventListener('statechange',check);check();
  });
}
async function syncPushSubscription(){
  pushDiagnostic('permission',('Notification' in window)?Notification.permission:'non disponibile');
  if(!('serviceWorker' in navigator)) throw new Error('Service worker non supportato da questo browser.');
  if(!('PushManager' in window)) throw new Error('Push non disponibile. Su iPhone installa la PWA dalla schermata Home.');
  if(Notification.permission!=='granted') throw new Error('Il permesso notifiche non è stato concesso.');
  const key=document.body.dataset.vapidPublicKey||'';
  if(!key) throw new Error('Chiave VAPID pubblica non configurata sul server.');
  const applicationServerKey=urlBase64ToUint8Array(key);
  if(applicationServerKey.length!==65) throw new Error('Chiave VAPID pubblica non valida.');
  let registration=await navigator.serviceWorker.getRegistration('/');
  if(!registration) registration=await navigator.serviceWorker.register('/sw.js',{scope:'/'});
  pushDiagnostic('registered',registration?'sì':'no',!registration);
  await waitForActiveWorker(registration);
  registration=await navigator.serviceWorker.ready;
  pushDiagnostic('active',registration.active?'sì':'no',!registration.active);
  let subscription=await registration.pushManager.getSubscription();
  if(!subscription) subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey});
  pushDiagnostic('subscription',subscription?'sì':'no',!subscription);
  pushDiagnostic('endpoint',subscription.endpoint.slice(0,32)+'…');
  const response=await fetch('/api/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({subscription:subscription.toJSON(),device:{name:navigator.userAgentData?.platform||navigator.platform||'PWA',platform:navigator.userAgent,standalone:window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true}})});
  const data=await response.json().catch(()=>({ok:false,error:`Risposta HTTP ${response.status}`}));
  pushDiagnostic('backend',data.message||data.error||`HTTP ${response.status}`,!response.ok||!data.ok);
  if(!response.ok||!data.ok) throw new Error(data.error||'Il server non ha salvato la sottoscrizione.');
  const count=Number(data.subscriptions||1);
  document.querySelectorAll('[data-push-device-count]').forEach(node=>node.textContent=String(count));
  pushDiagnostic('devices',String(count),count<1);pushDiagnostic('lastError','nessuno');
  document.getElementById('pushVisibleError')?.classList.add('hidden');
  return true;
}
async function enablePushNotifications(){
  if(!('Notification' in window) || !('PushManager' in window)){
    alert('Le notifiche push non sono disponibili in questo browser. Su iPhone installa prima la PWA dalla Home.'); return false;
  }
  const permission=Notification.permission==='granted'?'granted':await Notification.requestPermission();
  localStorage.setItem('ppm-notification-prompted','1');
  document.querySelector('.permission-prompt')?.remove();
  if(permission!=='granted'){alert('Permesso non concesso. Potrai riprovare dalle Impostazioni.');return false;}
  try{const ok=await syncPushSubscription();if(ok) alert('Notifiche abilitate su questo dispositivo.');return ok;}
  catch(error){console.warn('Attivazione notifiche non riuscita',error);alert(pushError(error));return false;}
}
function initializePushNotifications(){
  pushDiagnostic('permission',('Notification' in window)?Notification.permission:'non disponibile');
  fetch('/api/notifiche/stato',{headers:{'Accept':'application/json'}}).then(r=>r.json()).then(data=>{document.querySelectorAll('[data-push-device-count]').forEach(node=>node.textContent=String(data.subscriptions||0));pushDiagnostic('devices',String(data.subscriptions||0),!data.subscriptions);pushDiagnostic('endpoint',data.endpoint||'—');pushDiagnostic('lastError',data.last_error||'nessuno',!!data.last_error);pushDiagnostic('backend',data.ok?'raggiungibile':'errore',!data.ok);if('setAppBadge' in navigator){if(data.unread) navigator.setAppBadge(data.unread);else navigator.clearAppBadge();}}).catch(error=>pushError(error));
  if(!('Notification' in window) || !('serviceWorker' in navigator)) return;
  if(Notification.permission==='granted'){syncPushSubscription().catch(error=>{console.warn('Sincronizzazione push non riuscita',error);pushError(error);});return;}
  if(Notification.permission!=='default' || localStorage.getItem('ppm-notification-prompted')) return;
  const prompt=document.createElement('aside'); prompt.className='permission-prompt';
  prompt.innerHTML='<b>Ricevi le notifiche di Pet Paradise</b><p>Attivale per aggiornamenti su pratiche, recuperi, pagamenti e WhatsApp anche quando il gestionale è chiuso.</p><div class="actions"><button class="btn" type="button">Abilita notifiche</button><button class="btn ghost" type="button">Non ora</button></div>';
  const buttons=prompt.querySelectorAll('button'); buttons[0].onclick=enablePushNotifications; buttons[1].onclick=()=>{localStorage.setItem('ppm-notification-prompted','1');prompt.remove();}; document.body.appendChild(prompt);
}
async function schedulePushTest(){
  try{const response=await fetch('/api/push/test',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:'{}'});const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'Test non programmato.');pushDiagnostic('backend',data.message);alert(data.message);}
  catch(error){alert(pushError(error));}
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
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
}


def lucide(name, label=""):
    path=LUCIDE_PATHS.get(name,LUCIDE_PATHS["menu"])
    aria=f' aria-label="{esc(label)}"' if label else ' aria-hidden="true"'
    return f'<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"{aria}>{path}</svg>'


def normalize_money_text(value):
    text=str(value or "").strip().replace("€","").replace(" ","")
    if "," in text and "." in text:
        text=text.replace(".","").replace(",",".")
    else:
        text=text.replace(",",".")
    return text


def money_value(value):
    text=normalize_money_text(value)
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
    definitive_value=money_value(definitive)
    return definitive_value if definitive_value > 0 else calculated_service_total(practice)


def calculated_service_total(practice):
    keys=practice.keys() if hasattr(practice,"keys") else practice
    component_keys=("price_cremation","price_pickup","price_urn","price_urn_2","price_delivery","price_cast","price_cast_2","price_paw_cast","price_nose_cast","price_evening","price_night","price_holiday","price_accessories","price_accessories_2")
    available=[key for key in component_keys if key in keys]
    return sum(money_value(practice[key]) for key in available) if available else money_value(practice["total_service"] if "total_service" in keys else "")


def uses_total_d(practice):
    keys=practice.keys() if hasattr(practice,"keys") else practice
    return money_value(practice["total_text"] if "total_text" in keys else "") > 0


def payment_channel(practice):
    return "D" if uses_total_d(practice) else "W"


def payment_amount_with_channel(practice, amount):
    return f"{money_it(amount)} {payment_channel(practice)}"


def received_amount(practice):
    due=effective_total(practice)
    keys=practice.keys() if hasattr(practice,"keys") else practice
    if (practice["payment_status"] if "payment_status" in keys else "") == "Pagato":
        return due
    return min(due, max(0.0, money_value(practice["deposit"] if "deposit" in keys else "")))


def outstanding_amount(practice):
    return max(0.0, effective_total(practice)-received_amount(practice))


def dashboard_period_bounds(period, today=None):
    today=today or datetime.now().date()
    period=period if period in ("oggi","settimana","mese") else "oggi"
    if period=="settimana":
        start=today-timedelta(days=(today.weekday()-5)%7);end=start+timedelta(days=6)
    elif period=="mese":
        start=today.replace(day=1)
        next_month=(start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1))
        end=next_month-timedelta(days=1)
    else:start=end=today
    return period,start,end


def status_event_date_sql(status, alias="practices"):
    safe=str(status).replace("'","''")
    return f"""(SELECT date(MAX(h.created_at)) FROM practice_history h WHERE h.practice_id={alias}.id AND (
      (h.event_type='Cambio stato rapido' AND h.new_value='{safe}') OR
      (h.event_type='Cambio stati' AND h.new_value LIKE '{safe} +%') OR
      (h.event_type='Modifica Stato pratica' AND h.new_value='{safe}')
    ))"""


def dashboard_practice_date_sql(kind, alias="practices"):
    if kind=="ritirati":
        event=status_event_date_sql("Ritirato",alias)
        return f"COALESCE(date(NULLIF({alias}.pickup_date,'')),{event},CASE WHEN {alias}.status='Ritirato' THEN date({alias}.updated_at) END)"
    if kind=="in_programma":
        event=status_event_date_sql("In programma",alias)
        return f"COALESCE(date(NULLIF({alias}.pickup_date,'')),{event},CASE WHEN {alias}.status='In programma' THEN date({alias}.updated_at) END)"
    if kind=="consegnati":
        event=status_event_date_sql("Consegnato",alias)
        return f"COALESCE({event},CASE WHEN {alias}.status='Consegnato' THEN date({alias}.updated_at) END)"
    return "NULL"


def practice_status_class(status):
    return {"Ritirato":"practice-status-yellow","In programma":"practice-status-red","Cremato":"practice-status-blue","Da consegnare":"practice-status-yellow","Consegnato":"practice-status-green"}.get(status,"")


def row_open_attrs(url,label=""):
    aria=f' aria-label="{esc(label)}"' if label else ''
    return f'''tabindex="0" role="link"{aria} onclick="practiceRowSelect(this,event)" ondblclick="practiceRowOpen('{url}')" onkeydown="if(event.key==='Enter'||event.key===' '){{event.preventDefault();practiceRowOpen('{url}')}}"'''


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
    palette=("#fb4c67","#f59e0b","#22c55e","#14b8a6","#3b82f6","#8b5cf6","#ec4899")
    bars=''.join(f'<rect x="{x-12:.1f}" y="{y:.1f}" width="24" height="{top+plot_h-y:.1f}" rx="7" fill="{palette[i%len(palette)]}" opacity=".72"><title>{esc(labels[i])}: {money_it(values[i])}</title></rect>' for i,(x,y) in enumerate(points))
    dots=''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" style="fill:{palette[i%len(palette)]}"><title>{esc(labels[i])}: {money_it(values[i])}</title></circle>' for i,(x,y) in enumerate(points))
    return f'''<svg class="income-chart" viewBox="0 0 {width} {height}" role="img" aria-label="Entrate giornaliere nel periodo selezionato"><defs><linearGradient id="incomeArea" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fb4c67" stop-opacity=".38"/><stop offset=".5" stop-color="#3b82f6" stop-opacity=".20"/><stop offset="1" stop-color="#8b5cf6" stop-opacity="0"/></linearGradient></defs><g class="chart-grid">{''.join(grid)}</g><path class="chart-area" d="{area}"/>{bars}<polyline class="chart-line" points="{line}"/>{dots}<g class="chart-dates">{dates}</g></svg>'''


def collapse_advanced_search(body):
    pattern=re.compile(r'(<form\b(?=[^>]*\bmethod=["\']get["\'])[^>]*>.*?</form>)',re.IGNORECASE|re.DOTALL)
    def wrap(match):
        form=match.group(1)
        controls=len(re.findall(r'<(?:input|select|textarea)\b',form,re.IGNORECASE))
        if controls<2 or "advanced-search-form" in form:return form
        form=form.replace('class="','class="advanced-search-form ',1) if 'class="' in form.split('>',1)[0] else form.replace('<form','<form class="advanced-search-form"',1)
        return f'''<details class="advanced-search"><summary>Ricerca avanzata</summary>{form}</details>'''
    return pattern.sub(wrap,body)


SIDEBAR_LINKS=[
    ("/","home","Dashboard"),("/calendario","calendar","Calendario"),("/programma-cremazioni","paw","Programma Cremazioni"),("/notifiche","bell","Notifiche"),("/pratiche","archive","Archivio"),
    ("/catalogo-urne","archive","Catalogo Urne"),("/bilanci","chart","Report"),("/conversazioni-whatsapp","message","Conversazioni WhatsApp"),("/veterinari","stethoscope","Veterinari"),
    ("/prodotti","clipboard","Prodotti"),("/ordini","receipt","Ordini"),
    ("/archivio/pratiche","clipboard","Gestionale"),("/archivio/clienti","users","Clienti"),
    ("/archivio/pratiche","paw","Animali"),
    ("/archivio/pratiche?pagamento=Da%20saldare","wallet","Pagamenti"),("/fatture","receipt","Fatture"),
    ("/impostazioni","settings","Impostazioni"),("/il-mio-profilo","user","Il mio profilo"),("mailto:assistenza@petparadise.it","help","Assistenza"),
]
DASHBOARD_SECTION_LABELS=[
    ("practices","Pratiche / Ritiri"),("payments","Pagamenti"),("income_chart","Entrate settimana in corso"),("recent_practices","Ultime 10 pratiche"),
]
BOTTOM_NAV_DEFAULT_SLOTS=["Dashboard","Calendario","Archivio"]


def layout(title, body, user=None):
    body=body.replace("<th>Veterinario</th><th>Sede</th>","<th>Veterinario</th><th>Provenienza</th><th>Sede</th>")
    body=collapse_advanced_search(body)
    nav = ""; app_header=""; mobile_nav=""; body_class=""; body_attrs=""
    if user:
        with db() as conn:
            unread=conn.execute("SELECT count(*) n FROM notifications WHERE user_id=? AND is_read=0",(user["id"],)).fetchone()["n"]
        unread_badge=f'<span class="notification-badge">{unread if unread < 100 else "99+"}</span>' if unread else ''
        prefs=load_preferences(user["id"])
        if prefs.get("theme")=="light": body_class=" light-theme"
        body_attrs=f' data-has-session="1"{" data-server-theme=\"1\"" if "theme" in prefs else ""}'
        links=list(SIDEBAR_LINKS)
        sidebar_order=parse_preference_list(prefs.get("sidebar_order",""))
        if sidebar_order:
            links=reorder_by_saved(links,sidebar_order,lambda item:item[2])
        nav_links=''.join(f'<a href="{href}" class="{"nav-notification" if href=="/notifiche" else ""}">{lucide(icon)}<span>{label}</span>{unread_badge if href=="/notifiche" else ""}</a>' for href,icon,label in links)
        nav=f'''<nav class="nav" aria-label="Menu principale">{nav_links}<button class="btn ghost install-btn" type="button" onclick="installPetParadise()">{lucide("plus")}<span>Installa App</span></button><a class="logout" href="/logout">{lucide("menu")}<span>Esci</span></a></nav>'''
        today=datetime.now(); date_label=today.strftime("%d/%m/%Y"); weekday=["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"][today.weekday()]
        app_header=f'''<header class="app-header"><div class="header-actions"><form class="header-search lookup" action="/archivio/pratiche" method="get" role="search">{lucide("search")}<label class="sr-only" for="globalSearch">Ricerca rapida per animale o proprietario</label><input id="globalSearch" name="rapida" placeholder="Animale o proprietario..." autocomplete="off"><div id="globalSearchResults" class="lookup-results hidden"></div></form><a class="icon-btn nav-notification" href="/notifiche" aria-label="Notifiche, {unread} non lette">{lucide("bell")}{unread_badge}</a><button class="icon-btn" type="button" onclick="toggleTheme()" aria-label="Cambia tema">{lucide("sun")}</button><button class="btn header-new" type="button" onclick="toggleCreateMenu()" aria-label="Crea pratica o evento">{lucide("plus")}<span>Crea</span></button><time datetime="{today.date().isoformat()}">{date_label}<small>{weekday}</small></time></div></header>'''
        drawer_links=''.join(f'<a href="{href}" class="{"nav-notification" if href=="/notifiche" else ""}">{lucide(icon)}<span>{label}</span>{unread_badge if href=="/notifiche" else ""}</a>' for href,icon,label in links)
        bottom_default=BOTTOM_NAV_DEFAULT_SLOTS
        bottom_pool={label:(href,icon,label) for href,icon,label in links}
        bottom_slots=[label for label in parse_preference_list(prefs.get("bottom_nav_slots","")) if label in bottom_pool][:3]
        for label in bottom_default:
            if len(bottom_slots)>=3:break
            if label not in bottom_slots and label in bottom_pool:bottom_slots.append(label)
        slot1,slot2,slot3=(bottom_pool.get(label,("/","home",label)) for label in (bottom_slots+bottom_default)[:3])
        mobile_nav=f'''<nav class="bottom-nav" aria-label="Navigazione mobile"><a href="{slot1[0]}">{lucide(slot1[1])}<span>{slot1[2]}</span></a><a href="{slot2[0]}">{lucide(slot2[1])}<span>{slot2[2]}</span></a><button class="bottom-new" type="button" onclick="toggleCreateMenu()" aria-label="Crea">{lucide("plus")}</button><a href="{slot3[0]}">{lucide(slot3[1])}<span>{slot3[2]}</span></a><button type="button" onclick="toggleMoreMenu()">{lucide("menu")}<span>Altro</span></button></nav><div class="create-sheet-backdrop" onclick="toggleCreateMenu(false)"></div><aside class="create-sheet" aria-label="Crea"><a href="/nuova">{lucide("plus")}<span>Nuova pratica</span></a><a href="/calendario/nuovo" data-calendar-new-event>{lucide("calendar")}<span>Nuovo evento</span></a></aside><div class="more-backdrop" onclick="toggleMoreMenu(false)"></div><aside class="more-menu" aria-label="Altre funzioni"><div class="more-title"><b>Menu</b><button class="icon-btn" onclick="toggleMoreMenu(false)" aria-label="Chiudi">×</button></div>{drawer_links}<button class="btn ghost install-btn" type="button" onclick="installPetParadise()">Installa App</button></aside>'''
    vapid_public=esc(os.environ.get("VAPID_PUBLIC_KEY",""))
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#e9475b"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="PP Manager"><meta name="application-name" content="Pet Paradise Manager"><meta name="format-detection" content="telephone=no"><link rel="manifest" href="/manifest.json"><link rel="apple-touch-icon" href="/assets/apple-touch-icon.png"><link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png"><title>{esc(title)} - Pet Paradise Manager</title><style>{CSS}</style></head><body class="{body_class.strip()}"{body_attrs} data-vapid-public-key="{vapid_public}"><a class="skip-link" href="#main-content">Vai al contenuto</a><aside class="top"><a class="brand" href="/"><img class="brand-logo brand-logo-dark" src="/assets/company_logo.png" alt="Pet Paradise"><img class="brand-logo brand-logo-light" src="/assets/company_logo_light.png" alt="Pet Paradise"><span class="brand-copy">Pet Paradise <small>MANAGER</small></span></a>{nav}</aside>{app_header}<div id="main-content">{body}</div>{mobile_nav}{APP_JS}</body></html>'''


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

    def service_worker(self):
        script=(ASSETS / "sw.js").read_text(encoding="utf-8").replace("__SW_VERSION__",APP_VERSION)
        data=script.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type","application/javascript; charset=utf-8")
        self.send_header("Cache-Control","no-cache"); self.send_header("Content-Length",str(len(data)))
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
        if path == "/sw.js": return self.service_worker()
        static_assets={
            "/assets/company_logo.png":"company_logo.png",
            "/assets/company_logo_light.png":"company_logo_light.png",
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
        if user["must_change_password"] and path != "/imposta-password" and not path.startswith("/api/"):
            return self.redirect("/imposta-password")
        if path == "/imposta-password": return self.change_password_page(user)
        match = re.fullmatch(r"/uploads/urns/([A-Za-z0-9_.-]+)", path)
        if match:
            image=(DATA / "urn_images" / match.group(1)).resolve()
            root=(DATA / "urn_images").resolve()
            if not str(image).startswith(str(root)) or not image.exists(): return self.send_error(404)
            content_type={".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp"}.get(image.suffix.lower(),"application/octet-stream")
            return self.send_static(image,content_type)
        if path == "/": return self.dashboard(user)
        if path == "/calendario": return self.calendar_page(user)
        if path == "/calendario/nuovo": return self.calendar_event_form(user)
        if path == "/calendario/impostazioni": return self.calendar_settings(user)
        if path == "/calendario/cestino": return self.calendar_trash(user)
        match = re.fullmatch(r"/calendario/(\d+)/modifica",path)
        if match: return self.calendar_event_form(user,int(match.group(1)))
        match = re.fullmatch(r"/calendario/(\d+)",path)
        if match: return self.calendar_event_detail(user,int(match.group(1)))
        if path == "/bilanci": return self.balances_v2(user)
        if path == "/programma-cremazioni": return self.cremation_schedule(user)
        match = re.fullmatch(r"/pagamenti/(da-saldare|acconti|pagati)", path)
        if match: return self.payment_overview(user,match.group(1))
        if path == "/conversazioni-whatsapp": return self.whatsapp_conversations(user)
        if path == "/notifiche": return self.notifications(user)
        if path in ("/prodotti","/articoli"): return self.articles_page(user)
        if path == "/ordini": return self.orders_page(user)
        if path == "/ordini/storico": return self.orders_history_page(user)
        if path == "/ordini/impostazioni": return self.order_settings_page(user)
        match = re.fullmatch(r"/ordini/(\d+)", path)
        if match: return self.order_detail_page(user,int(match.group(1)))
        if path == "/fatture": return self.invoices_page(user)
        if path == "/catalogo-urne": return self.urn_catalog_page(user)
        if path == "/catalogo-urne/nuova": return self.urn_edit_page(user)
        if path in ("/diagnostica","/impostazioni"): return self.settings_page(user)
        if path == "/il-mio-profilo": return self.profile_page(user)
        if path == "/whatsapp-diagnostica": return self.whatsapp_diagnostics(user)
        if path == "/api/clienti/search": return self.api_clients_search(user)
        if path == "/api/cap": return self.api_zip_lookup(user)
        if path == "/api/veterinari/search": return self.api_veterinarians_search(user)
        if path == "/api/calendario/animali/search": return self.api_calendar_animals_search(user)
        if path == "/api/calendario/pratiche/search": return self.api_calendar_practices_search(user)
        if path == "/api/notifiche/stato": return self.notification_status(user)
        match = re.fullmatch(r"/api/veterinari/(\d+)/buoni", path)
        if match: return self.api_veterinarian_vouchers(user, int(match.group(1)))
        if path == "/nuova": return self.new_page(user)
        if path == "/pratiche": return self.archive_home(user)
        if path == "/archivio/pratiche": return self.archive(user)
        if path == "/archivio/clienti": return self.clients_archive(user)
        if path == "/cestino": return self.trash_page(user)
        if path == "/database-mesi": return self.redirect("/pratiche")
        if path == "/veterinari": return self.veterinarians_page(user)
        match = re.fullmatch(r"/catalogo-urne/(\d+)/modifica", path)
        if match: return self.urn_edit_page(user, int(match.group(1)))
        match = re.fullmatch(r"/catalogo-urne/(\d+)", path)
        if match: return self.urn_detail_page(user, int(match.group(1)))
        match = re.fullmatch(r"/veterinari/(\d+)", path)
        if match: return self.veterinarian_detail(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)", path)
        if match: return self.practice(user, int(match.group(1)))
        match = re.fullmatch(r"/notifiche/(\d+)/apri", path)
        if match: return self.open_notification(user, int(match.group(1)))
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
        if user["must_change_password"] and path != "/imposta-password" and not path.startswith("/api/"):
            return self.redirect("/imposta-password")
        if path == "/imposta-password": return self.change_password_submit(user)
        if path == "/nuova": return self.create_practice(user)
        if path == "/calendario/nuovo": return self.save_calendar_event(user)
        match = re.fullmatch(r"/calendario/(\d+)/modifica",path)
        if match: return self.save_calendar_event(user,int(match.group(1)))
        match = re.fullmatch(r"/calendario/(\d+)/(stato|commento|elimina|ripristina|elimina-definitiva|collega-pratica|scollega-pratica)",path)
        if match: return self.calendar_event_action(user,int(match.group(1)),match.group(2))
        match = re.fullmatch(r"/calendario/(\d+)/commenti/(\d+)/(modifica|elimina)",path)
        if match: return self.calendar_comment_action(user,int(match.group(1)),int(match.group(2)),match.group(3))
        if path == "/api/push/subscribe": return self.push_subscribe(user)
        if path == "/api/push/unsubscribe": return self.push_unsubscribe(user)
        if path == "/api/push/test": return self.push_test(user)
        if path == "/impostazioni/notifiche": return self.save_notification_preferences(user)
        if path == "/il-mio-profilo/salva": return self.save_preferences(user)
        if path in ("/impostazioni/ordini","/ordini/impostazioni"): return self.save_order_settings(user)
        if path == "/ordini/invia": return self.send_water_order(user)
        match = re.fullmatch(r"/ordini/(\d+)/(reinvia|duplica|archivia)",path)
        if match: return self.order_action(user,int(match.group(1)),match.group(2))
        if path == "/notifiche/segna-tutte-lette": return self.mark_all_notifications_read(user)
        match = re.fullmatch(r"/(?:prodotti|articoli)/(\d+)/ordina", path)
        if match: return self.order_article(user, int(match.group(1)))
        if path == "/catalogo-urne/nuova": return self.save_urn(user)
        match = re.fullmatch(r"/catalogo-urne/(\d+)/modifica", path)
        if match: return self.save_urn(user, int(match.group(1)))
        match = re.fullmatch(r"/catalogo-urne/(\d+)/elimina", path)
        if match: return self.delete_urn(user, int(match.group(1)))
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
        match = re.fullmatch(r"/pratiche/(\d+)/fattura", path)
        if match: return self.save_invoice(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/pagamento-rapido", path)
        if match: return self.quick_payment(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/stato-rapido", path)
        if match: return self.quick_state(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/cremazione-inserita", path)
        if match: return self.toggle_cremation_registered(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/catalogo-inviato", path)
        if match: return self.catalog_sent(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/whatsapp", path)
        if match: return self.resend_whatsapp(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/whatsapp-annulla", path)
        if match: return self.cancel_whatsapp_manual(user, int(match.group(1)))
        match = re.fullmatch(r"/api/pratiche/(\d+)/autosave", path)
        if match: return self.practice_autosave(user, int(match.group(1)))
        match = re.fullmatch(r"/whatsapp-messaggi/(\d+)/(riprova|annulla)", path)
        if match: return self.whatsapp_message_action(user, int(match.group(1)), match.group(2))
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

    def change_password_page(self, user, error="", return_to=None):
        forced=bool(user["must_change_password"])
        if return_to is None:
            q=parse_qs(urlparse(getattr(self,"path","")).query)
            return_to=(q.get("return_to") or ["/"])[0]
        return_to=safe_return_path(return_to)
        intro=(
            "Prima di continuare devi impostare una nuova password personale. Questa schermata non può essere saltata."
            if forced else
            "Cambia la tua password personale in qualsiasi momento."
        )
        body=f'''<main class="wrap"><section class="section" style="max-width:420px;margin:0 auto"><h1>{"Imposta la tua nuova password" if forced else "Cambia password"}</h1><p class="sub">{esc(intro)}</p>{f'<div class="flash warning">{esc(error)}</div>' if error else ''}<form method="post"><input type="hidden" name="return_to" value="{esc(return_to)}">{'' if forced else '<div class="field"><label>Password attuale</label><input type="password" name="current_password" required autofocus></div>'}<div class="field" style="margin-top:12px"><label>Nuova password</label><input type="password" name="new_password" minlength="8" required{' autofocus' if forced else ''}></div><div class="field" style="margin-top:12px"><label>Conferma nuova password</label><input type="password" name="confirm_password" minlength="8" required></div><button class="btn" style="width:100%;margin-top:20px">Salva nuova password</button></form></section></main>'''
        self.send_html(layout("Cambia password", body, user))

    def change_password_submit(self, user):
        f=self.form()
        return_to=safe_return_path(f.get("return_to") or "/")
        forced=bool(user["must_change_password"])
        current=f.get("current_password","")
        new=f.get("new_password","")
        confirm=f.get("confirm_password","")
        if not forced and not password_ok(current, user["password_hash"]):
            return self.change_password_page(user,"Password attuale non corretta.",return_to)
        if len(new)<8:
            return self.change_password_page(user,"La nuova password deve avere almeno 8 caratteri.",return_to)
        if new!=confirm:
            return self.change_password_page(user,"Le due password inserite non coincidono.",return_to)
        with db() as c:
            c.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?",(password_hash(new),user["id"]))
        return self.redirect(return_to)

    def dashboard_legacy(self,user):
        today=datetime.now().date()
        week_start=today-timedelta(days=(today.weekday()-5)%7)
        days=[week_start+timedelta(days=offset) for offset in range(7)]
        week_end=days[-1]
        with db() as c:
            active_where="deleted_at IS NULL OR deleted_at=''"
            counts={r["status"]:r["n"] for r in c.execute(f"SELECT status,count(*) n FROM practices WHERE {active_where} GROUP BY status")}
            payment_counts={r["payment_status"]:r["n"] for r in c.execute(f"SELECT COALESCE(payment_status,'Da saldare') payment_status,count(*) n FROM practices WHERE {active_where} GROUP BY COALESCE(payment_status,'Da saldare')")}
            payment_rows=c.execute(f"SELECT *,COALESCE(payment_status,'Da saldare') normalized_payment_status FROM practices WHERE {active_where}").fetchall()
            income_rows=c.execute(f"""SELECT date(m.paid_at) day,COALESCE(sum(m.amount),0) amount
                                      FROM payment_movements m JOIN practices p ON p.id=m.practice_id
                                      WHERE (p.deleted_at IS NULL OR p.deleted_at='') AND date(m.paid_at) BETWEEN date(?) AND date(?)
                                      GROUP BY date(m.paid_at)""",(week_start.isoformat(),week_end.isoformat())).fetchall()
            recent=c.execute(f"SELECT * FROM practices WHERE {active_where} ORDER BY date(COALESCE(NULLIF(pickup_date,''),created_at)) DESC,id DESC LIMIT 10").fetchall()
            activity=c.execute("""SELECT h.event_type,h.new_value,h.created_at,p.id practice_id,p.practice_number
                                  FROM practice_history h JOIN practices p ON p.id=h.practice_id
                                  WHERE p.deleted_at IS NULL OR p.deleted_at=''
                                  ORDER BY h.created_at DESC,h.id DESC LIMIT 6""").fetchall()
            incomplete=c.execute(f"SELECT count(*) n FROM practices WHERE ({active_where}) AND data_complete=0 AND status!='Consegnata'").fetchone()["n"]
            notification_recent=c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 5",(user["id"],)).fetchall()
            notification_unread=c.execute("SELECT count(*) n FROM notifications WHERE user_id=? AND is_read=0",(user["id"],)).fetchone()["n"]
        state_cards=[]
        state_specs=[("Ritirato","Ritirati","archive","state-yellow"),("In programma","In programma","calendar","state-red"),("Cremato","Cremati","archive","state-blue"),("Da consegnare","Da consegnare","clipboard","state-purple"),("Consegnato","Consegnati","home","state-green")]
        for state,label,icon,cls in state_specs:
            state_cards.append(f'<a class="metric-card {cls}" href="/archivio/pratiche?stato={quote(state)}"><span class="metric-copy"><small>{label}</small><strong>{counts.get(state,0)}</strong><em>{"Nessuna pratica" if not counts.get(state,0) else "Apri elenco"}</em></span><span class="metric-icon">{lucide(icon)}</span></a>')
        payment_totals={
            "Da saldare":sum(outstanding_amount(row) for row in payment_rows if outstanding_amount(row)>0),
            "Acconto":sum(money_value(row["deposit"]) for row in payment_rows),
            "Pagato":sum(received_amount(row) for row in payment_rows if row["normalized_payment_status"]=="Pagato"),
        }
        payment_counts["Acconto"]=sum(1 for row in payment_rows if money_value(row["deposit"])>0)
        payment_specs=[("Da saldare","Da saldare","wallet","payment-due","/pagamenti/da-saldare"),("Acconto","Acconti","receipt","payment-deposit","/pagamenti/acconti"),("Pagato","Pagati","chart","payment-paid","/pagamenti/pagati")]
        payment_cards=''.join(f'<a class="payment-card {cls}" href="{href}"><span><small>{label}</small><strong>{payment_counts.get(state,0)}</strong><em>{money_it(payment_totals[state])}</em></span><span class="metric-icon">{lucide(icon)}</span></a>' for state,label,icon,cls,href in payment_specs)
        income_by_day={day.isoformat():0.0 for day in days}
        for row in income_rows:
            if row["day"] in income_by_day: income_by_day[row["day"]]+=money_value(row["amount"])
        income_values=[income_by_day[day.isoformat()] for day in days]; income_total=sum(income_values)
        chart=income_chart(income_values,[day.strftime("%d/%m") for day in days])
        timeline=[]
        for index,event in enumerate(activity):
            label=event["event_type"] or "Aggiornamento pratica"; detail=event["new_value"] or event["practice_number"] or ""
            when=(event["created_at"] or "").replace("T"," ")[:16]
            timeline.append(f'<a class="activity-item activity-{index%4}" href="/pratiche/{event["practice_id"]}"><span class="activity-icon">{lucide("clipboard")}</span><span><b>{esc(label)}</b><small>{esc(detail)}</small></span><time>{esc(when)}</time></a>')
        if not timeline: timeline.append('<div class="activity-empty">Le nuove attività compariranno qui.</div>')
        notification_items=''.join(f'''<a class="activity-item" href="/notifiche/{item['id']}/apri"><span class="activity-icon">{NOTIFICATION_TYPES.get(item['type'],('', '🔔'))[1]}</span><span><b>{esc(item['title'])}</b><small>{esc(item['text'])}</small></span><time>{esc((item['created_at'] or '')[11:16])}</time></a>''' for item in notification_recent) or '<div class="activity-empty">Nessuna notifica.</div>'
        notification_panel=f'''<article class="dashboard-panel activity-panel" style="margin-top:16px"><header><div><h2>Centro notifiche</h2><p><strong>{notification_unread}</strong> non lette</p></div><a href="/notifiche">Visualizza tutte</a></header><div class="activity-list">{notification_items}</div></article>'''
        hour=datetime.now().hour; greeting="Buongiorno" if hour < 13 else "Buon pomeriggio" if hour < 18 else "Buonasera"
        body=f'''<main class="wrap dashboard-wrap"><section class="welcome"><div><h1>{greeting}, Pet Paradise <span aria-hidden="true">👋</span></h1><p>Panoramica aggiornata dell'attività</p></div></section>{f'<div class="flash warning">{incomplete} pratiche hanno dati ancora da completare.</div>' if incomplete else ''}<h2 class="dashboard-heading">Pratiche</h2><section class="dashboard-states">{''.join(state_cards)}</section><h2 class="dashboard-heading">Pagamenti</h2><section class="dashboard-payments">{payment_cards}</section><section class="dashboard-lower"><a class="dashboard-panel income-panel" href="/bilanci" aria-label="Apri Bilanci: entrate degli ultimi sette giorni"><header><div><h2>Entrate ultimi 7 giorni</h2><p>Totale: <strong>{money_it(income_total)}</strong></p></div><span class="panel-link">Apri Bilanci →</span></header>{chart}</a><article class="dashboard-panel activity-panel"><header><h2>Attività recenti</h2><a href="/archivio/pratiche">Vedi tutte</a></header><div class="activity-list">{''.join(timeline)}</div></article></section>{notification_panel}<section class="dashboard-recent"><div class="titlebar"><h2>Ultime 10 pratiche per data recupero</h2><a href="/archivio/pratiche">Apri archivio</a></div><div class="tablebox dashboard-table-scroll"><table class="practice-list-table"><thead><tr><th>Animale</th><th>Età</th><th>Proprietario</th><th>Data recupero</th><th>Codice pratica</th><th>Veterinario</th><th>Sede</th><th>Etichetta</th><th>Note</th><th>Urna</th><th>Totale pagato</th><th>Fattura</th><th>Totale W</th><th>TOTALE D</th><th>Acconto</th><th>Rimanenza</th><th>Stati</th></tr></thead><tbody>{self.practice_rows(recent,True)}</tbody></table></div></section></main>'''
        week_range=f"{week_start.strftime('%d/%m/%Y')} - {week_end.strftime('%d/%m/%Y')}"
        body=body.replace("Entrate ultimi 7 giorni", "Entrate settimana in corso")
        body=body.replace("Totale: <strong>", f"{week_range} · Totale: <strong>", 1)
        body=body.replace('href="/bilanci" aria-label="Apri Bilanci: entrate degli ultimi sette giorni"', f'href="/bilanci?dal={week_start.isoformat()}&al={week_end.isoformat()}" aria-label="Apri Bilanci: entrate della settimana in corso"')
        dashboard_actions='''<div class="calendar-quick-actions"><a class="btn" href="/nuova">+ Nuova pratica</a><a class="btn ghost" href="/calendario/nuovo">+ Nuovo evento</a></div>'''
        body=body.replace("</section>",dashboard_actions+"</section>",1)
        self.send_html(layout("Dashboard",body,user))

    def dashboard(self,user):
        q=parse_qs(urlparse(getattr(self,"path","/")).query);today=datetime.now().date()
        practice_period,practice_from,practice_to=dashboard_period_bounds((q.get("pratiche_periodo") or ["oggi"])[0],today)
        payment_period,payment_from,payment_to=dashboard_period_bounds((q.get("pagamenti_periodo") or ["oggi"])[0],today)
        _,week_start,week_end=dashboard_period_bounds("settimana",today);days=[week_start+timedelta(days=offset) for offset in range(7)]
        active="p.deleted_at IS NULL OR p.deleted_at=''"
        ritiro_date=dashboard_practice_date_sql("ritirati","p");programma_date=dashboard_practice_date_sql("in_programma","p");consegna_date=dashboard_practice_date_sql("consegnati","p")
        with db() as c:
            counts={
                "Ritirato":c.execute(f"SELECT count(*) n FROM practices p WHERE ({active}) AND p.status IN ('Ritirato','Cremato','Da consegnare','Consegnato','Smaltito') AND {ritiro_date} BETWEEN date(?) AND date(?)",(practice_from.isoformat(),practice_to.isoformat())).fetchone()["n"],
                "In programma":c.execute(f"SELECT count(*) n FROM practices p WHERE ({active}) AND p.status='In programma' AND ((p.pickup_date IS NULL OR p.pickup_date='') OR {programma_date} BETWEEN date(?) AND date(?))",(practice_from.isoformat(),practice_to.isoformat())).fetchone()["n"],
                "Da consegnare":c.execute(f"SELECT count(*) n FROM practices p WHERE ({active}) AND p.status='Da consegnare'").fetchone()["n"],
                "Consegnato":c.execute(f"SELECT count(*) n FROM practices p WHERE ({active}) AND p.status='Consegnato' AND {consegna_date} BETWEEN date(?) AND date(?)",(practice_from.isoformat(),practice_to.isoformat())).fetchone()["n"],
            }
            open_rows=c.execute(f"SELECT p.* FROM practices p WHERE ({active}) AND COALESCE(p.payment_status,'Da saldare')!='Pagato'").fetchall()
            movement_stats={row["category"]:row for row in c.execute(f"""SELECT CASE WHEN m.payment_type LIKE 'acconto_%' THEN 'Acconto' ELSE 'Pagato' END category,
                                         count(DISTINCT m.practice_id) practice_count,COALESCE(sum(m.amount),0) amount
                                         FROM payment_movements m JOIN practices p ON p.id=m.practice_id
                                         WHERE ({active}) AND m.amount>0 AND date(m.paid_at) BETWEEN date(?) AND date(?)
                                         AND m.payment_type IN ('acconto_ordinario','acconto_d','saldo_ordinario','saldo_d')
                                         GROUP BY category""",(payment_from.isoformat(),payment_to.isoformat())).fetchall()}
            income_rows=c.execute(f"""SELECT date(m.paid_at) day,COALESCE(sum(m.amount),0) amount
                                      FROM payment_movements m JOIN practices p ON p.id=m.practice_id
                                      WHERE ({active}) AND date(m.paid_at) BETWEEN date(?) AND date(?)
                                      GROUP BY date(m.paid_at)""",(week_start.isoformat(),week_end.isoformat())).fetchall()
            recent=c.execute("SELECT * FROM practices WHERE deleted_at IS NULL OR deleted_at='' ORDER BY date(COALESCE(NULLIF(pickup_date,''),created_at)) DESC,id DESC LIMIT 10").fetchall()
            incomplete=c.execute("SELECT count(*) n FROM practices WHERE (deleted_at IS NULL OR deleted_at='') AND data_complete=0 AND status!='Consegnato'").fetchone()["n"]
        def state_url(event,state="",include_dates=True):
            params={"dashboard_event":event,"periodo":practice_period}
            if state:params["stato"]=state
            if include_dates:params.update({"dal":practice_from.isoformat(),"al":practice_to.isoformat()})
            return "/archivio/pratiche?"+urlencode(params)
        state_specs=[
            ("Ritirato","Ritirati","archive","state-yellow",state_url("ritirati")),
            ("In programma","In programma","calendar","state-red",state_url("in_programma","In programma")),
            ("Da consegnare","Da consegnare","clipboard","state-purple",state_url("da_consegnare","Da consegnare",False)),
            ("Consegnato","Consegnati","home","state-green",state_url("consegnati","Consegnato")),
        ]
        state_cards=''.join(f'<a class="metric-card {cls}" data-dashboard-card="{state}" data-count="{counts[state]}" href="{href}"><span class="metric-copy"><small>{label}</small><strong>{counts[state]}</strong><em>{"Aperti, senza scadenza" if state=="Da consegnare" else "Apri elenco"}</em></span><span class="metric-icon">{lucide(icon)}</span></a>' for state,label,icon,cls,href in state_specs)
        open_due=[row for row in open_rows if outstanding_amount(row)>0]
        def movement_value(category,key):
            row=movement_stats.get(category)
            return row[key] if row else 0
        payment_counts={"Da saldare":len(open_due),"Acconto":movement_value("Acconto","practice_count"),"Pagato":movement_value("Pagato","practice_count")}
        payment_totals={"Da saldare":sum(outstanding_amount(row) for row in open_due),"Acconto":money_value(movement_value("Acconto","amount")),"Pagato":money_value(movement_value("Pagato","amount"))}
        payment_query=urlencode({"dal":payment_from.isoformat(),"al":payment_to.isoformat(),"periodo":payment_period})
        payment_specs=[("Da saldare","Da saldare","wallet","payment-due",f"/pagamenti/da-saldare?{payment_query}"),("Acconto","Acconti","receipt","payment-deposit",f"/pagamenti/acconti?{payment_query}"),("Pagato","Pagati","chart","payment-paid",f"/pagamenti/pagati?{payment_query}")]
        payment_cards=''.join(f'<a class="payment-card {cls}" data-dashboard-payment="{state}" data-count="{payment_counts[state]}" data-amount="{payment_totals[state]:.2f}" href="{href}"><span><small>{label}</small><strong>{payment_counts[state]}</strong><em>{money_it(payment_totals[state])}</em>{"<small>Tutte le rimanenze aperte</small>" if state=="Da saldare" else ""}</span><span class="metric-icon">{lucide(icon)}</span></a>' for state,label,icon,cls,href in payment_specs)
        income_by_day={day.isoformat():0.0 for day in days}
        for row in income_rows:
            if row["day"] in income_by_day:income_by_day[row["day"]]+=money_value(row["amount"])
        income_values=[income_by_day[day.isoformat()] for day in days];income_total=sum(income_values);chart=income_chart(income_values,[day.strftime("%d/%m") for day in days])
        def selector(key,current,other_key,other):
            links=[]
            for value,label in (("oggi","Oggi"),("settimana","Settimana"),("mese","Mese")):
                links.append(f'<a data-dashboard-period="{key}" data-period-value="{value}" class="{"active" if current==value else ""}" href="/?{urlencode({key:value,other_key:other})}">{label}</a>')
            return '<nav class="period-selector" aria-label="Seleziona periodo">'+''.join(links)+'</nav>'
        practice_selector=selector("pratiche_periodo",practice_period,"pagamenti_periodo",payment_period);payment_selector=selector("pagamenti_periodo",payment_period,"pratiche_periodo",practice_period)
        persistence_script='''<script>(function(){const allowed=['oggi','settimana','mese'];const url=new URL(location.href);let changed=false;['pratiche_periodo','pagamenti_periodo'].forEach(key=>{const saved=localStorage.getItem('ppm_'+key);if(!url.searchParams.has(key)&&allowed.includes(saved)&&saved!=='oggi'){url.searchParams.set(key,saved);changed=true;}});if(changed){location.replace(url);return;}document.querySelectorAll('[data-dashboard-period]').forEach(link=>link.addEventListener('click',()=>localStorage.setItem('ppm_'+link.dataset.dashboardPeriod,link.dataset.periodValue)));})();</script>'''
        hour=datetime.now().hour;greeting="Buongiorno" if hour<13 else "Buon pomeriggio" if hour<18 else "Buonasera"
        dashboard_sections={
            "practices": f'''<div class="dashboard-section-head"><h2 class="dashboard-heading">Pratiche / Ritiri</h2>{practice_selector}</div><section class="dashboard-states">{state_cards}</section>''',
            "payments": f'''<div class="dashboard-section-head"><h2 class="dashboard-heading">Pagamenti</h2>{payment_selector}</div><section class="dashboard-payments">{payment_cards}</section>''',
            "income_chart": f'''<section class="dashboard-chart-only"><a class="dashboard-panel income-panel" href="/bilanci?dal={week_start.isoformat()}&al={week_end.isoformat()}" aria-label="Apri Bilanci: entrate della settimana in corso"><header><div><h2>Entrate settimana in corso</h2><p>{week_start.strftime('%d/%m/%Y')} - {week_end.strftime('%d/%m/%Y')} · Totale: <strong>{money_it(income_total)}</strong></p></div><span class="panel-link">Apri Bilanci →</span></header>{chart}</a></section>''',
            "recent_practices": f'''<section class="dashboard-recent"><div class="titlebar"><h2>Ultime 10 pratiche per data recupero</h2><a href="/archivio/pratiche">Apri archivio</a></div><div class="tablebox dashboard-table-scroll"><table class="practice-list-table"><thead><tr><th>Animale</th><th>Età</th><th>Proprietario</th><th>Data recupero</th><th>Codice pratica</th><th>Veterinario</th><th>Sede</th><th>Etichetta</th><th>Note</th><th>Urna</th><th>Totale pagato</th><th>Fattura</th><th>Totale W</th><th>TOTALE D</th><th>Acconto</th><th>Rimanenza</th><th>Stati</th></tr></thead><tbody>{self.practice_rows(recent,True)}</tbody></table></div></section>''',
        }
        default_dashboard_order=[sid for sid,_ in DASHBOARD_SECTION_LABELS]
        saved_dashboard_order=[sid for sid in parse_preference_list(load_preferences(user["id"]).get("dashboard_sections","")) if sid in dashboard_sections]
        dashboard_order=saved_dashboard_order or default_dashboard_order
        sections_html=''.join(dashboard_sections[sid] for sid in dashboard_order)
        body=f'''<main class="wrap dashboard-wrap"><section class="welcome"><div><h1>{greeting}, {esc(user['display_name'])} <span aria-hidden="true">👋</span></h1><p>Panoramica operativa del periodo selezionato</p></div></section>{f'<div class="flash warning">{incomplete} pratiche hanno dati ancora da completare.</div>' if incomplete else ''}{sections_html}{persistence_script}</main>'''
        self.send_html(layout("Dashboard",body,user))

    def calendar_event_client_name(self,row,client_names=None,practice_owner_names=None):
        client_names=client_names or {};practice_owner_names=practice_owner_names or {}
        if row["client_id"] and client_names.get(row["client_id"]):return client_names[row["client_id"]]
        if row["linked_practice_id"] and practice_owner_names.get(row["linked_practice_id"]):return practice_owner_names[row["linked_practice_id"]]
        manual=" ".join(x for x in (row["client_first_name"],row["client_last_name"]) if x).strip()
        if manual:return manual
        if row["person_company"]:return row["person_company"]
        return ""

    def calendar_event_card(self,row,compact=False,client_names=None,practice_owner_names=None):
        cls=event_color_class(row)
        if row["event_type"] in ("Ritiro","Ritiro in sede"):
            cls="calendar-green" if row["event_status"]=="Ritirato" else "calendar-dark" if row["event_status"]=="Annullato" else "calendar-red"
        start=(row["start_at"] or "");end=(row["end_at"] or "")
        time_text="Tutto il giorno" if row["all_day"] else start[11:16]
        if start[:10]!=end[:10]:time_text=f'{start[:10]} → {end[:10]}'
        details=[]
        client_display=self.calendar_event_client_name(row,client_names,practice_owner_names)
        if client_display:details.append(client_display)
        if row.get("animal_species"):details.append(str(row["animal_species"]).replace(","," / "))
        if row["animal_weight_total"]:details.append(f'{float(row["animal_weight_total"]):g} kg')
        if row["cremation_types"]:details.append(str(row["cremation_types"]).replace(","," / "))
        if row["zone"] and row["event_type"]!="Appuntamento":details.append(row["zone"])
        if row["delivery_clinic_name"]:details.append(row["delivery_clinic_name"])
        if row["payment_status"]:
            channel=f' {row.get("payment_channel")}' if row.get("payment_channel") else ''
            details.append(f'{row["payment_status"]}{channel} {money_it(row["payment_amount"])}')
        if row["estimate_total"]:details.append(f'Preventivo {money_it(row["estimate_total"])}')
        display_title=row["title"]
        if row["event_type"]=="Appuntamento":display_title=re.sub(r"^APPUNTAMENTO\b","PROMEMORIA",display_title,flags=re.I)
        if compact:return f'<a class="calendar-band {cls}" href="/calendario/{row["id"]}">{esc(display_title)}</a>'
        icon_name={"Ritiro":"paw","Ritiro in sede":"home","Riconsegna":"archive","Riconsegna in sede":"home","Appuntamento":"calendar"}.get(row["event_type"],"calendar")
        client_missing=f'<span class="calendar-client-missing" title="Cliente da completare">{lucide("user")}</span>' if row["event_type"] in ("Ritiro","Ritiro in sede") and not client_display else ''
        return f'''<a class="calendar-event {cls}" href="/calendario/{row['id']}"><time class="calendar-event-time">{esc(time_text)}</time><span class="calendar-event-main"><span class="calendar-event-icon">{lucide(icon_name)}</span><span class="calendar-event-copy"><h3>{esc(display_title)}{client_missing}</h3><p>{esc(' · '.join(details) or ('Promemoria' if row['event_type']=='Appuntamento' else row['event_type']))}</p><p>{esc(row['operator_name'] or row['assigned_name'] or row['creator_name'])}</p></span></span></a>'''

    def calendar_page(self,user):
        q=parse_qs(urlparse(self.path).query);selected=(q.get("data") or [datetime.now().date().isoformat()])[0]
        try:date.fromisoformat(selected)
        except ValueError:selected=datetime.now().date().isoformat()
        view=(q.get("vista") or ["giorno"])[0]
        if view not in ("giorno","settimana","mese","mista_settimana","mista_mese","compatto"):view="giorno"
        start,end=calendar_period_bounds(view,selected)
        filters={key:(q.get(key) or [""])[0] for key in ("q","event_type","event_status","operator_name","veterinarian_id","location_type","zone","venue_scope","date_from","date_to")}
        with db() as c:
            rows=overlap_rows(c,start.isoformat(),end.isoformat(),filters)
            event_ids=[row["id"] for row in rows]
            species_by_event={}
            if event_ids:
                placeholders=','.join('?' for _ in event_ids)
                for species_row in c.execute(f"SELECT event_id,group_concat(DISTINCT species) species FROM calendar_event_animals WHERE event_id IN ({placeholders}) AND trim(COALESCE(species,''))<>'' GROUP BY event_id",event_ids):
                    species_by_event[species_row["event_id"]]=species_row["species"] or ""
            linked_ids={int(row["linked_practice_id"]) for row in rows if row["linked_practice_id"]}
            payment_channels={};practice_owner_names={}
            if linked_ids:
                linked_marks=','.join('?' for _ in linked_ids)
                for practice_row in c.execute(f"SELECT id,total_text,owner_first_name,owner_last_name,owner_company FROM practices WHERE id IN ({linked_marks})",tuple(linked_ids)):
                    payment_channels[practice_row["id"]]=payment_channel(practice_row)
                    owner=" ".join(x for x in (practice_row["owner_first_name"],practice_row["owner_last_name"]) if x).strip() or practice_row["owner_company"] or ""
                    if owner:practice_owner_names[practice_row["id"]]=owner
            client_ids={int(row["client_id"]) for row in rows if row["client_id"]}
            client_names={}
            if client_ids:
                client_marks=','.join('?' for _ in client_ids)
                for client_row in c.execute(f"SELECT id,first_name,last_name,company_name FROM clients WHERE id IN ({client_marks})",tuple(client_ids)):
                    name=" ".join(x for x in (client_row["first_name"],client_row["last_name"]) if x).strip() or client_row["company_name"] or ""
                    if name:client_names[client_row["id"]]=name
            rows=[dict(row,animal_species=species_by_event.get(row["id"],""),payment_channel=payment_channels.get(row["linked_practice_id"],"")) for row in rows]
            vets=c.execute("SELECT id,COALESCE(short_name,clinic_name) name FROM veterinarians WHERE active=1 ORDER BY name").fetchall()
        by_day={}
        for row in rows:
            cursor=max(start,date.fromisoformat(row["start_at"][:10]));last=min(end,date.fromisoformat(row["end_at"][:10]))
            while cursor<=last:by_day.setdefault(cursor.isoformat(),[]).append(row);cursor+=timedelta(days=1)
        selected_date=date.fromisoformat(selected)
        month_names=("Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno","Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre")
        day_names=("Lun","Mar","Mer","Gio","Ven","Sab","Dom")
        day_names_full=("Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica")
        def italian_short_day(value):return f"{day_names[value.weekday()]} {value.day}"
        def italian_long_date(value):return f"{day_names_full[value.weekday()]} {value.day} {month_names[value.month-1]}"
        if view=="giorno":
            prev_target,next_target=selected_date-timedelta(days=1),selected_date+timedelta(days=1)
        elif view in ("settimana","mista_settimana"):
            prev_target,next_target=selected_date-timedelta(days=7),selected_date+timedelta(days=7)
        else:
            previous_month_last=selected_date.replace(day=1)-timedelta(days=1)
            next_month_first=(selected_date.replace(day=28)+timedelta(days=4)).replace(day=1)
            prev_target=previous_month_last.replace(day=min(selected_date.day,previous_month_last.day))
            next_month_last=(next_month_first.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
            next_target=next_month_first.replace(day=min(selected_date.day,next_month_last.day))
        def view_url(target,current_view=view):
            params={"vista":current_view,"data":target.isoformat(),**{k:v for k,v in filters.items() if v}}
            return "/calendario?"+urlencode(params)
        switch_active={"mista_settimana":"settimana","mista_mese":"mese","compatto":"mese"}.get(view,view)
        switch=''.join(f'<a data-calendar-view="{key}" class="{"active" if switch_active==key else ""}" href="{view_url(selected_date,key)}">{label}</a>' for key,label in (("giorno","Giorno"),("settimana","Settimana"),("mese","Mese")))
        selected_rows=by_day.get(selected,[])
        timeline_start,timeline_end,pixels_per_hour=8*60,22*60,58
        timeline_height=((timeline_end-timeline_start)//60)*pixels_per_hour
        timeline_lines=''.join(f'<div class="calendar-timeline-line" style="top:{(hour*60-timeline_start)/60*pixels_per_hour}px"><time>{hour:02d}:00</time><span></span></div>' for hour in range(8,23))
        def positioned_events(day_value,event_rows,class_name):
            day_key=day_value.isoformat() if isinstance(day_value,date) else str(day_value)
            placements=[];lane_ends=[]
            def clock_minutes(value,fallback):
                try:
                    hour,minute=(int(part) for part in value.split(":"));return hour*60+minute
                except (ValueError,AttributeError):return fallback
            for row in sorted(event_rows,key=lambda item:(item["start_at"] or "",item["id"])):
                raw_start=row["start_at"] or "";raw_end=row["end_at"] or raw_start
                starts_before=raw_start[:10] and raw_start[:10]<day_key;ends_after=raw_end[:10] and raw_end[:10]>day_key
                minutes=timeline_start if row["all_day"] or starts_before else clock_minutes(raw_start[11:16],timeline_start)
                natural_end=timeline_end if ends_after else clock_minutes(raw_end[11:16],minutes+60)
                minutes=max(timeline_start,min(timeline_end-45,minutes));display_end=max(minutes+45,min(timeline_end,natural_end))
                lane=next((index for index,end_minute in enumerate(lane_ends) if end_minute<=minutes),len(lane_ends))
                if lane==len(lane_ends):lane_ends.append(display_end)
                else:lane_ends[lane]=display_end
                placements.append((row,minutes,display_end,lane))
            cluster_lane_count=[1]*len(placements);cluster_start=0;cluster_end=None
            for index,(_,minutes,display_end,_) in enumerate(placements):
                if cluster_end is not None and minutes>=cluster_end:
                    count=max(placements[i][3] for i in range(cluster_start,index))+1
                    for i in range(cluster_start,index):cluster_lane_count[i]=count
                    cluster_start=index;cluster_end=None
                cluster_end=display_end if cluster_end is None else max(cluster_end,display_end)
            if placements:
                count=max(placements[i][3] for i in range(cluster_start,len(placements)))+1
                for i in range(cluster_start,len(placements)):cluster_lane_count[i]=count
            rendered=[]
            for index,(row,minutes,display_end,lane) in enumerate(placements):
                top=max(0,(minutes-timeline_start)/60*pixels_per_hour);height=max(44,(display_end-minutes)/60*pixels_per_hour-3)
                style=f'--event-lane:{lane};--event-lanes:{cluster_lane_count[index]};--event-height:{height:.1f}px;top:{top:.1f}px'
                rendered.append(f'<div class="{class_name}" style="{style}">{self.calendar_event_card(row,client_names=client_names,practice_owner_names=practice_owner_names)}</div>')
            return ''.join(rendered)
        timeline_events=positioned_events(selected_date,selected_rows,"calendar-timeline-event")
        if selected_rows:
            day_view=f'<section class="calendar-day-timeline" style="--timeline-height:{timeline_height}px"><div class="calendar-timeline-grid">{timeline_lines}</div><div class="calendar-timeline-events">{timeline_events}</div></section>'
        else:
            day_view=f'<section class="calendar-day-list"><section class="section empty-state"><p>Nessun evento</p><a class="btn" href="/calendario/nuovo?data={selected}">+ Crea evento in questa data</a></section></section>'
        week_days=[start+timedelta(days=i) for i in range(7)]
        week_axis=''.join(f'<time style="top:{(hour*60-timeline_start)/60*pixels_per_hour}px">{hour:02d}:00</time>' for hour in range(8,23))
        week_grid_lines=''.join(f'<span class="calendar-week-grid-line" style="top:{(hour*60-timeline_start)/60*pixels_per_hour}px"></span>' for hour in range(8,23))
        week_columns=''.join(f'''<div class="calendar-day-column {'is-selected' if day==selected_date else ''}"><header><a href="{view_url(day,'giorno')}"><b>{italian_short_day(day)}</b></a></header><div class="calendar-week-day-body" style="--timeline-height:{timeline_height}px">{week_grid_lines}<div class="calendar-week-events">{positioned_events(day,by_day.get(day.isoformat(),[]),'calendar-week-event')}</div></div></div>''' for day in week_days)
        week_view=f'<div class="calendar-week-scroll"><section class="calendar-week"><div class="calendar-week-time-column"><header>ORA</header><div class="calendar-week-axis" style="--timeline-height:{timeline_height}px">{week_axis}</div></div>{week_columns}</section></div>'
        month_start=start;offset=month_start.weekday();grid_start=month_start-timedelta(days=offset);month_days=[grid_start+timedelta(days=i) for i in range(42)]
        compact=view=="compatto"
        month_grid='<section class="calendar-month">'+''.join(f'''<div class="calendar-month-day {'selected' if day.isoformat()==selected else ''}"><a href="{view_url(day,'mese')}">{day.day}</a><div class="calendar-dots">{''.join(f'<span class="calendar-dot {event_color_class(row)}" title="{esc(row["title"])}"></span>' for row in by_day.get(day.isoformat(),[]))}</div></div>''' for day in month_days)+'</section>'
        month_agenda=''.join(self.calendar_event_card(row,client_names=client_names,practice_owner_names=practice_owner_names) for row in selected_rows) or '<p class="sub calendar-month-empty">Nessun evento</p>'
        month_view=f'<div class="calendar-month-composition">{month_grid}<section class="calendar-month-agenda"><header><h2>{italian_long_date(selected_date)}</h2><span class="badge">{len(selected_rows)} eventi</span></header><div class="calendar-day-list">{month_agenda}</div></section></div>'
        if view=="giorno":content=day_view
        elif view=="settimana":content=week_view
        elif view in ("mese","compatto"):content=month_view
        elif view=="mista_settimana":content=f'<div class="calendar-mixed calendar-mixed-week"><div>{day_view}</div><div>{week_view}</div></div>'
        else:content=f'<div class="calendar-mixed"><div>{month_view}</div><div><h2>Eventi del {selected_date.strftime("%d/%m/%Y")}</h2>{day_view}</div></div>'
        type_options='<option value="">Tutti i tipi</option>'+''.join(f'<option value="{x}" {"selected" if filters["event_type"]==x else ""}>{"Promemoria" if x=="Appuntamento" else x}</option>' for x in EVENT_TYPES)
        status_options='<option value="">Tutti gli stati dei ritiri</option>'+''.join(f'<option {"selected" if filters["event_status"]==x else ""}>{x}</option>' for x in PICKUP_STATUSES)
        operator_options='<option value="">Tutti gli operatori</option>'+''.join(f'<option {"selected" if filters["operator_name"]==name else ""}>{name}</option>' for name in CALENDAR_OPERATORS)
        vet_options='<option value="">Tutti i veterinari</option>'+''.join(f'<option value="{row["id"]}" {"selected" if filters["veterinarian_id"]==str(row["id"]) else ""}>{esc(row["name"])}</option>' for row in vets)
        filters_html=f'''<details class="advanced-search"><summary>Ricerca e filtri</summary><form class="section advanced-search-form" method="get"><input type="hidden" name="vista" value="{view}"><input type="hidden" name="data" value="{selected}"><div class="fields"><div class="field full"><label>Parole chiave</label><input name="q" value="{esc(filters['q'])}" placeholder="Titolo, animale, cliente, veterinario, luogo, note"></div><div class="field"><label>Tipo</label><select name="event_type">{type_options}</select></div><div class="field"><label>Stato</label><select name="event_status">{status_options}</select></div><div class="field"><label>Operatore</label><select name="operator_name">{operator_options}</select></div><div class="field"><label>Veterinario</label><select name="veterinarian_id">{vet_options}</select></div><div class="field"><label>Luogo</label><select name="location_type"><option value="">Tutti</option>{''.join(f'<option {"selected" if filters["location_type"]==x else ""}>{x}</option>' for x in ("Veterinario","Privato","Sede Livorno","Sede Empoli","Altro indirizzo"))}</select></div><div class="field"><label>Zona</label><input name="zone" value="{esc(filters['zone'])}"></div><div class="field"><label>Sede o fuori sede</label><select name="venue_scope"><option value="">Tutti</option><option value="sede" {"selected" if filters["venue_scope"]=="sede" else ""}>In sede</option><option value="fuori" {"selected" if filters["venue_scope"]=="fuori" else ""}>Fuori sede</option></select></div><div class="field"><label>Dal</label><input type="date" name="date_from" value="{esc(filters['date_from'])}"></div><div class="field"><label>Al</label><input type="date" name="date_to" value="{esc(filters['date_to'])}"></div></div><button class="btn" style="margin-top:12px">Applica</button></form></details>'''
        preference_script=f'''<script>(function(){{const url=new URL(location.href);const saved=localStorage.getItem('ppm_calendar_view');const allowed=['giorno','settimana','mese','mista_settimana','mista_mese','compatto'];if(!url.searchParams.has('vista')&&allowed.includes(saved)&&saved!=='giorno'){{url.searchParams.set('vista',saved);location.replace(url);return;}}localStorage.setItem('ppm_calendar_view','{view}');}})();</script>'''
        if view in ("settimana","mista_settimana"):
            if start.month==end.month:date_title=f"{start.day} – {end.day} {month_names[end.month-1]} {end.year}"
            elif start.year==end.year:date_title=f"{start.day} {month_names[start.month-1]} – {end.day} {month_names[end.month-1]} {end.year}"
            else:date_title=f"{start.day} {month_names[start.month-1]} {start.year} – {end.day} {month_names[end.month-1]} {end.year}"
        elif view in ("mese","mista_mese","compatto"):date_title=f"{month_names[selected_date.month-1]} {selected_date.year}"
        else:date_title=f"{selected_date.day} {month_names[selected_date.month-1]} {selected_date.year}"
        body=f'''<main class="wrap calendar-wrap"><div class="titlebar calendar-main-title"><div><h1>Calendario operativo</h1><p class="sub">Ritiri, riconsegne e promemoria</p></div><div class="calendar-quick-actions"><a class="icon-btn" href="/calendario/cestino" aria-label="Cestino" title="Cestino">{lucide("trash-2")}</a><a class="icon-btn calendar-settings-link" href="/calendario/impostazioni" aria-label="Impostazioni" title="Impostazioni">{lucide("settings")}</a></div></div><nav class="calendar-date-nav"><a class="btn ghost" data-calendar-prev href="{view_url(prev_target)}" aria-label="Periodo precedente">←</a><label class="calendar-date-title"><span>{date_title}</span><input type="date" value="{selected}" onchange="const u=new URL(location.href);u.searchParams.set('data',this.value);location.href=u"></label><a class="btn ghost" data-calendar-next href="{view_url(next_target)}" aria-label="Periodo successivo">→</a><a class="btn ghost calendar-today" href="{view_url(datetime.now().date())}">OGGI</a></nav><div class="calendar-toolbar"><nav class="calendar-view-switch">{switch}</nav></div>{content}{filters_html}{preference_script}</main>'''
        self.send_html(layout("Calendario operativo",body,user))

    def calendar_settings(self,user):
        choices=''.join(f'''<label class="calendar-type-option"><input type="radio" name="calendar_view" value="{key}"><span><b>{label}</b></span></label>''' for key,label in (("giorno","Giorno"),("settimana","Settimana"),("mese","Mese"),("mista_settimana","Mista Giorno + Settimana"),("mista_mese","Mista Mese + Elenco"),("compatto","Mese compatto")))
        body=f'''<main class="wrap calendar-form"><div class="titlebar"><div><h1>Impostazioni Calendario</h1><p class="sub">Scegli la visualizzazione predefinita su questo dispositivo.</p></div><a class="btn ghost" href="/calendario">×</a></div><form class="section" onsubmit="event.preventDefault();localStorage.setItem('ppm_calendar_view',this.calendar_view.value);location.href='/calendario?vista='+encodeURIComponent(this.calendar_view.value)"><div class="calendar-type-grid">{choices}</div><button class="btn" style="margin-top:16px">Salva preferenza</button></form><script>document.addEventListener('DOMContentLoaded',()=>{{const saved=localStorage.getItem('ppm_calendar_view')||'giorno';const choice=document.querySelector('[name=calendar_view][value="'+saved+'"]');if(choice)choice.checked=true;}});</script></main>'''
        self.send_html(layout("Impostazioni Calendario",body,user))

    def calendar_event_form(self,user,event_id=None,draft=None,error=""):
        q=parse_qs(urlparse(self.path).query);rome_now=datetime.now(ROME_TZ);default_date=(q.get("data") or [rome_now.date().isoformat()])[0];next_hour=(rome_now+timedelta(hours=1)).replace(minute=0,second=0,microsecond=0).strftime("%H:%M")
        with db() as c:
            event=c.execute("SELECT * FROM calendar_events WHERE id=?",(event_id,)).fetchone() if event_id else None
            if event_id and not event:return self.send_error(404)
            animals=[dict(row) for row in c.execute("SELECT name,species,weight,cremation_type,notes FROM calendar_event_animals WHERE event_id=? ORDER BY id",(event_id,))] if event_id else []
            estimates=[dict(row) for row in c.execute("SELECT description,amount FROM calendar_event_estimate_items WHERE event_id=? ORDER BY sort_order,id",(event_id,))] if event_id else [{"preset":name,"description":name,"amount":""} for name in ("Cremazione","Ritiro","Riconsegna","Urna")]+[{"preset":"Altro","description":"","amount":""}]
            zones=c.execute("SELECT name FROM calendar_zones ORDER BY name").fetchall()
        if draft is not None:
            event=draft
            try:animals=calendar_parse_items(draft.get("animals_json"),"animal")
            except ValueError:animals=[]
            try:estimates=calendar_parse_items(draft.get("estimate_json"),"estimate")
            except ValueError:estimates=[]
        if not animals and not event_id:animals=[{}]
        val=lambda key,default="":esc(event[key] if event and key in event.keys() and event[key] not in (None,"") else default)
        raw=lambda key,default="":event[key] if event and key in event.keys() and event[key] not in (None,"") else default
        event_type=raw("event_type","");start_date=raw("start_date",raw("start_at",default_date))[:10];end_date=raw("end_date",raw("end_at",start_date or default_date))[:10];start_time=raw("start_time",raw("start_at","")[11:16] or next_hour);end_time=raw("end_time",raw("end_at","")[11:16])
        title_value="" if event_type=="Appuntamento" and re.fullmatch(r"PROMEMORIA\s*",str(raw("title","")).strip(),flags=re.I) else val("title")
        types=''.join(f'''<label class="calendar-type-option"><input type="radio" name="event_type" value="{kind}" {"checked" if event_type==kind else ""} required onclick="calendarTypeSelected(this)"><span class="calendar-event-type-icon">{lucide(icon_name)}</span><span><b>{label}</b><small class="sub">{desc}</small></span></label>''' for kind,label,desc,icon_name in (("Ritiro","RITIRO","Ritiro presso veterinario o privato","paw"),("Ritiro in sede","RITIRO IN SEDE","Consegna presso una sede","home"),("Riconsegna","RICONSEGNA","Riconsegna al cliente","archive"),("Riconsegna in sede","RICONSEGNA IN SEDE","Ritiro ceneri presso sede","home"),("Appuntamento","PROMEMORIA","Promemoria, riunioni, fornitori e impegni","calendar")))
        operator_options='<option value="">Seleziona operatore</option>'+''.join(f'<option {"selected" if raw("operator_name")==name else ""}>{name}</option>' for name in CALENDAR_OPERATORS)
        if user["role"]=="admin":
            operator_field=f'<label>Operatore *</label><select name="operator_name" required>{operator_options}</select><small class="calendar-wizard-error" data-operator-error></small>'
        else:
            operator_display=raw("operator_name") or user["display_name"]
            operator_field=f'<input type="hidden" name="operator_name" value="{esc(operator_display)}"><label>Operatore</label><p style="margin:0;padding:11px 0;font-weight:700">{esc(operator_display)}</p>'
        pickup_status=''.join(f'<option {"selected" if raw("event_status","Da ritirare")==s else ""}>{s}</option>' for s in PICKUP_STATUSES)
        location_type_value=raw("location_type","")
        location_type_options='<option value="">Seleziona</option>'+''.join(f'<option value="{value}" {"selected" if location_type_value==value else ""}>{label}</option>' for value,label in (("Veterinario","Presso veterinario"),("Privato","A domicilio privato")))
        client_is_empty=not (val('client_id') or val('client_first_name') or val('client_last_name'))
        pickup_location_block=f'''<div class="calendar-subblock" data-calendar-types="Ritiro" {"" if event_type=="Ritiro" else "hidden"}><h3>Luogo del ritiro</h3><div class="fields"><div class="field"><label>Tipo di luogo *</label><select name="location_type" required data-prev-value="{esc(location_type_value)}" onchange="calendarPickupLocationChanged(this)">{location_type_options}</select></div><div class="field full lookup" data-pickup-location="Veterinario" {"" if location_type_value=="Veterinario" else "hidden"}><label>Cerca veterinario</label><input id="calendarVetSearch" autocomplete="off" placeholder="Ambulatorio, medico o città"><div id="calendarVetResults" class="lookup-results hidden"></div><input type="hidden" name="veterinarian_id" value="{val('veterinarian_id')}"></div><div class="field" data-pickup-location="Veterinario" {"" if location_type_value=="Veterinario" else "hidden"}><label>Nome ambulatorio</label><input name="venue_name" value="{val('venue_name')}"></div><div class="field full"><label>Indirizzo *</label><input name="address" value="{val('address')}" {"required" if location_type_value else ""}><button type="button" class="btn ghost calendar-use-client-address" data-use-client-address hidden onclick="calendarUseClientAddress(this)">Usa indirizzo cliente</button></div></div></div>'''
        client_block=f'''<div class="calendar-subblock"><h3>Cliente / Proprietario</h3><div class="fields"><input type="hidden" name="client_id" value="{val('client_id')}"><div class="field full lookup"><label>Cerca cliente</label><input id="calendarClientSearch" autocomplete="off" placeholder="Nome, telefono o codice fiscale"><div id="calendarClientResults" class="lookup-results hidden"></div></div><div class="field"><label>Nome</label><input name="client_first_name" value="{val('client_first_name')}"></div><div class="field"><label>Cognome</label><input name="client_last_name" value="{val('client_last_name')}"></div><div class="field"><label>Telefono</label><input type="tel" inputmode="tel" name="client_phone" value="{val('client_phone')}"></div></div><p class="sub" data-client-empty-hint {"" if client_is_empty else "hidden"}>Da completare al momento del ritiro</p></div>'''
        vet_reference_block=f'''<div class="calendar-subblock"><h3>Veterinario di riferimento</h3><div class="fields"><div class="field full"><label>Nome ambulatorio</label><input name="veterinarian_name" value="{val('veterinarian_name')}"></div></div></div>'''
        delivery_status=''.join(f'<option {"selected" if raw("event_status","In programma")==s else ""}>{s}</option>' for s in DELIVERY_STATUSES)
        payment_status=''.join(f'<option {"selected" if raw("payment_status","Da pagare")==s else ""}>{s}</option>' for s in PAYMENT_STATUSES)
        action=f'/calendario/{event_id}/modifica' if event_id else '/calendario/nuovo'
        zones_json=json.dumps([row['name'] for row in zones],ensure_ascii=False).replace("</","<\\/")
        error_html=f'<div class="calendar-validation" role="alert">{esc(error)}</div><script>sessionStorage.removeItem("ppm_calendar_created")</script>' if error else ''
        close_url=f'/calendario/{event_id}' if event_id else '/calendario'
        error_step=2 if any(word in error.lower() for word in ("zona","sede","titolo","data","ora")) else 3 if any(word in error.lower() for word in ("animal","luogo")) else 1
        wheel_hours=''.join(f'<button class="calendar-wheel-option" type="button" data-time-value="{hour}">{hour:02d}</button>' for hour in range(24))
        wheel_minutes=''.join(f'<button class="calendar-wheel-option" type="button" data-time-value="{minute}">{minute:02d}</button>' for minute in range(0,60,5))
        def datetime_row(label,date_name,date_value,time_name,time_value,required=False):
            required_attr=' required' if required else ''
            return f'''<div class="calendar-datetime-row"><label>{label}</label><input class="calendar-date-compact" type="date" name="{date_name}" value="{esc(date_value)}"{required_attr}><div class="calendar-time-slot" data-calendar-time><input class="calendar-time-entry" type="text" inputmode="numeric" name="{time_name}" data-time-entry value="{esc(time_value)}"{required_attr} placeholder="{'09:30' if required else '10:30'}" onfocus="calendarTimeFocus(this)" onbeforeinput="calendarTimeBeforeInput(this,event)" oninput="calendarTimeInput(this)" onblur="calendarTimeBlur(this)"></div><div class="calendar-time-wheel" data-time-wheel hidden><div class="calendar-wheel-column" data-wheel-part="hour">{wheel_hours}</div><span class="calendar-wheel-separator">:</span><div class="calendar-wheel-column" data-wheel-part="minute">{wheel_minutes}</div></div></div>'''
        datetime_fields=datetime_row("Inizio","start_date",start_date,"start_time",start_time,True)+datetime_row("Fine","end_date",end_date,"end_time",end_time)
        draft_key=f"ppm_calendar_draft_edit_{event_id}" if event_id else "ppm_calendar_draft_new"
        draft_status='<div id="calendarDraftStatus" class="autosave-status" data-state="idle" hidden role="status"><span data-draft-label></span></div>'
        body=f'''<main class="wrap calendar-form"><div class="titlebar"><div><h1>{'Modifica evento' if event_id else 'Nuovo evento'}</h1><p class="sub">Crea o modifica un evento in tre passaggi</p></div><a class="btn ghost" href="{close_url}" onclick="return calendarConfirmExit(event,this.href)" aria-label="Chiudi">×</a></div>{error_html}{draft_status}<div class="calendar-steps" aria-label="Fasi evento"><button class="active" type="button" onclick="calendarStepFromIndicator(1)" aria-label="Vai alla fase 1">1</button><button type="button" onclick="calendarStepFromIndicator(2)" aria-label="Vai alla fase 2">2</button><button type="button" onclick="calendarStepFromIndicator(3)" aria-label="Vai alla fase 3">3</button></div><form id="calendarEventForm" data-current-step="1" data-draft-key="{draft_key}" method="post" action="{action}" onsubmit="return calendarSubmit(this)">
        <section class="section calendar-form-step" data-step="1"><div class="field calendar-first-operator">{operator_field}</div><h2>Tipo evento</h2><div class="calendar-type-grid">{types}</div></section>
        <section class="section calendar-form-step" data-step="2" hidden><h2>Data e titolo</h2><div class="fields"><div class="calendar-title-zone-row"><div class="field"><label>Titolo *</label><input name="title" value="{title_value}" required oninput="this.dataset.manual='1'"></div><div class="field calendar-zone-field" data-calendar-types="Ritiro|Riconsegna" {"" if event_type in ("Ritiro","Riconsegna") else "hidden"}><label>Zona{' *' if event_type=='Ritiro' else ''}</label><input name="zone" value="{val('zone')}" autocomplete="off" oninput="calendarZoneInput(this)" onfocus="calendarZoneInput(this)" onblur="calendarZoneOffer(this)"><div class="calendar-zone-results" hidden></div><small class="sub">Scrivi per cercare o inserire una nuova zona.</small><label><input type="checkbox" name="save_zone" value="1"> Salva nei suggerimenti</label></div><div class="field" data-calendar-types="Ritiro in sede|Riconsegna in sede" {"" if event_type in ("Ritiro in sede","Riconsegna in sede") else "hidden"}><label>Sede *</label><select name="destination_site" onchange="calendarAutoTitle()"><option value="">Seleziona</option><option {"selected" if raw("destination_site")=="Livorno" else ""}>Livorno</option><option {"selected" if raw("destination_site")=="Empoli" else ""}>Empoli</option></select></div></div><div class="field full lookup" data-calendar-types="Riconsegna|Riconsegna in sede" {"" if event_type in ("Riconsegna","Riconsegna in sede") else "hidden"}><label>Animale *</label><input id="calendarDeliveryAnimalSearch" name="animal_name" value="{val('animal_name')}" placeholder="Cerca animale o proprietario" autocomplete="off" oninput="calendarAutoTitle()"><div id="calendarDeliveryAnimalResults" class="lookup-results hidden"></div><input type="hidden" name="linked_practice_id" value="{val('linked_practice_id')}"></div><div class="calendar-datetime-stack">{datetime_fields}</div><div class="field full"><label class="modern-check"><input type="checkbox" name="all_day" value="1" {"checked" if raw("all_day") else ""} onchange="calendarAllDayChanged(this)"> Tutto il giorno</label></div></div><div class="actions" style="margin-top:16px"><button class="btn ghost" type="button" onclick="calendarStep(1,'back')">Indietro</button><button class="btn" type="button" onclick="calendarStep(3)">Avanti</button></div></section>
        <section class="section calendar-form-step" data-step="3" hidden><h2>Informazioni</h2>
        <div data-calendar-types="Ritiro|Ritiro in sede" {"" if event_type in ("Ritiro","Ritiro in sede") else "hidden"}><div class="fields"><div class="field"><label>Stato ritiro</label><select name="event_status">{pickup_status}</select></div>{pickup_location_block}{client_block}{vet_reference_block}<input type="hidden" name="phone" value="{val('phone')}"><input type="hidden" name="veterinarian_phone" value="{val('veterinarian_phone')}"><input type="hidden" name="veterinarian_address" value="{val('veterinarian_address')}"><input type="hidden" name="veterinarian_hours" value="{val('veterinarian_hours')}"><input type="hidden" name="veterinarian_contact" value="{val('veterinarian_contact')}"></div><h2>Animali</h2><div class="calendar-repeat-list" data-calendar-list="animal"></div><input type="hidden" name="animals_json"><button class="btn ghost" type="button" onclick="calendarAddRow('animal')">+ Aggiungi animale</button><h2 style="margin-top:18px">Preventivo previsto</h2><div class="calendar-repeat-list" data-calendar-list="estimate"></div><input type="hidden" name="estimate_json"><p>Totale automatico: <b data-estimate-total>€ 0,00</b></p><div class="field full" style="margin-top:18px"><label>Note</label><textarea name="notes">{val('notes')}</textarea></div></div>
        <div data-calendar-types="Riconsegna|Riconsegna in sede" {"" if event_type in ("Riconsegna","Riconsegna in sede") else "hidden"}><input type="hidden" name="event_status" value="In programma"><div class="fields"><div class="field"><label>Stato pagamento</label><select name="payment_status">{payment_status}</select></div><div class="field"><label>Importo</label><input inputmode="decimal" name="payment_amount" value="{val('payment_amount','0')}"></div><div class="field full"><label>Dettaglio pagamento dalla pratica</label><input data-delivery-payment-detail readonly value=""></div><div class="field full" data-calendar-types="Riconsegna" {"" if event_type=="Riconsegna" else "hidden"}><label>Indirizzo</label><input name="delivery_address" value="{val('address')}" placeholder="Compilato automaticamente selezionando l'animale"></div><div class="field full lookup" data-calendar-types="Riconsegna" {"" if event_type=="Riconsegna" else "hidden"}><label>Ambulatorio riconsegna (facoltativo)</label><input id="calendarDeliveryClinicSearch" value="{val('delivery_clinic_name')}" autocomplete="off" placeholder="Cerca ambulatorio o veterinario"><div id="calendarDeliveryClinicResults" class="lookup-results hidden"></div><input type="hidden" name="delivery_clinic_id" value="{val('delivery_clinic_id')}"><input type="hidden" name="delivery_clinic_name" value="{val('delivery_clinic_name')}"><input type="hidden" name="delivery_clinic_address" value="{val('delivery_clinic_address')}"><input type="hidden" name="delivery_clinic_phone" value="{val('delivery_clinic_phone')}"></div><div class="field full"><label>Note</label><textarea name="notes">{val('notes')}</textarea></div></div></div>
        <div data-calendar-types="Appuntamento" {"" if event_type=="Appuntamento" else "hidden"}><div class="field full"><label>Note</label><textarea name="notes">{val('notes')}</textarea></div></div>
        <div class="actions" style="margin-top:18px"><button class="btn ghost" type="button" onclick="calendarStep(2,'back')">Indietro</button><button class="btn">{'Salva modifiche' if event_id else 'Crea evento'}</button><a class="btn ghost" href="{close_url}" onclick="return calendarConfirmExit(event,this.href)">Annulla</a></div></section></form><script>window.CALENDAR_ZONES={zones_json};document.addEventListener('DOMContentLoaded',()=>{{{''.join(f"calendarAddRow('animal',{json.dumps(a,ensure_ascii=False)});" for a in animals) if event_type in ('Ritiro','Ritiro in sede','') else ''}{''.join(f"calendarAddRow('estimate',{json.dumps(i,ensure_ascii=False)});" for i in estimates) if event_type in ('Ritiro','Ritiro in sede','') else ''}calendarTypeChanged();calendarInitLookups();const allDay=document.querySelector('#calendarEventForm input[name="all_day"]');if(allDay)calendarAllDayChanged(allDay);document.querySelectorAll('[data-time-entry]').forEach(input=>{{calendarTimeInput(input);const wheel=input.closest('.calendar-datetime-row')?.querySelector('[data-time-wheel]');if(wheel)calendarInitTimeWheel(wheel);}});calendarStep({error_step if error else 1},'back','replace');}});</script></main>'''
        self.send_html(layout("Modifica evento" if event_id else "Nuovo evento",body,user))

    def save_calendar_event(self,user,event_id=None):
        form=self.form();stamp=now();created_now=False;linked_practice_id=int(form.get("linked_practice_id")) if str(form.get("linked_practice_id") or "").isdigit() else None
        if not form.get("phone") and form.get("client_phone"):form["phone"]=form.get("client_phone")
        try:
            animals=calendar_parse_items(form.get("animals_json"),"animal")
            estimates=calendar_parse_items(form.get("estimate_json"),"estimate")
            if animals and form.get("event_type") in ("Riconsegna","Riconsegna in sede"):
                form["animal_name"]=animals[0].get("name","")
            data=normalize_event(form)
        except ValueError as exc:
            print(f"[CALENDAR_VALIDATION] {exc}",flush=True)
            return self.calendar_event_form(user,event_id,form,str(exc))
        try:
            with db() as c:
                if data["client_id"] and not c.execute("SELECT 1 FROM clients WHERE id=?",(data["client_id"],)).fetchone():data["client_id"]=None
                if data["veterinarian_id"]:
                    vet=c.execute("SELECT * FROM veterinarians WHERE id=? AND active=1",(data["veterinarian_id"],)).fetchone()
                    if not vet:data["veterinarian_id"]=None
                    else:
                        data["veterinarian_name"]=data["veterinarian_name"] or vet["short_name"] or vet["clinic_name"]
                        data["veterinarian_phone"]=data["veterinarian_phone"] or vet["phone"] or ""
                        data["veterinarian_address"]=data["veterinarian_address"] or vet["address"] or ""
                        data["veterinarian_hours"]=data["veterinarian_hours"] or vet["notes"] or ""
                if data["delivery_clinic_id"]:
                    delivery_vet=c.execute("SELECT * FROM veterinarians WHERE id=? AND active=1",(data["delivery_clinic_id"],)).fetchone()
                    if not delivery_vet:data["delivery_clinic_id"]=None
                    else:
                        data["delivery_clinic_name"]=data["delivery_clinic_name"] or delivery_vet["short_name"] or delivery_vet["clinic_name"]
                        data["delivery_clinic_address"]=data["delivery_clinic_address"] or delivery_vet["address"] or ""
                        data["delivery_clinic_phone"]=data["delivery_clinic_phone"] or delivery_vet["phone"] or ""
                if form.get("save_zone")=="1" and data["zone"]:c.execute("INSERT OR IGNORE INTO calendar_zones(name,is_default,created_at) VALUES(?,0,?)",(data["zone"],stamp))
                if event_id:
                    old=c.execute("SELECT * FROM calendar_events WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",(event_id,)).fetchone()
                    if not old:return self.send_error(404)
                    if user["role"]!="admin": data["operator_name"]=old["operator_name"]
                    assignments=','.join(f"{key}=?" for key in data)
                    c.execute(f"UPDATE calendar_events SET {assignments},updated_at=?,updated_by=? WHERE id=?",tuple(data.values())+(stamp,user["id"],event_id))
                    for key,value in data.items():
                        if str(old[key] if key in old.keys() and old[key] is not None else "")!=str(value if value is not None else ""):calendar_add_history(c,event_id,user["id"],f"Modifica {key}",old[key] if key in old.keys() else "",value,stamp)
                    old_animals=[dict(row) for row in c.execute("SELECT name,species,weight,cremation_type,notes FROM calendar_event_animals WHERE event_id=? ORDER BY id",(event_id,))]
                    old_estimates=[dict(row) for row in c.execute("SELECT description,amount FROM calendar_event_estimate_items WHERE event_id=? ORDER BY sort_order,id",(event_id,))]
                    calendar_sync_children(c,event_id,animals,estimates,stamp)
                    if old_animals!=animals:calendar_add_history(c,event_id,user["id"],"Modifica animali",json.dumps(old_animals,ensure_ascii=False),json.dumps(animals,ensure_ascii=False),stamp)
                    if old_estimates!=estimates:calendar_add_history(c,event_id,user["id"],"Modifica preventivo",json.dumps(old_estimates,ensure_ascii=False),json.dumps(estimates,ensure_ascii=False),stamp)
                    kind="calendar_event_cancelled" if data["event_status"]=="Annullato" and old["event_status"]!="Annullato" else "calendar_event_updated"
                    emit_notification(c,kind,"Evento calendario aggiornato",data["title"],actor_user_id=user["id"],payload={"url":f"/calendario/{event_id}"},db_path=DB_PATH)
                else:
                    created_now=True
                    if user["role"]!="admin": data["operator_name"]=user["display_name"]
                    cols=list(data)+["created_by","created_at","updated_at","updated_by"]
                    cur=c.execute(f"INSERT INTO calendar_events({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",tuple(data.values())+(user["id"],stamp,stamp,user["id"]));event_id=cur.lastrowid
                    calendar_sync_children(c,event_id,animals,estimates,stamp);calendar_add_history(c,event_id,user["id"],"Creazione evento","",data["title"],stamp)
                    event_type_emoji={"Ritiro":"🐾","Ritiro in sede":"🐾","Riconsegna":"📦","Riconsegna in sede":"📦","Appuntamento":"📅"}.get(data["event_type"],"📆")
                    emit_notification(c,"calendar_event_created",f"{event_type_emoji} Nuovo evento calendario",data["title"],actor_user_id=user["id"],payload={"url":f"/calendario/{event_id}"},db_path=DB_PATH)
                if linked_practice_id and data["event_type"] in ("Riconsegna","Riconsegna in sede"):
                    c.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?",(linked_practice_id,event_id))
                if data["event_status"] in ("Annullato","Completato"):
                    c.execute("UPDATE calendar_event_notifications SET status='annullato',error='' WHERE event_id=? AND status IN ('programmato','in_invio','fallito')",(event_id,))
                else:
                    try:
                        event_start=datetime.fromisoformat(data["start_at"]).replace(tzinfo=ROME_TZ)
                        current_time=datetime.now(ROME_TZ)
                    except ValueError:event_start=current_time=datetime.now(ROME_TZ)
                    if event_start>current_time:schedule_event_notifications(c,event_id,data["start_at"],stamp)
                    else:c.execute("UPDATE calendar_event_notifications SET status='annullato',error='' WHERE event_id=? AND status IN ('programmato','in_invio','fallito')",(event_id,))
        except Exception:
            print("[CALENDAR_SAVE] errore salvataggio evento\n"+traceback.format_exc(),flush=True)
            return self.calendar_event_form(user,event_id,form,"Non è stato possibile salvare l’evento. Riprova senza chiudere questa schermata.")
        self.redirect(f"/calendario/{event_id}")

    def calendar_event_detail(self,user,event_id,error=""):
        tab=(parse_qs(urlparse(self.path).query).get("tab") or ["dettagli"])[0]
        if tab not in ("dettagli","note","commenti","storico"):tab="dettagli"
        with db() as c:
            event=c.execute("""SELECT e.*,u.display_name creator_name,uu.display_name updater_name,au.display_name assigned_name,p.practice_number,
              p.owner_first_name practice_owner_first_name,p.owner_last_name practice_owner_last_name,p.owner_company practice_owner_company
              FROM calendar_events e JOIN users u ON u.id=e.created_by LEFT JOIN users uu ON uu.id=e.updated_by LEFT JOIN users au ON au.id=e.assigned_user_id LEFT JOIN practices p ON p.id=e.linked_practice_id WHERE e.id=?""",(event_id,)).fetchone()
            if not event:return self.send_error(404)
            client_names={};practice_owner_names={}
            if event["client_id"]:
                client_row=c.execute("SELECT first_name,last_name,company_name FROM clients WHERE id=?",(event["client_id"],)).fetchone()
                if client_row:
                    name=" ".join(x for x in (client_row["first_name"],client_row["last_name"]) if x).strip() or client_row["company_name"] or ""
                    if name:client_names[event["client_id"]]=name
            if event["linked_practice_id"]:
                owner=" ".join(x for x in (event["practice_owner_first_name"],event["practice_owner_last_name"]) if x).strip() or event["practice_owner_company"] or ""
                if owner:practice_owner_names[event["linked_practice_id"]]=owner
            client_display=self.calendar_event_client_name(event,client_names,practice_owner_names)
            animals=c.execute("SELECT * FROM calendar_event_animals WHERE event_id=? ORDER BY id",(event_id,)).fetchall()
            estimates=c.execute("SELECT * FROM calendar_event_estimate_items WHERE event_id=? ORDER BY sort_order,id",(event_id,)).fetchall()
            comments=c.execute("SELECT c.*,u.display_name FROM calendar_event_comments c JOIN users u ON u.id=c.user_id WHERE event_id=? ORDER BY c.created_at",(event_id,)).fetchall()
            history=c.execute("SELECT h.*,u.display_name FROM calendar_event_history h LEFT JOIN users u ON u.id=h.user_id WHERE event_id=? ORDER BY h.created_at DESC",(event_id,)).fetchall()
        tabs=''.join(f'<a class="{"active" if tab==key else ""}" href="/calendario/{event_id}?tab={key}">{label}</a>' for key,label in (("dettagli","Dettagli"),("note","Note"),("commenti","Commenti"),("storico","Storico")))
        phone=only_digits(event["client_phone"] or event["phone"] or event["veterinarian_phone"]);contact=''.join((f'<a class="btn ghost" href="tel:+{phone}">Chiama</a><a class="btn ghost" href="https://wa.me/{phone}" target="_blank">WhatsApp</a>' if phone else '',f'<a class="btn ghost" href="https://www.google.com/maps/search/?api=1&query={quote(event["address"] or event["veterinarian_address"] or event["venue_name"] or "")}" target="_blank">Maps</a>' if event["address"] or event["veterinarian_address"] else ''))
        animal_rows=''.join(f'<tr><td>{esc(a["name"] or "-")}</td><td>{esc(a["species"])}</td><td>{esc(a["weight"])} kg</td><td>{esc(a["cremation_type"])}</td><td>{esc(a["notes"])}</td></tr>' for a in animals) or '<tr><td colspan="5">Nessun animale</td></tr>'
        estimate_total=sum(float(i["amount"] or 0) for i in estimates);estimate_rows=''.join(f'<tr><td>{esc(i["description"])}</td><td>{money_it(i["amount"])}</td></tr>' for i in estimates) or '<tr><td colspan="2">Nessuna voce</td></tr>'
        display_event_type="Promemoria" if event["event_type"]=="Appuntamento" else event["event_type"]
        status_kv=f'<div class="kv"><small>Stato</small><b class="{event_color_class(event)}">{esc(event["event_status"] or "Nessuno")}</b></div>' if event["event_type"] in ("Ritiro","Ritiro in sede") else ''
        zone_kv=f'<div class="kv"><small>Zona</small><b>{esc(event["zone"])}</b></div>' if event["zone"] and event["event_type"]!="Appuntamento" else ''
        delivery_clinic_kv=f'<div class="kv"><small>Ambulatorio riconsegna</small><b>{esc(event["delivery_clinic_name"])}</b></div>' if event["delivery_clinic_name"] else ''
        if tab=="dettagli":panel=f'''<section class="section"><div class="kvs"><div class="kv"><small>Tipo</small><b>{esc(display_event_type)}</b></div><div class="kv"><small>Intervallo</small><b>{esc(event['start_at'].replace('T',' ')[:16])} → {esc(event['end_at'].replace('T',' ')[:16])}</b></div>{status_kv}<div class="kv"><small>Cliente</small><b>{esc(client_display or '-')}</b></div>{zone_kv}{delivery_clinic_kv}<div class="kv"><small>Luogo</small><b>{esc(event['venue_name'] or event['location_type'] or '-')}</b></div><div class="kv"><small>Operatore</small><b>{esc(event['operator_name'] or event['assigned_name'] or '-')}</b></div><div class="kv"><small>Pagamento</small><b>{esc(event['payment_status'] or '-')} {money_it(event['payment_amount']) if event['payment_status'] else ''}</b></div></div><div class="actions" style="margin-top:15px">{contact}</div></section><section class="section" style="margin-top:14px"><h2>Animali</h2><div class="tablebox"><table><thead><tr><th>Nome</th><th>Specie</th><th>Peso</th><th>Cremazione</th><th>Note</th></tr></thead><tbody>{animal_rows}</tbody></table></div><h2 style="margin-top:18px">Preventivo previsto: {money_it(estimate_total)}</h2><div class="tablebox"><table><tbody>{estimate_rows}</tbody></table></div></section>'''
        elif tab=="note":panel=f'<section class="section"><h2>Note</h2><p style="white-space:pre-wrap">{esc(event["notes"] or "Nessuna nota")}</p></section>'
        elif tab=="commenti":
            comment_html=''.join(f'<article class="calendar-comment"><b>{esc(row["display_name"])}</b><small class="sub"> · {esc(row["created_at"].replace("T"," ")[:16])}{" · modificato" if row["updated_at"] else ""}{" · eliminato" if row["deleted_at"] else ""}</small><p>{esc("Commento eliminato" if row["deleted_at"] else row["message"])}</p>{f"<details><summary>Modifica commento</summary><form method=\"post\" action=\"/calendario/{event_id}/commenti/{row['id']}/modifica\"><textarea name=\"message\" required maxlength=\"2000\">{esc(row['message'])}</textarea><button class=\"btn ghost\">Salva</button></form></details><form method=\"post\" action=\"/calendario/{event_id}/commenti/{row['id']}/elimina\"><button class=\"btn ghost\">Elimina</button></form>" if not row["deleted_at"] and (row["user_id"]==user["id"] or user["role"]=="admin") else ""}</article>' for row in comments) or '<p>Nessun commento.</p>'
            panel=f'''<section class="section">{comment_html}<form method="post" action="/calendario/{event_id}/commento" style="margin-top:15px"><label>Nuovo commento</label><textarea name="message" required maxlength="2000"></textarea><button class="btn">Invia commento</button></form></section>'''
        else:panel='<section class="section timeline">'+''.join(f'<div class="event"><b>{esc(row["action"])}</b><small class="sub"> · {esc(row["display_name"] or "Sistema")} · {esc(row["created_at"].replace("T"," ")[:16])}</small><p>{esc(row["old_value"])} → {esc(row["new_value"])}</p></div>' for row in history)+'</section>'
        create_practice=''
        if event["event_type"] in ("Ritiro","Ritiro in sede") and event["event_status"]=="Ritirato":create_practice=f'<a class="btn" href="{f"/pratiche/{event["linked_practice_id"]}" if event["linked_practice_id"] else f"/nuova?calendar_event_id={event_id}"}">{"Apri pratica "+esc(event["practice_number"]) if event["linked_practice_id"] else "+ Crea pratica"}</a>'
        status_form=''
        if event["event_type"] in ("Ritiro","Ritiro in sede"):status_form=f'''<form class="calendar-detail-status" method="post" action="/calendario/{event_id}/stato" data-client-empty="{"1" if not client_display else "0"}" onsubmit="return calendarConfirmPickupStatus(this)"><label for="calendarDetailStatus"><b>Cambia stato del ritiro</b></label><select id="calendarDetailStatus" name="status">{''.join(f'<option {"selected" if event["event_status"]==s else ""}>{s}</option>' for s in PICKUP_STATUSES)}</select><button class="btn">Salva nuovo stato</button></form>'''
        link_practice_section=''
        if event["event_type"] in ("Ritiro","Ritiro in sede") and event["event_status"]=="Ritirato":
            if event["linked_practice_id"]:
                unlink=f'''<form method="post" action="/calendario/{event_id}/scollega-pratica" onsubmit="return confirm('Confermi lo scollegamento della pratica da questo evento?')" style="margin-top:8px"><input type="hidden" name="confirm" value="SCOLLEGA"><button class="btn danger-btn">Scollega pratica</button></form>''' if user["role"]=="admin" else ''
                link_practice_section=f'''<section class="section" style="margin-top:14px"><h2>Pratica collegata</h2><p><b>{esc(event["practice_number"])}</b></p><a class="btn ghost" href="/pratiche/{event["linked_practice_id"]}">Apri pratica</a>{unlink}</section>'''
            else:
                link_practice_section=f'''<section class="section" style="margin-top:14px"><h2>Collega pratica esistente</h2><div class="field full lookup"><input id="calendarLinkPracticeSearch" data-event-id="{event_id}" autocomplete="off" placeholder="Cerca per animale, proprietario, veterinario o numero pratica"><div id="calendarLinkPracticeResults" class="lookup-results hidden"></div></div></section>'''
        confetti=''.join(f'<i style="left:{(index*23)%97}%;--drift:{((index%9)-4)*22}px;--rotate:{index*31}deg;--delay:{(index%6)*0.06:.2f}s;--confetti:{("#ef405f","#34d399","#60a5fa","#facc15","#c084fc","#fb7185")[index%6]}"></i>' for index in range(44))
        checkmark='<svg class="calendar-created-check" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="24" fill="none"/><path fill="none" d="M14 27l8 8 16-17"/></svg>'
        celebration=f'<div class="calendar-created-celebration" aria-hidden="true"><div class="calendar-created-confetti">{confetti}</div><div class="calendar-created-celebration-card"><span class="calendar-created-celebration-icon">{checkmark}</span><b>Evento creato!</b></div></div>'
        created_animation=f'''<template id="calendarCreatedCelebration">{celebration}</template><script>try{{if(sessionStorage.getItem('ppm_calendar_created')==='1'){{sessionStorage.removeItem('ppm_calendar_created');document.body.append(document.getElementById('calendarCreatedCelebration').content.cloneNode(true));}}}}catch(error){{}}</script>'''
        error_html=f'<div class="flash warning">{esc(error)}</div>' if error else ''
        body=f'''{created_animation}<main class="wrap calendar-wrap">{error_html}<div class="titlebar"><div><h1 class="{event_color_class(event)}">{esc(re.sub(r'^APPUNTAMENTO\b','PROMEMORIA',event['title'],flags=re.I) if event['event_type']=='Appuntamento' else event['title'])}</h1><p class="sub">Creato da {esc(event['creator_name'])} · {esc(event['created_at'].replace('T',' ')[:16])}</p></div><div class="actions"><a class="btn ghost" href="/calendario">Calendario</a><a class="btn" href="/calendario/{event_id}/modifica">Modifica</a>{create_practice}</div></div><nav class="calendar-tabs">{tabs}</nav><div class="calendar-detail-grid"><div>{panel}</div><aside><section class="section"><h2>Azioni</h2>{status_form}<form method="post" action="/calendario/{event_id}/elimina" onsubmit="return confirm('Spostare questo evento nel cestino?')"><button class="btn ghost" style="margin-top:12px">Sposta nel cestino</button></form></section>{link_practice_section}</aside></div></main>'''
        self.send_html(layout(re.sub(r'^APPUNTAMENTO\b','PROMEMORIA',event["title"],flags=re.I) if event["event_type"]=='Appuntamento' else event["title"],body,user))

    def calendar_event_action(self,user,event_id,action):
        form=self.form();stamp=now()
        with db() as c:
            event=c.execute("SELECT * FROM calendar_events WHERE id=?",(event_id,)).fetchone()
            if not event:return self.send_error(404)
            if action=="stato":
                allowed=PICKUP_STATUSES if event["event_type"] in ("Ritiro","Ritiro in sede") else DELIVERY_STATUSES if event["event_type"] in ("Riconsegna","Riconsegna in sede") else ()
                status=form.get("status","")
                if status not in allowed:return self.calendar_event_detail(user,event_id,error="Stato non valido.")
                c.execute("UPDATE calendar_events SET event_status=?,updated_at=?,updated_by=? WHERE id=?",(status,stamp,user["id"],event_id));calendar_add_history(c,event_id,user["id"],"Cambio stato",event["event_status"],status,stamp)
                kind="calendar_event_cancelled" if status=="Annullato" else "calendar_event_updated";emit_notification(c,kind,"Stato evento aggiornato",f'{event["title"]}: {status}',actor_user_id=user["id"],payload={"url":f"/calendario/{event_id}"},db_path=DB_PATH)
            elif action=="commento":
                message=form.get("message","").strip()[:2000]
                if not message:return self.calendar_event_detail(user,event_id,error="Il commento non può essere vuoto.")
                c.execute("INSERT INTO calendar_event_comments(event_id,user_id,message,created_at) VALUES(?,?,?,?)",(event_id,user["id"],message,stamp));calendar_add_history(c,event_id,user["id"],"Aggiunta commento","",message,stamp);emit_notification(c,"calendar_comment","Nuovo commento evento",event["title"],actor_user_id=user["id"],payload={"url":f"/calendario/{event_id}?tab=commenti"},db_path=DB_PATH)
            elif action=="elimina":
                c.execute("UPDATE calendar_events SET deleted_at=?,deleted_by=?,updated_at=? WHERE id=?",(stamp,user["id"],stamp,event_id));c.execute("UPDATE calendar_event_notifications SET status='annullato' WHERE event_id=? AND status IN ('programmato','in_invio')",(event_id,));calendar_add_history(c,event_id,user["id"],"Eliminazione","","Cestino",stamp)
            elif action=="ripristina":c.execute("UPDATE calendar_events SET deleted_at=NULL,deleted_by=NULL,updated_at=?,updated_by=? WHERE id=?",(stamp,user["id"],event_id));calendar_add_history(c,event_id,user["id"],"Ripristino","Cestino","Attivo",stamp)
            elif action=="elimina-definitiva":
                if user["role"]!="admin" or form.get("confirm")!="ELIMINA DEFINITIVAMENTE":return self.send_error(403,"Conferma amministratore mancante")
                c.execute("DELETE FROM calendar_events WHERE id=? AND deleted_at IS NOT NULL",(event_id,));return self.redirect("/calendario/cestino")
            elif action=="collega-pratica":
                if event["event_type"] not in ("Ritiro","Ritiro in sede"):return self.send_error(400,"Azione non valida per questo tipo di evento")
                if event["linked_practice_id"]:return self.send_error(409,"Evento già collegato a una pratica")
                practice_id=form.get("practice_id","")
                if not practice_id.isdigit():return self.send_error(400,"Pratica non valida")
                practice=c.execute("SELECT id,practice_number FROM practices WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",(int(practice_id),)).fetchone()
                if not practice:return self.send_error(404,"Pratica non trovata")
                c.execute("UPDATE calendar_events SET linked_practice_id=?,updated_at=?,updated_by=? WHERE id=?",(practice["id"],stamp,user["id"],event_id))
                calendar_add_history(c,event_id,user["id"],"Collegamento pratica","",practice["practice_number"],stamp)
            elif action=="scollega-pratica":
                if user["role"]!="admin":return self.send_error(403,"Solo un amministratore può scollegare la pratica")
                if form.get("confirm")!="SCOLLEGA":return self.send_error(400,"Conferma mancante")
                c.execute("UPDATE calendar_events SET linked_practice_id=NULL,updated_at=?,updated_by=? WHERE id=?",(stamp,user["id"],event_id))
                calendar_add_history(c,event_id,user["id"],"Scollegamento pratica",str(event["linked_practice_id"] or ""),"",stamp)
        self.redirect(safe_return_path(form.get("return_to") or self.headers.get("Referer"),f"/calendario/{event_id}"))

    def calendar_comment_action(self,user,event_id,comment_id,action):
        with db() as c:
            row=c.execute("SELECT * FROM calendar_event_comments WHERE id=? AND event_id=?",(comment_id,event_id)).fetchone()
            if not row:return self.send_error(404)
            if row["user_id"]!=user["id"] and user["role"]!="admin":return self.send_error(403)
            stamp=now()
            if action=="modifica":
                message=self.form().get("message","").strip()[:2000]
                if not message:return self.calendar_event_detail(user,event_id,error="Il commento non può essere vuoto.")
                c.execute("UPDATE calendar_event_comments SET message=?,updated_at=? WHERE id=? AND deleted_at IS NULL",(message,stamp,comment_id));calendar_add_history(c,event_id,user["id"],"Modifica commento",row["message"],message,stamp)
            else:c.execute("UPDATE calendar_event_comments SET deleted_at=?,deleted_by=? WHERE id=?",(stamp,user["id"],comment_id));calendar_add_history(c,event_id,user["id"],"Eliminazione commento",row["message"],"",stamp)
        self.redirect(f"/calendario/{event_id}?tab=commenti")

    def calendar_trash(self,user):
        with db() as c:rows=c.execute("SELECT e.*,u.display_name FROM calendar_events e LEFT JOIN users u ON u.id=e.deleted_by WHERE e.deleted_at IS NOT NULL ORDER BY e.deleted_at DESC").fetchall()
        cards=''.join(f'''<article class="section"><h2>{esc(row['title'])}</h2><p class="sub">Eliminato {esc(row['deleted_at'].replace('T',' ')[:16])} da {esc(row['display_name'] or 'Sistema')}</p><div class="actions"><a class="btn ghost" href="/calendario/{row['id']}?tab=storico">Storico</a><form method="post" action="/calendario/{row['id']}/ripristina"><button class="btn">Ripristina</button></form>{f'<form method="post" action="/calendario/{row["id"]}/elimina-definitiva" onsubmit="return confirm(\'Conferma definitiva: i dati non saranno recuperabili. Continuare?\')"><input type="hidden" name="confirm" value="ELIMINA DEFINITIVAMENTE"><button class="btn danger-btn">Elimina definitivamente</button></form>' if user['role']=='admin' else ''}</div></article>''' for row in rows) or '<section class="section empty-state">Il cestino è vuoto.</section>'
        self.send_html(layout("Cestino calendario",f'<main class="wrap"><div class="titlebar"><h1>Cestino calendario</h1><a class="btn ghost" href="/calendario">Calendario</a></div><div class="grid">{cards}</div></main>',user))

    def cremation_schedule(self,user):
        q=parse_qs(urlparse(self.path).query)
        provenance_filter=(q.get("provenienza") or [""])[0].strip().upper()
        if provenance_filter not in PROVENANCE_LABELS:provenance_filter=""
        with db() as c:
            where="(deleted_at IS NULL OR deleted_at='') AND status='Ritirato' AND service_type='Cremazione singola'"
            args=[]
            if provenance_filter:
                where+=" AND provenance=?";args.append(provenance_filter)
            rows=c.execute(f"SELECT * FROM practices WHERE {where} ORDER BY date(COALESCE(NULLIF(pickup_date,''),created_at)) ASC,id ASC",args).fetchall()
            urn_ids={int(row[key]) for row in rows for key in ("urn_id","urn_id_2") if key in row.keys() and row[key]}
            urn_names={}
            if urn_ids:
                marks=','.join('?' for _ in urn_ids)
                urn_names={r["id"]:r["name"] for r in c.execute(f"SELECT id,name FROM urns WHERE id IN ({marks})",tuple(urn_ids))}
        def urn_cell(row):
            labels=[]
            for id_key,note_key in (("urn_id","urn_notes"),("urn_id_2","urn_notes_2")):
                label=urn_names.get(int(row[id_key])) if row[id_key] and int(row[id_key]) in urn_names else ""
                label=label or compact_text(row[note_key])
                if label and label not in labels:labels.append(label)
            if labels:return esc(" / ".join(labels))
            if row["send_catalog"]=="Si":return '<span class="badge tag-outline-orange">INVIARE CATALOGO</span>'
            return '<span class="sub">-</span>'
        table_rows=[]
        for row in rows:
            code=(row["provenance"] or "").strip().upper()
            label=PROVENANCE_LABELS.get(code,"")
            provenance_cell=f'{esc(code)} · {esc(label)}' if code and label else (esc(code) or '<span class="sub">-</span>')
            weight=(row["estimated_weight"] or "").strip()
            weight_cell=f'{esc(weight)} kg' if weight else '<span class="sub">-</span>'
            client=((row["owner_first_name"] or "")+" "+(row["owner_last_name"] or "")).strip() or row["owner_company"] or ""
            client_cell=esc(client) if client else '<span class="sub">-</span>'
            url=f'/pratiche/{row["id"]}'
            registered=row["cremation_registered"]=="Si"
            checkbox=f'''<label class="cremation-check" onclick="event.stopPropagation()"><input type="checkbox" data-practice-id="{row['id']}" {"checked" if registered else ""} onchange="toggleCremationRegistered(this)"> INSERITO</label>'''
            table_rows.append(f'''<tr class="practice-row-link {"cremation-row-done" if registered else ""}" {row_open_attrs(url,f'Apri pratica {row["practice_number"]}')}><td>{esc(row["animal_name"] or "Da inserire")}</td><td>{weight_cell}</td><td>{provenance_cell}</td><td>{self.tag_badges(row)}</td><td>{urn_cell(row)}</td><td>{client_cell}</td><td>{esc(date_it(row["pickup_date"] or row["created_at"]))}</td><td>{checkbox}</td></tr>''')
        table_body=''.join(table_rows) or '<tr><td colspan="8" class="sub">Nessuna cremazione singola in attesa.</td></tr>'
        filter_options='<option value="">Tutte le provenienze</option>'+''.join(f'<option value="{code}" {"selected" if provenance_filter==code else ""}>{esc(code)} · {esc(label)}</option>' for code,label in PROVENANCE_LABELS.items())
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Programma Cremazioni</h1><p class="sub">Cremazioni singole ancora da fare, ordinate dalla più vecchia data di recupero.</p></div></div><section class="balance-grid" style="grid-template-columns:max-content"><div class="balance-total"><small>Animali in attesa di cremazione singola</small><strong>{len(rows)}</strong></div></section><form class="section" method="get" style="margin-bottom:16px"><div class="fields"><div class="field"><label>Provenienza</label><select name="provenienza" onchange="this.form.submit()">{filter_options}</select></div></div></form><div class="tablebox"><table><thead><tr><th>Animale</th><th>Peso</th><th>Provenienza</th><th>Etichette</th><th>Urna</th><th>Cliente</th><th>Data recupero</th><th>Inserito</th></tr></thead><tbody>{table_body}</tbody></table></div></main>'''
        self.send_html(layout("Programma Cremazioni",body,user))

    def toggle_cremation_registered(self,user,pid):
        form=self.form()
        value="Si" if form.get("value")=="Si" else ""
        with db() as c:
            row=c.execute("SELECT id FROM practices WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",(pid,)).fetchone()
            if not row:return self.send_json({"ok":False,"error":"Pratica non trovata"},404)
            c.execute("UPDATE practices SET cremation_registered=? WHERE id=?",(value,pid))
        return self.send_json({"ok":True,"value":value})

    def balances(self,user):
        q=parse_qs(urlparse(self.path).query); today=datetime.now().date()
        date_from=(q.get("dal") or [(today-timedelta(days=6)).isoformat()])[0].strip()
        date_to=(q.get("al") or [today.isoformat()])[0].strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from): date_from=(today-timedelta(days=6)).isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to): date_to=today.isoformat()
        categories=[
            ("price_cremation","Cremazione",("price_cremation",)),("price_pickup","Ritiro",("price_pickup",)),("price_urn","Urna",("price_urn","price_urn_2")),
            ("price_delivery","Riconsegna",("price_delivery",)),("price_cast","Calco",("price_cast","price_cast_2","price_paw_cast","price_nose_cast")),("price_evening","Serale",("price_evening",)),
            ("price_night","Notturno",("price_night",)),("price_holiday","Festivo",("price_holiday",)),("price_accessories","Accessori",("price_accessories","price_accessories_2")),
            ("totale_calcolato","Entrate W",()),("totale_d","Entrate D",()),
            ("da_entrare","Da entrare W",()),("da_entrare_d","Da entrare D",()),
        ]
        category_map={key:label for key,label,_ in categories}; category_fields={key:fields for key,_,fields in categories}; selected=(q.get("voce") or [""])[0].strip()
        if selected not in category_map: selected=""
        with db() as c:
            rows=c.execute("""SELECT * FROM practices
                              WHERE (deleted_at IS NULL OR deleted_at='')
                                AND date(COALESCE(NULLIF(pickup_date,''),created_at)) BETWEEN date(?) AND date(?)
                              ORDER BY date(COALESCE(NULLIF(pickup_date,''),created_at)) DESC,id DESC""",(date_from,date_to)).fetchall()
        breakdown={key:sum(sum(money_value(row[field]) for field in fields) for row in rows) for key,_,fields in categories if fields}
        breakdown["totale_calcolato"]=sum(calculated_service_total(row) for row in rows if not (row["total_text"] or "").strip())
        breakdown["totale_d"]=sum(money_value(row["total_text"]) for row in rows if (row["total_text"] or "").strip())
        breakdown["da_entrare"]=sum(effective_total(row) for row in rows if (row["payment_status"] or "Da saldare")=="Da saldare")
        breakdown["da_entrare_d"]=sum(money_value(row["total_text"]) for row in rows if (row["payment_status"] or "Da saldare")!="Pagato" and (row["total_text"] or "").strip())
        grand_total=sum(effective_total(row) for row in rows if row["payment_status"]=="Pagato")
        shown_total=breakdown[selected] if selected else grand_total
        cards=''.join(f'<a class="balance-card {"active" if selected==key else ""}" href="/bilanci?dal={quote(date_from)}&al={quote(date_to)}&voce={quote(key)}"><small>{label}</small><strong>{money_it(breakdown[key])}</strong></a>' for key,label,_ in categories)
        table_rows=[]
        for row in rows:
            if selected=="da_entrare":
                if (row["payment_status"] or "Da saldare")!="Da saldare": continue
                amount=effective_total(row)
            elif selected=="da_entrare_d":
                if (row["payment_status"] or "Da saldare")=="Pagato" or not (row["total_text"] or "").strip(): continue
                amount=money_value(row["total_text"])
                if amount==0: continue
            elif selected=="totale_calcolato":
                if (row["total_text"] or "").strip(): continue
                amount=calculated_service_total(row)
                if amount==0: continue
            elif selected=="totale_d":
                if not (row["total_text"] or "").strip(): continue
                amount=money_value(row["total_text"])
                if amount==0: continue
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

    def balances_legacy_v2(self,user):
        q=parse_qs(urlparse(self.path).query); today=datetime.now().date()
        default_from=today-timedelta(days=6)
        date_from=(q.get("dal") or [default_from.isoformat()])[0].strip()
        date_to=(q.get("al") or [today.isoformat()])[0].strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from): date_from=default_from.isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to): date_to=today.isoformat()
        categories=[
            ("price_cremation","Cremazione",("price_cremation",)),("price_pickup","Ritiro",("price_pickup",)),("price_urn","Urna",("price_urn","price_urn_2")),
            ("price_delivery","Riconsegna",("price_delivery",)),("price_cast","Calco",("price_cast","price_cast_2","price_paw_cast","price_nose_cast")),("price_evening","Serale",("price_evening",)),
            ("price_night","Notturno",("price_night",)),("price_holiday","Festivo",("price_holiday",)),("price_accessories","Accessori",("price_accessories","price_accessories_2")),
            ("totale_calcolato","Entrate W",()),("totale_d","Entrate D",()),
            ("da_entrare","Da entrare W",()),("da_entrare_d","Da entrare D",()),
        ]
        category_map={key:label for key,label,_ in categories}; category_fields={key:fields for key,_,fields in categories}
        selected=(q.get("voce") or [""])[0].strip()
        if selected not in category_map: selected=""
        with db() as c:
            rows=c.execute("""SELECT * FROM practices WHERE (deleted_at IS NULL OR deleted_at='')
                              AND date(COALESCE(NULLIF(pickup_date,''),created_at)) BETWEEN date(?) AND date(?)
                              ORDER BY date(COALESCE(NULLIF(pickup_date,''),created_at)) DESC,id DESC""",(date_from,date_to)).fetchall()

        def amount_for(row,key):
            definitive=uses_total_d(row)
            if key=="totale_calcolato": return 0.0 if definitive else received_amount(row)
            if key=="totale_d": return received_amount(row) if definitive else 0.0
            if key=="da_entrare": return 0.0 if definitive else outstanding_amount(row)
            if key=="da_entrare_d": return outstanding_amount(row) if definitive else 0.0
            if key in category_fields:
                if definitive: return 0.0
                due=effective_total(row)
                ratio=min(1.0, received_amount(row)/due) if due else 0.0
                return sum(money_value(row[field]) for field in category_fields[key])*ratio
            return received_amount(row)

        breakdown={key:sum(amount_for(row,key) for row in rows) for key,_,_ in categories}
        shown_total=breakdown[selected] if selected else sum(received_amount(row) for row in rows)
        cards=''.join(f'<a class="balance-card {"active" if selected==key else ""}" href="/bilanci?dal={quote(date_from)}&al={quote(date_to)}&voce={quote(key)}"><small>{label}</small><strong>{money_it(breakdown[key])}</strong></a>' for key,label,_ in categories)
        table_rows=[]; chart_by_day={}
        for row in rows:
            amount=amount_for(row,selected)
            if amount <= 0: continue
            owner=((row["owner_first_name"] or "")+" "+(row["owner_last_name"] or "")).strip()
            effective_date=(row["pickup_date"] or row["created_at"] or "")[:10]
            chart_by_day[effective_date]=chart_by_day.get(effective_date,0.0)+amount
            url=f'/pratiche/{row["id"]}'
            row_label=f'Apri pratica {row["practice_number"]}'
            table_rows.append(f'<tr class="practice-row-link" {row_open_attrs(url,row_label)}><td>{esc(date_it(effective_date))}</td><td><a href="{url}"><b>{esc(row["practice_number"])}</b></a></td><td>{esc(owner)}</td><td>{esc(category_map.get(selected,"Entrata incassata"))}</td><td><b>{money_it(amount)}</b></td><td><a class="btn ghost" href="{url}">Apri</a></td></tr>')
        start_date=datetime.strptime(date_from,"%Y-%m-%d").date(); end_date=datetime.strptime(date_to,"%Y-%m-%d").date()
        chart_days=[]; cursor=start_date
        while cursor<=end_date:
            chart_days.append(cursor); cursor+=timedelta(days=1)
        chart=income_chart([chart_by_day.get(day.isoformat(),0.0) for day in chart_days],[day.strftime("%d/%m") for day in chart_days])
        table_body=''.join(table_rows) or '<tr><td colspan="6" class="sub">Nessun importo nel periodo selezionato.</td></tr>'
        options='<option value="">Tutte le entrate incassate</option>'+''.join(f'<option value="{key}" {"selected" if selected==key else ""}>{label}</option>' for key,label,_ in categories)
        subtitle="Risultati filtrati" if selected else "Denaro effettivamente incassato"
        body=f'''<main class="wrap balances-wrap"><div class="titlebar"><div><h1>Bilanci</h1><p class="sub">{subtitle} dal {esc(date_it(date_from))} al {esc(date_it(date_to))}</p></div><div class="balance-total"><small>{esc(category_map.get(selected,"Entrate totali"))}</small><strong>{money_it(shown_total)}</strong></div></div><section class="balance-grid">{cards}</section><section class="dashboard-panel balance-chart"><header><div><h2>Andamento nel periodo filtrato</h2><p>Il grafico si aggiorna con date e voce selezionate.</p></div></header>{chart}</section><section class="tablebox balance-table"><table><thead><tr><th>Data</th><th>Pratica</th><th>Cliente</th><th>Voce</th><th>Importo</th><th></th></tr></thead><tbody>{table_body}</tbody></table></section><section class="search-after-results"><h2>Filtra bilanci</h2><form class="section" method="get"><div class="fields"><div class="field"><label>Dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field full"><label>Voce</label><select name="voce">{options}</select></div></div><button class="btn" style="margin-top:12px">Applica filtri</button><a class="btn ghost" style="margin-top:12px" href="/bilanci">Ultimi 7 giorni</a></form></section></main>'''
        self.send_html(layout("Bilanci",body,user))

    def balances_v2(self,user):
        q=parse_qs(urlparse(self.path).query); today=datetime.now().date(); default_from=today-timedelta(days=6)
        date_from=(q.get("dal") or [default_from.isoformat()])[0].strip(); date_to=(q.get("al") or [today.isoformat()])[0].strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from): date_from=default_from.isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to): date_to=today.isoformat()
        categories=[
            ("price_cremation","Cremazione",("price_cremation",)),("price_pickup","Ritiro",("price_pickup",)),("price_urn","Urna",("price_urn","price_urn_2")),
            ("price_delivery","Riconsegna",("price_delivery",)),("price_cast","Calco",("price_cast","price_cast_2","price_paw_cast","price_nose_cast")),
            ("price_evening","Serale",("price_evening",)),("price_night","Notturno",("price_night",)),("price_holiday","Festivo",("price_holiday",)),
            ("price_accessories","Accessori",("price_accessories","price_accessories_2")),("totale_calcolato","Entrate W",()),
            ("totale_d","Entrate D",()),("da_entrare","Da entrare W",()),("da_entrare_d","Da entrare D",()),
        ]
        category_map={key:label for key,label,_ in categories}; category_fields={key:fields for key,_,fields in categories}
        selected=(q.get("voce") or [""])[0].strip(); selected=selected if selected in category_map else ""
        with db() as c:
            movements=c.execute("""SELECT m.id movement_id,m.payment_type,m.payment_channel,m.amount,m.paid_at,p.*
                                   FROM payment_movements m JOIN practices p ON p.id=m.practice_id
                                   WHERE (p.deleted_at IS NULL OR p.deleted_at='') AND date(m.paid_at) BETWEEN date(?) AND date(?)
                                   ORDER BY datetime(m.paid_at) DESC,m.id DESC""",(date_from,date_to)).fetchall()
            outstanding=c.execute("""SELECT * FROM practices WHERE (deleted_at IS NULL OR deleted_at='')
                                     AND date(COALESCE(NULLIF(pickup_date,''),created_at)) BETWEEN date(?) AND date(?)
                                     ORDER BY date(COALESCE(NULLIF(pickup_date,''),created_at)) DESC,id DESC""",(date_from,date_to)).fetchall()

        def movement_amount(row,key):
            amount=money_value(row["amount"]); is_d=row["payment_channel"]=="D"
            if key=="totale_calcolato": return 0.0 if is_d else amount
            if key=="totale_d": return amount if is_d else 0.0
            if key in ("da_entrare","da_entrare_d"): return 0.0
            return amount

        def component_amount(row,key):
            return sum(money_value(row[field]) for field in category_fields.get(key,()))

        practice_movements={}
        for row in movements:
            practice_id=row["id"]
            if practice_id not in practice_movements:
                practice_movements[practice_id]={"row":None,"net":0.0}
            movement_value=money_value(row["amount"])
            if movement_value>0 and practice_movements[practice_id]["row"] is None:
                practice_movements[practice_id]["row"]=row
            practice_movements[practice_id]["net"]+=movement_value
        component_rows=[item["row"] for item in practice_movements.values() if item["row"] is not None and item["net"]>0.004]

        breakdown={key:0.0 for key,_,_ in categories}
        for key,_,_ in categories:
            if key=="da_entrare": breakdown[key]=sum(outstanding_amount(row) for row in outstanding if not uses_total_d(row))
            elif key=="da_entrare_d": breakdown[key]=sum(outstanding_amount(row) for row in outstanding if uses_total_d(row))
            elif category_fields.get(key): breakdown[key]=sum(component_amount(row,key) for row in component_rows)
            else: breakdown[key]=sum(movement_amount(row,key) for row in movements)
        shown_total=breakdown[selected] if selected else sum(money_value(row["amount"]) for row in movements)
        cards=''.join(f'<a class="balance-card {"active" if selected==key else ""}" href="/bilanci?dal={quote(date_from)}&al={quote(date_to)}&voce={quote(key)}"><small>{label}</small><strong>{money_it(breakdown[key])}</strong></a>' for key,label,_ in categories)
        type_labels={"acconto_ordinario":"Acconto W","saldo_ordinario":"Saldo W","acconto_d":"Acconto D","saldo_d":"Saldo D","rettifica":"Rettifica"}
        table_rows=[]; chart_by_day={}
        component_selected=bool(selected and category_fields.get(selected))
        if selected in ("da_entrare","da_entrare_d"):
            source=outstanding
        elif component_selected:
            source=component_rows
        else:
            source=movements
        for row in source:
            if selected=="da_entrare": amount=outstanding_amount(row) if not uses_total_d(row) else 0.0
            elif selected=="da_entrare_d": amount=outstanding_amount(row) if uses_total_d(row) else 0.0
            elif component_selected: amount=component_amount(row,selected)
            else: amount=movement_amount(row,selected)
            if abs(amount)<0.005: continue
            owner=((row["owner_first_name"] or "")+" "+(row["owner_last_name"] or "")).strip(); url=f'/pratiche/{row["id"]}'
            economic_date=((row["pickup_date"] or row["created_at"] or "")[:10] if selected in ("da_entrare","da_entrare_d") else (row["paid_at"] or "")[:10])
            movement_label="Rimanenza prevista" if selected in ("da_entrare","da_entrare_d") else f'{category_map[selected]} · valore inserito' if component_selected else type_labels.get(row["payment_type"],row["payment_type"] or "Incasso")
            chart_by_day[economic_date]=chart_by_day.get(economic_date,0.0)+amount
            effective_label=("TOTALE D" if uses_total_d(row) else "Totale W")+f" {money_it(effective_total(row))}"
            table_rows.append(f'''<tr class="practice-row-link" {row_open_attrs(url,f'Apri pratica {row["practice_number"]}')}><td>{esc(date_it(economic_date))}</td><td>{esc(movement_label)}</td><td><a href="{url}"><b>{esc(row["practice_number"])}</b></a></td><td>{esc(owner)}</td><td><b>{money_it(amount)}</b></td><td>{esc(effective_label)}</td><td>{money_it(money_value(row["deposit"]))}</td><td>{money_it(outstanding_amount(row))}</td><td>{esc(row["payment_status"] or "Da saldare")}</td><td><a class="btn ghost" href="{url}">Apri</a></td></tr>''')
        start_date=datetime.strptime(date_from,"%Y-%m-%d").date(); end_date=datetime.strptime(date_to,"%Y-%m-%d").date(); chart_days=[]; cursor=start_date
        while cursor<=end_date: chart_days.append(cursor); cursor+=timedelta(days=1)
        chart=income_chart([chart_by_day.get(day.isoformat(),0.0) for day in chart_days],[day.strftime("%d/%m") for day in chart_days])
        table_body=''.join(table_rows) or '<tr><td colspan="10" class="sub">Nessun importo nel periodo selezionato.</td></tr>'
        options='<option value="">Tutti gli incassi</option>'+''.join(f'<option value="{key}" {"selected" if selected==key else ""}>{label}</option>' for key,label,_ in categories)
        if component_selected:
            subtitle="Valori integrali inseriti nelle pratiche con incassi nel periodo"
        elif selected in ("da_entrare","da_entrare_d"):
            subtitle="Importi ancora da incassare nel periodo operativo"
        else:
            subtitle="Risultati filtrati per data economica"
        chart_description="Ogni voce è conteggiata una sola volta per pratica, senza ripartizioni proporzionali." if component_selected else "Incassi registrati nella loro data effettiva."
        amount_heading="Valore inserito" if component_selected else "Importo"
        body=f'''<main class="wrap balances-wrap"><div class="titlebar"><div><h1>Bilanci</h1><p class="sub">{subtitle} dal {esc(date_it(date_from))} al {esc(date_it(date_to))}</p></div><div class="balance-total"><small>{esc(category_map.get(selected,"Entrate totali"))}</small><strong>{money_it(shown_total)}</strong></div></div><section class="balance-grid">{cards}</section><section class="dashboard-panel balance-chart"><header><div><h2>Andamento nel periodo filtrato</h2><p>{chart_description}</p></div></header>{chart}</section><section class="tablebox balance-table"><div class="section-collapse-head"><h2>Elenco pratiche</h2><button type="button" class="collapse-toggle" aria-expanded="true" onclick="toggleCollapsibleSection(this)">−</button></div><div class="collapsible-body"><table><thead><tr><th>Data economica</th><th>Movimento</th><th>Pratica</th><th>Cliente</th><th>{amount_heading}</th><th>Totale</th><th>Acconto</th><th>Rimanenza</th><th>Stato</th><th></th></tr></thead><tbody>{table_body}</tbody></table></div></section><section class="search-after-results"><h2>Filtra bilanci</h2><form class="section" method="get"><div class="fields"><div class="field"><label>Dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field full"><label>Voce</label><select name="voce">{options}</select></div></div><button class="btn" style="margin-top:12px">Applica filtri</button><a class="btn ghost" style="margin-top:12px" href="/bilanci">Ultimi 7 giorni</a></form></section></main>'''
        self.send_html(layout("Bilanci",body,user))

    def payment_overview_legacy(self,user,kind):
        specs={
            "da-saldare":("Da saldare",lambda row: outstanding_amount(row)>0,lambda row: outstanding_amount(row)),
            "acconti":("Acconti",lambda row: received_amount(row)>0 and outstanding_amount(row)>0,lambda row: received_amount(row)),
            "pagati":("Pagati",lambda row: (row["payment_status"] or "")=="Pagato",lambda row: received_amount(row)),
        }
        title,include,amount_for=specs[kind]
        with db() as c:
            rows=c.execute("SELECT * FROM practices WHERE deleted_at IS NULL OR deleted_at='' ORDER BY updated_at DESC,id DESC").fetchall()
        groups=[(False,title,"#3b82f6"),(True,f"{title} D","#f59e0b")]; sections=[]
        for is_d,label,color in groups:
            selected=[row for row in rows if include(row) and uses_total_d(row)==is_d]; total=sum(amount_for(row) for row in selected)
            body=[]
            for row in selected:
                owner=((row["owner_first_name"] or "")+" "+(row["owner_last_name"] or "")).strip(); url=f'/pratiche/{row["id"]}'
                body.append(f'''<tr class="practice-row-link" {row_open_attrs(url,f'Apri pratica {row["practice_number"]}')}><td><a href="{url}"><b>{esc(row["practice_number"])}</b></a></td><td>{esc(row["animal_name"] or "")}</td><td>{esc(owner)}</td><td>{money_it(effective_total(row))}</td><td>{money_it(money_value(row["deposit"]))}</td><td><b>{money_it(amount_for(row))}</b></td><td>{esc(row["payment_status"] or "Da saldare")}</td><td><a class="btn ghost" href="{url}">Apri</a></td></tr>''')
            table=''.join(body) or '<tr><td colspan="8" class="sub">Nessuna pratica in questa categoria.</td></tr>'
            sections.append(f'''<section class="dashboard-panel" style="margin-bottom:20px;border-top:4px solid {color}"><header><div><h2>{esc(label)}</h2><p>{len(selected)} pratiche</p></div><strong>{money_it(total)}</strong></header><div class="tablebox"><table><thead><tr><th>Pratica</th><th>Animale</th><th>Cliente</th><th>Totale</th><th>Acconto</th><th>{esc(title)}</th><th>Stato</th><th></th></tr></thead><tbody>{table}</tbody></table></div></section>''')
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Pagamenti · {esc(title)}</h1><p class="sub">Separazione tra Totale W e Totale D (contanti).</p></div><a class="btn ghost" href="/">Dashboard</a></div>{''.join(sections)}</main>'''
        self.send_html(layout(f"Pagamenti · {title}",body,user))

    def payment_overview(self,user,kind):
        titles={"da-saldare":"Da saldare","acconti":"Acconti","pagati":"Pagati"};title=titles[kind]
        q=parse_qs(urlparse(self.path).query);today=datetime.now().date()
        period,date_from,date_to=dashboard_period_bounds((q.get("periodo") or ["oggi"])[0],today)
        raw_from=(q.get("dal") or [date_from.isoformat()])[0];raw_to=(q.get("al") or [date_to.isoformat()])[0]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",raw_from):date_from=datetime.strptime(raw_from,"%Y-%m-%d").date()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",raw_to):date_to=datetime.strptime(raw_to,"%Y-%m-%d").date()
        with db() as c:
            if kind=="da-saldare":
                rows=c.execute("SELECT *,NULL dashboard_amount,NULL dashboard_channel,NULL dashboard_paid_at FROM practices WHERE (deleted_at IS NULL OR deleted_at='') AND COALESCE(payment_status,'Da saldare')!='Pagato' ORDER BY updated_at DESC,id DESC").fetchall()
                rows=[row for row in rows if outstanding_amount(row)>0]
            else:
                prefix="acconto_%" if kind=="acconti" else "saldo_%"
                rows=c.execute("""SELECT p.*,SUM(m.amount) dashboard_amount,m.payment_channel dashboard_channel,MAX(m.paid_at) dashboard_paid_at
                                  FROM payment_movements m JOIN practices p ON p.id=m.practice_id
                                  WHERE (p.deleted_at IS NULL OR p.deleted_at='') AND m.amount>0 AND m.payment_type LIKE ?
                                  AND date(m.paid_at) BETWEEN date(?) AND date(?)
                                  GROUP BY p.id,m.payment_channel ORDER BY dashboard_paid_at DESC,p.id DESC""",
                               (prefix,date_from.isoformat(),date_to.isoformat())).fetchall()
        def amount_for(row):return outstanding_amount(row) if kind=="da-saldare" else money_value(row["dashboard_amount"])
        def row_is_d(row):return uses_total_d(row) if kind=="da-saldare" else row["dashboard_channel"]=="D"
        groups=[(False,title,"#3b82f6"),(True,f"{title} D","#f59e0b")];sections=[]
        for is_d,label,color in groups:
            selected=[row for row in rows if row_is_d(row)==is_d];total=sum(amount_for(row) for row in selected);table_rows=[]
            for row in selected:
                owner=((row["owner_first_name"] or "")+" "+(row["owner_last_name"] or "")).strip();url=f'/pratiche/{row["id"]}?return_to={quote(self.path,safe="")}'
                economic_date=date_it(row["dashboard_paid_at"]) if kind!="da-saldare" else "Aperta"
                table_rows.append(f'''<tr class="practice-row-link" {row_open_attrs(url,f'Apri pratica {row["practice_number"]}')}><td>{esc(economic_date)}</td><td><a href="{url}"><b>{esc(row["practice_number"])}</b></a></td><td>{esc(row["animal_name"] or "")}</td><td>{esc(owner)}</td><td>{money_it(effective_total(row))}</td><td>{money_it(money_value(row["deposit"]))}</td><td><b>{money_it(amount_for(row))}</b></td><td>{esc(row["payment_status"] or "Da saldare")}</td><td><a class="btn ghost" href="{url}">Apri</a></td></tr>''')
            table=''.join(table_rows) or '<tr><td colspan="9" class="sub">Nessuna pratica in questa categoria.</td></tr>'
            sections.append(f'''<section class="dashboard-panel" style="margin-bottom:20px;border-top:4px solid {color}"><header><div><h2>{esc(label)}</h2><p>{len(selected)} pratiche</p></div><strong>{money_it(total)}</strong></header><div class="tablebox"><table><thead><tr><th>Data economica</th><th>Pratica</th><th>Animale</th><th>Cliente</th><th>Totale</th><th>Acconto</th><th>{esc(title)}</th><th>Stato</th><th></th></tr></thead><tbody>{table}</tbody></table></div></section>''')
        period_note="Tutte le rimanenze attualmente aperte: non esiste una scadenza di saldo." if kind=="da-saldare" else f"Incassi registrati dal {date_it(date_from.isoformat())} al {date_it(date_to.isoformat())}."
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Pagamenti · {esc(title)}</h1><p class="sub">Separazione tra Totale W e Totale D (contanti). {esc(period_note)}</p></div><a class="btn ghost" href="/?pratiche_periodo=oggi&amp;pagamenti_periodo={period}">Dashboard</a></div>{''.join(sections)}</main>'''
        self.send_html(layout(f"Pagamenti · {title}",body,user))

    def orders_page_legacy(self,user):
        q=parse_qs(urlparse(self.path).query);date_from=(q.get("dal") or [""])[0].strip();date_to=(q.get("al") or [""])[0].strip();status=(q.get("stato") or [""])[0].strip();draft_id=(q.get("bozza") or [""])[0].strip()
        statuses=("Bozza","Invio in corso","Inviato","Fallito")
        if status not in statuses:status=""
        where=["o.archived_at IS NULL"];args=[]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from):where.append("date(o.created_at)>=date(?)");args.append(date_from)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to):where.append("date(o.created_at)<=date(?)");args.append(date_to)
        if status:where.append("o.status=?");args.append(status)
        with db() as c:
            recipient_row=c.execute("SELECT value FROM settings WHERE key='order_recipient_email'").fetchone();recipient=recipient_row["value"] if recipient_row else ""
            rows=c.execute(f"""SELECT o.*,u.display_name operator_name FROM email_orders o JOIN users u ON u.id=o.operator_id
                               WHERE {' AND '.join(where)} ORDER BY o.created_at DESC,o.id DESC""",args).fetchall()
            draft=c.execute("SELECT * FROM email_orders WHERE id=? AND status='Bozza' AND archived_at IS NULL",(int(draft_id),)).fetchone() if draft_id.isdigit() else None
        quantity=draft["quantity"] if draft else 1;notes=draft["notes"] if draft else "";preview=water_order_body(quantity,notes)
        options='<option value="">Tutti gli esiti</option>'+''.join(f'<option value="{item}" {"selected" if status==item else ""}>{item}</option>' for item in statuses)
        history=[]
        for row in rows:
            badge={"Bozza":"tag-yellow","Invio in corso":"tag-blue","Inviato":"tag-green","Fallito":"tag-red"}.get(row["status"],"")
            history.append(f'''<tr><td>{esc(date_it(row["created_at"]))}<br><small>{esc((row["created_at"] or "")[11:16])}</small></td><td>{esc(row["operator_name"])}</td><td>{row["quantity"]}</td><td>{esc(row["recipient"])}</td><td><span class="badge {badge}">{esc(row["status"])}</span></td><td><a class="btn ghost" href="/ordini/{row["id"]}">Apri</a></td></tr>''')
        table=''.join(history) or '<tr><td colspan="6" class="sub">Nessun ordine trovato.</td></tr>'
        invalid_recipient=not valid_email_address(recipient)
        warning='<div class="flash warning">Configura un indirizzo valido in <a href="/impostazioni"><b>Impostazioni</b></a> prima di inviare.</div>' if invalid_recipient else ''
        body=f'''<main class="wrap orders-wrap"><div class="titlebar"><div><h1>Ordini</h1><p class="sub">Invio ordini tramite l'account email aziendale.</p></div></div>{warning}<section class="section order-compose"><h2>Ordine acqua</h2><form method="post" action="/ordini/invia" data-recipient="{esc(recipient)}" onsubmit="return confirmOrderSubmission(this)"><input type="hidden" name="confirm_send" value=""><input type="hidden" name="source_order_id" value="{draft['id'] if draft else ''}"><div class="fields"><div class="field"><label>Numero boccioni</label><input type="number" name="quantity" min="1" step="1" value="{quantity}" required inputmode="numeric" oninput="updateOrderPreview(this.form)"></div><div class="field"><label>Destinatario attualmente configurato</label><input value="{esc(recipient)}" readonly></div><div class="field full"><label>Note opzionali</label><textarea name="notes" maxlength="2000" oninput="updateOrderPreview(this.form)">{esc(notes)}</textarea></div><div class="field full"><label>Oggetto email</label><input name="subject_preview" value="{esc(ORDER_EMAIL_SUBJECT)}" readonly></div><div class="field full"><label>Anteprima del testo della mail</label><pre class="order-preview" aria-live="polite">{esc(preview)}</pre></div></div><button class="btn" {'disabled' if invalid_recipient else ''}>Invia ordine</button></form></section><section class="search-after-results"><h2>Storico ordini</h2><form class="section" method="get"><div class="fields"><div class="field"><label>Dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field"><label>Esito</label><select name="stato">{options}</select></div></div><button class="btn" style="margin-top:12px">Filtra</button><a class="btn ghost" style="margin-top:12px" href="/ordini">Pulisci</a></form></section><div class="tablebox"><table><thead><tr><th>Data e ora</th><th>Operatore</th><th>Quantità</th><th>Destinatario</th><th>Esito</th><th></th></tr></thead><tbody>{table}</tbody></table></div></main>'''
        self.send_html(layout("Ordini",body,user))

    def order_confirmation_modal(self):
        return '''<div class="order-modal" id="orderConfirmModal" hidden role="dialog" aria-modal="true" aria-labelledby="orderConfirmTitle"><div class="order-modal-card"><h2 id="orderConfirmTitle">Conferma ordine</h2><p class="sub">Controlla i dati prima dell'invio.</p><div class="kvs"><div class="kv"><small>Quantità</small><b data-order-confirm="quantity"></b></div><div class="kv"><small>Destinatario</small><b data-order-confirm="recipient"></b></div><div class="kv"><small>Oggetto</small><b data-order-confirm="subject"></b></div></div><h3>Anteprima messaggio</h3><pre class="order-preview" data-order-confirm="preview"></pre><div class="actions"><button class="btn ghost" type="button" onclick="closeOrderConfirmation()">Annulla</button><button class="btn" type="button" data-order-confirm-button onclick="confirmAndSubmitOrder()">Conferma e invia</button></div></div></div>'''

    def orders_page(self,user):
        q=parse_qs(urlparse(self.path).query);draft_id=(q.get("bozza") or [""])[0].strip();result=(q.get("esito") or [""])[0].strip();order_id=(q.get("ordine") or [""])[0].strip()
        with db() as c:
            settings=order_email_settings(c)
            draft=c.execute("SELECT * FROM email_orders WHERE id=? AND status='Bozza' AND archived_at IS NULL",(int(draft_id),)).fetchone() if draft_id.isdigit() else None
            recent=c.execute("""SELECT o.*,u.display_name operator_name FROM email_orders o JOIN users u ON u.id=o.operator_id
                                WHERE o.archived_at IS NULL ORDER BY o.created_at DESC,o.id DESC LIMIT 5""").fetchall()
            last_sent=c.execute("SELECT * FROM email_orders WHERE status='Inviato' AND archived_at IS NULL ORDER BY sent_at DESC,id DESC LIMIT 1").fetchone()
            failed=c.execute("SELECT error_message FROM email_orders WHERE id=? AND status='Fallito'",(int(order_id),)).fetchone() if order_id.isdigit() else None
        requested=(q.get("quantita") or [""])[0]
        try:quantity=int(requested)
        except (TypeError,ValueError):quantity=draft["quantity"] if draft else 1
        quantity=max(1,min(999,quantity));recipient=settings["order_recipient_email"].strip();subject,preview=render_order_email(987654,settings);preview_template=preview.replace("987654","__QUANTITY__")
        invalid=not valid_email_address(recipient);is_admin=user["role"]=="admin"
        if result=="inviato":flash='<div class="flash">Ordine inviato correttamente.</div>'
        elif result=="fallito":flash=f'<div class="flash warning"><b>Invio non riuscito.</b> {esc((failed["error_message"] if failed else "Riprova tra poco."))}</div>'
        else:flash=""
        if invalid:flash+='<div class="flash warning">Il destinatario ordini non è configurato correttamente. '+('<a href="/ordini/impostazioni"><b>Apri le impostazioni</b></a>.' if is_admin else 'Contatta un amministratore.')+'</div>'
        rows=[]
        for row in recent:
            badge={"Bozza":"tag-yellow","Invio in corso":"tag-blue","Inviato":"tag-green","Fallito":"tag-red"}.get(row["status"],"")
            rows.append(f'''<tr><td>{esc(date_it(row["created_at"]))}<br><small>{esc((row["created_at"] or "")[11:16])}</small></td><td>{row["quantity"]}</td><td>{esc(row["recipient"])}</td><td><span class="badge {badge}">{esc(row["status"])}</span></td><td><a class="btn ghost" href="/ordini/{row["id"]}">Apri</a></td></tr>''')
        table=''.join(rows) or '<tr><td colspan="5" class="sub">Nessun ordine registrato.</td></tr>'
        last=(f'Ultimo ordine inviato: <b>{esc(date_it(last_sent["sent_at"]))} alle {esc((last_sent["sent_at"] or "")[11:16])}</b> · {last_sent["quantity"]} boccioni' if last_sent else 'Nessun ordine ancora inviato')
        settings_link='<a class="btn ghost" href="/ordini/impostazioni">Modifica impostazioni</a>' if is_admin else ''
        body=f'''<main class="wrap orders-wrap"><section class="section water-order-card"><h1>Ordina boccioni d’acqua</h1><p class="sub">Seleziona la quantità e invia l’ordine al fornitore</p>{flash}<form method="post" action="/ordini/invia" data-recipient="{esc(recipient)}" data-subject="{esc(subject)}" data-preview="{esc(preview_template)}" onsubmit="return openOrderConfirmation(this,event)"><input type="hidden" name="confirm_send"><input type="hidden" name="source_order_id" value="{draft['id'] if draft else ''}"><div class="quantity-stepper"><button class="btn ghost" type="button" aria-label="Diminuisci quantità" onclick="adjustOrderQuantity(this.form,-1)">−</button><input aria-label="Numero boccioni" type="number" name="quantity" min="1" max="999" step="1" value="{quantity}" inputmode="numeric" required onblur="normalizeOrderQuantity(this,this.value)"><button class="btn ghost" type="button" aria-label="Aumenta quantità" onclick="adjustOrderQuantity(this.form,1)">+</button></div><div class="quick-quantities" aria-label="Quantità rapide"><button class="btn ghost" type="button" onclick="setOrderQuantity(this.form,3)">3 boccioni</button><button class="btn ghost" type="button" onclick="setOrderQuantity(this.form,5)">5 boccioni</button><button class="btn ghost" type="button" onclick="setOrderQuantity(this.form,10)">10 boccioni</button></div><button class="btn order-now" {'disabled' if invalid else ''}>Ordina adesso</button></form><p class="last-order sub">{last}</p><div class="order-secondary-actions">{settings_link}</div></section><section class="recent-orders"><div class="titlebar"><div><h2>Ultimi ordini</h2><p class="sub">Gli ultimi 5 ordini registrati.</p></div><a class="btn ghost" href="/ordini/storico">Vedi tutti gli ordini</a></div><div class="tablebox"><table><thead><tr><th>Data e ora</th><th>Quantità</th><th>Destinatario</th><th>Stato</th><th></th></tr></thead><tbody>{table}</tbody></table></div></section>{self.order_confirmation_modal()}</main>'''
        self.send_html(layout("Ordini",body,user))

    def orders_history_page(self,user):
        q=parse_qs(urlparse(self.path).query);date_from=(q.get("dal") or [""])[0].strip();date_to=(q.get("al") or [""])[0].strip();status=(q.get("stato") or [""])[0].strip();statuses=("Bozza","Invio in corso","Inviato","Fallito")
        if status not in statuses:status=""
        where=["o.archived_at IS NULL"];args=[]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from):where.append("date(o.created_at)>=date(?)");args.append(date_from)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to):where.append("date(o.created_at)<=date(?)");args.append(date_to)
        if status:where.append("o.status=?");args.append(status)
        with db() as c:
            settings=order_email_settings(c);rows=c.execute(f"""SELECT o.*,u.display_name operator_name FROM email_orders o JOIN users u ON u.id=o.operator_id WHERE {' AND '.join(where)} ORDER BY o.created_at DESC,o.id DESC""",args).fetchall()
        recipient=settings["order_recipient_email"];subject,preview=render_order_email(987654,settings);preview=preview.replace("987654","__QUANTITY__")
        options='<option value="">Tutti gli esiti</option>'+''.join(f'<option value="{item}" {"selected" if status==item else ""}>{item}</option>' for item in statuses);table_rows=[]
        for row in rows:
            badge={"Bozza":"tag-yellow","Invio in corso":"tag-blue","Inviato":"tag-green","Fallito":"tag-red"}.get(row["status"],"");error=esc(row["error_message"] or "-")
            duplicate=f'''<form method="post" action="/ordini/{row['id']}/duplica"><button class="btn ghost">Duplica</button></form>'''
            resend=f'''<form method="post" action="/ordini/{row['id']}/reinvia" data-recipient="{esc(recipient)}" data-subject="{esc(subject)}" data-preview="{esc(preview)}" onsubmit="return openOrderConfirmation(this,event)"><input type="hidden" name="confirm_send"><input type="hidden" name="quantity" value="{row['quantity']}"><button class="btn">Reinvia</button></form>''' if row["status"]=="Fallito" else ""
            table_rows.append(f'''<tr><td>{esc(date_it(row["created_at"]))}<br><small>{esc((row["created_at"] or "")[11:16])}</small></td><td>{row["quantity"]}</td><td>{esc(row["recipient"])}</td><td>{esc(row["subject"])}</td><td>{esc(row["operator_name"])}</td><td><span class="badge {badge}">{esc(row["status"])}</span></td><td>{error}</td><td><div class="actions"><a class="btn ghost" href="/ordini/{row['id']}">Apri</a>{duplicate}{resend}</div></td></tr>''')
        table=''.join(table_rows) or '<tr><td colspan="8" class="sub">Nessun ordine trovato.</td></tr>'
        body=f'''<main class="wrap orders-wrap"><div class="titlebar"><div><h1>Storico ordini</h1><p class="sub">Consulta, duplica o riprova gli ordini falliti.</p></div><a class="btn ghost" href="/ordini">Torna a Ordine acqua</a></div><form class="section" method="get"><div class="fields"><div class="field"><label>Dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field"><label>Esito</label><select name="stato">{options}</select></div></div><div class="actions"><button class="btn">Filtra</button><a class="btn ghost" href="/ordini/storico">Pulisci</a></div></form><div class="tablebox"><table><thead><tr><th>Data e ora</th><th>Quantità</th><th>Destinatario</th><th>Oggetto</th><th>Operatore</th><th>Stato</th><th>Errore</th><th></th></tr></thead><tbody>{table}</tbody></table></div>{self.order_confirmation_modal()}</main>'''
        self.send_html(layout("Storico ordini",body,user))

    def order_settings_page(self,user,draft=None,error=""):
        if user["role"]!="admin":return self.send_error(403,"Solo gli amministratori possono modificare le impostazioni degli ordini.")
        with db() as c:settings=order_email_settings(c)
        if draft is not None:settings={**settings,**draft}
        error_html=f'<div class="flash warning">{esc(error)}</div>' if error else ''
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Impostazioni ordine acqua</h1><p class="sub">Questi dati vengono applicati automaticamente a ogni nuovo ordine.</p></div><a class="btn ghost" href="/ordini">Torna agli ordini</a></div>{error_html}<section class="section admin-order-settings"><form method="post" action="/ordini/impostazioni"><div class="fields"><div class="field full"><label>Email destinatario ordini</label><input type="email" name="order_recipient_email" value="{esc(settings['order_recipient_email'])}" required></div><div class="field full"><label>Oggetto email</label><input name="order_email_subject" value="{esc(settings['order_email_subject'])}" maxlength="200" required></div><div class="field full"><label>Testo email</label><textarea name="order_email_template" maxlength="10000" required>{esc(settings['order_email_template'])}</textarea><small class="sub">Inserisci <b>{{{{quantita}}}}</b> nel punto in cui deve apparire la quantità. Sono disponibili anche {{{{note_predefinite}}}}, {{{{firma}}}}, {{{{nome_mittente}}}} e {{{{telefono}}}}.</small></div><div class="field"><label>Firma finale</label><input name="order_email_signature" value="{esc(settings['order_email_signature'])}" maxlength="200" required></div><div class="field"><label>Nome mittente</label><input name="order_sender_name" value="{esc(settings['order_sender_name'])}" maxlength="200" required></div><div class="field"><label>Numero di telefono (opzionale)</label><input name="order_phone" value="{esc(settings['order_phone'])}" maxlength="100"></div><div class="field full"><label>Note predefinite (opzionali)</label><textarea name="order_default_notes" maxlength="2000">{esc(settings['order_default_notes'])}</textarea></div><div class="field full"><label>Mittente tecnico</label><input value="info@petparadisempoli.com" readonly><small class="sub">Le credenziali SMTP restano esclusivamente nelle variabili d’ambiente di Render e non sono mostrate qui.</small></div></div><button class="btn">Salva impostazioni</button></form></section></main>'''
        self.send_html(layout("Impostazioni ordine acqua",body,user))

    def order_detail_page(self,user,order_id):
        with db() as c:
            row=c.execute("SELECT o.*,u.display_name operator_name FROM email_orders o JOIN users u ON u.id=o.operator_id WHERE o.id=?",(order_id,)).fetchone()
            settings=order_email_settings(c)
        if not row:return self.send_error(404)
        result=(f'<div class="flash">Ordine inviato correttamente.</div>' if row["status"]=="Inviato" else f'<div class="flash warning"><b>Invio non riuscito:</b> {esc(row["error_message"] or "Errore non specificato")}</div>' if row["status"]=="Fallito" else "")
        actions=""
        if not row["archived_at"]:
            current_subject,current_preview=render_order_email(987654,settings);current_preview=current_preview.replace("987654","__QUANTITY__")
            resend=f'''<form method="post" action="/ordini/{order_id}/reinvia" data-recipient="{esc(settings['order_recipient_email'])}" data-subject="{esc(current_subject)}" data-preview="{esc(current_preview)}" onsubmit="return openOrderConfirmation(this,event)"><input type="hidden" name="confirm_send"><input type="hidden" name="quantity" value="{row['quantity']}"><button class="btn">Reinvia ordine</button></form>''' if row["status"]=="Fallito" else ""
            duplicate=f'''<form method="post" action="/ordini/{order_id}/duplica"><button class="btn ghost">Duplica ordine</button></form>'''
            archive=f'''<form method="post" action="/ordini/{order_id}/archivia" onsubmit="return confirm('Archiviare questo ordine?')"><button class="btn ghost">Archivia</button></form>'''
            actions=f'<div class="actions">{resend}{duplicate}{archive}</div>'
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Ordine #{row["id"]}</h1><p class="sub">{esc(row["status"])} · {esc(date_it(row["created_at"]))} {esc((row["created_at"] or "")[11:16])}</p></div><a class="btn ghost" href="/ordini/storico">Torna allo storico</a></div>{result}<section class="section"><div class="kvs"><div class="kv"><small>Operatore</small><b>{esc(row["operator_name"])}</b></div><div class="kv"><small>Destinatario</small><b>{esc(row["recipient"])}</b></div><div class="kv"><small>Quantità</small><b>{row["quantity"]} boccioni</b></div><div class="kv"><small>Esito</small><b>{esc(row["status"])}</b></div><div class="kv"><small>Tentativi</small><b>{row["attempt_count"]}</b></div><div class="kv"><small>Inviato il</small><b>{esc((row["sent_at"] or "").replace("T"," ") or "-")}</b></div></div><h2 style="margin-top:20px">{esc(row["subject"])}</h2><pre class="order-preview">{esc(row["body"])}</pre>{actions}</section>{self.order_confirmation_modal()}</main>'''
        self.send_html(layout(f"Ordine #{order_id}",body,user))

    def _create_and_send_order(self,user,quantity,notes,parent_order_id=None):
        with db() as c:
            settings=order_email_settings(c);recipient=settings["order_recipient_email"].strip().lower()
        if not valid_email_address(recipient):return None,"L'indirizzo destinatario ordini è vuoto o non valido. Configuralo nelle Impostazioni."
        subject,body=render_order_email(quantity,settings,notes);stamp=now()
        with db() as c:
            cur=c.execute("""INSERT INTO email_orders(order_type,quantity,notes,recipient,subject,body,status,operator_id,parent_order_id,attempt_count,created_at,updated_at)
                             VALUES('water',?,?,?,?,?,'Invio in corso',?,?,1,?,?)""",(quantity,notes,recipient,subject,body,user["id"],parent_order_id,stamp,stamp));order_id=cur.lastrowid
        try:
            send_email(recipient,subject,body,from_name=settings["order_sender_name"])
        except (EmailConfigurationError,EmailDeliveryError) as exc:
            message=str(exc)[:500]
            print(f"[ORDER_EMAIL] order_id={order_id} status=failed error={message}",flush=True)
            with db() as c:c.execute("UPDATE email_orders SET status='Fallito',error_message=?,updated_at=? WHERE id=?",(message,now(),order_id))
            return order_id,message
        except Exception as exc:
            message=f"Errore imprevisto durante l'invio email: {type(exc).__name__}."
            print(f"[ORDER_EMAIL] order_id={order_id} status=failed error={message}",flush=True)
            with db() as c:c.execute("UPDATE email_orders SET status='Fallito',error_message=?,updated_at=? WHERE id=?",(message,now(),order_id))
            return order_id,message
        sent=now()
        with db() as c:c.execute("UPDATE email_orders SET status='Inviato',sent_at=?,error_message=NULL,updated_at=? WHERE id=?",(sent,sent,order_id))
        return order_id,""

    def send_water_order(self,user):
        form=self.form()
        if form.get("confirm_send")!="SI":return self.error_page("Conferma mancante","L'email non è stata inviata perché manca la conferma esplicita.","/ordini")
        try:quantity=int(form.get("quantity",0))
        except (TypeError,ValueError):quantity=0
        if quantity<1:return self.error_page("Quantità non valida","Il numero di boccioni deve essere almeno 1.","/ordini")
        if quantity>999:return self.error_page("Quantità non valida","Il numero di boccioni è troppo elevato.","/ordini")
        parent=int(form["source_order_id"]) if str(form.get("source_order_id","")).isdigit() else None
        order_id,error=self._create_and_send_order(user,quantity,"",parent)
        if order_id and error:return self.redirect(f"/ordini?esito=fallito&ordine={order_id}&quantita={quantity}")
        if order_id:return self.redirect(f"/ordini?esito=inviato&ordine={order_id}")
        return self.error_page("Invio non disponibile",error,"/ordini")

    def order_action(self,user,order_id,action):
        with db() as c:row=c.execute("SELECT * FROM email_orders WHERE id=? AND archived_at IS NULL",(order_id,)).fetchone()
        if not row:return self.send_error(404)
        if action=="archivia":
            with db() as c:c.execute("UPDATE email_orders SET archived_at=?,updated_at=? WHERE id=?",(now(),now(),order_id))
            return self.redirect("/ordini")
        if action=="duplica":
            stamp=now()
            with db() as c:
                cur=c.execute("""INSERT INTO email_orders(order_type,quantity,notes,recipient,subject,body,status,operator_id,parent_order_id,created_at,updated_at)
                                 VALUES(?,?,?,?,?,?,'Bozza',?,?,?,?)""",(row["order_type"],row["quantity"],row["notes"],row["recipient"],row["subject"],row["body"],user["id"],order_id,stamp,stamp))
            return self.redirect(f"/ordini?bozza={cur.lastrowid}")
        if row["status"]!="Fallito":return self.error_page("Reinvio non disponibile","È possibile reinviare soltanto un ordine fallito.",f"/ordini/{order_id}")
        form=self.form()
        if form.get("confirm_send")!="SI":return self.error_page("Conferma mancante","Il reinvio richiede una conferma esplicita.",f"/ordini/{order_id}")
        new_id,error=self._create_and_send_order(user,row["quantity"],row["notes"] or "",order_id)
        if new_id and error:return self.redirect(f"/ordini?esito=fallito&ordine={new_id}&quantita={row['quantity']}")
        if new_id:return self.redirect(f"/ordini?esito=inviato&ordine={new_id}")
        return self.error_page("Invio non disponibile",error,f"/ordini/{order_id}")

    def articles_page(self,user):
        with db() as c:
            articles=c.execute("SELECT * FROM articles WHERE active=1 ORDER BY id").fetchall()
            recent=c.execute("""SELECT o.created_at,a.name,u.display_name FROM article_orders o
                                JOIN articles a ON a.id=o.article_id JOIN users u ON u.id=o.ordered_by
                                ORDER BY o.created_at DESC,o.id DESC LIMIT 10""").fetchall()
        cards=''.join(f'''<article class="article-card"><div><span class="badge tag-outline-orange">Da ordinare</span><h2>{esc(item["name"])}</h2></div><p>Invia la richiesta di ordine al centro notifiche.</p><form method="post" action="/prodotti/{item["id"]}/ordina" onsubmit="return confirm('Inviare la richiesta per {esc(item["name"])}?')"><button class="btn">Ordina prodotto</button></form></article>''' for item in articles)
        history=''.join(f'<div class="event"><b>{esc(row["name"])}</b><br><small class="sub">Richiesto da {esc(row["display_name"])} · {esc((row["created_at"] or "").replace("T"," "))}</small></div>' for row in recent) or '<p class="sub">Nessuna richiesta inviata.</p>'
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Articoli</h1><p class="sub">Seleziona un articolo sotto la voce “Da ordinare”.</p></div></div><section class="article-grid">{cards}</section><section class="section" style="margin-top:20px"><h2>Ultime richieste</h2><div class="timeline">{history}</div></section></main>'''
        body=body.replace("<h1>Articoli</h1>","<h1>Prodotti</h1>").replace("Seleziona un articolo","Seleziona un prodotto")
        self.send_html(layout("Prodotti",body,user))

    def invoices_page(self,user):
        q=parse_qs(urlparse(self.path).query); term=(q.get("q") or [""])[0].strip(); date_from=(q.get("dal") or [""])[0].strip(); date_to=(q.get("al") or [""])[0].strip()
        where=["(deleted_at IS NULL OR deleted_at='')","COALESCE(invoice_number,'')<>''"]; args=[]
        if term:
            where.append("(invoice_number LIKE ? OR practice_number LIKE ? OR owner_first_name LIKE ? OR owner_last_name LIKE ? OR animal_name LIKE ?)");args.extend([f"%{term}%"]*5)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_from): where.append("date(invoice_date)>=date(?)");args.append(date_from)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_to): where.append("date(invoice_date)<=date(?)");args.append(date_to)
        with db() as c:
            rows=c.execute(f"SELECT * FROM practices WHERE {' AND '.join(where)} ORDER BY COALESCE(NULLIF(invoice_date,''),created_at) DESC,id DESC",args).fetchall()
            reminders=c.execute("SELECT id,practice_number,owner_first_name,owner_last_name,animal_name FROM practices WHERE (deleted_at IS NULL OR deleted_at='') AND make_invoice='Si' AND COALESCE(invoice_number,'')='' ORDER BY updated_at DESC").fetchall()
        table=[]
        for row in rows:
            owner=((row["owner_first_name"] or "")+" "+(row["owner_last_name"] or "")).strip();url=f'/pratiche/{row["id"]}'
            invoice_total=money_value(row["invoice_total"]) if "invoice_total" in row.keys() and row["invoice_total"] else effective_total(row)
            table.append(f'''<tr class="practice-row-link" {row_open_attrs(url,f'Apri pratica {row["practice_number"]}')}><td><b>{esc(row["invoice_number"])}</b></td><td>{esc(date_it(row["invoice_date"]))}</td><td><a href="{url}">{esc(row["practice_number"])}</a></td><td>{esc(owner)}</td><td>{esc(row["animal_name"] or "")}</td><td>{money_it(invoice_total)}</td><td><a class="btn ghost" href="{url}">Apri</a></td></tr>''')
        reminder_html=''.join(f'<a class="event" href="/pratiche/{row["id"]}"><b>{esc(row["practice_number"])}</b> · {esc(((row["owner_first_name"] or "")+" "+(row["owner_last_name"] or "")).strip())} · {esc(row["animal_name"] or "")}</a>' for row in reminders) or '<p class="sub">Nessuna fattura da ricordare.</p>'
        empty='<tr><td colspan="7" class="sub">Nessuna fattura trovata.</td></tr>'
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Fatture</h1><p class="sub">Ogni numero fattura identifica e apre la pratica collegata.</p></div></div><form class="section" method="get"><div class="fields"><div class="field full"><label>Numero fattura o pratica</label><input name="q" value="{esc(term)}" placeholder="Cerca per fattura, pratica, cliente o animale"></div><div class="field"><label>Dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Al</label><input type="date" name="al" value="{esc(date_to)}"></div></div><button class="btn" style="margin-top:12px">Cerca</button></form><section class="tablebox"><table><thead><tr><th>Fattura</th><th>Data</th><th>Pratica</th><th>Cliente</th><th>Animale</th><th>Totale</th><th></th></tr></thead><tbody>{''.join(table) or empty}</tbody></table></section><section class="section" style="margin-top:20px"><h2>Da fatturare</h2><div class="timeline">{reminder_html}</div></section></main>'''
        self.send_html(layout("Fatture",body,user))

    def urn_catalog_page(self,user):
        q=parse_qs(urlparse(self.path).query); term=(q.get("q") or [""])[0].strip(); material=(q.get("materiale") or [""])[0].strip(); availability=(q.get("disponibilita") or [""])[0].strip()
        category_options_map={"urne":"Urna","accessori":"Accessorio","calchi":"Calco"}
        category_param=(q.get("categoria") or ["urne"])[0].strip().lower()
        if category_param not in category_options_map:category_param="urne"
        category=category_options_map[category_param]
        where=["active=1","category=?"]; args=[category]
        if term:
            where.append("(name LIKE ? OR material LIKE ? OR COALESCE(internal_code,'') LIKE ?)"); args.extend([f"%{term}%"]*3)
        if material: where.append("material=?"); args.append(material)
        if availability=="disponibile": where.append("quantity>low_stock_threshold")
        elif availability=="bassa": where.append("quantity>0 AND quantity<=low_stock_threshold")
        elif availability=="esaurita": where.append("quantity<=0")
        with db() as c:
            urns=c.execute(f"SELECT * FROM urns WHERE {' AND '.join(where)} ORDER BY name",args).fetchall()
            all_urns=c.execute("SELECT * FROM urns WHERE active=1 AND category=?",(category,)).fetchall()
            materials=[row["material"] for row in c.execute("SELECT DISTINCT material FROM urns WHERE active=1 AND category=? AND material<>'' ORDER BY material",(category,))]
        tabs_html=''.join(f'<a href="/catalogo-urne?categoria={key}" class="{"active" if key==category_param else ""}">{label}</a>' for key,label in (("urne","Urne"),("accessori","Accessori"),("calchi","Calchi")))
        new_item_label={"Urna":"Nuova urna","Accessorio":"Nuovo accessorio","Calco":"Nuovo calco"}[category]
        stats={"models":len(all_urns),"quantity":sum(max(0,int(u["quantity"] or 0)) for u in all_urns),"out":sum(1 for u in all_urns if int(u["quantity"] or 0)<=0),"low":sum(1 for u in all_urns if 0<int(u["quantity"] or 0)<=int(u["low_stock_threshold"] or 3)),"value":sum(max(0,int(u["quantity"] or 0))*money_value(u["price"]) for u in all_urns)}
        stat_html=''.join(f'<div class="urn-stat"><small>{label}</small><strong>{value}</strong></div>' for label,value in (("Modelli",stats["models"]),("Pezzi disponibili",stats["quantity"]),("Esaurite",stats["out"]),("Scorte basse",stats["low"]),("Valore magazzino",money_it(stats["value"]))))
        cards=[]
        for urn in urns:
            qty=int(urn["quantity"] or 0); threshold=int(urn["low_stock_threshold"] or 3); cls="stock-out" if qty<=0 else "stock-low" if qty<=threshold else "stock-good"; label="Esaurita" if qty<=0 else "Scorta bassa" if qty<=threshold else "Disponibile"
            image=f'<img src="{esc(urn["image_path"])}" alt="{esc(urn["name"])}">' if urn["image_path"] else '<div class="urn-placeholder">Nessuna foto</div>'
            searchable=esc(" ".join((urn["name"] or "",urn["material"] or "",urn["internal_code"] or "")).lower())
            cards.append(f'''<article class="urn-card" data-urn-search="{searchable}">{image}<div><span class="{cls}"><b>{label}</b> · {qty} pz</span><h2>{esc(urn["name"])}</h2><div class="urn-meta">{esc(urn["material"] or "Senza categoria")} {("· "+esc(urn["internal_code"])) if urn["internal_code"] else ""}</div><strong>{money_it(money_value(urn["price"]))}</strong></div><div class="actions"><a class="btn" href="/catalogo-urne/{urn["id"]}">Apri scheda</a></div></article>''')
        no_results=not urns
        display_term="" if no_results else term
        display_material="" if no_results else material
        display_availability="" if no_results else availability
        material_options='<option value="">Tutti i materiali</option>'+''.join(f'<option value="{esc(item)}" {"selected" if item==display_material else ""}>{esc(item)}</option>' for item in materials)
        availability_options=''.join(f'<option value="{value}" {"selected" if display_availability==value else ""}>{label}</option>' for value,label in (("","Tutte le disponibilita"),("disponibile","Disponibili"),("bassa","Scorte basse"),("esaurita","Esaurite")))
        empty='<section id="urnLiveEmpty" class="section empty-state">Nessuna urna corrisponde ai filtri. Puoi aggiungerne una nuova.</section>'
        content=f'<section id="urnCatalogGrid" class="urn-grid">{"".join(cards)}</section><section id="urnLiveEmpty" class="section empty-state" style="display:none">Nessuna urna corrisponde alla ricerca.</section>' if cards else empty
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Catalogo Urne</h1><p class="sub">Catalogo e magazzino collegati alle pratiche.</p></div><a class="btn" href="/catalogo-urne/nuova?categoria={category_param}">{new_item_label}</a></div><nav class="calendar-tabs urn-tabs">{tabs_html}</nav><section class="urn-stats">{stat_html}</section><form class="section urn-filter" method="get"><input type="hidden" name="categoria" value="{category_param}"><div class="fields"><div class="field full"><label>Cerca urna</label><input id="urnCatalogSearch" name="q" value="{esc(display_term)}" placeholder="Inizia a scrivere il nome dell urna" autocomplete="off"></div><div class="field"><label>Materiale</label><select name="materiale">{material_options}</select></div><div class="field"><label>Disponibilita</label><select name="disponibilita">{availability_options}</select></div></div><button class="btn" style="margin-top:12px">Filtra</button></form>{content}<script>(function(){{const input=document.getElementById("urnCatalogSearch"),grid=document.getElementById("urnCatalogGrid"),empty=document.getElementById("urnLiveEmpty");if(!input||!grid)return;const cards=[...grid.querySelectorAll(".urn-card")];const normalize=v=>String(v||"").toLocaleLowerCase("it").normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim();input.addEventListener("input",()=>{{const words=normalize(input.value).split(/\s+/).filter(Boolean);let visible=0;cards.forEach(card=>{{const hay=normalize(card.dataset.urnSearch);const show=words.every(word=>hay.includes(word));card.style.display=show?"":"none";if(show)visible++;}});if(empty)empty.style.display=visible?"none":"block";}});}})();</script></main>'''
        self.send_html(layout("Catalogo Urne",body,user))

    def urn_edit_page(self,user,urn_id=None,draft=None,error=""):
        urn=None
        if urn_id:
            with db() as c: urn=c.execute("SELECT * FROM urns WHERE id=? AND active=1",(urn_id,)).fetchone()
            if not urn:return self.send_error(404)
        value=lambda key: esc(draft.get(key,"") if draft is not None else (urn[key] if urn else ""))
        category_labels={"Urna":"Urna","Accessorio":"Accessorio","Calco":"Calco"}
        category_param_map={"urne":"Urna","accessori":"Accessorio","calchi":"Calco"}
        q=parse_qs(urlparse(self.path).query)
        default_category_param=(q.get("categoria") or ["urne"])[0].strip().lower()
        if default_category_param not in category_param_map:default_category_param="urne"
        draft_category=draft.get("category") if draft is not None else None
        current_category=draft_category if draft_category in category_labels else (urn["category"] if urn and urn["category"] in category_labels else category_param_map[default_category_param])
        category_options=''.join(f'<option value="{value_}" {"selected" if value_==current_category else ""}>{label}</option>' for value_,label in category_labels.items())
        new_item_title={"Urna":"Nuova urna","Accessorio":"Nuovo accessorio","Calco":"Nuovo calco"}[current_category]
        edit_item_title={"Urna":"Modifica urna","Accessorio":"Modifica accessorio","Calco":"Modifica calco"}[current_category]
        with db() as c:
            materials=[row["material"] for row in c.execute("SELECT DISTINCT material FROM urns WHERE active=1 AND TRIM(COALESCE(material,''))<>'' ORDER BY material")]
        material_list=''.join(f'<option value="{esc(item)}"></option>' for item in materials)
        action=f'/catalogo-urne/{urn_id}/modifica' if urn_id else '/catalogo-urne/nuova'
        readonly_code='' if urn else 'readonly'
        error_html=f'<div class="flash warning">{esc(error)}</div>' if error else ''
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>{edit_item_title if urn else new_item_title}</h1><p class="sub">I dati saranno disponibili subito nella compilazione delle pratiche.</p></div></div>{error_html}<form method="post" action="{action}" class="section"><div class="fields"><div class="field"><label>Categoria</label><select name="category">{category_options}</select></div><div class="field"><label>Nome *</label><input name="name" value="{value('name')}" required></div><div class="field"><label>Materiale</label><input name="material" list="urnMaterialOptions" value="{value('material')}" placeholder="Scegli o scrivi un materiale"><datalist id="urnMaterialOptions">{material_list}</datalist></div><div class="field"><label>Codice interno</label><input name="internal_code" value="{value('internal_code')}" placeholder="Generato automaticamente" {readonly_code}><small class="sub">Per un nuovo articolo viene assegnato automaticamente.</small></div><div class="field"><label>Prezzo € *</label><input name="price" value="{value('price')}" inputmode="decimal" required></div><div class="field"><label>Quantita disponibile</label><div class="quantity-stepper urn-quantity-stepper"><button class="btn ghost" type="button" aria-label="Diminuisci quantità" onclick="adjustUrnQuantity(this.form,-1)">−</button><input name="quantity" value="{value('quantity') if urn or draft is not None else '0'}" inputmode="numeric"><button class="btn ghost" type="button" aria-label="Aumenta quantità" onclick="adjustUrnQuantity(this.form,1)">+</button></div></div><div class="field"><label>Soglia scorte basse</label><input name="low_stock_threshold" value="{value('low_stock_threshold') if urn or draft is not None else '3'}" inputmode="numeric"></div><div class="field full"><label>Foto (PNG, JPG o WEBP; max 3 MB)</label><input id="urnImageFile" type="file" accept="image/png,image/jpeg,image/webp"><input type="hidden" name="image_data"><small class="sub">Lascia vuoto per mantenere la foto esistente.</small></div><div class="field full"><label>Note</label><textarea name="notes">{value('notes')}</textarea></div></div><div class="actions" style="margin-top:16px"><button class="btn">Salva</button><a class="btn ghost" href="{f'/catalogo-urne/{urn_id}' if urn_id else '/catalogo-urne'}">Annulla</a></div></form></main>'''
        self.send_html(layout("Catalogo Urne",body,user))

    def urn_detail_page(self,user,urn_id):
        with db() as c:
            urn=c.execute("SELECT * FROM urns WHERE id=? AND active=1",(urn_id,)).fetchone()
            movements=c.execute("""SELECT m.*,u.display_name,p.practice_number FROM urn_movements m LEFT JOIN users u ON u.id=m.user_id LEFT JOIN practices p ON p.id=m.practice_id WHERE m.urn_id=? ORDER BY m.created_at DESC,m.id DESC LIMIT 100""",(urn_id,)).fetchall()
        if not urn:return self.send_error(404)
        qty=int(urn["quantity"] or 0); threshold=int(urn["low_stock_threshold"] or 3); status="Esaurita" if qty<=0 else "Scorta bassa" if qty<=threshold else "Disponibile"; cls="stock-out" if qty<=0 else "stock-low" if qty<=threshold else "stock-good"
        image=f'<img style="width:100%;border-radius:14px" src="{esc(urn["image_path"])}" alt="{esc(urn["name"])}">' if urn["image_path"] else '<div class="urn-placeholder">Nessuna foto</div>'
        history_items=[]
        for movement in movements:
            practice_link=(f' · <a href="/pratiche/{movement["practice_id"]}">{esc(movement["practice_number"])}</a>' if movement["practice_id"] else "")
            note=(f' · {esc(movement["note"])}' if movement["note"] else "")
            history_items.append(f'<div class="event"><b>{esc(movement["movement_type"])}</b> · {movement["quantity_delta"]:+d} ({movement["old_quantity"]} → {movement["new_quantity"]}){practice_link}<br><small class="sub">{esc(movement["display_name"] or "Sistema")} · {esc((movement["created_at"] or "").replace("T"," "))}{note}</small></div>')
        history=''.join(history_items) or '<p class="sub">Nessun movimento registrato.</p>'
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>{esc(urn["name"])}</h1><p class="sub">{esc(urn["material"] or "Senza categoria")} {("· "+esc(urn["internal_code"])) if urn["internal_code"] else ""}</p></div><div class="actions"><a class="btn" href="/catalogo-urne/{urn_id}/modifica">Modifica</a><a class="btn ghost" href="/catalogo-urne">Catalogo</a></div></div><section class="grid urn-detail"><article class="section">{image}</article><article class="section"><div class="kvs"><div class="kv"><small>Prezzo</small><b>{money_it(money_value(urn["price"]))}</b></div><div class="kv"><small>Quantita</small><b>{qty}</b></div><div class="kv"><small>Stato</small><b class="{cls}">{status}</b></div><div class="kv"><small>Soglia scorte basse</small><b>{threshold}</b></div></div><h2 style="margin-top:18px">Note</h2><p>{esc(urn["notes"]) or '<span class="sub">Nessuna nota.</span>'}</p><form method="post" action="/catalogo-urne/{urn_id}/elimina" onsubmit="return confirm('Rimuovere questa urna dal catalogo?')"><button class="btn danger-btn">Rimuovi dal catalogo</button></form></article></section><section class="section" style="margin-top:20px"><h2>Storico movimenti</h2><div class="timeline">{history}</div></section></main>'''
        self.send_html(layout(urn["name"],body,user))

    def _save_urn_image(self,image_data,urn_id):
        if not image_data:return None
        match=re.fullmatch(r"data:image/(png|jpeg|webp);base64,(.+)",image_data,re.S)
        if not match:return None
        raw=base64.b64decode(match.group(2),validate=True)
        if len(raw)>3*1024*1024: raise ValueError("Immagine troppo grande")
        extension={"png":"png","jpeg":"jpg","webp":"webp"}[match.group(1)]
        folder=DATA / "urn_images"; folder.mkdir(parents=True,exist_ok=True)
        filename=f"urna-{urn_id}-{secrets.token_hex(5)}.{extension}"; (folder / filename).write_bytes(raw)
        return f"/uploads/urns/{filename}"

    def save_urn(self,user,urn_id=None):
        f=self.form(); name=f.get("name","").strip(); material=f.get("material","").strip(); code=f.get("internal_code","").strip() or None; price=normalize_money_text(f.get("price","")); notes=f.get("notes","").strip()
        category=f.get("category","").strip()
        if category not in ("Urna","Accessorio","Calco"):category="Urna"
        draft={"name":name,"material":material,"internal_code":f.get("internal_code","").strip(),"price":price,"quantity":f.get("quantity","0"),"low_stock_threshold":f.get("low_stock_threshold","3"),"notes":notes,"category":category}
        try: quantity=max(0,int(f.get("quantity","0") or 0)); threshold=max(0,int(f.get("low_stock_threshold","3") or 3))
        except ValueError:return self.urn_edit_page(user,urn_id,draft=draft,error="Quantità o soglia scorte non valide: usa solo numeri interi.")
        if not name or not re.fullmatch(r"\d+(?:\.\d{1,2})?",price):return self.urn_edit_page(user,urn_id,draft=draft,error="Inserisci un nome e un prezzo validi (es. 120,00).")
        code_prefix={"Urna":"URN","Accessorio":"ACC","Calco":"CALCO"}[category]
        try:
            with db() as c:
                stamp=now()
                if not urn_id and not code:
                    used={str(row[0] or "").upper() for row in c.execute("SELECT internal_code FROM urns WHERE internal_code IS NOT NULL")}
                    next_number=1
                    while f"{code_prefix}-{next_number:03d}" in used:
                        next_number+=1
                    code=f"{code_prefix}-{next_number:03d}"
                if urn_id:
                    old=c.execute("SELECT * FROM urns WHERE id=? AND active=1",(urn_id,)).fetchone()
                    if not old:return self.send_error(404)
                    image_path=old["image_path"]
                    saved=self._save_urn_image(f.get("image_data",""),urn_id)
                    if saved:image_path=saved
                    c.execute("UPDATE urns SET name=?,material=?,category=?,internal_code=?,price=?,quantity=?,low_stock_threshold=?,image_path=?,notes=?,updated_at=? WHERE id=?",(name,material,category,code,price,quantity,threshold,image_path,notes,stamp,urn_id))
                    if quantity!=int(old["quantity"] or 0): c.execute("INSERT INTO urn_movements(urn_id,user_id,movement_type,quantity_delta,old_quantity,new_quantity,note,created_at) VALUES(?,?,?,?,?,?,?,?)",(urn_id,user["id"],"Rettifica manuale",quantity-int(old["quantity"] or 0),int(old["quantity"] or 0),quantity,"Modifica scheda urna",stamp))
                else:
                    cur=c.execute("INSERT INTO urns(name,material,category,internal_code,price,quantity,low_stock_threshold,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(name,material,category,code,price,quantity,threshold,notes,stamp,stamp)); urn_id=cur.lastrowid
                    image_path=self._save_urn_image(f.get("image_data",""),urn_id)
                    if image_path:c.execute("UPDATE urns SET image_path=? WHERE id=?",(image_path,urn_id))
                    c.execute("INSERT INTO urn_movements(urn_id,user_id,movement_type,quantity_delta,old_quantity,new_quantity,note,created_at) VALUES(?,?,?,?,?,?,?,?)",(urn_id,user["id"],"Creazione / carico iniziale",quantity,0,quantity,"Nuova urna",stamp))
        except sqlite3.IntegrityError:return self.urn_edit_page(user,urn_id,draft=draft,error="Codice interno già utilizzato da un altro articolo.")
        except (ValueError,base64.binascii.Error):return self.urn_edit_page(user,urn_id,draft=draft,error="Immagine non valida: usa PNG, JPG o WEBP entro 3 MB.")
        self.redirect(f"/catalogo-urne/{urn_id}")

    def delete_urn(self,user,urn_id):
        with db() as c:
            urn=c.execute("SELECT * FROM urns WHERE id=? AND active=1",(urn_id,)).fetchone()
            if not urn:return self.send_error(404)
            stamp=now(); c.execute("UPDATE urns SET active=0,updated_at=? WHERE id=?",(stamp,urn_id)); c.execute("INSERT INTO urn_movements(urn_id,user_id,movement_type,quantity_delta,old_quantity,new_quantity,note,created_at) VALUES(?,?,?,?,?,?,?,?)",(urn_id,user["id"],"Rimozione dal catalogo",0,urn["quantity"],urn["quantity"],"Articolo disattivato",stamp))
        self.redirect("/catalogo-urne")

    def order_article(self,user,article_id):
        with db() as c:
            article=c.execute("SELECT * FROM articles WHERE id=? AND active=1",(article_id,)).fetchone()
            if not article:return self.send_error(404)
            stamp=now()
            c.execute("INSERT INTO article_orders(article_id,ordered_by,created_at) VALUES(?,?,?)",(article_id,user["id"],stamp))
            emit_notification(c,"article_ordered","📦 Prodotto da ordinare",f'{article["name"]}\nRichiesto da {user["display_name"]}',actor_user_id=user["id"],payload={"url":"/prodotti"},db_path=DB_PATH)
        self.redirect("/prodotti")

    def whatsapp_conversations(self,user):
        q=parse_qs(urlparse(self.path).query)
        term=(q.get("q") or [""])[0].strip(); date_from=(q.get("dal") or [""])[0].strip(); date_to=(q.get("al") or [""])[0].strip()
        message_status=(q.get("stato_messaggio") or [""])[0].strip(); practice_status=(q.get("stato_pratica") or [""])[0].strip()
        allowed_message_statuses=["programmato","in_invio","accettato_da_meta","consegnato","letto","fallito","annullato"]
        if message_status not in allowed_message_statuses: message_status=""
        if practice_status not in STATES: practice_status=""
        try: page=max(1,int((q.get("pagina") or ["1"])[0]))
        except ValueError: page=1
        per_page=20
        event_date="COALESCE(NULLIF(wm.sent_at,''),NULLIF(wm.last_attempt_at,''),NULLIF(wm.scheduled_at,''),wm.created_at)"
        where=["wm.manual=0","(wm.sent_at IS NOT NULL OR wm.status IN ('programmato','in_invio','accettato_da_meta','consegnato','letto','fallito','annullato'))"]
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
        status_labels={"programmato":"Programmato","in_invio":"Invio in corso","accettato_da_meta":"Inviato","consegnato":"Consegnato","letto":"Letto","fallito":"Fallito","annullato":"Annullato"}
        cards=[]
        for row in rows:
            client=" ".join(x for x in [row["owner_first_name"],row["owner_last_name"]] if x).strip() or row["owner_company"] or "Cliente non indicato"
            phone=only_digits(row["recipient_phone"] or row["owner_phone"]); status=status_labels.get(row["status"],self.whatsapp_status_label(row["status"]))
            last_message=(f'Errore: {compact_text(row["last_error"])}' if row["status"]=="fallito" and row["last_error"] else f'Ringraziamento automatico · {row["template_name"]}' if row["template_name"] else "Ringraziamento automatico")
            chat_action=f'<a class="btn whatsapp-open" href="https://wa.me/{phone}" target="_blank" rel="noopener noreferrer">Apri chat WhatsApp</a>' if phone else '<span class="sub">Numero non disponibile</span>'
            retry_action=f'''<form method="post" action="/whatsapp-messaggi/{row['id']}/riprova" onsubmit="return confirm('Riprovare ora questo invio WhatsApp?')"><button class="btn">Riprova</button></form>''' if row["status"]=="fallito" else ""
            cancel_action=f'''<form method="post" action="/whatsapp-messaggi/{row['id']}/annulla" onsubmit="return confirm('Annullare questo messaggio programmato?')"><button class="btn ghost">Annulla</button></form>''' if row["status"]=="programmato" else ""
            error_detail=f'<div class="conversation-error"><dt>Errore</dt><dd>{esc(row["last_error"])}</dd></div>' if row["last_error"] else ""
            cards.append(f'''<article class="conversation-card"><div class="conversation-main"><div class="conversation-avatar">{lucide("message")}</div><div><h2>{esc(client)}</h2><p><b>{esc(row["animal_name"] or "Animale non indicato")}</b> · pratica <a href="/pratiche/{row["practice_id"]}?return_to={quote(self.path,safe='')}">{esc(row["practice_number"])}</a></p><p class="conversation-message">{esc(last_message[:180])}</p></div></div><dl><div><dt>WhatsApp</dt><dd>{('+'+esc(phone)) if phone else '-'}</dd></div><div><dt>Stato reale</dt><dd><span class="badge message-{esc(row["status"])}">{esc(status)}</span></dd></div><div><dt>Orario programmato</dt><dd>{esc((row["scheduled_at"] or "-").replace("T"," ")[:16])}</dd></div><div><dt>Ultimo tentativo</dt><dd>{esc((row["last_attempt_at"] or "-").replace("T"," ")[:16])}</dd></div><div><dt>Data invio</dt><dd>{esc((row["sent_at"] or "-").replace("T"," ")[:16])}</dd></div><div><dt>Pratica</dt><dd><span class="badge">{esc(row["practice_status"])}</span></dd></div>{error_detail}</dl><div class="conversation-action actions">{retry_action}{cancel_action}{chat_action}</div></article>''')
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

    def notifications(self,user):
        q=parse_qs(urlparse(self.path).query); term=(q.get("q") or [""])[0].strip(); kind=(q.get("tipo") or [""])[0].strip(); state=(q.get("stato") or [""])[0].strip()
        with db() as c:
            c.execute("UPDATE notifications SET is_read=1,read_at=COALESCE(read_at,?) WHERE user_id=? AND is_read=0",(now(),user["id"]))
        where=["n.user_id=?"]; args=[user["id"]]
        if term:
            where.append("(n.title LIKE ? OR n.text LIKE ? OR COALESCE(p.practice_number,'') LIKE ?)"); args.extend([f"%{term}%"]*3)
        if kind in NOTIFICATION_TYPES: where.append("n.type=?"); args.append(kind)
        else: kind=""
        if state in ("lette","non_lette"): where.append("n.is_read=?"); args.append(1 if state=="lette" else 0)
        else: state=""
        with db() as c:
            rows=c.execute(f"""SELECT n.*,u.display_name actor_name,p.practice_number
                                FROM notifications n LEFT JOIN users u ON u.id=n.actor_user_id
                                LEFT JOIN practices p ON p.id=n.practice_id
                                WHERE {' AND '.join(where)} ORDER BY n.created_at DESC,n.id DESC LIMIT 300""",args).fetchall()
        cards=[]
        for row in rows:
            icon=NOTIFICATION_TYPES.get(row["type"],("","🔔"))[1]
            cards.append(f'''<article class="notification-item {'unread' if not row['is_read'] else ''}"><span class="notification-icon">{icon}</span><div class="notification-copy"><b>{esc(row['title'])}</b><p>{esc(row['text'])}</p><small>{esc((row['created_at'] or '').replace('T',' ')[:16])} · {esc(row['actor_name'] or 'Sistema')} · {esc(row['practice_number'] or 'Generale')} · {'Letta' if row['is_read'] else 'Non letta'}</small></div><div class="notification-actions"><a class="btn ghost" href="/notifiche/{row['id']}/apri">Apri</a></div></article>''')
        results=''.join(cards) or '<section class="section empty-state">Nessuna notifica trovata. Lo storico resterà disponibile qui.</section>'
        type_options='<option value="">Tutte le tipologie</option>'+''.join(f'<option value="{key}" {"selected" if kind==key else ""}>{icon} {esc(label)}</option>' for key,(label,icon) in NOTIFICATION_TYPES.items())
        state_options=''.join(f'<option value="{value}" {"selected" if state==value else ""}>{label}</option>' for value,label in (("","Lette e non lette"),("non_lette","Non lette"),("lette","Lette")))
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Notifiche</h1><p class="sub">Storico completo delle notifiche del tuo utente.</p></div><form method="post" action="/notifiche/segna-tutte-lette"><button class="btn ghost">Segna tutte come lette</button></form></div><form class="section" method="get" style="margin-bottom:16px"><div class="fields"><div class="field"><label>Ricerca</label><input name="q" value="{esc(term)}" placeholder="Titolo, testo o pratica"></div><div class="field"><label>Tipologia</label><select name="tipo">{type_options}</select></div><div class="field"><label>Stato</label><select name="stato">{state_options}</select></div></div><button class="btn" style="margin-top:12px">Filtra</button><a class="btn ghost" style="margin-top:12px" href="/notifiche">Pulisci</a></form><section class="notification-center">{results}</section></main>'''
        self.send_html(layout("Notifiche",body,user))

    def settings_page(self,user):
        asset_rows = []
        for name in ("DCS_NUOVO.pdf", "DCS_LIVORNO.pdf", "DCS_EMPOLI.pdf"):
            path = ASSETS / name
            status = "OK" if path.exists() else "MANCANTE"
            size = f"{path.stat().st_size} byte" if path.exists() else "-"
            asset_rows.append(f"<tr><td>{esc(name)}</td><td>{status}</td><td>{size}</td></tr>")
        data_ok = DATA.exists()
        ddt_ok = DDT_DIR.exists()
        writable = os.access(DATA, os.W_OK) if data_ok else False
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Impostazioni</h1><div class="sub">Diagnostica del gestionale. Per le preferenze personali vai su <a href="/il-mio-profilo">Il mio profilo</a>.</div></div></div><section class="section"><h2>Modelli PDF</h2><div class="tablebox"><table><thead><tr><th>File</th><th>Stato</th><th>Dimensione</th></tr></thead><tbody>{''.join(asset_rows)}</tbody></table></div></section><section class="section" style="margin-top:16px"><h2>Cartelle dati</h2><p><b>Assets:</b> {esc(ASSETS)}</p><p><b>DATA:</b> {esc(DATA)} - {'OK' if data_ok else 'MANCANTE'} - scrittura {'OK' if writable else 'NO'}</p><p><b>DDT:</b> {esc(DDT_DIR)} - {'OK' if ddt_ok else 'MANCANTE'}</p></section></main>'''
        self.send_html(layout("Impostazioni",body,user))

    diagnostics=settings_page

    def profile_page(self,user,error=""):
        with db() as c:
            saved={row["type"]:bool(row["enabled"]) for row in c.execute("SELECT type,enabled FROM notification_preferences WHERE user_id=?",(user["id"],))}
            subscriptions=c.execute("SELECT count(*) n FROM push_subscriptions WHERE user_id=?",(user["id"],)).fetchone()["n"]
        prefs=load_preferences(user["id"])
        toggles=''.join(f'''<label class="toggle-row"><span>{icon} {esc(label)}</span><input type="checkbox" name="{key}" value="1" {'checked' if saved.get(key,True) else ''}></label>''' for key,(label,icon) in NOTIFICATION_TYPES.items())
        theme=prefs.get("theme","dark")
        theme_options=''.join(f'<option value="{value}" {"selected" if theme==value else ""}>{label}</option>' for value,label in (("dark","Scuro"),("light","Chiaro")))
        sidebar_order=parse_preference_list(prefs.get("sidebar_order",""))
        ordered_links=reorder_by_saved(SIDEBAR_LINKS,sidebar_order,lambda item:item[2]) if sidebar_order else list(SIDEBAR_LINKS)
        sidebar_rows=''.join(f'''<div class="field"><label>{esc(label)}</label><input type="number" name="sidebar_pos__{esc(label)}" value="{index}" min="0" style="width:80px"></div>''' for index,(href,icon,label) in enumerate(ordered_links))
        bottom_pool={label:(href,icon,label) for href,icon,label in SIDEBAR_LINKS}
        bottom_slots=[label for label in parse_preference_list(prefs.get("bottom_nav_slots","")) if label in bottom_pool][:3]
        for label in BOTTOM_NAV_DEFAULT_SLOTS:
            if len(bottom_slots)>=3:break
            if label not in bottom_slots:bottom_slots.append(label)
        bottom_option_labels=[label for href,_,label in SIDEBAR_LINKS if ":" not in href]
        bottom_selects=''.join(f'''<div class="field"><label>Posizione {position}</label><select name="bottom_slot_{position}">{''.join(f'<option {"selected" if bottom_slots[position-1]==label else ""}>{esc(label)}</option>' for label in bottom_option_labels)}</select></div>''' for position in (1,2,3))
        dashboard_order=[sid for sid in parse_preference_list(prefs.get("dashboard_sections","")) if sid in dict(DASHBOARD_SECTION_LABELS)]
        visible_ids=set(dashboard_order) if dashboard_order else {sid for sid,_ in DASHBOARD_SECTION_LABELS}
        ordered_sections=[(sid,label) for sid in (dashboard_order or [sid for sid,_ in DASHBOARD_SECTION_LABELS])for s,label in DASHBOARD_SECTION_LABELS if s==sid]
        remaining_sections=[(sid,label) for sid,label in DASHBOARD_SECTION_LABELS if sid not in visible_ids]
        dashboard_rows=''.join(f'''<div class="field"><label><input type="checkbox" name="dash_show__{sid}" value="1" checked style="width:auto"> {esc(label)}</label><input type="number" name="dash_pos__{sid}" value="{index}" min="0" style="width:80px"></div>''' for index,(sid,label) in enumerate(ordered_sections))+''.join(f'''<div class="field"><label><input type="checkbox" name="dash_show__{sid}" value="1" style="width:auto"> {esc(label)}</label><input type="number" name="dash_pos__{sid}" value="{len(ordered_sections)+index}" min="0" style="width:80px"></div>''' for index,(sid,label) in enumerate(remaining_sections))
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Il mio profilo</h1><div class="sub">Preferenze personali di {esc(user['display_name'])}. Non modificano i permessi del tuo account.</div></div></div>{f'<div class="flash warning">{esc(error)}</div>' if error else ''}
        <section class="section"><h2>Password</h2><p class="sub">Cambia la tua password personale in qualsiasi momento.</p><a class="btn ghost" href="/imposta-password?return_to=/il-mio-profilo">Cambia password</a></section>
        <section class="section" style="margin-top:16px"><h2>Sessione</h2><p class="sub">Esci dall'account su questo dispositivo, inclusa l'app installata sulla schermata Home.</p><a class="btn danger-btn" href="/logout">Esci</a></section>
        <section class="section" style="margin-top:16px"><h2>Aspetto</h2><form method="post" action="/il-mio-profilo/salva"><input type="hidden" name="return_to" value="/il-mio-profilo"><div class="fields"><div class="field"><label>Tema colori</label><select name="theme">{theme_options}</select></div></div><button class="btn" style="margin-top:12px">Salva tema</button></form></section>
        <section class="section" style="margin-top:16px"><h2>Ordine della sidebar</h2><p class="sub">Numeri più bassi vengono mostrati per primi.</p><form method="post" action="/il-mio-profilo/salva"><input type="hidden" name="return_to" value="/il-mio-profilo"><div class="fields">{sidebar_rows}</div><button class="btn" style="margin-top:12px">Salva ordine sidebar</button></form></section>
        <section class="section" style="margin-top:16px"><h2>Barra di navigazione mobile</h2><p class="sub">Scegli le tre voci accanto al pulsante centrale "+".</p><form method="post" action="/il-mio-profilo/salva"><input type="hidden" name="return_to" value="/il-mio-profilo"><div class="fields">{bottom_selects}</div><button class="btn" style="margin-top:12px">Salva barra mobile</button></form></section>
        <section class="section" style="margin-top:16px"><h2>Dashboard</h2><p class="sub">Spunta i pannelli da mostrare e scegli l'ordine.</p><form method="post" action="/il-mio-profilo/salva"><input type="hidden" name="return_to" value="/il-mio-profilo"><div class="fields">{dashboard_rows}</div><button class="btn" style="margin-top:12px">Salva dashboard</button></form></section>
        <section class="section" style="margin-top:16px"><h2>Notifiche</h2><p class="sub">Dispositivi collegati: <b data-push-device-count>{subscriptions}</b>. Su iPhone la PWA deve essere installata dalla schermata Home.</p><div id="pushVisibleError" class="flash warning hidden"></div><div class="actions" style="margin-bottom:16px"><button class="btn" type="button" onclick="enablePushNotifications()">Abilita notifiche</button><button class="btn ghost" type="button" onclick="schedulePushTest()">Test con PWA chiusa (10 secondi)</button></div><details class="section" open><summary><b>Diagnostica notifiche</b></summary><div class="kvs" style="margin-top:12px"><div class="kv"><small>Notification.permission</small><b data-push-diagnostic="permission">verifica…</b></div><div class="kv"><small>Service worker registrato</small><b data-push-diagnostic="registered">verifica…</b></div><div class="kv"><small>Service worker attivo</small><b data-push-diagnostic="active">verifica…</b></div><div class="kv"><small>Subscription presente</small><b data-push-diagnostic="subscription">verifica…</b></div><div class="kv"><small>Endpoint</small><b data-push-diagnostic="endpoint">—</b></div><div class="kv"><small>Risposta backend</small><b data-push-diagnostic="backend">verifica…</b></div><div class="kv"><small>Ultimo errore</small><b data-push-diagnostic="lastError">nessuno</b></div><div class="kv"><small>Dispositivi registrati</small><b data-push-diagnostic="devices">{subscriptions}</b></div></div></details><form method="post" action="/impostazioni/notifiche"><div class="toggle-list">{toggles}</div><button class="btn" style="margin-top:16px">Salva preferenze</button></form></section></main>'''
        self.send_html(layout("Il mio profilo",body,user))

    def save_preferences(self,user):
        form=self.form()
        updates={}
        if form.get("theme") in ("light","dark"):
            updates["theme"]=form["theme"]
        sidebar_positions=[(key[len("sidebar_pos__"):],value) for key,value in form.items() if key.startswith("sidebar_pos__")]
        if sidebar_positions:
            try:
                ordered=[label for label,_ in sorted(sidebar_positions,key=lambda item:float(item[1]))]
                updates["sidebar_order"]=json.dumps(ordered,ensure_ascii=False)
            except ValueError:
                pass
        bottom_slots=[form.get(f"bottom_slot_{position}","").strip() for position in (1,2,3)]
        if any(bottom_slots):
            updates["bottom_nav_slots"]=json.dumps([slot for slot in bottom_slots if slot],ensure_ascii=False)
        dash_positions=[(key[len("dash_pos__"):],value) for key,value in form.items() if key.startswith("dash_pos__")]
        if dash_positions:
            visible={key[len("dash_show__"):] for key in form if key.startswith("dash_show__")}
            try:
                ordered=[sid for sid,_ in sorted(dash_positions,key=lambda item:float(item[1])) if sid in visible]
                updates["dashboard_sections"]=json.dumps(ordered,ensure_ascii=False)
            except ValueError:
                pass
        if updates:
            with db() as c:
                for key,value in updates.items():
                    c.execute("INSERT INTO user_preferences(user_id,key,value) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",(user["id"],key,value))
        return self.redirect(safe_return_path(form.get("return_to"),"/il-mio-profilo"))

    def json_body(self):
        origin=(self.headers.get("Origin") or "").strip()
        if origin and urlparse(origin).netloc != (self.headers.get("Host") or ""):
            raise PermissionError("Origine richiesta non valida")
        size=min(int(self.headers.get("Content-Length",0)),64_000)
        return json.loads(self.rfile.read(size).decode("utf-8") or "{}")

    def notification_status(self,user):
        with db() as c:
            unread=c.execute("SELECT count(*) n FROM notifications WHERE user_id=? AND is_read=0",(user["id"],)).fetchone()["n"]
            subscriptions=c.execute("SELECT count(*) n FROM push_subscriptions WHERE user_id=?",(user["id"],)).fetchone()["n"]
            latest=c.execute("SELECT endpoint,last_error FROM push_subscriptions WHERE user_id=? ORDER BY updated_at DESC,id DESC LIMIT 1",(user["id"],)).fetchone()
        return self.send_json({"ok":True,"unread":unread,"subscriptions":subscriptions,"vapid_configured":bool(os.environ.get("VAPID_PUBLIC_KEY")),"endpoint":((latest["endpoint"][:32]+"…") if latest else ""),"last_error":(latest["last_error"] if latest else "")})

    def push_subscribe(self,user):
        try: data=self.json_body()
        except (ValueError,PermissionError) as exc: return self.send_json({"ok":False,"error":str(exc)},400)
        subscription=data.get("subscription") if isinstance(data.get("subscription"),dict) else data
        device=data.get("device") if isinstance(data.get("device"),dict) else {}
        endpoint=str(subscription.get("endpoint") or "").strip(); keys=subscription.get("keys") or {}; p256dh=str(keys.get("p256dh") or "").strip(); auth=str(keys.get("auth") or "").strip()
        if not endpoint.startswith("https://") or not p256dh or not auth or len(endpoint)>4096:
            return self.send_json({"ok":False,"error":"Sottoscrizione non valida"},400)
        stamp=now(); agent=(self.headers.get("User-Agent") or "")[:300]; device_name=str(device.get("name") or "PWA")[:100]; platform=str(device.get("platform") or agent)[:300]
        with db() as c:
            existed=c.execute("SELECT id FROM push_subscriptions WHERE endpoint=?",(endpoint,)).fetchone()
            c.execute("""INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,user_agent,device_name,platform,created_at,updated_at,last_error)
                         VALUES(?,?,?,?,?,?,?,?,?,'') ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,p256dh=excluded.p256dh,auth=excluded.auth,user_agent=excluded.user_agent,device_name=excluded.device_name,platform=excluded.platform,updated_at=excluded.updated_at,last_error=''""",
                      (user["id"],endpoint,p256dh,auth,agent,device_name,platform,stamp,stamp))
            subscriptions=c.execute("SELECT count(*) n FROM push_subscriptions WHERE user_id=?",(user["id"],)).fetchone()["n"]
        return self.send_json({"ok":True,"message":"Sottoscrizione già sincronizzata" if existed else "Dispositivo registrato","subscriptions":subscriptions,"endpoint":endpoint[:32]+"…"})

    def push_test(self,user):
        with db() as c:
            count=c.execute("SELECT count(*) n FROM push_subscriptions WHERE user_id=?",(user["id"],)).fetchone()["n"]
        if not count:return self.send_json({"ok":False,"error":"Nessun dispositivo registrato. Premi prima Abilita notifiche."},400)
        if not os.environ.get("VAPID_PRIVATE_KEY"):return self.send_json({"ok":False,"error":"VAPID_PRIVATE_KEY non configurata sul server."},503)
        def send_later():
            with db() as c: emit_notification(c,"push_test","🔔 Test Pet Paradise","La notifica push funziona anche con la PWA chiusa.",actor_user_id=user["id"],target_user_ids=[user["id"]],payload={"url":"/notifiche"},db_path=DB_PATH)
        timer=threading.Timer(10,send_later); timer.daemon=True; timer.start()
        return self.send_json({"ok":True,"message":"Test programmato tra 10 secondi. Chiudi ora la PWA."})

    def push_unsubscribe(self,user):
        try: data=self.json_body()
        except (ValueError,PermissionError) as exc: return self.send_json({"ok":False,"error":str(exc)},400)
        endpoint=str(data.get("endpoint") or "").strip()
        with db() as c: c.execute("DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?",(user["id"],endpoint))
        return self.send_json({"ok":True})

    def save_notification_preferences(self,user):
        form=self.form()
        with db() as c:
            for kind in NOTIFICATION_TYPES:
                c.execute("""INSERT INTO notification_preferences(user_id,type,enabled) VALUES(?,?,?)
                             ON CONFLICT(user_id,type) DO UPDATE SET enabled=excluded.enabled""",
                          (user["id"],kind,1 if form.get(kind)=="1" else 0))
        return self.redirect("/impostazioni")

    def save_order_settings(self,user):
        if user["role"]!="admin":return self.send_error(403,"Solo gli amministratori possono modificare le impostazioni degli ordini.")
        form=self.form();recipient=form.get("order_recipient_email","").strip().lower();subject=form.get("order_email_subject","").strip();template=form.get("order_email_template","").strip();signature=form.get("order_email_signature","").strip();sender_name=form.get("order_sender_name","").strip();phone=form.get("order_phone","").strip();default_notes=form.get("order_default_notes","").strip()
        values={"order_recipient_email":recipient,"order_email_subject":subject,"order_email_template":template,"order_email_signature":signature,"order_sender_name":sender_name,"order_phone":phone,"order_default_notes":default_notes}
        if not valid_email_address(recipient):
            return self.order_settings_page(user,draft=values,error="Inserisci un indirizzo email destinatario valido.")
        if not subject or len(subject)>200 or "\n" in subject or "\r" in subject:return self.order_settings_page(user,draft=values,error="Inserisci un oggetto email valido (senza andare a capo, max 200 caratteri).")
        if not template or len(template)>10000 or "{{quantita}}" not in template:return self.order_settings_page(user,draft=values,error="Il testo deve contenere la variabile {{quantita}}.")
        if not signature or len(signature)>200 or not sender_name or len(sender_name)>200:return self.order_settings_page(user,draft=values,error="Firma e nome mittente sono obbligatori (max 200 caratteri).")
        if len(phone)>100 or len(default_notes)>2000:return self.order_settings_page(user,draft=values,error="Telefono o note predefinite sono troppo lunghi.")
        with db() as c:
            c.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",values.items())
        return self.redirect("/ordini/impostazioni")

    def mark_all_notifications_read(self,user):
        stamp=now()
        with db() as c: c.execute("UPDATE notifications SET is_read=1,read_at=COALESCE(read_at,?) WHERE user_id=? AND is_read=0",(stamp,user["id"]))
        return self.redirect("/notifiche")

    def open_notification(self,user,notification_id):
        with db() as c:
            row=c.execute("SELECT * FROM notifications WHERE id=? AND user_id=?",(notification_id,user["id"])).fetchone()
            if not row: return self.send_error(404)
            c.execute("UPDATE notifications SET is_read=1,read_at=COALESCE(read_at,?) WHERE id=?",(now(),notification_id))
        try: target=json.loads(row["payload"] or "{}").get("url") or "/notifiche"
        except Exception: target="/notifiche"
        if not isinstance(target,str) or not target.startswith("/") or target.startswith("//"): target="/notifiche"
        return self.redirect(target)

    def tag_badges(self,r):
        tags = [
            ("tag_assistita", "ASSISTITA", "tag-red"),
            ("tag_possibile_assistita", "POSSIBILE ASSISTITA", "tag-red"),
            ("tag_assistita_streaming", "ASSISTITA STREAMING", "tag-orange"),
            ("tag_possibile_assistita_streaming", "POSSIBILE ASSISTITA STREAMING", "tag-orange"),
            ("tag_saluto", "SALUTO", "tag-purple"),
            ("tag_calco", "CALCO", "tag-yellow"),
            ("tag_possibile_calco", "POSSIBILE CALCO", "tag-yellow"),
            ("tag_calco_urna", "CALCO PER URNA", "tag-yellow"),
            ("tag_calco_paw", "CALCO POLPASTRELLO", "tag-yellow"),
            ("tag_possibile_calco_paw", "POSSIBILE CALCO POLPASTRELLO", "tag-yellow"),
            ("tag_calco_nose", "CALCO NASO", "tag-yellow"),
            ("tag_possibile_calco_nose", "POSSIBILE CALCO NASO", "tag-yellow"),
            ("tag_avvisare", "AVVISARE", "tag-pink"),
            ("tag_da_richiamare", "DA RICHIAMARE", "tag-blue"),
            ("send_catalog", "INVIARE CATALOGO", "tag-outline-orange"),
            ("catalog_sent", "CATALOGO INVIATO", "tag-outline-green"),
            ("send_estremi", "INVIARE ESTREMI", "tag-outline-orange"),
        ]
        html_badges = ''.join(f'<span class="badge {cls}">{label}</span> ' for key,label,cls in tags if key in r.keys() and r[key])
        return html_badges or '<span class="sub">-</span>'

    def status_badges(self,r):
        payment = r["payment_status"] if "payment_status" in r.keys() and r["payment_status"] else "Da saldare"
        pay_cls = {"Da saldare":"pay-yellow","Acconto":"pay-blue","Pagato":"pay-green"}.get(payment,"")
        status_cls=practice_status_class(r["status"])
        allowed_states=[state for state in STATES if state!="Smaltito" or r["service_type"]=="Cremazione collettiva"]
        state_options=''.join(f'<option value="{esc(state)}" {"selected" if state==r["status"] else ""}>{esc(state)}</option>' for state in allowed_states)
        payment_options=''.join(f'<option value="{esc(state)}" {"selected" if state==payment else ""}>{esc(state)}</option>' for state in PAYMENT_STATES)
        method_value=r["payment_method"] if "payment_method" in r.keys() and r["payment_method"] else ""
        method_options=''.join(f'<option value="{esc(method)}" {"selected" if method==method_value else ""}>{esc(method or "Metodo di pagamento")}</option>' for method in PAYMENT_METHODS)
        amount_value=r["payment_amount"] if "payment_amount" in r.keys() and r["payment_amount"] else (r["deposit"] if payment=="Acconto" else f'{effective_total(r):.2f}' if payment=="Pagato" else "")
        invoice_number=r["invoice_number"] if "invoice_number" in r.keys() and r["invoice_number"] else ""
        invoice_total=r["invoice_total"] if "invoice_total" in r.keys() and r["invoice_total"] else f'{effective_total(r):.2f}'
        invoice_date=r["invoice_date"] if "invoice_date" in r.keys() and r["invoice_date"] else ""
        modal_id=f'paymentPopover{r["id"]}'
        return_to=esc(getattr(self,"path",""))
        return f'''<div class="inline-statuses" onclick="event.stopPropagation()">
        <form method="post" action="/pratiche/{r['id']}/stato-rapido" onsubmit="return savePracticeState(this,event)"><input type="hidden" name="return_to" value="{return_to}"><select class="inline-state-select practice-status {status_cls}" name="status" aria-label="Stato pratica" data-saved-value="{esc(r['status'])}" onchange="savePracticeState(this.form)">{state_options}</select><span class="inline-save-note" aria-live="polite"></span></form>
        <select class="inline-state-select {pay_cls}" aria-label="Stato pagamento" data-payment-popover="{modal_id}" onchange="openPaymentPopover(this)">{payment_options}</select>
        <div class="payment-popover" id="{modal_id}" hidden onclick="if(event.target===this)closePaymentPopover(this)"><form class="payment-dialog" method="post" action="/pratiche/{r['id']}/pagamento-rapido"><div class="titlebar"><div><h2>Pagamento · {esc(r['practice_number'])}</h2><p class="sub">Registra i dettagli senza lasciare questa lista.</p></div><button class="btn ghost" type="button" onclick="closePaymentPopover(this)">Chiudi</button></div><input type="hidden" name="return_to" value="{return_to}"><div class="fields"><div class="field"><label>Stato pagamento</label><select name="payment_status">{payment_options}</select></div><div class="field"><label>Metodo di pagamento</label><select name="payment_method">{method_options}</select></div><div class="field"><label>Totale incassato €</label><input name="payment_amount" value="{esc(amount_value)}" inputmode="decimal" pattern="[0-9]+([,.][0-9]{{1,2}})?" title="Solo numeri, es. 120,00"></div><div class="field"><label>Numero fattura</label><input name="invoice_number" value="{esc(invoice_number)}"></div><div class="field"><label>Totale fattura €</label><input name="invoice_total" value="{esc(invoice_total)}" inputmode="decimal" pattern="[0-9]+([,.][0-9]{{1,2}})?" title="Solo numeri, es. 120,00"></div><div class="field"><label>Data fattura</label><input type="date" name="invoice_date" value="{esc(invoice_date)}"></div></div><button class="btn" style="margin-top:16px">Salva pagamento</button></form></div></div>'''

    def practice_rows(self,rows,show_financials=False):
        rows=list(rows)
        columns=18 if show_financials else 14
        if not rows:return f'<tr><td colspan="{columns}" class="sub">Nessuna pratica presente.</td></tr>'
        urn_ids={int(row[key]) for row in rows for key in ("urn_id","urn_id_2") if key in row.keys() and row[key]}
        urn_names={}
        if urn_ids:
            marks=','.join('?' for _ in urn_ids)
            with db() as c:urn_names={row["id"]:row["name"] for row in c.execute(f"SELECT id,name FROM urns WHERE id IN ({marks})",tuple(urn_ids))}
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
            age_parts=[]
            if r["age_years"]: age_parts.append(f'{esc(r["age_years"])} anni')
            if r["age_months"]: age_parts.append(f'{esc(r["age_months"])} mesi')
            age_cell=' e '.join(age_parts) or '<span class="sub">-</span>'
            invoice_number=compact_text(r["invoice_number"]) if "invoice_number" in r.keys() else ""
            invoice_amount=compact_text(r["invoice_total"]) if "invoice_total" in r.keys() else ""
            invoice_cell=(f'<b>{esc(invoice_number)}</b>'+ (f'<br><small>{money_it(money_value(invoice_amount))}</small>' if invoice_amount else '')) if invoice_number else '<span class="sub">-</span>'
            vet_label=esc(r['clinic_name'] if 'clinic_name' in r.keys() and r['clinic_name'] else '-')
            recovery_date=date_it(r['pickup_date'] if 'pickup_date' in r.keys() and r['pickup_date'] else r['created_at'])
            notes_preview=compact_text(r["notes"]) if "notes" in r.keys() else ""
            notes_cell=esc(notes_preview[:70])+("..." if len(notes_preview)>70 else "") if notes_preview else '<span class="sub">-</span>'
            urn_labels=[]
            for id_key,note_key in (("urn_id","urn_notes"),("urn_id_2","urn_notes_2")):
                label=urn_names.get(int(r[id_key])) if id_key in r.keys() and r[id_key] else ""
                label=label or (compact_text(r[note_key]) if note_key in r.keys() else "")
                if label and label not in urn_labels:urn_labels.append(label)
            urn_notes=" / ".join(urn_labels)
            urn_prices=[compact_text(r[key]) for key in ("price_urn","price_urn_2") if key in r.keys() and r[key]]
            urn_price=" + ".join(urn_prices)
            urn_cell='<br>'.join(x for x in [esc(urn_notes), f'<small>{esc(urn_price)} €</small>' if urn_price else ''] if x) or '<span class="sub">-</span>'
            channel=payment_channel(r)
            paid_total=received_amount(r)
            paid_cell=payment_amount_with_channel(r,paid_total) if paid_total>0 else f"€ 0,00 {channel}"
            financial_cells=''
            if show_financials:
                total_d=(r["total_text"] or "").strip() if "total_text" in r.keys() else ""
                deposit_label=f"Acconto {channel}"
                remaining_label=f"Rimanenza {channel}"
                financial_cells=f'<td>{money_it(calculated_service_total(r))}</td><td>{money_it(money_value(total_d)) if total_d else "-"}</td><td><small>{deposit_label}</small><br>{money_it(money_value(r["deposit"]))}</td><td><small>{remaining_label}</small><br>{money_it(money_value(r["remaining_balance"]))}</td>'
            practice_url=f'/pratiche/{r["id"]}?return_to={quote(self.path,safe="")}'
            provenance=esc(r["provenance"] if "provenance" in r.keys() and r["provenance"] else "-")
            html.append(f'<tr class="practice-row-link" {row_open_attrs(practice_url,f"Apri pratica {code}")}><td>{animal_cell}</td><td>{age_cell}</td><td>{owner}<br><small>{esc(r["owner_phone"])}</small></td><td>{esc(recovery_date)}</td><td><a href="{practice_url}"><b class="{code_cls}">{esc(code)}</b></a></td><td>{vet_label}</td><td><b>{provenance}</b></td><td>{esc(r["destination_branch"])}</td><td>{self.tag_badges(r)}</td><td>{notes_cell}</td><td>{urn_cell}</td><td><b>{paid_cell}</b></td><td>{invoice_cell}</td>{financial_cells}<td>{self.status_badges(r)}</td></tr>')
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
                                    LIMIT 50""", args+[q,digits,f"%{digits}%",f"%{q}%",f"%{q}%"]).fetchall()
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

    def api_calendar_animals_search(self,user):
        q=(parse_qs(urlparse(self.path).query).get("q",[""])[0] or "").strip()
        if len(q)<2:return self.send_json({"ok":True,"query":q,"too_short":True,"results":[]})
        tokens=[token for token in re.split(r"\s+",q) if len(token)>=2][:5]
        searchable="COALESCE(animal_name,'')||' '||COALESCE(owner_first_name,'')||' '||COALESCE(owner_last_name,'')||' '||COALESCE(species,'')||' '||COALESCE(practice_number,'')"
        where=[];args=[]
        for token in tokens:
            where.append(f"{searchable} LIKE ? COLLATE NOCASE");args.append(f"%{token}%")
        try:
            with db() as c:
                rows=c.execute(f"""SELECT * FROM practices
                                  WHERE (deleted_at IS NULL OR deleted_at='') AND {' AND '.join(where)}
                                  ORDER BY COALESCE(pickup_date,created_at) DESC,id DESC LIMIT 50""",args).fetchall()
            results=[]
            for row in rows:
                channel="D" if uses_total_d(row) else "W"
                total=effective_total(row);deposit=money_value(row["deposit"]);remaining=outstanding_amount(row)
                base_status=(row["payment_status"] or "Da saldare")
                if base_status=="Pagato":detail=f"Pagato {channel} · {money_it(total)}";calendar_status="Pagato";calendar_amount=total
                elif deposit>0:detail=f"Acconto {channel} · {money_it(deposit)} · Rimanenza {channel} · {money_it(remaining)}";calendar_status="Da saldare";calendar_amount=remaining
                else:detail=f"Da pagare {channel} · {money_it(total)}";calendar_status="Da pagare";calendar_amount=total
                owner=" ".join(part for part in (row["owner_first_name"],row["owner_last_name"]) if part).strip()
                owner_address=", ".join(part for part in (row["owner_street"],row["owner_city"],row["owner_province"]) if part).strip()
                results.append({"practice_id":row["id"],"practice_number":row["practice_number"] or "","animal_name":row["animal_name"] or "","species":row["species"] or "","owner_name":owner,"owner_address":owner_address,"pickup_date":date_it(row["pickup_date"] or ""),"payment_channel":channel,"payment_status":base_status,"total":total,"deposit":deposit,"remaining":remaining,"calendar_payment_status":calendar_status,"calendar_payment_amount":calendar_amount,"payment_summary":detail})
            return self.send_json({"ok":True,"query":q,"results":results})
        except Exception as exc:
            print(f"[CALENDAR_ANIMAL_SEARCH] {type(exc).__name__}: {exc}",flush=True)
            return self.send_json({"ok":False,"error":"Errore durante la ricerca animali"},500)

    def api_calendar_practices_search(self,user):
        q=(parse_qs(urlparse(self.path).query).get("q",[""])[0] or "").strip()
        if len(q)<2:return self.send_json({"ok":True,"query":q,"too_short":True,"results":[]})
        tokens=[token for token in re.split(r"\s+",q) if len(token)>=2][:5]
        searchable="COALESCE(animal_name,'')||' '||COALESCE(owner_first_name,'')||' '||COALESCE(owner_last_name,'')||' '||COALESCE(clinic_name,'')||' '||COALESCE(veterinarian_name,'')||' '||COALESCE(practice_number,'')"
        where=[];args=[]
        for token in tokens:
            where.append(f"{searchable} LIKE ? COLLATE NOCASE");args.append(f"%{token}%")
        try:
            with db() as c:
                rows=c.execute(f"""SELECT * FROM practices
                                  WHERE (deleted_at IS NULL OR deleted_at='') AND {' AND '.join(where)}
                                  ORDER BY COALESCE(pickup_date,created_at) DESC,id DESC LIMIT 50""",args).fetchall()
            results=[]
            for row in rows:
                owner=" ".join(part for part in (row["owner_first_name"],row["owner_last_name"]) if part).strip()
                vet=row["clinic_name"] or row["veterinarian_name"] or ""
                pickup_display=date_it(row["pickup_date"] or "")
                weight=(row["estimated_weight"] or "").strip()
                service_type=(row["service_type"] or "").strip()
                service_label={"Cremazione singola":"Singola","Cremazione collettiva":"Collettiva"}.get(service_type,service_type)
                display=" · ".join(part for part in (pickup_display,row["animal_name"]) if part)
                subtitle=" · ".join(part for part in (owner,f"{weight} kg" if weight else "",service_label,row["practice_number"]) if part)
                results.append({"practice_id":row["id"],"practice_number":row["practice_number"] or "","animal_name":row["animal_name"] or "","species":row["species"] or "","owner_name":owner,"veterinarian_name":vet,"pickup_date":pickup_display,"status":row["status"] or "","display":display,"subtitle":subtitle})
            return self.send_json({"ok":True,"query":q,"results":results})
        except Exception as exc:
            print(f"[CALENDAR_PRACTICE_SEARCH] {type(exc).__name__}: {exc}",flush=True)
            return self.send_json({"ok":False,"error":"Errore durante la ricerca pratiche"},500)

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
                rows=c.execute(f"""SELECT id, short_name, clinic_name, doctor_name, phone, address, city, notes
                                   FROM veterinarians
                                   WHERE active=1 AND {' AND '.join(where)}
                                   ORDER BY CASE WHEN short_name LIKE ? COLLATE NOCASE THEN 0 WHEN clinic_name LIKE ? COLLATE NOCASE THEN 1 ELSE 9 END,
                                            COALESCE(short_name, clinic_name), clinic_name
                                   LIMIT 50""", args+[f"{q}%", f"%{q}%"]).fetchall()
            results=[{"id":r["id"],"short_name":r["short_name"] or "","clinic_name":r["clinic_name"] or "","doctor_name":r["doctor_name"] or "","phone":r["phone"] or "","address":r["address"] or "","city":r["city"] or "","notes":r["notes"] or "","provenance":veterinarian_provenance(r["short_name"],r["clinic_name"]),"display":r["short_name"] or r["clinic_name"] or "Veterinario","subtitle":" - ".join(x for x in [r["clinic_name"], r["address"], r["city"]] if x)} for r in rows]
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
        quick=q.get("rapida",[""])[0].strip()
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
        dashboard_event=q.get("dashboard_event",[""])[0].strip()
        dashboard_period=q.get("periodo",[""])[0].strip()
        if dashboard_event not in ("ritirati","in_programma","da_consegnare","consegnati"):dashboard_event=""
        with_deposit=q.get("con_acconto",[""])[0].strip()=="1"
        promemoria=q.get("promemoria",[""])[0].strip()
        event_date_sql=dashboard_practice_date_sql(dashboard_event,"practices") if dashboard_event and dashboard_event!="da_consegnare" else ""
        event_select=f", {event_date_sql} AS dashboard_event_date" if event_date_sql else ""
        sql=f"SELECT practices.*{event_select} FROM practices WHERE (deleted_at IS NULL OR deleted_at='')"; args=[]
        if term:
            like=f"%{term}%"
            sql+=" AND (practice_number LIKE ? OR animal_name LIKE ? OR owner_first_name||' '||owner_last_name LIKE ? OR owner_phone LIKE ? OR owner_phone_2 LIKE ? OR microchip LIKE ? OR clinic_name LIKE ? OR veterinarian_name LIKE ? OR collaborator_name LIKE ? OR CAST(ddt_number AS TEXT) LIKE ?)"
            args += [like]*10
        if quick:
            like=f"%{quick}%"
            sql+=" AND (animal_name LIKE ? OR owner_first_name LIKE ? OR owner_last_name LIKE ? OR owner_first_name||' '||owner_last_name LIKE ?)"
            args += [like]*4
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
        if date_from and not dashboard_event:
            sql += " AND date(COALESCE(NULLIF(pickup_date,''), created_at))>=date(?)"; args.append(date_from)
        if date_to and not dashboard_event:
            sql += " AND date(COALESCE(NULLIF(pickup_date,''), created_at))<=date(?)"; args.append(date_to)
        if dashboard_event=="ritirati":
            sql += f" AND status IN ('Ritirato','Cremato','Da consegnare','Consegnato','Smaltito') AND {event_date_sql} BETWEEN date(?) AND date(?)";args.extend([date_from,date_to])
        elif dashboard_event=="in_programma":
            sql += f" AND status='In programma' AND ((pickup_date IS NULL OR pickup_date='') OR {event_date_sql} BETWEEN date(?) AND date(?))";args.extend([date_from,date_to])
        elif dashboard_event=="da_consegnare":
            sql += " AND status='Da consegnare'"
        elif dashboard_event=="consegnati":
            sql += f" AND status='Consegnato' AND {event_date_sql} BETWEEN date(?) AND date(?)";args.extend([date_from,date_to])
        elif state:
            sql += " AND status=?"; args.append(state)
        if payment:
            sql += " AND COALESCE(payment_status,'Da saldare')=?"; args.append(payment)
        if with_deposit:
            sql += " AND CAST(REPLACE(COALESCE(NULLIF(deposit,''),'0'), ',', '.') AS REAL)>0"
        if promemoria == "catalogo":
            sql += " AND send_catalog='Si' AND status!='Consegnato'"
        if promemoria == "estremi":
            sql += " AND send_estremi='Si' AND status!='Consegnato'"
        sql += f" ORDER BY {event_date_sql} DESC, id DESC" if event_date_sql else " ORDER BY date(COALESCE(NULLIF(pickup_date,''), created_at)) DESC, id DESC"
        with db() as c:
            rows=c.execute(sql,args).fetchall()
        opts='<option value="">Tutti gli stati</option>'+''.join(f'<option {"selected" if state==s else ""}>{esc(s)}</option>' for s in STATES)
        pay_opts='<option value="">Tutti i pagamenti</option>'+''.join(f'<option {"selected" if payment==s else ""}>{esc(s)}</option>' for s in PAYMENT_STATES)
        service_opts=''.join(f'<option value="{esc(x)}" {"selected" if service==x else ""}>{esc(x or "Tutti i servizi")}</option>' for x in ["","Da decidere","Cremazione singola","Cremazione collettiva"])
        period_names={"oggi":"Oggi","settimana":"Settimana in corso","mese":"Mese corrente"}
        promemoria_label = f" - {period_names.get(dashboard_period,dashboard_period)}" if dashboard_event else f" - Ricerca rapida: {esc(quick)}" if quick else " - Pratiche con acconto" if with_deposit else " - Promemoria catalogo" if promemoria=="catalogo" else " - Promemoria estremi" if promemoria=="estremi" else ""
        month_names=["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno","Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
        groups={}
        for r in rows:
            grouping_date=(r["dashboard_event_date"] if "dashboard_event_date" in r.keys() else "") or r["pickup_date"] or r["created_at"] or ""
            key=(grouping_date[:7]) or "Senza data"
            groups.setdefault(key,[]).append(r)
        blocks=[]
        archive_financial_headers='<th>Totale W</th><th>TOTALE D</th><th>Acconto</th><th>Rimanenza</th>' if quick else ''
        for key,items in groups.items():
            title=key
            if key != "Senza data":
                try:
                    y,m=key.split("-"); title=f"{month_names[int(m)]} {y}"
                except Exception:
                    pass
            blocks.append(f'''<section class="month-block"><div class="month-title"><div class="month-heading"><button class="month-toggle" type="button" aria-expanded="true" aria-label="Chiudi {esc(title)}" onclick="toggleArchiveMonth(this)">-</button><h2>{esc(title)}</h2></div><span class="badge">{len(items)} pratiche</span></div><div class="month-content"><div class="tablebox"><table class="practice-list-table"><thead><tr><th>Animale</th><th>Età</th><th>Proprietario</th><th>Data recupero</th><th>Codice pratica</th><th>Veterinario</th><th>Sede</th><th>Etichetta</th><th>Note</th><th>Urna</th><th>Totale pagato</th><th>Fattura</th>{archive_financial_headers}<th>Stati</th></tr></thead><tbody>{self.practice_rows(items,bool(quick))}</tbody></table></div></div></section>''')
        results_html=''.join(blocks) if blocks else '<section class="section"><p class="sub">Nessuna pratica trovata.</p></section>'
        filters_html=f'''<section class="search-after-results"><h2>Ricerca e filtri</h2><form class="section" method="get"><div class="fields"><div class="field"><label>Ricerca generale</label><input name="q" value="{esc(term)}" placeholder="Proprietario, telefono, microchip, pratica, DDT"></div><div class="field"><label>Nome animale</label><input name="animale" value="{esc(animal)}"></div><div class="field"><label>Tipo cremazione</label><select name="servizio">{service_opts}</select></div><div class="field"><label>Veterinario</label><input name="veterinario" value="{esc(vet)}" placeholder="Clinica o medico"></div><div class="field"><label>Collaboratore</label><input name="collaboratore" value="{esc(collaborator)}"></div><div class="field"><label>Spesa minima</label><input type="number" min="0" step="0.01" name="spesa_min" value="{esc(spesa_min)}" inputmode="decimal" placeholder="Es. 100"></div><div class="field"><label>Spesa massima</label><input type="number" min="0" step="0.01" name="spesa_max" value="{esc(spesa_max)}" inputmode="decimal" placeholder="Es. 350"></div><div class="field"><label>Periodo dal</label><input type="date" name="dal" value="{esc(date_from)}"></div><div class="field"><label>Periodo al</label><input type="date" name="al" value="{esc(date_to)}"></div><div class="field"><label>Stato pratica</label><select name="stato">{opts}</select></div><div class="field"><label>Pagamento</label><select name="pagamento">{pay_opts}</select></div></div><button class="btn" style="margin-top:12px">Cerca</button><a class="btn ghost" style="margin-top:12px" href="/archivio/pratiche">Pulisci filtri</a></form></section>'''
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>ARCHIVIO</h1><div class="sub">{len(rows)} pratiche trovate{promemoria_label}</div></div></div>{results_html}{filters_html}<script>function toggleArchiveMonth(button){{const content=button.closest('.month-block').querySelector('.month-content');const closing=button.getAttribute('aria-expanded')==='true';button.setAttribute('aria-expanded',String(!closing));button.textContent=closing?'+':'-';button.setAttribute('aria-label',(closing?'Apri ':'Chiudi ')+button.closest('.month-heading').querySelector('h2').textContent);content.hidden=closing;}}</script></main>'''
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

    def fields_html(self,p=None,user=None):
        return self._fields_html(p,user).replace("Totale servizio €","Totale W €")

    def _fields_html(self,p=None,user=None):
        val=lambda k: esc(p[k] if p and k in p.keys() else "")
        raw=lambda k,default="": (p[k] if p and k in p.keys() and p[k] not in (None,"") else default)
        selected=lambda k,v,default="": "selected" if str(raw(k,default))==v else ""
        tag_select=lambda name,label,cls: f'''<div class="field"><label><input type="checkbox" name="{name}" value="Si" {"checked" if raw(name)=="Si" else ""}> <span class="badge {cls}">{label}</span></label></div>'''
        with db() as c:
            vets=c.execute("SELECT * FROM veterinarians WHERE active=1 ORDER BY COALESCE(short_name, clinic_name), clinic_name").fetchall()
            urns=c.execute("SELECT * FROM urns WHERE active=1 ORDER BY name").fetchall()
        vet_option=lambda v, selected_id: f'<option value="{v["id"]}" data-shortname="{esc(v["short_name"] or v["clinic_name"])}" data-fullname="{esc(v["clinic_name"])}" data-address="{esc(v["address"])}" data-city="{esc(v["city"])}" data-phone="{esc(v["phone"])}" data-provenance="{veterinarian_provenance(v["short_name"],v["clinic_name"])}" {"selected" if str(selected_id)==str(v["id"]) else ""}>{esc(v["short_name"] or v["clinic_name"])}{(" - "+esc(v["clinic_name"])) if v["short_name"] else ""}</option>'
        vet_options='<option value="">Nessun veterinario selezionato</option>'+''.join(vet_option(v, raw("veterinarian_id")) for v in vets)
        owner_vet_options='<option value="">Compilazione manuale</option>'+''.join(vet_option(v, raw("owner_veterinarian_id")) for v in vets)
        origin_vet_options='<option value="">Seleziona veterinario</option>'+''.join(vet_option(v, raw("origin_veterinarian_id")) for v in vets)
        urn_options=lambda selected_id: '<option value="">Nessuna urna dal catalogo</option>'+''.join(f'<option value="{u["id"]}" data-name="{esc(u["name"])}" data-price="{esc(u["price"])}" data-quantity="{u["quantity"]}" data-image="{esc(u["image_path"] or "")}" {"selected" if str(selected_id)==str(u["id"]) else ""}>{esc(u["name"])} · {esc(u["material"] or "Senza categoria")} · {money_it(money_value(u["price"]))} · disp. {u["quantity"]}</option>' for u in urns)
        voucher_checked='checked' if raw('voucher_requested')=="Si" else ''
        use_voucher_checked='checked' if raw('use_voucher')=="Si" else ''
        catalog_checked='checked' if raw('send_catalog')=="Si" else ''
        catalog_sent_checked='checked' if raw('catalog_sent')=="Si" else ''
        estremi_checked='checked' if raw('send_estremi')=="Si" else ''
        estremi_sent_checked='checked' if raw('estremi_sent')=="Si" else ''
        make_invoice_checked='checked' if raw('make_invoice')=="Si" else ''
        payment_options=''.join(f'<option {"selected" if raw("payment_status","Da saldare")==state else ""}>{state}</option>' for state in PAYMENT_STATES)
        payment_method_options=''.join(f'<option value="{method}" {"selected" if raw("payment_method")==method else ""}>{method or "Seleziona metodo"}</option>' for method in PAYMENT_METHODS)
        if user is None or user["role"]=="admin":
            operator_field=f'''<div class="field"><label>Operatore *</label><select name="operator_name" required><option value="">Seleziona operatore</option><option {selected('operator_name','SERENA')}>SERENA</option><option {selected('operator_name','ALESSIO')}>ALESSIO</option><option {selected('operator_name','FILIPPO')}>FILIPPO</option><option {selected('operator_name','GIANLUCA')}>GIANLUCA</option></select></div>'''
        else:
            operator_display=raw('operator_name') or user['display_name'].upper()
            operator_field=f'''<input type="hidden" name="operator_name" value="{esc(operator_display)}"><div class="field"><label>Operatore</label><p style="margin:0;padding:11px 0;font-weight:700">{esc(operator_display)}</p></div>'''
        return f'''<section class="section"><h2>Operatore e stati</h2><div class="fields">{operator_field}<div class="field"><label>Stato pratica</label><select name="status"><option {selected('status','Ritirato','Ritirato')}>Ritirato</option><option {selected('status','In programma','Ritirato')}>In programma</option><option {selected('status','Cremato','Ritirato')}>Cremato</option><option {selected('status','Da consegnare','Ritirato')}>Da consegnare</option><option {selected('status','Consegnato','Ritirato')}>Consegnato</option><option data-collective-only="1" {selected('status','Smaltito','Ritirato')}>Smaltito</option></select></div></div></section>
        <input type="hidden" name="urn_notes" value="{val('urn_notes')}"><select name="urn_id" class="hidden" aria-hidden="true" tabindex="-1">{urn_options(raw('urn_id'))}</select><small id="urnStockWarning" class="sub hidden"></small>
        <input type="hidden" name="price_urn_2" value="{val('price_urn_2')}"><input type="hidden" name="urn_notes_2" value="{val('urn_notes_2')}"><select name="urn_id_2" class="hidden" aria-hidden="true" tabindex="-1">{urn_options(raw('urn_id_2'))}</select><small id="urnStockWarning2" class="sub hidden"></small><input type="hidden" name="price_cast_2" value="{val('price_cast_2')}"><input type="hidden" name="price_paw_cast_2" value="{val('price_paw_cast_2')}"><input type="hidden" name="price_paw_cast_3" value="{val('price_paw_cast_3')}"><input type="hidden" name="price_paw_cast_4" value="{val('price_paw_cast_4')}"><input type="hidden" name="price_nose_cast_2" value="{val('price_nose_cast_2')}"><input type="hidden" name="price_nose_cast_3" value="{val('price_nose_cast_3')}"><input type="hidden" name="price_nose_cast_4" value="{val('price_nose_cast_4')}"><input type="hidden" name="price_accessories_2" value="{val('price_accessories_2')}"><input type="hidden" name="accessory_type" value="{val('accessory_type')}"><input type="hidden" name="accessory_type_2" value="{val('accessory_type_2')}"><input type="hidden" name="accessory_detail" value="{val('accessory_detail')}"><input type="hidden" name="accessory_detail_2" value="{val('accessory_detail_2')}"><input type="hidden" name="nose_cast_type" value="{val('nose_cast_type')}"><input type="hidden" name="nose_cast_type_2" value="{val('nose_cast_type_2')}"><input type="hidden" name="nose_cast_type_3" value="{val('nose_cast_type_3')}"><input type="hidden" name="nose_cast_type_4" value="{val('nose_cast_type_4')}"><input type="hidden" name="paw_cast_type" value="{val('paw_cast_type')}"><input type="hidden" name="paw_cast_type_2" value="{val('paw_cast_type_2')}"><input type="hidden" name="paw_cast_type_3" value="{val('paw_cast_type_3')}"><input type="hidden" name="paw_cast_type_4" value="{val('paw_cast_type_4')}"><select name="payment_status" class="hidden">{payment_options}</select><select name="payment_method" class="hidden">{payment_method_options}</select><input type="hidden" name="catalog_sent" value="{'Si' if catalog_sent_checked else ''}"><input type="hidden" name="estremi_sent" value="{'Si' if estremi_sent_checked else ''}"><input type="hidden" name="invoice_number" value="{val('invoice_number')}"><input type="hidden" name="invoice_date" value="{val('invoice_date')}"><input type="hidden" name="invoice_total" value="{val('invoice_total')}"><input type="hidden" name="make_invoice" value="{'Si' if make_invoice_checked else ''}">
        <section class="section"><h2>Richiesta</h2><div class="fields"><div class="field"><label>Servizio *</label><select name="service_type" required><option value="" {"selected" if not raw("service_type") else ""}>SELEZIONA</option><option {selected('service_type','Da decidere')}>Da decidere</option><option {selected('service_type','Cremazione singola')}>Cremazione singola</option><option {selected('service_type','Cremazione collettiva')}>Cremazione collettiva</option></select></div><div class="field"><label>Origine richiesta *</label><select name="request_origin" required><option {selected('request_origin','Veterinario')}>Veterinario</option><option {selected('request_origin','Privato')}>Privato</option><option value="Consegna in sede" {selected('request_origin','Consegna in sede')}>Consegnato in sede</option><option {selected('request_origin','Collaboratore')}>Collaboratore</option></select></div><div class="field {'hidden' if raw('request_origin')!='Collaboratore' else ''}" id="collaboratorBox"><label>Collaboratore</label><select name="collaborator_name"><option value="">Nessun collaboratore</option><option {selected('collaborator_name','HUMANITAS CROCE VERDE')}>HUMANITAS CROCE VERDE</option></select></div><div class="field"><label>Sede di destinazione</label><select name="destination_branch"><option {selected('destination_branch','Livorno')}>Livorno</option><option {selected('destination_branch','Empoli')}>Empoli</option></select></div><div class="field"><label>Data recupero</label><input type="date" name="pickup_date" value="{val('pickup_date')}"></div></div></section>
        <section class="section"><h2>SPEDITORE</h2><div class="fields"><input type="hidden" name="client_id" value="{val('client_id')}"><div class="field full lookup"><label>Cerca cliente in anagrafica</label><input id="clientSearch" autocomplete="off" placeholder="Scrivi nome, telefono, email, codice fiscale, città..."><div id="clientResults" class="lookup-results hidden"></div><div id="clientSelected" class="selected-box hidden"><span id="clientSelectedText"></span><button class="btn ghost" type="button" id="clearClientSelection">Cancella selezione</button></div><small class="sub">Se scegli un cliente, i campi vengono compilati automaticamente. Se li modifichi, l'anagrafica non viene aggiornata senza conferma.</small></div><div class="field full lookup"><label>Usa veterinario come speditore</label><input id="ownerVetSearch" autocomplete="off" placeholder="Scrivi per cercare il veterinario"><div id="ownerVetResults" class="lookup-results hidden"></div><select name="owner_veterinarian_id" class="hidden" aria-hidden="true" tabindex="-1">{owner_vet_options}</select><small class="sub">Compila automaticamente i dati dello speditore. Sul DDT, nel Luogo di origine, verra scritto solo il nome breve del veterinario.</small></div><div class="field"><label>Nome *</label><input name="owner_first_name" value="{val('owner_first_name')}" required></div><div class="field"><label>Cognome *</label><input name="owner_last_name" value="{val('owner_last_name')}" required></div><div class="field"><label>Ragione sociale</label><input name="owner_company" value="{val('owner_company')}"></div><div class="field"><label>Telefono *</label><input type="tel" inputmode="numeric" name="owner_phone" value="{val('owner_phone')}" required></div><div class="field"><label>Secondo telefono</label><input type="tel" inputmode="numeric" name="owner_phone_2" value="{val('owner_phone_2')}"></div><div class="field"><label>Note telefono</label><input name="owner_phone_note" value="{val('owner_phone_note')}" placeholder="Testo libero"></div><div class="field"><label>Email</label><input type="email" name="owner_email" value="{val('owner_email')}"></div><div class="field"><label>Codice fiscale *</label><input name="owner_tax_code" value="{val('owner_tax_code')}" required></div><div class="field"><label>Partita IVA</label><input name="owner_vat" value="{val('owner_vat')}"></div><div class="field full"><label>Indirizzo *</label><input name="owner_street" value="{val('owner_street') or val('owner_address')}" required></div><div class="field"><label>Comune *</label><input name="owner_city" value="{val('owner_city')}" required></div><div class="field"><label>Provincia *</label><input name="owner_province" value="{val('owner_province')}" maxlength="2" placeholder="Si compila dal comune" required></div><div class="field"><label>CAP *</label><input name="owner_zip" value="{val('owner_zip')}" inputmode="numeric" required></div><div class="field full"><label>Note cliente</label><textarea name="owner_notes" placeholder="Note anagrafiche utili">{val('owner_notes')}</textarea></div></div></section>
        <section class="section"><h2>DESTINATARIO E LUOGO DI DESTINAZIONE</h2><p class="sub">Compilati automaticamente in base alla sede selezionata: Livorno oppure Empoli.</p></section>
        <section class="section"><h2>LUOGO DI ORIGINE</h2><div class="fields"><div class="field"><label>Provenienza</label><select name="provenance"><option value="">Seleziona zona</option>{''.join(f'<option value="{code}" {"selected" if raw("provenance")==code else ""}>{code} · {label}</option>' for code,label in (("L","Livorno"),("E","Empoli"),("V","Viareggio"),("F","Firenze"),("P","Pisa")))}</select></div><div class="field"><label>Luogo di origine</label><select name="origin_mode"><option {selected('origin_mode','IDEM SPED','IDEM SPED')}>IDEM SPED</option><option {selected('origin_mode','Veterinario','IDEM SPED')}>Veterinario</option><option {selected('origin_mode','Testo libero','IDEM SPED')}>Testo libero</option></select></div><div class="field lookup"><label>Cerca veterinario o scrivi testo libero</label><input id="originVetSearch" autocomplete="off" placeholder="Nome o iniziali del veterinario"><div id="originVetResults" class="lookup-results hidden"></div><select name="origin_veterinarian_id" class="hidden" aria-hidden="true" tabindex="-1">{origin_vet_options}</select><small class="sub">Seleziona un risultato oppure continua a scrivere liberamente.</small></div><div class="field full"><label>Testo libero / indirizzo diverso</label><input name="origin_text" value="{val('origin_text') or (val('pickup_address') if raw('pickup_address_mode')=='Altro indirizzo' else '')}" placeholder="Nome breve veterinario o indirizzo diverso"></div></div></section>
        <section class="section"><h2>Animale</h2><div class="fields"><div class="field"><label>Specie *</label><input name="species" value="{val('species')}" required></div><div class="field"><label>Nome</label><input name="animal_name" value="{val('animal_name')}"></div><div class="field"><label>Peso</label><input name="estimated_weight" value="{val('estimated_weight')}"></div><div class="field"><label>Anni</label><input name="age_years" value="{val('age_years')}"></div><div class="field"><label>Mesi</label><input name="age_months" value="{val('age_months')}"></div><div class="field"><label>Microchip</label><input name="microchip" value="{val('microchip')}"></div><div class="field full"><label>Razza</label><input name="breed" value="{val('breed')}"></div></div><button class="btn ghost" type="button" id="showSecondAnimal" style="margin-top:12px;{'display:none' if raw('animal2_name') else ''}">+ Aggiungi altro animale</button><div id="secondAnimalBox" style="display:{'block' if raw('animal2_name') else 'none'};margin-top:14px"><h2>Secondo animale</h2><div class="fields"><div class="field"><label>Nome</label><input name="animal2_name" value="{val('animal2_name')}"></div><div class="field"><label>Specie</label><input name="animal2_species" value="{val('animal2_species')}"></div><div class="field"><label>Peso stimato (kg)</label><input name="animal2_weight" value="{val('animal2_weight')}"></div><div class="field"><label>Microchip</label><input name="animal2_microchip" value="{val('animal2_microchip')}"></div><div class="field full"><label>Razza</label><input name="animal2_breed" value="{val('animal2_breed')}"></div></div></div></section>
        <section class="section"><h2>AMBULATORIO VETERINARIO</h2><div class="fields"><div class="field full lookup"><label>VETERINARIO</label><input id="vetSearch" autocomplete="off" placeholder="Scrivi per cercare il veterinario"><div id="vetResults" class="lookup-results hidden"></div><select name="veterinarian_id">{vet_options}</select><input type="hidden" name="clinic_name" value="{val('clinic_name')}"><button class="btn ghost" type="button" id="clearVetSelection" style="margin-top:8px">Cancella veterinario</button></div><div class="field"><label>MEDICO VETERINARIO</label><input name="veterinarian_name" value="{val('veterinarian_name')}"></div><div class="field"><label><input type="checkbox" name="voucher_requested" value="Si" {voucher_checked}> BUONO</label><small class="sub">Spunta per assegnare un buono al veterinario selezionato.</small></div></div></section>
        <section class="section"><h2>TRASPORTATORE</h2><div class="fields"><div class="field"><label>Dati trasportatore</label><select name="transporter_mode"><option {selected('transporter_mode','IDEM SPED','IDEM SPED')}>IDEM SPED</option><option {selected('transporter_mode','DATI PET PARADISE','IDEM SPED')}>DATI PET PARADISE</option></select></div><div class="field"><label>Mezzo di trasporto</label><select name="transport_method" id="transport_method_quick"><option value="">Seleziona mezzo</option><option {selected('transport_method','Fiat Fiorino')}>Fiat Fiorino</option><option {selected('transport_method','Renault Captur')}>Renault Captur</option><option {selected('transport_method','Dr PK8')}>Dr PK8</option><option {selected('transport_method','Mezzo proprio')}>Mezzo proprio</option></select></div><div class="field"><label>Targa automezzo</label><input name="vehicle_plate" value="{val('vehicle_plate')}" placeholder="Compilata automaticamente, modificabile"></div><div class="field"><label>Temperatura</label><select name="temperature_mode"><option {selected('temperature_mode','Ambiente','Ambiente')}>Ambiente</option><option {selected('temperature_mode','Refrigerato','Ambiente')}>Refrigerato</option><option {selected('temperature_mode','Congelato','Ambiente')}>Congelato</option></select></div><div class="field"><label>Numero colli</label><input name="package_count" value="{val('package_count') or '1'}"></div><div class="field"><label>ID contenitore</label><select name="container_id"><option value="">Seleziona ID contenitore</option><option {selected('container_id','03/2021')}>03/2021</option><option {selected('container_id','04/2021')}>04/2021</option></select></div><div class="field"><label>Numero lotto</label><input name="lot_number" value="{val('lot_number') or '/'}"></div><div class="field"><label>Metodo trattamento</label><input name="treatment_method" value="{val('treatment_method') or '/'}"></div></div></section>
        <section class="section"><h2>Preventivo</h2><div class="fields"><div class="field"><label>Cremazione €</label><input name="price_cremation" value="{val('price_cremation')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Ritiro €</label><input name="price_pickup" value="{val('price_pickup')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Urna €</label><input name="price_urn" value="{val('price_urn')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label><input type="checkbox" name="send_catalog" value="Si" {catalog_checked} style="width:auto"> INVIARE CATALOGO</label></div><div class="field"><label>Riconsegna €</label><input name="price_delivery" value="{val('price_delivery')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Calco €</label><input name="price_cast" value="{val('price_cast')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Calco polpastrello €</label><input name="price_paw_cast" value="{val('price_paw_cast')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Calco naso €</label><input name="price_nose_cast" value="{val('price_nose_cast')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Serale €</label><input name="price_evening" value="{val('price_evening')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Notturno €</label><input name="price_night" value="{val('price_night')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Festivo €</label><input name="price_holiday" value="{val('price_holiday')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Accessori €</label><input name="price_accessories" value="{val('price_accessories')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Totale servizio €</label><input name="total_service" value="{val('total_service')}" readonly></div><div class="field"><label>Acconto €</label><input name="deposit" value="{val('deposit')}" placeholder="Numero o testo libero"></div><div class="field"><label>Rimanenza €</label><input name="remaining_balance" value="{val('remaining_balance')}" readonly></div><div class="field full"><label>TOTALE D</label><textarea name="total_text" placeholder="Testo libero per note sul totale">{val('total_text')}</textarea></div><div class="field"><label>Acconto D €</label><input name="deposit_final" value="{val('deposit_final')}" placeholder="Numero o testo libero"></div><div class="field"><label>Rimanenza D €</label><input name="remaining_final" value="{val('remaining_final')}" readonly></div><div class="field full"><label>NOTE</label><textarea name="notes">{val('notes')}</textarea></div><div class="field"><label><input type="checkbox" name="send_estremi" value="Si" {estremi_checked} style="width:auto"> INVIARE ESTREMI</label></div><div class="field"><label><input type="checkbox" name="use_voucher" value="Si" {use_voucher_checked} style="width:auto"> USA BUONO</label><div id="useVoucherBox" class="selected-box hidden"><span id="useVoucherStatus">Seleziona il veterinario e spunta USA BUONO.</span><select name="used_voucher_id" data-current="{val('used_voucher_id')}" class="hidden"><option value="">Seleziona buono</option></select></div></div></div></section>
        <section class="section"><h2>Etichette operative</h2><div class="fields">{tag_select('tag_assistita','ASSISTITA','tag-red')}{tag_select('tag_possibile_assistita','POSSIBILE ASSISTITA','tag-red')}{tag_select('tag_assistita_streaming','ASSISTITA STREAMING','tag-orange')}{tag_select('tag_possibile_assistita_streaming','POSSIBILE ASSISTITA STREAMING','tag-orange')}{tag_select('tag_saluto','SALUTO','tag-purple')}{tag_select('tag_calco','CALCO','tag-yellow')}{tag_select('tag_possibile_calco','POSSIBILE CALCO','tag-yellow')}{tag_select('tag_calco_urna','CALCO PER URNA','tag-yellow')}{tag_select('tag_calco_paw','CALCO POLPASTRELLO','tag-yellow')}{tag_select('tag_possibile_calco_paw','POSSIBILE CALCO POLPASTRELLO','tag-yellow')}{tag_select('tag_calco_nose','CALCO NASO','tag-yellow')}{tag_select('tag_possibile_calco_nose','POSSIBILE CALCO NASO','tag-yellow')}{tag_select('tag_avvisare','AVVISARE','tag-pink')}{tag_select('tag_da_richiamare','DA RICHIAMARE','tag-blue')}</div></section>
        <section class="section"><h2>Documento e accettazione</h2><div class="fields"><div class="field"><label>Numero documento</label><input name="identity_document_number" value="{val('identity_document_number')}"></div><div class="field"><label>Data rilascio</label><input type="date" name="identity_document_date" value="{val('identity_document_date')}"></div><div class="field full"><label>Luogo firma</label><input name="signing_place" value="{val('signing_place') or val('destination_branch')}"></div></div></section>'''

    def new_page(self,user,draft=None,error=""):
        q=parse_qs(urlparse(getattr(self,"path","")).query);calendar_event_id=(q.get("calendar_event_id") or [""])[0];prefill={}
        if calendar_event_id.isdigit():
            with db() as c:
                event=c.execute("SELECT * FROM calendar_events WHERE id=? AND deleted_at IS NULL",(int(calendar_event_id),)).fetchone()
                animal=c.execute("SELECT * FROM calendar_event_animals WHERE event_id=? ORDER BY id LIMIT 1",(int(calendar_event_id),)).fetchone()
            if event:
                if event["linked_practice_id"]:return self.redirect(f'/pratiche/{event["linked_practice_id"]}')
                client=None
                if event["client_id"]:
                    with db() as c:
                        client=c.execute("SELECT * FROM clients WHERE id=?",(event["client_id"],)).fetchone()
                prefill={"client_id":event["client_id"] or "","owner_first_name":(client["first_name"] if client else event["client_first_name"]) or "","owner_last_name":(client["last_name"] if client else event["client_last_name"]) or "","owner_company":(client["company_name"] if client else "") or "","owner_phone":(client["phone"] if client else event["client_phone"]) or "","owner_phone_2":(client["phone_2"] if client else "") or "","owner_email":(client["email"] if client else "") or "","owner_tax_code":(client["tax_code"] if client else "") or "","owner_vat":(client["vat_number"] if client else "") or "","owner_street":(client["street"] or client["address"] if client else "") or "","owner_city":(client["city"] if client else "") or "","owner_province":(client["province"] if client else "") or "","owner_zip":(client["zip"] if client else "") or "","owner_notes":(client["notes"] if client else "") or "","pickup_address":event["address"] or "","pickup_date":event["start_at"][:10],"destination_branch":event["destination_site"] or "Livorno","request_origin":"Veterinario" if event["veterinarian_id"] else "Privato","veterinarian_id":event["veterinarian_id"] or "","clinic_name":event["veterinarian_name"] or "","notes":event["notes"] or "","animal_name":(animal["name"] if animal else event["animal_name"]) or "","species":animal["species"] if animal else "","estimated_weight":animal["weight"] if animal else "","service_type":f'Cremazione {animal["cremation_type"].lower()}' if animal and animal["cremation_type"] else ""}
        if draft is not None:prefill=draft
        hidden=(f'<input type="hidden" name="calendar_event_id" value="{calendar_event_id}"><input type="hidden" name="pickup_time" value="{event["start_at"][11:16]}">' if calendar_event_id.isdigit() and event else '')
        error_html=f'<div class="flash warning">{esc(error)}</div>' if error else ''
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Nuova pratica</h1><div class="sub">Inserisci subito i dati disponibili; potrai completarli in seguito.</div></div><div class="actions"><button class="btn" form="practiceForm">Crea pratica</button></div></div>{error_html}<form method="post" id="practiceForm">{hidden}<div class="grid form-grid">{self.fields_html(prefill,user)}</div><div class="actions" style="margin-top:18px"><button class="btn">Crea pratica</button><a class="btn ghost" href="{f'/calendario/{calendar_event_id}' if calendar_event_id.isdigit() else '/'}">Annulla</a></div></form></main>'''
        self.send_html(layout("Nuova pratica",body,user))

    def normalized_fields(self,f):
        keys=["client_id","owner_veterinarian_id","origin_veterinarian_id","operator_name","request_origin","collaborator_name","destination_branch","owner_first_name","owner_last_name","owner_company","owner_phone","owner_phone_2","owner_phone_note","owner_email","owner_tax_code","owner_vat","owner_notes","owner_address","owner_street","owner_city","owner_province","owner_zip","pickup_address_mode","pickup_address","origin_mode","origin_text","provenance","pickup_date","animal_name","species","breed","estimated_weight","age_years","age_months","microchip","animal2_name","animal2_species","animal2_breed","animal2_weight","animal2_microchip","service_type","veterinarian_id","voucher_requested","use_voucher","used_voucher_id","clinic_name","veterinarian_name","notes","transporter_mode","transport_method","vehicle_plate","temperature_mode","package_count","container_id","lot_number","treatment_method","tag_assistita","tag_possibile_assistita","tag_assistita_streaming","tag_possibile_assistita_streaming","tag_saluto","tag_calco","tag_possibile_calco","tag_calco_urna","tag_calco_paw","tag_possibile_calco_paw","tag_calco_nose","tag_possibile_calco_nose","tag_avvisare","tag_da_richiamare","payment_status","payment_method","price_cremation","price_pickup","price_evening","price_urn","send_catalog","catalog_sent","send_estremi","estremi_sent","price_delivery","price_night","price_cast","price_paw_cast","price_nose_cast","price_holiday","price_accessories","deposit","deposit_final","remaining_balance","remaining_final","total_service","total_text","invoice_number","invoice_date","invoice_total","make_invoice","identity_document_number","identity_document_date","signing_place"]
        data = {k:f.get(k,"").strip() for k in keys}
        data["pickup_time"] = f.get("pickup_time","").strip()
        data["urn_id"] = f.get("urn_id","").strip() or None
        data["urn_id_2"] = f.get("urn_id_2","").strip() or None
        data["urn_notes"] = f.get("urn_notes","").strip()
        for key in ("price_urn_2","urn_notes_2","price_cast_2","price_paw_cast_2","price_paw_cast_3","price_paw_cast_4","price_nose_cast_2","price_nose_cast_3","price_nose_cast_4","price_accessories_2","accessory_type","accessory_type_2","nose_cast_type","nose_cast_type_2","nose_cast_type_3","nose_cast_type_4","paw_cast_type","paw_cast_type_2","paw_cast_type_3","paw_cast_type_4","accessory_detail","accessory_detail_2"):
            data[key]=f.get(key,"").strip()
        for key in MONEY_FIELDS:
            data[key]=normalize_money_text(data.get(key,""))
        data["invoice_total"]=normalize_money_text(data["invoice_total"])
        allowed_accessories={"","Calco naso","Collana","Braccialetto","Calco inchiostro","Altro"}
        if data["accessory_type"] not in allowed_accessories: data["accessory_type"]="Altro"
        if data["accessory_type_2"] not in allowed_accessories: data["accessory_type_2"]="Altro"
        if not data["payment_status"] or data["payment_status"] not in PAYMENT_STATES:
            data["payment_status"] = "Da saldare"
        if data["payment_method"] not in PAYMENT_METHODS:
            data["payment_method"] = ""
        data["send_catalog"] = "Si" if data["send_catalog"] == "Si" else ""
        data["catalog_sent"] = "Si" if data["catalog_sent"] == "Si" else ""
        if data["catalog_sent"] == "Si":
            data["send_catalog"] = ""
        data["send_estremi"] = "Si" if data["send_estremi"] == "Si" else ""
        data["estremi_sent"] = "Si" if data["estremi_sent"] == "Si" else ""
        if data["estremi_sent"] == "Si":
            data["send_estremi"] = ""
        data["make_invoice"] = "Si" if data["make_invoice"] == "Si" else ""
        data["use_voucher"] = "Si" if data["use_voucher"] == "Si" else ""
        data["used_voucher_id"] = data["used_voucher_id"] or None
        for key in ("tag_assistita","tag_possibile_assistita","tag_assistita_streaming","tag_possibile_assistita_streaming","tag_saluto","tag_calco","tag_possibile_calco","tag_calco_urna","tag_calco_paw","tag_possibile_calco_paw","tag_calco_nose","tag_possibile_calco_nose","tag_avvisare","tag_da_richiamare"):
            data[key] = "Si" if data[key] == "Si" else ""
        selected_urn_ids=[urn_id for urn_id in (data["urn_id"],data["urn_id_2"]) if urn_id]
        if selected_urn_ids:
            marks=','.join('?' for _ in selected_urn_ids)
            with db() as c:
                frame_urn=c.execute(f"SELECT 1 FROM urns WHERE id IN ({marks}) AND LOWER(name) LIKE '%doppia cornice%' LIMIT 1",selected_urn_ids).fetchone()
            if frame_urn:data["tag_calco_urna"]="Si"
        data["voucher_requested"] = "Si" if data["voucher_requested"] == "Si" else ""
        data["client_id"] = data["client_id"] or None
        data["owner_veterinarian_id"] = data["owner_veterinarian_id"] or None
        data["origin_veterinarian_id"] = data["origin_veterinarian_id"] or None
        data["provenance"] = data["provenance"].upper() if data["provenance"].upper() in ("L","E","V","F","P") else ""
        if data["use_voucher"] == "Si":
            data["payment_status"] = "Pagato"
        data["veterinarian_id"] = data["veterinarian_id"] or None
        if not data["provenance"]:
            provenance_vet_id=(data["origin_veterinarian_id"] if data["origin_mode"]=="Veterinario" else None) or data["veterinarian_id"] or data["owner_veterinarian_id"]
            if provenance_vet_id:
                with db() as c:
                    provenance_vet=c.execute("SELECT short_name,clinic_name FROM veterinarians WHERE id=? AND active=1",(provenance_vet_id,)).fetchone()
                if provenance_vet:data["provenance"]=veterinarian_provenance(provenance_vet["short_name"],provenance_vet["clinic_name"])
        if data["origin_mode"] == "Veterinario" and data["origin_veterinarian_id"]:
            with db() as c:
                origin_vet=c.execute("SELECT short_name,clinic_name FROM veterinarians WHERE id=? AND active=1",(data["origin_veterinarian_id"],)).fetchone()
            if origin_vet: data["origin_text"]=origin_vet["short_name"] or origin_vet["clinic_name"] or data["origin_text"]
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
        data["owner_tax_code"] = data["owner_tax_code"].upper()
        data["owner_province"] = data["owner_province"].upper()
        if data["owner_city"]:
            data["owner_city"] = data["owner_city"][:1].upper() + data["owner_city"][1:]
        city_line = " ".join(x for x in [data["owner_zip"], data["owner_city"], f'({data["owner_province"]})' if data["owner_province"] else ""] if x).strip()
        composed_address = " - ".join(x for x in [data["owner_street"], city_line] if x)
        if composed_address:
            data["owner_address"] = composed_address
        if not data["origin_mode"]:
            data["origin_mode"] = "IDEM SPED"
        if not data["transporter_mode"]:
            data["transporter_mode"] = "IDEM SPED"
        vehicle_plates={"Fiat Fiorino":"GP793KP","Renault Captur":"GV932LL","Dr PK8":"GV041FX"}
        if data["transport_method"] in vehicle_plates and not data["vehicle_plate"]:
            data["vehicle_plate"]=vehicle_plates[data["transport_method"]]
        if data["request_origin"] == "Consegna in sede":
            data["transporter_mode"] = "IDEM SPED"
            data["transport_method"] = "Mezzo proprio"
        elif data["request_origin"] in ("Veterinario","Privato","Collaboratore"):
            data["transporter_mode"] = "DATI PET PARADISE"
        if data["origin_mode"] == "IDEM SPED":
            data["pickup_address"] = data["owner_address"]
            data["pickup_address_mode"] = "Idem sped."
        else:
            data["pickup_address"] = data["origin_text"]
            data["pickup_address_mode"] = "Altro indirizzo"
        if data.get("urn_id"):
            with db() as c:
                selected_urn=c.execute("SELECT id,name,price FROM urns WHERE id=? AND active=1",(data["urn_id"],)).fetchone()
            if selected_urn:
                data["urn_id"]=selected_urn["id"]
                data["urn_notes"]=selected_urn["name"]
                data["price_urn"]=selected_urn["price"]
            else:
                data["urn_id"]=None
        if data.get("urn_id_2"):
            with db() as c:
                selected_urn_2=c.execute("SELECT id,name,price FROM urns WHERE id=? AND active=1",(data["urn_id_2"],)).fetchone()
            if selected_urn_2:
                data["urn_id_2"]=selected_urn_2["id"]
                data["urn_notes_2"]=selected_urn_2["name"]
                data["price_urn_2"]=selected_urn_2["price"]
            else:
                data["urn_id_2"]=None
        calculated=calculated_service_total(data)
        data["total_service"]=(f"{calculated:.2f}" if calculated else "")
        if not data["invoice_total"]:data["invoice_total"]=data["total_text"] or data["total_service"]
        due=effective_total(data)
        data["remaining_balance"]=(f"{max(0.0, due-money_value(data['deposit'])):.2f}" if due else "")
        if due and money_value(data["deposit"]) >= due:
            data["payment_status"]="Pagato"
        return data

    def is_complete(self,d):
        if d.get("tag_da_richiamare") == "Si":
            return 0
        if d.get("service_type") == "Cremazione collettiva":
            return 1
        required=["operator_name","service_type","request_origin","owner_first_name","owner_last_name","owner_phone","owner_tax_code","owner_street","owner_city","owner_province","owner_zip"]
        return int(all(d.get(k) for k in required))

    def validation_error(self,d):
        invalid_money=[label for key,label in MONEY_FIELDS.items() if d.get(key) and not re.fullmatch(r"\d+(?:\.\d{1,2})?",d[key])]
        if invalid_money:
            return "Nel Preventivo sono ammessi solo numeri, con al massimo due decimali: " + ", ".join(invalid_money)
        if d.get("tag_da_richiamare") == "Si":
            return ""
        if d.get("service_type") == "Cremazione collettiva":
            return ""
        labels={
            "operator_name":"Operatore","service_type":"Servizio","request_origin":"Richiesta","owner_first_name":"Nome",
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
        return self.wa_digits(p["owner_phone"] or "")

    def wa_digits(self, phone):
        digits=re.sub(r"\D+","",phone or "")
        if digits.startswith("00"):
            digits=digits[2:]
        if digits and not digits.startswith("39"):
            digits="39"+digits
        return digits

    def phone_action_buttons(self, phone):
        phone=(phone or "").strip()
        if not phone: return ""
        tel=re.sub(r"[^0-9+]","",phone)
        wa=self.wa_digits(phone)
        wa_btn=f'<a class="icon-btn phone-action-btn whatsapp-btn" href="https://wa.me/{wa}" target="_blank" rel="noopener noreferrer" aria-label="Apri chat WhatsApp">{lucide("message")}</a>' if wa else ""
        return f'{esc(phone)} <a class="icon-btn phone-action-btn call-btn" href="tel:{esc(tel)}" aria-label="Chiama">{lucide("phone")}</a> {wa_btn}'

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
            "in_invio":"Invio in corso",
            "accettato_da_meta":"Inviato",
            "consegnato":"Consegnato",
            "letto":"Letto",
            "fallito":"Fallito",
            "annullato":"Annullato",
        }.get(status or "", status or "Non programmato")

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

    def whatsapp_block_reason(self,practice):
        if practice["service_type"] == "Cremazione collettiva":
            return "Cremazione collettiva: WhatsApp disattivato"
        if "owner_veterinarian_id" in practice.keys() and practice["owner_veterinarian_id"]:
            return "Speditore veterinario: WhatsApp disattivato"
        return ""

    def schedule_whatsapp_thanks(self,c,pid,user_id=None):
        p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p:
            return False, "Pratica non trovata"
        block_reason=self.whatsapp_block_reason(p)
        if block_reason:
            self.cancel_whatsapp_scheduled(c,pid,user_id,block_reason)
            c.execute("UPDATE practices SET no_whatsapp_message='Si',whatsapp_thanks_last_error=? WHERE id=?",(block_reason,pid))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"WhatsApp ringraziamento",block_reason,user_id,now()))
            return False,block_reason
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
        scheduled_at=whatsapp_now(whatsapp_datetime()+timedelta(hours=48))
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

    def send_whatsapp_message(self,c,msg_id,manual=False,user_id=None,attempt_recorded=False):
        msg=c.execute("SELECT * FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()
        if not msg:
            return False, "Invio WhatsApp non trovato"
        p=c.execute("SELECT * FROM practices WHERE id=?",(msg["practice_id"],)).fetchone()
        if not p:
            stamp=whatsapp_now();error="Pratica non trovata"
            c.execute("UPDATE whatsapp_messages SET status='fallito',failed_at=?,last_attempt_at=COALESCE(last_attempt_at,?),last_error=?,updated_at=? WHERE id=?",(stamp,stamp,error,stamp,msg_id))
            return False,error
        if not manual and (p["status"] != "Consegnato" or ("deleted_at" in p.keys() and p["deleted_at"])):
            reason="Pratica non più nello stato Consegnato: invio annullato"
            self.cancel_whatsapp_scheduled(c,p["id"],user_id,reason)
            return False,reason
        block_reason=self.whatsapp_block_reason(p)
        if block_reason:
            self.cancel_whatsapp_scheduled(c,p["id"],user_id,block_reason)
            return False,block_reason
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
            stamp=whatsapp_now()
            increment=0 if attempt_recorded else 1
            c.execute("UPDATE whatsapp_messages SET status='fallito', failed_at=?, last_error=?, attempts=attempts+?, last_attempt_at=?, updated_at=? WHERE id=?",(stamp,error,increment,stamp,stamp,msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(error,p["id"]))
            return False,error
        if not token or not phone_id:
            error="Config WhatsApp mancante: imposta WHATSAPP_ACCESS_TOKEN e WHATSAPP_PHONE_NUMBER_ID su Render"
            stamp=whatsapp_now()
            increment=0 if attempt_recorded else 1
            c.execute("UPDATE whatsapp_messages SET status='fallito', failed_at=?, last_error=?, attempts=attempts+?, last_attempt_at=?, updated_at=? WHERE id=?",(stamp,error,increment,stamp,stamp,msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(error,p["id"]))
            return False,error
        payload=json.dumps(payload_obj,ensure_ascii=False).encode("utf-8")
        print(f"[WHATSAPP] POST pratica_id={p['id']} message_row={msg_id} endpoint={endpoint} phone_number_id={phone_id} token={self.masked_whatsapp_token(token)} destinatario=+{phone} template={template} lingua={language} scheduled_at={msg['scheduled_at']} payload={json.dumps(payload_obj,ensure_ascii=False)}", flush=True)
        req=urllib.request.Request(endpoint,data=payload,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},method="POST")
        attempt_stamp=whatsapp_now()
        try:
            with urllib.request.urlopen(req,timeout=18) as resp:
                response_body=resp.read().decode("utf-8","replace")
                http_status=resp.status
            response_json=json.loads(response_body) if response_body else {}
            message_id=""
            if isinstance(response_json,dict) and response_json.get("messages"):
                message_id=response_json["messages"][0].get("id","")
            sent_at=whatsapp_now()
            increment=0 if attempt_recorded else 1
            c.execute("""UPDATE whatsapp_messages SET status='accettato_da_meta', attempts=attempts+?, last_error='', message_id=?, sent_at=?, last_attempt_at=?, template_name=?, language_code=?, recipient_phone=?, payload_json=?, response_json=?, updated_at=? WHERE id=?""",(increment,message_id,sent_at,attempt_stamp,template,language,phone,json.dumps(payload_obj,ensure_ascii=False),response_body,sent_at,msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_sent_at=?, whatsapp_thanks_last_error='' WHERE id=?",(sent_at,p["id"]))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(p["id"],"WhatsApp ringraziamento",f"Accettato da Meta {sent_at} a +{phone} - template {template} - message_id {message_id}",user_id,sent_at))
            print(f"[WHATSAPP] pratica_id={p['id']} message_row={msg_id} esito=ACCETTATO_DA_META http={http_status} message_id={message_id} risposta={response_body}", flush=True)
            if manual:
                self.cancel_whatsapp_scheduled(c,p["id"],user_id,"Annullato perché è stato inviato manualmente")
                c.execute("UPDATE whatsapp_messages SET status='accettato_da_meta', updated_at=? WHERE id=?",(sent_at,msg_id))
            owner=f'{p["owner_first_name"] or ""} {p["owner_last_name"] or ""}'.strip() or p["owner_company"] or "Cliente non indicato"
            emit_notification(c,"whatsapp_sent","📲 Messaggio WhatsApp inviato",owner,p["id"],user_id,{"url":"/conversazioni-whatsapp"},db_path=DB_PATH)
            if not manual:
                emit_notification(c,"thank_you_sent","💚 Messaggio di ringraziamento inviato",owner,p["id"],user_id,{"url":f'/pratiche/{p["id"]}'},db_path=DB_PATH)
            return True, f"Accettato da Meta. Message ID: {message_id or 'non restituito'}"
        except urllib.error.HTTPError as exc:
            detail=exc.read().decode("utf-8","replace")
            error=f"Meta API HTTP {exc.code}: {detail}"
            attempts=int(msg["attempts"] or 0)+(0 if attempt_recorded else 1)
            failed_at=whatsapp_now()
            c.execute("""UPDATE whatsapp_messages SET status='fallito', attempts=?, last_error=?, last_attempt_at=?, failed_at=?, template_name=?, language_code=?, recipient_phone=?, payload_json=?, response_json=?, updated_at=? WHERE id=?""",(attempts,error,attempt_stamp,failed_at,template,language,phone,json.dumps(payload_obj,ensure_ascii=False),detail,failed_at,msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(error,p["id"]))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(p["id"],"WhatsApp ringraziamento",f"Errore: {error}",user_id,now()))
            emit_notification(c,"whatsapp_error","❌ Errore invio WhatsApp",f'{p["owner_first_name"] or ""} {p["owner_last_name"] or ""}\nTocca per vedere il dettaglio.',p["id"],user_id,{"url":"/conversazioni-whatsapp"},db_path=DB_PATH)
            print(f"[WHATSAPP] pratica_id={p['id']} message_row={msg_id} esito=FALLITO http={exc.code} tentativi={attempts} endpoint={endpoint} destinatario=+{phone} template={template} lingua={language} risposta={detail}", flush=True)
            return False,error
        except Exception as exc:
            error=str(exc)
            attempts=int(msg["attempts"] or 0)+(0 if attempt_recorded else 1)
            failed_at=whatsapp_now()
            c.execute("""UPDATE whatsapp_messages SET status='fallito', attempts=?, last_error=?, last_attempt_at=?, failed_at=?, template_name=?, language_code=?, recipient_phone=?, payload_json=?, updated_at=? WHERE id=?""",(attempts,error,attempt_stamp,failed_at,template,language,phone,json.dumps(payload_obj,ensure_ascii=False),failed_at,msg_id))
            c.execute("UPDATE practices SET whatsapp_thanks_last_error=? WHERE id=?",(error,p["id"]))
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(p["id"],"WhatsApp ringraziamento",f"Errore: {error}",user_id,now()))
            emit_notification(c,"whatsapp_error","❌ Errore invio WhatsApp",f'{p["owner_first_name"] or ""} {p["owner_last_name"] or ""}\nTocca per vedere il dettaglio.',p["id"],user_id,{"url":"/conversazioni-whatsapp"},db_path=DB_PATH)
            print(f"[WHATSAPP] pratica_id={p['id']} message_row={msg_id} esito=FALLITO tentativi={attempts} endpoint={endpoint} destinatario=+{phone} template={template} lingua={language} errore={error}", flush=True)
            return False,error

    def process_whatsapp_queue(self,limit=20,current_time=None):
        results=[]
        current_dt=whatsapp_datetime(current_time)
        current=whatsapp_now(current_dt)
        stale=whatsapp_now(current_dt-timedelta(minutes=10))
        with db() as c:
            stuck=c.execute("""SELECT id,message_id FROM whatsapp_messages
                               WHERE status='in_invio' AND COALESCE(NULLIF(last_attempt_at,''),updated_at)<=?""",(stale,)).fetchall()
            for row in stuck:
                if row["message_id"]:
                    c.execute("UPDATE whatsapp_messages SET status='accettato_da_meta',sent_at=COALESCE(sent_at,?),last_attempt_at=COALESCE(last_attempt_at,?),last_error='',updated_at=? WHERE id=?",(current,current,current,row["id"]))
                    results.append({"id":row["id"],"ok":True,"message":"Invio precedente riconciliato tramite Message ID"})
                else:
                    error="Invio interrotto da oltre 10 minuti: non reinviato automaticamente per evitare duplicazioni"
                    c.execute("UPDATE whatsapp_messages SET status='fallito',failed_at=?,last_attempt_at=COALESCE(last_attempt_at,?),last_error=?,updated_at=? WHERE id=?",(current,current,error,current,row["id"]))
                    results.append({"id":row["id"],"ok":False,"message":error})
            due=c.execute("SELECT id,message_id FROM whatsapp_messages WHERE status='programmato' AND scheduled_at<=? ORDER BY scheduled_at LIMIT ?",(current,limit)).fetchall()
            for row in due:
                if row["message_id"]:
                    c.execute("UPDATE whatsapp_messages SET status='accettato_da_meta',sent_at=COALESCE(sent_at,?),last_error='',updated_at=? WHERE id=? AND status='programmato'",(current,current,row["id"]))
                    results.append({"id":row["id"],"ok":True,"message":"Già accettato da Meta: invio non duplicato"})
                    continue
                changed=c.execute("""UPDATE whatsapp_messages
                                     SET status='in_invio',attempts=attempts+1,last_attempt_at=?,updated_at=?
                                     WHERE id=? AND status='programmato' AND message_id IS NULL""",(current,current,row["id"])).rowcount
                if not changed:
                    continue
                ok,msg=self.send_whatsapp_message(c,row["id"],manual=False,user_id=None,attempt_recorded=True)
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
        started_at=whatsapp_now()
        with db() as c:
            run_id=c.execute("INSERT INTO whatsapp_cron_runs(started_at,status) VALUES(?,'in_corso')",(started_at,)).lastrowid
        print(f"[WHATSAPP_CRON] run_id={run_id} autorizzato={reason} timezone=Europe/Rome started_at={started_at}", flush=True)
        try:
            results=self.process_whatsapp_queue()
            with db() as c:
                scheduled_created=process_scheduled_notifications(c,DB_PATH)
                scheduled_created+=process_calendar_notifications(c,DB_PATH)
        except Exception as exc:
            error=f"{type(exc).__name__}: {exc}"
            print(f"[WHATSAPP_CRON] run_id={run_id} errore={error}\n{traceback.format_exc()}",flush=True)
            with db() as c:
                c.execute("UPDATE whatsapp_cron_runs SET finished_at=?,status='fallito',error=? WHERE id=?",(whatsapp_now(),error,run_id))
                emit_notification(c,"whatsapp_cron_error","❌ Errore Cron WhatsApp","Controlla la diagnostica WhatsApp.",payload={"url":"/whatsapp-diagnostica"},db_path=DB_PATH)
            return self.send_json({"ok":False,"error":"Errore durante il cron"},500)
        with db() as c:
            c.execute("UPDATE whatsapp_cron_runs SET finished_at=?,status='completato',processed=? WHERE id=?",(whatsapp_now(),len(results),run_id))
        print(f"[WHATSAPP_CRON] run_id={run_id} completato processed={len(results)} results={json.dumps(results,ensure_ascii=False)}", flush=True)
        return self.send_json({"ok":True,"processed":len(results),"scheduled_notifications":scheduled_created,"results":results})

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

    def adjust_urn_stock(self,c,urn_id,delta,movement_type,practice_id,user_id,note=""):
        if not urn_id:return
        urn=c.execute("SELECT quantity FROM urns WHERE id=?",(urn_id,)).fetchone()
        if not urn:return
        old_quantity=int(urn["quantity"] or 0); new_quantity=max(0,old_quantity+int(delta)); actual_delta=new_quantity-old_quantity
        c.execute("UPDATE urns SET quantity=?,updated_at=? WHERE id=?",(new_quantity,now(),urn_id))
        c.execute("INSERT INTO urn_movements(urn_id,practice_id,user_id,movement_type,quantity_delta,old_quantity,new_quantity,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(urn_id,practice_id,user_id,movement_type,actual_delta,old_quantity,new_quantity,note,now()))

    def add_payment_movement(self,c,practice_id,payment_type,channel,amount,user_id,notes,paid_at=None):
        amount=round(float(amount),2)
        if abs(amount)<0.005:return
        stamp=paid_at or now()
        c.execute("""INSERT INTO payment_movements(practice_id,payment_type,payment_channel,amount,paid_at,user_id,notes,created_at)
                     VALUES(?,?,?,?,?,?,?,?)""",(practice_id,payment_type,channel,amount,stamp,user_id,notes,now()))

    def reconcile_payment_movements(self,c,practice_id,previous,current,user_id,reason):
        channel="D" if uses_total_d(current) else "ordinario"
        due=effective_total(current); target=received_amount(current)
        rows=c.execute("SELECT payment_channel,COALESCE(sum(amount),0) amount FROM payment_movements WHERE practice_id=? GROUP BY payment_channel",(practice_id,)).fetchall()
        totals={row["payment_channel"]:float(row["amount"] or 0) for row in rows}
        existing_total=sum(totals.values())
        for old_channel,old_amount in list(totals.items()):
            if old_channel!=channel and abs(old_amount)>=0.005:
                self.add_payment_movement(c,practice_id,"rettifica",old_channel,-old_amount,user_id,f"Riclassificazione verso circuito {channel}: {reason}")
                self.add_payment_movement(c,practice_id,"rettifica",channel,old_amount,user_id,f"Riclassificazione dal circuito {old_channel}: {reason}")
        old_status=(previous["payment_status"] or "Da saldare") if previous is not None else "Da saldare"
        new_status=current["payment_status"] or "Da saldare"
        old_deposit=money_value(previous["deposit"]) if previous is not None else 0.0
        new_deposit=min(due,max(0.0,money_value(current["deposit"])))
        delta=round(target-existing_total,2)
        stamp=now()
        if previous is None and new_status=="Pagato" and target>0:
            deposit_part=min(new_deposit,target)
            self.add_payment_movement(c,practice_id,"acconto_d" if channel=="D" else "acconto_ordinario",channel,deposit_part,user_id,reason,stamp)
            self.add_payment_movement(c,practice_id,"saldo_d" if channel=="D" else "saldo_ordinario",channel,target-deposit_part,user_id,reason,stamp)
        elif delta>0:
            is_balance=new_status=="Pagato"
            movement_type=("saldo_d" if channel=="D" else "saldo_ordinario") if is_balance else ("acconto_d" if channel=="D" else "acconto_ordinario")
            self.add_payment_movement(c,practice_id,movement_type,channel,delta,user_id,reason,stamp)
        elif delta<0:
            self.add_payment_movement(c,practice_id,"rettifica",channel,delta,user_id,reason,stamp)
        if new_deposit>old_deposit:
            c.execute("UPDATE practices SET deposit_paid_at=? WHERE id=?",(stamp,practice_id))
        if new_status=="Pagato" and old_status!="Pagato":
            c.execute("UPDATE practices SET paid_at=? WHERE id=?",(stamp,practice_id))

    def sync_practice_urn(self,c,practice_id,old_urn_id,new_urn_id,user_id):
        old_id=int(old_urn_id) if old_urn_id else None; new_id=int(new_urn_id) if new_urn_id else None
        if old_id==new_id:return
        if old_id:self.adjust_urn_stock(c,old_id,1,"Restituita dalla pratica",practice_id,user_id,"Urna rimossa o sostituita")
        if new_id:self.adjust_urn_stock(c,new_id,-1,"Utilizzata nella pratica",practice_id,user_id,"Selezione urna")

    def create_client_from_practice_data(self,c,d):
        stamp=now()
        cur=c.execute("""INSERT INTO clients(first_name,last_name,company_name,phone,phone_2,email,tax_code,vat_number,street,city,province,zip,address,notes,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (d.get("owner_first_name"),d.get("owner_last_name"),d.get("owner_company"),d.get("owner_phone"),d.get("owner_phone_2"),d.get("owner_email"),d.get("owner_tax_code"),d.get("owner_vat"),d.get("owner_street"),d.get("owner_city"),d.get("owner_province"),d.get("owner_zip"),d.get("owner_address"),d.get("owner_notes"),stamp,stamp))
        return cur.lastrowid

    def invoice_conflict(self,c,invoice_number,exclude_id=None):
        number=(invoice_number or "").strip()
        if not number:return None
        sql="SELECT id,practice_number FROM practices WHERE lower(trim(invoice_number))=lower(trim(?))"
        args=[number]
        if exclude_id is not None:sql+=" AND id<>?";args.append(exclude_id)
        return c.execute(sql,args).fetchone()

    def duplicate_client_page(self,user,d,duplicates):
        rows=''.join(f'''<tr><td>{esc(((r["first_name"] or "")+" "+(r["last_name"] or "")).strip() or r["company_name"])}</td><td>{esc(r["phone"])}</td><td>{esc(r["email"])}</td><td>{esc(r["tax_code"] or r["vat_number"])}</td><td>{esc(r["city"] or r["address"])}</td><td>ID {r["id"]}</td></tr>''' for r in duplicates)
        hidden=''.join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k,v in d.items() if v is not None)
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Possibile cliente già presente</h1><div class="sub">Prima di creare una nuova anagrafica, controlla questi possibili duplicati.</div></div></div><section class="section"><div class="flash warning">Abbiamo trovato clienti con nome, telefono, email, codice fiscale o partita IVA simili. Puoi tornare alla pratica e usare la ricerca cliente, oppure confermare esplicitamente la creazione di un nuovo cliente.</div><div class="tablebox"><table><thead><tr><th>Cliente</th><th>Telefono</th><th>Email</th><th>CF / P.IVA</th><th>Città / indirizzo</th><th>ID</th></tr></thead><tbody>{rows}</tbody></table></div><div class="actions" style="margin-top:18px"><a class="btn ghost" href="/nuova">Torna e usa cliente esistente</a><form method="post" action="/nuova">{hidden}<input type="hidden" name="confirm_new_client" value="SI"><button class="btn">Conferma nuovo cliente</button></form></div></section></main>'''
        self.send_html(layout("Possibile duplicato cliente",body,user),409)

    def create_practice(self,user):
        f=self.form(); d=self.normalized_fields(f); stamp=now();calendar_event_id=int(f["calendar_event_id"]) if f.get("calendar_event_id","").isdigit() else None
        if user["role"]!="admin": d["operator_name"]=user["display_name"].upper()
        error=self.validation_error(d)
        if error: return self.new_page(user,draft=d,error=error)
        initial=f.get("status","Ritirato")
        if initial not in STATES or (initial=="Smaltito" and d.get("service_type")!="Cremazione collettiva"): initial="Ritirato"
        with db() as c:
            calendar_event=None
            if calendar_event_id:
                calendar_event=c.execute("SELECT * FROM calendar_events WHERE id=? AND deleted_at IS NULL AND event_type IN ('Ritiro','Ritiro in sede') AND event_status='Ritirato'",(calendar_event_id,)).fetchone()
                if not calendar_event:return self.new_page(user,draft=d,error="Evento calendario non valido per la creazione pratica")
                if calendar_event["linked_practice_id"]:return self.redirect(f'/pratiche/{calendar_event["linked_practice_id"]}')
            conflict=self.invoice_conflict(c,d.get("invoice_number"))
            if conflict:return self.new_page(user,draft=d,error=f'Numero fattura già usato nella pratica {conflict["practice_number"]}')
            if d.get("client_id"):
                exists=c.execute("SELECT id FROM clients WHERE id=?",(d["client_id"],)).fetchone()
                if not exists:
                    d["client_id"]=None
            if not d.get("client_id"):
                has_client_data=any(d.get(key) for key in ("owner_first_name","owner_last_name","owner_company","owner_phone","owner_email","owner_tax_code","owner_vat"))
                if has_client_data:
                    duplicates=self.find_client_duplicates(c,d)
                    if duplicates and f.get("confirm_new_client") != "SI":
                        return self.duplicate_client_page(user,d,duplicates)
                    d["client_id"]=self.create_client_from_practice_data(c,d)
                else:
                    d["client_id"]=None
            number=next_practice_code(c,d["service_type"])
            cols=list(d)+["practice_number","status","data_complete","created_at","updated_at","created_by"]
            values=list(d.values())+[number,initial,self.is_complete(d),stamp,stamp,user["id"]]
            marks=','.join('?' for _ in cols)
            cur=c.execute(f"INSERT INTO practices({','.join(cols)}) VALUES({marks})",values); pid=cur.lastrowid
            self.sync_practice_urn(c,pid,None,d.get("urn_id"),user["id"])
            self.sync_practice_urn(c,pid,None,d.get("urn_id_2"),user["id"])
            created_practice=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.reconcile_payment_movements(c,pid,None,created_practice,user["id"],"Creazione pratica")
            self.sync_voucher(c,pid,d)
            self.apply_used_voucher(c,pid,d,user["id"])
            c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Creazione pratica",initial,user["id"],stamp))
            owner=" ".join(x for x in (d.get("owner_first_name"),d.get("owner_last_name")) if x).strip() or d.get("owner_company") or "Cliente non indicato"
            weight_line=f'\n⚖️ {d.get("estimated_weight")} kg' if d.get("estimated_weight") else ""
            emit_notification(c,"practice_created","🐾 Nuova pratica",f'{owner}\n{d.get("animal_name") or number}{weight_line}\n📍 {d.get("destination_branch") or ""}',pid,user["id"],db_path=DB_PATH)
            if d.get("catalog_sent")=="Si":
                emit_notification(c,"catalog_sent","📖 Catalogo inviato",f'{number} · {d.get("animal_name") or "Animale non indicato"}',pid,user["id"],db_path=DB_PATH)
            if calendar_event:
                c.execute("UPDATE calendar_events SET linked_practice_id=?,updated_at=?,updated_by=? WHERE id=? AND linked_practice_id IS NULL",(pid,stamp,user["id"],calendar_event_id))
                calendar_add_history(c,calendar_event_id,user["id"],"Creazione pratica","",number,stamp)
        self.redirect(f"/pratiche/{pid}")

    def practice(self,user,pid,error=""):
        q=parse_qs(urlparse(getattr(self,"path","")).query); back_url=safe_return_path((q.get("return_to") or [""])[0],"/archivio/pratiche")
        encoded_back=quote(back_url,safe=""); practice_view=f'/pratiche/{pid}?return_to={encoded_back}'
        with db() as c:
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            history=c.execute("SELECT h.*,u.display_name FROM practice_history h LEFT JOIN users u ON u.id=h.user_id WHERE practice_id=? ORDER BY h.created_at DESC",(pid,)).fetchall()
            whatsapp_msg=c.execute("SELECT * FROM whatsapp_messages WHERE practice_id=? ORDER BY created_at DESC LIMIT 1",(pid,)).fetchone()
        if not p:return self.send_error(404)
        tag_badges_raw=self.tag_badges(p)
        tag_badges_html='<br>'+tag_badges_raw if 'sub">-' not in tag_badges_raw else ''
        payment_value = p["payment_status"] if "payment_status" in p.keys() and p["payment_status"] else "Da saldare"
        payment_method_value = p["payment_method"] if "payment_method" in p.keys() and p["payment_method"] else "Non indicato"
        catalog_value = "Si" if "send_catalog" in p.keys() and p["send_catalog"] else "No"
        send_catalog_checked="checked" if p["send_catalog"]=="Si" else ""
        catalog_sent_checked="checked" if p["catalog_sent"]=="Si" else ""
        catalog_controls=f'''<form class="catalog-summary-form" method="post" action="/pratiche/{pid}/catalogo-inviato"><input type="hidden" name="practice_view" value="{esc(practice_view)}"><label class="modern-check"><input type="checkbox" name="send_catalog" value="Si" {send_catalog_checked} onchange="if(this.checked)this.form.catalog_sent.checked=false;this.form.submit()"> INVIARE CATALOGO</label><label class="modern-check"><input type="checkbox" name="catalog_sent" value="Si" {catalog_sent_checked} onchange="if(this.checked)this.form.send_catalog.checked=false;this.form.submit()"> CATALOGO INVIATO</label></form>'''
        urn_parts=[]
        if p["urn_notes"]: urn_parts.append(esc(p["urn_notes"]))
        if p["price_urn"]: urn_parts.append(money_it(money_value(p["price_urn"])))
        if p["urn_notes_2"]: urn_parts.append(esc(p["urn_notes_2"]))
        if p["price_urn_2"]: urn_parts.append(money_it(money_value(p["price_urn_2"])))
        invoice_value = p["invoice_number"] if "invoice_number" in p.keys() and p["invoice_number"] else ""
        invoice_date = p["invoice_date"] if "invoice_date" in p.keys() and p["invoice_date"] else ""
        invoice_total_value = p["invoice_total"] if "invoice_total" in p.keys() and p["invoice_total"] else f"{effective_total(p):.2f}"
        make_invoice_checked = "checked" if "make_invoice" in p.keys() and p["make_invoice"] == "Si" else ""
        invoice_box=f'''<div class="kv full"><small>Fattura</small><form class="invoice-inline" method="post" action="/pratiche/{pid}/fattura"><input type="hidden" name="practice_view" value="{esc(practice_view)}"><input name="invoice_number" value="{esc(invoice_value)}" placeholder="Numero fattura"><input type="date" name="invoice_date" value="{esc(invoice_date)}"><input name="invoice_total" value="{esc(invoice_total_value)}" inputmode="decimal" placeholder="Totale fattura €" aria-label="Totale fattura" pattern="[0-9]+([,.][0-9]{{1,2}})?" title="Solo numeri, es. 120,00"><label class="modern-check"><input type="checkbox" name="make_invoice" value="Si" {make_invoice_checked}> FARE FATTURA</label><button class="btn ghost">Salva fattura</button></form></div>'''
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
        no_whatsapp_form = f'''<form method="post" action="/pratiche/{pid}/stato" style="margin-top:14px"><input type="hidden" name="return_to" value="{esc(practice_view)}"><input type="hidden" name="status" value="{esc(p['status'])}"><input type="hidden" name="payment_status" value="{esc(payment_value)}"><label class="modern-check"><input type="checkbox" name="no_whatsapp_message" value="Si" {no_whatsapp_checked} onchange="this.form.submit()"> NO MESSAGGIO</label><small class="sub">Se spuntato, quando la pratica passa a Consegnato non parte il WhatsApp automatico.</small></form>'''
        whatsapp_block = f'''<div class="section"><h2>WhatsApp ringraziamento</h2>{no_whatsapp_note}<div class="kvs"><div class="kv"><small>Stato attuale</small><b>{esc(status_label)}</b></div><div class="kv"><small>Destinatario</small>{('+'+esc(recipient_show)) if recipient_show else '<span class="sub">Telefono mancante</span>'}</div></div>{f'<div class="flash warning">{esc(msg_error)}</div>' if msg_error else ''}<div class="actions" style="margin-top:14px"><a class="btn" href="/pratiche/{pid}/whatsapp-conferma">{whatsapp_button}</a>{cancel_form}</div>{no_whatsapp_form}</div>'''
        age_parts=[]
        if p["age_years"]: age_parts.append(f'{esc(p["age_years"])} {"anno" if str(p["age_years"]).strip()=="1" else "anni"}')
        if p["age_months"]: age_parts.append(f'{esc(p["age_months"])} {"mese" if str(p["age_months"]).strip()=="1" else "mesi"}')
        animal_age='<br><span class="sub">Età: '+', '.join(age_parts)+'</span>' if age_parts else '<br><span class="sub">Età non indicata</span>'
        animal2_block = f'<div class="kv"><small>Secondo animale</small>{esc(p["animal2_name"])}<br>{esc(p["animal2_species"])} {esc(p["animal2_weight"])} kg</div>' if "animal2_name" in p.keys() and p["animal2_name"] else ""
        total_w=calculated_service_total(p);total_d_raw=(p["total_text"] or "").strip();total_d=money_value(total_d_raw)
        practice_total=effective_total(p);paid_total=received_amount(p);due_total=outstanding_amount(p);deposit_total=money_value(p["deposit"])
        remaining_total=money_value(p["remaining_balance"]) if (p["remaining_balance"] or "").strip() else due_total
        estimate_fields=(("price_cremation","Cremazione"),("price_pickup","Ritiro"),("price_delivery","Riconsegna"),("price_cast","Calco"),("price_cast_2","Secondo calco"),("price_paw_cast","Calco polpastrello"),("price_nose_cast","Calco naso"),("price_evening","Serale"),("price_night","Notturno"),("price_holiday","Festivo"),("price_accessories","Accessori"),("price_accessories_2","Secondi accessori"))
        estimate_rows=[]
        for key,label in estimate_fields:
            raw_value=(p[key] or "").strip()
            if raw_value:estimate_rows.append(f'<div class="kv"><small>{label}</small><b>{money_it(money_value(raw_value))}</b></div>')
        urn_summary='<br>'.join(urn_parts) if urn_parts else '<span class="sub">Nessuna urna o prezzo inserito</span>'
        estimate_rows.insert(2,f'<div class="kv"><small>Urna</small>{urn_summary}{catalog_controls}</div>')
        economic_block=f'''<div class="section"><h2>Dati economici</h2><div class="economic-estimate"><h3>Voci del preventivo</h3><div class="kvs">{''.join(estimate_rows)}</div></div><div class="kvs"><div class="kv"><small>Totale pratica</small><b>{money_it(practice_total)}</b></div><div class="kv"><small>Totale W</small><b>{money_it(total_w)}</b></div><div class="kv"><small>Totale D</small><b>{money_it(total_d) if total_d_raw else "-"}</b></div><div class="kv"><small>Totale pagato {payment_channel(p)}</small><b>{money_it(paid_total)}</b></div><div class="kv"><small>Da pagare {payment_channel(p)}</small><b>{money_it(due_total)}</b></div><div class="kv"><small>Acconto {payment_channel(p)}</small><b>{money_it(deposit_total)}</b></div><div class="kv"><small>Rimanenza {payment_channel(p)}</small><b>{money_it(remaining_total)}</b></div><div class="kv"><small>Stato pagamento</small><b>{esc(payment_value)}</b></div><div class="kv"><small>Metodo</small><b>{esc(payment_method_value)}</b></div>{invoice_box}</div></div>'''
        hist_items=[]
        for h in history:
            old_value=compact_text(h["old_value"]); new_value=compact_text(h["new_value"]); note=compact_text(h["note"])
            change=f'{esc(old_value)} → {esc(new_value)}' if old_value and new_value else esc(new_value or old_value)
            hist_items.append(f'<div class="event"><b>{esc(h["event_type"])}</b>{f"<br><span>{change}</span>" if change else ""}{f"<br><span class=\"sub\">{esc(note)}</span>" if note else ""}<br><small class="sub">{esc(h["created_at"].replace("T"," "))} - {esc(h["display_name"] or "Sistema")}</small></div>')
        hist=''.join(hist_items) or '<p class="sub">Nessuna modifica registrata.</p>'
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
            pdf_block = f'<div class="actions"><a class="btn ghost" href="/pratiche/{pid}">Salva pratica</a><a class="btn ghost" href="/pratiche/{pid}/ddt-bozza.pdf">Apri bozza PDF</a><a class="btn ghost" href="/pratiche/{pid}/ddt-bozza-download.pdf">Salva bozza sul dispositivo</a>{final_action}</div><p class="sub">La pratica resta salvata in archivio. Il DDT numerato puo essere generato anche in un secondo momento, per esempio alla fine della pratica.</p>'
        body=f"""
        <main class="wrap">
          <div class="titlebar"><div><h1>{esc(p['practice_number'])} - {esc(p['animal_name'] or 'Animale da inserire')}</h1><div class="sub">Creata il {esc(p['created_at'].replace('T',' '))}</div></div><div class="actions"><a class="btn ghost" href="{esc(back_url)}">← Torna alla pagina precedente</a><a class="btn ghost" href="/pratiche/{pid}/modifica?return_to={encoded_back}">Modifica dati</a><a class="btn ghost" href="/pratiche/{pid}/firma">Firma su telefono</a></div></div>
          {'' if p['data_complete'] else '<div class="flash warning">Questa pratica contiene ancora dati da completare.</div>'}
          <section class="grid practice-layout">
            <div class="grid">
              <div class="section"><h2>Riepilogo</h2><div class="kvs"><div class="kv"><small>Stato</small>{self.status_badges(p)}{tag_badges_html}</div><div class="kv"><small>Speditore</small>{esc((p['owner_first_name'] or '')+' '+(p['owner_last_name'] or ''))}<br>{self.phone_action_buttons(p['owner_phone'])}{('<br>'+self.phone_action_buttons(p['owner_phone_2'])) if 'owner_phone_2' in p.keys() and p['owner_phone_2'] else ''}</div><div class="kv"><small>Animale</small>{esc(p['species'])} - {esc(p['breed'])}<br>{esc(p['estimated_weight'])} kg{animal_age}</div>{animal2_block}<div class="kv"><small>Sede</small><b>{esc(p['destination_branch'])}</b></div><div class="kv"><small>Origine</small><b>{esc(p['request_origin'])}</b></div><div class="kv"><small>Veterinario</small>{esc(p['clinic_name'])}<br>{esc(p['veterinarian_name'])}</div><div class="kv"><small>Catalogo urna</small><b>{esc(catalog_value)}</b></div></div></div>
              {economic_block}
              <div class="section"><h2>Firma proprietario</h2><p class="sub">{'Firma salvata.' if p['signature_data'] else 'Firma non ancora salvata.'}</p><a class="btn ghost" href="/pratiche/{pid}/firma">Apri firma</a></div>
              {whatsapp_block}
              <div class="section"><h2>Documento DCS / DDT</h2><p>{ddt}</p>{pdf_block}</div>
              <div class="section"><h2>Note</h2><p>{esc(p['notes']) or '<span class="sub">Nessuna nota.</span>'}</p></div>
              <div class="section danger"><h2>Sposta nel Cestino</h2><p class="danger-note">La pratica non verra cancellata definitivamente: verra nascosta da Dashboard e Archivio e potrai ripristinarla dal Cestino.</p><form method="post" action="/pratiche/{pid}/elimina" onsubmit="return confirm('Spostare questa pratica nel Cestino? Potrai ripristinarla in seguito.')"><button class="btn danger-btn" style="margin-top:12px">Sposta nel Cestino</button></form></div>
            </div>
            <aside class="section"><h2>Cronologia modifiche</h2><div class="timeline">{hist}</div></aside>
          </section>
        </main>"""
        body=body.replace(f'<a class="btn ghost" href="/pratiche/{pid}/firma">Firma su telefono</a>',"")
        body=body.replace(f'<div class="kv"><small>Catalogo urna</small><b>{esc(catalog_value)}</b></div>',"")
        if error:
            body=body.replace('<main class="wrap">','<main class="wrap"><div class="flash warning">'+esc(error)+'</div>',1)
        self.send_html(layout(p["practice_number"],body,user))

    def edit_page(self,user,pid,draft=None,error=""):
        q=parse_qs(urlparse(getattr(self,"path","")).query);back_url=safe_return_path((q.get("return_to") or [""])[0],"/archivio/pratiche")
        with db() as c:p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p:return self.send_error(404)
        display=draft if draft is not None else p
        autosave=f'''<div id="practiceAutosaveStatus" class="autosave-status" data-state="saved" role="status"><span data-autosave-label>Salvato</span><small data-autosave-time>Ultimo salvataggio: —</small><button class="autosave-retry" data-autosave-retry type="button" hidden>Riprova</button></div>'''
        error_html=f'<div class="flash warning">{esc(error)}</div>' if error else ''
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Modifica {esc(p['practice_number'])}</h1><div class="sub">Completa o correggi i dati della pratica.</div>{autosave}</div><div class="actions"><button class="btn" form="practiceForm">Salva modifiche</button><button class="btn ghost" form="practiceForm" name="save_and_return" value="1">Salva e torna</button><a class="btn ghost" href="{esc(back_url)}">Annulla</a></div></div>{error_html}<form method="post" id="practiceForm" data-autosave-url="/api/pratiche/{pid}/autosave" data-updated-at="{esc(p['updated_at'])}"><input type="hidden" name="return_to" value="{esc(back_url)}"><div class="grid form-grid">{self.fields_html(display,user)}</div><div class="actions" style="margin-top:18px"><button class="btn">Salva modifiche</button><button class="btn ghost" name="save_and_return" value="1">Salva e torna</button><a class="btn ghost" href="{esc(back_url)}">Annulla</a></div></form></main>'''
        self.send_html(layout("Modifica pratica",body,user))

    def practice_autosave(self,user,pid):
        request=self.form()
        try:
            changes=json.loads(request.get("changes_json") or "{}")
            if not isinstance(changes,dict) or len(changes)>120:raise ValueError("Richiesta non valida")
        except (ValueError,TypeError,json.JSONDecodeError):
            return self.send_json({"ok":False,"error":"Dati di autosalvataggio non validi"},400)
        try:
            with db() as c:
                previous=c.execute("SELECT * FROM practices WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",(pid,)).fetchone()
                if not previous:return self.send_json({"ok":False,"error":"Pratica non trovata"},404)
                if (request.get("updated_at") or "")!=(previous["updated_at"] or ""):
                    return self.send_json({"ok":False,"error":"La pratica è stata modificata da un altro dispositivo. Ricarica la pagina prima di continuare.","conflict":True},409)
                protected={"id","practice_number","status","created_at","updated_at","created_by","deleted_at","deleted_by","ddt_number","ddt_pdf","public_token","signature_data"}
                if user["role"]!="admin": protected=protected|{"operator_name"}
                submitted={key:("" if value is None else str(value)) for key,value in changes.items() if key in previous.keys() and key not in protected}
                if not submitted:return self.send_json({"ok":True,"updated_at":previous["updated_at"],"saved_at":datetime.now(ROME_TZ).strftime("%H:%M"),"saved_fields":[]})
                merged={key:("" if previous[key] is None else str(previous[key])) for key in previous.keys()}
                merged.update(submitted)
                normalized=self.normalized_fields(merged)
                error=self.validation_error(normalized)
                if error:return self.send_json({"ok":False,"error":error},422)
                allowed=set(normalized);requested=set(submitted)&allowed
                economic=set(MONEY_FIELDS)|{"payment_status","payment_method","invoice_total","urn_id","urn_id_2","price_urn_2"}
                address={"owner_street","owner_city","owner_province","owner_zip","owner_address","origin_mode","origin_text","request_origin","origin_veterinarian_id","owner_veterinarian_id","veterinarian_id","transport_method","vehicle_plate"}
                dependencies=set()
                if requested&economic:dependencies|={"total_service","remaining_balance","invoice_total","payment_status","urn_notes","urn_notes_2","price_urn","price_urn_2","tag_calco_urna"}
                if requested&address:dependencies|={"owner_address","pickup_address","pickup_address_mode","origin_mode","origin_text","provenance","transporter_mode","transport_method","vehicle_plate","clinic_name","owner_first_name","owner_last_name","owner_company","owner_phone","owner_city","owner_zip","owner_province"}
                if requested&{"catalog_sent","send_catalog"}:dependencies|={"catalog_sent","send_catalog"}
                update_keys=[key for key in normalized if key in requested|dependencies and key in previous.keys() and compact_text(previous[key])!=compact_text(normalized[key])]
                if not update_keys:return self.send_json({"ok":True,"updated_at":previous["updated_at"],"saved_at":datetime.now(ROME_TZ).strftime("%H:%M"),"saved_fields":[]})
                conflict=self.invoice_conflict(c,normalized.get("invoice_number"),pid) if "invoice_number" in update_keys else None
                if conflict:return self.send_json({"ok":False,"error":f'Numero fattura già usato nella pratica {conflict["practice_number"]}'},409)
                stamp=datetime.now().isoformat(timespec="microseconds");assignments=','.join(f"{key}=?" for key in update_keys)
                cursor=c.execute(f"UPDATE practices SET {assignments},data_complete=?,updated_at=? WHERE id=? AND updated_at=?",[normalized[key] for key in update_keys]+[self.is_complete({**merged,**normalized}),stamp,pid,previous["updated_at"]])
                if not cursor.rowcount:return self.send_json({"ok":False,"error":"La pratica è stata modificata altrove. Ricarica la pagina.","conflict":True},409)
                if "urn_id" in update_keys:self.sync_practice_urn(c,pid,previous["urn_id"],normalized.get("urn_id"),user["id"])
                if "urn_id_2" in update_keys:self.sync_practice_urn(c,pid,previous["urn_id_2"],normalized.get("urn_id_2"),user["id"])
                current=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
                if requested&economic:self.reconcile_payment_movements(c,pid,previous,current,user["id"],"Salvataggio automatico")
                summary=", ".join(key.replace("_"," ") for key in update_keys[:8])
                c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Salvataggio automatico",summary,user["id"],stamp))
            return self.send_json({"ok":True,"updated_at":stamp,"saved_at":datetime.now(ROME_TZ).strftime("%H:%M"),"saved_fields":update_keys})
        except Exception:
            print(f"[PRACTICE_AUTOSAVE] pratica={pid}\n{traceback.format_exc()}",flush=True)
            return self.send_json({"ok":False,"error":"Salvataggio automatico non riuscito. I dati restano nella schermata; puoi riprovare o usare Salva modifiche."},500)

    def signature_page(self,user,pid,error=""):
        with db() as c:p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        if not p:return self.send_error(404)
        owner=esc(((p["owner_first_name"] or "")+" "+(p["owner_last_name"] or "")).strip())
        error_html=f'<div class="flash warning">{esc(error)}</div>' if error else ''
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Firma proprietario</h1><div class="sub">{owner} - pratica {esc(p['practice_number'])}</div></div></div>{error_html}<section class="section"><p class="sub">Fai firmare qui il proprietario con il dito. La firma verrà salvata nella pratica e inserita nel PDF DDT.</p><form method="post" id="signatureForm"><canvas class="signature-pad" id="pad"></canvas><input type="hidden" name="signature_data" id="signatureData"><div class="actions" style="margin-top:14px"><button class="btn" type="submit">Salva firma</button><button class="btn ghost" type="button" id="clearPad">Cancella</button><a class="btn ghost" href="/pratiche/{pid}">Annulla</a></div></form></section><script>
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
            return self.signature_page(user,pid,error="Firma non valida: prova a firmare di nuovo prima di salvare.")
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
        form=self.form(); d=self.normalized_fields(form); stamp=now(); assignments=','.join(f'{k}=?' for k in d)
        error=self.validation_error(d)
        if error: return self.edit_page(user,pid,draft=d,error=error)
        with db() as c:
            previous=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            if not previous:return self.send_error(404)
            if user["role"]!="admin": d["operator_name"]=previous["operator_name"]
            conflict=self.invoice_conflict(c,d.get("invoice_number"),pid)
            if conflict:return self.edit_page(user,pid,draft=d,error=f'Numero fattura già usato nella pratica {conflict["practice_number"]}')
            field_labels={"animal_name":"Nome animale","owner_first_name":"Nome proprietario","owner_last_name":"Cognome proprietario","owner_phone":"Telefono proprietario","owner_phone_2":"Secondo telefono","service_type":"Tipo cremazione","destination_branch":"Sede","notes":"Note","send_catalog":"Inviare catalogo","catalog_sent":"Catalogo inviato","send_estremi":"Inviare estremi","use_voucher":"Usa buono","payment_method":"Metodo di pagamento","invoice_number":"Numero fattura","invoice_date":"Data fattura","invoice_total":"Totale fattura","make_invoice":"Fare fattura",**MONEY_FIELDS}
            changes=[]
            for key,new_value in d.items():
                old_value=str(previous[key] or "") if key in previous.keys() else ""
                if compact_text(old_value)!=compact_text(new_value):
                    changes.append((field_labels.get(key,key.replace("_"," ").title()),old_value,new_value))
            requested_status=form.get("status",previous["status"])
            if requested_status not in STATES or (requested_status=="Smaltito" and d.get("service_type")!="Cremazione collettiva"): requested_status=previous["status"]
            if requested_status!=previous["status"]:
                changes.append(("Stato pratica",previous["status"],requested_status))
            c.execute(f"UPDATE practices SET {assignments},status=?,data_complete=?,updated_at=? WHERE id=?",list(d.values())+[requested_status,self.is_complete(d),stamp,pid])
            self.sync_practice_urn(c,pid,previous["urn_id"],d.get("urn_id"),user["id"])
            self.sync_practice_urn(c,pid,previous["urn_id_2"],d.get("urn_id_2"),user["id"])
            p=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.reconcile_payment_movements(c,pid,previous,p,user["id"],"Modifica pratica")
            wanted_prefix,_=practice_code_prefix(d["service_type"])
            current_number=p["practice_number"] or ""
            if not p["ddt_number"] and wanted_prefix in ("CR-","SM-") and not current_number.startswith(wanted_prefix):
                new_number=next_practice_code(c,d["service_type"])
                c.execute("UPDATE practices SET practice_number=? WHERE id=?",(new_number,pid))
                c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Cambio codice pratica",current_number,new_number,user["id"],stamp))
            self.sync_voucher(c,pid,d)
            self.apply_used_voucher(c,pid,d,user["id"])
            for label,old_value,new_value in changes:
                c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,f"Modifica {label}",old_value,new_value,user["id"],stamp))
            if not changes:
                c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(pid,"Dati verificati","Nessuna variazione ai dati",user["id"],stamp))
            emit_notification(c,"practice_updated","✏️ Pratica modificata",f'{previous["practice_number"]} · {d.get("animal_name") or "Animale non indicato"}',pid,user["id"],db_path=DB_PATH)
            if previous["status"]!=requested_status:
                if previous["status"]=="Consegnato" and requested_status!="Consegnato": self.cancel_whatsapp_scheduled(c,pid,user["id"],"Pratica spostata da Consegnato")
                elif requested_status=="Consegnato": self.schedule_whatsapp_thanks(c,pid,user["id"])
                if requested_status=="Consegnato": emit_notification(c,"practice_delivered","📦 Pratica consegnata",f'{d.get("animal_name") or previous["practice_number"]}\nCliente: {d.get("owner_first_name","")} {d.get("owner_last_name","")}',pid,user["id"],db_path=DB_PATH)
                elif requested_status=="Da consegnare": emit_notification(c,"delivery_scheduled","📅 Consegna programmata",d.get("animal_name") or previous["practice_number"],pid,user["id"],db_path=DB_PATH)
            if (previous["payment_status"] or "Da saldare") != d["payment_status"] and d["payment_status"]=="Pagato":
                emit_notification(c,"payment_received","💰 Pagamento ricevuto",f'{d.get("owner_first_name","")} {d.get("owner_last_name","")}\n{money_it(effective_total(d))}',pid,user["id"],db_path=DB_PATH)
            if previous["catalog_sent"]!="Si" and d.get("catalog_sent")=="Si":
                emit_notification(c,"catalog_sent","📖 Catalogo inviato",f'{previous["practice_number"]} · {d.get("animal_name") or "Animale non indicato"}',pid,user["id"],db_path=DB_PATH)
        back_url=safe_return_path(form.get("return_to"),"/archivio/pratiche")
        self.redirect(back_url if form.get("save_and_return")=="1" else f'/pratiche/{pid}?return_to={quote(back_url,safe="")}')

    def save_invoice(self,user,pid):
        f=self.form();number=f.get("invoice_number","").strip();invoice_date=f.get("invoice_date","").strip();invoice_total=normalize_money_text(f.get("invoice_total",""));make_invoice="Si" if f.get("make_invoice")=="Si" else ""
        if invoice_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}",invoice_date):return self.practice(user,pid,error="Data fattura non valida: usa il formato AAAA-MM-GG.")
        if invoice_total and not re.fullmatch(r"\d+(?:\.\d{1,2})?",invoice_total):return self.practice(user,pid,error="Totale fattura non valido: inserisci solo un numero, es. 120,00.")
        with db() as c:
            current=c.execute("SELECT * FROM practices WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",(pid,)).fetchone()
            if not current:return self.send_error(404)
            if not invoice_total:invoice_total=f"{effective_total(current):.2f}"
            conflict=self.invoice_conflict(c,number,pid)
            if conflict:return self.practice(user,pid,error=f'Numero fattura già usato nella pratica {conflict["practice_number"]}')
            c.execute("UPDATE practices SET invoice_number=?,invoice_date=?,invoice_total=?,make_invoice=?,updated_at=? WHERE id=?",(number,invoice_date,invoice_total,make_invoice,now(),pid))
            old=f'{current["invoice_number"] or "Non inserita"} · {date_it(current["invoice_date"])} · {money_it(money_value(current["invoice_total"])) if current["invoice_total"] else "Totale non inserito"}';new=f'{number or "Non inserita"} · {date_it(invoice_date)} · {money_it(money_value(invoice_total))}'
            c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Fattura",old,new,user["id"],now()))
        self.redirect(safe_return_path(f.get("practice_view"),f"/pratiche/{pid}"))

    def catalog_sent(self,user,pid):
        f=self.form();new_sent="Si" if f.get("catalog_sent")=="Si" else "";new_send="Si" if f.get("send_catalog")=="Si" and not new_sent else "";stamp=now()
        with db() as c:
            current=c.execute("SELECT send_catalog,catalog_sent FROM practices WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",(pid,)).fetchone()
            if not current:return self.send_error(404)
            old_value="CATALOGO INVIATO" if current["catalog_sent"]=="Si" else "INVIARE CATALOGO" if current["send_catalog"]=="Si" else "Nessuna etichetta catalogo"
            new_value="CATALOGO INVIATO" if new_sent else "INVIARE CATALOGO" if new_send else "Nessuna etichetta catalogo"
            if (current["send_catalog"] or "")==new_send and (current["catalog_sent"] or "")==new_sent:return self.redirect(safe_return_path(f.get("practice_view"),f"/pratiche/{pid}"))
            c.execute("UPDATE practices SET catalog_sent=?,send_catalog=?,updated_at=? WHERE id=?",(new_sent,new_send,stamp,pid))
            c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Catalogo urna",old_value,new_value,user["id"],stamp))
            if new_sent and current["catalog_sent"]!="Si":
                practice=c.execute("SELECT practice_number,animal_name FROM practices WHERE id=?",(pid,)).fetchone()
                emit_notification(c,"catalog_sent","📖 Catalogo inviato",f'{practice["practice_number"]} · {practice["animal_name"] or "Animale non indicato"}',pid,user["id"],db_path=DB_PATH)
        self.redirect(safe_return_path(f.get("practice_view"),f"/pratiche/{pid}"))

    def change_state(self,user,pid):
        f=self.form(); new=f.get("status",""); payment=f.get("payment_status","Da saldare"); requested_invoice=f.get("invoice_number"); no_whatsapp="Si" if f.get("no_whatsapp_message")=="Si" else ""
        if new not in STATES or payment not in PAYMENT_STATES:return self.practice(user,pid,error="Stato pratica o stato pagamento non validi.")
        with db() as c:
            old=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            if not old:return self.send_error(404)
            invoice=old["invoice_number"] if requested_invoice is None else requested_invoice.strip()
            conflict=self.invoice_conflict(c,invoice,pid)
            if conflict:return self.practice(user,pid,error=f'Numero fattura già usato nella pratica {conflict["practice_number"]}')
            if new=="Smaltito" and old["service_type"]!="Cremazione collettiva": return self.practice(user,pid,error="Smaltito è disponibile solo per la cremazione collettiva.")
            old_payment=old["payment_status"] or "Da saldare"
            c.execute("UPDATE practices SET status=?,payment_status=?,invoice_number=?,no_whatsapp_message=?,updated_at=? WHERE id=?",(new,payment,invoice,no_whatsapp,now(),pid))
            updated=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.reconcile_payment_movements(c,pid,old,updated,user["id"],"Aggiornamento stato pagamento")
            new_value=f'{new} + {payment}' + (f' - Fattura {invoice}' if invoice else '') + (" - NO MESSAGGIO" if no_whatsapp else "")
            old_value=f'{old["status"]} + {old_payment}' + (f' - Fattura {old["invoice_number"]}' if old["invoice_number"] else '') + (" - NO MESSAGGIO" if old["no_whatsapp_message"]=="Si" else "")
            c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Cambio stati",old_value,new_value,user["id"],now()))
            if no_whatsapp:
                self.cancel_whatsapp_scheduled(c,pid,user["id"],"NO MESSAGGIO selezionato")
            elif old["status"] == "Consegnato" and new != "Consegnato":
                self.cancel_whatsapp_scheduled(c,pid,user["id"],"Pratica spostata da Consegnato a un altro stato")
            elif old["status"] != "Consegnato" and new == "Consegnato":
                self.schedule_whatsapp_thanks(c,pid,user["id"])
            if old["status"]!=new and new=="Consegnato":
                emit_notification(c,"practice_delivered","📦 Pratica consegnata",f'{old["animal_name"] or old["practice_number"]}\nCliente: {(old["owner_first_name"] or "")} {(old["owner_last_name"] or "")}',pid,user["id"],db_path=DB_PATH)
            elif old["status"]!=new and new=="Da consegnare":
                emit_notification(c,"delivery_scheduled","📅 Consegna programmata",f'{old["animal_name"] or old["practice_number"]} · {(old["owner_first_name"] or "")} {(old["owner_last_name"] or "")}',pid,user["id"],db_path=DB_PATH)
            if old_payment!=payment and payment=="Pagato":
                emit_notification(c,"payment_received","💰 Pagamento ricevuto",f'{(old["owner_first_name"] or "")} {(old["owner_last_name"] or "")}\n{money_it(effective_total(old))}',pid,user["id"],db_path=DB_PATH)
        self.redirect(safe_return_path(f.get("practice_view"),f"/pratiche/{pid}"))

    def quick_payment(self,user,pid):
        form=self.form(); payment=form.get("payment_status","Da saldare"); amount=normalize_money_text(form.get("payment_amount","")); method=form.get("payment_method","").strip(); invoice=form.get("invoice_number","").strip(); invoice_total=normalize_money_text(form.get("invoice_total","")); invoice_date=form.get("invoice_date","").strip()
        if payment not in PAYMENT_STATES or method not in PAYMENT_METHODS or (amount and not re.fullmatch(r"\d+(?:\.\d{1,2})?",amount)) or (invoice_total and not re.fullmatch(r"\d+(?:\.\d{1,2})?",invoice_total)) or (invoice_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}",invoice_date)): return self.practice(user,pid,error="Dati del pagamento non validi: controlla importo, totale fattura e data (usa solo numeri e il formato data AAAA-MM-GG).")
        with db() as c:
            row=c.execute("SELECT * FROM practices WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",(pid,)).fetchone()
            if not row:return self.send_error(404)
            conflict=self.invoice_conflict(c,invoice,pid)
            if conflict:return self.practice(user,pid,error=f'Numero fattura già usato nella pratica {conflict["practice_number"]}')
            old=row["payment_status"] or "Da saldare"; stamp=now()
            deposit=amount if payment=="Acconto" and amount else row["deposit"]
            due=effective_total(row)
            if payment!="Pagato" and due and money_value(deposit)>=due: payment="Pagato"
            remaining=0.0 if payment=="Pagato" else max(0.0,due-money_value(deposit))
            c.execute("UPDATE practices SET payment_status=?,payment_method=?,payment_amount=?,deposit=?,remaining_balance=?,invoice_number=?,invoice_total=?,invoice_date=?,updated_at=? WHERE id=?",(payment,method,amount,deposit,f"{remaining:.2f}",invoice,invoice_total or row["invoice_total"],invoice_date,stamp,pid))
            updated=c.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.reconcile_payment_movements(c,pid,row,updated,user["id"],"Pagamento rapido")
            c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Pagamento rapido",old,f'{payment}' + (f' · {money_it(money_value(amount))}' if amount else ''),user["id"],stamp))
            if payment=="Pagato" and old!="Pagato":
                owner=f'{row["owner_first_name"] or ""} {row["owner_last_name"] or ""}'.strip()
                emit_notification(c,"payment_received","💰 Pagamento ricevuto",f'{owner}\n{money_it(money_value(amount) or effective_total(row))}',pid,user["id"],db_path=DB_PATH)
        return self.redirect(safe_return_path(form.get("return_to") or self.headers.get("Referer"),"/"))

    def quick_state(self,user,pid):
        form=self.form(); new=form.get("status",""); ajax=form.get("ajax")=="1"
        if new not in STATES:return self.send_json({"ok":False,"error":"Stato pratica non valido"},400) if ajax else self.practice(user,pid,error="Stato pratica non valido.")
        with db() as c:
            old=c.execute("SELECT * FROM practices WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",(pid,)).fetchone()
            if not old:return self.send_json({"ok":False,"error":"Pratica non trovata"},404) if ajax else self.send_error(404)
            if new=="Smaltito" and old["service_type"]!="Cremazione collettiva":return self.send_json({"ok":False,"error":"Smaltito è disponibile solo per la cremazione collettiva"},400) if ajax else self.practice(user,pid,error="Smaltito è disponibile solo per la cremazione collettiva.")
            if old["status"]!=new:
                stamp=now();c.execute("UPDATE practices SET status=?,updated_at=? WHERE id=?",(new,stamp,pid))
                c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Cambio stato rapido",old["status"],new,user["id"],stamp))
                if old["status"]=="Consegnato" and new!="Consegnato":self.cancel_whatsapp_scheduled(c,pid,user["id"],"Pratica spostata da Consegnato")
                elif old["status"]!="Consegnato" and new=="Consegnato":self.schedule_whatsapp_thanks(c,pid,user["id"])
                if new=="Consegnato":emit_notification(c,"practice_delivered","📦 Pratica consegnata",old["animal_name"] or old["practice_number"],pid,user["id"],db_path=DB_PATH)
                elif new=="Da consegnare":emit_notification(c,"delivery_scheduled","📅 Consegna programmata",old["animal_name"] or old["practice_number"],pid,user["id"],db_path=DB_PATH)
        if ajax:return self.send_json({"ok":True,"status":new,"practice_id":pid})
        return self.redirect(safe_return_path(form.get("return_to") or self.headers.get("Referer"),"/"))

    def resend_whatsapp(self,user,pid):
        if user["role"] != "admin":
            return self.send_error(403)
        f=self.form()
        if f.get("confirm_send") != "SI":
            return self.whatsapp_confirm_page(user,pid,error="Devi confermare l'invio prima di procedere.")
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

    def whatsapp_message_action(self,user,msg_id,action):
        return_to=safe_return_path(self.headers.get("Referer"),"/conversazioni-whatsapp")
        with db() as c:
            msg=c.execute("SELECT * FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()
            if not msg:
                return self.send_error(404)
            if action == "annulla":
                if msg["status"] != "programmato":
                    return self.send_error(409,"Solo un messaggio ancora programmato può essere annullato")
                stamp=whatsapp_now()
                reason="Invio programmato annullato manualmente"
                c.execute("UPDATE whatsapp_messages SET status='annullato',last_error=?,updated_at=? WHERE id=? AND status='programmato'",(reason,stamp,msg_id))
                c.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(msg["practice_id"],"WhatsApp annullato",reason,user["id"],stamp))
            elif action == "riprova":
                if msg["status"] != "fallito":
                    return self.send_error(409,"È possibile riprovare soltanto un messaggio fallito")
                if msg["message_id"]:
                    stamp=whatsapp_now()
                    c.execute("UPDATE whatsapp_messages SET status='accettato_da_meta',sent_at=COALESCE(sent_at,?),last_error='',updated_at=? WHERE id=?",(stamp,stamp,msg_id))
                else:
                    stamp=whatsapp_now()
                    changed=c.execute("""UPDATE whatsapp_messages SET status='in_invio',attempts=attempts+1,
                                         last_attempt_at=?,failed_at=NULL,updated_at=? WHERE id=? AND status='fallito' AND message_id IS NULL""",(stamp,stamp,msg_id)).rowcount
                    if changed:
                        self.send_whatsapp_message(c,msg_id,manual=False,user_id=user["id"],attempt_recorded=True)
        self.redirect(return_to)

    def whatsapp_confirm_page(self,user,pid,error=""):
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
        error_html=f'<div class="flash warning">{esc(error)}</div>' if error else ''
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>{btn}</h1><div class="sub">Conferma invio template WhatsApp per la pratica {esc(p['practice_number'])}</div></div><a class="btn ghost" href="/pratiche/{pid}">Torna alla pratica</a></div>{error_html}{warning}<section class="section"><h2>Dati invio</h2><div class="kvs"><div class="kv"><small>Destinatario</small><b>+{esc(phone)}</b></div><div class="kv"><small>Template</small><b>{esc(template)}</b></div><div class="kv"><small>Lingua</small><b>{esc(payload['template']['language']['code'])}</b></div><div class="kv"><small>Nome cliente</small><b>{esc(nome_cliente)}</b></div><div class="kv"><small>Nome animale</small><b>{esc(nome_animale)}</b></div></div><form method="post" action="/pratiche/{pid}/whatsapp" onsubmit="return confirm('Confermi invio WhatsApp a +{esc(phone)} con template {esc(template)}?')"><input type="hidden" name="confirm_send" value="SI"><button class="btn" style="margin-top:18px">{btn}</button></form></section></main>'''
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
                display_number=r["original_practice_number"] or r["practice_number"]
                body_rows.append(f'''<tr><td>{esc(date_it(r["pickup_date"] or r["created_at"]))}</td><td><a href="/pratiche/{r["id"]}"><b>{esc(display_number)}</b></a><br><small>Cestinata il {esc((r["deleted_at"] or "").replace("T"," "))}</small></td><td>{animal}</td><td>{owner}</td><td>{esc(r["clinic_name"] or "-")}</td><td><div class="actions"><form method="post" action="/pratiche/{r["id"]}/ripristina" onsubmit="return confirm('Ripristinare questa pratica?')"><button class="btn ghost">Ripristina</button></form><form method="post" action="/pratiche/{r["id"]}/elimina-definitiva" onsubmit="return confirm('Sei sicuro di voler eliminare definitivamente questa pratica?') && confirm('Questa operazione e irreversibile.')"><input type="hidden" name="confirm_delete" value="ELIMINA DEFINITIVAMENTE"><button class="btn danger-btn">Elimina definitivamente</button></form></div></td></tr>''')
            content=f'''<div class="trash-note">Le pratiche nel Cestino non compaiono in Dashboard e Archivio. Ripristinale se sono state eliminate per errore.</div><div class="tablebox"><table><thead><tr><th>Data recupero</th><th>Pratica</th><th>Animale</th><th>Speditore</th><th>Veterinario</th><th>Azioni</th></tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>'''
        else:
            content='<section class="section empty-state">Il Cestino e vuoto.</section>'
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Cestino</h1><div class="sub">Pratiche eliminate ma ancora recuperabili.</div></div><a class="btn ghost" href="/archivio/pratiche">Torna all archivio</a></div>{content}</main>'''
        self.send_html(layout("Cestino", body, user))

    def sync_sequence_counter(self,c,prefix):
        key="next_cr_number" if prefix=="CR" else "next_sm_number"
        values=[sequence_code_parts(row["practice_number"]) for row in c.execute("SELECT practice_number FROM practices WHERE (deleted_at IS NULL OR deleted_at='') AND practice_number LIKE ?",(f"{prefix}-%",))]
        maximum=max((parts[1] for parts in values if parts),default=0)
        c.execute("UPDATE settings SET value=? WHERE key=?",(str(maximum+1),key))

    def shift_sequence_after_delete(self,c,number,user_id,stamp):
        parts=sequence_code_parts(number)
        if not parts:return
        prefix,removed,width=parts
        rows=c.execute("SELECT id,practice_number FROM practices WHERE (deleted_at IS NULL OR deleted_at='') AND practice_number LIKE ? ORDER BY practice_number",(f"{prefix}-%",)).fetchall()
        for row in rows:
            current=sequence_code_parts(row["practice_number"])
            if not current or current[1]<=removed:continue
            new_number=format_sequence_code(prefix,current[1]-1,max(width,current[2]))
            c.execute("UPDATE practices SET practice_number=?,updated_at=? WHERE id=?",(new_number,stamp,row["id"]))
            c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(row["id"],"Rinumerazione automatica",row["practice_number"],new_number,user_id,stamp))
        self.sync_sequence_counter(c,prefix)

    def shift_sequence_for_restore(self,c,number,user_id,stamp):
        parts=sequence_code_parts(number)
        if not parts:return
        prefix,target,width=parts
        rows=c.execute("SELECT id,practice_number FROM practices WHERE (deleted_at IS NULL OR deleted_at='') AND practice_number LIKE ? ORDER BY practice_number DESC",(f"{prefix}-%",)).fetchall()
        for row in rows:
            current=sequence_code_parts(row["practice_number"])
            if not current or current[1]<target:continue
            new_number=format_sequence_code(prefix,current[1]+1,max(width,current[2]))
            c.execute("UPDATE practices SET practice_number=?,updated_at=? WHERE id=?",(new_number,stamp,row["id"]))
            c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(row["id"],"Rinumerazione automatica",row["practice_number"],new_number,user_id,stamp))

    def delete_practice(self,user,pid):
        stamp=now()
        try:
            with db() as c:
                p=c.execute("SELECT id,deleted_at,urn_id,practice_number FROM practices WHERE id=?",(pid,)).fetchone()
                if not p:
                    return self.error_page("Pratica non trovata", "La pratica non esiste o e gia stata eliminata definitivamente.", "/pratiche")
                if p["deleted_at"]:
                    return self.redirect("/cestino")
                original=p["practice_number"]
                if sequence_code_parts(original):
                    placeholder=f"DEL-{pid}-{original}"
                    c.execute("UPDATE practices SET practice_number=?,original_practice_number=?,deleted_at=?,deleted_by=?,updated_at=? WHERE id=?",(placeholder,original,stamp,user["id"],stamp,pid))
                    self.shift_sequence_after_delete(c,original,user["id"],stamp)
                else:
                    c.execute("UPDATE practices SET deleted_at=?,deleted_by=?,updated_at=? WHERE id=?",(stamp,user["id"],stamp,pid))
                self.adjust_urn_stock(c,p["urn_id"],1,"Restituita per pratica cestinata",pid,user["id"],"Pratica spostata nel Cestino")
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
                p=c.execute("SELECT id,deleted_at,urn_id,practice_number,original_practice_number FROM practices WHERE id=?",(pid,)).fetchone()
                if not p:
                    return self.error_page("Pratica non trovata", "La pratica non esiste o e stata eliminata definitivamente.", "/cestino")
                original=p["original_practice_number"] or p["practice_number"]
                if sequence_code_parts(original):
                    self.shift_sequence_for_restore(c,original,user["id"],stamp)
                    c.execute("UPDATE practices SET practice_number=?,original_practice_number=NULL,deleted_at=NULL,deleted_by=NULL,updated_at=? WHERE id=?",(original,stamp,pid))
                    self.sync_sequence_counter(c,sequence_code_parts(original)[0])
                else:
                    c.execute("UPDATE practices SET deleted_at=NULL,deleted_by=NULL,updated_at=? WHERE id=?",(stamp,pid))
                self.adjust_urn_stock(c,p["urn_id"],-1,"Utilizzata per pratica ripristinata",pid,user["id"],"Pratica ripristinata dal Cestino")
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
