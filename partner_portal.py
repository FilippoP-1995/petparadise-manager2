"""Portale Veterinari (PWA) di Pet Paradise: pagine HTML e piccola API JSON sotto /partner.

Le regole di dominio stanno in partner_service.py; qui ci sono solo routing,
sessione (cookie separato da quello del gestionale) e pagine. Il modulo non
importa app.py: il gestionale gli passa le sue funzioni (db, verifica password,
utente staff) tramite ``dispatch``.
"""

from __future__ import annotations

import html
import json
import re
import secrets
from datetime import datetime, timedelta
from http import cookies
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

import partner_service as ps
import quote_service as qs

COOKIE = "pp_partner_session"
ROME = ZoneInfo("Europe/Rome")
UTC = ZoneInfo("UTC")
DAYS = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
MONTHS = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")
CONTACT_EMAIL = "info@petparadisempoli.com"

STATUS_CLASS = {
    "ricevuta": "st-wait", "in_congelatore": "st-ice", "programmato": "st-plan", "ritirato": "st-pick",
    "in_lavorazione": "st-work", "pronto_riconsegna": "st-ready", "completata": "st-done", "annullata": "st-off",
}
OPEN_STATUSES = ("ricevuta", "in_congelatore", "programmato", "ritirato", "in_lavorazione", "pronto_riconsegna")
PROGRESS_STEPS = ("ricevuta", "programmato", "ritirato", "in_lavorazione", "pronto_riconsegna", "completata")
MODE_ICON_NAMES = {"ritiro_clinica": "hospital", "ritiro_domicilio": "home", "invio_in_sede": "car"}
MODE_SHORT = {
    "ritiro_clinica": "Ritiro in clinica",
    "ritiro_domicilio": "Ritiro a domicilio",
    "invio_in_sede": "Cliente in sede",
}
FASCE = {"mattina": ("09:00", "13:00"), "pomeriggio": ("14:00", "18:00")}


def e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


# ---------------------------------------------------------------------------
# Icone (stile linea, inline: nessuna dipendenza esterna, funzionano offline)
# ---------------------------------------------------------------------------

_ICONS = {
    "paw": '<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/>'
           '<path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z"/>',
    "list": '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>'
            '<path d="M12 11h4M12 16h4M8 11h.01M8 16h.01"/>',
    "plus": '<path d="M5 12h14M12 5v14"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    "hospital": '<path d="M12 6v4M14 14h-4M14 18h-4M14 8h-4"/><path d="M18 12h2a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2h2"/>'
                '<path d="M18 22V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v18"/>',
    "home": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/>'
            '<path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "car": '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/>'
           '<circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>',
    "snow": '<path d="m10 20-1.25-2.5L6 18M10 4 8.75 6.5 6 6M14 20l1.25-2.5L18 18M14 4l1.25 2.5L18 6M17 21l-3-6h-4M17 3l-3 6 1.5 3M2 12h6.5L10 9M20 10l-1.5 2 1.5 2M22 12h-6.5L14 15M4 10l1.5 2L4 14M7 21l3-6-1.5-3M7 3l3 6h4"/>',
    "ticket": '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M13 5v2M13 17v2M13 11v2"/>',
    "alert": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
    "calendar": '<path d="M8 2v4M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "pin": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    "mail": '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "back": '<path d="m12 19-7-7 7-7M19 12H5"/>',
    "eye": '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "calc": '<rect width="16" height="20" x="4" y="2" rx="2"/><path d="M8 6h8M16 14v4M16 10h.01M12 10h.01M8 10h.01M12 14h.01M8 14h.01M12 18h.01M8 18h.01"/>',
    "urn": '<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
}


def icon(name: str, size: int = 20, cls: str = "") -> str:
    return (f'<svg class="ic {cls}" viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" '
            f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS[name]}</svg>')


# ---------------------------------------------------------------------------
# Formattazione
# ---------------------------------------------------------------------------

def _fmt_date(date_text: str) -> str:
    try:
        d = datetime.strptime(date_text[:10], "%Y-%m-%d")
    except ValueError:
        return date_text or ""
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def _fmt_window(start_at: str, end_at: str) -> str:
    """'2026-11-14T09:00:00','2026-11-14T12:00:59' -> 'sab 14 nov, 09:00-12:00'."""
    if not start_at:
        return ""
    day = _fmt_date(start_at)
    s, t = start_at[11:16], (end_at or "")[11:16]
    if s == "00:00" and t in ("23:59", ""):
        return f"{day}, tutto il giorno"
    return f"{day}, {s}-{t}" if t else f"{day}, {s}"


def _fmt_proposed(r) -> str:
    if not r["proposed_date"]:
        return ""
    if r["proposed_from"] and r["proposed_to"]:
        return f"{_fmt_date(r['proposed_date'])}, {r['proposed_from']}-{r['proposed_to']}"
    return f"{_fmt_date(r['proposed_date'])}, il prima possibile"


def _fmt_utc(stamp: str) -> str:
    try:
        d = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).astimezone(ROME)
    except ValueError:
        return stamp or ""
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}, {d:%H:%M}"


def _when_text(r) -> tuple[str, str]:
    """(etichetta, testo) per 'quando' di una richiesta."""
    status = r["public_status"]
    if status == "in_congelatore":
        return "Ritiro", "quando il congelatore è pieno, nessuna fretta"
    if status in ("programmato", "ritirato", "in_lavorazione", "pronto_riconsegna", "completata") and r["event_start"]:
        label = "Ritiro confermato" if status == "programmato" else "Ritiro"
        return label, _fmt_window(r["event_start"], r["event_end"])
    proposed = _fmt_proposed(r)
    return ("Orario proposto", proposed) if proposed else ("", "")


def _greeting() -> str:
    hour = datetime.now(ROME).hour
    return "Buongiorno" if hour < 13 else "Buon pomeriggio" if hour < 18 else "Buonasera"


# ---------------------------------------------------------------------------
# Stile e script
# ---------------------------------------------------------------------------

