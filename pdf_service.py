from __future__ import annotations

import base64
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


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


# Unica sorgente coordinate: tutti i campi automatici usano centro + larghezza.
# In questo modo il testo resta centrato anche se cambia lunghezza.
PAGE1_FIELDS = {
    # x, y, w, h sono riquadri reali in punti PDF (origine in basso a sinistra).
    # I campi evidenziati sul DDT cartaceo sono gestiti come box: testo centrato
    # verticalmente, padding interno e riduzione automatica font se troppo lungo.
    "ddt_number": {"x": 380, "y": 722, "w": 76, "h": 18, "size": 12, "align": "center"},
    "ddt_date": {"x": 390, "y": 692, "w": 58, "h": 18, "size": 11, "date": True, "align": "center"},
    "owner": {"x": 90, "y": 696, "w": 238, "h": 18, "size": 11.5, "align": "left"},
    "owner_address": {"x": 76, "y": 653, "w": 248, "h": 34, "size": 10.5, "align": "left"},
    "transport_method": {"cx": 500, "y": 674, "w": 128, "size": 11},
    "vehicle_plate": {"cx": 500, "y": 648, "w": 128, "size": 11},
    "origin": {"x": 78, "y": 533, "w": 246, "h": 22, "size": 11, "align": "left"},
    "package_count": {"cx": 528, "y": 444, "w": 62, "size": 13},
    "container_id": {"x": 300, "y": 442, "w": 76, "h": 20, "size": 12, "align": "center"},
    "species_goods": {"x": 207, "y": 373, "w": 58, "h": 18, "size": 12, "align": "center"},
    "weight_goods": {"cx": 455, "y": 392, "w": 112, "size": 12},
    "lot_number": {"cx": 542, "y": 392, "w": 55, "size": 11},
    "treatment_method": {"cx": 555, "y": 338, "w": 95, "size": 11},
    "species_animal": {"x": 207, "y": 304, "w": 58, "h": 18, "size": 12, "align": "center"},
    "microchip": {"cx": 675 / 2, "y": 269, "w": 250, "size": 11},
}
PAGE2_FIELDS = {
    "ddt_date": {"cx": 195, "y": 588, "w": 120, "size": 14, "date": True},
    "ddt_number": {"cx": 505, "y": 588, "w": 90, "size": 14},
    "animal_name": {"cx": 200, "y": 556, "w": 155, "size": 14},
    "microchip": {"cx": 500, "y": 556, "w": 130, "size": 13.5},
    "age_years": {"cx": 158, "y": 524, "w": 70, "size": 13.5},
    "age_months": {"cx": 246, "y": 524, "w": 70, "size": 13.5},
    "weight": {"cx": 472, "y": 524, "w": 165, "size": 13.5},
    "clinic": {"cx": 305, "y": 487, "w": 275, "size": 13.5},
    "owner": {"cx": 103, "y": 431, "w": 125, "size": 12.5, "cover": True},
    "owner_phone": {"cx": 300, "y": 431, "w": 125, "size": 12.5, "cover": True},
    "owner_email": {"cx": 458, "y": 431, "w": 160, "size": 10.5, "cover": True},
    "price_cremation": {"cx": 185, "y": 379, "w": 105, "size": 13.5},
    "price_pickup": {"cx": 340, "y": 379, "w": 105, "size": 13.5},
    "price_evening": {"cx": 535, "y": 379, "w": 105, "size": 13.5},
    "price_urn": {"cx": 182, "y": 354, "w": 48, "size": 11.5},
    "price_delivery": {"cx": 340, "y": 354, "w": 105, "size": 13.5},
    "price_night": {"cx": 535, "y": 354, "w": 105, "size": 13.5},
    "price_cast": {"cx": 185, "y": 330, "w": 105, "size": 13.5},
    "price_holiday": {"cx": 340, "y": 330, "w": 105, "size": 13.5},
    "price_accessories": {"cx": 515, "y": 330, "w": 145, "size": 12.5},
    "deposit": {"cx": 182, "y": 251, "w": 140, "size": 13.5},
    "total_service": {"cx": 455, "y": 259, "w": 150, "size": 14},
    "decl_owner": {"cx": 218, "y": 212, "w": 145, "size": 10, "cover": True},
    "tax_code": {"cx": 394, "y": 212, "w": 128, "size": 10, "cover": True},
    "decl_species": {"cx": 261, "y": 198, "w": 58, "size": 10, "cover": True},
    "decl_breed": {"cx": 390, "y": 198, "w": 136, "size": 10, "cover": True},
    "decl_microchip": {"cx": 222, "y": 184, "w": 128, "size": 10, "cover": True},
    "decl_date": {"cx": 131, "y": 130, "w": 106, "size": 10, "date": True, "cover": True},
    "signing_place": {"cx": 255, "y": 130, "w": 75, "size": 10, "cover": True},
    "document_number": {"cx": 206, "y": 102, "w": 132, "size": 10, "cover": True},
    "document_date": {"cx": 380, "y": 102, "w": 80, "size": 10, "date": True, "cover": True},
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


def _get(page, key, default=""):
    try:
        value = page[key]
    except Exception:
        value = default
    return value if value not in (None, "") else default


def _date(value):
    value = str(value or "")
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return value


def _fit_text(value, width, size, font="Helvetica"):
    value = _pdf_safe(value)
    if not value:
        return "", size
    while size > 7 and stringWidth(value, font, size) > width:
        size -= 0.5
    if stringWidth(value, font, size) <= width:
        return value, size
    ellipsis = "..."
    while value and stringWidth(value + ellipsis, font, size) > width:
        value = value[:-1]
    return value + ellipsis if value else "", size


def _draw_text_box(c, spec, value):
    value = _date(value) if spec.get("date") else value
    padding = float(spec.get("padding", 2.5))
    width = max(1, float(spec["w"]) - padding * 2)
    value, size = _fit_text(value, width, spec["size"])
    if not value:
        return

    if "x" in spec:
        x = float(spec["x"])
        y = float(spec["y"])
        h = float(spec.get("h", size + padding * 2))
        draw_y = y + (h - size) / 2 + 0.8
        if spec.get("cover"):
            c.saveState()
            c.setFillColorRGB(1, 1, 1)
            c.rect(x, y, spec["w"], h, fill=1, stroke=0)
            c.restoreState()
        c.setFont("Helvetica", size)
        align = spec.get("align", "center")
        text_w = stringWidth(value, "Helvetica", size)
        if align == "left":
            draw_x = x + padding
        elif align == "right":
            draw_x = x + spec["w"] - padding - text_w
        else:
            draw_x = x + (spec["w"] - text_w) / 2
        c.drawString(draw_x, draw_y, value)
        return

    cx, y, w = spec["cx"], spec["y"], spec["w"]
    if spec.get("cover"):
        c.saveState()
        c.setFillColorRGB(1, 1, 1)
        c.rect(cx - w / 2 - 2, y - 2, w + 4, size + 4, fill=1, stroke=0)
        c.restoreState()
    c.setFont("Helvetica", size)
    c.drawString(cx - stringWidth(value, "Helvetica", size) / 2, y, value)


def _draw_centered(c, spec, value):
    _draw_text_box(c, spec, value)


def _draw_fields(c, fields, values):
    for name, value in values.items():
        if name in fields:
            _draw_centered(c, fields[name], value)


def _draw_signature(c, page):
    data = _get(page, "signature_data")
    if not data.startswith("data:image/png;base64,"):
        return
    try:
        raw = base64.b64decode(data.split(",", 1)[1])
        c.drawImage(ImageReader(BytesIO(raw)), 88, 42, width=190, height=58, mask="auto", preserveAspectRatio=True)
    except Exception:
        return


def _urn_display(value):
    value = str(value or "").strip()
    match = re.match(r"^(\d+(?:[,.]\d+)?)\s+(.+)$", value)
    if match:
        return f"{match.group(2).strip()} {match.group(1).strip()}"
    return value


def _overlay_page_1(page, width, height):
    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(width, height))
    branch = BRANCHES[_get(page, "destination_branch", "Livorno")]
    owner = f'{_get(page, "owner_first_name")} {_get(page, "owner_last_name")}'.strip()

    values = {
        "ddt_number": _get(page, "ddt_number"),
        "ddt_date": _get(page, "ddt_date"),
        "owner": owner,
        "owner_address": _get(page, "owner_address"),
        "transport_method": _get(page, "transport_method"),
        "vehicle_plate": _get(page, "vehicle_plate"),
        "origin": _get(page, "origin_text") if _get(page, "origin_mode", "IDEM SPED") == "Testo libero" else "IDEM SPED.",
        "package_count": _get(page, "package_count", "1"),
        "container_id": _get(page, "container_id"),
        "species_goods": _get(page, "species"),
        "weight_goods": f'{_get(page, "estimated_weight")} KG',
        "lot_number": _get(page, "lot_number", "/"),
        "treatment_method": _get(page, "treatment_method", "/"),
        "species_animal": _get(page, "species"),
        "microchip": _get(page, "microchip", "/"),
    }
    _draw_fields(c, PAGE1_FIELDS, values)

    if _get(page, "transporter_mode", "IDEM SPED") == "DATI PET PARADISE":
        _draw_centered(c, {"cx": 420, "y": 620, "w": 170, "size": 8.8}, COMPANY["name"])
        _draw_centered(c, {"cx": 420, "y": 607, "w": 175, "size": 6.8}, branch["address"])
        _draw_centered(c, {"cx": 420, "y": 596, "w": 165, "size": 7.2}, COMPANY["vat"])
    else:
        _draw_centered(c, {"cx": 420, "y": 612, "w": 165, "size": 11}, "IDEM SPED.")

    temp_x = {"Ambiente": 108, "Refrigerato": 238, "Congelato": 354}.get(_get(page, "temperature_mode", "Ambiente"), 108)
    c.setFont("Helvetica", 11)
    c.drawString(temp_x, 467, "X")
    c.save(); stream.seek(0)
    return PdfReader(stream).pages[0]


