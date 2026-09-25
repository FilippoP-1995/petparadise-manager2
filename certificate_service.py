"""Certificato di garanzia di avvenuta cremazione singola (DOCX).

Solo lettura: legge i dati gia' presenti nella pratica e riempie una NUOVA copia
del modello Word ufficiale (assets/CERTIFICATO_CREMAZIONE_TEMPLATE.docx, lo stesso
modello di Pet Paradise con i dati variabili sostituiti da segnaposto). Nessuna
libreria aggiuntiva: il DOCX e' uno zip, si sostituisce il solo testo dei
segnaposto in word/document.xml e tutti gli altri file (logo, immagini, stili,
intestazione, piede) restano byte per byte identici al modello.
"""
import io
import re
import unicodedata
import zipfile
from datetime import date
from html import escape
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "CERTIFICATO_CREMAZIONE_TEMPLATE.docx"
PLANT_TYPE = "FORNO CREMATORIO"
PLACEHOLDERS = ("PROPRIETARIO", "DATA_CERTIFICATO", "NOME_ANIMALE", "ETA", "SPECIE", "SEDE")


class CertificateDataError(ValueError):
    """Dati obbligatori mancanti: `missing` e' l'elenco dei messaggi per l'operatore."""

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__("; ".join(self.missing))


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def _count(value):
    """Numero intero di anni/mesi come inserito dall'operatore; None se vuoto/zero."""
    text = _clean(value)
    if not text:
        return None
    if text.isdigit():
        return int(text) or None
    return text  # testo libero non numerico: si riporta cosi' com'e', mai ricalcolato


def age_segments(years, months):
    """Segmenti (testo, grassetto) dell'eta' dai soli campi anni/mesi della pratica.

    Numeri in grassetto, parole normali, come nel modello. Nessun calcolo da date.
    Ritorna [] se non c'e' nessuna eta' inserita.
    """
    parts = []
    for value, singular, plural in ((_count(years), "anno", "anni"), (_count(months), "mese", "mesi")):
        if value is None:
            continue
        unit = singular if value == 1 else plural
        parts.append((str(value), unit))
    segments = []
    for index, (number, unit) in enumerate(parts):
        if index:
            segments.append((" e ", False))
        segments.append((number, True))
        segments.append((f" {unit}", False))
    return segments


def plant_branch_name(destination_branch, branches):
    """Sede dell'impianto di cremazione, dalla stessa fonte V1 (BRANCHES/plant_type).

    Il certificato attesta la cremazione: vale la sede che in V1 e' il FORNO
    CREMATORIO. Se la sede di destinazione della pratica lo e' gia', e' quella.
    """
    branch = _clean(destination_branch)
    if branch in branches and branches[branch].get("plant_type") == PLANT_TYPE:
        return branch
    plants = [name for name, info in branches.items() if info.get("plant_type") == PLANT_TYPE]
    return plants[0] if len(plants) == 1 else ""


def certificate_data(practice, today, branches):
    """Dati del certificato dalla pratica. `today` e' la data di GENERAZIONE (Roma)."""
    keys = practice.keys()
    get = lambda key: practice[key] if key in keys else ""
    owner = " ".join(part for part in (_clean(get("owner_first_name")), _clean(get("owner_last_name"))) if part)
    animal = _clean(get("animal_name"))
    species = _clean(get("species"))
    age = age_segments(get("age_years"), get("age_months"))
    sede = plant_branch_name(get("destination_branch"), branches)
    missing = []
    if _clean(get("service_type")) != "Cremazione singola":
        missing.append("Il certificato riguarda solo la cremazione singola.")
    if not owner:
        missing.append("Manca il proprietario nella pratica.")
    if not animal:
        missing.append("Manca il nome dell'animale.")
    if not age:
        missing.append("Manca l'età dell'animale.")
    if not species:
        missing.append("Manca la specie.")
    if not sede:
        missing.append("Manca la sede dell'impianto.")
    if missing:
        raise CertificateDataError(missing)
    return {
        "PROPRIETARIO": owner,
        "DATA_CERTIFICATO": today.strftime("%d.%m.%Y"),
        "NOME_ANIMALE": animal,
        # nel modello la specie e' scritta in minuscolo ("gatto"): solo formato, non il dato
        "SPECIE": species.lower(),
        "ETA": age,
        "SEDE": sede,
    }


def _age_runs(run_xml, segments):
    plain_rpr = run_xml.replace("<w:b/>", "").replace("<w:bCs/>", "")
    out = []
    for text, bold in segments:
        template = run_xml if bold else plain_rpr
        out.append(re.sub(r"<w:t[^>]*>.*?</w:t>", f'<w:t xml:space="preserve">{escape(text, quote=False)}</w:t>', template, count=1, flags=re.S))
    return "".join(out)


def build_certificate(data, template_path=TEMPLATE_PATH):
    """DOCX (bytes): copia del modello con i segnaposto sostituiti; il modello non viene toccato."""
    with zipfile.ZipFile(template_path) as source:
        document = source.read("word/document.xml").decode("utf-8")
        # eta': sostituisce l'intero run del segnaposto con run in grassetto/normale
        marker = "{{ETA}}"
        position = document.index(marker)
        starts = [m.start() for m in re.finditer(r"<w:r[ >]", document[:position])]
        run_start = starts[-1]
        run_end = document.index("</w:r>", position) + len("</w:r>")
        document = document[:run_start] + _age_runs(document[run_start:run_end], data["ETA"]) + document[run_end:]
        for name in PLACEHOLDERS:
            if name == "ETA":
                continue
            document = document.replace("{{" + name + "}}", escape(data[name], quote=False))
        if "{{" in document or "}}" in document:
            raise RuntimeError("Segnaposto non sostituito nel certificato")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as target:
            for info in source.infolist():
                payload = document.encode("utf-8") if info.filename == "word/document.xml" else source.read(info.filename)
                target.writestr(info, payload, compress_type=info.compress_type)
        return buffer.getvalue()


def certificate_filename(practice):
    """Certificato_Cremazione_<Animale>_<CR000123>.docx, solo caratteri ASCII sicuri."""
    def ascii_part(value):
        text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    animal = ascii_part(practice["animal_name"]) or "Animale"
    number = re.sub(r"[^A-Za-z0-9]", "", ascii_part(practice["practice_number"]))
    return "_".join(part for part in ("Certificato_Cremazione", animal, number) if part) + ".docx"