CSS = """
:root{--brand:#a74045;--brand2:#7f3035;--brand-soft:#fbeeee;--ink:#1f2a26;--ink2:#3b4944;--muted:#6f7c76;--line:#e7e1da;--bg:#f6f2ee;--card:#fff;
--ok:#2f7d65;--safe-b:env(safe-area-inset-bottom,0px);--safe-t:env(safe-area-inset-top,0px);--r:20px;
--sh1:0 1px 2px rgba(31,42,38,.05),0 4px 14px rgba(31,42,38,.05);--sh2:0 2px 4px rgba(31,42,38,.06),0 12px 32px rgba(31,42,38,.10);color-scheme:light}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{overscroll-behavior-y:contain;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI Variable","Segoe UI",Roboto,Inter,sans-serif;min-height:100vh;
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:inherit;text-decoration:none}
.ic{flex:none;vertical-align:middle}
.wrap{max-width:680px;margin:0 auto;padding:18px 16px calc(104px + var(--safe-b));animation:fadein .22s ease both}
.wrap.login{display:flex;flex-direction:column;justify-content:flex-start;min-height:100vh;padding-top:0;padding-bottom:32px}
@keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}html{scroll-behavior:auto}}

.staffbar{background:#1f2a26;color:#e9efec;font-size:13px;padding:8px 16px;padding-top:calc(8px + var(--safe-t));display:flex;gap:12px;align-items:center;justify-content:space-between}
.staffbar span{display:flex;gap:8px;align-items:center}
.staffbar a{display:inline-flex;gap:6px;align-items:center;background:rgba(255,255,255,.14);border-radius:999px;padding:5px 12px;white-space:nowrap;font-weight:600}

.top{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.92);backdrop-filter:saturate(1.6) blur(14px);-webkit-backdrop-filter:saturate(1.6) blur(14px);border-bottom:1px solid var(--line)}
.top-in{max-width:880px;margin:0 auto;padding:10px 16px;padding-top:calc(10px + var(--safe-t));display:flex;align-items:center;gap:12px}
.top .brand{display:flex;align-items:center;gap:12px;min-width:0;flex:1}
.top img{width:42px;height:42px;border-radius:12px;box-shadow:0 2px 8px rgba(127,48,53,.35);flex:none}
.top b{display:block;font-size:16px;line-height:1.2;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.top small{color:var(--muted);font-size:12.5px}
.topnav{display:none;gap:4px}
.topnav a{display:flex;align-items:center;gap:8px;padding:9px 14px;border-radius:12px;font-weight:600;font-size:15px;color:var(--ink2)}
.topnav a:hover{background:#f3eee9}.topnav a.on{background:var(--brand-soft);color:var(--brand)}
.topnav a.cta{background:var(--brand);color:#fff}.topnav a.cta:hover{background:var(--brand2)}

h1{font-size:26px;line-height:1.15;margin:4px 0 6px;letter-spacing:-.02em}
h2{font-size:13px;margin:28px 2px 10px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);font-weight:700;display:flex;align-items:center;gap:8px}
h2 .count{background:#ebe5de;color:var(--ink2);border-radius:999px;padding:1px 9px;font-size:12px;letter-spacing:0}
h3{font-size:15px;margin:0 0 10px;display:flex;align-items:center;gap:8px}
.sub{color:var(--muted);font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:18px;margin:12px 0;box-shadow:var(--sh1)}
.row{display:flex;gap:10px;align-items:center;justify-content:space-between}

.hero{position:relative;overflow:hidden;border-radius:26px;padding:22px 20px 18px;color:#fff;margin-bottom:6px;
background:radial-gradient(120% 140% at 100% 0%,#c95a60 0,rgba(201,90,96,0) 55%),linear-gradient(145deg,#a74045 0,#6f2a2f 100%);box-shadow:0 14px 34px rgba(127,48,53,.35)}
.hero:before{content:"";position:absolute;right:-30px;top:-30px;width:170px;height:170px;border-radius:50%;background:rgba(255,255,255,.07)}
.hero:after{content:"";position:absolute;right:46px;bottom:-60px;width:140px;height:140px;border-radius:50%;background:rgba(255,255,255,.05)}
.hero small{opacity:.85;font-size:14px;display:block}
.hero h1{margin:2px 0 2px;font-size:27px}.hero p{margin:0 0 16px;opacity:.85;font-size:14.5px}
.hero .btn{position:relative;z-index:1}
.stats{position:relative;z-index:1;display:grid;gap:10px;margin-top:16px}
.tile{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.2);border-radius:16px;padding:12px 8px;text-align:center;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.tile b{display:block;font-size:26px;line-height:1.05;font-weight:800;letter-spacing:-.02em}.tile span{font-size:12px;opacity:.9;line-height:1.2;display:block;margin-top:2px}

.btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;min-height:52px;padding:0 22px;border:0;border-radius:16px;background:linear-gradient(180deg,#b34a50,#a74045);color:#fff;
font:700 16px/1 inherit;font-family:inherit;cursor:pointer;width:100%;box-shadow:0 6px 16px rgba(167,64,69,.32);transition:transform .12s,box-shadow .12s,background .12s;letter-spacing:.005em}
.btn:hover{box-shadow:0 8px 20px rgba(167,64,69,.4)}.btn:active{transform:scale(.985);background:var(--brand2)}.btn[disabled]{opacity:.6;pointer-events:none}
.btn.light{background:#fff;color:var(--brand);box-shadow:0 6px 18px rgba(0,0,0,.18)}.btn.light:active{background:#f7ecec}
.btn.ghost{background:#fff;color:var(--brand);border:1.5px solid #e2c3c5;box-shadow:none}.btn.ghost:hover{background:var(--brand-soft)}
.btn.small{min-height:40px;font-size:14px;width:auto;padding:0 16px;border-radius:12px}
.btn.danger{background:#fff;color:#a23030;border:1.5px solid #e3b9b9;box-shadow:none}
:focus-visible{outline:3px solid rgba(167,64,69,.4);outline-offset:2px}

.badge{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:5px 12px;font-size:12.5px;font-weight:700;white-space:nowrap;line-height:1.1}
.badge:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.st-wait{background:#fff1cf;color:#8a5a00}.st-ice{background:#ddf0fc;color:#165e8f}.st-plan{background:#e4eaff;color:#2a45a0}
.st-pick{background:#d6f1e9;color:#17695a}.st-work{background:#ede2fb;color:#62389c}.st-ready{background:#d9f3dc;color:#1f6a2a}
.st-done{background:#e6ebe8;color:#46544d}.st-off{background:#eee;color:#777}
.tag{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:700;border-radius:8px;padding:3px 9px;background:#f3eee8;color:#5a4a3b}
.tag.urgent{background:#fde1e1;color:#a02323}.tag.ice{background:#e3f2fc;color:#165e8f}.tag.voucher{background:#fdf0d3;color:#8a5a00}
.tags{display:flex;gap:6px;flex-wrap:wrap}

.rcard{display:grid;grid-template-columns:46px 1fr;gap:4px 14px;align-items:start;background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:16px;margin:12px 0;
box-shadow:var(--sh1);position:relative;transition:transform .12s,box-shadow .15s}
.rcard:hover{box-shadow:var(--sh2);transform:translateY(-1px)}.rcard:active{transform:scale(.99)}
.rcard:before{content:"";position:absolute;left:0;top:16px;bottom:16px;width:4px;border-radius:0 4px 4px 0;background:var(--accent,#d8d0c7)}
.rcard.acc-wait{--accent:#e0a92b}.rcard.acc-ice{--accent:#4aa3da}.rcard.acc-plan{--accent:#4a62d1}.rcard.acc-pick{--accent:#2f9a82}
.rcard.acc-work{--accent:#8a5cd0}.rcard.acc-ready{--accent:#3aa24a}.rcard.acc-done{--accent:#aab4af}.rcard.acc-off{--accent:#c7c7c7}
.rc-ico{width:46px;height:46px;border-radius:14px;background:var(--brand-soft);color:var(--brand);display:flex;align-items:center;justify-content:center}
.rc-main{min-width:0}.rc-title{font-size:17px;font-weight:750;letter-spacing:-.01em;line-height:1.25}.rc-title span{font-weight:500}
.rc-sub{color:var(--muted);font-size:14px;margin-top:2px}
.rc-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin-top:10px}
.rc-when{display:flex;align-items:center;gap:7px;font-size:14.5px;color:var(--ink2)}.rc-when .ic{color:var(--muted)}
.rc-foot{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:10px;padding-top:12px;border-top:1px dashed var(--line)}
.rc-code{font:600 12.5px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);letter-spacing:.06em}

label{display:block;font-weight:650;font-size:14px;margin:14px 0 6px;color:var(--ink2)}
input,select,textarea{width:100%;min-height:50px;border:1.5px solid var(--line);border-radius:14px;padding:11px 14px;font:16px inherit;font-family:inherit;background:#fcfbfa;color:var(--ink);transition:border-color .12s,box-shadow .12s,background .12s}
textarea{min-height:90px;resize:vertical}
input:hover,select:hover,textarea:hover{border-color:#d7cfc6}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 4px rgba(167,64,69,.14);background:#fff}
input::placeholder,textarea::placeholder{color:#a9b1ad}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.fsec{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:6px 18px 18px;margin:14px 0;box-shadow:var(--sh1)}
.fsec>h2{margin:16px 0 4px;color:var(--ink);text-transform:none;letter-spacing:-.01em;font-size:17px;font-weight:750}
.fsec>h2 .num{width:26px;height:26px;border-radius:50%;background:var(--brand);color:#fff;font-size:13px;font-weight:800;display:inline-flex;align-items:center;justify-content:center}
.fsec>.sub{margin:0 0 4px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{position:relative;margin:0;font-weight:inherit}.chip input{position:absolute;opacity:0;inset:0;width:100%;height:100%;min-height:0;cursor:pointer;margin:0}
.chip span,.pill{display:inline-flex;align-items:center;gap:7px;border:1.5px solid var(--line);background:#fff;border-radius:999px;padding:11px 17px;font-weight:650;font-size:15px;color:var(--ink2);transition:all .12s;cursor:pointer;font-family:inherit}
.chip:hover span,.pill:hover{border-color:#d3b5b7}
.chip input:checked+span,.pill.on{background:var(--brand);border-color:var(--brand);color:#fff;box-shadow:0 4px 12px rgba(167,64,69,.28)}
.chip input:focus-visible+span{outline:3px solid rgba(167,64,69,.4);outline-offset:2px}
.modes{display:grid;gap:10px;margin-top:12px}
.mode{position:relative;margin:0;font-weight:inherit}.mode input{position:absolute;opacity:0;inset:0;width:100%;height:100%;min-height:0;cursor:pointer;margin:0;z-index:2}
.mode>div{display:flex;gap:14px;align-items:center;border:1.5px solid var(--line);background:#fff;border-radius:16px;padding:14px 16px;transition:all .12s}
.mode .mi{width:46px;height:46px;border-radius:14px;background:#f4efe9;color:var(--ink2);display:flex;align-items:center;justify-content:center;flex:none;transition:all .12s}
.mode b{display:block;font-size:16px}.mode small{color:var(--muted);font-size:13.5px}
.mode:hover>div{border-color:#d3b5b7}
.mode input:checked+div{border-color:var(--brand);background:var(--brand-soft);box-shadow:0 0 0 3px rgba(167,64,69,.14)}
.mode input:checked+div .mi{background:var(--brand);color:#fff}
.mode input:focus-visible+div{outline:3px solid rgba(167,64,69,.4);outline-offset:2px}
.check{display:flex;gap:13px;align-items:flex-start;border:1.5px solid var(--line);background:#fff;border-radius:16px;padding:13px 15px;margin:12px 0 0;cursor:pointer;transition:all .12s}
.check input{width:22px;height:22px;min-height:0;margin:2px 0 0;accent-color:var(--brand);flex:none;padding:0;box-shadow:none}
.check .ci{width:36px;height:36px;border-radius:11px;background:#f4efe9;color:var(--ink2);display:flex;align-items:center;justify-content:center;flex:none}
.check b{display:block;font-size:15px}.check small{color:var(--muted);font-size:13.5px;line-height:1.35;display:block}
.check:hover{border-color:#d3b5b7}.check:has(input:checked){border-color:var(--brand);background:var(--brand-soft)}
.check.urgent .ci{background:#fde4e4;color:#b02a2a}.check.urgent:has(input:checked){border-color:#d34a4a;background:#fff3f3}
.check.ice .ci{background:#e3f2fc;color:#165e8f}.check.voucher .ci{background:#fdf0d3;color:#8a5a00}
.submitbar{margin:18px 0 6px}
.flash{display:flex;gap:10px;align-items:flex-start;border-radius:16px;padding:13px 15px;margin:12px 0;font-size:15px}
.flash.ok{background:#e0f4e6;color:#1f5f2a}.flash.err{background:#fde8e8;color:#8f2020}
.hidden{display:none!important}

.kv{display:grid;grid-template-columns:118px 1fr;gap:10px 12px;font-size:15px;margin:0}.kv dt{color:var(--muted)}.kv dd{margin:0;font-weight:600;word-break:break-word}
.hero-d{display:flex;gap:14px;align-items:center}
.avatar{width:54px;height:54px;border-radius:18px;background:linear-gradient(145deg,#b34a50,#7f3035);color:#fff;display:flex;align-items:center;justify-content:center;flex:none;box-shadow:0 6px 14px rgba(127,48,53,.3)}
.hero-d b.n{font-size:21px;letter-spacing:-.01em;line-height:1.2;display:block}
.prog{margin-top:18px}.prog .bar{display:grid;grid-template-columns:repeat(6,1fr);gap:5px}
.prog .bar i{height:7px;border-radius:99px;background:#e9e3dc}.prog .bar i.done{background:var(--ok)}.prog .bar i.now{background:var(--brand);box-shadow:0 0 0 3px rgba(167,64,69,.16)}
.plabel{display:flex;justify-content:space-between;align-items:baseline;margin-top:9px;font-size:13.5px}.plabel b{font-size:15px}.plabel span{color:var(--muted)}
.prog.off,.prog.ice{display:flex;gap:10px;align-items:center;background:#f4f1ee;border-radius:14px;padding:12px 14px;font-size:14.5px}
.prog.ice{background:#e8f4fc;color:#165e8f}
.ticket{position:relative;margin-top:18px;border:2px dashed #d9c9c2;border-radius:18px;padding:14px;text-align:center;background:linear-gradient(180deg,#fffaf7,#fbf2ed)}
.ticket small{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.1em;font-weight:700}
.code{font:800 30px/1.15 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.12em;color:var(--brand2);margin:4px 0 8px}
.copy{background:#fff;border:1.5px solid #e2c3c5;color:var(--brand);border-radius:999px;padding:7px 14px;font:700 13px inherit;font-family:inherit;cursor:pointer;display:inline-flex;gap:6px;align-items:center}
.timeline{list-style:none;margin:6px 0 0;padding:0}
.timeline li{position:relative;padding:0 0 20px 34px}
.timeline li:before{content:"";position:absolute;left:11px;top:22px;bottom:-2px;width:2px;background:var(--line)}
.timeline li:last-child:before{display:none}.timeline li:last-child{padding-bottom:0}
.timeline li i{position:absolute;left:0;top:1px;width:24px;height:24px;border-radius:50%;background:#ece6df;color:#8c8279;display:flex;align-items:center;justify-content:center}
.timeline li:first-child i{background:var(--brand);color:#fff;box-shadow:0 0 0 4px rgba(167,64,69,.16)}
.timeline b{display:block;font-size:15px;line-height:1.3}.timeline small{color:var(--muted);font-size:13.5px}

.tabbar{position:fixed;left:12px;right:12px;bottom:calc(10px + var(--safe-b));z-index:30;background:rgba(255,255,255,.94);backdrop-filter:saturate(1.6) blur(14px);-webkit-backdrop-filter:saturate(1.6) blur(14px);
border:1px solid var(--line);border-radius:24px;display:grid;grid-template-columns:repeat(4,1fr);padding:6px 8px;box-shadow:0 10px 30px rgba(31,42,38,.18);max-width:520px;margin:0 auto}
.tabbar a{display:flex;flex-direction:column;align-items:center;gap:3px;font-size:12px;color:var(--muted);padding:7px 0;font-weight:600;border-radius:16px}
.tabbar a.on{color:var(--brand)}.tabbar a.on .ic{stroke-width:2.3}
.tabbar a.plus .pbtn{width:54px;height:54px;border-radius:50%;background:linear-gradient(180deg,#b84e54,#a74045);color:#fff;display:flex;align-items:center;justify-content:center;margin-top:-26px;
box-shadow:0 8px 20px rgba(167,64,69,.5),0 0 0 5px #fff}
.tabbar a.plus{color:var(--brand);font-weight:700}
.empty{text-align:center;color:var(--muted);padding:34px 16px}
.empty .eico{width:64px;height:64px;border-radius:50%;background:var(--brand-soft);color:var(--brand);display:flex;align-items:center;justify-content:center;margin:0 auto 12px}
.empty b{color:var(--ink);display:block;font-size:17px;margin-bottom:2px}
.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(98px + var(--safe-b));background:#1f2a26;color:#fff;border-radius:999px;padding:11px 18px;font-size:14px;z-index:40;box-shadow:0 8px 24px rgba(0,0,0,.25);display:flex;gap:8px;align-items:center;animation:fadein .25s both}
.center{text-align:center}
.infocard{display:flex;gap:14px;align-items:flex-start}
.infocard .ii{width:42px;height:42px;border-radius:13px;background:var(--brand-soft);color:var(--brand);display:flex;align-items:center;justify-content:center;flex:none}
.infocard b{display:block;font-size:16px}

.qhero{border-radius:24px;padding:20px;color:#fff;text-align:center;margin:0 0 4px;background:radial-gradient(120% 140% at 100% 0%,#c95a60 0,rgba(201,90,96,0) 55%),linear-gradient(145deg,#a74045 0,#6f2a2f 100%);box-shadow:0 14px 34px rgba(127,48,53,.3)}
.qhero small{display:block;opacity:.88;font-size:13.5px;letter-spacing:.04em;text-transform:uppercase;font-weight:700}
.qtotal{font-size:44px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin:4px 0}.qhero span{opacity:.85;font-size:13.5px}
.qchip{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(96px + var(--safe-b));z-index:35;background:#1f2a26;color:#fff;border-radius:999px;padding:10px 18px;font-size:14px;box-shadow:0 8px 24px rgba(0,0,0,.28);white-space:nowrap}
.qchip b{font-size:16px;margin-left:4px}#quoteResult{scroll-margin-top:96px}
.qline{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:11px 0;border-bottom:1px dashed var(--line)}
.qline:last-child{border-bottom:0}.qline b{display:block;font-size:15px;line-height:1.3}.qline small{color:var(--muted);font-size:13px}
.qline>span{font-weight:750;white-space:nowrap}.qsum{border-top:2px solid var(--ink);border-bottom:0;margin-top:4px;padding-top:12px;font-size:17px}
.login-hero{margin:0 -16px;padding:calc(34px + var(--safe-t)) 16px 92px;background:radial-gradient(120% 140% at 100% 0%,#c95a60 0,rgba(201,90,96,0) 55%),linear-gradient(145deg,#a74045,#6f2a2f);border-radius:0 0 34px 34px;color:#fff;text-align:center}
.login-hero h1{color:#fff;margin:14px 0 4px}.login-hero p{margin:0;opacity:.88;font-size:15px}
.login-logo{width:132px;height:132px;border-radius:34px;background:#fff;margin:0 auto;display:flex;align-items:center;justify-content:center;box-shadow:0 14px 34px rgba(0,0,0,.28)}
.login-logo img{width:112px;height:auto}
.login-card{margin-top:-58px;position:relative}
.hint{background:#fff8e6;border:1px dashed #e3c66f;border-radius:14px;padding:12px 14px;font-size:13.5px;margin-top:14px;color:#6b5200;line-height:1.5}
.login-foot{text-align:center;margin-top:20px;color:var(--muted);font-size:13.5px}

@media(min-width:860px){
.wrap{padding-top:28px;padding-bottom:60px;max-width:760px}.topnav{display:flex}.tabbar{display:none}.toast{bottom:28px}
.hero{padding:30px 28px 24px}.hero h1{font-size:32px}
.rcard:hover{transform:translateY(-2px)}
.fsec{padding:6px 24px 22px}
.login-hero{border-radius:0 0 44px 44px}
.wrap.login{max-width:480px}
}
"""

