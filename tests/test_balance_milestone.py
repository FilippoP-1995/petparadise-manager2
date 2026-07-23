import tempfile
import unittest
from pathlib import Path

import app
from balance_service import (
    create_manual_expense,
    create_movement,
    get_balance_snapshot,
    get_movements,
    normalize_filters,
)


class BalanceMilestoneOneTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old=(app.DATA,app.DB_PATH,app.DDT_DIR)
        app.DATA=Path(self.temp.name)
        app.DB_PATH=app.DATA/"test.db"
        app.DDT_DIR=app.DATA/"ddt"
        app.init_db()
        self.handler=object.__new__(app.App)
        self.handler.headers={}
        with app.db() as connection:
            self.admin=connection.execute(
                "SELECT * FROM users WHERE username='admin'"
            ).fetchone()
            self.serena=connection.execute(
                "SELECT * FROM users WHERE username='serena'"
            ).fetchone()
            collaborator_id=connection.execute(
                "SELECT id FROM collaborators ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            self.w_id=self.insert_practice(
                connection,"CR-M1-W","2026-06-01T09:00:00","300","",
                "Acconto","Pos",self.admin["id"],owner="Mario Rossi",
            )
            self.d_id=self.insert_practice(
                connection,"CR-M1-D","2026-06-03T09:00:00","400","330",
                "Acconto","Contanti",self.admin["id"],owner="Daria Verdi",
            )
            self.collaborator_id=self.insert_practice(
                connection,"COL-M1","2026-06-05T09:00:00","500","450",
                "Acconto","Bonifico",self.serena["id"],owner="Cliente Collaboratore",
                request_origin="Collaboratore",collaborator_id=collaborator_id,
            )
            self.old_open_id=self.insert_practice(
                connection,"CR-M1-OLD","2026-05-01T09:00:00","120","",
                "Da saldare","Pos",self.admin["id"],owner="Debito Vecchio",
            )
            self.paid_id=self.insert_practice(
                connection,"CR-M1-PAID","2026-06-10T09:00:00","200","",
                "Pagato","Pos",self.admin["id"],owner="Pratica Chiusa",
            )
            self.future_id=self.insert_practice(
                connection,"CR-M1-FUTURE","2026-08-01T09:00:00","900","",
                "Da saldare","Pos",self.admin["id"],owner="Pratica Futura",
            )
            self.add_income(connection,self.w_id,"CR-M1-W",10000,"2026-07-01","W","Pos","w-boundary-start",self.admin["id"],"Acconto luglio")
            self.add_income(connection,self.w_id,"CR-M1-W",5000,"2026-07-15","W","Pos","w-middle",self.admin["id"],"Secondo incasso")
            self.add_income(connection,self.d_id,"CR-M1-D",10000,"2026-07-31","D","Contanti","d-boundary-end",self.admin["id"],"Acconto D")
            self.add_income(connection,self.collaborator_id,"COL-M1",20000,"2026-07-20","Collaboratori","Bonifico","collab-income",self.serena["id"],"Incasso collaboratore")
            self.add_income(connection,self.paid_id,"CR-M1-PAID",20000,"2026-07-11","W","Pos","paid-full",self.admin["id"],"Incasso completo")
            create_manual_expense(
                connection,amount_cents=3000,movement_date="2026-07-11",
                category="W",description="Materiale ufficio",
                idempotency_key="expense-w",created_by=self.admin["id"],
            )
            create_manual_expense(
                connection,amount_cents=2000,movement_date="2026-07-12",
                category="D",description="Spesa contanti",
                idempotency_key="expense-d",created_by=self.serena["id"],
            )

    def tearDown(self):
        app.DATA,app.DB_PATH,app.DDT_DIR=self.old
        self.temp.cleanup()

    def insert_practice(
        self,connection,number,created_at,total_w,total_d,status,method,user_id,
        *,owner,request_origin="Privato",collaborator_id=None,
    ):
        first,last=(owner.split(" ",1)+[""])[:2]
        return connection.execute(
            """
            INSERT INTO practices(
              practice_number,request_origin,destination_branch,status,
              created_at,updated_at,created_by,animal_name,service_type,
              payment_status,total_service,total_text,payment_method,
              owner_first_name,owner_last_name,collaborator_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                number,request_origin,"Livorno","Ritirato",created_at,created_at,
                user_id,"Fido","Cremazione singola",status,total_w,total_d,
                method,first,last,collaborator_id,
            ),
        ).lastrowid

    def add_income(
        self,connection,practice_id,number,amount,date_,category,method,key,
        user_id,description,
    ):
        create_movement(
            connection,amount_cents=amount,movement_date=date_,
            category=category,ledger_section="Entrata",movement_type="Acconto",
            idempotency_key=key,practice_id=practice_id,
            practice_number_snapshot=number,payment_method=method,
            description=description,source="test",created_by=user_id,
        )

    def snapshot(self,**kwargs):
        values={
            "date_from":"2026-07-01","date_to":"2026-07-31",
            "category":None,"payment_method":None,"operator_id":None,"search":"",
        }
        values.update(kwargs)
        with app.db() as connection:
            return get_balance_snapshot(
                connection,filters=normalize_filters(**values)
            )

    def test_every_card_uses_exact_displayed_rows_and_expected_totals(self):
        snapshot=self.snapshot()
        expected={
            "entrate-w":35000,
            "entrate-d":10000,
            "collaboratori-incassato":20000,
            "da-riscuotere-w":27000,
            "da-riscuotere-d":23000,
            "collaboratori-da-riscuotere":25000,
            "uscite-w":3000,
            "uscite-d":2000,
            "saldo-netto":60000,
        }
        self.assertEqual(set(snapshot.sections),set(expected))
        for key,total in expected.items():
            section=snapshot.sections[key]
            self.assertEqual(section.total_cents,total,key)
            self.assertEqual(
                section.total_cents,sum(section.row_amounts_cents),key
            )
            self.assertEqual(len(section.rows),len(section.row_amounts_cents),key)

    def test_all_filters_apply_to_cards_and_detail_source_rows(self):
        self.assertEqual(self.snapshot(category="D").sections["entrate-w"].total_cents,0)
        self.assertEqual(self.snapshot(category="D").sections["entrate-d"].total_cents,10000)
        self.assertEqual(self.snapshot(payment_method="Contanti").sections["entrate-d"].total_cents,10000)
        self.assertEqual(self.snapshot(payment_method="Contanti").sections["entrate-w"].total_cents,0)
        self.assertEqual(self.snapshot(operator_id=self.serena["id"]).sections["collaboratori-incassato"].total_cents,20000)
        self.assertEqual(self.snapshot(operator_id=self.serena["id"]).sections["uscite-d"].total_cents,2000)
        self.assertEqual(self.snapshot(search="Materiale ufficio").sections["uscite-w"].total_cents,3000)
        self.assertEqual(self.snapshot(search="CR-M1-D").sections["entrate-d"].total_cents,10000)
        self.assertEqual(self.snapshot(search="Daria Verdi").sections["da-riscuotere-d"].total_cents,23000)

    def test_date_boundaries_are_inclusive(self):
        snapshot=self.snapshot(date_from="2026-07-01",date_to="2026-07-31")
        self.assertEqual(snapshot.sections["entrate-w"].total_cents,35000)
        self.assertEqual(snapshot.sections["entrate-d"].total_cents,10000)
        before_end=self.snapshot(date_from="2026-07-02",date_to="2026-07-30")
        self.assertEqual(before_end.sections["entrate-w"].total_cents,25000)
        self.assertEqual(before_end.sections["entrate-d"].total_cents,0)

    def test_outstanding_uses_end_date_and_ignores_start_date(self):
        historical=self.snapshot(
            date_from="2026-07-10",date_to="2026-07-10"
        )
        w_rows=historical.sections["da-riscuotere-w"].rows
        by_number={row.practice_number:row for row in w_rows}
        self.assertIn("CR-M1-W",by_number)
        self.assertIn("CR-M1-OLD",by_number)
        self.assertNotIn("CR-M1-FUTURE",by_number)
        self.assertEqual(by_number["CR-M1-W"].received_cents,10000)
        self.assertEqual(by_number["CR-M1-W"].remaining_cents,20000)
        self.assertEqual(by_number["CR-M1-PAID"].remaining_cents,20000)

    def test_collaborators_are_excluded_from_w_and_d(self):
        snapshot=self.snapshot()
        w_ids={row.practice_id for row in snapshot.sections["entrate-w"].rows}
        d_ids={row.practice_id for row in snapshot.sections["entrate-d"].rows}
        collaborator_ids={
            row.practice_id
            for row in snapshot.sections["collaboratori-incassato"].rows
        }
        self.assertNotIn(self.collaborator_id,w_ids|d_ids)
        self.assertEqual(collaborator_ids,{self.collaborator_id})

    def test_manual_expense_is_idempotent_and_immutable(self):
        with app.db() as connection:
            first=create_manual_expense(
                connection,amount_cents=1234,movement_date="2026-07-21",
                category="W",description="Uscita duplicata",
                idempotency_key="same-expense",created_by=self.admin["id"],
            )
            second=create_manual_expense(
                connection,amount_cents=1234,movement_date="2026-07-21",
                category="W",description="Uscita duplicata",
                idempotency_key="same-expense",created_by=self.admin["id"],
            )
            self.assertEqual(first.id,second.id)
            self.assertEqual(
                len([row for row in get_movements(connection) if row.idempotency_key=="same-expense"]),
                1,
            )

    def test_page_selection_shows_only_linked_rows_and_real_total(self):
        rendered=[]
        self.handler.send_html=lambda html,*args:rendered.append(html)
        self.handler.path="/bilanci?data_iniziale=2026-07-01&data_finale=2026-07-31&view=uscite-w"
        self.handler.balances_page(self.admin)
        page=rendered[-1]
        self.assertIn('data-selected-balance-section="uscite-w"',page)
        self.assertIn('data-balance-total-cents="3000"',page)
        self.assertIn("Materiale ufficio",page)
        self.assertNotIn("Spesa contanti</td>",page)
        self.assertEqual(page.count("data-balance-detail-row"),1)
        self.assertIn('data-amount-cents="3000"',page)

    def test_manual_expense_post_uses_service_and_duplicate_request_is_safe(self):
        redirects=[]
        self.handler.redirect=lambda path:redirects.append(path)
        form={
            "movement_date":"2026-07-25","amount":"12,34","category":"D",
            "description":"Uscita dal form","balance_idempotency_key":"form-token",
            "return_to":"/bilanci?view=uscite-d",
        }
        self.handler.form=lambda:dict(form)
        self.handler.path="/bilanci/uscite?view=uscite-d"
        self.handler.balance_expense_submit(self.admin)
        self.handler.balance_expense_submit(self.admin)
        with app.db() as connection:
            rows=[
                row for row in get_movements(connection)
                if row.idempotency_key=="manual-expense:form-token"
            ]
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0].amount_cents,rows[0].category,rows[0].ledger_section),(1234,"D","Uscita"))
        self.assertTrue(all("uscita_creata=1" in path for path in redirects))

    def test_practice_flow_still_creates_no_movement_when_due(self):
        handler=object.__new__(app.App)
        redirects=[]
        handler.redirect=lambda path:redirects.append(path)
        handler.form=lambda:{
            "calendar_event_id":"","operator_name":"FILIPPO",
            "service_type":"Cremazione collettiva","request_origin":"Privato",
            "payment_status":"Da saldare","price_cremation":"100",
            "balance_idempotency_key":"regression-practice",
        }
        with app.db() as connection:
            before=len(get_movements(connection))
        handler.create_practice(self.admin)
        with app.db() as connection:
            after=len(get_movements(connection))
        self.assertEqual(before,after)
        self.assertTrue(redirects[-1].startswith("/pratiche/"))


if __name__=="__main__":
    unittest.main()
