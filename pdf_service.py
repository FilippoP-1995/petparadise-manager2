from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth


COMPANY = {
    "name": "PET PARADISE SNC",
    "vat": "P.IVA 02023030493",
    "approval": "ABP 3811 - INCP1",
}

BRANCHES = {
    "Livorno": {
        "address": "VIA DEI MATERASSAI, 10 - LIVORNO, 57121 (LI)",
        "plant_type": "FORNO CREMATORIO",
    },
    "Empoli": {
        "address": "VIA RENATO FUCINI, 23 - EMPOLI, 50053 (FI)",
        "plant_type": "IMPRESA FUNEBRE",
    },
}


def _pdf_safe(value):
    value = str(value or "").strip()
    replacements = {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u20ac": "Euro",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value.encode("latin-1", "replace").decode("latin-1")


def _text(c, x, y, value, size=8, max_chars=None):
    value = _pdf_safe(value)
    if not value:
        return
    if max_chars and len(value) > max_chars:
        value = value[: max_chars - 3] + "..."
    c.setFont("Helvetica", size)
    c.drawString(x, y, value)


def _get(page, key, default=""):
    try:
        value = page[key]
    except Exception:
        value = default
    return value if value not in (None, "") else default


def _center_text(c, center_x, y, value, size=8, max_chars=None):
    value = _pdf_safe(value)
    if not value:
        return
    if max_chars and len(value) > max_chars:
        value = value[: max_chars - 3] + "..."
    c.setFont("Helvetica", size)
    c.drawString(center_x - stringWidth(value, "Helvetica", size) / 2, y, value)


def _field_text(c, x, y, value, width, size=8, max_chars=None):
    value = _pdf_safe(value)
    if not value:
        return
    c.saveState()
    c.setFillColorRGB(1, 1, 1)
    c.rect(x - 2, y - 2, width, size + 4, fill=1, stroke=0)
    c.restoreState()
    _text(c, x, y, value, size, max_chars)


def _center_field_text(c, center_x, y, value, width, size=8, max_chars=None):
    value = _pdf_safe(value)
    if not value:
        return
    if max_chars and len(value) > max_chars:
        value = value[: max_chars - 3] + "..."
    x = center_x - width / 2
    c.saveState()
    c.setFillColorRGB(1, 1, 1)
    c.rect(x - 2, y - 2, width + 4, size + 4, fill=1, stroke=0)
    c.restoreState()
    _center_text(c, center_x, y, value, size)


def _draw_signature(c, page):
    data = _get(page, "signature_data")
    if not data.startswith("data:image/png;base64,"):
        return
    try:
        raw = base64.b64decode(data.split(",", 1)[1])
        c.drawImage(ImageReader(BytesIO(raw)), 88, 42, width=190, height=58, mask="auto", preserveAspectRatio=True)
    except Exception:
        return


def _date(value):
    value = str(value or "")
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%y")
    except ValueError:
        return value


def _overlay_page_1(page, width, height):
    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(width, height))
    branch = BRANCHES[_get(page, "destination_branch", "Livorno")]
    owner = f'{_get(page, "owner_first_name")} {_get(page, "owner_last_name")}'.strip()
    source_name = owner
    source_address = _get(page, "owner_address")

    # DDT and date
    _text(c, 390, 727, _get(page, "ddt_number"), 12)
    _text(c, 375, 700, _date(_get(page, "ddt_date")), 12)

    # Speditore: campi puliti e leggermente piu bassi per non sovrapporsi al modello.
    _field_text(c, 92, 706, owner, 230, 11.5, 32)
    _field_text(c, 70, 674, _get(page, "owner_address"), 285, 10.5, 55)
    _text(c, 440, 674, _get(page, "transport_method"), 11, 18)
    _text(c, 430, 648, _get(page, "vehicle_plate"), 11, 16)

    # Destinatario e destinazione sono prestampati nei modelli di sede.
    if _get(page, "transporter_mode", "IDEM SPED") == "DATI PET PARADISE":
        _center_text(c, 420, 620, COMPANY["name"], 8.8, 28)
        _center_text(c, 420, 607, branch["address"], 6.8, 34)
        _center_text(c, 420, 596, COMPANY["vat"], 7.2, 24)
    else:
        _center_text(c, 420, 612, "IDEM SPED.", 11)

    # Luogo di origine
    origin = _get(page, "origin_text") if _get(page, "origin_mode", "IDEM SPED") == "Testo libero" else "IDEM SPED."
    _text(c, 120, 547, origin, 11, 48)

    # Condizioni di trasporto
    temp_x = {"Ambiente": 108, "Refrigerato": 238, "Congelato": 354}.get(_get(page, "temperature_mode", "Ambiente"), 108)
    _text(c, temp_x, 467, "X", 11)
    _text(c, 510, 444, _get(page, "package_count", "1"), 13)
    _center_text(c, 292, 423, _get(page, "container_id"), 12, 45)

    # Merce / animale
    _text(c, 150, 392, _get(page, "species"), 12, 20)
    _center_text(c, 455, 392, f'{_get(page, "estimated_weight")} KG', 12)
    _text(c, 520, 392, _get(page, "lot_number", "/"), 11)
    _text(c, 300, 338, _get(page, "treatment_method", "/"), 11)
    _text(c, 136, 307, _get(page, "species"), 12)
    _text(c, 270, 269, _get(page, "microchip", "/"), 11, 45)
    c.save(); stream.seek(0)
    return PdfReader(stream).pages[0]


