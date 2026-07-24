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


class ProductionBalanceModuleTests(unittest.TestCase):
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
            self.collaborator=connection.execute(
                "SELECT * FROM collaborators ORDER BY id LIMIT 1"
            ).fetchone()
            self.w_id=self.practice(
                connection,"CR-PROD-W","Acconto","300","",
                owner="Mario Storico",method="Pos",deposit="100",
                deposit_paid_at="2026-07-03T10:00:00",
            )
            self.d_id=self.practice(
                connection,"CR-PROD-D","Pagato","400","330",
                owner="Daria Storica",method="Contanti",
                paid_at="2026-07-04T11:00:00",
            )
            self.collab_id=self.practice(
                connection,"COL-PROD","Pagato","200","",
                owner="Cliente Collaboratore",method="Bonifico",
                request_origin="Collaboratore",
                collaborator_id=self.collaborator["id"],
            )
            self.old_payment(
                connection,self.collab_id,"saldo_ordinario",200,
                "2026-07-05T12:00:00","Bonifico",
            )
            self.hybrid_id=self.practice(
                connection,"CR-PROD-HYBRID","Acconto","100","",
                owner="Ibrido Duplicato",method="Pos",
            )
            self.old_payment(
                connection,self.hybrid_id,"acconto_ordinario",50,
                "2026-07-06T12:00:00","Pos",
            )
            create_movement(
                connection,amount_cents=5000,movement_date="2026-07-06",
                category="W",ledger_section="Entrata",
                movement_type="Acconto",idempotency_key="prod-hybrid-new",
                practice_id=self.hybrid_id,
                practice_number_snapshot="CR-PROD-HYBRID",
                payment_method="Pos",description="Acconto",
                source="practice_payment_transition",
                created_by=self.admin["id"],
            )
            self.due_d_id=self.practice(
                connection,"CR-PROD-DUE-D","Da saldare","250","100",
                owner="Debito D",method="Contanti",
            )
            self.undated_id=self.practice(
                connection,"CR-PROD-UNDATED","Acconto","300","",
                owner="Data Mancante",method="Pos",deposit="90",
            )

    def tearDown(self):
        app.DATA,app.DB_PATH,app.DDT_DIR=self.old
        self.temp.cleanup()

    def practice(
        self,connection,number,status,total_w,total_d,*,owner,method,
        deposit="",deposit_paid_at="",paid_at="",request_origin="Privato",
        collaborator_id=None,
    ):
        first,last=(owner.split(" ",1)+[""])[:2]
        stamp="2026-07-01T09:00:00"
        return connection.execute(
            """
            INSERT INTO practices(
              practice_number,request_origin,destination_branch,status,
              created_at,updated_at,created_by,animal_name,payment_status,
              total_service,total_text,deposit,deposit_final,deposit_paid_at,
              paid_at,payment_method,owner_first_name,owner_last_name,
              collaborator_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                number,request_origin,"Livorno","Ritirato",stamp,stamp,
                self.admin["id"],"Fido",status,total_w,total_d,deposit,
                deposit if total_d else "",deposit_paid_at,paid_at,method,
                first,last,collaborator_id,
            ),
        ).lastrowid

    def old_payment(
        self,connection,practice_id,kind,amount,paid_at,method
    ):
        connection.execute(
            """
            INSERT INTO payment_movements(
              practice_id,payment_type,payment_channel,payment_method,
              movement_category,amount,paid_at,user_id,notes,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                practice_id,kind,"ordinario",method,"",amount,paid_at,
                self.admin["id"],"Pagamento storico",paid_at,
            ),
        )

    def filters(self,**overrides):
        values={
            "date_from":"2026-07-01","date_to":"2026-07-31",
            "category":None,"collaborator_id":None,"payment_method":None,
            "operator_id":None,"search":"",
        }
        values.update(overrides)
        return normalize_filters(**values)

    def test_historical_and_ledger_rows_share_cards_without_duplicates(self):
        with app.db() as connection:
            ledger_before=connection.execute(
                "SELECT count(*) n FROM balance_movements"
            ).fetchone()["n"]
            legacy_before=connection.execute(
                "SELECT count(*) n FROM payment_movements"
            ).fetchone()["n"]
            snapshot=get_balance_snapshot(
                connection,filters=self.filters()
            )
            movements=get_movements(connection,filters=self.filters())
            ledger_after=connection.execute(
                "SELECT count(*) n FROM balance_movements"
            ).fetchone()["n"]
            legacy_after=connection.execute(
                "SELECT count(*) n FROM payment_movements"
            ).fetchone()["n"]
        self.assertEqual((ledger_after,legacy_after),(ledger_before,legacy_before))
        self.assertEqual(snapshot.sections["entrate-w"].total_cents,15000)
        self.assertEqual(snapshot.sections["entrate-d"].total_cents,33000)
        self.assertEqual(
            snapshot.sections["collaboratori-incassato"].total_cents,20000
        )
        self.assertEqual(snapshot.sections["saldo-netto"].total_cents,68000)
        self.assertEqual(
            snapshot.sections["da-riscuotere-w"].total_cents,55000
        )
        self.assertEqual(
            snapshot.sections["da-riscuotere-d"].total_cents,10000
        )
        hybrid=[
            row for row in movements
            if row.practice_number_snapshot=="CR-PROD-HYBRID"
        ]
        self.assertEqual(len(hybrid),1)
        self.assertEqual(hybrid[0].source,"practice_payment_transition")
        for section in snapshot.sections.values():
            self.assertEqual(
                section.total_cents,sum(section.row_amounts_cents)
            )

    def test_every_filter_applies_to_historical_and_new_rows(self):
        with app.db() as connection:
            by_d=get_balance_snapshot(
                connection,filters=self.filters(category="D")
            )
            by_method=get_balance_snapshot(
                connection,filters=self.filters(payment_method="Bonifico")
            )
            by_collaborator=get_balance_snapshot(
                connection,filters=self.filters(
                    collaborator_id=self.collaborator["id"]
                )
            )
            by_search=get_balance_snapshot(
                connection,filters=self.filters(search="Mario Storico")
            )
            before_date=get_balance_snapshot(
                connection,filters=self.filters(date_to="2026-07-03")
            )
        self.assertEqual(by_d.sections["entrate-d"].total_cents,33000)
        self.assertEqual(by_d.sections["entrate-w"].total_cents,0)
        self.assertEqual(
            by_method.sections["collaboratori-incassato"].total_cents,20000
        )
        self.assertEqual(
            by_collaborator.sections[
                "collaboratori-incassato"
            ].total_cents,20000
        )
        self.assertEqual(
            by_collaborator.sections["saldo-netto"].total_cents,20000
        )
        self.assertEqual(by_search.sections["entrate-w"].total_cents,10000)
        self.assertEqual(before_date.sections["entrate-w"].total_cents,10000)

    def test_missing_historical_date_never_creates_an_income(self):
        with app.db() as connection:
            movements=get_movements(connection,filters=self.filters())
            snapshot=get_balance_snapshot(
                connection,filters=self.filters()
            )
        self.assertNotIn(
            "CR-PROD-UNDATED",
            {row.practice_number_snapshot for row in movements},
        )
        outstanding={
            row.practice_number:row
            for row in snapshot.sections["da-riscuotere-w"].rows
        }
        self.assertEqual(
            outstanding["CR-PROD-UNDATED"].remaining_cents,30000
        )

    def test_snapshot_uses_a_fixed_number_of_bulk_queries(self):
        statements=[]
        with app.db() as connection:
            connection.set_trace_callback(statements.append)
            get_balance_snapshot(connection,filters=self.filters())
            connection.set_trace_callback(None)
        reads=[
            statement for statement in statements
            if statement.lstrip().upper().startswith(("SELECT","WITH"))
        ]
        self.assertLessEqual(len(reads),10)
        self.assertFalse(any(" FOR EACH " in statement.upper() for statement in reads))

    def test_balance_page_paginates_without_changing_total(self):
        with app.db() as connection:
            for index in range(55):
                create_manual_expense(
                    connection,amount_cents=100,
                    movement_date="2026-07-10",category="W",
                    description=f"Uscita {index:02d}",
                    idempotency_key=f"pagination-{index}",
                    created_by=self.admin["id"],
                )
        rendered=[]
        self.handler.send_html=lambda html,*args:rendered.append(html)
        self.handler.path=(
            "/bilanci?data_iniziale=2026-07-01&"
            "data_finale=2026-07-31&view=uscite-w"
        )
        self.handler.balances_page(self.admin)
        first_page=rendered[-1]
        self.assertEqual(first_page.count("data-balance-detail-row"),50)
        self.assertIn("Pagina 1 di 2",first_page)
        self.assertIn('data-balance-total-cents="5500"',first_page)
        self.assertIn('name="collaboratore"',first_page)
        self.assertIn("Caricamento…",first_page)
        self.assertIn("Registrazione…",first_page)
        for heading in (
            "Data incasso / movimento","Data creazione pratica","Animale",
            "Proprietario","Stato","Categoria","Importo","Metodo","Collaboratore",
        ):
            self.assertIn(heading,first_page)
        self.assertLess(
            first_page.index('id="balanceDetails"'),
            first_page.index('class="balance-grid"'),
        )
        self.assertLess(
            first_page.index('class="balance-grid"'),
            first_page.index('aria-label="Filtri Bilanci"'),
        )
        self.assertIn(
            ".balance-grid{display:grid;grid-template-columns:repeat(2",
            app.CSS,
        )
        rendered.clear()
        self.handler.path+=("&pagina=2")
        self.handler.balances_page(self.admin)
        self.assertEqual(rendered[-1].count("data-balance-detail-row"),5)
        self.assertIn("Pagina 2 di 2",rendered[-1])


if __name__=="__main__":
    unittest.main()
