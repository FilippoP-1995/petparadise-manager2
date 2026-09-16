"""Assistente AI del gestionale (V1) — livello di strumenti controllati.

Questo modulo NON parla mai direttamente con l'LLM del significato dei dati:
espone solo un elenco di strumenti tipizzati (TOOL_SPECS), ciascuno dei quali
esegue una query reale sul database V1, riusando ove possibile la STESSA
logica gia' presente altrove nel gestionale (revenue_by_quote_category per i
ricavi per voce di preventivo, il ledger balance_movements per gli incassi,
shift_service per turni/ferie/reperibilita', ecc.) — mai una seconda fonte di
verita', mai un numero inventato.

Architettura richiesta dall'utente:
  UTENTE -> ASSISTENTE AI -> interpretazione -> STRUMENTO AUTORIZZATO -> DATABASE REALE -> risultato -> risposta
non:
  AI -> accesso libero al database.

Le poche funzioni di logica economica che vivono solo in app.py (non in un
modulo di servizio separato: money_value, effective_total,
channel_paid_amount, channel_remaining, revenue_by_quote_category, ...)
vengono iniettate una sola volta all'avvio tramite configure(Deps(...)),
cosi' questo modulo non deve importare app.py (che creerebbe un'importazione
circolare) e non deve MAI duplicarne la logica.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from calendar_service import CALENDAR_OPERATORS, EVENT_TYPES
from shift_service import (
    SHIFT_BRANCHES,
    oncall_operator_for_week,
    shifts_for_range,
    vacations_overlapping,
    week_monday,
)
from balance_service import BalanceFilters, get_movements, get_outstanding_balances


class ToolInputError(Exception):
    """Un parametro passato dall'AI non e' valido/riconosciuto, oppure la
    domanda non e' determinabile con i filtri forniti. Il messaggio arriva
    all'LLM come risultato di errore dello strumento (mai come dato reale),
    cosi' l'AI puo' riportarlo o chiedere un chiarimento invece di inventare
    un valore plausibile."""


class AssistantConfigError(Exception):
    """Manca una configurazione necessaria lato server (tipicamente
    ANTHROPIC_API_KEY) — non deve mai essere aggirata inventando una chiave
    nel codice, va sempre segnalata chiaramente."""


class AssistantAPIError(Exception):
    """Anthropic ha rifiutato o non e' riuscita a completare la richiesta
    (autenticazione, modello, limite di utilizzo/credito, richiesta non
    valida, problema di rete...). Il messaggio e' sempre sicuro: solo cio'
    che Anthropic stesso restituisce nel corpo della risposta di errore, o
    il tipo dell'eccezione di rete — MAI la chiave, MAI header di
    richiesta. Distinta da un errore di uno strumento: qui e' la chiamata
    al modello stessa a non essere andata a buon fine."""


@dataclass(frozen=True)
class Deps:
    money_value: Callable[[Any], float]
    effective_total: Callable[[Any], float]
    channel_paid_amount: Callable[[Any, int, str], float]
    channel_remaining: Callable[[Any], float]
    revenue_by_quote_category: Callable[[Any, str | None, str | None], list]
    # Stessa identica formula SQL gia' usata dalla dashboard del gestionale
    # per decidere "quando" una pratica e' stata ritirata/consegnata (vedi
    # dashboard_practice_date_sql/status_event_date_sql in app.py) - iniettata
    # qui, non riscritta, cosi' conta_ritiri/conta_riconsegne non possono MAI
    # disallinearsi dal numero che l'utente vede gia' sulla dashboard.
    dashboard_practice_date_sql: Callable[[str, str], str]
    states: tuple
    shift_operators: tuple
    month_names_it: tuple


DEPS: Deps | None = None


def configure(deps: Deps) -> None:
    global DEPS
    DEPS = deps


# ---------------------------------------------------------------------------
# Interpretazione del periodo (fuso orario Italia — "now" e' sempre passato
# dal chiamante, gia' calcolato con rome_now(), non ricalcolato qui).
# ---------------------------------------------------------------------------

PERIOD_TOKENS = (
    "oggi", "ieri", "domani", "questa_settimana", "settimana_scorsa", "settimana_prossima",
    "questo_mese", "mese_scorso", "quest_anno", "anno_scorso",
    "ultimi_7_giorni", "ultimi_30_giorni", "intervallo_personalizzato",
)


def _month_bounds(d: date) -> tuple[date, date]:
    first = d.replace(day=1)
    next_first = date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)
    return first, next_first - timedelta(days=1)


def _fmt_date(d: date) -> str:
    return f"{d.day} {DEPS.month_names_it[d.month - 1]} {d.year}"


def _period_label(d_from: date, d_to: date) -> str:
    if d_from == d_to:
        return _fmt_date(d_from)
    if d_from.year == d_to.year and d_from.month == d_to.month:
        return f"{d_from.day}–{d_to.day} {DEPS.month_names_it[d_from.month - 1]} {d_from.year}"
    if d_from.year == d_to.year:
        return f"{d_from.day} {DEPS.month_names_it[d_from.month - 1]} – {d_to.day} {DEPS.month_names_it[d_to.month - 1]} {d_from.year}"
    return f"{d_from.day} {DEPS.month_names_it[d_from.month - 1]} {d_from.year} – {d_to.day} {DEPS.month_names_it[d_to.month - 1]} {d_to.year}"


def resolve_period(now, periodo=None, data_da=None, data_a=None, optional=False):
    """Ritorna (data_da_iso, data_a_iso, etichetta_leggibile). Se optional e'
    True e non viene fornito ne' periodo ne' un intervallo esplicito, ritorna
    (None, None, None) — nessun filtro data, non un errore (es. "quante
    pratiche ha gestito Serena" senza indicazione di periodo)."""
    today = now.date()
    if periodo in (None, "", "intervallo_personalizzato"):
        if not data_da and not data_a:
            if optional:
                return None, None, None
            raise ToolInputError("Specifica un periodo (es. 'questo_mese') oppure sia data_da sia data_a in formato AAAA-MM-GG.")
        if not data_da or not data_a:
            raise ToolInputError("Per un intervallo personalizzato servono sia data_da sia data_a in formato AAAA-MM-GG.")
        try:
            d_from = date.fromisoformat(data_da)
            d_to = date.fromisoformat(data_a)
        except ValueError:
            raise ToolInputError("data_da/data_a non sono date valide (formato AAAA-MM-GG).")
        if d_from > d_to:
            raise ToolInputError("data_da e' successiva a data_a.")
        return d_from.isoformat(), d_to.isoformat(), _period_label(d_from, d_to)
    if periodo not in PERIOD_TOKENS:
        raise ToolInputError(f"periodo '{periodo}' non riconosciuto. Valori ammessi: {', '.join(PERIOD_TOKENS)}.")
    monday = today - timedelta(days=today.weekday())
    if periodo == "oggi":
        d_from = d_to = today
    elif periodo == "ieri":
        d_from = d_to = today - timedelta(days=1)
    elif periodo == "domani":
        d_from = d_to = today + timedelta(days=1)
    elif periodo == "questa_settimana":
        d_from, d_to = monday, monday + timedelta(days=6)
    elif periodo == "settimana_scorsa":
        d_from, d_to = monday - timedelta(days=7), monday - timedelta(days=1)
    elif periodo == "settimana_prossima":
        d_from, d_to = monday + timedelta(days=7), monday + timedelta(days=13)
    elif periodo == "questo_mese":
        d_from, d_to = _month_bounds(today)
    elif periodo == "mese_scorso":
        d_from, d_to = _month_bounds(today.replace(day=1) - timedelta(days=1))
    elif periodo == "quest_anno":
        d_from, d_to = date(today.year, 1, 1), date(today.year, 12, 31)
    elif periodo == "anno_scorso":
        d_from, d_to = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    elif periodo == "ultimi_7_giorni":
        d_from, d_to = today - timedelta(days=6), today
    else:  # ultimi_30_giorni
        d_from, d_to = today - timedelta(days=29), today
    return d_from.isoformat(), d_to.isoformat(), _period_label(d_from, d_to)


_PERIOD_PROPS = {
    "periodo": {
        "type": "string",
        "enum": list(PERIOD_TOKENS),
        "description": "Periodo relativo a oggi, fuso orario Italia. Usa 'intervallo_personalizzato' insieme a data_da/data_a per un intervallo esplicito (es. 'dal 10 al 20 settembre', 'da gennaio a marzo').",
    },
    "data_da": {"type": "string", "description": "Data ISO AAAA-MM-GG, richiesta solo con periodo='intervallo_personalizzato' o se periodo e' omesso."},
    "data_a": {"type": "string", "description": "Data ISO AAAA-MM-GG, richiesta solo con periodo='intervallo_personalizzato' o se periodo e' omesso."},
}


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


# ---------------------------------------------------------------------------
# Strumenti — cremazioni / ritiri / riconsegne / calendario
# ---------------------------------------------------------------------------

def _tool_conta_cremazioni(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"))
    stato = p.get("stato")
    if stato and stato not in ("pianificato", "in_attesa", "completato"):
        raise ToolInputError("stato deve essere uno tra: pianificato, in_attesa, completato.")
    sede = p.get("sede")
    if sede and sede not in SHIFT_BRANCHES:
        raise ToolInputError(f"sede deve essere una tra: {', '.join(SHIFT_BRANCHES)}.")
    where = ["cc.cycle_date>=date(?)", "cc.cycle_date<=date(?)"]
    args: list = [d_from, d_to]
    join = ""
    if stato:
        where.append("cc.status=?")
        args.append(stato)
    if sede:
        join = " JOIN practices p2 ON p2.cremation_cycle_id=cc.id"
        where.append("p2.destination_branch=?")
        args.append(sede)
    sql = f"SELECT COUNT(DISTINCT cc.id) n FROM cremation_cycles cc{join} WHERE {' AND '.join(where)}"
    n = c.execute(sql, args).fetchone()["n"]
    return {"conteggio": n, "periodo_analizzato": label, "filtri": {"stato": stato, "sede": sede}}


# conta_ritiri/conta_riconsegne NON contano gli eventi di calendario
# (Ritiro/Ritiro in sede/Riconsegna/Riconsegna in sede): quegli eventi sono
# solo la fase di PROGRAMMAZIONE, includono voci pianificate/annullate mai
# davvero avvenute, e usano zone (12 citta') invece della sede reale della
# pratica (destination_branch, solo Livorno/Empoli) - un conteggio basato
# su di essi non corrisponde a "quanti ritiri/riconsegne sono stati fatti"
# come lo intende il resto del gestionale (bug reale riscontrato: la prima
# versione di questo strumento rispondeva 42 per un mese/sede dove la
# dashboard del gestionale ne mostrava un numero diverso).
#
# La fonte autorevole e' la STESSA gia' usata dalla dashboard per le card
# "Ritirati"/"Consegnati" (dashboard_practice_date_sql/status_event_date_sql
# in app.py, iniettate qui via Deps, mai riscritte): una pratica conta come
# "ritirata" quando il suo stato ha raggiunto o superato Ritirato
# (STATES e' una progressione lineare: Ritirato->Cremato->Da consegnare->
# Consegnato->Smaltito, quindi una pratica gia' Consegnato deve continuare
# a contare come "ritirata" nel mese in cui e' stata ritirata - per questo
# lo stato e' un IN(...) e non un singolo valore), mentre "consegnata"
# richiede lo stato ESATTO 'Consegnato' (una pratica poi Smaltita smette di
# contare, esattamente come sulla dashboard - comportamento replicato
# fedelmente, non "corretto", perche' e' quello che l'utente vede gia' nel
# gestionale).
def _conta_pratiche_per_fase(c, now, p, *, kind, status_where):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"))
    sede = p.get("sede")
    if sede and sede not in SHIFT_BRANCHES:
        raise ToolInputError(f"sede deve essere una tra: {', '.join(SHIFT_BRANCHES)}.")
    operatore = p.get("operatore")
    if operatore and operatore not in CALENDAR_OPERATORS:
        raise ToolInputError(f"operatore deve essere uno tra: {', '.join(CALENDAR_OPERATORS)}.")
    date_sql = DEPS.dashboard_practice_date_sql(kind, "p")
    where = ["(p.deleted_at IS NULL OR p.deleted_at='')", status_where, f"{date_sql} BETWEEN date(?) AND date(?)"]
    args: list = [d_from, d_to]
    if sede:
        where.append("p.destination_branch=?")
        args.append(sede)
    if operatore:
        where.append("p.operator_name=?")
        args.append(operatore)
    sql = f"SELECT COUNT(*) n FROM practices p WHERE {' AND '.join(where)}"
    n = c.execute(sql, args).fetchone()["n"]
    return n, label, sede, operatore


def _tool_conta_ritiri(c, user, now, p):
    n, label, sede, operatore = _conta_pratiche_per_fase(
        c, now, p, kind="ritirati",
        status_where="p.status IN ('Ritirato','Cremato','Da consegnare','Consegnato','Smaltito')",
    )
    return {
        "conteggio": n, "periodo_analizzato": label,
        "filtri": {"sede": sede, "operatore_nome": operatore},
        "nota": "Conta le pratiche il cui ritiro risulta effettuato (stesso criterio della dashboard del gestionale: stato che ha raggiunto o superato 'Ritirato'), non gli eventi di calendario programmati. Non e' disponibile la distinzione tra ritiro a domicilio e in sede su una pratica gia' conclusa: quel dettaglio esiste solo nella fase di programmazione a calendario.",
    }


def _tool_conta_riconsegne(c, user, now, p):
    n, label, sede, operatore = _conta_pratiche_per_fase(
        c, now, p, kind="consegnati",
        status_where="p.status='Consegnato'",
    )
    return {
        "conteggio": n, "periodo_analizzato": label,
        "filtri": {"sede": sede, "operatore_nome": operatore},
        "nota": "Conta le pratiche attualmente nello stato 'Consegnato' nel periodo (stesso criterio della dashboard del gestionale). Una pratica successivamente smaltita non risulta piu' qui, come sulla dashboard. Non e' disponibile la distinzione tra riconsegna a domicilio e in sede su una pratica gia' conclusa: quel dettaglio esiste solo nella fase di programmazione a calendario.",
    }


_FASE_CONFIG = {
    "ritiri": ("ritirati", "p.status IN ('Ritirato','Cremato','Da consegnare','Consegnato','Smaltito')"),
    "riconsegne": ("consegnati", "p.status='Consegnato'"),
}


# Stessa logica/fonte di _conta_pratiche_per_fase, ma aggregata giorno per
# giorno invece che su un unico totale (necessario per "giorno con piu'
# ritiri" o "giorni a zero in una sede": un totale unico non lo permette,
# ed elencare i giorni presenti in una GROUP BY non basta per i giorni a
# zero, che semplicemente non hanno righe - vanno generati esplicitamente
# per l'intero intervallo, non dedotti dal modello).
def _andamento_pratiche_per_fase(c, now, p, *, kind, status_where):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"))
    sede = p.get("sede")
    if sede and sede not in SHIFT_BRANCHES:
        raise ToolInputError(f"sede deve essere una tra: {', '.join(SHIFT_BRANCHES)}.")
    operatore = p.get("operatore")
    if operatore and operatore not in CALENDAR_OPERATORS:
        raise ToolInputError(f"operatore deve essere uno tra: {', '.join(CALENDAR_OPERATORS)}.")
    d1, d2 = date.fromisoformat(d_from), date.fromisoformat(d_to)
    if (d2 - d1).days > 366:
        raise ToolInputError("Il periodo per un andamento giornaliero non puo' superare un anno: restringi l'intervallo.")
    date_sql = DEPS.dashboard_practice_date_sql(kind, "p")
    where = ["(p.deleted_at IS NULL OR p.deleted_at='')", status_where, f"{date_sql} BETWEEN date(?) AND date(?)"]
    args: list = [d_from, d_to]
    if sede:
        where.append("p.destination_branch=?")
        args.append(sede)
    if operatore:
        where.append("p.operator_name=?")
        args.append(operatore)
    sql = f"SELECT {date_sql} AS giorno, COUNT(*) n FROM practices p WHERE {' AND '.join(where)} GROUP BY {date_sql}"
    counts = {r["giorno"]: r["n"] for r in c.execute(sql, args).fetchall()}
    giorni = []
    cur = d1
    while cur <= d2:
        iso = cur.isoformat()
        giorni.append({"data": iso, "conteggio": counts.get(iso, 0)})
        cur += timedelta(days=1)
    return giorni, label, sede, operatore


def _tool_andamento_giornaliero(c, user, now, p):
    metrica = p.get("metrica")
    fase = _FASE_CONFIG.get(metrica)
    if not fase:
        raise ToolInputError(f"metrica deve essere una tra: {', '.join(_FASE_CONFIG)}.")
    kind, status_where = fase
    giorni, label, sede, operatore = _andamento_pratiche_per_fase(c, now, p, kind=kind, status_where=status_where)
    max_n = max((g["conteggio"] for g in giorni), default=0)
    giorni_max = [g["data"] for g in giorni if g["conteggio"] == max_n] if max_n > 0 else []
    giorni_zero = [g["data"] for g in giorni if g["conteggio"] == 0]
    dettaglio_omesso = len(giorni) > 62
    return {
        "metrica": metrica,
        "periodo_analizzato": label,
        "filtri": {"sede": sede, "operatore_nome": operatore},
        "totale_periodo": sum(g["conteggio"] for g in giorni),
        "numero_giorni_nel_periodo": len(giorni),
        "giorno_con_valore_massimo": {"date": giorni_max, "conteggio": max_n} if giorni_max else None,
        "numero_giorni_a_zero": len(giorni_zero),
        "date_giorni_a_zero": giorni_zero[:100],
        "date_giorni_a_zero_troncate": len(giorni_zero) > 100,
        "andamento_giornaliero_dettaglio": None if dettaglio_omesso else giorni,
        "dettaglio_giornaliero_omesso_periodo_troppo_lungo": dettaglio_omesso,
        "nota": "Ogni giorno del periodo e' incluso anche con conteggio zero (non dedurre i giorni a zero: sono gia' calcolati qui in numero_giorni_a_zero/date_giorni_a_zero). giorno_con_valore_massimo elenca tutte le date in caso di parita'.",
    }


def _tool_eventi_calendario(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"))
    tipo = p.get("tipo")
    if tipo and tipo not in EVENT_TYPES:
        raise ToolInputError(f"tipo deve essere uno tra: {', '.join(EVENT_TYPES)}.")
    sede = (p.get("sede") or "").strip()
    operatore = p.get("operatore")
    where = ["date(start_at)>=date(?)", "date(start_at)<=date(?)", "(deleted_at IS NULL OR deleted_at='')"]
    args: list = [d_from, d_to]
    if tipo:
        where.append("event_type=?")
        args.append(tipo)
    if sede:
        where.append("zone=?")
        args.append(sede)
    if operatore:
        where.append("operator_name=?")
        args.append(operatore)
    sql = f"SELECT event_type,title,start_at,zone,operator_name,event_status,animal_name FROM calendar_events WHERE {' AND '.join(where)} ORDER BY start_at LIMIT 50"
    rows = c.execute(sql, args).fetchall()
    result = [{"tipo": r["event_type"], "titolo": r["title"], "quando": r["start_at"], "sede": r["zone"], "operatore": r["operator_name"], "stato": r["event_status"], "animale": r["animal_name"]} for r in rows]
    return {"eventi": result, "totale_nel_periodo": len(result), "troncato_a_50": len(result) == 50, "periodo_analizzato": label}


# ---------------------------------------------------------------------------
# Strumenti — pratiche
# ---------------------------------------------------------------------------

def _tool_conta_pratiche(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    stato = p.get("stato")
    if stato and stato not in DEPS.states:
        raise ToolInputError(f"stato deve essere uno tra: {', '.join(DEPS.states)}.")
    sede = p.get("sede")
    if sede and sede not in SHIFT_BRANCHES:
        raise ToolInputError(f"sede deve essere una tra: {', '.join(SHIFT_BRANCHES)}.")
    where = ["(deleted_at IS NULL OR deleted_at='')"]
    args: list = []
    if d_from:
        where.append("date(COALESCE(NULLIF(pickup_date,''),created_at))>=date(?)")
        args.append(d_from)
    if d_to:
        where.append("date(COALESCE(NULLIF(pickup_date,''),created_at))<=date(?)")
        args.append(d_to)
    if stato:
        where.append("status=?")
        args.append(stato)
    if sede:
        where.append("destination_branch=?")
        args.append(sede)
    cliente = (p.get("cliente_nome") or "").strip()
    if cliente:
        where.append("((owner_first_name||' '||owner_last_name) LIKE ? OR owner_company LIKE ?)")
        args += [f"%{cliente}%", f"%{cliente}%"]
    animale = (p.get("animale_nome") or "").strip()
    if animale:
        where.append("animal_name LIKE ?")
        args.append(f"%{animale}%")
    operatore = (p.get("operatore_nome") or "").strip()
    if operatore:
        where.append("operator_name=?")
        args.append(operatore)
    veterinario = (p.get("veterinario_nome") or "").strip()
    if veterinario:
        where.append("id IN (SELECT pr.id FROM practices pr JOIN veterinarians v ON v.id IN (pr.veterinarian_id,pr.origin_veterinarian_id,pr.owner_veterinarian_id) WHERE v.clinic_name LIKE ?)")
        args.append(f"%{veterinario}%")
    collaboratore = (p.get("collaboratore_nome") or "").strip()
    if collaboratore:
        where.append("collaborator_id IN (SELECT id FROM collaborators WHERE name LIKE ?)")
        args.append(f"%{collaboratore}%")
    sql = f"SELECT COUNT(*) n FROM practices WHERE {' AND '.join(where)}"
    n = c.execute(sql, args).fetchone()["n"]
    return {
        "conteggio": n,
        "periodo_analizzato": label or "tutto il periodo disponibile",
        "filtri": {"stato": stato, "sede": sede, "cliente_nome": cliente or None, "animale_nome": animale or None, "operatore_nome": operatore or None, "veterinario_nome": veterinario or None, "collaboratore_nome": collaboratore or None},
    }


def _tool_cerca_pratiche(c, user, now, p):
    q = (p.get("query") or "").strip()
    if not q:
        raise ToolInputError("Specifica un termine di ricerca (nome animale, proprietario o numero pratica).")
    like = f"%{q}%"
    sql = """SELECT id,practice_number,animal_name,owner_first_name,owner_last_name,status,destination_branch,pickup_date
             FROM practices WHERE (deleted_at IS NULL OR deleted_at='') AND
             (animal_name LIKE ? OR owner_first_name LIKE ? OR owner_last_name LIKE ? OR practice_number LIKE ? OR (owner_first_name||' '||owner_last_name) LIKE ?)
             ORDER BY created_at DESC LIMIT 20"""
    rows = c.execute(sql, (like, like, like, like, like)).fetchall()
    results = [
        {"id": r["id"], "numero_pratica": r["practice_number"], "animale": r["animal_name"], "proprietario": f'{r["owner_first_name"] or ""} {r["owner_last_name"] or ""}'.strip(), "stato": r["status"], "sede": r["destination_branch"], "data_ritiro": r["pickup_date"]}
        for r in rows
    ]
    return {"risultati": results, "totale_trovati": len(results), "troncato_a_20": len(results) == 20}


def _tool_dettaglio_pratica(c, user, now, p):
    pid = p.get("id")
    numero = (p.get("numero_pratica") or "").strip()
    row = None
    if pid:
        row = c.execute("SELECT * FROM practices WHERE id=? AND (deleted_at IS NULL OR deleted_at='')", (pid,)).fetchone()
    elif numero:
        row = c.execute("SELECT * FROM practices WHERE practice_number=? AND (deleted_at IS NULL OR deleted_at='')", (numero,)).fetchone()
    else:
        raise ToolInputError("Specifica id oppure numero_pratica (usa prima cerca_pratiche se non li conosci).")
    if not row:
        raise ToolInputError("Nessuna pratica trovata con questo id/numero. Usa prima cerca_pratiche per trovare l'id o il numero corretto.")
    canale = "D" if DEPS.money_value(row["total_text"]) > 0 else "W"
    total = DEPS.effective_total(row)
    paid = DEPS.channel_paid_amount(c, row["id"], canale)
    remaining = DEPS.channel_remaining(row)
    return {
        "id": row["id"], "numero_pratica": row["practice_number"], "stato": row["status"],
        "sede": row["destination_branch"], "animale": row["animal_name"], "specie": row["species"],
        "proprietario": f'{row["owner_first_name"] or ""} {row["owner_last_name"] or ""}'.strip(),
        "data_ritiro": row["pickup_date"], "creata_il": row["created_at"],
        "circuito_economico": canale, "totale": round(total, 2), "gia_incassato": round(paid, 2), "rimanenza": round(remaining, 2),
        "stato_pagamento": row["payment_status"], "note": row["notes"],
    }


def _tool_pratiche_saldo_aperto(c, user, now, p):
    as_of = p.get("alla_data")
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            raise ToolInputError("alla_data non e' una data valida (AAAA-MM-GG).")
    else:
        as_of = now.date().isoformat()
    sede = p.get("sede")
    if sede and sede not in SHIFT_BRANCHES:
        raise ToolInputError(f"sede deve essere una tra: {', '.join(SHIFT_BRANCHES)}.")
    filters = BalanceFilters(date_to=as_of, status="Da saldare")
    rows = get_outstanding_balances(c, filters=filters)
    if sede:
        ids = [r.practice_id for r in rows]
        branch_by_id = {}
        if ids:
            marks = ",".join("?" for _ in ids)
            branch_by_id = {r["id"]: r["destination_branch"] for r in c.execute(f"SELECT id,destination_branch FROM practices WHERE id IN ({marks})", ids)}
        rows = [r for r in rows if branch_by_id.get(r.practice_id) == sede]
    totale_cents = sum(r.remaining_cents for r in rows)
    esempi = [{"numero_pratica": r.practice_number, "animale": r.animal_name, "proprietario": r.owner_name, "rimanenza": round(r.remaining_cents / 100, 2)} for r in rows[:15]]
    return {"numero_pratiche_con_saldo_aperto": len(rows), "totale_rimanenza": round(totale_cents / 100, 2), "alla_data": as_of, "esempi": esempi, "troncato": len(rows) > 15}


# ---------------------------------------------------------------------------
# Strumenti — economia (ricavi per voce, incassi, W/D, urne, accessori, fatture)
# ---------------------------------------------------------------------------

def _tool_ricavi_per_voce(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    rows = DEPS.revenue_by_quote_category(c, d_from, d_to)
    voce = (p.get("voce") or "").strip()
    if voce:
        match = [(l, v) for l, v in rows if l.lower() == voce.lower()]
        if not match:
            raise ToolInputError(f"voce '{voce}' non riconosciuta. Voci disponibili: {', '.join(l for l, _ in rows)}.")
        rows = match
    return {"ricavi": [{"voce": l, "totale": round(v, 2)} for l, v in rows], "periodo_analizzato": label or "tutto il periodo disponibile"}


def _tool_riepilogo_incassi(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    categoria = p.get("categoria")
    if categoria and categoria not in ("W", "D", "Collaboratori"):
        raise ToolInputError("categoria deve essere una tra: W, D, Collaboratori.")
    sede = p.get("sede")
    if sede and sede not in SHIFT_BRANCHES:
        raise ToolInputError(f"sede deve essere una tra: {', '.join(SHIFT_BRANCHES)}.")
    filters = BalanceFilters(date_from=d_from, date_to=d_to, category=categoria)
    movements = get_movements(c, filters=filters)
    if sede:
        ids = {m.practice_id for m in movements if m.practice_id}
        branch_by_id = {}
        if ids:
            marks = ",".join("?" for _ in ids)
            branch_by_id = {r["id"]: r["destination_branch"] for r in c.execute(f"SELECT id,destination_branch FROM practices WHERE id IN ({marks})", list(ids))}
        movements = [m for m in movements if branch_by_id.get(m.practice_id) == sede]
    entrate = sum(m.amount_cents for m in movements if m.ledger_section == "Entrata")
    uscite = sum(m.amount_cents for m in movements if m.ledger_section == "Uscita")
    per_categoria: dict = {}
    for m in movements:
        if m.ledger_section == "Entrata":
            per_categoria[m.category] = per_categoria.get(m.category, 0) + m.amount_cents
    return {
        "incassato": round(entrate / 100, 2), "uscite": round(uscite / 100, 2), "netto": round((entrate - uscite) / 100, 2),
        "per_categoria": {k: round(v / 100, 2) for k, v in per_categoria.items()},
        "periodo_analizzato": label or "tutto il periodo disponibile",
        "filtri": {"categoria": categoria, "sede": sede},
    }


def _tool_vendite_urne(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    modello = (p.get("modello") or "").strip()
    where = ["pi.category='urna'", "(pr.deleted_at IS NULL OR pr.deleted_at='')"]
    args: list = []
    if d_from:
        where.append("date(COALESCE(NULLIF(pr.pickup_date,''),pr.created_at))>=date(?)")
        args.append(d_from)
    if d_to:
        where.append("date(COALESCE(NULLIF(pr.pickup_date,''),pr.created_at))<=date(?)")
        args.append(d_to)
    if modello:
        where.append("u.name LIKE ?")
        args.append(f"%{modello}%")
    sql = f"SELECT u.name modello, pi.price price FROM practice_items pi JOIN urns u ON u.id=pi.urn_catalog_id JOIN practices pr ON pr.id=pi.practice_id WHERE {' AND '.join(where)}"
    rows = c.execute(sql, args).fetchall()
    per_modello: dict = {}
    for r in rows:
        e = per_modello.setdefault(r["modello"], {"unita": 0, "ricavo": 0.0})
        e["unita"] += 1
        e["ricavo"] += DEPS.money_value(r["price"])
    result = [{"modello": k, "unita_vendute": v["unita"], "ricavo": round(v["ricavo"], 2)} for k, v in sorted(per_modello.items(), key=lambda kv: -kv[1]["unita"])]
    return {"vendite_per_modello": result, "totale_unita": sum(v["unita"] for v in per_modello.values()), "periodo_analizzato": label or "tutto il periodo disponibile"}


def _tool_vendite_accessori(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    categoria = p.get("categoria")
    if categoria and categoria not in ("calco", "accessorio"):
        raise ToolInputError("categoria deve essere una tra: calco, accessorio.")
    where = ["(pr.deleted_at IS NULL OR pr.deleted_at='')"]
    args: list = []
    if categoria:
        where.append("pi.category=?")
        args.append(categoria)
    else:
        where.append("pi.category IN ('calco','accessorio')")
    if d_from:
        where.append("date(COALESCE(NULLIF(pr.pickup_date,''),pr.created_at))>=date(?)")
        args.append(d_from)
    if d_to:
        where.append("date(COALESCE(NULLIF(pr.pickup_date,''),pr.created_at))<=date(?)")
        args.append(d_to)
    sql = f"SELECT pi.category cat, pi.label label, pi.price price FROM practice_items pi JOIN practices pr ON pr.id=pi.practice_id WHERE {' AND '.join(where)}"
    rows = c.execute(sql, args).fetchall()
    per: dict = {}
    for r in rows:
        key = (r["cat"], r["label"] or "(senza etichetta)")
        e = per.setdefault(key, {"unita": 0, "ricavo": 0.0})
        e["unita"] += 1
        e["ricavo"] += DEPS.money_value(r["price"])
    result = [{"categoria": k[0], "etichetta": k[1], "unita_vendute": v["unita"], "ricavo": round(v["ricavo"], 2)} for k, v in sorted(per.items(), key=lambda kv: -kv[1]["unita"])]
    return {"vendite": result, "totale_unita": sum(v["unita"] for v in per.values()), "periodo_analizzato": label or "tutto il periodo disponibile"}


def _tool_fatture(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    solo_condivise = bool(p.get("solo_condivise"))
    where = ["(deleted_at IS NULL OR deleted_at='')", "COALESCE(invoice_number,'')<>''"]
    args: list = []
    if d_from:
        where.append("date(invoice_date)>=date(?)")
        args.append(d_from)
    if d_to:
        where.append("date(invoice_date)<=date(?)")
        args.append(d_to)
    where_mi = ["(p.deleted_at IS NULL OR p.deleted_at='')", "COALESCE(mi.invoice_number,'')<>''"]
    args_mi: list = []
    if d_from:
        where_mi.append("date(mi.invoice_date)>=date(?)")
        args_mi.append(d_from)
    if d_to:
        where_mi.append("date(mi.invoice_date)<=date(?)")
        args_mi.append(d_to)
    rows = c.execute(f"SELECT * FROM practices WHERE {' AND '.join(where)}", args).fetchall()
    mrows = c.execute(
        f"SELECT mi.*,p.practice_number FROM movement_invoices mi JOIN practices p ON p.id=mi.practice_id WHERE {' AND '.join(where_mi)}",
        args_mi,
    ).fetchall()
    entries = []
    for row in rows:
        total = DEPS.money_value(row["invoice_total"]) if row["invoice_total"] else DEPS.money_value(row["total_service"])
        entries.append({"number": row["invoice_number"], "date": row["invoice_date"], "practice_number": row["practice_number"], "pid": row["id"], "total": total})
    for mrow in mrows:
        entries.append({"number": mrow["invoice_number"], "date": mrow["invoice_date"], "practice_number": mrow["practice_number"], "pid": mrow["practice_id"], "total": DEPS.money_value(mrow["invoice_total"])})
    groups: dict = {}
    for e in entries:
        key = (e["number"] or "").strip().lower()
        if key:
            groups.setdefault(key, []).append(e)
    shared = [g for g in groups.values() if len({x["pid"] for x in g}) > 1]
    result_entries = [e for g in shared for e in g] if solo_condivise else entries
    return {
        "numero_fatture_totali": len(entries),
        "numero_fatture_condivise_tra_piu_pratiche": len(shared),
        "totale_importo": round(sum(e["total"] for e in entries), 2),
        "esempi": [{"numero_fattura": e["number"], "pratica": e["practice_number"], "data": e["date"], "importo": round(e["total"], 2)} for e in result_entries[:15]],
        "periodo_analizzato": label or "tutto il periodo disponibile (per data fattura)",
    }


# ---------------------------------------------------------------------------
# Strumenti — orari / turni / ferie / reperibilita'
# ---------------------------------------------------------------------------

def _tool_turni(c, user, now, p):
    giorno = p.get("data") or now.date().isoformat()
    try:
        d = date.fromisoformat(giorno)
    except ValueError:
        raise ToolInputError("data non valida (AAAA-MM-GG).")
    sede = p.get("sede")
    if sede and sede not in SHIFT_BRANCHES:
        raise ToolInputError(f"sede deve essere una tra: {', '.join(SHIFT_BRANCHES)}.")
    by_day = shifts_for_range(c, d, d)
    day_data = by_day.get(d.isoformat(), {})
    result = []
    for branch, rows in day_data.items():
        if sede and branch != sede:
            continue
        for row in rows:
            result.append({"operatore": row["operator_name"], "sede": branch, "orario": "Tutto il giorno" if row["all_day"] else f'{row["start_time"]}–{row["end_time"]}'})
    return {"data": giorno, "turni": result, "totale": len(result)}


def _tool_ferie(c, user, now, p):
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"))
    operatore = (p.get("operatore") or "").strip()
    if operatore and operatore not in CALENDAR_OPERATORS:
        raise ToolInputError(f"operatore deve essere uno tra: {', '.join(CALENDAR_OPERATORS)}.")
    rows = vacations_overlapping(c, date.fromisoformat(d_from), date.fromisoformat(d_to))
    result = [{"operatore": r["operator_name"], "dal": r["start_date"], "al": r["end_date"]} for r in rows if not operatore or r["operator_name"] == operatore]
    return {"ferie": result, "totale": len(result), "periodo_analizzato": label}


def _tool_conta_turni(c, user, now, p):
    # Stessa fonte dati di turni_operatori (shifts_for_range, gia' usata
    # anche dalla pagina Orari/Turni per la vista mensile) - qui aggregata
    # su un intervallo invece che su un solo giorno, per rispondere a
    # domande come "quante volte Filippo e' stato assegnato a Empoli ad
    # agosto" che il solo turni_operatori (un giorno alla volta) non puo'
    # determinare senza sommare a mano, cosa che l'AI non deve mai fare da
    # sola con dati che non ha davvero interrogato.
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"))
    sede = p.get("sede")
    if sede and sede not in SHIFT_BRANCHES:
        raise ToolInputError(f"sede deve essere una tra: {', '.join(SHIFT_BRANCHES)}.")
    operatore = p.get("operatore")
    if operatore and operatore not in CALENDAR_OPERATORS:
        raise ToolInputError(f"operatore deve essere uno tra: {', '.join(CALENDAR_OPERATORS)}.")
    by_day = shifts_for_range(c, date.fromisoformat(d_from), date.fromisoformat(d_to))
    turni = []
    for day_iso in sorted(by_day):
        for branch, rows in by_day[day_iso].items():
            if sede and branch != sede:
                continue
            for row in rows:
                if operatore and row["operator_name"] != operatore:
                    continue
                turni.append({
                    "data": day_iso,
                    "operatore": row["operator_name"],
                    "sede": branch,
                    "orario": "Tutto il giorno" if row["all_day"] else f'{row["start_time"]}–{row["end_time"]}',
                })
    return {
        "totale_turni": len(turni),
        "periodo_analizzato": label,
        "filtri": {"sede": sede, "operatore": operatore},
        "turni": turni[:100],
        "troncato_a_100": len(turni) > 100,
        "nota": "Ogni riga e' un turno assegnato (un giorno con turno nella sede/operatore indicati conta 1). Non esiste una categoria esplicita 'mattina/pomeriggio/sera' nei dati: deducila dagli orari elencati, oppure chiedi all'utente quale fascia oraria intende, se non e' chiaro.",
    }


def _tool_reperibilita(c, user, now, p):
    giorno = p.get("data") or now.date().isoformat()
    try:
        d = date.fromisoformat(giorno)
    except ValueError:
        raise ToolInputError("data non valida (AAAA-MM-GG).")
    monday = week_monday(d)
    operatore, is_manual = oncall_operator_for_week(c, monday, list(DEPS.shift_operators))
    return {"settimana_dal": monday.isoformat(), "settimana_al": (monday + timedelta(days=6)).isoformat(), "operatore_reperibile": operatore, "assegnazione_manuale": bool(is_manual)}


# ---------------------------------------------------------------------------
# Strumenti — clienti / veterinari / collaboratori
# ---------------------------------------------------------------------------

def _tool_cerca_cliente(c, user, now, p):
    q = (p.get("query") or "").strip()
    if not q:
        raise ToolInputError("Specifica un nome, telefono o email da cercare.")
    like = f"%{q}%"
    rows = c.execute(
        """SELECT id,first_name,last_name,company_name,phone,email FROM clients WHERE active=1 AND
           (first_name LIKE ? OR last_name LIKE ? OR (first_name||' '||last_name) LIKE ? OR company_name LIKE ? OR phone LIKE ? OR email LIKE ?)
           LIMIT 20""",
        (like, like, like, like, like, like),
    ).fetchall()
    result = []
    for r in rows:
        cnt = c.execute("SELECT COUNT(*) n FROM practices WHERE client_id=? AND (deleted_at IS NULL OR deleted_at='')", (r["id"],)).fetchone()["n"]
        result.append({"id": r["id"], "nome": (f'{r["first_name"] or ""} {r["last_name"] or ""}'.strip() or r["company_name"]), "telefono": r["phone"], "email": r["email"], "numero_pratiche": cnt})
    return {"clienti": result, "totale_trovati": len(result)}


_VET_ROLE_COLUMNS = {"servizio": "veterinarian_id", "origine": "origin_veterinarian_id", "proprietario": "owner_veterinarian_id"}


_RANKING_LIMIT = 15


def _tool_pratiche_veterinario(c, user, now, p):
    nome = (p.get("veterinario_nome") or "").strip()
    ruolo = p.get("ruolo") or "servizio"
    col = _VET_ROLE_COLUMNS.get(ruolo)
    if not col:
        raise ToolInputError(f"ruolo deve essere uno tra: {', '.join(_VET_ROLE_COLUMNS)}.")
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    sql = f"SELECT v.id,v.clinic_name,COUNT(*) n FROM practices pr JOIN veterinarians v ON v.id=pr.{col} WHERE (pr.deleted_at IS NULL OR pr.deleted_at='')"
    args: list = []
    if nome:
        sql += " AND v.clinic_name LIKE ?"
        args.append(f"%{nome}%")
    if d_from:
        sql += " AND date(COALESCE(NULLIF(pr.pickup_date,''),pr.created_at))>=date(?)"
        args.append(d_from)
    if d_to:
        sql += " AND date(COALESCE(NULLIF(pr.pickup_date,''),pr.created_at))<=date(?)"
        args.append(d_to)
    sql += " GROUP BY v.id ORDER BY n DESC"
    if not nome:
        sql += f" LIMIT {_RANKING_LIMIT}"
    rows = c.execute(sql, args).fetchall()
    result = [{"veterinario": r["clinic_name"], "numero_pratiche": r["n"]} for r in rows]
    return {
        "risultati": result, "ruolo": ruolo, "periodo_analizzato": label or "tutto il periodo disponibile",
        "nota": f"Nessun nome specificato: classifica dei primi {_RANKING_LIMIT} veterinari per numero di pratiche (ordine gia' decrescente, il primo e' il piu' presente)." if not nome else None,
    }


def _tool_pratiche_collaboratore(c, user, now, p):
    nome = (p.get("collaboratore_nome") or "").strip()
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    sql = "SELECT co.id,co.name,COUNT(*) n FROM practices pr JOIN collaborators co ON co.id=pr.collaborator_id WHERE (pr.deleted_at IS NULL OR pr.deleted_at='')"
    args: list = []
    if nome:
        sql += " AND co.name LIKE ?"
        args.append(f"%{nome}%")
    if d_from:
        sql += " AND date(COALESCE(NULLIF(pr.pickup_date,''),pr.created_at))>=date(?)"
        args.append(d_from)
    if d_to:
        sql += " AND date(COALESCE(NULLIF(pr.pickup_date,''),pr.created_at))<=date(?)"
        args.append(d_to)
    sql += " GROUP BY co.id ORDER BY n DESC"
    if not nome:
        sql += f" LIMIT {_RANKING_LIMIT}"
    rows = c.execute(sql, args).fetchall()
    result = [{"collaboratore": r["name"], "numero_pratiche": r["n"]} for r in rows]
    return {
        "risultati": result, "periodo_analizzato": label or "tutto il periodo disponibile",
        "nota": f"Nessun nome specificato: classifica dei primi {_RANKING_LIMIT} collaboratori per numero di pratiche (ordine gia' decrescente, il primo e' il piu' presente)." if not nome else None,
    }


def _tool_buoni_veterinari(c, user, now, p):
    veterinario = (p.get("veterinario_nome") or "").strip()
    stato = p.get("stato")
    if stato and stato not in ("Maturato", "Usato"):
        raise ToolInputError("stato deve essere uno tra: Maturato, Usato.")
    d_from, d_to, label = resolve_period(now, p.get("periodo"), p.get("data_da"), p.get("data_a"), optional=True)
    where = []
    args: list = []
    if veterinario:
        where.append("v.clinic_name LIKE ?")
        args.append(f"%{veterinario}%")
    if stato:
        where.append("vv.status=?")
        args.append(stato)
    date_col = "vv.used_at" if stato == "Usato" else "vv.created_at"
    if d_from:
        where.append(f"date({date_col})>=date(?)")
        args.append(d_from)
    if d_to:
        where.append(f"date({date_col})<=date(?)")
        args.append(d_to)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    sql = f"SELECT v.clinic_name vet, vv.status stato, COUNT(*) n FROM veterinarian_vouchers vv JOIN veterinarians v ON v.id=vv.veterinarian_id{where_sql} GROUP BY v.id, vv.status ORDER BY n DESC"
    rows = c.execute(sql, args).fetchall()
    result = [{"veterinario": r["vet"], "stato": r["stato"], "numero_buoni": r["n"]} for r in rows]
    return {
        "risultati": result, "periodo_analizzato": label or "tutto il periodo disponibile",
        "filtri": {"veterinario_nome": veterinario or None, "stato": stato},
        "nota": "Maturato = buono accreditato e non ancora usato (il filtro periodo si applica alla data di maturazione); Usato = buono gia' utilizzato su una pratica (il filtro periodo, se indicato con stato=Usato, si applica alla data d'uso). Se non specifichi stato, il periodo si applica alla data di maturazione per entrambi gli stati.",
    }


# ---------------------------------------------------------------------------
# Registro strumenti
# ---------------------------------------------------------------------------

TOOL_SPECS = [
    {
        "name": "conta_cremazioni",
        "description": "Conta i cicli di cremazione nel periodo indicato, con filtri opzionali per stato (pianificato/in_attesa/completato) e sede (Livorno/Empoli).",
        "input_schema": _schema({**_PERIOD_PROPS, "stato": {"type": "string", "enum": ["pianificato", "in_attesa", "completato"]}, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}}),
        "handler": _tool_conta_cremazioni,
    },
    {
        "name": "conta_ritiri",
        "description": "Conta i RITIRI EFFETTUATI (pratiche il cui animale e' stato realmente ritirato) nel periodo indicato, con filtri opzionali per sede e operatore. Usa lo stesso identico criterio della card 'Ritirati' della dashboard del gestionale (stato pratica che ha raggiunto o superato 'Ritirato'), non gli eventi di calendario programmati/annullati. Non supporta la distinzione tra ritiro a domicilio e in sede (non tracciata su una pratica conclusa).",
        "input_schema": _schema({**_PERIOD_PROPS, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}, "operatore": {"type": "string", "enum": list(CALENDAR_OPERATORS)}}),
        "handler": _tool_conta_ritiri,
    },
    {
        "name": "conta_riconsegne",
        "description": "Conta le RICONSEGNE EFFETTUATE (pratiche attualmente nello stato 'Consegnato') nel periodo indicato, con filtri opzionali per sede e operatore. Usa lo stesso identico criterio della card 'Consegnato' della dashboard del gestionale, non gli eventi di calendario programmati/annullati. Non supporta la distinzione tra riconsegna a domicilio e in sede (non tracciata su una pratica conclusa).",
        "input_schema": _schema({**_PERIOD_PROPS, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}, "operatore": {"type": "string", "enum": list(CALENDAR_OPERATORS)}}),
        "handler": _tool_conta_riconsegne,
    },
    {
        "name": "eventi_calendario",
        "description": "Elenca (fino a 50) gli eventi di calendario nel periodo indicato — cosa e' PROGRAMMATO (inclusi non ancora confermati/annullati) in un giorno/periodo — con filtri opzionali per tipo evento, sede/zona (zona di ritiro/riconsegna, non la sede pratica) e operatore. Per un CONTEGGIO di ritiri o riconsegne realmente effettuati usa invece conta_ritiri/conta_riconsegne, non questo strumento.",
        "input_schema": _schema({**_PERIOD_PROPS, "tipo": {"type": "string", "enum": list(EVENT_TYPES)}, "sede": {"type": "string"}, "operatore": {"type": "string", "enum": list(CALENDAR_OPERATORS)}}),
        "handler": _tool_eventi_calendario,
    },
    {
        "name": "conta_pratiche",
        "description": "Conta le pratiche con i filtri indicati (periodo opzionale, stato, sede, cliente, animale, operatore che ha gestito la pratica, veterinario, collaboratore). Se non specifichi un periodo conta su tutto lo storico.",
        "input_schema": _schema({**_PERIOD_PROPS, "stato": {"type": "string"}, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}, "cliente_nome": {"type": "string"}, "animale_nome": {"type": "string"}, "operatore_nome": {"type": "string"}, "veterinario_nome": {"type": "string"}, "collaboratore_nome": {"type": "string"}}),
        "handler": _tool_conta_pratiche,
    },
    {
        "name": "cerca_pratiche",
        "description": "Cerca pratiche per nome animale, nome proprietario o numero pratica (ricerca libera, fino a 20 risultati). Usalo per rispondere a domande come 'dov'e' la pratica di Mario Rossi'.",
        "input_schema": _schema({"query": {"type": "string"}}, ["query"]),
        "handler": _tool_cerca_pratiche,
    },
    {
        "name": "dettaglio_pratica",
        "description": "Restituisce il dettaglio completo di UNA pratica (stato, sede, animale, proprietario, totale, gia' incassato, rimanenza, circuito economico W/D) dato l'id o il numero pratica. Se non conosci l'id/numero, usa prima cerca_pratiche.",
        "input_schema": _schema({"id": {"type": "integer"}, "numero_pratica": {"type": "string"}}),
        "handler": _tool_dettaglio_pratica,
    },
    {
        "name": "pratiche_saldo_aperto",
        "description": "Elenca le pratiche con un saldo/rimanenza ancora aperta a una certa data (default oggi), con conteggio e totale rimanenza. Filtro opzionale per sede.",
        "input_schema": _schema({"alla_data": {"type": "string", "description": "Data ISO AAAA-MM-GG, default oggi."}, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}}),
        "handler": _tool_pratiche_saldo_aperto,
    },
    {
        "name": "ricavi_per_voce_preventivo",
        "description": "Ricavi aggregati per voce del preventivo (Cremazione, Ritiro, Riconsegna, Serale, Notturno, Festivo, Urne, Calchi, Accessori) nel periodo indicato. Base preventivo/fatturazione, non incassi di cassa. Filtro opzionale su una singola voce.",
        "input_schema": _schema({**_PERIOD_PROPS, "voce": {"type": "string"}}),
        "handler": _tool_ricavi_per_voce,
    },
    {
        "name": "riepilogo_incassi",
        "description": "Riepilogo dei movimenti di cassa reali (ledger Bilanci) nel periodo indicato: incassato, uscite, netto, e ripartizione per categoria economica W/D/Collaboratori. Filtri opzionali per categoria e sede.",
        "input_schema": _schema({**_PERIOD_PROPS, "categoria": {"type": "string", "enum": ["W", "D", "Collaboratori"]}, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}}),
        "handler": _tool_riepilogo_incassi,
    },
    {
        "name": "vendite_urne",
        "description": "Unita' vendute e ricavo per modello di urna nel periodo indicato. Filtro opzionale per modello (ricerca parziale).",
        "input_schema": _schema({**_PERIOD_PROPS, "modello": {"type": "string"}}),
        "handler": _tool_vendite_urne,
    },
    {
        "name": "vendite_accessori",
        "description": "Unita' vendute e ricavo per accessorio/calco nel periodo indicato. Filtro opzionale per categoria (calco o accessorio).",
        "input_schema": _schema({**_PERIOD_PROPS, "categoria": {"type": "string", "enum": ["calco", "accessorio"]}}),
        "handler": _tool_vendite_accessori,
    },
    {
        "name": "fatture",
        "description": "Numero e totale delle fatture emesse nel periodo indicato (per data fattura), incluse quelle condivise tra piu' pratiche. Filtro opzionale per mostrare solo quelle condivise.",
        "input_schema": _schema({**_PERIOD_PROPS, "solo_condivise": {"type": "boolean"}}),
        "handler": _tool_fatture,
    },
    {
        "name": "turni_operatori",
        "description": "Chi lavora (e con quale orario/sede) in un dato giorno (default oggi). Filtro opzionale per sede. Per un CONTEGGIO su un periodo (es. 'quante volte in un mese') usa invece conta_turni.",
        "input_schema": _schema({"data": {"type": "string", "description": "Data ISO AAAA-MM-GG, default oggi."}, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}}),
        "handler": _tool_turni,
    },
    {
        "name": "conta_turni",
        "description": "Conta quante volte un operatore e' stato assegnato in turno (Orari/Turni) in un periodo, con filtri opzionali per sede e operatore; restituisce anche l'elenco dei singoli turni trovati (data, sede, orario) fino a 100. Usa questo per domande come 'quante volte X e' stato a Empoli ad agosto' - turni_operatori risponde invece solo per UN giorno alla volta.",
        "input_schema": _schema({**_PERIOD_PROPS, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}, "operatore": {"type": "string", "enum": list(CALENDAR_OPERATORS)}}),
        "handler": _tool_conta_turni,
    },
    {
        "name": "ferie_operatori",
        "description": "Chi e' in ferie nel periodo indicato. Filtro opzionale per operatore.",
        "input_schema": _schema({**_PERIOD_PROPS, "operatore": {"type": "string", "enum": list(CALENDAR_OPERATORS)}}),
        "handler": _tool_ferie,
    },
    {
        "name": "reperibilita_settimana",
        "description": "Chi e' l'operatore reperibile (turno notturno) nella settimana che contiene la data indicata (default oggi).",
        "input_schema": _schema({"data": {"type": "string", "description": "Data ISO AAAA-MM-GG, default oggi."}}),
        "handler": _tool_reperibilita,
    },
    {
        "name": "cerca_cliente",
        "description": "Cerca un cliente per nome, telefono o email; restituisce anche il numero di pratiche collegate.",
        "input_schema": _schema({"query": {"type": "string"}}, ["query"]),
        "handler": _tool_cerca_cliente,
    },
    {
        "name": "pratiche_per_veterinario",
        "description": "Numero di pratiche collegate a un veterinario/clinica, opzionalmente filtrato per periodo e per ruolo del veterinario nella pratica (servizio di cremazione, provenienza, o proprietario abituale). Se specifichi veterinario_nome (ricerca parziale) restituisce il conteggio per quel veterinario; se lo OMETTI restituisce la CLASSIFICA dei veterinari piu' presenti per quel ruolo - usa questa modalita' per domande come 'qual e' il veterinario piu' presente come luogo di origine'.",
        "input_schema": _schema({**_PERIOD_PROPS, "veterinario_nome": {"type": "string"}, "ruolo": {"type": "string", "enum": ["servizio", "origine", "proprietario"], "description": "Default 'servizio'."}}),
        "handler": _tool_pratiche_veterinario,
    },
    {
        "name": "pratiche_per_collaboratore",
        "description": "Numero di pratiche collegate a un collaboratore/partner commerciale, opzionalmente filtrato per periodo. Se specifichi collaboratore_nome (ricerca parziale) restituisce il conteggio per quel collaboratore; se lo OMETTI restituisce la CLASSIFICA dei collaboratori piu' presenti - usa questa modalita' per domande come 'qual e' il collaboratore con piu' pratiche'.",
        "input_schema": _schema({**_PERIOD_PROPS, "collaboratore_nome": {"type": "string"}}),
        "handler": _tool_pratiche_collaboratore,
    },
    {
        "name": "buoni_veterinari",
        "description": "Conta/elenca i buoni veterinario (accreditati per invio pratiche) per veterinario/clinica e periodo, con filtro opzionale per stato (Maturato = accreditato e non ancora usato, Usato = gia' utilizzato su una pratica). Usa questo per domande come 'quanti buoni ha maturato il veterinario X ad agosto' o 'quanti buoni sono stati usati questo mese'.",
        "input_schema": _schema({**_PERIOD_PROPS, "veterinario_nome": {"type": "string"}, "stato": {"type": "string", "enum": ["Maturato", "Usato"]}}),
        "handler": _tool_buoni_veterinari,
    },
    {
        "name": "andamento_giornaliero",
        "description": "Andamento giorno per giorno dei ritiri o delle riconsegne effettuati in un periodo (stessa fonte autorevole di conta_ritiri/conta_riconsegne: stato reale della pratica, non gli eventi di calendario), con ogni giorno del periodo incluso anche quando il conteggio e' zero. Usa questo per 'qual e' stato il giorno con piu' ritiri', 'ci sono stati giorni senza ritiri in una sede', andamenti e picchi nel tempo. Filtri opzionali per sede e operatore. Periodo massimo un anno.",
        "input_schema": _schema({**_PERIOD_PROPS, "metrica": {"type": "string", "enum": list(_FASE_CONFIG)}, "sede": {"type": "string", "enum": list(SHIFT_BRANCHES)}, "operatore": {"type": "string", "enum": list(CALENDAR_OPERATORS)}}, ["metrica"]),
        "handler": _tool_andamento_giornaliero,
    },
]

_HANDLERS_BY_NAME = {t["name"]: t["handler"] for t in TOOL_SPECS}


# ---------------------------------------------------------------------------
# Orchestrazione LLM (Anthropic Messages API, tool use)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """Sei l'Assistente AI interno del gestionale PetParadise Manager (V1), una software house per la gestione di un'azienda di cremazione animali.

OGGI e' {oggi} (fuso orario Europe/Rome, stesso fuso di tutti i dati del gestionale). Usa sempre questa data come riferimento: se una domanda nomina un mese per nome (es. "agosto", "settembre") senza indicare l'anno, calcola tu l'intervallo esatto di quel mese nell'anno corretto rispetto a oggi e passalo come periodo='intervallo_personalizzato' con data_da/data_a in formato AAAA-MM-GG — non chiedere mai all'utente l'anno per un mese ovvio dal contesto.

REGOLA ASSOLUTA: non devi MAI inventare, stimare o dedurre statistiche, numeri, pratiche, eventi, ricavi o qualsiasi altro dato del gestionale. Ogni informazione numerica o fattuale su pratiche, cremazioni, ritiri, riconsegne, calendario, turni, ferie, reperibilita', urne, accessori, preventivi, incassi, fatture, clienti, veterinari o collaboratori DEVE provenire da una chiamata a uno degli strumenti disponibili in questa conversazione. Non hai nessuna conoscenza propria dei dati reali di questa azienda: l'unica fonte di verita' sono i risultati degli strumenti. Se una domanda successiva fa riferimento a un dato gia' ottenuto in QUESTA conversazione puoi riusarlo senza richiamare di nuovo lo stesso strumento con gli stessi parametri, ma non aggiungere mai un numero che non sia mai stato restituito da uno strumento in questa conversazione.

Se uno strumento non puo' determinare il dato richiesto (errore di parametro, dominio non coperto, nessun dato disponibile) dillo chiaramente all'utente, ad esempio: "Non posso determinare questo dato dai dati attualmente disponibili." Non proporre mai una stima plausibile al suo posto.

Se la domanda e' ambigua (non e' chiaro a quale dominio si riferisce, es. cremazioni/ritiri/riconsegne, oppure manca un'informazione necessaria per scegliere i parametri corretti) chiedi un chiarimento invece di scegliere arbitrariamente.

Quando rispondi con un dato ottenuto da uno strumento, sii conciso ma specifica il perimetro del dato (periodo, sede o altri filtri applicati) cosi' l'utente capisce su cosa si basa il risultato. Rispondi sempre in italiano, in modo diretto e senza dettagli tecnici (mai SQL, mai nomi di tabelle).

Puoi chiamare piu' strumenti, anche piu' volte con filtri diversi, per rispondere a domande di confronto (es. confrontare due sedi chiamando lo stesso strumento due volte con sede diversa) o che richiedono piu' fonti."""

_WEEKDAY_NAMES_IT = ("lunedi'", "martedi'", "mercoledi'", "giovedi'", "venerdi'", "sabato", "domenica")


def _system_prompt(now):
    oggi = f"{_WEEKDAY_NAMES_IT[now.weekday()]} {now.day} {DEPS.month_names_it[now.month - 1]} {now.year}"
    return _SYSTEM_PROMPT_TEMPLATE.format(oggi=oggi)


MAX_TOOL_ROUNDS = 6
DEFAULT_MODEL = "claude-sonnet-5"


# Il default della libreria (httpx2 DEFAULT_TIMEOUT: connect=5.0s, resto
# 10 minuti) ha un timeout di CONNESSIONE molto stretto: su un'istanza
# Render "starter" la prima connessione TCP+TLS verso api.anthropic.com
# puo' richiedere piu' di 5s (nessun pool gia' caldo, dato che _client()
# viene chiamata una volta per richiesta), facendo scattare un timeout che
# la libreria segnala come APITimeoutError (sottoclasse di
# APIConnectionError) - visibile all'utente come "impossibile raggiungere
# l'API" anche quando la connessione avrebbe funzionato con qualche
# secondo in piu'. 20s di connect e 60s complessivi restano ragionevoli
# (stesso ordine di grandezza gia' usato per la chiamata esterna WhatsApp,
# timeout=18, vedi send_whatsapp_message) senza nascondere un errore reale
# dietro un'attesa indefinita.
_ANTHROPIC_TIMEOUT_SECONDS = 60.0
_ANTHROPIC_CONNECT_TIMEOUT_SECONDS = 20.0

# Causa reale osservata in produzione: ANTHROPIC_API_KEY configurata su
# Render conteneva un intero comando curl incollato per errore ("curl
# https://api.anthropic.com/v1/messages --header \"x-api-key: ...\" ..."),
# non la sola chiave. httpx2 rifiutava quel valore come header HTTP
# ("LocalProtocolError: Illegal header value") — un errore che veniva
# classificato genericamente come "errore di rete", nascondendo la vera
# causa (configurazione). Il controllo qui e' volutamente generico (nessun
# formato/prefisso specifico di Anthropic, che potrebbe cambiare): una
# vera chiave API e' un singolo token senza spazi ne' caratteri di
# controllo, esattamente il vincolo che un header HTTP impone comunque —
# qualunque comando curl, blocco di header o JSON incollato per errore
# contiene spazi/newline e viene intercettato da questo solo controllo.
# Le sottostringhe elencate sotto sono un secondo livello diagnostico per
# i casi (rari) di un valore incollato per errore ma senza spazi (es. solo
# "x-api-key:sk-...", o solo l'URL). Nessun tentativo di "ripararlo" o
# estrarre una chiave dalla stringa: un valore malformato viene sempre
# rifiutato, mai corretto in automatico.
_API_KEY_ILLEGAL_CHARS_RE = re.compile(r"[\s\x00-\x1f\x7f]")
_API_KEY_SUSPICIOUS_SUBSTRINGS = (
    "curl", "https://api.anthropic.com", "--header", "x-api-key:",
    "anthropic-version", "authorization:", "bearer ",
)
_INVALID_API_KEY_MESSAGE = (
    "Configurazione Anthropic non valida: ANTHROPIC_API_KEY contiene un valore non valido "
    "(sembra una configurazione o un comando incollato per errore, non la sola chiave API). "
    "Verificare la Environment Variable del servizio su Render."
)


def _validate_api_key(value):
    """Solleva AssistantConfigError (messaggio sempre sicuro, mai il
    valore) se `value` non puo' essere una vera chiave API — mai un
    tentativo di ripararla."""
    if _API_KEY_ILLEGAL_CHARS_RE.search(value):
        raise AssistantConfigError(_INVALID_API_KEY_MESSAGE)
    lowered = value.lower()
    if any(marker in lowered for marker in _API_KEY_SUSPICIOUS_SUBSTRINGS):
        raise AssistantConfigError(_INVALID_API_KEY_MESSAGE)


def get_configured_api_key():
    """Legge e valida ANTHROPIC_API_KEY dall'ambiente — unico punto in cui
    questo avviene (usato sia dall'endpoint per un fallimento rapido, sia
    da _client()), cosi' le due verifiche (assente / malformata) non
    possono disallinearsi. Ritorna la chiave valida, o solleva
    AssistantConfigError con un messaggio diagnostico sicuro."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise AssistantConfigError("ANTHROPIC_API_KEY non configurata sul server.")
    _validate_api_key(api_key)
    return api_key


def _client():
    api_key = get_configured_api_key()
    import anthropic
    return anthropic.Anthropic(
        api_key=api_key,
        timeout=anthropic.Timeout(_ANTHROPIC_TIMEOUT_SECONDS, connect=_ANTHROPIC_CONNECT_TIMEOUT_SECONDS),
    )


def _anthropic_tools():
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in TOOL_SPECS]


_TIMEOUT_PHASE_IT = {
    "ConnectTimeout": "durante l'apertura della connessione",
    "ReadTimeout": "in attesa della risposta del modello",
    "WriteTimeout": "durante l'invio della richiesta",
    "PoolTimeout": "in attesa di una connessione disponibile nel pool",
}

# LocalProtocolError (h11/httpx2, sollevata per un header HTTP illegale -
# es. contenente spazi/newline, il caso reale osservato con
# ANTHROPIC_API_KEY malformata) e RemoteProtocolError includono nel
# proprio messaggio il valore illegale stesso (verificato: str(exc) di
# LocalProtocolError contiene il byte-string completo dell'header
# rifiutato). Per queste, il messaggio verso utente/log non deve MAI
# includere str(cause)/str(exc) — solo il tipo.
_UNSAFE_TO_ECHO_CAUSE_TYPES = {"LocalProtocolError", "RemoteProtocolError"}


def _describe_anthropic_error(exc):
    """Messaggio diagnostico sicuro (mai la chiave, mai header di richiesta)
    a partire da un'eccezione sollevata da client.messages.create — cosi'
    un errore reale di Anthropic (autenticazione, modello, rate limit,
    richiesta non valida, timeout, connessione rifiutata...) arriva
    all'utente/ai log in modo specifico invece di sparire dietro un
    generico "errore tecnico" o un generico "errore di rete" che confonde
    un timeout con un'impossibilita' reale di connettersi.

    L'eccezione originale di httpx2 (ConnectTimeout/ReadTimeout/
    ConnectError/...) e' sempre disponibile su exc.__cause__ - la SDK la
    imposta esplicitamente con "raise ... from err" - ed e' cio' che
    permette di distinguere le due situazioni senza accesso ai log del
    processo di produzione.

    ATTENZIONE SICUREZZA: alcune eccezioni di basso livello di httpx2/h11
    (in particolare LocalProtocolError, sollevata per un header HTTP
    illegale — es. contenente spazi/newline, esattamente il caso reale
    osservato con una ANTHROPIC_API_KEY malformata) includono nel proprio
    messaggio il VALORE ILLEGALE STESSO (confermato: str(LocalProtocolError)
    contiene il byte-string completo dell'header rifiutato). Per queste
    eccezioni non bisogna MAI includere str(cause)/str(exc) nel messaggio
    restituito: la validazione di get_configured_api_key() dovrebbe gia'
    impedire che una chiave malformata arrivi fin qui, ma questo resta un
    secondo livello di difesa nel caso arrivi comunque (es. per un motivo
    diverso dalla chiave)."""
    import anthropic
    if isinstance(exc, anthropic.APIStatusError):
        detail = getattr(exc, "message", None) or str(exc)
        return f"Anthropic ha rifiutato la richiesta (HTTP {exc.status_code}): {detail}"
    cause = exc.__cause__
    cause_name = type(cause).__name__ if cause is not None else None
    if isinstance(exc, anthropic.APITimeoutError):
        fase = _TIMEOUT_PHASE_IT.get(cause_name, "")
        return f"Timeout nella chiamata ad Anthropic{(' ' + fase) if fase else ''} (tipo: {cause_name or 'timeout'})."
    if isinstance(exc, anthropic.APIConnectionError):
        if cause_name in _UNSAFE_TO_ECHO_CAUSE_TYPES:
            return (
                "Errore di configurazione nella richiesta ad Anthropic (un header HTTP non e' valido). "
                "Verificare che ANTHROPIC_API_KEY contenga esclusivamente la chiave API, senza spazi "
                "ne' altri caratteri, nella Environment Variable del servizio su Render."
            )
        return f"Impossibile stabilire la connessione con l'API di Anthropic (tipo: {cause_name or type(exc).__name__}): {cause or exc}."
    return f"Errore imprevisto nella chiamata al modello ({type(exc).__name__}: {exc})."


def run_chat(db_factory, user, now, history, message, log=None):
    """db_factory: funzione che apre una connessione al database come
    context manager (es. app.db) — non una connessione gia' aperta.
    Ogni strumento apre/chiude la PROPRIA connessione di breve durata
    subito prima/dopo la query reale: nessuna connessione resta aperta
    durante le chiamate di rete a Anthropic (che possono richiedere piu'
    round-trip successivi). Questo db non e' in modalita' WAL: tenere una
    connessione aperta per l'intera conversazione bloccherebbe ogni altra
    richiesta dell'app per tutta la sua durata — stesso bug reale gia'
    risolto per l'invio WhatsApp (vedi send_whatsapp_message), qui evitato
    fin dall'inizio.

    history: lista di messaggi gia' nel formato Anthropic (role/content),
    cosi' come restituiti da una chiamata precedente — mantenuta lato
    client, MAI persistita lato server (vedi nota privacy nell'endpoint).
    Ritorna (testo_risposta, nuova_history)."""
    if DEPS is None:
        raise AssistantConfigError("Assistente AI non inizializzato (configure() non chiamato).")
    client = _client()
    messages = list(history) + [{"role": "user", "content": message}]
    tool_defs = _anthropic_tools()
    model = os.environ.get("AI_ASSISTANT_MODEL", DEFAULT_MODEL)
    system_prompt = _system_prompt(now)
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.messages.create(model=model, max_tokens=1024, system=system_prompt, tools=tool_defs, messages=messages)
        except Exception as exc:
            description = _describe_anthropic_error(exc)
            if log:
                log("ai_assistant_api_error", "messages.create", {}, description)
            raise AssistantAPIError(description) from exc
        content = [block.model_dump() for block in response.content]
        messages.append({"role": "assistant", "content": content})
        if response.stop_reason != "tool_use":
            text = "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
            return text or "Non ho una risposta da darti.", messages
        tool_results = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            name = block.get("name")
            handler = _HANDLERS_BY_NAME.get(name)
            params = block.get("input") or {}
            if log:
                log("ai_assistant_tool_call", name, params)
            if not handler:
                tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": "Strumento sconosciuto.", "is_error": True})
                continue
            try:
                with db_factory() as c:
                    result = handler(c, user, now, params)
                tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": json.dumps(result, ensure_ascii=False)})
            except ToolInputError as exc:
                tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": str(exc), "is_error": True})
            except Exception as exc:  # noqa: BLE001 — mai far crashare la chat per un errore di uno strumento
                if log:
                    log("ai_assistant_tool_error", name, params, repr(exc))
                tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": "Errore tecnico nell'esecuzione dello strumento.", "is_error": True})
        messages.append({"role": "user", "content": tool_results})
    return "Non sono riuscito a completare la richiesta in un numero ragionevole di passaggi. Prova a riformulare la domanda in modo piu' specifico.", messages