JS = """
(function(){
  var live=document.querySelector('[data-live]');
  if(live){
    var cursor=parseInt(live.getAttribute('data-cursor')||'0',10);
    var busy=false;
    function showToast(msg){var t=document.createElement('div');t.className='toast';t.textContent=msg;document.body.appendChild(t);setTimeout(function(){t.remove()},2800)}
    function poll(){
      if(document.hidden||busy)return; busy=true;
      fetch('/partner/v1/eventi?dopo='+cursor,{credentials:'same-origin',headers:{'Accept':'application/json'}})
        .then(function(r){if(r.status===401){location.href='/partner/accedi';return null}return r.json()})
        .then(function(d){
          if(!d||!d.events||!d.events.length){busy=false;return}
          cursor=d.cursor;
          return fetch(location.href,{credentials:'same-origin'}).then(function(r){return r.text()}).then(function(t){
            var doc=new DOMParser().parseFromString(t,'text/html');var fresh=doc.querySelector('[data-live]');
            if(fresh){live.innerHTML=fresh.innerHTML}
            showToast('Aggiornato: '+d.events[d.events.length-1].label);busy=false;
          });
        }).catch(function(){busy=false});
    }
    setInterval(poll,20000);document.addEventListener('visibilitychange',function(){if(!document.hidden)poll()});
  }
  var form=document.getElementById('reqForm');
  if(form){
    var q=function(s){return form.querySelector(s)};
    var val=function(n){var x=form.querySelector('[name="'+n+'"]:checked');return x?x.value:''};
    function sync(){
      var mode=val('mode'),service=val('service_type');
      var coll=service==='Cremazione collettiva';
      var frz=q('[name="freezer"]');
      var canFreezer=coll&&mode==='ritiro_clinica';
      var fz=q('#freezerBox'); if(fz)fz.classList.toggle('hidden',!canFreezer);
      var vb=q('#voucherBox'); if(vb)vb.classList.toggle('hidden',!coll);
      var inFreezer=canFreezer&&frz&&frz.checked;
      q('#whenBox').classList.toggle('hidden',!!inFreezer);
      q('#addressBox').classList.toggle('hidden',mode!=='ritiro_domicilio');
      q('#siteBox').classList.toggle('hidden',mode!=='invio_in_sede');
      q('#siteHelp').classList.toggle('hidden',mode!=='invio_in_sede');
      var urgent=q('#urgentBox'); if(urgent)urgent.classList.toggle('hidden',!!inFreezer);
      q('#timeBox').classList.toggle('hidden',val('fascia')!=='preciso');
      if(!coll){var u=q('[name="use_voucher"]');if(u)u.checked=false}
      if(!canFreezer&&frz)frz.checked=false;
    }
    form.addEventListener('change',sync);sync();
    var dateInput=q('[name="proposed_date"]');
    function markDate(){form.querySelectorAll('[data-date]').forEach(function(x){x.classList.toggle('on',!!dateInput&&x.getAttribute('data-date')===dateInput.value)})}
    form.querySelectorAll('[data-date]').forEach(function(b){b.addEventListener('click',function(){dateInput.value=b.getAttribute('data-date');markDate()})});
    if(dateInput){dateInput.addEventListener('input',markDate);markDate()}
    form.addEventListener('submit',function(){var b=q('button[type=submit]');if(b){b.disabled=true;b.textContent='Invio in corso…'}});
  }
  var qf=document.getElementById('quoteForm');
  if(qf){
    var qbox=document.getElementById('quoteResult'),qtimer=null;
    var qsubmit=document.getElementById('quoteSubmit');if(qsubmit)qsubmit.classList.add('hidden');
    function qsync(){
      document.getElementById('pickupBox').classList.toggle('hidden',!qf.querySelector('[name=ritiro]').checked);
      document.getElementById('deliveryBox').classList.toggle('hidden',!qf.querySelector('[name=riconsegna]').checked);
    }
    function qrun(){
      var p=new URLSearchParams(new FormData(qf));p.set('frag','1');
      fetch('/partner/preventivo?'+p.toString(),{credentials:'same-origin'}).then(function(r){
        if(r.status===401||r.redirected){location.href='/partner/accedi';return null}return r.text()
      }).then(function(h){if(h!==null&&h!==undefined){qbox.innerHTML=h;qchip()}}).catch(function(){});
    }
    var chip=document.createElement('a');chip.className='qchip hidden';chip.href='#quoteResult';document.body.appendChild(chip);
    function qchip(){
      var tot=qbox.querySelector('.qtotal');
      if(!tot){chip.classList.add('hidden');return}
      chip.innerHTML='Totale <b>'+tot.textContent+'</b> ↑';
      chip.classList.toggle('hidden',qbox.getBoundingClientRect().bottom>90);
    }
    window.addEventListener('scroll',qchip,{passive:true});
    function qschedule(){clearTimeout(qtimer);qtimer=setTimeout(qrun,220)}
    qf.addEventListener('input',function(){qsync();qschedule()});qf.addEventListener('change',function(){qsync();qschedule()});
    qf.addEventListener('submit',function(ev){ev.preventDefault();qrun()});
    qsync();
  }
  document.querySelectorAll('[data-freezer-date]').forEach(function(b){b.addEventListener('click',function(){
    var f=b.closest('form');var i=f.querySelector('[name="proposed_date"]');i.value=b.getAttribute('data-freezer-date');
    f.querySelectorAll('[data-freezer-date]').forEach(function(x){x.classList.toggle('on',x===b)})})});
  document.querySelectorAll('form[data-once]').forEach(function(f){f.addEventListener('submit',function(){
    var b=f.querySelector('button[type=submit]');if(b){b.disabled=true}})});
  document.addEventListener('click',function(ev){
    var b=ev.target.closest('[data-copy]');if(!b)return;
    var text=b.getAttribute('data-copy');
    var done=function(){var old=b.innerHTML;b.textContent='Copiato!';setTimeout(function(){b.innerHTML=old},1600)};
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(done,function(){})}
  });
})();
"""


