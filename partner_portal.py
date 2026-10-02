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
MODE_ICONS = {"ritiro_clinica": "🏥", "ritiro_domicilio": "🏠", "invio_in_sede": "🚗"}
MODE_SHORT = {
    "ritiro_clinica": "Ritiro in clinica",
    "ritiro_domicilio": "Ritiro a domicilio",
    "invio_in_sede": "Cliente in sede",
}
FASCE = {"mattina": ("09:00", "13:00"), "pomeriggio": ("14:00", "18:00")}


def e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


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
        return "Ritiro", "Quando il congelatore e' pieno: nessuna fretta"
    if status in ("programmato", "ritirato", "in_lavorazione", "pronto_riconsegna", "completata") and r["event_start"]:
        label = "Ritiro confermato" if status == "programmato" else "Ritiro"
        return label, _fmt_window(r["event_start"], r["event_end"])
    proposed = _fmt_proposed(r)
    return ("Orario proposto", proposed) if proposed else ("", "")


# ---------------------------------------------------------------------------
# Stile e script
# ---------------------------------------------------------------------------

CSS = """
:root{--brand:#a74045;--brand2:#7f3035;--ink:#24312c;--muted:#6e7b75;--line:#e4ded7;--bg:#f4f1ed;--card:#fff;--green:#39745b;
--safe-b:env(safe-area-inset-bottom,0px);--safe-t:env(safe-area-inset-top,0px);color-scheme:light}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{overscroll-behavior-y:contain}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;min-height:100vh}
a{color:inherit;text-decoration:none}
.wrap{max-width:640px;margin:0 auto;padding:14px 16px calc(96px + var(--safe-b))}
.wrap.login{display:flex;flex-direction:column;justify-content:center;min-height:100vh;padding-bottom:32px}
.staffbar{background:#24312c;color:#fff;font-size:13px;padding:8px 16px;display:flex;gap:10px;align-items:center;justify-content:space-between;padding-top:calc(8px + var(--safe-t))}
.staffbar a{background:rgba(255,255,255,.16);border-radius:999px;padding:4px 12px;white-space:nowrap}
.top{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:10px 16px;padding-top:calc(10px + var(--safe-t));display:flex;align-items:center;gap:12px}
.top img{width:40px;height:40px;object-fit:cover;border-radius:10px}
.top b{display:block;font-size:16px;line-height:1.2}.top small{color:var(--muted);font-size:12.5px}
h1{font-size:24px;line-height:1.2;margin:6px 0 4px}h2{font-size:17px;margin:22px 0 10px}h3{font-size:15px;margin:0 0 6px}
.sub{color:var(--muted);font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px;margin:12px 0;box-shadow:0 1px 2px rgba(36,49,44,.04)}
a.card{display:block}a.card:active{transform:scale(.99)}
.row{display:flex;gap:10px;align-items:center;justify-content:space-between}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}
.tile{background:#fff;border:1px solid var(--line);border-radius:16px;padding:12px;text-align:center}
.tile b{display:block;font-size:24px;line-height:1.1}.tile span{font-size:12.5px;color:var(--muted)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:48px;padding:0 20px;border:0;border-radius:14px;background:var(--brand);color:#fff;font:600 16px system-ui,sans-serif;cursor:pointer;width:100%}
.btn:active{background:var(--brand2)}.btn[disabled]{opacity:.6}
.btn.ghost{background:#fff;color:var(--brand);border:1.5px solid var(--brand)}
.btn.small{min-height:38px;font-size:14px;width:auto;padding:0 14px}
.btn.danger{background:#fff;color:#a23030;border:1.5px solid #d9a5a5}
.badge{display:inline-block;border-radius:999px;padding:3px 11px;font-size:12.5px;font-weight:700;white-space:nowrap}
.st-wait{background:#fff0c9;color:#7a5200}.st-ice{background:#dff0fb;color:#17598a}.st-plan{background:#e6ecff;color:#2a45a0}
.st-pick{background:#d8f1ea;color:#1b6753}.st-work{background:#eee3fb;color:#63399e}.st-ready{background:#dcf4de;color:#21682b}
.st-done{background:#e7ebe8;color:#46544d}.st-off{background:#eee;color:#777}
.tag{display:inline-block;font-size:12px;font-weight:700;border-radius:8px;padding:2px 8px;margin-right:4px;background:#f1ece5;color:#5a4a3b}
.tag.urgent{background:#fde1e1;color:#a02323}
label{display:block;font-weight:600;font-size:14px;margin:12px 0 5px}
input,select,textarea{width:100%;min-height:48px;border:1.5px solid var(--line);border-radius:12px;padding:10px 12px;font:16px system-ui,sans-serif;background:#fff;color:var(--ink)}
textarea{min-height:84px;resize:vertical}
input:focus,select:focus,textarea:focus{outline:2px solid rgba(167,64,69,.35);border-color:var(--brand)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{position:relative}.chip input{position:absolute;opacity:0;inset:0;width:100%;height:100%;min-height:0;cursor:pointer}
.chip span{display:block;border:1.5px solid var(--line);background:#fff;border-radius:999px;padding:10px 16px;font-weight:600;font-size:15px}
.chip input:checked+span{background:var(--brand);border-color:var(--brand);color:#fff}
.modes{display:grid;gap:10px}
.mode{position:relative}.mode input{position:absolute;opacity:0;inset:0;width:100%;height:100%;min-height:0;cursor:pointer}
.mode div{display:flex;gap:14px;align-items:center;border:1.5px solid var(--line);background:#fff;border-radius:16px;padding:14px}
.mode i{font-style:normal;font-size:28px}.mode b{display:block}.mode small{color:var(--muted)}
.mode input:checked+div{border-color:var(--brand);background:#fbf1f1;box-shadow:0 0 0 2px rgba(167,64,69,.18)}
.check{display:flex;gap:12px;align-items:flex-start;border:1.5px solid var(--line);background:#fff;border-radius:14px;padding:12px 14px;margin:10px 0}
.check input{width:22px;height:22px;min-height:0;margin-top:2px;accent-color:var(--brand);flex:none}
.check b{display:block;font-size:15px}.check small{color:var(--muted)}
.check.urgent{border-color:#e8b4b4;background:#fff7f7}
.flash{border-radius:14px;padding:12px 14px;margin:12px 0;font-size:15px}
.flash.ok{background:#e1f5e3;color:#1f5f2a}.flash.err{background:#fde8e8;color:#8f2020}
.hidden{display:none!important}
.kv{display:grid;grid-template-columns:116px 1fr;gap:6px 10px;font-size:15px}.kv dt{color:var(--muted)}.kv dd{margin:0;font-weight:600;word-break:break-word}
.code{font:700 26px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.08em;background:#f6efe7;border-radius:12px;padding:12px;text-align:center;margin:10px 0}
.timeline{list-style:none;margin:0;padding:0 0 0 6px}
.timeline li{position:relative;padding:0 0 18px 22px;border-left:2px solid var(--line);margin-left:6px}
.timeline li:last-child{border-left-color:transparent;padding-bottom:0}
.timeline li:before{content:"";position:absolute;left:-7px;top:3px;width:12px;height:12px;border-radius:50%;background:#cfc7bd}
.timeline li:first-child:before{background:var(--brand)}
.timeline b{display:block;font-size:15px}.timeline small{color:var(--muted)}
.tabbar{position:fixed;left:0;right:0;bottom:0;z-index:6;background:#fff;border-top:1px solid var(--line);display:grid;grid-template-columns:1fr 1fr 1fr;padding:6px 8px calc(6px + var(--safe-b));max-width:none}
.tabbar a,.tabbar button{display:flex;flex-direction:column;align-items:center;gap:2px;font-size:12px;color:var(--muted);padding:6px 0;background:none;border:0;font-family:inherit;cursor:pointer}
.tabbar span.ico{font-size:22px;line-height:1}
.tabbar a.on{color:var(--brand);font-weight:700}
.tabbar a.plus .ico{background:var(--brand);color:#fff;width:50px;height:50px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:28px;margin-top:-22px;box-shadow:0 4px 12px rgba(167,64,69,.45)}
.tabbar a.plus{color:var(--brand);font-weight:700}
.empty{text-align:center;color:var(--muted);padding:26px 10px}
.loginlogo{width:210px;max-width:60%;margin:0 auto 10px;display:block}
.center{text-align:center}
.hint{background:#fff8e1;border:1px dashed #e0c36a;border-radius:12px;padding:10px 12px;font-size:13.5px;margin-top:14px;color:#6b5200}
.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(92px + var(--safe-b));background:#24312c;color:#fff;border-radius:999px;padding:10px 18px;font-size:14px;z-index:9}
@media(min-width:700px){.wrap{padding-top:24px}.tabbar{left:50%;right:auto;transform:translateX(-50%);width:640px;border-radius:18px 18px 0 0;border:1px solid var(--line)}}
"""

