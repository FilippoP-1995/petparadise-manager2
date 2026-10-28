import html as html_lib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import app


class AvvisoAssistitaTests(unittest.TestCase):
    # Richiesta esplicita dell'utente (28/10/2026): nuovo messaggio
    # preimpostato "Avviso assistita"; nome del proprietario e dell'animale
    # dalla pratica, data e ora dal ciclo di cremazione in cui si trova
    # l'animale. Esempi dell'utente: Maria/Zoe venerdi' 30 ottobre ore 12;
    # Greta/Pepita (CR-000378) giovedi' 29 ottobre ore 15.

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        self.pages = []
        self.handler.send_html = lambda html, *a: self.pages.append(html)
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def practice(self, number, owner, animal, cycle=None):
        stamp = app.now()
        with app.db() as conn:
            cycle_id = None
            if cycle:
                cycle_id = conn.execute(
                    "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (cycle[0], "in_attesa", cycle[1], cycle[2], stamp, stamp)).lastrowid
            return conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   owner_first_name,owner_last_name,owner_phone,animal_name,species,service_type,payment_status,cremation_cycle_id)
                   VALUES(?,'Privato','Livorno','In programma',?,?,?,?,'Rossi','3331234567',?,'Cane','Cremazione singola','Da saldare',?)""",
                (number, stamp, stamp, self.admin["id"], owner, animal, cycle_id)).lastrowid

    def quick_vars(self, pid):
        self.handler.path = f"/pratiche/{pid}"
        self.handler.practice(self.admin, pid)
        raw = self.pages[-1].split('data-qwa="', 1)[1].split('"', 1)[0]
        return json.loads(html_lib.unescape(raw))["vars"]

    def template(self):
        with app.db() as conn:
            return next(t for t in app.wa_quick_templates(conn) if t["id"] == "avviso_assistita")

    def fill(self, body, qvars):
        node = shutil.which("node")
        if not node:
            self.skipTest("node non disponibile")
        js = app.APP_JS
        script = (js[js.index("function ppmQwaNeedsPractice("):js.index("function ppmQuickWa(")]
                  + f"\nconst body={json.dumps(body)},vars={json.dumps(qvars)};"
                  + "console.log(JSON.stringify({cycle:ppmQwaNeedsCycle(body,vars),text:ppmQwaFill(body,vars)}));")
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_template_exists_with_the_users_text(self):
        template = self.template()
        self.assertEqual(template["title"], "Avviso assistita")
        self.assertEqual(template["body"],
                         "Ciao {nome_cliente}, scriviamo dallo staff di Pet Paradise Cremazione Animali per comunicarvi che la cremazione di "
                         "{nome_animale} è in programma per {data_cremazione} alle ore {ora_cremazione}. Vi aspettiamo quindi presso la nostra "
                         "sede di Livorno in Via dei Materassai, 10, grazie")

    def test_users_examples_take_date_and_time_from_the_cycle(self):
        body = self.template()["body"]
        zoe = self.practice("CR-ZOE", "Maria", "Zoe", ("2026-10-30", "12:00", "13:00"))
        pepita = self.practice("CR-000378", "Greta", "Pepita", ("2026-10-29", "15:00", "16:00"))
        expected = {
            zoe: "Ciao Maria, scriviamo dallo staff di Pet Paradise Cremazione Animali per comunicarvi che la cremazione di Zoe è in programma "
                 "per Venerdì 30 Ottobre alle ore 12. Vi aspettiamo quindi presso la nostra sede di Livorno in Via dei Materassai, 10, grazie",
            pepita: "Ciao Greta, scriviamo dallo staff di Pet Paradise Cremazione Animali per comunicarvi che la cremazione di Pepita è in programma "
                    "per Giovedì 29 Ottobre alle ore 15. Vi aspettiamo quindi presso la nostra sede di Livorno in Via dei Materassai, 10, grazie",
        }
        for pid, text in expected.items():
            filled = self.fill(body, self.quick_vars(pid))
            self.assertEqual(filled, {"cycle": False, "text": text})

    def test_half_hour_and_practice_without_a_cycle(self):
        half = self.practice("CR-HALF", "Anna", "Fido", ("2026-11-02", "09:30", "10:30"))
        qvars = self.quick_vars(half)
        self.assertEqual((qvars["data_cremazione"], qvars["ora_cremazione"]), ("Lunedì 2 Novembre", "9:30"))
        none = self.practice("CR-NONE", "Anna", "Fido")
        qvars = self.quick_vars(none)
        self.assertEqual((qvars["data_cremazione"], qvars["ora_cremazione"]), ("", ""))
        # Senza ciclo il messaggio resta nell'elenco ma non e' selezionabile.
        self.assertTrue(self.fill(self.template()["body"], qvars)["cycle"])
        self.assertIn("Disponibile quando l'animale è in un ciclo di cremazione", app.APP_JS)


if __name__ == "__main__":
    unittest.main()