# ---------------------------------------------------------------------------
# Impaginazione
# ---------------------------------------------------------------------------

def page(title, body, *, user=None, active="", staff=False, cursor=None, wrap_class=""):
    staffbar = (
        f'<div class="staffbar"><span>{icon("eye", 16)} Anteprima staff: le richieste create qui arrivano davvero nel calendario.</span>'
        f'<a href="/">{icon("back", 14)} Gestionale</a></div>' if staff else "")
    top = ""
    tabs = ""
    if user is not None:
        def nav(href, key, name, label, cls=""):
            return f'<a href="{href}" class="{cls}{" on" if active == key else ""}">{icon(name, 18)}<span>{label}</span></a>'
        top = (f'<header class="top"><div class="top-in"><div class="brand"><img src="/assets/pwa-192.png" alt="" width="42" height="42">'
               f'<div style="min-width:0"><b>{e(user["clinic_label"])}</b><small>Portale Veterinari · Pet Paradise</small></div></div>'
               f'<nav class="topnav" aria-label="Menu">{nav("/partner", "home", "list", "Richieste")}{nav("/partner/preventivo", "quote", "calc", "Preventivo")}{nav("/partner/info", "info", "info", "Info")}'
               f'{nav("/partner/nuova", "new", "plus", "Nuova richiesta", "cta")}</nav></div></header>')

        def tab(href, key, name, label, cls=""):
            inner = f'<span class="pbtn">{icon(name, 26)}</span>' if cls == "plus" else icon(name, 23)
            return f'<a href="{href}" class="{cls}{" on" if active == key else ""}">{inner}<span>{label}</span></a>'
        tabs = ('<nav class="tabbar" aria-label="Menu">'
                + tab("/partner", "home", "list", "Richieste")
                + tab("/partner/preventivo", "quote", "calc", "Preventivo")
                + tab("/partner/nuova", "new", "plus", "Nuova", "plus")
                + tab("/partner/info", "info", "info", "Info") + "</nav>")
    live = f' data-live data-cursor="{int(cursor)}"' if cursor is not None else ""
    classes = ("login " if user is None else "") + wrap_class
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{e(title)} · Pet Paradise Partners</title>
<meta name="theme-color" content="#a74045"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="PP Partners"><meta name="mobile-web-app-capable" content="yes">
<link rel="manifest" href="/partner/manifest.json"><link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="icon" href="/assets/favicon-32.png"><style>{CSS}</style></head>
<body>{staffbar}{top}<main class="wrap {classes}"{live}>{body}</main>{tabs}<script>{JS}</script></body></html>'''


def _flash(query):
    out = ""
    if query.get("ok"):
        out += f'<div class="flash ok">{icon("check", 20)}<span>{e(query["ok"][-1])}</span></div>'
    if query.get("err"):
        out += f'<div class="flash err">{icon("alert", 20)}<span>{e(query["err"][-1])}</span></div>'
    return out


def badge(status, mode=""):
    return f'<span class="badge {STATUS_CLASS.get(status, "st-off")}">{e(ps.public_status_label(status, mode))}</span>'


def _tags(r):
    return ("".join((
        f'<span class="tag urgent">{icon("alert", 13)}URGENTE</span>' if r["urgent"] else "",
        f'<span class="tag ice">{icon("snow", 13)}Congelatore</span>' if r["freezer"] else "",
        f'<span class="tag voucher">{icon("ticket", 13)}Usa un buono</span>' if r["reserved_voucher_id"] else "")))


def login_page(*, error="", staff=False, username=""):
    hint = (f'<div class="hint"><b>Anteprima staff</b><br>Utente: <b>{e(ps.DEMO_USERNAME)}</b> · '
            f'Password: <b>{e(ps.DEMO_PASSWORD)}</b><br>Entri come se fossi una clinica: le richieste che crei '
            f'arrivano davvero sul calendario.</div>' if staff else "")
    body = f'''<div class="login-hero"><div class="login-logo"><img src="/assets/company_logo_light.png" alt="Pet Paradise"></div>
