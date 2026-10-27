import re
import tempfile
import unittest
from pathlib import Path

import app


class PaymentCircuitSwitchTests(unittest.TestCase):
    # Richiesta esplicita dell'utente (27/10/2026): pratica creata con il solo
    # totale W, poi il cliente cambia idea e paga su D dalla finestra
    # Pagamento. Se la pratica non ha un TOTALE D lo compila il pagamento:
    # acconto -> Totale D concordato indicato nella finestra; saldo -> tutto
    # l'incassato su D. Il totale W resta solo come riferimento. Un TOTALE D
    # gia' scritto non viene mai toccato.

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        self.responses = []
        self.handler.send_json = lambda obj, status=200: self.responses.append((obj, status))
        self.redirects = []
        self.handler.redirect = lambda path: self.redirects.append(path)
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def w_practice(self, number="CR-W400", total_text=""):
        stamp = app.now()
        with app.db() as conn:
            return conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,species,service_type,price_cremation,total_service,remaining_balance,total_text,payment_status)
                   VALUES(?,'Privato','Livorno','In programma',?,?,?,'Shana','Cane','Cremazione singola','400','400','400',?,'Da saldare')""",
                (number, stamp, stamp, self.admin["id"], total_text)).lastrowid

    def pay(self, pid, kind, amount, channel, agreed="", token="k"):
        form = {"ajax": "1", "macroarea": kind, f"{kind}_data": "2026-10-27", f"{kind}_totale": amount,
                f"{kind}_circuito": channel, f"{kind}_modalita": "Contanti", "balance_idempotency_key": f"{token}-{kind}-{amount}"}
        if agreed:
            form[f"{kind}_totale_d_concordato"] = agreed
        self.handler.form = lambda: form
        self.handler.save_payment_macroarea(self.admin, pid)
        return self.responses[-1]

    def row(self, pid):
        with app.db() as conn:
            return conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()

    def balance(self, pid):
        with app.db() as conn:
            return [(r["category"], r["amount_cents"]) for r in conn.execute(
                "SELECT category,amount_cents FROM balance_movements WHERE practice_id=? ORDER BY id", (pid,))]

    def summary(self, pid):
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = f"/pratiche/{pid}"
        self.handler.practice(self.admin, pid)
        match = re.search(r'<small>(Totale pagato|Totale da pagare) ([WD])</small><b>([^<]*)</b>', rendered[-1])
        return match.groups(), rendered[-1]

    def test_saldo_on_d_without_total_d_sets_total_d_to_what_was_collected(self):
        pid = self.w_practice()
        response, status = self.pay(pid, "saldo", "330", "D")
        self.assertEqual(status, 200, response)
        practice = self.row(pid)
        self.assertEqual((practice["total_text"], practice["deposit_final"], practice["remaining_final"], practice["payment_status"]),
                         ("330.00", "330.00", "0.00", "Pagato"))
        self.assertEqual(self.balance(pid), [("D", 33000)])
        (label, channel, amount), page = self.summary(pid)
        self.assertEqual((label, channel, amount), ("Totale pagato", "D", "€ 330,00"))
        with app.db() as conn:
            history = conn.execute("SELECT old_value,new_value,note FROM practice_history WHERE practice_id=? AND event_type='Modifica TOTALE D'", (pid,)).fetchone()
        self.assertEqual((history["old_value"], history["new_value"]), ("", "330.00"))
        self.assertIn("incassato sul circuito D", history["note"])

    def test_acconto_on_d_needs_the_agreed_total_d_and_never_half_saves(self):
        pid = self.w_practice()
        response, status = self.pay(pid, "acconto", "100", "D")
        self.assertEqual(status, 400)
        self.assertIn("Totale D concordato", response["error"])
        response, status = self.pay(pid, "acconto", "100", "D", agreed="80")
        self.assertEqual(status, 400)
        self.assertIn("non può essere inferiore all'acconto", response["error"])
        self.assertEqual(self.balance(pid), [])
        self.assertFalse(self.row(pid)["total_text"])
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM payment_movements WHERE practice_id=?", (pid,)).fetchone()["n"], 0)

    def test_acconto_with_agreed_total_then_saldo_closes_the_d_circuit(self):
        pid = self.w_practice()
        self.assertEqual(self.pay(pid, "acconto", "100", "D", agreed="330")[1], 200)
        practice = self.row(pid)
        self.assertEqual((practice["total_text"], practice["deposit_final"], practice["remaining_final"], practice["payment_status"]),
                         ("330.00", "100.00", "230.00", "Acconto"))
        self.assertEqual(self.summary(pid)[0], ("Totale da pagare", "D", "€ 230,00"))
        self.assertEqual(self.pay(pid, "saldo", "230", "D")[1], 200)
        practice = self.row(pid)
        # Il Totale D concordato resta: il saldo non lo ricalcola.
        self.assertEqual((practice["total_text"], practice["deposit_final"], practice["remaining_final"], practice["payment_status"]),
                         ("330.00", "330.00", "0.00", "Pagato"))
        self.assertEqual(self.balance(pid), [("D", 10000), ("D", 23000)])

    def test_existing_total_d_and_w_payments_are_left_alone(self):
        with_total = self.w_practice("CR-D350", total_text="350")
        self.pay(with_total, "saldo", "330", "D")
        self.assertEqual((self.row(with_total)["total_text"], self.row(with_total)["remaining_final"]), ("350", "20.00"))
        w_only = self.w_practice("CR-W")
        self.pay(w_only, "saldo", "400", "W")
        self.assertFalse(self.row(w_only)["total_text"])
        self.assertEqual(self.summary(w_only)[0], ("Totale pagato", "W", "€ 400,00"))

    def test_split_payment_w_then_d_stays_as_before(self):
        # Pagamento diviso (scelta dell'utente: resta come prima): acconto su W
        # con fattura, saldo su D. Nessun Totale D automatico, il W resta il
        # circuito della pratica con la sua rimanenza.
        pid = self.w_practice("CR-SPLIT")
        self.assertEqual(self.pay(pid, "acconto", "100", "W")[1], 200)
        _, page = self.summary(pid)
        self.assertNotIn('name="acconto_totale_d_concordato"', page)
        self.assertNotIn("il TOTALE D diventa quanto incassato su D", page)
        self.assertEqual(self.pay(pid, "saldo", "200", "D")[1], 200)
        practice = self.row(pid)
        self.assertFalse(practice["total_text"])
        self.assertEqual((practice["deposit"], practice["remaining_balance"], practice["deposit_final"], practice["payment_status"]),
                         ("100.00", "300.00", "200.00", "Pagato"))
        self.assertEqual(self.balance(pid), [("W", 10000), ("D", 20000)])

    def test_extra_payment_on_d_does_not_invent_a_total_d(self):
        pid = self.w_practice()
        self.handler.form = lambda: {"ajax": "1", "extra_circuito": "D", "extra_data": "2026-10-27", "extra_totale": "50",
                                     "extra_modalita": "Contanti", "balance_idempotency_key": "extra-1"}
        self.handler.save_payment_extra(self.admin, pid)
        self.assertFalse(self.row(pid)["total_text"])

    def test_practice_creation_follows_the_same_rule(self):
        errors = []
        self.handler.new_page = lambda user, draft=None, error="", error_field="": errors.append((error, error_field))
        base = {"operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato", "provenance": "L",
                "owner_first_name": "Mario", "owner_last_name": "Verdi", "owner_phone": "3471234567", "owner_tax_code": "Y",
                "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100", "price_cremation": "400"}
        self.handler.form = lambda: {**base, "acconto_d_totale": "100", "acconto_d_data": "2026-10-27"}
        self.handler.create_practice(self.admin)
        self.assertEqual(errors[-1], ("Per un acconto sul circuito D compila il TOTALE D concordato con il cliente.", "total_text"))
        self.assertEqual(self.redirects, [])
        self.handler.form = lambda: {**base, "saldo_d_totale": "330", "saldo_d_data": "2026-10-27", "saldo_d_totale_touched": "1"}
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].rsplit("/", 1)[-1])
        practice = self.row(pid)
        self.assertEqual((practice["total_text"], practice["remaining_final"], practice["payment_status"]), ("330.00", "0.00", "Pagato"))

    def test_payment_window_shows_agreed_total_field_and_w_reference(self):
        pid = self.w_practice()
        _, page = self.summary(pid)
        self.assertIn('name="acconto_totale_d_concordato"', page)
        self.assertIn("Indica il prezzo concordato in D".lower(), page.lower())
        self.assertIn("il TOTALE D diventa quanto incassato su D", page)
        self.assertIn("[data-macroarea-agreed-d][hidden]{display:none!important}", app.CSS)
        self.assertIn("const agreed=form.querySelector('[data-macroarea-agreed-d]');", app.APP_JS)
        self.assertNotIn("Circuito W · solo riferimento", page)
        self.pay(pid, "saldo", "330", "D")
        _, page = self.summary(pid)
        # Pratica ormai su D: niente piu' campo/nota, e il W non e' "da pagare".
        self.assertNotIn('name="acconto_totale_d_concordato"', page)
        self.assertIn("Circuito W · solo riferimento", page)
        self.assertIn("Non da pagare: la pratica è sul circuito D", page)


if __name__ == "__main__":
    unittest.main()