def _overlay_page_2(page, width, height):
    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(width, height))
    owner = f'{_get(page, "owner_first_name")} {_get(page, "owner_last_name")}'.strip()

    _text(c, 153, 588, _date(_get(page, "ddt_date")), 14)
    _text(c, 466, 588, _get(page, "ddt_number"), 14)
    _text(c, 122, 556, _get(page, "animal_name"), 14, 24)
    _text(c, 427, 556, _get(page, "microchip", "/"), 13.5, 26)
    _text(c, 135, 524, _get(page, "age_years", "0"), 13.5)
    _text(c, 220, 524, _get(page, "age_months", "0"), 13.5)
    _center_text(c, 472, 524, f'{_get(page, "estimated_weight")} KG', 13.5)
    _center_text(c, 305, 487, _get(page, "clinic_name"), 13.5, 30)
    _center_field_text(c, 135, 431, owner, 150, 12.5, 28)
    _center_field_text(c, 390, 431, _get(page, "owner_phone"), 116, 12.5, 24)
    _center_field_text(c, 514, 431, _get(page, "owner_email"), 96, 10.5, 27)

    # Preventivo stimato
    _text(c, 155, 379, _get(page, "price_cremation"), 13.5)
    _text(c, 310, 379, _get(page, "price_pickup"), 13.5)
    _text(c, 505, 379, _get(page, "price_evening"), 13.5)
    _text(c, 155, 354, _get(page, "price_urn"), 13.5)
    _text(c, 310, 354, _get(page, "price_delivery"), 13.5)
    _text(c, 505, 354, _get(page, "price_night"), 13.5)
    _text(c, 155, 330, _get(page, "price_cast"), 13.5)
    _text(c, 310, 330, _get(page, "price_holiday"), 13.5)
    _text(c, 475, 330, _get(page, "price_accessories"), 12.5)
    _text(c, 125, 251, _get(page, "deposit"), 13.5)
    _text(c, 405, 259, _get(page, "total_service"), 14)

    # Dichiarazione del proprietario
    _center_field_text(c, 218, 212, owner, 166, 10, 30)
    _center_field_text(c, 394, 212, _get(page, "owner_tax_code"), 128, 10, 22)
    _field_text(c, 232, 198, _get(page, "species"), 58, 10, 13)
    _field_text(c, 322, 198, _get(page, "breed"), 136, 10, 24)
    _field_text(c, 158, 184, _get(page, "microchip"), 128, 10, 24)
    _field_text(c, 78, 130, _date(_get(page, "ddt_date")), 106, 10)
    _field_text(c, 218, 130, _get(page, "signing_place", _get(page, "destination_branch")), 75, 10, 18)
    _field_text(c, 140, 102, _get(page, "identity_document_number"), 132, 10, 24)
    _field_text(c, 340, 102, _date(_get(page, "identity_document_date")), 80, 10)
    _draw_signature(c, page)
    c.save(); stream.seek(0)
    return PdfReader(stream).pages[0]


def generate_ddt(practice, template_path: Path, output_path: Path):
    branch_template = template_path.parent / f'DCS_{str(_get(practice, "destination_branch", "Livorno")).upper()}.pdf'
    if branch_template.exists():
        template_path = branch_template
    reader = PdfReader(str(template_path))
    writer = PdfWriter()
    for index, base in enumerate(reader.pages):
        width, height = float(base.mediabox.width), float(base.mediabox.height)
        overlay = _overlay_page_1(practice, width, height) if index == 0 else _overlay_page_2(practice, width, height)
        base.merge_page(overlay)
        writer.add_page(base)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        writer.write(handle)