<h1>Portale Veterinari</h1><p>Invia e segui le richieste di ritiro in tempo reale.</p></div>
<form class="card login-card" method="post" action="/partner/accedi" autocomplete="on">
{f'<div class="flash err">{icon("alert", 20)}<span>{e(error)}</span></div>' if error else ""}
<label for="u" style="margin-top:4px">Utente</label><input id="u" name="username" value="{e(username)}" autocomplete="username" autocapitalize="none" autocorrect="off" required>
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<div style="margin-top:18px"><button class="btn" type="submit">Accedi</button></div>{hint}</form>
<p class="login-foot">Problemi di accesso? Scrivi a <a href="mailto:{CONTACT_EMAIL}"><u>{CONTACT_EMAIL}</u></a></p>'''
    return page("Accesso", body, staff=staff)


def request_card(r):
    label, when = _when_text(r)
    who = " ".join(x for x in (r["owner_first_name"], r["owner_last_name"]) if x)
    species = f'{e(r["species"])}{", " + e(r["weight_text"]) if r["weight_text"] else ""}'
    when_html = (f'<div class="rc-when">{icon("clock" if r["public_status"] == "in_congelatore" else "calendar", 16)}'
                 f'<span>{e(label)}: <b>{e(when)}</b></span></div>' if when else "")
    acc = STATUS_CLASS.get(r["public_status"], "st-off").replace("st-", "acc-")
    return f'''<a class="rcard {acc}" href="/partner/richieste/{r["id"]}">
<div class="rc-ico">{icon(MODE_ICON_NAMES.get(r["mode"], "paw"), 24)}</div>
<div class="rc-main"><div class="rc-title">{e(r["animal_name"] or r["species"])} <span class="sub">· {species}</span></div>
<div class="rc-sub">{e(MODE_SHORT.get(r["mode"], ""))} · {e(who)}</div>
<div class="rc-meta">{badge(r["public_status"], r["mode"])}{when_html}</div></div>
<div class="rc-foot"><div class="tags">{_tags(r)}</div><span class="rc-code">{e(r["request_code"])}</span></div></a>'''


def _date_chips(today):
    days = []
    for i, name in enumerate(("Oggi", "Domani", "Dopodomani")):
        d = today + timedelta(days=i)
        days.append((name, d.isoformat()))
    return days


def home_page(user, *, requests, pending_freezer, vouchers, cursor, query, staff, today):
    open_reqs = [r for r in requests if r["public_status"] in OPEN_STATUSES]
    done = [r for r in requests if r["public_status"] not in OPEN_STATUSES][:10]
    tiles = f'<div class="tile"><b>{len(open_reqs)}</b><span>In corso</span></div>'
    if user["vouchers_enabled"]:
        tiles += f'<div class="tile"><b>{vouchers}</b><span>Buoni disponibili</span></div>'
    if user["has_freezer"]:
        tiles += f'<div class="tile"><b>{len(pending_freezer)}</b><span>In congelatore</span></div>'
    tile_count = 1 + (1 if user["vouchers_enabled"] else 0) + (1 if user["has_freezer"] else 0)
    freezer_card = ""
    if user["has_freezer"] and pending_freezer:
        chips = "".join(f'<button type="button" class="pill" data-freezer-date="{iso}">{name}</button>'
                        for name, iso in _date_chips(today))
        freezer_card = f'''<details class="card"><summary style="cursor:pointer;list-style:none"><div class="infocard"><div class="ii" style="background:#e3f2fc;color:#165e8f">{icon("snow", 22)}</div>
<div><b>{len(pending_freezer)} animal{"e" if len(pending_freezer) == 1 else "i"} in congelatore</b>
<span class="sub">Nessuna fretta. Congelatore pieno? Tocca qui per chiedere il ritiro.</span></div></div></summary>
<form method="post" action="/partner/congelatore" data-once><label>Quando possiamo passare?</label>
<div class="chips" style="margin-bottom:10px">{chips}</div>
<input type="date" name="proposed_date" min="{today.isoformat()}" required>
<label>Fascia</label><div class="chips">
<label class="chip"><input type="radio" name="fascia" value="mattina" checked><span>Mattina 9-13</span></label>
<label class="chip"><input type="radio" name="fascia" value="pomeriggio"><span>Pomeriggio 14-18</span></label></div>
<div style="margin-top:16px"><button class="btn" type="submit">Richiedi il ritiro</button></div></form></details>'''
    open_html = "".join(request_card(r) for r in open_reqs) or (
        f'<div class="card empty"><div class="eico">{icon("paw", 30)}</div><b>Nessuna richiesta in corso</b>'
        f'Quando ne crei una la trovi qui, con tutti gli aggiornamenti.</div>')
    done_html = (f'<h2>Concluse <span class="count">{len(done)}</span></h2>{"".join(request_card(r) for r in done)}' if done else "")
    body = f'''{_flash(query)}<section class="hero"><small>{_greeting()},</small><h1>{e(user["display_name"] or user["clinic_label"])}</h1>
<p>{e(user["clinic_label"])} · segui i ritiri in tempo reale</p>
<a class="btn light" href="/partner/nuova">{icon("plus", 20)} Nuova richiesta</a>
<div class="stats" style="grid-template-columns:repeat({tile_count},1fr)">{tiles}</div></section>
{freezer_card}<h2>In corso <span class="count">{len(open_reqs)}</span></h2>{open_html}{done_html}'''
    return page("Richieste", body, user=user, active="home", staff=staff, cursor=cursor)


def new_page(user, *, draft, error, token, sites, vouchers, staff, today):
    d = draft or {}
    ck = lambda name, value, default=False: ("checked" if (d.get(name, "") == value or (default and not d.get(name))) else "")
    modes = "".join(
        f'''<label class="mode"><input type="radio" name="mode" value="{key}" {ck("mode", key, key == "ritiro_clinica")}><div><span class="mi">{icon(MODE_ICON_NAMES[key], 24)}</span><span><b>{title}</b><small>{sub}</small></span></div></label>'''
        for key, title, sub in (
            ("ritiro_clinica", "Ritiro in clinica", "Passiamo noi dalla tua clinica"),
            ("ritiro_domicilio", "Ritiro a domicilio", "Presso il cliente"),
            ("invio_in_sede", "Cliente in sede", "Il cliente porta l'animale da noi")))
    chips_date = "".join(f'<button type="button" class="pill" data-date="{iso}">{name}</button>' for name, iso in _date_chips(today))
    freezer_box = (f'''<label class="check ice" id="freezerBox"><input type="checkbox" name="freezer" value="1" {"checked" if d.get("freezer") else ""}>
