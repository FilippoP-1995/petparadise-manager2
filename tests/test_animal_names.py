import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import app
import partner_service
from calendar_service import format_animal_name


class AnimalNameFormatTests(unittest.TestCase):
    # Richiesta esplicita dell'utente (25/10/2026): i nomi degli animali sono
    # sempre nello stesso formato, iniziale maiuscola e resto minuscolo, anche
    # nei nomi doppi ("Milù Bella"), in pratiche, calendario e portale; anche
    # i nomi gia' inseriti vengono corretti, con l'originale nello storico.

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        self.redirects = []
        self.handler.redirect = lambda path: self.redirects.append(path)
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def test_format_rule_capitalizes_every_name_and_lowers_the_rest(self):
        cases = {
            "FIDO": "Fido", "fido": "Fido", "  fIDO  ": "Fido",
            "MILÙ BELLA": "Milù Bella", "milù   bella": "Milù Bella",
            "mary-jane": "Mary-Jane", "d'artagnan": "D'Artagnan", "o’malley": "O’Malley",
            "ÈLIO": "Èlio", "rex2": "Rex2", "": "", None: "",
        }
        for raw, expected in cases.items():
            self.assertEqual(format_animal_name(raw), expected, raw)

    def practice_form(self, **changes):
        form = {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "provenance": "L", "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI",
            "owner_zip": "57100", "species": "Cane", "animal_name": "MILÙ BELLA", "animal2_name": "rex",
        }
        form.update(changes)
        return form

    def create_practice(self, **changes):
        self.handler.form = lambda: self.practice_form(**changes)
        self.handler.create_practice(self.admin)
        return int(self.redirects[-1].rsplit("/", 1)[-1])

    def test_new_practice_saves_both_animal_names_in_the_single_format(self):
        pid = self.create_practice()
        with app.db() as conn:
            row = conn.execute("SELECT animal_name, animal2_name FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual((row["animal_name"], row["animal2_name"]), ("Milù Bella", "Rex"))

    def test_autosave_and_full_edit_also_format_the_animal_name(self):
        pid = self.create_practice()
        with app.db() as conn:
            stamp = conn.execute("SELECT updated_at FROM practices WHERE id=?", (pid,)).fetchone()["updated_at"]
        captured = []
        self.handler.send_json = lambda obj, status=200: captured.append((obj, status))
        self.handler.form = lambda: {"updated_at": stamp, "changes_json": json.dumps({"animal_name": "BRICIOLA DI MARE"})}
        self.handler.practice_autosave(self.admin, pid)
        self.assertEqual(captured[-1][1], 200, captured[-1])
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT animal_name FROM practices WHERE id=?", (pid,)).fetchone()["animal_name"], "Briciola Di Mare")
        self.assertEqual(app.App.normalized_fields(self.handler, self.practice_form(animal_name="tobia", animal2_name="ARGO"))["animal_name"], "Tobia")
        self.assertEqual(app.App.normalized_fields(self.handler, self.practice_form(animal_name="tobia", animal2_name="ARGO"))["animal2_name"], "Argo")

    def test_calendar_event_animals_and_delivery_animal_name_are_formatted(self):
        form = {
            "event_type": "Riconsegna", "operator_name": "Serena", "title": "", "zone": "Livorno",
            "destination_site": "", "animal_name": "LUNA ROSSA", "location_type": "", "address": "",
            "start_date": "2026-07-15", "start_time": "09:30", "end_date": "2026-07-15", "end_time": "10:30",
            "event_status": "In programma", "payment_status": "Da pagare", "payment_amount": "0",
            "animals_json": json.dumps([{"name": "PEPE", "species": "Gatto"}, {"name": "nala-maria", "species": "Gatto"}]),
            "estimate_json": "[]",
        }
        self.handler.form = lambda: dict(form)
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin)
        event_id = int(self.redirects[-1].rsplit("/", 1)[-1])
        with app.db() as conn:
            event = conn.execute("SELECT animal_name FROM calendar_events WHERE id=?", (event_id,)).fetchone()
            names = [r["name"] for r in conn.execute("SELECT name FROM calendar_event_animals WHERE event_id=? ORDER BY id", (event_id,))]
        self.assertEqual(names, ["Pepe", "Nala-Maria"])
        # In una riconsegna il nome dell'evento e' quello del primo animale.
        self.assertEqual(event["animal_name"], "Pepe")
        # Senza elenco animali vale il nome scritto nel campo dell'evento.
        self.handler.form = lambda: {**form, "animals_json": "[]"}
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin)
        event_id = int(self.redirects[-1].rsplit("/", 1)[-1])
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT animal_name FROM calendar_events WHERE id=?", (event_id,)).fetchone()["animal_name"], "Luna Rossa")

    def test_portal_request_and_its_pickup_event_use_the_single_format(self):
        stamp = app.now()
        with app.db() as conn:
            vet_id = conn.execute(
                "INSERT INTO veterinarians(clinic_name,short_name,active,created_at,updated_at) VALUES(?,?,1,?,?)",
                ("Clinica Prova Nomi", "PROVA NOMI", stamp, stamp)).lastrowid
            clinic_id = partner_service.create_clinic(conn, veterinarian_id=vet_id)
            request, _ = partner_service.create_request(
                conn, clinic_id=clinic_id, user_id=None, client_request_id="nomi-1", mode="ritiro_clinica",
                service_type="Cremazione singola", species="Cane", weight="12 kg", animal_name="BIRBA STELLA",
                proposed_date=(date.today() + timedelta(days=1)).isoformat(), proposed_from="09:00", proposed_to="12:00")
            animals = [r["name"] for r in conn.execute("SELECT name FROM calendar_event_animals WHERE event_id=?", (request["calendar_event_id"],))]
        self.assertEqual(request["animal_name"], "Birba Stella")
        self.assertEqual(animals, ["Birba Stella"])

    def test_startup_repair_formats_saved_names_once_and_keeps_the_original_in_history(self):
        stamp = "2026-05-02T10:00:00"
        with app.db() as conn:
            insert = """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name,animal2_name)
                        VALUES(?,?,?,?,?,?,?,?,?)"""
            wrong = conn.execute(insert, ("CR-NOMI1", "Privato", "Livorno", "Consegnato", stamp, stamp, self.admin["id"], "MILÙ BELLA", "rex")).lastrowid
            right = conn.execute(insert, ("CR-NOMI2", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"], "Fido", "")).lastrowid
            event_id = conn.execute(
                """INSERT INTO calendar_events(event_type,title,animal_name,operator_name,start_at,end_at,event_status,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("Riconsegna", "RICONSEGNA", "NUVOLA", "Serena", "2026-07-15T09:00:00", "2026-07-15T10:00:00", "In programma", self.admin["id"], stamp, stamp)).lastrowid
            conn.execute("INSERT INTO calendar_event_animals(event_id,name,species,created_at,updated_at) VALUES(?,?,?,?,?)",
                         (event_id, "ugo", "Gatto", stamp, stamp))
            vet_id = conn.execute("INSERT INTO veterinarians(clinic_name,active,created_at,updated_at) VALUES(?,1,?,?)",
                                  ("Clinica Riparazione", stamp, stamp)).lastrowid
            clinic_id = partner_service.create_clinic(conn, veterinarian_id=vet_id)
            conn.execute("""INSERT INTO partner_requests(clinic_id,client_request_id,request_code,mode,service_type,animal_name,species,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?)""", (clinic_id, "r1", "RP-NOMI01", "ritiro_clinica", "Cremazione singola", "ARGO", "Cane", stamp, stamp))
            # Database come prima della pubblicazione: correzione non ancora fatta.
            conn.execute("DELETE FROM settings WHERE key='animal_names_formatted'")
        app.init_db()
        app.init_db()  # una volta sola: il secondo avvio non cambia nulla
        with app.db() as conn:
            practice = conn.execute("SELECT * FROM practices WHERE id=?", (wrong,)).fetchone()
            history = conn.execute("SELECT old_value,new_value,note,user_id FROM practice_history WHERE practice_id=? AND event_type='Formato nome animale' ORDER BY id", (wrong,)).fetchall()
            untouched = conn.execute("SELECT COUNT(*) n FROM practice_history WHERE practice_id=? AND event_type='Formato nome animale'", (right,)).fetchone()["n"]
            event = conn.execute("SELECT animal_name FROM calendar_events WHERE id=?", (event_id,)).fetchone()
            event_animal = conn.execute("SELECT name FROM calendar_event_animals WHERE event_id=?", (event_id,)).fetchone()
            event_history = conn.execute("SELECT old_value,new_value FROM calendar_event_history WHERE event_id=? AND action='Formato nome animale' ORDER BY id", (event_id,)).fetchall()
            portal = conn.execute("SELECT animal_name FROM partner_requests WHERE request_code='RP-NOMI01'").fetchone()
        self.assertEqual((practice["animal_name"], practice["animal2_name"]), ("Milù Bella", "Rex"))
        self.assertEqual(practice["updated_at"], stamp)
        self.assertEqual([tuple(r) for r in history], [("MILÙ BELLA", "Milù Bella", "Nome animale", None), ("rex", "Rex", "Nome secondo animale", None)])
        self.assertEqual(untouched, 0)
        self.assertEqual((event["animal_name"], event_animal["name"]), ("Nuvola", "Ugo"))
        self.assertEqual([tuple(r) for r in event_history], [("NUVOLA", "Nuvola"), ("ugo", "Ugo")])
        self.assertEqual(portal["animal_name"], "Argo")


if __name__ == "__main__":
    unittest.main()
