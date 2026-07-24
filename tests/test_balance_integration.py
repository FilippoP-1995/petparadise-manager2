import json
import tempfile
import unittest
from datetime import datetime
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
        return {
            "calendar_event_id": "",
            "operator_name": "FILIPPO",
            "service_type": "Cremazione collettiva",
            "request_origin": request_origin,
            "collaborator_id": collaborator_id,
            "payment_status": payment_status,
            "payment_method": "Contanti",
            "payment_amount": amount,
            "economic_at": "2026-07-23",
            "price_cremation": total_w,
            "total_text": total_d,
            "deposit": deposit,
            "deposit_final": deposit if total_d else "",
            "balance_idempotency_key": token,
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
        today = datetime.now(app.ROME_TZ).date().isoformat()
        self.assertIn('name="balance_idempotency_key"', html)
        self.assertIn('data-payment-full-amount="300.00"', html)
        self.assertIn('data-payment-remaining-amount="200.00"', html)
        self.assertIn(f'data-payment-default-date="{today}"', html)
        self.assertIn('name="economic_at" value=""', html)
        self.assertIn("ppmLocalDateValue", app.APP_JS)
        self.assertIn(
            "if(trigger)trigger.value=trigger.dataset.savedValue||trigger.value",
            app.APP_JS,
        )
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
        self.handler.new_page=lambda user,draft=None,error="":errors.append((draft,error))
        invalid=self.creation_form(payment_status="Pagato",token="missing-date")
        invalid["economic_at"]=""
        self.handler.form=lambda:invalid
        self.handler.create_practice(self.admin)
        self.assertIn("data",errors[-1][1].lower())
        with app.db() as connection:
            count=connection.execute("SELECT COUNT(*) n FROM practices").fetchone()["n"]
        self.assertEqual(count,0)


if __name__ == "__main__":
    unittest.main()