<span class="ci">{icon("snow", 20)}</span><span><b>Ce l'ho nel congelatore</b><small>Nessuna fretta: lo ritirate quando il congelatore è pieno.</small></span></label>'''
                   if user["has_freezer"] else "")
    voucher_box = (f'''<label class="check voucher" id="voucherBox"><input type="checkbox" name="use_voucher" value="1" {"checked" if d.get("use_voucher") else ""}>
<span class="ci">{icon("ticket", 20)}</span><span><b>Usa un buono</b><small>{vouchers} disponibil{"e" if vouchers == 1 else "i"}.</small></span></label>'''
                   if user["vouchers_enabled"] and vouchers > 0 else "")
    site_opts = "".join(f'<option value="{e(s)}" {"selected" if d.get("destination_site") == s else ""}>{e(s)}</option>' for s in sites)
    species_list = "".join(f"<option>{s}</option>" for s in ("Cane", "Gatto", "Coniglio", "Furetto", "Uccello", "Criceto", "Altro"))
    body = f'''<h1>Nuova richiesta</h1><p class="sub" style="margin:0 2px">Ci vuole meno di un minuto.</p>
{f'<div class="flash err">{icon("alert", 20)}<span>{e(error)}</span></div>' if error else ""}
<form id="reqForm" method="post" action="/partner/nuova"><input type="hidden" name="token" value="{e(token)}">
<section class="fsec"><h2><span class="num">1</span>Cosa serve?</h2><div class="modes">{modes}</div>
<div id="siteHelp" class="sub hidden" style="margin-top:10px">Riceverai un codice da presentare in sede insieme all'animale.</div>
<div id="siteBox" class="hidden"><label>Sede</label><select name="destination_site">{site_opts}</select></div>
<div id="addressBox" class="hidden"><label>Indirizzo del ritiro</label><input name="pickup_address" value="{e(d.get("pickup_address", ""))}" placeholder="Via, numero, comune" autocomplete="street-address"></div></section>
<section class="fsec"><h2><span class="num">2</span>Servizio</h2><div class="chips" style="margin-top:12px">
<label class="chip"><input type="radio" name="service_type" value="Cremazione singola" {ck("service_type", "Cremazione singola", True)}><span>Cremazione singola</span></label>
<label class="chip"><input type="radio" name="service_type" value="Cremazione collettiva" {ck("service_type", "Cremazione collettiva")}><span>Cremazione collettiva</span></label></div>
{freezer_box}{voucher_box}</section>
<section class="fsec"><h2><span class="num">3</span>Proprietario</h2><div class="grid2"><div><label>Nome</label><input name="owner_first_name" value="{e(d.get("owner_first_name", ""))}" autocomplete="off"></div>
<div><label>Cognome</label><input name="owner_last_name" value="{e(d.get("owner_last_name", ""))}" autocomplete="off"></div></div>
<label>Telefono</label><input name="owner_phone" type="tel" inputmode="tel" value="{e(d.get("owner_phone", ""))}" autocomplete="off" placeholder="333 1234567"></section>
<section class="fsec"><h2><span class="num">4</span>Animale</h2><div class="grid2"><div><label>Specie</label><input name="species" list="speciesList" value="{e(d.get("species", ""))}" autocomplete="off"><datalist id="speciesList">{species_list}</datalist></div>
<div><label>Nome (facoltativo)</label><input name="animal_name" value="{e(d.get("animal_name", ""))}" autocomplete="off"></div></div>
<label>Peso o taglia</label><input name="weight" value="{e(d.get("weight", ""))}" placeholder="es. 12 kg, oppure taglia media" autocomplete="off"></section>
<section class="fsec" id="whenBox"><h2><span class="num">5</span>Quando?</h2>
<div class="chips" style="margin:12px 0 10px">{chips_date}</div>
<input type="date" name="proposed_date" min="{today.isoformat()}" value="{e(d.get("proposed_date", ""))}">
<label>Fascia oraria</label><div class="chips">
<label class="chip"><input type="radio" name="fascia" value="mattina" {ck("fascia", "mattina", True)}><span>Mattina 9-13</span></label>
<label class="chip"><input type="radio" name="fascia" value="pomeriggio" {ck("fascia", "pomeriggio")}><span>Pomeriggio 14-18</span></label>
<label class="chip"><input type="radio" name="fascia" value="preciso" {ck("fascia", "preciso")}><span>Orario preciso</span></label></div>
<div id="timeBox" class="grid2 hidden"><div><label>Dalle</label><input type="time" name="proposed_from" value="{e(d.get("proposed_from", ""))}"></div>
<div><label>Alle</label><input type="time" name="proposed_to" value="{e(d.get("proposed_to", ""))}"></div></div>
<label class="check urgent" id="urgentBox"><input type="checkbox" name="urgent" value="1" {"checked" if d.get("urgent") else ""}>
<span class="ci">{icon("alert", 20)}</span><span><b>Urgente</b><small>Ritiro il prima possibile.</small></span></label></section>
<section class="fsec"><h2><span class="num">6</span>Note</h2><label style="margin-top:10px">Facoltative</label>
<textarea name="notes" placeholder="Accessi, citofono, indicazioni utili…">{e(d.get("notes", ""))}</textarea></section>
<div class="submitbar"><button class="btn" type="submit">{icon("send", 19)} Invia richiesta</button></div></form>'''
    return page("Nuova richiesta", body, user=user, active="new", staff=staff)


def _progress(r):
    status = r["public_status"]
    if status == "annullata":
        return f'<div class="prog off">{icon("alert", 20)}<span>Richiesta annullata</span></div>'
    if status == "in_congelatore":
        return (f'<div class="prog ice">{icon("snow", 20)}<span><b>In congelatore.</b> Lo ritiriamo quando il congelatore è pieno, '
                f'nessuna fretta.</span></div>')
    idx = PROGRESS_STEPS.index(status) if status in PROGRESS_STEPS else 0
    segs = "".join(f'<i class="{"done" if k < idx else "now" if k == idx else ""}"></i>' for k in range(len(PROGRESS_STEPS)))
    return (f'<div class="prog"><div class="bar">{segs}</div><div class="plabel"><b>{e(ps.public_status_label(status, r["mode"]))}</b>'
            f'<span>Passo {idx + 1} di {len(PROGRESS_STEPS)}</span></div></div>')


def detail_page(user, r, timeline, *, cursor, query, staff):
    label, when = _when_text(r)
    who = " ".join(x for x in (r["owner_first_name"], r["owner_last_name"]) if x)
    rows = [
        ("Animale", f'{r["animal_name"] or "-"} ({r["species"]}{", " + r["weight_text"] if r["weight_text"] else ""})'),
        ("Proprietario", f'{who} · {r["owner_phone"]}'),
        ("Servizio", r["service_type"]),
        ("Modalità", ps.MODES.get(r["mode"], "")),
    ]
    if r["pickup_address"]:
        rows.append(("Indirizzo", r["pickup_address"]))
    if r["destination_site"]:
        rows.append(("Sede", r["destination_site"]))
    if when:
        rows.append((label or "Quando", when))
    if r["notes"]:
        rows.append(("Note", r["notes"]))
    kv = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows)
    tags = _tags(r)
    items = []
    for ev in reversed(timeline):
        text = "Ritiro riprogrammato" if ev["kind"] == "riprogrammato" else ps.public_status_label(ev["public_status"], r["mode"])
        extra = f' · {_fmt_window(ev["scheduled_start"], ev["scheduled_end"])}' if ev["scheduled_start"] else ""
        mark = icon("clock", 13) if ev["kind"] == "riprogrammato" else icon("check", 14)
        items.append(f'<li><i>{mark}</i><b>{e(text)}</b><small>{e(_fmt_utc(ev["created_at"]))}{e(extra)}</small></li>')
    sede_note = ('<p class="sub center" style="margin:0">Presenta questo codice in sede insieme all\'animale.</p>'
                 if r["mode"] == "invio_in_sede" and r["public_status"] not in ("annullata", "completata") else "")
    cancel = ""
    if r["public_status"] in ("ricevuta", "in_congelatore"):
        cancel = (f'<form method="post" action="/partner/richieste/{r["id"]}/annulla" data-once style="margin-top:6px" '
                  f'onsubmit="return confirm(\'Annullare questa richiesta?\')"><button class="btn danger" type="submit">Annulla richiesta</button></form>')
    elif r["public_status"] not in ("annullata", "completata"):
        cancel = '<p class="sub center">Per modificare o annullare ora contatta Pet Paradise.</p>'
    ok_new = (f'<div class="flash ok">{icon("check", 20)}<span>Richiesta inviata! Ti aggiorniamo qui a ogni passaggio.</span></div>'
              if query.get("nuova") else "")
    body = f'''{ok_new}{_flash({k: v for k, v in query.items() if k in ("ok", "err")})}
<div class="card"><div class="hero-d"><div class="avatar">{icon(MODE_ICON_NAMES.get(r["mode"], "paw"), 28)}</div>
<div style="flex:1;min-width:0"><b class="n">{e(r["animal_name"] or r["species"])}</b><span class="sub">{e(who)}</span></div>{badge(r["public_status"], r["mode"])}</div>
{f'<div class="tags" style="margin-top:12px">{tags}</div>' if tags else ""}
{_progress(r)}
<div class="ticket"><small>Codice richiesta</small><div class="code">{e(r["request_code"])}</div>{sede_note}
<button type="button" class="copy" data-copy="{e(r["request_code"])}">{icon("copy", 15)} Copia codice</button></div></div>
<div class="card"><h3>{icon("list", 18)} Dettagli</h3><dl class="kv">{kv}</dl></div>
<div class="card"><h3>{icon("clock", 18)} Cronologia</h3><ul class="timeline">{"".join(items)}</ul></div>{cancel}
<p style="margin-top:16px"><a class="btn ghost" href="/partner">{icon("back", 18)} Tutte le richieste</a></p>'''
    return page("Dettaglio richiesta", body, user=user, active="home", staff=staff, cursor=cursor)


