"""Preventivo indicativo per il Portale Veterinari: cremazione singola + ritiro/riconsegna.

Modulo puro (nessun import da app.py): le regole di calcolo ricevono il
listino come dizionario, cosi' sono testabili da sole. Il listino vive nella
tabella ``settings`` (una riga JSON) ed e' modificabile da una pagina del
gestionale; se manca o e' danneggiato si usa quello di default qui sotto, che
riproduce il listino cartaceo (prezzi IVA inclusa).
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SETTINGS_KEY = "partner_quote_pricelist"
MAX_WEIGHT_KG = Decimal("300")
OTHER_COMUNE = "__altro__"
SAME_COMUNE = "__stesso__"

# chiave del form -> (etichetta, riga di tariffa nel listino ritiri)
PICKUP_PLACES = {
    "ambulatorio": ("Ambulatorio / clinica", "base"),
    "terra": ("Domicilio, piano terra", "base"),
    "ascensore": ("Domicilio, piano con ascensore", "base"),
    "piano12": ("Domicilio, 1° o 2° piano senza ascensore", "piano12"),
    "piano3": ("Domicilio, 3° piano o oltre senza ascensore", "piano3"),
}
PICKUP_TARIFFS = ("base", "piano12", "piano3")
PICKUP_TARIFF_LABELS = {
    "base": "Ambulatorio, piano terra o piano con ascensore",
    "piano12": "Primo o secondo piano",
    "piano3": "Terzo piano e oltre",
}

DEFAULT_PRICELIST = {
    "cremation": [
        {"max_kg": 5, "price": 210}, {"max_kg": 10, "price": 250}, {"max_kg": 15, "price": 270},
        {"max_kg": 20, "price": 290}, {"max_kg": 25, "price": 310}, {"max_kg": 30, "price": 330},
        {"max_kg": 35, "price": 350}, {"max_kg": 40, "price": 380}, {"max_kg": 45, "price": 400},
        {"max_kg": 50, "price": 450}, {"max_kg": 60, "price": 520}, {"max_kg": 70, "price": 570},
        {"max_kg": None, "price": 620},
    ],
    "pickup": {
        "bands": [10, 30, 45],
        "prices": {"base": [50, 60, 70, 80], "piano12": [60, 70, 80, 90], "piano3": [70, 80, 90, 100]},
        "outside_surcharge": 10,
    },
    "delivery": {"inside": 40, "outside": 50},
    "supplements": {"festivo": 80, "serale": 50, "notturno": 120},
    "urn": {"from_price": 20},
    "circondari": [
        {"name": "Livorno", "comuni": ["Livorno"]},
        {"name": "Circondario Empolese Valdelsa", "comuni": [
            "Capraia e Limite", "Castelfiorentino", "Cerreto Guidi", "Certaldo", "Empoli", "Fucecchio",
            "Gambassi Terme", "Montaione", "Montelupo Fiorentino", "Montespertoli", "Vinci"]},
    ],
}


class QuoteError(ValueError):
    """Dato non valido: il messaggio e' pensato per essere mostrato all'utente."""


class QuoteIncomplete(QuoteError):
    """Manca ancora una scelta (luogo, comune...): non e' un errore, e' un invito a completare."""


# ---------------------------------------------------------------------------
# Numeri e formattazione
# ---------------------------------------------------------------------------

def _dec(value, field="valore") -> Decimal:
    text = str(value if value is not None else "").strip().replace("€", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise QuoteError(f"{field}: numero non valido.") from None
    if not number.is_finite():
        raise QuoteError(f"{field}: numero non valido.")
    return number


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _json_number(value: Decimal):
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(value) if value == value.to_integral_value() else float(value)


def fmt_eur(value) -> str:
    return f"€ {_money(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_kg(value) -> str:
    text = format(Decimal(value).normalize(), "f")
    return text.replace(".", ",")


# ---------------------------------------------------------------------------
# Validazione / normalizzazione del listino
# ---------------------------------------------------------------------------

def _price(value, field) -> Decimal:
    number = _dec(value, field)
    if number < 0 or number > Decimal("100000"):
        raise QuoteError(f"{field}: importo non valido.")
    return number


def _ascending(values, field):
    previous = Decimal("0")
    for number in values:
        if number <= previous:
            raise QuoteError(f"{field}: i limiti di peso devono essere crescenti e maggiori di zero.")
        previous = number


def validate_pricelist(data) -> dict:
    """Controlla un listino (da JSON o da form) e lo restituisce normalizzato."""
    if not isinstance(data, dict):
        raise QuoteError("Listino non valido.")
    try:
        cremation_raw = data["cremation"]
        pickup_raw = data["pickup"]
        delivery_raw = data["delivery"]
        supplements_raw = data["supplements"]
        urn_raw = data["urn"]
        circondari_raw = data["circondari"]
    except (KeyError, TypeError):
        raise QuoteError("Listino incompleto.") from None

    if not isinstance(cremation_raw, list) or len(cremation_raw) < 2:
        raise QuoteError("Cremazione: servono almeno due fasce di peso.")
    limits = [_dec(row.get("max_kg"), "Cremazione, peso") for row in cremation_raw[:-1]]
    _ascending(limits, "Cremazione")
    if cremation_raw[-1].get("max_kg") not in (None, ""):
        raise QuoteError("Cremazione: l'ultima fascia deve essere senza limite.")
    cremation = [{"max_kg": _json_number(limit), "price": _json_number(_price(row.get("price"), "Cremazione, prezzo"))}
                 for limit, row in zip(limits, cremation_raw)]
    cremation.append({"max_kg": None, "price": _json_number(_price(cremation_raw[-1].get("price"), "Cremazione, prezzo"))})

    bands = [_dec(b, "Ritiro, peso") for b in pickup_raw.get("bands", [])]
    if not bands:
        raise QuoteError("Ritiro: servono dei limiti di peso.")
    _ascending(bands, "Ritiro")
    prices = {}
    for tariff in PICKUP_TARIFFS:
        row = (pickup_raw.get("prices") or {}).get(tariff)
        if not isinstance(row, list) or len(row) != len(bands) + 1:
            raise QuoteError("Ritiro: tabella prezzi incompleta.")
        prices[tariff] = [_json_number(_price(v, "Ritiro, prezzo")) for v in row]
    pickup = {"bands": [_json_number(b) for b in bands], "prices": prices,
              "outside_surcharge": _json_number(_price(pickup_raw.get("outside_surcharge"), "Ritiro fuori circondario"))}

    delivery = {"inside": _json_number(_price(delivery_raw.get("inside"), "Riconsegna nel circondario")),
                "outside": _json_number(_price(delivery_raw.get("outside"), "Riconsegna fuori circondario"))}
    supplements = {key: _json_number(_price(supplements_raw.get(key), f"Supplemento {key}"))
                   for key in ("festivo", "serale", "notturno")}
    urn = {"from_price": _json_number(_price(urn_raw.get("from_price"), "Urna"))}

    if not isinstance(circondari_raw, list) or not circondari_raw:
        raise QuoteError("Circondario: serve almeno una zona.")
    circondari, seen = [], set()
    for item in circondari_raw:
        name = " ".join(str(item.get("name") or "").split())[:60]
        comuni = []
        for comune in item.get("comuni") or []:
            clean = " ".join(str(comune or "").split())[:60]
            if not clean:
                continue
            if clean.lower() in seen:
                raise QuoteError(f"Circondario: il comune \"{clean}\" e' ripetuto.")
            seen.add(clean.lower())
            comuni.append(clean)
        if not name or not comuni:
            raise QuoteError("Circondario: ogni zona ha bisogno di un nome e di almeno un comune.")
        circondari.append({"name": name, "comuni": comuni})
    return {"cremation": cremation, "pickup": pickup, "delivery": delivery, "supplements": supplements,
            "urn": urn, "circondari": circondari}


def get_pricelist(conn) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (SETTINGS_KEY,)).fetchone()
    if row:
        try:
            return validate_pricelist(json.loads(row["value"]).get("pricelist"))
        except (QuoteError, ValueError, AttributeError, TypeError):
            pass
    return validate_pricelist(copy.deepcopy(DEFAULT_PRICELIST))


def get_pricelist_meta(conn) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (SETTINGS_KEY,)).fetchone()
    if row:
        try:
            return json.loads(row["value"]).get("meta") or {}
        except (ValueError, AttributeError):
            pass
    return {}


def save_pricelist(conn, pricelist, *, updated_by="", updated_at="") -> dict:
    clean = validate_pricelist(pricelist)
    payload = json.dumps({"pricelist": clean, "meta": {"updated_by": updated_by, "updated_at": updated_at}},
                         ensure_ascii=False)
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (SETTINGS_KEY, payload))
    return clean


def pricelist_from_form(form) -> dict:
    """Costruisce un listino dai campi della pagina di modifica del gestionale."""
    cremation = []
    count = len(DEFAULT_PRICELIST["cremation"])
    for i in range(count):
        cremation.append({"max_kg": None if i == count - 1 else form.get(f"cr_max_{i}", ""),
                          "price": form.get(f"cr_price_{i}", "")})
    band_count = len(DEFAULT_PRICELIST["pickup"]["bands"])
    pickup = {
        "bands": [form.get(f"pk_band_{i}", "") for i in range(band_count)],
        "prices": {t: [form.get(f"pk_{t}_{j}", "") for j in range(band_count + 1)] for t in PICKUP_TARIFFS},
        "outside_surcharge": form.get("pk_outside", ""),
    }
    circondari = []
    for i, default in enumerate(DEFAULT_PRICELIST["circondari"]):
        names = [line for line in str(form.get(f"circ_{i}", "")).replace(",", "\n").splitlines()]
        circondari.append({"name": default["name"], "comuni": names})
    return validate_pricelist({
        "cremation": cremation, "pickup": pickup,
        "delivery": {"inside": form.get("dl_inside", ""), "outside": form.get("dl_outside", "")},
        "supplements": {"festivo": form.get("sp_festivo", ""), "serale": form.get("sp_serale", ""),
                        "notturno": form.get("sp_notturno", "")},
        "urn": {"from_price": form.get("urn_from", "")},
        "circondari": circondari,
    })


# ---------------------------------------------------------------------------
# Calendario: domeniche e festivita' nazionali
# ---------------------------------------------------------------------------

def easter_sunday(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def national_holidays(year: int) -> set[date]:
    fixed = [(1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)]
    days = {date(year, m, d) for m, d in fixed}
    days.add(easter_sunday(year) + timedelta(days=1))  # Lunedi' dell'Angelo
    return days


def is_festivo(day: date) -> bool:
    return day.weekday() == 6 or day in national_holidays(day.year)


# ---------------------------------------------------------------------------
# Calcolo
# ---------------------------------------------------------------------------

def parse_weight(value) -> Decimal:
    weight = _dec(value, "Peso")
    if weight <= 0:
        raise QuoteError("Indica il peso dell'animale (maggiore di zero).")
    if weight > MAX_WEIGHT_KG:
        raise QuoteError("Il peso indicato non sembra corretto.")
    return weight


def comune_zone(pricelist, comune: str):
    """Nome della zona di circondario che contiene il comune, oppure None."""
    wanted = " ".join(str(comune or "").split()).lower()
    for item in pricelist["circondari"]:
        if any(c.lower() == wanted for c in item["comuni"]):
            return item["name"]
    return None


def _is_inside(pricelist, comune: str) -> bool:
    if comune == OTHER_COMUNE:
        return False
    if not comune:
        raise QuoteIncomplete("Seleziona il comune.")
    if comune_zone(pricelist, comune) is None:
        raise QuoteError("Comune non valido: scegli dall'elenco.")
    return True


def cremation_band_label(pricelist, index: int) -> str:
    rows = pricelist["cremation"]
    if index == len(rows) - 1:
        return f"oltre {fmt_kg(Decimal(str(rows[index - 1]['max_kg'])))} kg"
    start = "0" if index == 0 else fmt_kg(Decimal(str(rows[index - 1]["max_kg"])) + Decimal("0.1"))
    return f"{start}-{fmt_kg(Decimal(str(rows[index]['max_kg'])))} kg"


def pickup_band_label(pricelist, index: int) -> str:
    bands = pricelist["pickup"]["bands"]
    if index == len(bands):
        return f"oltre {fmt_kg(Decimal(str(bands[-1])))} kg"
    start = "0" if index == 0 else fmt_kg(Decimal(str(bands[index - 1])) + Decimal("0.1"))
    return f"{start}-{fmt_kg(Decimal(str(bands[index])))} kg"


def _cremation_index(pricelist, weight: Decimal) -> int:
    for index, row in enumerate(pricelist["cremation"]):
        if row["max_kg"] is None or weight <= Decimal(str(row["max_kg"])):
            return index
    return len(pricelist["cremation"]) - 1


def _pickup_index(pricelist, weight: Decimal) -> int:
    for index, limit in enumerate(pricelist["pickup"]["bands"]):
        if weight <= Decimal(str(limit)):
            return index
    return len(pricelist["pickup"]["bands"])


def calculate_quote(pricelist, *, weight, pickup_place="", pickup_comune="", delivery=False,
                    delivery_comune="", when: datetime | None = None, time_known=True) -> dict:
    """Preventivo indicativo. Restituisce {"total", "lines", "urn_note", "hints"}.

    lines: elenco di {"label", "detail", "amount"} (amount e' Decimal).
    Solleva QuoteError con un messaggio per l'utente se i dati non bastano."""
    weight = parse_weight(weight)
    lines, hints = [], []

    index = _cremation_index(pricelist, weight)
    lines.append({"label": "Cremazione singola", "detail": cremation_band_label(pricelist, index),
                  "amount": _money(str(pricelist["cremation"][index]["price"]))})

    pickup_inside = None
    if pickup_place:
        if pickup_place not in PICKUP_PLACES:
            raise QuoteError("Seleziona il luogo del ritiro.")
        label, tariff = PICKUP_PLACES[pickup_place]
        pickup_inside = _is_inside(pricelist, pickup_comune)
        band = _pickup_index(pricelist, weight)
        lines.append({"label": f"Ritiro: {label}", "detail": pickup_band_label(pricelist, band),
                      "amount": _money(str(pricelist["pickup"]["prices"][tariff][band]))})
        if not pickup_inside:
            lines.append({"label": "Ritiro fuori circondario", "detail": "",
                          "amount": _money(str(pricelist["pickup"]["outside_surcharge"]))})

    if delivery:
        comune = delivery_comune
        if comune in ("", SAME_COMUNE):
            if not pickup_place:
                raise QuoteIncomplete("Seleziona il comune della riconsegna.")
            delivery_inside = pickup_inside
        else:
            delivery_inside = _is_inside(pricelist, comune)
        key = "inside" if delivery_inside else "outside"
        lines.append({"label": "Riconsegna", "detail": "nel circondario" if delivery_inside else "fuori circondario",
                      "amount": _money(str(pricelist["delivery"][key]))})

    if when is not None and (pickup_place or delivery):
        sup = pricelist["supplements"]
        if is_festivo(when.date()):
            lines.append({"label": "Servizio festivo", "detail": "domenica o festività",
                          "amount": _money(str(sup["festivo"]))})
        if time_known:
            hour = when.time()
            if time(19, 0) <= hour < time(21, 0):
                lines.append({"label": "Servizio serale", "detail": "19:00-21:00", "amount": _money(str(sup["serale"]))})
            elif hour >= time(21, 0) or hour < time(7, 0):
                lines.append({"label": "Servizio notturno", "detail": "21:00-07:00", "amount": _money(str(sup["notturno"]))})
        else:
            hints.append("Indica anche l'ora per verificare gli eventuali supplementi serale o notturno.")

    urn_price = _money(str(pricelist["urn"]["from_price"]))
    return {
        "total": sum((line["amount"] for line in lines), Decimal("0.00")),
        "lines": lines,
        "hints": hints,
        "urn_note": f"L'urna non è inclusa nel preventivo: disponibile a partire da {fmt_eur(urn_price)} (modello standard).",
    }
