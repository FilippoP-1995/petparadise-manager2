import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import app


class _PracticeForm(HTMLParser):
    """Valori che il browser invierebbe con il modulo "Modifica pratica"."""

    def __init__(self):
        super().__init__()
        self.fields = {}
        self.inside = False
        self.select = self.option = self.textarea = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "practiceForm":
            self.inside = True
        if not self.inside:
            return
        name = attrs.get("name")
        if tag == "input" and name:
            if attrs.get("type") in ("checkbox", "radio"):
                if "checked" in attrs:
                    self.fields[name] = attrs.get("value", "on")
            else:
                self.fields[name] = attrs.get("value", "")
        elif tag == "select" and name:
            self.select = name
            self.fields.setdefault(name, "")
        elif tag == "option" and self.select and "selected" in attrs:
            if "value" in attrs:
                self.fields[self.select] = attrs["value"]
            else:
                self.option = self.select
                self.fields[self.select] = ""
        elif tag == "textarea" and name:
            self.textarea = name
            self.fields[name] = ""

    def handle_data(self, data):
        if self.textarea:
            self.fields[self.textarea] += data
        if self.option:
            self.fields[self.option] += data

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None
        elif tag == "option":
            self.option = None
        elif tag == "textarea":
            self.textarea = None
        elif tag == "form":
            self.inside = False


class EditPaymentDateTests(unittest.TestCase):
    # Segnalazione dell'utente (27/10/2026, CR-000042): pratica con acconto
    # D 100 e TOTALE D 240; il cliente paga su W, quindi TOTALE D cancellato
    # e acconto spostato su W (100 contanti, con data). "Salva modifiche"
    # rispondeva "La data pagamento/acconto è obbligatoria e deve essere
    # valida." perche' la pratica non aveva una data dell'acconto salvata
    # (campo nascosto economic_at vuoto), anche se la data era compilata.

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
        self.pages = []
        self.handler.redirect = lambda path: self.redirects.append(path)
        self.handler.send_html = lambda html, *a: self.pages.append(html)
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def practice_with_d_deposit(self, stored_date=True):
        form = {"operator_name": "GIANLUCA", "service_type": "Cremazione singola", "request_origin": "Privato", "provenance": "L",
                "owner_first_name": "Mario", "owner_last_name": "Verdi", "owner_phone": "3471234567", "owner_tax_code": "Y",
                "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100", "species": "Gatto",
                "price_cremation": "270", "total_text": "240", "acconto_d_totale": "100", "acconto_d_data": "2026-07-18"}
        self.handler.form = lambda: form
        self.handler.path = "/nuova"
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].rsplit("/", 1)[-1])
        if not stored_date:
            with app.db() as conn:
                conn.execute("UPDATE practices SET deposit_paid_at=NULL WHERE id=?", (pid,))
        return pid

    def edit_form(self, pid):
        self.handler.path = f"/pratiche/{pid}/modifica"
        self.handler.edit_page(self.admin, pid)
        parser = _PracticeForm()
        parser.feed(self.pages[-1])
        return parser.fields

    def submit(self, pid, form):
        self.redirects.clear()
        self.pages.clear()
        self.handler.form = lambda: form
        self.handler.edit_submit(self.admin, pid)
        if self.pages:
            match = re.search(r'class="flash[^"]*">([^<]*)', self.pages[-1])
            return match.group(1) if match else "errore"
        return None

    def move_deposit_to_w(self, form):
        form.update({"total_text": "", "acconto_d_totale": "", "acconto_d_data": "",
                     "acconto_w_totale": "100", "acconto_w_data": "2026-07-18", "acconto_w_modalita": "Contanti"})
        return form

    def test_practice_without_a_stored_deposit_date_saves_the_switch_to_w(self):
        pid = self.practice_with_d_deposit(stored_date=False)
        form = self.edit_form(pid)
        self.assertEqual((form["economic_at"], form["payment_status"]), ("", "Acconto"))
        self.assertIsNone(self.submit(pid, self.move_deposit_to_w(form)))
        with app.db() as conn:
            practice = conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
            ledger = [(r["category"], r["amount_cents"]) for r in conn.execute(
                "SELECT category,amount_cents FROM balance_movements WHERE practice_id=? ORDER BY id", (pid,))]
        self.assertEqual((practice["payment_status"], practice["total_text"], practice["deposit"], practice["deposit_final"], practice["deposit_paid_at"]),
                         ("Acconto", "", "100.00", "0.00", "2026-07-18"))
        self.assertEqual(ledger, [("D", 10000), ("D", -10000), ("W", 10000)])

    def test_a_stored_date_still_cannot_be_cleared_or_made_invalid(self):
        pid = self.practice_with_d_deposit(stored_date=True)
        form = self.edit_form(pid)
        self.assertEqual(form["economic_at"], "2026-07-18")
        for value in ("", "18/07"):
            changed = dict(form, economic_at=value)
            self.assertEqual(self.submit(pid, changed), "La data pagamento/acconto è obbligatoria e deve essere valida.", value)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT deposit_paid_at FROM practices WHERE id=?", (pid,)).fetchone()["deposit_paid_at"], "2026-07-18")
        # Con la data salvata lo stesso cambio di circuito funziona come prima.
        self.assertIsNone(self.submit(pid, self.move_deposit_to_w(form)))


if __name__ == "__main__":
    unittest.main()