JS = """
(function(){
  var live=document.querySelector('[data-live]');
  if(live){
    var cursor=parseInt(live.getAttribute('data-cursor')||'0',10);
    var busy=false;
    function showToast(msg){var t=document.createElement('div');t.className='toast';t.textContent=msg;document.body.appendChild(t);setTimeout(function(){t.remove()},2600)}
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
    form.querySelectorAll('[data-date]').forEach(function(b){b.addEventListener('click',function(){q('[name="proposed_date"]').value=b.getAttribute('data-date');
      form.querySelectorAll('[data-date]').forEach(function(x){x.classList.toggle('on',x===b)})})});
    form.addEventListener('submit',function(){var b=q('button[type=submit]');if(b){b.disabled=true;b.textContent='Invio in corso…'}});
  }
  document.querySelectorAll('[data-freezer-date]').forEach(function(b){b.addEventListener('click',function(){
    var f=b.closest('form');f.querySelector('[name="proposed_date"]').value=b.getAttribute('data-freezer-date')})});
  document.querySelectorAll('form[data-once]').forEach(function(f){f.addEventListener('submit',function(){
    var b=f.querySelector('button[type=submit]');if(b){b.disabled=true}})});
})();
"""


# ---------------------------------------------------------------------------
# Impaginazione
# ---------------------------------------------------------------------------

