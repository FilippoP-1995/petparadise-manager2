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
    "ddt_number": {"x": 405, "y": 716, "w": 132, "h": 18, "size": 10.5, "align": "center"},
    "ddt_date": {"x": 386, "y": 699, "w": 72, "h": 16, "size": 10.5, "date": True, "align": "center"},
    "owner": {"x": 90, "y": 690, "w": 238, "h": 21, "size": 11.5, "align": "left"},
    "owner_address": {"x": 76, "y": 648, "w": 248, "h": 28, "size": 10.5, "align": "left"},
    "transport_method": {"x": 420, "y": 662, "w": 118, "h": 18, "size": 10.5, "align": "center"},
    "vehicle_plate": {"x": 420, "y": 647, "w": 118, "h": 17, "size": 10.5, "align": "center"},
    "origin": {"x": 78, "y": 526, "w": 210, "h": 22, "size": 10.5, "align": "left"},
    "package_count": {"x": 426, "y": 447, "w": 112, "h": 18, "size": 12, "align": "center"},
    "container_id": {"x": 270, "y": 436, "w": 145, "h": 19, "size": 11.5, "align": "center"},
    "species_goods": {"x": 207, "y": 373, "w": 58, "h": 18, "size": 12, "align": "center"},
    "weight_goods": {"x": 377, "y": 386, "w": 118, "h": 18, "size": 11.5, "align": "center"},
    "lot_number": {"x": 503, "y": 386, "w": 40, "h": 18, "size": 9.5, "align": "center"},
    "treatment_method": {"x": 305, "y": 323, "w": 112, "h": 18, "size": 10.5, "align": "center"},
    "species_animal": {"x": 207, "y": 304, "w": 58, "h": 18, "size": 12, "align": "center"},
    "microchip": {"x": 270, "y": 257, "w": 268, "h": 18, "size": 10.5, "align": "center"},
}
PAGE2_FIELDS = {
    "ddt_date": {"x": 151, "y": 582, "w": 83, "h": 18, "size": 12, "date": True},
    "ddt_number": {"x": 468, "y": 582, "w": 68, "h": 18, "size": 11},
    "animal_name": {"x": 121, "y": 550, "w": 92, "h": 18, "size": 12},
    "microchip": {"x": 426, "y": 550, "w": 101, "h": 18, "size": 11.5},
    "age_years": {"x": 121, "y": 518, "w": 47, "h": 18, "size": 11.5},
    "age_months": {"x": 206, "y": 518, "w": 57, "h": 18, "size": 11.5},
    "weight": {"x": 426, "y": 518, "w": 101, "h": 18, "size": 11.5},
    "clinic": {"x": 206, "y": 481, "w": 182, "h": 18, "size": 11.5},
    "owner": {"cx": 103, "y": 431, "w": 125, "size": 12.5, "cover": True},
    "owner_phone": {"cx": 300, "y": 431, "w": 125, "size": 12.5, "cover": True},
    "owner_email": {"cx": 458, "y": 431, "w": 160, "size": 10.5, "cover": True},
    "price_cremation": {"x": 156, "y": 373, "w": 49, "h": 17, "size": 11.5},
    "price_pickup": {"x": 313, "y": 373, "w": 49, "h": 17, "size": 11.5},
    "price_evening": {"x": 497, "y": 373, "w": 54, "h": 17, "size": 11.5},
    "price_urn": {"x": 156, "y": 348, "w": 49, "h": 17, "size": 9.5},
    "price_delivery": {"x": 313, "y": 348, "w": 49, "h": 17, "size": 11.5},
    "price_night": {"x": 497, "y": 348, "w": 54, "h": 17, "size": 11.5},
    "price_cast": {"x": 156, "y": 324, "w": 49, "h": 17, "size": 11.5},
    "price_holiday": {"x": 313, "y": 324, "w": 49, "h": 17, "size": 11.5},
    "price_accessories": {"x": 456, "y": 324, "w": 71, "h": 17, "size": 9.5},
    "deposit": {"x": 121, "y": 250, "w": 64, "h": 18, "size": 11.5},
    "total_service": {"x": 392, "y": 258, "w": 82, "h": 18, "size": 12},
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
        c.saveState()
        clip = c.beginPath()
        clip.rect(x, y, spec["w"], h)
        c.clipPath(clip, stroke=0, fill=0)
        c.drawString(draw_x, draw_y, value)
        c.restoreState()
        return

    cx, y, w = spec["cx"], spec["y"], spec["w"]
    if spec.get("cover"):
        c.saveState()
        c.setFillColorRGB(1, 1, 1)
        c.rect(cx - w / 2 - 2, y - 2, w + 4, size + 4, fill=1, stroke=0)
        c.restoreState()
    c.setFont("Helvetica", size)
    c.saveState()
    clip = c.beginPath()
    clip.rect(cx - w / 2, y - 2, w, size + 4)
    c.clipPath(clip, stroke=0, fill=0)
    c.drawString(cx - stringWidth(value, "Helvetica", size) / 2, y, value)
    c.restoreState()


def _draw_centered(c, spec, value):
    _draw_text_box(c, spec, value)


def _draw_fields(c, fields, values):
    for name, value in values.items():
        if name in fields:
            _draw_centered(c, fields[name], value)


def _draw_total_w_label(c):
    """Sostituisce solo la dicitura stampata, senza cambiare il campo total_service."""
    c.saveState()
    c.setFillColorRGB(1, 1, 1)
    c.rect(363, 274, 143, 23, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Bold", 12.5)
    c.drawCentredString(434.5, 279.5, "TOTALE W")
    c.restoreState()


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
    branch_name = _get(page, "destination_branch", "Livorno")
    branch = BRANCHES[branch_name]
    y_offset = 12 if branch_name == "Empoli" else 0
    fields = {name: ({**spec, "y": spec["y"] + y_offset} if "y" in spec else dict(spec))
              for name, spec in PAGE1_FIELDS.items()}
    owner = f'{_get(page, "owner_first_name")} {_get(page, "owner_last_name")}'.strip()

    values = {
        "ddt_number": _get(page, "ddt_number"),
        "ddt_date": _get(page, "ddt_date"),
        "owner": owner,
        "owner_address": _get(page, "owner_address"),
        "transport_method": _get(page, "transport_method"),
        "vehicle_plate": _get(page, "vehicle_plate"),
        "origin": _get(page, "origin_text") if _get(page, "origin_mode", "IDEM SPED") != "IDEM SPED" else "IDEM SPED.",
        "package_count": _get(page, "package_count", "1"),
        "container_id": _get(page, "container_id"),
        "species_goods": _get(page, "species"),
        "weight_goods": f'{_get(page, "estimated_weight")} KG',
        "lot_number": _get(page, "lot_number", "/"),
        "treatment_method": _get(page, "treatment_method", "/"),
        "species_animal": _get(page, "species"),
        "microchip": _get(page, "microchip", "/"),
    }
    _draw_fields(c, fields, values)

    if _get(page, "transporter_mode", "IDEM SPED") == "DATI PET PARADISE":
        _draw_centered(c, {"cx": 420, "y": 620 + y_offset, "w": 170, "size": 8.8}, COMPANY["name"])
        _draw_centered(c, {"cx": 420, "y": 607 + y_offset, "w": 175, "size": 6.8}, branch["address"])
        _draw_centered(c, {"cx": 420, "y": 596 + y_offset, "w": 165, "size": 7.2}, COMPANY["vat"])
    else:
        _draw_centered(c, {"cx": 420, "y": 612 + y_offset, "w": 165, "size": 11}, "IDEM SPED.")

    temp_x = {"Ambiente": 108, "Refrigerato": 238, "Congelato": 354}.get(_get(page, "temperature_mode", "Ambiente"), 108)
    c.setFont("Helvetica", 11)
    c.drawString(temp_x, 467 + y_offset, "X")
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
    _draw_total_w_label(c)
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