def _overlay_page_2(page, width, height):
    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(width, height))
    owner = f'{_get(page, "owner_first_name")} {_get(page, "owner_last_name")}'.strip()

    values = {
        "ddt_date": _get(page, "ddt_date"),
        "ddt_number": _get(page, "ddt_number"),
        "animal_name": _get(page, "animal_name"),
        "microchip": _get(page, "microchip", "/"),
        "age_years": _get(page, "age_years", "0"),
        "age_months": _get(page, "age_months", "0"),
        "weight": f'{_get(page, "estimated_weight")} KG',
        "clinic": _get(page, "clinic_name"),
        "owner": owner,
        "owner_phone": _get(page, "owner_phone"),
        "owner_email": _get(page, "owner_email"),
        "price_cremation": _get(page, "price_cremation"),
        "price_pickup": _get(page, "price_pickup"),
        "price_evening": _get(page, "price_evening"),
        "price_urn": _urn_display(_get(page, "price_urn")),
        "price_delivery": _get(page, "price_delivery"),
        "price_night": _get(page, "price_night"),
        "price_cast": _get(page, "price_cast"),
        "price_holiday": _get(page, "price_holiday"),
        "price_accessories": _get(page, "price_accessories"),
        "deposit": _get(page, "deposit"),
        "total_service": _get(page, "total_service"),
        "decl_owner": owner,
        "tax_code": _get(page, "owner_tax_code"),
        "decl_species": _get(page, "species"),
        "decl_breed": _get(page, "breed"),
        "decl_microchip": _get(page, "microchip"),
        "decl_date": _get(page, "ddt_date"),
        "signing_place": _get(page, "signing_place", _get(page, "destination_branch")),
        "document_number": _get(page, "identity_document_number"),
        "document_date": _get(page, "identity_document_date"),
    }
    _draw_fields(c, PAGE2_FIELDS, values)
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