def page(title, body, *, user=None, active="", staff=False, cursor=None):
    staffbar = (
        '<div class="staffbar"><span>👁 Anteprima staff: le richieste create qui arrivano davvero nel calendario.</span>'
        '<a href="/">← Gestionale</a></div>' if staff else "")
    top = ""
    tabs = ""
    if user is not None:
        top = (f'<header class="top"><img src="/assets/pwa-192.png" alt="" width="40" height="40">'
               f'<div><b>{e(user["clinic_label"])}</b><small>Portale Veterinari · Pet Paradise</small></div></header>')
        def tab(href, key, ico, label, cls=""):
            return f'<a href="{href}" class="{cls}{" on" if active == key else ""}"><span class="ico">{ico}</span>{label}</a>'
        tabs = ('<nav class="tabbar" aria-label="Menu">'
                + tab("/partner", "home", "📋", "Richieste")
                + tab("/partner/nuova", "new", "+", "Nuova", "plus")
                + tab("/partner/info", "info", "ℹ️", "Info") + "</nav>")
    live = f' data-live data-cursor="{int(cursor)}"' if cursor is not None else ""
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{e(title)} · Pet Paradise Partners</title>
<meta name="theme-color" content="#a74045"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="PP Partners"><meta name="mobile-web-app-capable" content="yes">
<link rel="manifest" href="/partner/manifest.json"><link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="icon" href="/assets/favicon-32.png"><style>{CSS}</style></head>
<body>{staffbar}{top}<main class="wrap{" login" if user is None else ""}"{live}>{body}</main>{tabs}<script>{JS}</script></body></html>'''


def _flash(query):
    out = ""
    if query.get("ok"):
        out += f'<div class="flash ok">{e(query["ok"][-1])}</div>'
    if query.get("err"):
        out += f'<div class="flash err">{e(query["err"][-1])}</div>'
    return out


def badge(status, mode=""):
    return f'<span class="badge {STATUS_CLASS.get(status, "st-off")}">{e(ps.public_status_label(status, mode))}</span>'


def login_page(*, error="", staff=False, username=""):
    hint = (f'<div class="hint"><b>Anteprima staff</b><br>Utente: <b>{e(ps.DEMO_USERNAME)}</b> · '
            f'Password: <b>{e(ps.DEMO_PASSWORD)}</b><br>Entri come se fossi una clinica: le richieste che crei '
            f'arrivano davvero sul calendario.</div>' if staff else "")
    body = f'''<div class="center"><img class="loginlogo" src="/assets/company_logo_light.png" alt="Pet Paradise">