def info_page(user, *, locations, vouchers, staff, query):
    sedi = "".join(
        f'<div class="card"><div class="infocard"><div class="ii">{icon("pin", 22)}</div><div><b>{e(loc["name"])}</b>'
        f'<div class="sub">{e(loc["address"])}</div>'
        f'<p style="margin:10px 0 0"><a class="btn ghost small" target="_blank" rel="noopener" '
        f'href="https://www.google.com/maps/search/?api=1&query={quote(loc["address"])}">Apri in Maps</a></p></div></div></div>'
        for loc in locations) or '<div class="card sub">Informazioni sulle sedi in arrivo.</div>'
    voucher_card = (f'<div class="card"><div class="infocard"><div class="ii" style="background:#fdf0d3;color:#8a5a00">{icon("ticket", 22)}</div>'
                    f'<div><b>I tuoi buoni</b><span class="sub"><b style="display:inline;color:var(--ink)">{vouchers}</b> '
                    f'disponibil{"e" if vouchers == 1 else "i"} per le cremazioni collettive.</span></div></div></div>'
                    if user["vouchers_enabled"] else "")
    body = f'''{_flash(query)}<h1>Info e contatti</h1><h2>Le nostre sedi</h2>{sedi}
<h2>Contatti</h2><div class="card"><div class="infocard"><div class="ii">{icon("mail", 22)}</div><div><b>Scrivici</b>
<span class="sub">Per qualsiasi necessità: <a href="mailto:{CONTACT_EMAIL}" style="color:var(--brand)"><u>{CONTACT_EMAIL}</u></a></span></div></div></div>
{voucher_card}
<h2>Il tuo account</h2><div class="card"><div class="infocard"><div class="ii">{icon("user", 22)}</div><div><b>{e(user["display_name"] or user["username"] or "")}</b>
<span class="sub">{e(user["clinic_name"])} · {e(user["email"])}</span></div></div>
<form method="post" action="/partner/esci" style="margin-top:14px"><button class="btn ghost" type="submit">{icon("logout", 18)} Esci</button></form></div>'''
    return page("Info", body, user=user, active="info", staff=staff)


def _comune_options(pricelist, selected="", *, first_label="Seleziona il comune", same_label=""):
    groups = "".join(
        f'<optgroup label="{e(item["name"])}">' + "".join(
            f'<option value="{e(c)}" {"selected" if selected == c else ""}>{e(c)}</option>' for c in item["comuni"]) + "</optgroup>"
        for item in pricelist["circondari"])
    same = (f'<option value="{qs.SAME_COMUNE}" {"selected" if selected in ("", qs.SAME_COMUNE) else ""}>{e(same_label)}</option>'
            if same_label else f'<option value="">{e(first_label)}</option>')
    other = f'<option value="{qs.OTHER_COMUNE}" {"selected" if selected == qs.OTHER_COMUNE else ""}>Altro comune (fuori circondario)</option>'
    return same + groups + other


def quote_result_html(pricelist, values) -> str:
    """Blocco risultato del preventivo (anche come frammento per l'aggiornamento live)."""
    if not (values.get("peso") or "").strip():
        return (f'<div class="card empty"><div class="eico">{icon("calc", 28)}</div><b>Inserisci il peso</b>'
                f'Il preventivo si aggiorna mentre compili i campi.</div>')
    when, time_known = None, True
    try:
        if values.get("data"):
            try:
                day = datetime.strptime(values["data"], "%Y-%m-%d").date()
            except ValueError:
                raise qs.QuoteError("Data non valida.") from None
            hour = (values.get("ora") or "").strip()
            if hour:
                if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hour):
                    raise qs.QuoteError("Ora non valida.")
                when = datetime.combine(day, datetime.strptime(hour, "%H:%M").time())
            else:
                when, time_known = datetime.combine(day, datetime.min.time().replace(hour=12)), False
        pickup = values.get("ritiro") == "1"
        if pickup and not values.get("luogo"):
            raise qs.QuoteIncomplete("Seleziona il luogo del ritiro.")
        result = qs.calculate_quote(
            pricelist, weight=values.get("peso"),
            pickup_place=values.get("luogo", "") if pickup else "", pickup_comune=values.get("comune", "") if pickup else "",
            delivery=values.get("riconsegna") == "1", delivery_comune=values.get("comune_riconsegna", ""),
            when=when, time_known=time_known)
    except qs.QuoteIncomplete as exc:
        return f'<div class="flash">{icon("info", 20)}<span>{e(exc)} Il preventivo compare appena hai scelto tutto.</span></div>'
    except qs.QuoteError as exc:
        return f'<div class="flash err">{icon("alert", 20)}<span>{e(exc)}</span></div>'
    rows = ""
    for line in result["lines"]:
        detail = f'<small>{e(line["detail"])}</small>' if line["detail"] else ""
        rows += (f'<div class="qline"><div><b>{e(line["label"])}</b>{detail}</div>'
                 f'<span>{e(qs.fmt_eur(line["amount"]))}</span></div>')
    hints = "".join(f'<div class="flash"><span>{e(h)}</span></div>' for h in result["hints"])
    return (f'<div class="qhero"><small>Preventivo indicativo</small><div class="qtotal">{e(qs.fmt_eur(result["total"]))}</div>'
            f'<span>IVA inclusa · urna esclusa</span></div>'
            f'<div class="card"><h3>{icon("list", 18)} Dettaglio</h3>{rows}'
            f'<div class="qline qsum"><div><b>Totale</b></div><span>{e(qs.fmt_eur(result["total"]))}</span></div></div>'
            f'<div class="card"><div class="infocard"><div class="ii" style="background:#fdf0d3;color:#8a5a00">{icon("urn", 22)}</div>'
            f'<div><b>Urna</b><span class="sub">{e(result["urn_note"])}</span></div></div></div>{hints}')


def quote_page(user, pricelist, values, *, staff):
    v = values
    luoghi = "".join(
        f'<option value="{key}" {"selected" if v.get("luogo") == key else ""}>{e(label)}</option>'
        for key, (label, _tariff) in qs.PICKUP_PLACES.items())
    body = f'''<h1>Preventivo</h1><p class="sub" style="margin:0 2px">Cremazione singola con ritiro e riconsegna: un'indicazione di prezzo da dare al cliente.</p>
<div id="quoteResult" style="margin-top:14px">{quote_result_html(pricelist, v)}</div>
<form id="quoteForm" method="get" action="/partner/preventivo" autocomplete="off">
<section class="fsec"><h2><span class="num">1</span>Animale</h2><label>Peso (kg)</label>
<input name="peso" inputmode="decimal" placeholder="es. 12,5" value="{e(v.get("peso", ""))}"></section>
<section class="fsec"><h2><span class="num">2</span>Ritiro</h2>
<label class="check"><input type="checkbox" name="ritiro" value="1" {"checked" if v.get("ritiro", "1") == "1" else ""}><span class="ci">{icon("home", 20)}</span>
<span><b>Serve il ritiro</b><small>Lascia spento se il cliente porta l'animale in sede.</small></span></label>
<div id="pickupBox"><label>Dove si ritira?</label><select name="luogo"><option value="">Seleziona il luogo</option>{luoghi}</select>
<label>Comune del ritiro</label><select name="comune">{_comune_options(pricelist, v.get("comune", ""))}</select></div></section>
<section class="fsec"><h2><span class="num">3</span>Riconsegna</h2>
<label class="check"><input type="checkbox" name="riconsegna" value="1" {"checked" if v.get("riconsegna") == "1" else ""}><span class="ci">{icon("car", 20)}</span>
<span><b>Serve la riconsegna</b><small>Nel circondario o fuori: cambia il prezzo.</small></span></label>
<div id="deliveryBox"><label>Comune della riconsegna</label><select name="comune_riconsegna">{_comune_options(pricelist, v.get("comune_riconsegna", ""), same_label="Stesso comune del ritiro")}</select></div></section>
<section class="fsec"><h2><span class="num">4</span>Quando <small class="sub" style="font-weight:500;text-transform:none;letter-spacing:0">(facoltativo)</small></h2>
<p class="sub" style="margin:2px 0 0">Serve per i supplementi festivo, serale e notturno.</p>
<div class="grid2"><div><label>Data</label><input type="date" name="data" value="{e(v.get("data", ""))}"></div>
<div><label>Ora</label><input type="time" name="ora" value="{e(v.get("ora", ""))}"></div></div></section>
<div class="submitbar" id="quoteSubmit"><button class="btn" type="submit">{icon("calc", 19)} Calcola preventivo</button></div></form>
<p class="sub center" style="margin:14px 8px 0">Preventivo indicativo: il prezzo definitivo viene confermato da Pet Paradise.</p>'''
    return page("Preventivo", body, user=user, active="quote", staff=staff)


