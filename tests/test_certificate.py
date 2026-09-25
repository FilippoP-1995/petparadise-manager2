import hashlib
import html
import io
import re
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app
import certificate_service


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    # ---------- helpers ----------
    def make_practice(self, number="CR-000123", animal="Luna", species="Gatto", years="18", months="6",
                      first="Mario", last="Rossi", branch="Livorno", service="Cremazione singola",
                      pickup="2026-09-20", cycle_id=None):
        stamp = app.now()
        with app.db() as conn:
            return conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                   created_at,updated_at,created_by,animal_name,species,age_years,age_months,owner_first_name,
                   owner_last_name,pickup_date,cremation_cycle_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (number, "Privato", branch, "Cremato", service, stamp, stamp, self.admin["id"], animal, species,
                 years, months, first, last, pickup, cycle_id),
            ).lastrowid

    def make_cycle(self, cycle_date="2026-09-20"):
        stamp = app.now()
        with app.db() as conn:
            return conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,actual_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (cycle_date, "completato", "08:30", "09:30", f"{cycle_date}T09:30:00", stamp, stamp),
            ).lastrowid

    def generate(self, pid, today=(2026, 9, 24)):
        """Chiama la vera route di generazione con la data di 'oggi' simulata. -> (status, headers, body)."""
        sent = {}
        written = []
        self.handler.send_response = lambda status: sent.update(status=status)
        self.handler.send_header = lambda k, v: sent.setdefault("headers", {}).__setitem__(k, v)
        self.handler.end_headers = lambda: None
        self.handler.wfile = type("W", (), {"write": staticmethod(lambda data: written.append(data))})()
        self.handler.send_html = lambda content, status=200: sent.update(status=status, html=content)
        self.handler.send_error = lambda status, *a: sent.update(status=status)
        with patch.object(app, "rome_now", return_value=datetime(*today, 15, 30, 0)):
            self.handler.cremation_certificate(self.admin, pid)
        return sent.get("status"), sent.get("headers", {}), b"".join(written), sent.get("html")

    @staticmethod
    def lines(payload):
        xml = zipfile.ZipFile(io.BytesIO(payload)).read("word/document.xml").decode("utf-8")
        out = []
        for par in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
            text = html.unescape("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", par, re.S)))
            if text.strip():
                out.append(text)
        return out

    def text(self, pid, today=(2026, 9, 24)):
        status, _headers, body, _page = self.generate(pid, today)
        self.assertEqual(status, 200)
        return "\n".join(self.lines(body))

    # ---------- eta' (anni/mesi inseriti dall'operatore, mai calcolati) ----------
    def test_age_dog_years_and_months(self):
        pid = self.make_practice(animal="Rex", species="Cane", years="18", months="6")
        self.assertIn("Età: 18 anni e 6 mesi", self.text(pid))

    def test_age_dog_years_only(self):
        pid = self.make_practice(animal="Rex", species="Cane", years="12", months="")
        text = self.text(pid)
        self.assertIn("Età: 12 anni ", text)
        self.assertNotIn("12 anni e", text)
        pid = self.make_practice(number="CR-000124", animal="Rex", species="Cane", years="12", months="0")
        self.assertNotIn("12 anni e", self.text(pid))

    def test_age_cat_years_and_months(self):
        pid = self.make_practice(animal="Micio", species="Gatto", years="3", months="2")
        self.assertIn("Età: 3 anni e 2 mesi", self.text(pid))

    def test_age_months_only_and_singular_forms(self):
        self.assertIn("Età: 6 mesi", self.text(self.make_practice(years="0", months="6")))
        self.assertIn("Età: 6 mesi", self.text(self.make_practice(number="CR-000125", years="", months="6")))
        self.assertIn("Età: 1 anno e 1 mese", self.text(self.make_practice(number="CR-000126", years="1", months="1")))
        self.assertIn("Età: 1 anno ", self.text(self.make_practice(number="CR-000127", years="1", months="")))

    def test_age_is_taken_from_fields_never_computed_from_dates(self):
        # nascita/creazione/cremazione lontanissime dall'eta' inserita: conta solo anni/mesi
        pid = self.make_practice(years="7", months="", pickup="2001-01-01")
        self.assertIn("Età: 7 anni ", self.text(pid, today=(2030, 1, 1)))

    def test_age_segments_bold_only_numbers(self):
        self.assertEqual(certificate_service.age_segments("18", "6"),
                         [("18", True), (" anni", False), (" e ", False), ("6", True), (" mesi", False)])
        self.assertEqual(certificate_service.age_segments("", ""), [])
        self.assertEqual(certificate_service.age_segments("0", "0"), [])

    # ---------- dati della pratica ----------
    def test_owner_animal_species_come_from_the_practice(self):
        pid = self.make_practice(animal="Fido", species="Cane", first="Anna", last="Bianchi")
        text = self.text(pid)
        self.assertIn("Egregio/a Sig/ra Anna Bianchi", text)
        self.assertIn("Nome:  Fido", text)
        self.assertIn("Specie: cane", text)

    def test_special_characters_are_escaped_and_valid_xml(self):
        pid = self.make_practice(animal="Fido & <Co>", last="D'Angelo & Figli")
        status, _h, body, _p = self.generate(pid)
        self.assertEqual(status, 200)
        text = "\n".join(self.lines(body))
        self.assertIn("Fido & <Co>", text)
        self.assertIn("D'Angelo & Figli", text)
        from xml.dom import minidom
        minidom.parseString(zipfile.ZipFile(io.BytesIO(body)).read("word/document.xml"))

    def test_no_placeholder_is_left_visible(self):
        _s, _h, body, _p = self.generate(self.make_practice())
        xml = zipfile.ZipFile(io.BytesIO(body)).read("word/document.xml").decode("utf-8")
        self.assertNotIn("{{", xml)
        self.assertNotIn("}}", xml)

    # ---------- data del certificato = data di GENERAZIONE (Europe/Rome) ----------
    def test_certificate_date_is_generation_day_not_cremation_day(self):
        cycle_id = self.make_cycle("2026-09-20")
        pid = self.make_practice(pickup="2026-09-20", cycle_id=cycle_id)
        with app.db() as conn:
            conn.execute("UPDATE practices SET ddt_date='2026-09-20',created_at='2026-09-20T09:00:00' WHERE id=?", (pid,))
        text = self.text(pid, today=(2026, 9, 24))
        self.assertIn("in data 24.09.2026", text)
        self.assertNotIn("20.09.2026", text)
        self.assertNotIn("2026-09-20", text)

    def test_regeneration_uses_the_new_generation_day(self):
        pid = self.make_practice()
        self.assertIn("in data 24.09.2026", self.text(pid, today=(2026, 9, 24)))
        second = self.text(pid, today=(2026, 9, 25))
        self.assertIn("in data 25.09.2026", second)
        self.assertNotIn("24.09.2026", second)

    def test_date_uses_rome_timezone_not_utc(self):
        # 23:30 UTC del 24/09 = 01:30 del 25/09 a Roma (CEST): il certificato e' del 25
        pid = self.make_practice()
        sent = {}
        written = []
        self.handler.send_response = lambda status: sent.update(status=status)
        self.handler.send_header = lambda k, v: None
        self.handler.end_headers = lambda: None
        self.handler.wfile = type("W", (), {"write": staticmethod(lambda data: written.append(data))})()
        from datetime import timezone
        real_datetime = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                moment = real_datetime(2026, 9, 24, 23, 30, tzinfo=timezone.utc)
                return moment.astimezone(tz) if tz else moment

        with patch.object(app, "datetime", FrozenDateTime):
            self.handler.cremation_certificate(self.admin, pid)
        self.assertIn("in data 25.09.2026", "\n".join(self.lines(b"".join(written))))

    # ---------- sede ----------
    def test_site_comes_from_v1_branches_plant_logic(self):
        self.assertIn("Pet Paradise” di Livorno", self.text(self.make_practice(branch="Livorno")))
        # Empoli e' IMPRESA FUNEBRE in BRANCHES: l'impianto di cremazione e' il FORNO CREMATORIO
        self.assertIn("Pet Paradise” di Livorno", self.text(self.make_practice(number="CR-000130", branch="Empoli")))
        self.assertEqual(certificate_service.plant_branch_name("Empoli", app.BRANCHES), "Livorno")
        self.assertEqual(certificate_service.plant_branch_name("", app.BRANCHES), "Livorno")
        custom = {"Pisa": {"plant_type": "FORNO CREMATORIO"}, "Empoli": {"plant_type": "IMPRESA FUNEBRE"}}
        self.assertEqual(certificate_service.plant_branch_name("Empoli", custom), "Pisa")
        self.assertEqual(certificate_service.plant_branch_name("Pisa", custom), "Pisa")
        two = {"A": {"plant_type": "FORNO CREMATORIO"}, "B": {"plant_type": "FORNO CREMATORIO"}}
        self.assertEqual(certificate_service.plant_branch_name("B", two), "B")
        self.assertEqual(certificate_service.plant_branch_name("", two), "")

    # ---------- dati mancanti ----------
    def test_missing_data_blocks_generation_with_clear_message(self):
        cases = (
            (dict(first="", last=""), "Manca il proprietario nella pratica."),
            (dict(animal=""), "Manca il nome dell&#x27;animale."),
            (dict(years="", months=""), "Manca l&#x27;età dell&#x27;animale."),
            (dict(years="0", months="0"), "Manca l&#x27;età dell&#x27;animale."),
            (dict(species=""), "Manca la specie."),
            (dict(service="Cremazione collettiva"), "solo la cremazione singola"),
        )
        for index, (overrides, expected) in enumerate(cases):
            pid = self.make_practice(number=f"CR-00090{index}", **overrides)
            status, headers, body, page = self.generate(pid)
            self.assertEqual(status, 422, overrides)
            self.assertEqual(body, b"")
            self.assertNotIn("Content-Disposition", headers)
            self.assertIn(expected, page, overrides)
            self.assertIn(f'/pratiche/{pid}"', page)  # per correggere la pratica

    def test_missing_data_lists_every_missing_item_and_can_be_retried(self):
        pid = self.make_practice(animal="", species="", years="", months="")
        _s, _h, _b, page = self.generate(pid)
        for expected in ("nome dell&#x27;animale", "l&#x27;età", "specie"):
            self.assertIn(expected, page)
        with app.db() as conn:
            conn.execute("UPDATE practices SET animal_name='Luna',species='Gatto',age_years='4' WHERE id=?", (pid,))
        status, _h, body, _p = self.generate(pid)
        self.assertEqual(status, 200)
        self.assertIn("Nome:  Luna", "\n".join(self.lines(body)))

    def test_unknown_or_deleted_practice_is_404(self):
        self.assertEqual(self.generate(99999)[0], 404)
        pid = self.make_practice()
        with app.db() as conn:
            conn.execute("UPDATE practices SET deleted_at='2026-09-20T10:00:00' WHERE id=?", (pid,))
        self.assertEqual(self.generate(pid)[0], 404)

    # ---------- piu' animali nello stesso ciclo: nessuna contaminazione ----------
    def test_animals_in_the_same_cycle_never_mix_data(self):
        cycle_id = self.make_cycle()
        luna = self.make_practice(number="CR-000201", animal="Luna", species="Gatto", years="18", months="6",
                                  first="Proprietario", last="A", cycle_id=cycle_id)
        thor = self.make_practice(number="CR-000202", animal="Thor", species="Cane", years="9", months="",
                                  first="Proprietario", last="B", cycle_id=cycle_id)
        a = self.text(luna, today=(2026, 9, 24))
        b = self.text(thor, today=(2026, 9, 25))
        self.assertIn("Egregio/a Sig/ra Proprietario A", a)
        self.assertIn("Nome:  Luna", a)
        self.assertIn("Età: 18 anni e 6 mesi", a)
        self.assertIn("Specie: gatto", a)
        self.assertIn("in data 24.09.2026", a)
        self.assertIn("Egregio/a Sig/ra Proprietario B", b)
        self.assertIn("Nome:  Thor", b)
        self.assertIn("Età: 9 anni ", b)
        self.assertNotIn("mesi", b)
        self.assertIn("Specie: cane", b)
        self.assertIn("in data 25.09.2026", b)
        for forbidden in ("Thor", "Proprietario B", "9 anni", "cane"):
            self.assertNotIn(forbidden, a)
        for forbidden in ("Luna", "Proprietario A", "18 anni", "gatto"):
            self.assertNotIn(forbidden, b)

    def test_three_animals_in_one_cycle_each_get_their_own_button_and_certificate(self):
        cycle_id = self.make_cycle("2026-09-24")
        pids = {
            "Luna": self.make_practice(number="CR-000301", animal="Luna", species="Gatto", years="18", months="6", last="Uno", cycle_id=cycle_id),
            "Milo": self.make_practice(number="CR-000302", animal="Milo", species="Gatto", years="2", months="", last="Due", cycle_id=cycle_id),
            "Thor": self.make_practice(number="CR-000303", animal="Thor", species="Cane", years="", months="8", last="Tre", cycle_id=cycle_id),
        }
        for view in ("", "&vista=settimana"):
            rendered = []
            self.handler.path = f"/programma-cremazioni?data=2026-09-24{view}"
            self.handler.send_html = lambda content, *a: rendered.append(content)
            self.handler.cremation_schedule(self.admin)
            page = rendered[-1]
            self.assertEqual(page.count("📄 Crea certificato"), 3, view)
            for name, pid in pids.items():
                self.assertIn(f'href="/pratiche/{pid}/certificato-cremazione"', page)
                self.assertIn(f"per {name}", page)
        expected = {"Luna": ("Uno", "18 anni e 6 mesi", "gatto"), "Milo": ("Due", "2 anni", "gatto"), "Thor": ("Tre", "8 mesi", "cane")}
        for name, pid in pids.items():
            text = self.text(pid)
            last, age, species = expected[name]
            self.assertIn(f"Sig/ra Mario {last}", text)
            self.assertIn(f"Nome:  {name}", text)
            self.assertIn(f"Età: {age}", text)
            self.assertIn(f"Specie: {species}", text)
            for other in pids:
                if other != name:
                    self.assertNotIn(other, text)

    def test_reserved_not_yet_affidato_animal_has_no_certificate_button(self):
        # nessuna pratica: nessun dato reale da cui generare il certificato
        stamp = app.now()
        with app.db() as conn:
            cycle_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-09-30", "pianificato", "08:30", "09:30", stamp, stamp)).lastrowid
            event_id = conn.execute(
                """INSERT INTO calendar_events(event_type,title,created_by,created_at,updated_at,event_status,start_at,end_at)
                   VALUES('Ritiro','x',?,?,?,'Da ritirare','2026-09-30T09:00:00','2026-09-30T09:30:00')""",
                (self.admin["id"], stamp, stamp)).lastrowid
            conn.execute("INSERT INTO calendar_event_animals(event_id,name,species,weight,cremation_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (event_id, "Rocky", "Cane", "40", "Singola", stamp, stamp))
            conn.execute("INSERT INTO cremation_cycle_reservations(cycle_id,calendar_event_id,created_at) VALUES(?,?,?)", (cycle_id, event_id, stamp))
        rendered = []
        self.handler.path = "/programma-cremazioni?data=2026-09-30"
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.cremation_schedule(self.admin)
        self.assertIn("NON ANCORA AFFIDATO", rendered[-1])
        self.assertNotIn("Crea certificato", rendered[-1])

    # ---------- documento Word reale / template ----------
    def test_output_is_a_real_docx_with_correct_headers_and_filename(self):
        pid = self.make_practice(animal="Luna", number="CR-000123")
        status, headers, body, _p = self.generate(pid)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="Certificato_Cremazione_Luna_CR000123.docx"')
        self.assertEqual(headers["Content-Length"] if "Content-Length" in headers else str(len(body)), str(len(body)))
        self.assertTrue(body.startswith(b"PK"))
        archive = zipfile.ZipFile(io.BytesIO(body))
        self.assertIsNone(archive.testzip())
        self.assertIn("word/document.xml", archive.namelist())

    def test_filename_is_ascii_and_safe(self):
        self.assertEqual(certificate_service.certificate_filename({"animal_name": "Fido è/../x", "practice_number": "CR-000123"}),
                         "Certificato_Cremazione_Fido_e_x_CR000123.docx")
        self.assertEqual(certificate_service.certificate_filename({"animal_name": "", "practice_number": "CR-000123"}),
                         "Certificato_Cremazione_Animale_CR000123.docx")

    def test_original_template_is_unchanged_and_all_other_parts_are_identical(self):
        template = certificate_service.TEMPLATE_PATH
        before = hashlib.sha256(template.read_bytes()).hexdigest()
        _s, _h, body, _p = self.generate(self.make_practice())
        self.assertEqual(hashlib.sha256(template.read_bytes()).hexdigest(), before)
        source = zipfile.ZipFile(template)
        output = zipfile.ZipFile(io.BytesIO(body))
        self.assertEqual(source.namelist(), output.namelist())
        for name in source.namelist():
            if name != "word/document.xml":
                self.assertEqual(source.read(name), output.read(name), name)  # logo, immagini, stili, header/footer
        self.assertTrue(any(n.startswith("word/media/") for n in source.namelist()))

    def test_versioned_template_has_exactly_the_placeholders_and_no_personal_data(self):
        xml = zipfile.ZipFile(certificate_service.TEMPLATE_PATH).read("word/document.xml").decode("utf-8")
        for name in certificate_service.PLACEHOLDERS:
            self.assertEqual(xml.count("{{" + name + "}}"), 1, name)
        self.assertEqual(len(re.findall(r"\{\{[A-Z_]+\}\}", xml)), len(certificate_service.PLACEHOLDERS))
        for personal in ("Galdoporpora", "Raffaella"):
            self.assertNotIn(personal, xml)
        # frasi e stile del modello ufficiale preservati
        for phrase in ("Certificato di garanzia di avvenuta cremazione", "Egregio/a", "Si attesta che in data",
                       "cremazione", "Presso l", "Un vero amico lascia", "Buon ponte"):
            self.assertIn(phrase, xml)
        self.assertIn("Edwardian Script ITC", xml)

    # ---------- sola lettura ----------
    def test_generation_is_read_only(self):
        cycle_id = self.make_cycle()
        pid = self.make_practice(cycle_id=cycle_id)

        def snapshot():
            with app.db() as conn:
                tables = {}
                for table in ("practices", "practice_history", "payment_movements", "balance_movements",
                              "calendar_events", "cremation_cycles", "cremation_cycle_reservations",
                              "whatsapp_messages", "notifications", "practice_items", "clients"):
                    try:
                        tables[table] = [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
                    except Exception:
                        tables[table] = None
                return tables

        before = snapshot()
        self.generate(pid)
        self.generate(pid, today=(2026, 9, 25))
        self.assertEqual(snapshot(), before)

    def test_generation_does_not_log_personal_data(self):
        import contextlib
        pid = self.make_practice(first="Raffaella", last="Segretissima", animal="Nomeprivato")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            self.generate(pid)
        self.assertNotIn("Segretissima", captured.getvalue())
        self.assertNotIn("Nomeprivato", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