<h1>Portale Veterinari</h1><p class="sub">Accedi per inviare e seguire le tue richieste di ritiro.</p></div>
<form class="card" method="post" action="/partner/accedi" autocomplete="on">
{f'<div class="flash err">{e(error)}</div>' if error else ""}
<label for="u">Utente</label><input id="u" name="username" value="{e(username)}" autocomplete="username" autocapitalize="none" autocorrect="off" required>
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<div style="margin-top:16px"><button class="btn" type="submit">Accedi</button></div></form>{hint}
<p class="sub center" style="margin-top:18px">Problemi di accesso? Scrivi a <a href="mailto:{CONTACT_EMAIL}"><u>{CONTACT_EMAIL}</u></a></p>'''
    return page("Accesso", body, staff=staff)


def request_card(r):
    label, when = _when_text(r)
    who = " ".join(x for x in (r["owner_first_name"], r["owner_last_name"]) if x)
    tags = ('<span class="tag urgent">URGENTE</span>' if r["urgent"] else "") + (
        '<span class="tag">Congelatore</span>' if r["freezer"] else "") + (
        '<span class="tag">Buono</span>' if r["reserved_voucher_id"] else "")
    return f'''<a class="card" href="/partner/richieste/{r["id"]}"><div class="row"><div>
<b>{e(r["animal_name"] or r["species"])}</b> <span class="sub">· {e(r["species"])}{", " + e(r["weight_text"]) if r["weight_text"] else ""}</span>
<div class="sub">{MODE_ICONS.get(r["mode"], "")} {e(MODE_SHORT.get(r["mode"], ""))} · {e(who)}</div></div>{badge(r["public_status"], r["mode"])}</div>
{f'<div style="margin-top:8px;font-size:14.5px"><span class="sub">{e(label)}:</span> <b>{e(when)}</b></div>' if when else ""}
<div style="margin-top:8px">{tags}<span class="sub" style="float:right">{e(r["request_code"])}</span></div></a>'''


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
    tiles = f'<div class="tiles" style="grid-template-columns:repeat({tile_count},1fr)">{tiles}</div>'
    freezer_card = ""
    if user["has_freezer"] and pending_freezer:
        chips = "".join(f'<button type="button" class="btn ghost small" data-freezer-date="{iso}">{name}</button> '
                        for name, iso in _date_chips(today))
        freezer_card = f'''<details class="card"><summary><b>❄️ {len(pending_freezer)} animal{"e" if len(pending_freezer) == 1 else "i"} in congelatore</b>
<span class="sub"> · nessuna fretta. Congelatore pieno? Chiedi il ritiro.</span></summary>
<form method="post" action="/partner/congelatore" data-once><label>Quando possiamo passare?</label>
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">{chips}</div>
<input type="date" name="proposed_date" min="{today.isoformat()}" required>
<label>Fascia</label><div class="chips">
<label class="chip"><input type="radio" name="fascia" value="mattina" checked><span>Mattina 9-13</span></label>
<label class="chip"><input type="radio" name="fascia" value="pomeriggio"><span>Pomeriggio 14-18</span></label></div>
<div style="margin-top:14px"><button class="btn" type="submit">Richiedi il ritiro</button></div></form></details>'''
    open_html = "".join(request_card(r) for r in open_reqs) or (
        '<div class="empty card">Nessuna richiesta in corso.<br>Tocca <b>+</b> per crearne una.</div>')
    done_html = (f'<h2>Concluse</h2>{"".join(request_card(r) for r in done)}' if done else "")
    body = f'''{_flash(query)}<h1>Ciao, {e(user["display_name"] or user["clinic_label"])}</h1>
