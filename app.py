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
from datetime import datetime
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
            "voucher_requested": "TEXT"
        }
        existing = {row["name"] for row in c.execute("PRAGMA table_info(practices)")}
        for name, definition in extra_columns.items():
            if name not in existing:
                c.execute(f"ALTER TABLE practices ADD COLUMN {name} {definition}")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_practices_ddt_share_token ON practices(ddt_share_token)")
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
        if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            c.execute(
                "INSERT INTO users(username,password_hash,display_name,role) VALUES(?,?,?,?)",
                ("admin", password_hash("petparadise"), "Amministratore", "admin"),
            )


def esc(value):
    return html.escape(str(value or ""), quote=True)


def now():
    return datetime.now().isoformat(timespec="seconds")


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
        nav = f'''<nav class="nav"><a href="/">Dashboard</a><a href="/pratiche"><span>Archivio </span>pratiche</a><a href="/database-mesi">Database mesi</a><a href="/veterinari">Veterinari</a><a href="/nuova" class="btn">+ Nuova pratica</a><a href="/logout">Esci</a></nav>'''
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Pet Paradise Manager</title><style>{CSS}</style></head><body><header class="top"><a class="brand" href="/">Pet Paradise <small>MANAGER</small></a>{nav}</header>{body}{APP_JS}</body></html>'''


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
        if path == "/assets/company_logo.png" and (ASSETS / "company_logo.png").exists(): return self.send_png(ASSETS / "company_logo.png")
        match = re.fullmatch(r"/pubblici/ddt/([A-Za-z0-9_-]+)\.pdf", path)
        if match: return self.public_ddt(match.group(1))
        if path == "/login": return self.login_page()
        if path == "/logout": return self.logout()
        user = self.require_user()
        if not user: return
        if path == "/": return self.dashboard(user)
        if path == "/diagnostica": return self.diagnostics(user)
        if path == "/nuova": return self.new_page(user)
        if path == "/pratiche": return self.archive(user)
        if path == "/database-mesi": return self.monthly_database(user)
        if path == "/veterinari": return self.veterinarians_page(user)
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
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/login": return self.login_submit()
        user = self.require_user()
        if not user: return
        if path == "/nuova": return self.create_practice(user)
        if path == "/veterinari": return self.save_veterinarian(user)
        match = re.fullmatch(r"/veterinari/(\d+)/buono-usato", path)
        if match: return self.use_voucher(user, int(match.group(1)))
        match = re.fullmatch(r"/buoni/(\d+)/usato", path)
        if match: return self.use_specific_voucher(user, int(match.group(1)))
        match = re.fullmatch(r"/pratiche/(\d+)/stato", path)
        if match: return self.change_state(user, int(match.group(1)))
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
        cards=''.join(f'<a class="card stat" href="/pratiche?stato={quote(s)}"><span>{esc(s)}</span><b>{counts.get(s,0)}</b></a>' for s in STATES)
        pay_card_cls={"Da saldare":"pay-yellow","Acconto":"pay-blue","Pagato":"pay-green"}
        payment_cards=''.join(f'<a class="card stat" href="/pratiche?pagamento={quote(s)}"><span class="badge {pay_card_cls.get(s,"")}">{esc(s)}</span><b>{payment_counts.get(s,0)}</b></a>' for s in PAYMENT_STATES)
        promemoria_cards=f'''<a class="card stat" href="/pratiche?promemoria=catalogo"><span class="badge tag-outline-orange">Inviare catalogo</span><b>{catalog_count}</b></a><a class="card stat" href="/pratiche?promemoria=estremi"><span class="badge tag-outline-orange">Inviare estremi</span><b>{estremi_count}</b></a>'''
        rows=self.practice_rows(recent)
        hour=datetime.now().hour
        greeting="Buongiorno" if hour < 13 else "Buon pomeriggio" if hour < 18 else "Buonasera"
        logo='<img class="home-logo" src="/assets/company_logo.png" alt="Pet Paradise">' if (ASSETS / "company_logo.png").exists() else ''
        body=f'''<main class="wrap"><div class="titlebar"><div style="display:flex;gap:18px;align-items:center">{logo}<div><h1>{greeting}, {esc(user['display_name'])}</h1><div class="sub">Situazione operativa aggiornata</div></div></div><div class="actions"><a class="btn ghost" href="/database-mesi">Database mensile</a><a class="btn" href="/nuova">+ Nuova pratica</a></div></div>{f'<div class="flash warning">{incomplete} pratiche hanno dati ancora da completare.</div>' if incomplete else ''}<h2>Avanzamento pratiche</h2><section class="grid stats">{cards}</section><div style="height:20px"></div><h2>Pagamenti</h2><section class="grid stats">{payment_cards}</section><div style="height:20px"></div><h2>Promemoria</h2><section class="grid stats">{promemoria_cards}</section><div style="height:24px"></div><div class="titlebar"><h2>Attività recenti</h2><a href="/pratiche">Vedi archivio →</a></div><div class="tablebox"><table><thead><tr><th>Data</th><th>Pratica</th><th>Animale</th><th>Proprietario</th><th>Sede</th><th>Etichette</th><th>Stato</th></tr></thead><tbody>{rows}</tbody></table></div></main>'''
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
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Diagnostica</h1><div class="sub">Controllo rapido per PDF e cartelle online.</div></div></div><section class="section"><h2>Modelli PDF</h2><div class="tablebox"><table><thead><tr><th>File</th><th>Stato</th><th>Dimensione</th></tr></thead><tbody>{''.join(asset_rows)}</tbody></table></div></section><section class="section" style="margin-top:16px"><h2>Cartelle dati</h2><p><b>Assets:</b> {esc(ASSETS)}</p><p><b>DATA:</b> {esc(DATA)} · {'OK' if data_ok else 'MANCANTE'} · scrittura {'OK' if writable else 'NO'}</p><p><b>DDT:</b> {esc(DDT_DIR)} · {'OK' if ddt_ok else 'MANCANTE'}</p></section></main>'''
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
        return ''.join(f'''<tr><td>{esc((r['created_at'] or '')[:10])}</td><td><a href="/pratiche/{r['id']}"><b>{esc(r['practice_number'])}</b></a></td><td>{esc(r['animal_name'] or 'Da inserire')}<br><small>{esc(r['species'])}{(' · '+esc(r['estimated_weight'])+' kg') if r['estimated_weight'] else ''}</small></td><td>{esc((r['owner_first_name'] or '')+' '+(r['owner_last_name'] or ''))}<br><small>{esc(r['owner_phone'])}</small></td><td>{esc(r['destination_branch'])}</td><td>{self.tag_badges(r)}</td><td>{self.status_badges(r)}</td></tr>''' for r in rows)

    def monthly_database(self,user):
        month_names=["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno","Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
        with db() as c:
            rows=c.execute("SELECT * FROM practices ORDER BY created_at DESC").fetchall()
        groups={}
        for r in rows:
            key=(r["created_at"] or "")[:7] or "Senza data"
            groups.setdefault(key,[]).append(r)
        blocks=[]
        for key,items in groups.items():
            if key != "Senza data":
                try:
                    year,month=key.split("-")
                    title=f"{month_names[int(month)]} {year}"
                except Exception:
                    title=key
            else:
                title=key
            blocks.append(f'''<section class="month-block"><div class="month-title"><h2>{esc(title)}</h2><span class="badge">{len(items)} pratiche</span></div><div class="tablebox"><table><thead><tr><th>Data</th><th>Pratica</th><th>Animale</th><th>Proprietario</th><th>Sede</th><th>Etichette</th><th>Stato</th></tr></thead><tbody>{self.practice_rows(items)}</tbody></table></div></section>''')
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Database mensile pratiche</h1><div class="sub">Tutte le pratiche divise per mese di creazione.</div></div><div class="actions"><a class="btn ghost" href="/">Dashboard</a><a class="btn" href="/nuova">+ Nuova pratica</a></div></div>{''.join(blocks) if blocks else '<section class="section"><p class="sub">Nessuna pratica presente.</p></section>'}</main>'''
        self.send_html(layout("Database mensile",body,user))

    def archive(self,user):
        q=parse_qs(urlparse(self.path).query); term=q.get("q",[""])[0]; state=q.get("stato",[""])[0]; payment=q.get("pagamento",[""])[0]; promemoria=q.get("promemoria",[""])[0]
        sql="SELECT * FROM practices WHERE 1=1"; args=[]
        if term:
            like=f"%{term}%"; sql+=" AND (practice_number LIKE ? OR animal_name LIKE ? OR owner_first_name||' '||owner_last_name LIKE ? OR owner_phone LIKE ? OR owner_phone_2 LIKE ? OR microchip LIKE ? OR clinic_name LIKE ? OR veterinarian_name LIKE ? OR CAST(ddt_number AS TEXT) LIKE ?)"; args += [like]*9
        if state: sql+=" AND status=?"; args.append(state)
        if payment: sql+=" AND COALESCE(payment_status,'Da saldare')=?"; args.append(payment)
        if promemoria == "catalogo": sql+=" AND send_catalog='Si' AND status!='Consegnato'"
        if promemoria == "estremi": sql+=" AND send_estremi='Si' AND status!='Consegnato'"
        sql+=" ORDER BY created_at DESC"
        with db() as c: rows=c.execute(sql,args).fetchall()
        opts='<option value="">Tutti gli stati</option>'+''.join(f'<option {"selected" if state==s else ""}>{esc(s)}</option>' for s in STATES)
        pay_opts='<option value="">Tutti i pagamenti</option>'+''.join(f'<option {"selected" if payment==s else ""}>{esc(s)}</option>' for s in PAYMENT_STATES)
        promemoria_label = " · Promemoria catalogo" if promemoria=="catalogo" else " · Promemoria estremi" if promemoria=="estremi" else ""
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Archivio pratiche</h1><div class="sub">{len(rows)} risultati{promemoria_label}</div></div><a class="btn" href="/nuova">+ Nuova pratica</a></div><form class="section" method="get" style="margin-bottom:18px"><div class="fields"><div class="field"><label>Ricerca</label><input name="q" value="{esc(term)}" placeholder="Animale, proprietario, telefono, microchip, veterinario, pratica o DDT"></div><div class="field"><label>Stato pratica</label><select name="stato">{opts}</select></div><div class="field"><label>Pagamento</label><select name="pagamento">{pay_opts}</select></div></div><button class="btn" style="margin-top:12px">Cerca</button></form><div class="tablebox"><table><thead><tr><th>Data</th><th>Pratica</th><th>Animale</th><th>Proprietario</th><th>Sede</th><th>Etichette</th><th>Stato</th></tr></thead><tbody>{self.practice_rows(rows)}</tbody></table></div></main>'''
        self.send_html(layout("Archivio",body,user))

    def veterinarians_page(self,user):
        q=parse_qs(urlparse(self.path).query)
        term=q.get("q",[""])[0].strip()
        voucher_filter=q.get("buoni",[""])[0]
        selected_vet=q.get("vet",[""])[0].strip()
        with db() as c:
            sql=("SELECT v.*, "
                 "COALESCE(SUM(CASE WHEN vv.status='Maturato' THEN 1 ELSE 0 END),0) available_vouchers, "
                 "COALESCE(SUM(CASE WHEN vv.status='Usato' THEN 1 ELSE 0 END),0) used_vouchers, "
                 "COUNT(vv.id) total_vouchers "
                 "FROM veterinarians v LEFT JOIN veterinarian_vouchers vv ON vv.veterinarian_id=v.id WHERE v.active=1")
            args=[]
            if term:
                sql += " AND (v.clinic_name LIKE ? OR v.doctor_name LIKE ? OR v.notes LIKE ?)"
                like=f"%{term}%"; args += [like,like,like]
            sql += " GROUP BY v.id"
            if voucher_filter == "Maturati":
                sql += " HAVING available_vouchers>0"
            elif voucher_filter == "Usati":
                sql += " HAVING used_vouchers>0"
            elif voucher_filter == "Senza buoni":
                sql += " HAVING total_vouchers=0"
            sql += " ORDER BY available_vouchers DESC, v.clinic_name, v.doctor_name"
            vets=c.execute(sql,args).fetchall()
            status_filter = "Maturato" if voucher_filter=="Maturati" else "Usato" if voucher_filter=="Usati" else ""
            vouchers=c.execute("SELECT vv.*, p.animal_name, p.species, p.practice_number, v.clinic_name FROM veterinarian_vouchers vv LEFT JOIN practices p ON p.id=vv.practice_id LEFT JOIN veterinarians v ON v.id=vv.veterinarian_id WHERE (?='' OR v.clinic_name LIKE ? OR v.doctor_name LIKE ? OR v.notes LIKE ?) AND (?='' OR vv.status=?) AND (?='' OR CAST(vv.veterinarian_id AS TEXT)=?) ORDER BY vv.status, vv.created_at DESC",(term,f"%{term}%",f"%{term}%",f"%{term}%",status_filter,status_filter,selected_vet,selected_vet)).fetchall()
        rows=[]
        for v in vets:
            disabled = "disabled" if v['available_vouchers']==0 else ""
            mature_badge = f'''<a class="badge tag-green" href="/veterinari?vet={v['id']}">{v['available_vouchers']} maturati</a>''' if v['available_vouchers'] else '<span class="badge tag-blue">0 maturati</span>'
            rows.append(f'''<tr><td><b>{esc(v['clinic_name'])}</b><br><small>{esc(v['doctor_name'])} {esc(v['notes'])}</small></td><td>{mature_badge} <span class="badge tag-red">{v['used_vouchers']} usati</span></td><td>{esc(v['phone'])}</td><td><form method="post" action="/veterinari/{v['id']}/buono-usato"><button class="btn ghost" {disabled}>Usa primo buono</button></form></td></tr>''')
        rows_html=''.join(rows) or '<tr><td colspan="4" class="sub">Nessun veterinario trovato.</td></tr>'
        voucher_rows=[]
        for b in vouchers:
            animal=b["animal_name"] or "Animale non indicato"
            species=b["species"] or ""
            action=f'<form method="post" action="/buoni/{b["id"]}/usato"><button class="btn ghost">Segna usato</button></form>' if b["status"]=="Maturato" else f'<span class="sub">Usato il {esc((b["used_at"] or "").replace("T"," "))}</span>'
            badge_cls = "tag-green" if b['status']=="Maturato" else "tag-red"
            voucher_rows.append(f'''<tr><td>{esc(b['clinic_name'])}</td><td><span class="badge {badge_cls}">{esc(b['status'])}</span></td><td>{esc((b['created_at'] or '').replace('T',' '))}</td><td>{esc(animal)}<br><small>{esc(species)} - {esc(b['practice_number'])}</small></td><td>{action}</td></tr>''')
        voucher_rows_html=''.join(voucher_rows) or '<tr><td colspan="5" class="sub">Nessun buono trovato.</td></tr>'
        filter_opts=''.join(f'<option {"selected" if voucher_filter==x else ""}>{x}</option>' for x in ["","Maturati","Usati","Senza buoni"])
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Veterinari</h1><div class="sub">Buoni maturati e usati dai veterinari.</div></div></div><form class="section" method="get"><div class="fields"><div class="field"><label>Ricerca veterinario</label><input name="q" value="{esc(term)}" placeholder="Nome, medico, comune"></div><div class="field"><label>Filtro buoni</label><select name="buoni">{filter_opts}</select></div></div><button class="btn" style="margin-top:12px">Filtra</button></form><div style="height:14px"></div><section class="section"><h2>LISTA VETERINARI</h2><div class="tablebox"><table><thead><tr><th>Veterinario</th><th>Buoni</th><th>Telefono</th><th>Azione</th></tr></thead><tbody>{rows_html}</tbody></table></div></section><div style="height:14px"></div><section class="section"><h2>Dettaglio buoni</h2><div class="tablebox"><table><thead><tr><th>Veterinario</th><th>Stato</th><th>Maturato il</th><th>Pratica / animale</th><th>Azione</th></tr></thead><tbody>{voucher_rows_html}</tbody></table></div></section><div style="height:14px"></div><section class="section"><h2>Aggiungi veterinario</h2><form method="post"><div class="fields"><div class="field"><label>Clinica / ambulatorio *</label><input name="clinic_name" required></div><div class="field"><label>Medico</label><input name="doctor_name"></div><div class="field"><label>Telefono</label><input name="phone"></div><div class="field"><label>Note</label><input name="notes"></div></div><button class="btn" style="margin-top:12px">Aggiungi veterinario</button></form></section></main>'''
        self.send_html(layout("Veterinari",body,user))

    def save_veterinarian(self,user):
        f=self.form(); stamp=now()
        clinic=f.get("clinic_name","").strip()
        if not clinic:return self.send_error(400, "Nome clinica obbligatorio")
        data=(clinic,f.get("doctor_name","").strip(),f.get("phone","").strip(),f.get("notes","").strip())
        with db() as c:
            if f.get("id"):
                c.execute("UPDATE veterinarians SET clinic_name=?,doctor_name=?,phone=?,notes=?,updated_at=? WHERE id=?",data+(stamp,int(f["id"])))
            else:
                c.execute("INSERT INTO veterinarians(clinic_name,doctor_name,phone,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",data+(stamp,stamp))
        self.redirect("/veterinari")

    def use_voucher(self,user,vet_id):
        with db() as c:
            voucher=c.execute("SELECT id FROM veterinarian_vouchers WHERE veterinarian_id=? AND status='Maturato' ORDER BY created_at LIMIT 1",(vet_id,)).fetchone()
            if voucher:
                c.execute("UPDATE veterinarian_vouchers SET status='Usato', used_at=? WHERE id=?",(now(),voucher["id"]))
        self.redirect("/veterinari")

    def use_specific_voucher(self,user,voucher_id):
        with db() as c:
            c.execute("UPDATE veterinarian_vouchers SET status='Usato', used_at=? WHERE id=? AND status='Maturato'",(now(),voucher_id))
        self.redirect("/veterinari")

    def fields_html(self,p=None):
        val=lambda k: esc(p[k] if p and k in p.keys() else "")
        raw=lambda k,default="": (p[k] if p and k in p.keys() and p[k] not in (None,"") else default)
        selected=lambda k,v,default="": "selected" if str(raw(k,default))==v else ""
        tag_select=lambda name,label,cls: f'''<div class="field"><label><input type="checkbox" name="{name}" value="Si" {"checked" if raw(name)=="Si" else ""}> <span class="badge {cls}">{label}</span></label></div>'''
        with db() as c:
            vets=c.execute("SELECT * FROM veterinarians WHERE active=1 ORDER BY clinic_name, doctor_name").fetchall()
        vet_options='<option value="">Nessun veterinario selezionato / testo libero</option>'+''.join(f'<option value="{v["id"]}" {"selected" if str(raw("veterinarian_id"))==str(v["id"]) else ""}>{esc(v["clinic_name"])}{(" - "+esc(v["doctor_name"])) if v["doctor_name"] else ""}{(" - "+esc(v["notes"])) if v["notes"] else ""}</option>' for v in vets)
        voucher_checked='checked' if raw('voucher_requested')=="Si" else ''
        catalog_checked='checked' if raw('send_catalog')=="Si" else ''
        estremi_checked='checked' if raw('send_estremi')=="Si" else ''
        return f'''<section class="section"><h2>Operatore</h2><div class="fields"><div class="field"><label>Operatore *</label><select name="operator_name" required><option value="">Seleziona operatore</option><option {selected('operator_name','SERENA')}>SERENA</option><option {selected('operator_name','ALESSIO')}>ALESSIO</option><option {selected('operator_name','FILIPPO')}>FILIPPO</option></select></div></div></section>
        <section class="section"><h2>Richiesta</h2><div class="fields"><div class="field"><label>Servizio</label><select name="service_type"><option {selected('service_type','Da decidere')}>Da decidere</option><option {selected('service_type','Cremazione singola')}>Cremazione singola</option><option {selected('service_type','Cremazione collettiva')}>Cremazione collettiva</option></select></div><div class="field"><label>Origine richiesta *</label><select name="request_origin" required><option {selected('request_origin','Veterinario')}>Veterinario</option><option {selected('request_origin','Privato')}>Privato</option><option {selected('request_origin','Consegna in sede')}>Consegna in sede</option><option {selected('request_origin','Collaboratore')}>Collaboratore</option></select></div><div class="field {'hidden' if raw('request_origin')!='Collaboratore' else ''}" id="collaboratorBox"><label>Collaboratore</label><select name="collaborator_name"><option value="">Nessun collaboratore</option><option {selected('collaborator_name','HUMANITAS CROCE VERDE')}>HUMANITAS CROCE VERDE</option></select></div><div class="field"><label>Sede di destinazione *</label><select name="destination_branch" required><option {selected('destination_branch','Livorno')}>Livorno</option><option {selected('destination_branch','Empoli')}>Empoli</option></select></div></div></section>
        <section class="section"><h2>SPEDITORE</h2><div class="fields"><div class="field"><label>Nome *</label><input name="owner_first_name" value="{val('owner_first_name')}" required></div><div class="field"><label>Cognome *</label><input name="owner_last_name" value="{val('owner_last_name')}" required></div><div class="field"><label>Telefono *</label><input type="tel" inputmode="numeric" name="owner_phone" value="{val('owner_phone')}" required></div><div class="field"><label>Secondo telefono</label><input type="tel" inputmode="numeric" name="owner_phone_2" value="{val('owner_phone_2')}"></div><div class="field"><label>Email</label><input type="email" name="owner_email" value="{val('owner_email')}"></div><div class="field"><label>Codice fiscale</label><input name="owner_tax_code" value="{val('owner_tax_code')}"></div><div class="field full"><label>Indirizzo - via / piazza *</label><input name="owner_street" value="{val('owner_street') or val('owner_address')}" required></div><div class="field"><label>Comune *</label><input name="owner_city" value="{val('owner_city')}" required></div><div class="field"><label>Provincia</label><input name="owner_province" value="{val('owner_province')}" maxlength="2" placeholder="Si compila dal comune"></div><div class="field"><label>CAP</label><input name="owner_zip" value="{val('owner_zip')}" inputmode="numeric"></div></div></section>
        <section class="section"><h2>DESTINATARIO E LUOGO DI DESTINAZIONE</h2><p class="sub">Compilati automaticamente in base alla sede selezionata: Livorno oppure Empoli.</p></section>
        <section class="section"><h2>LUOGO DI ORIGINE</h2><div class="fields"><div class="field"><label>Luogo di origine</label><select name="origin_mode"><option {selected('origin_mode','IDEM SPED','IDEM SPED')}>IDEM SPED</option><option {selected('origin_mode','Testo libero','IDEM SPED')}>Testo libero</option></select></div><div class="field"><label>Data recupero</label><input type="date" name="pickup_date" value="{val('pickup_date')}"></div><div class="field full"><label>Testo libero / indirizzo diverso</label><input name="origin_text" value="{val('origin_text') or (val('pickup_address') if raw('pickup_address_mode')=='Altro indirizzo' else '')}" placeholder="Scrivi qui solo se il luogo non è IDEM SPED"></div></div></section>
        <section class="section"><h2>Animale</h2><div class="fields"><div class="field"><label>Nome</label><input name="animal_name" value="{val('animal_name')}"></div><div class="field"><label>Specie</label><input name="species" value="{val('species')}"></div><div class="field"><label>Peso stimato (kg)</label><input name="estimated_weight" value="{val('estimated_weight')}"></div><div class="field"><label>Età - anni</label><input name="age_years" value="{val('age_years')}"></div><div class="field"><label>Età - mesi</label><input name="age_months" value="{val('age_months')}"></div><div class="field"><label>Microchip</label><input name="microchip" value="{val('microchip')}"></div><div class="field full"><label>Razza</label><input name="breed" value="{val('breed')}"></div></div><button class="btn ghost" type="button" id="showSecondAnimal" style="margin-top:12px;{'display:none' if raw('animal2_name') else ''}">+ Aggiungi altro animale</button><div id="secondAnimalBox" style="display:{'block' if raw('animal2_name') else 'none'};margin-top:14px"><h2>Secondo animale</h2><div class="fields"><div class="field"><label>Nome</label><input name="animal2_name" value="{val('animal2_name')}"></div><div class="field"><label>Specie</label><input name="animal2_species" value="{val('animal2_species')}"></div><div class="field"><label>Peso stimato (kg)</label><input name="animal2_weight" value="{val('animal2_weight')}"></div><div class="field"><label>Microchip</label><input name="animal2_microchip" value="{val('animal2_microchip')}"></div><div class="field full"><label>Razza</label><input name="animal2_breed" value="{val('animal2_breed')}"></div></div></div></section>
        <section class="section"><h2>AMBULATORIO VETERINARIO</h2><div class="fields"><div class="field"><label>Veterinario per buono</label><select name="veterinarian_id">{vet_options}</select></div><div class="field"><label>Clinica / ambulatorio</label><input name="clinic_name" value="{val('clinic_name')}"></div><div class="field"><label>Medico veterinario</label><input name="veterinarian_name" value="{val('veterinarian_name')}"></div><div class="field full"><label><input type="checkbox" name="voucher_requested" value="Si" {voucher_checked} style="width:auto"> BUONO - aggiungi un buono al veterinario selezionato</label></div></div></section>
        <section class="section"><h2>TRASPORTATORE</h2><div class="fields"><div class="field"><label>Dati trasportatore</label><select name="transporter_mode"><option {selected('transporter_mode','IDEM SPED','IDEM SPED')}>IDEM SPED</option><option {selected('transporter_mode','DATI PET PARADISE','IDEM SPED')}>DATI PET PARADISE</option></select></div><div class="field"><label>Scelta rapida mezzo</label><select id="transport_method_quick"><option value="">Seleziona se serve</option><option value="MEZZO PROPRIO">MEZZO PROPRIO</option></select></div><div class="field"><label>Mezzo di trasporto</label><input name="transport_method" value="{val('transport_method')}"></div><div class="field"><label>Targa automezzo</label><input name="vehicle_plate" value="{val('vehicle_plate')}"></div><div class="field"><label>Temperatura</label><select name="temperature_mode"><option {selected('temperature_mode','Ambiente','Ambiente')}>Ambiente</option><option {selected('temperature_mode','Refrigerato','Ambiente')}>Refrigerato</option><option {selected('temperature_mode','Congelato','Ambiente')}>Congelato</option></select></div><div class="field"><label>Numero colli</label><input name="package_count" value="{val('package_count') or '1'}"></div><div class="field"><label>ID contenitore</label><select name="container_id"><option value="">Seleziona ID contenitore</option><option {selected('container_id','03/2021')}>03/2021</option><option {selected('container_id','04/2021')}>04/2021</option></select></div><div class="field"><label>Numero lotto</label><input name="lot_number" value="{val('lot_number') or '/'}"></div><div class="field"><label>Metodo trattamento</label><input name="treatment_method" value="{val('treatment_method') or '/'}"></div></div></section>
        <section class="section"><h2>Preventivo</h2><div class="fields"><div class="field"><label>Cremazione €</label><input name="price_cremation" value="{val('price_cremation')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Ritiro €</label><input name="price_pickup" value="{val('price_pickup')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Urna €</label><input name="price_urn" value="{val('price_urn')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label><input type="checkbox" name="send_catalog" value="Si" {catalog_checked} style="width:auto"> INVIARE CATALOGO</label></div><div class="field"><label>Riconsegna €</label><input name="price_delivery" value="{val('price_delivery')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Calco €</label><input name="price_cast" value="{val('price_cast')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Serale €</label><input name="price_evening" value="{val('price_evening')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Notturno €</label><input name="price_night" value="{val('price_night')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Festivo €</label><input name="price_holiday" value="{val('price_holiday')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Accessori €</label><input name="price_accessories" value="{val('price_accessories')}" data-preventivo-sum="1" placeholder="Numero o testo libero"></div><div class="field"><label>Totale servizio €</label><input name="total_service" value="{val('total_service')}" placeholder="Numero o testo libero"></div><div class="field"><label><input type="checkbox" name="send_estremi" value="Si" {estremi_checked} style="width:auto"> INVIARE ESTREMI</label></div><div class="field"><label>Acconto €</label><input name="deposit" value="{val('deposit')}" placeholder="Numero o testo libero"></div><div class="field"><label>Rimanenza €</label><input name="remaining_balance" value="{val('remaining_balance')}" readonly></div><div class="field full"><label>TOTALE</label><textarea name="total_text" placeholder="Testo libero per note sul totale">{val('total_text')}</textarea></div><div class="field full"><label>Note operative</label><textarea name="notes">{val('notes')}</textarea></div></div></section>
        <section class="section"><h2>Etichette operative</h2><div class="fields">{tag_select('tag_assistita','ASSISTITA','tag-red')}{tag_select('tag_possibile_assistita','POSSIBILE ASSISTITA','tag-red')}{tag_select('tag_assistita_streaming','ASSISTITA STREAMING','tag-orange')}{tag_select('tag_saluto','SALUTO','tag-purple')}{tag_select('tag_calco','CALCO','tag-yellow')}{tag_select('tag_avvisare','AVVISARE','tag-pink')}{tag_select('tag_da_richiamare','DA RICHIAMARE','tag-blue')}</div></section>
        <section class="section"><h2>Documento e accettazione</h2><div class="fields"><div class="field"><label>Numero documento</label><input name="identity_document_number" value="{val('identity_document_number')}"></div><div class="field"><label>Data rilascio</label><input type="date" name="identity_document_date" value="{val('identity_document_date')}"></div><div class="field full"><label>Luogo firma</label><input name="signing_place" value="{val('signing_place') or val('destination_branch')}"></div></div></section>'''

    def new_page(self,user):
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Nuova pratica</h1><div class="sub">Inserisci subito i dati disponibili; potrai completarli in seguito.</div></div></div><form method="post"><div class="grid form-grid">{self.fields_html()}</div><div class="actions" style="margin-top:18px"><button class="btn">Crea pratica</button><a class="btn ghost" href="/">Annulla</a></div></form></main>'''
        self.send_html(layout("Nuova pratica",body,user))

    def normalized_fields(self,f):
        keys=["operator_name","request_origin","collaborator_name","destination_branch","owner_first_name","owner_last_name","owner_phone","owner_phone_2","owner_email","owner_tax_code","owner_address","owner_street","owner_city","owner_province","owner_zip","pickup_address_mode","pickup_address","origin_mode","origin_text","pickup_date","animal_name","species","breed","estimated_weight","age_years","age_months","microchip","animal2_name","animal2_species","animal2_breed","animal2_weight","animal2_microchip","service_type","veterinarian_id","voucher_requested","clinic_name","veterinarian_name","notes","transporter_mode","transport_method","vehicle_plate","temperature_mode","package_count","container_id","lot_number","treatment_method","tag_assistita","tag_possibile_assistita","tag_assistita_streaming","tag_saluto","tag_calco","tag_avvisare","tag_da_richiamare","payment_status","price_cremation","price_pickup","price_evening","price_urn","send_catalog","send_estremi","price_delivery","price_night","price_cast","price_holiday","price_accessories","deposit","remaining_balance","total_service","total_text","identity_document_number","identity_document_date","signing_place"]
        data = {k:f.get(k,"").strip() for k in keys}
        if not data["payment_status"] or data["payment_status"] not in PAYMENT_STATES:
            data["payment_status"] = "Da saldare"
        data["send_catalog"] = "Si" if data["send_catalog"] == "Si" else ""
        data["send_estremi"] = "Si" if data["send_estremi"] == "Si" else ""
        for key in ("tag_assistita","tag_possibile_assistita","tag_assistita_streaming","tag_saluto","tag_calco","tag_avvisare","tag_da_richiamare"):
            data[key] = "Si" if data[key] == "Si" else ""
        data["voucher_requested"] = "Si" if data["voucher_requested"] == "Si" else ""
        data["veterinarian_id"] = data["veterinarian_id"] or None
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
        required=["owner_first_name","owner_last_name","owner_phone","owner_street","owner_city","animal_name","species","estimated_weight","service_type"]
        return int(all(d.get(k) and d.get(k)!="Da decidere" for k in required))

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

    def create_practice(self,user):
        d=self.normalized_fields(self.form()); stamp=now()
        if not d.get("operator_name"):
            return self.send_error(400, "Operatore obbligatorio")
        if not all(d.get(k) for k in ("owner_first_name","owner_last_name","owner_phone","owner_street","owner_city")):
            return self.send_error(400, "Nome, cognome, telefono, via e comune dello speditore sono obbligatori")
        initial="Ritirato"
        with db() as c:
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
        if not p:return self.send_error(404)
        options=''.join(f'<option {"selected" if s==p["status"] else ""}>{esc(s)}</option>' for s in STATES)
        payment_value = p["payment_status"] if "payment_status" in p.keys() and p["payment_status"] else "Da saldare"
        payment_cls = {"Da saldare":"pay-yellow","Acconto":"pay-blue","Pagato":"pay-green"}.get(payment_value,"")
        catalog_value = "Si" if "send_catalog" in p.keys() and p["send_catalog"] else "No"
        invoice_value = p["invoice_number"] if "invoice_number" in p.keys() and p["invoice_number"] else ""
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
              <div class="section"><h2>Stati pratica</h2><form method="post" action="/pratiche/{pid}/stato"><div class="fields"><div class="field"><label>Avanzamento</label><select name="status">{options}</select></div><div class="field"><label>Pagamento</label><select name="payment_status">{payment_options}</select></div><div class="field"><label>Numero fattura</label><input name="invoice_number" value="{esc(invoice_value)}" placeholder="Da inserire quando risulta pagato"></div></div><button class="btn" style="margin-top:12px">Aggiorna stati</button></form></div>
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
        body=f'''<main class="wrap"><div class="titlebar"><div><h1>Firma proprietario</h1><div class="sub">{owner} · pratica {esc(p['practice_number'])}</div></div></div><section class="section"><p class="sub">Fai firmare qui il proprietario con il dito. La firma verrà salvata nella pratica e inserita nel PDF DDT.</p><form method="post" id="signatureForm"><canvas class="signature-pad" id="pad"></canvas><input type="hidden" name="signature_data" id="signatureData"><div class="actions" style="margin-top:14px"><button class="btn" type="submit">Salva firma</button><button class="btn ghost" type="button" id="clearPad">Cancella</button><a class="btn ghost" href="/pratiche/{pid}">Annulla</a></div></form></section><script>
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
        if not d.get("operator_name"):
            return self.send_error(400, "Operatore obbligatorio")
        if not all(d.get(k) for k in ("owner_first_name","owner_last_name","owner_phone","owner_street","owner_city")):
            return self.send_error(400, "Nome, cognome, telefono, via e comune dello speditore sono obbligatori")
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
        f=self.form(); new=f.get("status",""); payment=f.get("payment_status","Da saldare"); invoice=f.get("invoice_number","").strip()
        if new not in STATES or payment not in PAYMENT_STATES:return self.send_error(400)
        with db() as c:
            old=c.execute("SELECT status,payment_status,invoice_number FROM practices WHERE id=?",(pid,)).fetchone()
            if not old:return self.send_error(404)
            old_payment=old["payment_status"] or "Da saldare"
            c.execute("UPDATE practices SET status=?,payment_status=?,invoice_number=?,updated_at=? WHERE id=?",(new,payment,invoice,now(),pid))
            new_value=f'{new} + {payment}' + (f' · Fattura {invoice}' if invoice else '')
            old_value=f'{old["status"]} + {old_payment}' + (f' · Fattura {old["invoice_number"]}' if old["invoice_number"] else '')
            c.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",(pid,"Cambio stati",old_value,new_value,user["id"],now()))
        self.redirect(f"/pratiche/{pid}")

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
