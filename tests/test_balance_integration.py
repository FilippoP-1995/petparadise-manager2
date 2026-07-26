import json
import tempfile
import unittest
from pathlib import Path

import app
from balance_service import get_movements


class BalancePracticeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.redirects = []
        self.handler.redirect = lambda path: self.redirects.append(path)
        self.handler.headers = {}
        with app.db() as connection:
            self.admin = connection.execute(
                "SELECT * FROM users WHERE username='admin'"
            ).fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def creation_form(
        self,
        *,
        payment_status="Da saldare",
        amount="",
        total_w="300",
        total_d="",
        deposit="",
        token="creation-token",
        request_origin="Privato",
        collaborator_id="",
    ):
        # payment_status/payment_amount/economic_at no longer drive practice
        # creation (replaced by the acconto/saldo x D/W macroarea fields) —
        # this helper keeps its old parameter shape but translates into the
        # new fields, so every existing call site above stays meaningful.
        channel = "d" if total_d else "w"
        macro_fields = {}
        if payment_status == "Acconto":
            macro_fields[f"acconto_{channel}_totale"] = amount or deposit
            macro_fields[f"acconto_{channel}_data"] = "2026-07-23"
            macro_fields[f"acconto_{channel}_modalita"] = "Contanti"
        elif payment_status == "Pagato":
            macro_fields[f"saldo_{channel}_totale"] = amount or (total_d or total_w)
            macro_fields[f"saldo_{channel}_data"] = "2026-07-23"
            macro_fields[f"saldo_{channel}_modalita"] = "Contanti"
        return {
            "calendar_event_id": "",
            "operator_name": "FILIPPO",
            "service_type": "Cremazione collettiva",
            "request_origin": request_origin,
            "collaborator_id": collaborator_id,
            "price_cremation": total_w,
            "total_text": total_d,
            "deposit": deposit,
            "deposit_final": deposit if total_d else "",
            "balance_idempotency_key": token,
            **macro_fields,
        }

    def insert_practice(
        self,
        *,
        number,
        payment_status="Da saldare",
        total_w="300",
        total_d="",
        deposit="",
        request_origin="Privato",
        collaborator_id=None,
    ):
        stamp = app.now()
        with app.db() as connection:
            return connection.execute(
                """
                INSERT INTO practices(
                  practice_number,request_origin,destination_branch,status,
                  created_at,updated_at,created_by,animal_name,service_type,
                  payment_status,price_cremation,total_service,total_text,deposit,
                  collaborator_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    number,
                    request_origin,
                    "Livorno",
                    "Ritirato",
                    stamp,
                    stamp,
                    self.admin["id"],
                    "Fido",
                    "Cremazione singola",
                    payment_status,
                    total_w,
                    total_w,
                    total_d,
                    deposit,
                    collaborator_id,
                ),
            ).lastrowid

    def submit_quick_payment(
        self, practice_id, *, status, amount, token, method="Pos", paid_at="2026-07-23"
    ):
        responses = []
        self.handler.send_json = (
            lambda payload, status=200: responses.append((payload, status))
        )
        self.handler.form = lambda: {
            "payment_status": status,
            "payment_method": method,
            "payment_amount": amount,
            "economic_at": paid_at,
            "balance_idempotency_key": token,
            "ajax": "1",
        }
        self.handler.quick_payment(self.admin, practice_id)
        return responses[-1]

    def test_practice_creation_registers_none_deposit_and_full_payment(self):
        self.handler.form = lambda: self.creation_form(
            payment_status="Da saldare", token="create-due"
        )
        self.handler.create_practice(self.admin)
        with app.db() as connection:
            self.assertEqual(get_movements(connection), [])

        self.handler.form = lambda: self.creation_form(
            payment_status="Acconto", deposit="100", token="create-deposit"
        )
        self.handler.create_practice(self.admin)

        self.handler.form = lambda: self.creation_form(
            payment_status="Pagato",
            total_w="410",
            total_d="330",
            token="create-paid",
        )
        self.handler.create_practice(self.admin)
        with app.db() as connection:
            movements = get_movements(connection)
        deposit = next(
            movement for movement in movements
            if movement.idempotency_key.endswith(":practice-create:create-deposit")
        )
        paid = next(
            movement for movement in movements
            if movement.idempotency_key.endswith(":practice-create:create-paid")
        )
        self.assertEqual(
            (deposit.movement_type, deposit.amount_cents, deposit.category),
            ("Acconto", 10000, "W"),
        )
        self.assertEqual(
            (paid.movement_type, paid.amount_cents, paid.category),
            ("Incasso completo", 33000, "D"),
        )
        self.assertEqual(
            (deposit.movement_date, paid.movement_date),
            ("2026-07-23", "2026-07-23"),
        )

    def test_replayed_paid_creation_redirects_to_original_without_duplicate(self):
        form = self.creation_form(
            payment_status="Pagato", amount="300", token="same-creation-request"
        )
        self.handler.form = lambda: dict(form)
        self.handler.create_practice(self.admin)
        first_redirect = self.redirects[-1]
        self.handler.create_practice(self.admin)
        self.assertEqual(self.redirects[-1], first_redirect)
        with app.db() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM practices").fetchone()[0],
                1,
            )
            self.assertEqual(len(get_movements(connection)), 1)

    def test_create_practice_with_only_acconto_d_registers_d_movement_without_method(self):
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "total_text": "350", "acconto_d_totale": "100", "acconto_d_data": "2026-07-23", "acconto_d_modalita": "",
            "balance_idempotency_key": "acconto-d-only",
        }
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].split("/pratiche/")[1])
        with app.db() as connection:
            practice = connection.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
            movements = [m for m in get_movements(connection) if m.practice_id == pid]
        self.assertEqual(practice["payment_status"], "Acconto")
        self.assertEqual(practice["deposit_final"], "100.00")
        self.assertEqual(practice["deposit"], "")
        self.assertEqual(len(movements), 1)
        self.assertEqual((movements[0].movement_type, movements[0].amount_cents, movements[0].category), ("Acconto", 10000, "D"))

    def test_create_practice_with_untouched_autofilled_rimanenza_d_is_silently_skipped(self):
        # Regression test: the browser auto-fills Rimanenza D's amount from
        # Totale D - Acconto D as a live preview (updateMacroRimanenza in
        # APP_JS) even when the user never intends to register a saldo yet
        # (they don't know the payment date for money still owed). Without
        # the *_touched flag this amount alone used to force a "data valida"
        # error and — worse — silently wipe the just-typed Acconto D on
        # redisplay (payment_draft() didn't preserve macro fields either).
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "total_text": "350", "acconto_d_totale": "100", "acconto_d_data": "2026-07-23", "acconto_d_modalita": "",
            "saldo_d_totale": "250", "saldo_d_data": "", "saldo_d_totale_touched": "",
        }
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].split("/pratiche/")[1])
        with app.db() as connection:
            movements = [m for m in get_movements(connection) if m.practice_id == pid]
        self.assertEqual(len(movements), 1)
        self.assertEqual((movements[0].movement_type, movements[0].category), ("Acconto", "D"))

    def test_create_practice_with_manually_touched_rimanenza_d_still_requires_a_date(self):
        errors = []
        self.handler.new_page = lambda user, draft=None, error="", error_field="": errors.append((error, error_field))
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "total_text": "350", "saldo_d_totale": "250", "saldo_d_data": "", "saldo_d_totale_touched": "1",
        }
        self.handler.create_practice(self.admin)
        self.assertIn("Indica una data valida per Rimanenza D", errors[-1][0])
        self.assertEqual(errors[-1][1], "saldo_d_data")
        with app.db() as connection:
            count = connection.execute("SELECT COUNT(*) n FROM practices").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_validation_error_redisplay_preserves_already_typed_macro_payment_fields(self):
        # Regression test: any validation error used to redisplay the form
        # via payment_draft(), which only carried the "classic" normalized
        # fields — the Acconto/Saldo macroarea fields the user had just
        # typed were silently dropped, forcing them to retype everything.
        drafts = []
        self.handler.new_page = lambda user, draft=None, error="", error_field="": drafts.append(draft)
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "total_text": "350", "saldo_d_totale": "250", "saldo_d_data": "", "saldo_d_totale_touched": "1",
        }
        self.handler.create_practice(self.admin)
        self.assertEqual(drafts[-1].get("saldo_d_totale"), "250")
        self.assertEqual(drafts[-1].get("saldo_d_totale_touched"), "1")

    def test_create_practice_with_only_saldo_w_requires_payment_method(self):
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "price_cremation": "300", "saldo_w_totale": "300", "saldo_w_data": "2026-07-23", "saldo_w_modalita": "",
        }
        errors = []
        self.handler.new_page = lambda user, draft=None, error="", error_field="": errors.append(error)
        self.handler.create_practice(self.admin)
        self.assertIn("Seleziona il metodo di pagamento", errors[-1])
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) n FROM practices").fetchone()["n"], 0)

    def test_create_practice_with_both_acconto_d_and_w_only_registers_d(self):
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "total_text": "350", "price_cremation": "300",
            "acconto_d_totale": "100", "acconto_d_data": "2026-07-23", "acconto_d_modalita": "",
            "acconto_w_totale": "80", "acconto_w_data": "2026-07-23", "acconto_w_modalita": "Contanti",
            "balance_idempotency_key": "acconto-both",
        }
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].split("/pratiche/")[1])
        with app.db() as connection:
            practice = connection.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
            movements = [m for m in get_movements(connection) if m.practice_id == pid]
        # D wins over W when both are filled for the same macroarea: only one
        # movement is ever registered, never both, to avoid double-counting
        # the same receipt.
        self.assertEqual(len(movements), 1)
        self.assertEqual((movements[0].amount_cents, movements[0].category), (10000, "D"))
        self.assertEqual(practice["deposit_final"], "100.00")

    def test_create_practice_with_no_payment_fields_creates_no_movement(self):
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "total_text": "350",
        }
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].split("/pratiche/")[1])
        with app.db() as connection:
            practice = connection.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
            movements = [m for m in get_movements(connection) if m.practice_id == pid]
        self.assertEqual(practice["payment_status"], "Da saldare")
        self.assertEqual(movements, [])

    def test_acconto_and_saldo_registered_at_creation_prefill_the_payment_popover(self):
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "price_cremation": "300",
            "acconto_w_totale": "100", "acconto_w_data": "2026-07-23", "acconto_w_modalita": "Contanti",
        }
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].split("/pratiche/")[1])
        with app.db() as connection:
            row = connection.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
        self.handler.path = f"/pratiche/{pid}"
        html = self.handler.status_badges(row)
        self.assertIn('name="acconto_totale" value="100.00"', html)
        self.assertIn('name="acconto_data" value="2026-07-23" required', html)
        self.assertIn('<option value="W" selected>W</option>', html)

    def test_create_practice_with_acconto_w_fattura_fields_creates_a_real_invoice(self):
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "price_cremation": "300",
            "acconto_w_totale": "100", "acconto_w_data": "2026-07-23", "acconto_w_modalita": "Contanti",
            "acconto_w_fattura_numero": "FT-001", "acconto_w_fattura_data": "2026-07-23", "acconto_w_fattura_totale": "100",
        }
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].split("/pratiche/")[1])
        with app.db() as connection:
            movement = connection.execute(
                "SELECT id FROM payment_movements WHERE practice_id=? AND payment_type LIKE 'acconto%'", (pid,)
            ).fetchone()
            invoice = connection.execute(
                "SELECT * FROM movement_invoices WHERE practice_id=?", (pid,)
            ).fetchone()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice["invoice_number"], "FT-001")
        with app.db() as connection:
            link = connection.execute(
                "SELECT 1 FROM movement_invoice_links WHERE payment_movement_id=?", (movement["id"],)
            ).fetchone()
        self.assertIsNotNone(link)

    def test_create_practice_saldo_d_amount_does_not_leak_into_acconto_w_invoice(self):
        # D circuito never gets an invoice (matches the popover's own rule);
        # this locks that a D amount alone does not accidentally trigger
        # invoice creation through the shared apply_payment_macroarea path.
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "total_text": "350",
            "saldo_d_totale": "350", "saldo_d_data": "2026-07-23", "saldo_d_modalita": "",
        }
        self.handler.create_practice(self.admin)
        pid = int(self.redirects[-1].split("/pratiche/")[1])
        with app.db() as connection:
            invoice = connection.execute(
                "SELECT * FROM movement_invoices WHERE practice_id=?", (pid,)
            ).fetchone()
        self.assertIsNone(invoice)

    def test_payment_transitions_create_acconto_full_payment_and_only_remaining(self):
        split_id = self.insert_practice(number="CR-SPLIT", total_w="300")
        response = self.submit_quick_payment(
            split_id,
            status="Acconto",
            amount="100",
            token="split-deposit",
            paid_at="2026-07-10",
        )
        self.assertEqual(response[1], 200)
        response = self.submit_quick_payment(
            split_id,
            status="Pagato",
            amount="200",
            token="split-balance",
            paid_at="2026-07-20",
        )
        self.assertEqual(response[1], 200)

        full_id = self.insert_practice(number="CR-FULL", total_w="450")
        response = self.submit_quick_payment(
            full_id,
            status="Pagato",
            amount="450",
            token="full-payment",
        )
        self.assertEqual(response[1], 200)
        with app.db() as connection:
            movements = get_movements(connection)
        split = [
            movement for movement in movements if movement.practice_id == split_id
        ]
        full = [movement for movement in movements if movement.practice_id == full_id]
        self.assertEqual(
            sorted(
                (movement.movement_type, movement.amount_cents, movement.movement_date)
                for movement in split
            ),
            [("Acconto", 10000, "2026-07-10"), ("Saldo", 20000, "2026-07-20")],
        )
        self.assertEqual(
            [(movement.movement_type, movement.amount_cents) for movement in full],
            [("Incasso completo", 45000)],
        )
        with app.db() as connection:
            deposit_period = get_movements(
                connection, date_from="2026-07-10", date_to="2026-07-10"
            )
            balance_period = get_movements(
                connection, date_from="2026-07-20", date_to="2026-07-20"
            )
        self.assertEqual(
            [(row.movement_type, row.amount_cents) for row in deposit_period],
            [("Acconto", 10000)],
        )
        self.assertEqual(
            [(row.movement_type, row.amount_cents) for row in balance_period],
            [("Saldo", 20000)],
        )

    def test_same_quick_payment_request_does_not_create_duplicate(self):
        practice_id = self.insert_practice(number="CR-IDEMPOTENT", total_w="300")
        first = self.submit_quick_payment(
            practice_id,
            status="Pagato",
            amount="300",
            token="same-payment-request",
        )
        second = self.submit_quick_payment(
            practice_id,
            status="Pagato",
            amount="300",
            token="same-payment-request",
        )
        self.assertEqual((first[1], second[1]), (200, 200))
        with app.db() as connection:
            self.assertEqual(len(get_movements(connection)), 1)

    def test_new_ledger_category_ignores_method_and_prioritizes_collaborator(self):
        cash_w_id = self.insert_practice(number="CR-CASH-W", total_w="200")
        self.submit_quick_payment(
            cash_w_id,
            status="Pagato",
            amount="200",
            token="cash-w",
            method="Contanti",
        )
        with app.db() as connection:
            collaborator_id = connection.execute(
                "SELECT id FROM collaborators ORDER BY id LIMIT 1"
            ).fetchone()["id"]
        collaborator_practice_id = self.insert_practice(
            number="COL-D",
            total_w="400",
            total_d="330",
            request_origin="Collaboratore",
            collaborator_id=collaborator_id,
        )
        self.submit_quick_payment(
            collaborator_practice_id,
            status="Pagato",
            amount="330",
            token="collaborator-d",
            method="Pos",
        )
        with app.db() as connection:
            categories = {
                movement.practice_id: movement.category
                for movement in get_movements(connection)
            }
        self.assertEqual(categories[cash_w_id], "W")
        self.assertEqual(categories[collaborator_practice_id], "Collaboratori")

    def test_normal_edit_and_autosave_do_not_create_balance_movements(self):
        practice_id = self.insert_practice(number="CR-NO-MOVEMENT")
        self.handler.form = lambda: {
            "operator_name": "FILIPPO",
            "service_type": "Cremazione collettiva",
            "request_origin": "Privato",
            "payment_status": "Da saldare",
            "notes": "Modifica manuale senza pagamento",
            "return_to": "/archivio/pratiche",
        }
        self.handler.edit_submit(self.admin, practice_id)
        with app.db() as connection:
            version = connection.execute(
                "SELECT updated_at FROM practices WHERE id=?", (practice_id,)
            ).fetchone()["updated_at"]
            self.assertEqual(get_movements(connection), [])

        responses = []
        self.handler.send_json = (
            lambda payload, status=200: responses.append((payload, status))
        )
        self.handler.form = lambda: {
            "updated_at": version,
            "changes_json": json.dumps({"notes": "Autosalvataggio senza pagamento"}),
        }
        self.handler.practice_autosave(self.admin, practice_id)
        self.assertEqual(responses[-1][1], 200)
        with app.db() as connection:
            self.assertEqual(get_movements(connection), [])

    def test_existing_payment_popup_prefills_amount_date_and_has_cancel_path(self):
        practice_id = self.insert_practice(
            number="CR-POPUP",
            payment_status="Acconto",
            total_w="300",
            deposit="100",
        )
        with app.db() as connection:
            row = connection.execute(
                "SELECT * FROM practices WHERE id=?", (practice_id,)
            ).fetchone()
        self.handler.path = f"/pratiche/{practice_id}"
        html = self.handler.status_badges(row)
        self.assertIn('name="balance_idempotency_key"', html)
        # no movement saved yet: acconto pre-fills from the deposit set at
        # creation, saldo pre-fills from the outstanding remainder
        self.assertIn('name="acconto_totale" value="100"', html)
        self.assertIn('name="saldo_totale" value="200.00"', html)
        self.assertIn('name="acconto_data" value="" required', html)
        self.assertIn('name="saldo_data" value="" required', html)
        self.assertIn("function closePaymentPopover(button){", app.APP_JS)
        self.assertIn("target.hidden=true;", app.APP_JS)
        with app.db() as connection:
            self.assertEqual(get_movements(connection), [])

    def test_acconto_date_correction_keeps_one_visible_economic_row(self):
        practice_id=self.insert_practice(number="CR-DATE-A")
        response,status=self.submit_quick_payment(
            practice_id,status="Acconto",amount="100",token="deposit-event",
            paid_at="2026-07-10",
        )
        self.assertEqual(status,200)
        response,status=self.submit_quick_payment(
            practice_id,status="Acconto",amount="100",token="date-only",
            paid_at="2026-07-12",
        )
        self.assertEqual(status,200)
        with app.db() as connection:
            visible=get_movements(connection)
            raw=connection.execute(
                "SELECT * FROM balance_movements WHERE practice_id=? ORDER BY id",
                (practice_id,),
            ).fetchall()
            practice=connection.execute(
                "SELECT deposit_paid_at FROM practices WHERE id=?",(practice_id,)
            ).fetchone()
        self.assertEqual(len(visible),1)
        self.assertEqual((visible[0].movement_type,visible[0].movement_date),("Acconto","2026-07-12"))
        self.assertEqual(sum(row["amount_cents"] for row in raw),10000)
        self.assertEqual(len(raw),3)
        self.assertEqual(practice["deposit_paid_at"],"2026-07-12")

    def test_paid_date_correction_and_repeated_save_do_not_duplicate(self):
        practice_id=self.insert_practice(number="CR-DATE-P")
        self.submit_quick_payment(
            practice_id,status="Pagato",amount="300",token="paid-event",
            paid_at="2026-07-20",
        )
        self.submit_quick_payment(
            practice_id,status="Pagato",amount="300",token="date-correction",
            paid_at="2026-07-22",
        )
        self.submit_quick_payment(
            practice_id,status="Pagato",amount="300",token="repeat-save",
            paid_at="2026-07-22",
        )
        with app.db() as connection:
            visible=get_movements(connection)
            raw_count=connection.execute(
                "SELECT COUNT(*) n FROM balance_movements WHERE practice_id=?",
                (practice_id,),
            ).fetchone()["n"]
            old_period=get_movements(
                connection,date_from="2026-07-20",date_to="2026-07-20"
            )
            new_period=get_movements(
                connection,date_from="2026-07-22",date_to="2026-07-22"
            )
        self.assertEqual(len(visible),1)
        self.assertEqual(visible[0].movement_date,"2026-07-22")
        self.assertEqual(raw_count,3)
        self.assertEqual(old_period,[])
        self.assertEqual(len(new_period),1)

    def test_payment_date_is_visible_and_required_on_create_and_edit(self):
        rendered=[]
        self.handler.send_html=lambda html,*args:rendered.append(html)
        self.handler.path="/nuova"
        self.handler.new_page(self.admin,draft={
            "payment_status":"Acconto","economic_at":"2026-07-09",
        })
        self.assertIn("Data pagamento / acconto",rendered[-1])
        self.assertIn('name="economic_at" value="2026-07-09"',rendered[-1])
        errors=[]
        self.handler.new_page=lambda user,draft=None,error="",error_field="":errors.append((draft,error))
        invalid=self.creation_form(payment_status="Pagato",token="missing-date")
        invalid["saldo_w_data"]=""
        invalid["saldo_w_totale_touched"]="1"
        self.handler.form=lambda:invalid
        self.handler.create_practice(self.admin)
        self.assertIn("data",errors[-1][1].lower())
        with app.db() as connection:
            count=connection.execute("SELECT COUNT(*) n FROM practices").fetchone()["n"]
        self.assertEqual(count,0)

    def test_editing_paid_amount_creates_one_append_only_correction(self):
        practice_id=self.insert_practice(
            number="CR-AMOUNT-EDIT",payment_status="Da saldare",total_w="370"
        )
        response,status=self.submit_quick_payment(
            practice_id,status="Pagato",amount="370",token="paid-370",
            paid_at="2026-07-15",
        )
        self.assertEqual(status,200)
        base_form={
            "operator_name":"FILIPPO",
            "service_type":"Cremazione collettiva",
            "request_origin":"Privato",
            "payment_status":"Pagato",
            "payment_method":"Pos",
            "price_cremation":"320",
            "economic_at":"2026-07-15",
            "notes":"Importo corretto",
            "return_to":"/archivio/pratiche",
        }
        self.handler.form=lambda:dict(base_form)
        self.handler.edit_submit(self.admin,practice_id)
        with app.db() as connection:
            standard=get_movements(connection)
            audit=get_movements(connection,include_technical=True)
            stored=connection.execute(
                "SELECT total_service FROM practices WHERE id=?",(practice_id,)
            ).fetchone()
        self.assertEqual(
            [(row.movement_type,row.amount_cents) for row in standard],
            [("Incasso completo",32000)],
        )
        self.assertEqual(
            sorted(row.amount_cents for row in audit),[-37000,32000,37000]
        )
        self.assertEqual(float(stored["total_service"]),320.0)

        self.handler.form=lambda:{**base_form,"notes":"Solo nota aggiornata"}
        self.handler.edit_submit(self.admin,practice_id)
        with app.db() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) n FROM balance_movements WHERE practice_id=?",
                    (practice_id,),
                ).fetchone()["n"],
                3,
            )


if __name__ == "__main__":
    unittest.main()