<p class="sub">Qui segui i ritiri della tua clinica in tempo reale.</p>
<a class="btn" href="/partner/nuova" style="margin-top:6px">+ Nuova richiesta</a>
<div data-live-inner>{tiles}{freezer_card}<h2>In corso</h2>{open_html}{done_html}</div>'''
    return page("Richieste", body, user=user, active="home", staff=staff, cursor=cursor)


def new_page(user, *, draft, error, token, sites, vouchers, staff, today):
    d = draft or {}
    ck = lambda name, value, default=False: ("checked" if (d.get(name, "") == value or (default and not d.get(name))) else "")
    modes = "".join(
        f'''<label class="mode"><input type="radio" name="mode" value="{key}" {ck("mode", key, key == "ritiro_clinica")}><div><i>{MODE_ICONS[key]}</i><span><b>{title}</b><small>{sub}</small></span></div></label>'''
        for key, title, sub in (
            ("ritiro_clinica", "Ritiro in clinica", "Passiamo noi dalla tua clinica"),
            ("ritiro_domicilio", "Ritiro a domicilio", "Presso il cliente"),
            ("invio_in_sede", "Cliente in sede", "Il cliente porta l'animale da noi")))
    chips_date = "".join(f'<button type="button" class="btn ghost small" data-date="{iso}">{name}</button> '
                         for name, iso in _date_chips(today))
    freezer_box = (f'''<label class="check" id="freezerBox"><input type="checkbox" name="freezer" value="1" {"checked" if d.get("freezer") else ""}>
