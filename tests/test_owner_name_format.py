import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import app
import partner_service
from calendar_service import format_address, format_person_name


class OwnerNameAddressFormatTests(unittest.TestCase):
    # Richiesta esplicita dell'utente (02/11/2026): la regola dei nomi degli
    # animali vale anche per nome e cognome del proprietario e per via e
    # comune. Scelte dell'utente: negli indirizzi le parole brevi restano
    # minuscole ("Via dei Materassai"); ovunque si salvano (pratiche,
    # clienti, calendario, portale); solo da ora in poi, i dati gia' salvati
    # non vengono corretti in automatico.

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

    def test_rules(self):
        names = {"MARIO": "Mario", "rossi": "Rossi", "DE LUCA": "De Luca", "d'angelo": "D'Angelo",
                 "maria grazia": "Maria Grazia", "McQueen": "McQueen", "": ""}
        for raw, expected in names.items():
            self.assertEqual(format_person_name(raw), expected, raw)
        addresses = {
            "VIA DEI MATERASSAI, 10": "Via dei Materassai, 10", "via roma 1": "Via Roma 1",
            "V.LE ITALIA 12/B": "V.le Italia 12/B", "castiglione della pescaia": "Castiglione della Pescaia",
            "REGGIO NELL'EMILIA": "Reggio nell'Emilia", "Via Roma 1, Livorno (LI)": "Via Roma 1, Livorno (LI)",
            "via d'azeglio 3": "Via d'Azeglio 3", "LOC. LE CASCINE": "Loc. Le Cascine", "VIA A. VOLTA 3": "Via A. Volta 3",
            "borgo a mozzano": "Borgo a Mozzano", "Via Della Vittoria": "Via della Vittoria", "": "",
        }
        for raw, expected in addresses.items():
            self.assertEqual(format_address(raw), expected, raw)

    def practice_form(self, **changes):
        form = {"operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato", "provenance": "L",
                "owner_first_name": "MARIA GRAZIA", "owner_last_name": "de luca", "owner_phone": "3331234567",
                "owner_tax_code": "DLCMGR80A41E625X", "owner_street": "VIA DEI MATERASSAI, 10", "owner_city": "LIVORNO",
                "owner_province": "LI", "owner_zip": "57100", "owner_company": "ACME SRL", "owner_email": "Maria@Example.com",
                "species": "Gatto"}
        form.update(changes)
        return form

    def test_practice_creation_edit_and_client(self):
        self.handler.form = lambda: self.practice_form()
        self.handler.path = "/nuova"
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].rsplit("/", 1)[-1])
        with app.db() as conn:
            p = conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
            client = conn.execute("SELECT * FROM clients WHERE id=?", (p["client_id"],)).fetchone()
        self.assertEqual((p["owner_first_name"], p["owner_last_name"], p["owner_street"], p["owner_city"]),
                         ("Maria Grazia", "De Luca", "Via dei Materassai, 10", "Livorno"))
        # Non toccati: provincia, CAP, ragione sociale, codice fiscale, email.
        self.assertEqual((p["owner_province"], p["owner_zip"], p["owner_company"], p["owner_tax_code"], p["owner_email"]),
                         ("LI", "57100", "ACME SRL", "DLCMGR80A41E625X", "Maria@Example.com"))
        self.assertEqual((client["first_name"], client["last_name"], client["street"], client["city"]),
                         ("Maria Grazia", "De Luca", "Via dei Materassai, 10", "Livorno"))
        # "Salva modifiche" e salvataggio automatico passano dalla stessa regola.
        normalized = app.App.normalized_fields(self.handler, self.practice_form(owner_first_name="giulia", owner_city="san vincenzo"))
        self.assertEqual((normalized["owner_first_name"], normalized["owner_city"]), ("Giulia", "San Vincenzo"))

    def test_client_registry_save(self):
        self.handler.form = lambda: {"first_name": "PAOLO", "last_name": "BIANCHI", "company_name": "", "phone": "333", "phone_2": "",
                                     "email": "", "tax_code": "", "vat_number": "", "street": "piazza della repubblica 4",
                                     "city": "CAMPIGLIA MARITTIMA", "province": "LI", "zip": "57021", "notes": ""}
        self.handler.save_client(self.admin)
        client_id = int(self.redirects[-1].rsplit("/", 1)[-1])
        with app.db() as conn:
            client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        self.assertEqual((client["first_name"], client["last_name"], client["street"], client["city"], client["province"]),
                         ("Paolo", "Bianchi", "Piazza della Repubblica 4", "Campiglia Marittima", "LI"))

    def test_calendar_event_and_quick_edit(self):
        form = {"event_type": "Ritiro", "operator_name": "Serena", "title": "", "zone": "Livorno", "location_type": "Privato",
                "address": "VIA DELLA VITTORIA 5, ROSIGNANO (LI)", "start_date": "2026-11-05", "start_time": "09:30",
                "end_date": "2026-11-05", "end_time": "10:30", "event_status": "Da confermare", "payment_status": "Da pagare",
                "payment_amount": "0", "client_first_name": "ANNA", "client_last_name": "NERI", "animals_json": "[]", "estimate_json": "[]"}
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin)
        event_id = int(self.redirects[-1].rsplit("/", 1)[-1])
        with app.db() as conn:
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual((event["client_first_name"], event["client_last_name"], event["address"]),
                         ("Anna", "Neri", "Via della Vittoria 5, Rosignano (LI)"))
        self.handler.form = lambda: {"client_first_name": "luca", "client_last_name": "DI STEFANO", "client_phone": ""}
        self.handler.path = f"/calendario/{event_id}/cliente"
        self.handler.calendar_event_action(self.admin, event_id, "cliente")
        with app.db() as conn:
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual((event["client_first_name"], event["client_last_name"]), ("Luca", "Di Stefano"))

    def test_portal_request(self):
        stamp = app.now()
        with app.db() as conn:
            vet_id = conn.execute("INSERT INTO veterinarians(clinic_name,short_name,active,created_at,updated_at) VALUES(?,?,1,?,?)",
                                  ("Clinica Prova", "PROVA", stamp, stamp)).lastrowid
            clinic_id = partner_service.create_clinic(conn, veterinarian_id=vet_id)
            request, _ = partner_service.create_request(
                conn, clinic_id=clinic_id, user_id=None, client_request_id="nomi-prop-1", mode="ritiro_domicilio",
                service_type="Cremazione singola", species="Cane", weight="12 kg", owner_first_name="GRETA",
                owner_last_name="verdi", owner_phone="3330000000", pickup_address="VIA DEI PINI 14, LIVORNO",
                proposed_date=(date.today() + timedelta(days=1)).isoformat(), proposed_from="09:00", proposed_to="12:00")
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (request["calendar_event_id"],)).fetchone()
        self.assertEqual((request["owner_first_name"], request["owner_last_name"], request["pickup_address"]),
                         ("Greta", "Verdi", "Via dei Pini 14, Livorno"))
        self.assertEqual((event["client_first_name"], event["client_last_name"], event["address"]),
                         ("Greta", "Verdi", "Via dei Pini 14, Livorno"))

    def test_saved_data_is_not_rewritten_at_startup(self):
        stamp = app.now()
        with app.db() as conn:
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   owner_first_name,owner_last_name,owner_street,owner_city) VALUES('CR-OLD','Privato','Livorno','Ritirato',?,?,?,
                   'MARIO','ROSSI','VIA ROMA 1','LIVORNO')""", (stamp, stamp, self.admin["id"])).lastrowid
        app.init_db()
        with app.db() as conn:
            p = conn.execute("SELECT owner_first_name,owner_last_name,owner_street,owner_city FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(tuple(p), ("MARIO", "ROSSI", "VIA ROMA 1", "LIVORNO"))


if __name__ == "__main__":
    unittest.main()