def not_found_page(user=None, staff=False):
    return page("Pagina non trovata",
                f'<div class="card empty"><div class="eico">{icon("paw", 30)}</div><b>Pagina non trovata</b>'
                f'<p style="margin:0 0 16px">Il collegamento non è valido o è scaduto.</p><a class="btn" href="/partner">Torna alle richieste</a></div>',
                user=user, staff=staff)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def manifest() -> dict:
    return {
        "id": "/partner", "name": "Pet Paradise Partners", "short_name": "PP Partners",
        "description": "Portale per le cliniche veterinarie partner di Pet Paradise",
        "lang": "it-IT", "start_url": "/partner", "scope": "/partner", "display": "standalone",
        "background_color": "#f4f1ed", "theme_color": "#a74045",
        "icons": [
            {"src": "/assets/pwa-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/assets/pwa-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }


def _session_token(h) -> str:
    jar = cookies.SimpleCookie(h.headers.get("Cookie", ""))
    morsel = jar.get(COOKIE)
    return morsel.value if morsel else ""


def _client_ip(h) -> str:
    forwarded = (h.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (h.client_address[0] if getattr(h, "client_address", None) else "")


def _cookie_header(h, token: str, max_age: int) -> str:
    secure = "; Secure" if (h.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
    return f"{COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/partner; Max-Age={max_age}{secure}"


def _redirect(h, location, set_cookie=None):
    h.send_response(303)
    h.send_header("Location", location)
    if set_cookie:
        h.send_header("Set-Cookie", set_cookie)
    h.send_header("Cache-Control", "no-store")
    h.end_headers()


def _to_query(message_key, text):
    return f"?{message_key}={quote(text)}"


def _requests_url(request_id, **params):
    suffix = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"/partner/richieste/{request_id}" + (f"?{suffix}" if suffix else "")


def dispatch(h, method, path, *, db, password_ok, staff_user, db_path=None):
    """Gestisce ogni richiesta sotto /partner. Ritorna sempre True."""
    parsed = urlparse(h.path)
    query = parse_qs(parsed.query)
    if path != "/partner" and path.endswith("/"):
        path = path.rstrip("/")
    staff = bool(staff_user())
    today = datetime.now(ROME).date()

    if method == "GET" and path == "/partner/manifest.json":
        data = json.dumps(manifest(), ensure_ascii=False).encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Type", "application/manifest+json; charset=utf-8")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
        return True

    token = _session_token(h)

    if path == "/partner/accedi":
        with db() as c:
            user = ps.session_user(c, token)
        if method == "GET":
            if user:
                return _redirect(h, "/partner") or True
            h.send_html(login_page(staff=staff))
            return True
        form = h.form()
        username = form.get("username", "")
        with db() as c:
            row, error = ps.authenticate(c, username=username, password=form.get("password", ""),
                                         ip=_client_ip(h), verify_password=password_ok, allow_demo=staff)
            new_token = ps.create_session(c, row["id"], demo=bool(row["is_demo"])) if row else ""
        if not row:
            h.send_html(login_page(error=error, staff=staff, username=username), 200)
            return True
        max_age = ps.DEMO_SESSION_HOURS * 3600 if row["is_demo"] else ps.SESSION_DAYS * 86400
        _redirect(h, "/partner", _cookie_header(h, new_token, max_age))
        return True

    with db() as c:
        user = ps.session_user(c, token)
    if not user:
        if path.startswith("/partner/v1/"):
            h.send_json({"error": "non autenticato"}, 401)
        else:
            _redirect(h, "/partner/accedi")
        return True

    if method == "POST" and path == "/partner/esci":
        with db() as c:
            ps.delete_session(c, token)
        _redirect(h, "/partner/accedi", _cookie_header(h, "", 0))
        return True

    cid = user["clinic_id"]

    def cursor_now():
        with db() as c:
            row = c.execute("SELECT COALESCE(MAX(id),0) AS m FROM partner_events WHERE clinic_id=?", (cid,)).fetchone()
        return row["m"]

    if method == "GET" and path == "/partner/v1/eventi":
        after = (query.get("dopo") or ["0"])[-1]
        with db() as c:
            events = ps.events_since(c, cid, int(after) if after.isdigit() else 0)
        payload = {
            "cursor": events[-1]["id"] if events else int(after) if after.isdigit() else 0,
            "events": [{"id": ev["id"], "request_id": ev["request_id"], "request_code": ev["request_code"],
                        "kind": ev["kind"], "public_status": ev["public_status"],
                        "label": ("Ritiro riprogrammato" if ev["kind"] == "riprogrammato"
                                  else ps.public_status_label(ev["public_status"], ev["mode"])),
                        "created_at": ev["created_at"]} for ev in events],
        }
        h.send_json(payload)
        return True

    if method == "GET" and path == "/partner":
        with db() as c:
            requests = ps.list_requests(c, cid, limit=60)
            pending = ps.pending_freezer_requests(c, cid) if user["has_freezer"] else []
            vouchers = len(ps.available_vouchers(c, cid)) if user["vouchers_enabled"] else 0
        h.send_html(home_page(user, requests=requests, pending_freezer=pending, vouchers=vouchers,
                              cursor=cursor_now(), query=query, staff=staff, today=today))
        return True

    if path == "/partner/nuova":
        with db() as c:
            sites = ps.BRANCHES
            vouchers = len(ps.available_vouchers(c, cid)) if user["vouchers_enabled"] else 0
        if method == "GET":
            h.send_html(new_page(user, draft={}, error="", token=secrets.token_urlsafe(18), sites=sites,
                                 vouchers=vouchers, staff=staff, today=today))
            return True
        form = h.form()
        fascia = form.get("fascia", "mattina")
        p_from, p_to = FASCE.get(fascia, (form.get("proposed_from", ""), form.get("proposed_to", "")))
        if form.get("urgent") == "1" and not form.get("proposed_date"):
            p_from = p_to = ""  # urgente senza data: oggi, il prima possibile (non una fascia gia' trascorsa)
        if fascia == "preciso" and not (p_from and p_to) and form.get("urgent") != "1" and form.get("freezer") != "1":
            h.send_html(new_page(user, draft=form, error="Indica l'orario preciso: dalle ... alle ...",
                                 token=form.get("token", "") or secrets.token_urlsafe(18), sites=sites,
                                 vouchers=vouchers, staff=staff, today=today))
            return True
        freezer = form.get("freezer") == "1" and form.get("service_type") == "Cremazione collettiva"
        try:
            with db() as c:
                request, _created = ps.create_request(
                    c, clinic_id=cid, user_id=user["user_id"], client_request_id=form.get("token", ""),
                    mode=form.get("mode", ""), service_type=form.get("service_type", ""),
                    owner_first_name=form.get("owner_first_name", ""), owner_last_name=form.get("owner_last_name", ""),
                    owner_phone=form.get("owner_phone", ""), species=form.get("species", ""), weight=form.get("weight", ""),
                    animal_name=form.get("animal_name", ""), notes=form.get("notes", ""),
                    pickup_address=form.get("pickup_address", ""), destination_site=form.get("destination_site", ""),
                    proposed_date=form.get("proposed_date", ""), proposed_from=p_from, proposed_to=p_to,
                    urgent=form.get("urgent") == "1", freezer=freezer,
                    use_voucher=form.get("use_voucher") == "1", db_path=db_path)
        except ps.PartnerError as exc:
            h.send_html(new_page(user, draft=form, error=str(exc), token=form.get("token", "") or secrets.token_urlsafe(18),
                                 sites=sites, vouchers=vouchers, staff=staff, today=today))
            return True
        _redirect(h, _requests_url(request["id"], nuova=1))
        return True

    match = re.fullmatch(r"/partner/richieste/(\d+)", path)
    if method == "GET" and match:
        with db() as c:
            request = ps.get_request(c, cid, int(match.group(1)))
            timeline = ps.request_timeline(c, cid, int(match.group(1))) if request else []
        if not request:
            h.send_html(not_found_page(user, staff), 404)
            return True
        h.send_html(detail_page(user, request, timeline, cursor=cursor_now(), query=query, staff=staff))
        return True

    match = re.fullmatch(r"/partner/richieste/(\d+)/annulla", path)
    if method == "POST" and match:
        rid = int(match.group(1))
        try:
            with db() as c:
                ps.cancel_request(c, clinic_id=cid, request_id=rid, user_id=user["user_id"], db_path=db_path)
        except ps.PartnerError as exc:
            _redirect(h, _requests_url(rid, err=str(exc)))
            return True
        _redirect(h, _requests_url(rid, ok="Richiesta annullata."))
        return True

    if method == "POST" and path == "/partner/congelatore":
        form = h.form()
        p_from, p_to = FASCE.get(form.get("fascia", "mattina"), ("", ""))
        try:
            with db() as c:
                ps.request_freezer_pickup(c, clinic_id=cid, user_id=user["user_id"],
                                          proposed_date=form.get("proposed_date", ""), proposed_from=p_from,
                                          proposed_to=p_to, db_path=db_path)
        except ps.PartnerError as exc:
            _redirect(h, "/partner" + _to_query("err", str(exc)))
            return True
        _redirect(h, "/partner" + _to_query("ok", "Richiesta di ritiro inviata: ti confermiamo l'orario qui."))
        return True

    if method == "GET" and path == "/partner/preventivo":
        with db() as c:
            pricelist = qs.get_pricelist(c)
        values = {k: (query.get(k) or [""])[-1].strip() for k in
                  ("peso", "ritiro", "luogo", "comune", "riconsegna", "comune_riconsegna", "data", "ora")}
        if not query:
            values["ritiro"] = "1"  # prima apertura: il caso piu' comune
        if query.get("frag"):
            h.send_html(quote_result_html(pricelist, values))
        else:
            h.send_html(quote_page(user, pricelist, values, staff=staff))
        return True

    if method == "GET" and path == "/partner/info":
        with db() as c:
            locations = c.execute("SELECT name,address FROM company_locations WHERE active=1 ORDER BY name").fetchall()
            vouchers = len(ps.available_vouchers(c, cid)) if user["vouchers_enabled"] else 0
        h.send_html(info_page(user, locations=locations, vouchers=vouchers, staff=staff, query=query))
        return True

    h.send_html(not_found_page(user, staff), 404)
    return True