<span><b>❄️ Ce l'ho nel congelatore</b><small>Nessuna fretta: lo ritirate quando il congelatore è pieno.</small></span></label>'''
                   if user["has_freezer"] else "")
    voucher_box = (f'''<label class="check" id="voucherBox"><input type="checkbox" name="use_voucher" value="1" {"checked" if d.get("use_voucher") else ""}>
<span><b>🎟️ Usa un buono</b><small>{vouchers} disponibil{"e" if vouchers == 1 else "i"}.</small></span></label>'''
                   if user["vouchers_enabled"] and vouchers > 0 else "")
    site_opts = "".join(f'<option value="{e(s)}" {"selected" if d.get("destination_site") == s else ""}>{e(s)}</option>' for s in sites)
    species_list = "".join(f"<option>{s}</option>" for s in ("Cane", "Gatto", "Coniglio", "Furetto", "Uccello", "Criceto", "Altro"))
    body = f'''<h1>Nuova richiesta</h1><p class="sub">Ci vuole meno di un minuto.</p>
{f'<div class="flash err">{e(error)}</div>' if error else ""}
<form id="reqForm" method="post" action="/partner/nuova"><input type="hidden" name="token" value="{e(token)}">
<h2>Cosa serve?</h2><div class="modes">{modes}</div>
<div id="siteHelp" class="sub hidden" style="margin-top:8px">Riceverai un codice da presentare in sede insieme all'animale.</div>
<div id="siteBox" class="hidden"><label>Sede</label><select name="destination_site">{site_opts}</select></div>
<div id="addressBox" class="hidden"><label>Indirizzo del ritiro</label><input name="pickup_address" value="{e(d.get("pickup_address", ""))}" placeholder="Via, numero, comune" autocomplete="street-address"></div>
<h2>Servizio</h2><div class="chips">
<label class="chip"><input type="radio" name="service_type" value="Cremazione singola" {ck("service_type", "Cremazione singola", True)}><span>Cremazione singola</span></label>
<label class="chip"><input type="radio" name="service_type" value="Cremazione collettiva" {ck("service_type", "Cremazione collettiva")}><span>Cremazione collettiva</span></label></div>
{freezer_box}{voucher_box}
<h2>Proprietario</h2><div class="grid2"><div><label>Nome</label><input name="owner_first_name" value="{e(d.get("owner_first_name", ""))}" autocomplete="off"></div>
<div><label>Cognome</label><input name="owner_last_name" value="{e(d.get("owner_last_name", ""))}" autocomplete="off"></div></div>
<label>Telefono</label><input name="owner_phone" type="tel" inputmode="tel" value="{e(d.get("owner_phone", ""))}" autocomplete="off" placeholder="333 1234567">
<h2>Animale</h2><div class="grid2"><div><label>Specie</label><input name="species" list="speciesList" value="{e(d.get("species", ""))}" autocomplete="off"><datalist id="speciesList">{species_list}</datalist></div>
<div><label>Nome (facoltativo)</label><input name="animal_name" value="{e(d.get("animal_name", ""))}" autocomplete="off"></div></div>
<label>Peso o taglia</label><input name="weight" value="{e(d.get("weight", ""))}" placeholder="es. 12 kg, oppure taglia media" autocomplete="off">
<div id="whenBox"><h2>Quando?</h2>
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">{chips_date}</div>
<input type="date" name="proposed_date" min="{today.isoformat()}" value="{e(d.get("proposed_date", ""))}">
<label>Fascia oraria</label><div class="chips">
<label class="chip"><input type="radio" name="fascia" value="mattina" {ck("fascia", "mattina", True)}><span>Mattina 9-13</span></label>
<label class="chip"><input type="radio" name="fascia" value="pomeriggio" {ck("fascia", "pomeriggio")}><span>Pomeriggio 14-18</span></label>
<label class="chip"><input type="radio" name="fascia" value="preciso" {ck("fascia", "preciso")}><span>Orario preciso</span></label></div>
<div id="timeBox" class="grid2 hidden"><div><label>Dalle</label><input type="time" name="proposed_from" value="{e(d.get("proposed_from", ""))}"></div>
<div><label>Alle</label><input type="time" name="proposed_to" value="{e(d.get("proposed_to", ""))}"></div></div>
<label class="check urgent" id="urgentBox"><input type="checkbox" name="urgent" value="1" {"checked" if d.get("urgent") else ""}>
<span><b>🚨 Urgente</b><small>Ritiro il prima possibile.</small></span></label></div>
<label>Note (facoltative)</label><textarea name="notes" placeholder="Accessi, citofono, indicazioni utili…">{e(d.get("notes", ""))}</textarea>
<div style="margin-top:18px"><button class="btn" type="submit">Invia richiesta</button></div></form>'''
    return page("Nuova richiesta", body, user=user, active="new", staff=staff)


def detail_page(user, r, timeline, *, cursor, query, staff):
    label, when = _when_text(r)
    who = " ".join(x for x in (r["owner_first_name"], r["owner_last_name"]) if x)
    rows = [
        ("Animale", f'{r["animal_name"] or "-"} ({r["species"]}{", " + r["weight_text"] if r["weight_text"] else ""})'),
        ("Proprietario", f'{who} · {r["owner_phone"]}'),
        ("Servizio", r["service_type"]),
        ("Modalità", f'{MODE_ICONS.get(r["mode"], "")} {ps.MODES.get(r["mode"], "")}'),
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
    tags = ('<span class="tag urgent">URGENTE</span>' if r["urgent"] else "") + (
        '<span class="tag">Congelatore</span>' if r["freezer"] else "") + (
        '<span class="tag">Usa un buono</span>' if r["reserved_voucher_id"] else "")
    items = []
    for ev in reversed(timeline):
        text = "Ritiro riprogrammato" if ev["kind"] == "riprogrammato" else ps.public_status_label(ev["public_status"], r["mode"])
        extra = f' · {_fmt_window(ev["scheduled_start"], ev["scheduled_end"])}' if ev["scheduled_start"] else ""
        items.append(f'<li><b>{e(text)}</b><small>{e(_fmt_utc(ev["created_at"]))}{e(extra)}</small></li>')
    sede_note = ('<p class="sub center">Presenta questo codice in sede insieme all\'animale.</p>'
                 if r["mode"] == "invio_in_sede" and r["public_status"] not in ("annullata", "completata") else "")
    cancel = ""
    if r["public_status"] in ("ricevuta", "in_congelatore"):
        cancel = (f'<form method="post" action="/partner/richieste/{r["id"]}/annulla" data-once '
                  f'onsubmit="return confirm(\'Annullare questa richiesta?\')"><button class="btn danger" type="submit">Annulla richiesta</button></form>')
    elif r["public_status"] not in ("annullata", "completata"):
        cancel = '<p class="sub center">Per modificare o annullare ora contatta Pet Paradise.</p>'
    ok_new = ('<div class="flash ok">Richiesta inviata! Ti aggiorniamo qui a ogni passaggio.</div>'
              if query.get("nuova") else "")
    body = f'''{ok_new}{_flash({k: v for k, v in query.items() if k in ("ok", "err")})}
<div data-live-inner><div class="card"><div class="row"><div><b style="font-size:19px">{e(r["animal_name"] or r["species"])}</b>
<div class="sub">{e(who)}</div></div>{badge(r["public_status"], r["mode"])}</div>
<div style="margin-top:8px">{tags}</div><div class="code">{e(r["request_code"])}</div>{sede_note}</div>
<div class="card"><h3>Dettagli</h3><dl class="kv">{kv}</dl></div>
<div class="card"><h3>Cronologia</h3><ul class="timeline">{"".join(items)}</ul></div>{cancel}</div>
<p style="margin-top:14px"><a class="btn ghost" href="/partner">← Tutte le richieste</a></p>'''
    return page("Dettaglio richiesta", body, user=user, active="home", staff=staff, cursor=cursor)


def info_page(user, *, locations, vouchers, staff, query):
    sedi = "".join(
        f'<div class="card"><b>{e(loc["name"])}</b><div class="sub">{e(loc["address"])}</div>'
        f'<p style="margin:8px 0 0"><a class="btn ghost small" target="_blank" rel="noopener" '
        f'href="https://www.google.com/maps/search/?api=1&query={quote(loc["address"])}">Apri in Maps</a></p></div>'
        for loc in locations) or '<div class="card sub">Informazioni sulle sedi in arrivo.</div>'
    voucher_card = (f'<div class="card"><h3>🎟️ I tuoi buoni</h3><p style="margin:0"><b>{vouchers}</b> '
                    f'disponibil{"e" if vouchers == 1 else "i"} per le cremazioni collettive.</p></div>'
                    if user["vouchers_enabled"] else "")
    body = f'''{_flash(query)}<h1>Info e contatti</h1><h2>Le nostre sedi</h2>{sedi}
<div class="card"><h3>Contatti</h3><p style="margin:0">Per qualsiasi necessità: <a href="mailto:{CONTACT_EMAIL}"><u>{CONTACT_EMAIL}</u></a></p></div>
{voucher_card}
<h2>Il tuo account</h2><div class="card"><b>{e(user["display_name"] or user["username"] or "")}</b>
<div class="sub">{e(user["clinic_name"])} · {e(user["email"])}</div>
<form method="post" action="/partner/esci" style="margin-top:12px"><button class="btn ghost" type="submit">Esci</button></form></div>'''
    return page("Info", body, user=user, active="info", staff=staff)


def not_found_page(user=None, staff=False):
    return page("Pagina non trovata", '<div class="empty card">Pagina non trovata.<br><br><a class="btn" href="/partner">Torna alle richieste</a></div>',
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

    if method == "GET" and path == "/partner/info":
        with db() as c:
            locations = c.execute("SELECT name,address FROM company_locations WHERE active=1 ORDER BY name").fetchall()
            vouchers = len(ps.available_vouchers(c, cid)) if user["vouchers_enabled"] else 0
        h.send_html(info_page(user, locations=locations, vouchers=vouchers, staff=staff, query=query))
        return True

    h.send_html(not_found_page(user, staff), 404)
    return True
