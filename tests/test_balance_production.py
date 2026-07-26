import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from balance_service import (
    InvalidMovementError,
    MovementNotFoundError,
    create_legacy_reversal,
    create_manual_expense,
    create_movement,
    get_balance_snapshot,
    get_movements,
    get_outstanding_balances,
    get_recent_movement_deletions,
    normalize_filters,
    practice_id_for_legacy_key,
    restore_movement_deletion,
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
            "Creazione","Movimento","Animale","Proprietario","Stato",
            "Categoria","Importo","Metodo","Collaboratore","Azione",
        ):
            self.assertIn(heading,first_page)
        self.assertLess(
            first_page.index('class="balance-col-date">Creazione</th>'),
            first_page.index('class="balance-col-date">Movimento</th>'),
        )
        self.assertLess(
            first_page.index('class="balance-col-action">Azione</th>'),
            first_page.index('class="balance-col-date">Creazione</th>'),
        )
        self.assertIn(">Elimina</a>",first_page)
        self.assertIn("Registra entrata",first_page)
        self.assertIn("Registra uscita",first_page)
        self.assertIn("balance-manual-toolbar",first_page)
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
        self.assertIn(
            "@media(max-width:560px){.balance-filters .fields{grid-template-columns:1fr}.balance-grid{grid-template-columns:repeat(2",
            app.CSS,
        )
        rendered.clear()
        self.handler.path+=("&pagina=2")
        self.handler.balances_page(self.admin)
        self.assertEqual(rendered[-1].count("data-balance-detail-row"),5)
        self.assertIn("Pagina 2 di 2",rendered[-1])

    def test_admin_delete_permanently_removes_the_row_and_updates_totals(self):
        with app.db() as connection:
            movement=create_movement(
                connection,amount_cents=12500,movement_date="2026-07-12",
                category="W",ledger_section="Entrata",
                movement_type="Entrata manuale",
                idempotency_key="production-manual-income",
                payment_method="Bonifico",description="Entrata da eliminare",
                source="manual_income",created_by=self.admin["id"],
            )
            before_total=connection.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM balance_movements WHERE category='W' AND ledger_section='Entrata'"
            ).fetchone()[0]
        self.handler.form=lambda:{"return_to":"/bilanci?view=entrate-w"}
        redirects=[]
        self.handler.redirect=redirects.append
        self.handler.balance_movement_delete(self.admin,movement.id)
        self.assertEqual(
            redirects,["/bilanci?view=entrate-w&movimento_stornato=1"]
        )
        with app.db() as connection:
            standard=get_movements(
                connection,filters=self.filters(search="Entrata da eliminare")
            )
            audit=get_movements(
                connection,
                filters=normalize_filters(
                    date_from="2026-07-01",date_to="2026-07-31",
                    search="Entrata da eliminare",include_technical=True,
                ),
            )
            # the row must be genuinely gone from the table, not just hidden
            # or offset by a Storno row still sitting in the ledger.
            row=connection.execute(
                "SELECT * FROM balance_movements WHERE id=?",(movement.id,),
            ).fetchone()
            after_total=connection.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM balance_movements WHERE category='W' AND ledger_section='Entrata'"
            ).fetchone()[0]
        self.assertEqual(standard,[])
        self.assertEqual(audit,[])
        self.assertIsNone(row)
        self.assertEqual(before_total-after_total,12500)

        with app.db() as connection:
            deletions=get_recent_movement_deletions(connection,limit=10)
        self.assertEqual(deletions[0]["description"],"Entrata da eliminare")
        self.assertEqual(deletions[0]["amount_cents"],12500)
        self.assertEqual(deletions[0]["deleted_by"],self.admin["id"])

        rendered=[]
        self.handler.send_html=lambda content,*a:rendered.append(content)
        self.handler.path="/bilanci"
        self.handler.balances_page(self.admin)
        self.assertIn("Movimenti eliminati di recente",rendered[-1])
        self.assertIn("Entrata da eliminare",rendered[-1])

        # deleting again must not crash: it's reported as "not found".
        pages=[]
        self.handler.balances_page=lambda user,error="",expense_draft=None:pages.append(error)
        self.handler.balance_movement_delete(self.admin,movement.id)
        self.assertIn("non trovato",pages[-1])

    def test_restrict_practice_id_finds_the_same_single_row_without_scanning_others(self):
        # Bilanci's Elimina confirm/delete flow used to always pay for a full,
        # unbounded scan+synthesis of every practice and payment_movements
        # row just to find the one the user clicked on. On a small test
        # database that's invisible; on a production database that has grown
        # over months of real use it can make the confirmation appear to
        # hang indefinitely. restrict_practice_id must return exactly the
        # same single row as the unrestricted scan, and nothing from any
        # other practice.
        legacy_key=f"historical-practice:{self.w_id}:deposit"
        with app.db() as connection:
            resolved_practice_id=practice_id_for_legacy_key(connection,legacy_key)
            self.assertEqual(resolved_practice_id,self.w_id)
            unrestricted=get_movements(connection,filters=normalize_filters(include_technical=True))
            restricted=get_movements(
                connection,filters=normalize_filters(include_technical=True),
                restrict_practice_id=resolved_practice_id,
            )
        unrestricted_match=next((m for m in unrestricted if m.idempotency_key==legacy_key),None)
        restricted_match=next((m for m in restricted if m.idempotency_key==legacy_key),None)
        self.assertIsNotNone(unrestricted_match)
        self.assertEqual(unrestricted_match,restricted_match)
        self.assertTrue(all(m.practice_id==self.w_id for m in restricted))
        self.assertGreater(len(unrestricted),len(restricted))

    def test_practice_id_for_legacy_key_parses_both_formats_and_rejects_garbage(self):
        with app.db() as connection:
            pm_id=connection.execute(
                "SELECT id FROM payment_movements WHERE practice_id=?",(self.collab_id,)
            ).fetchone()["id"]
            self.assertEqual(
                practice_id_for_legacy_key(connection,f"legacy-payment-movement:{pm_id}"),
                self.collab_id,
            )
            self.assertEqual(
                practice_id_for_legacy_key(connection,f"historical-practice:{self.w_id}:deposit"),
                self.w_id,
            )
            self.assertEqual(
                practice_id_for_legacy_key(connection,f"historical-practice:{self.d_id}:balance"),
                self.d_id,
            )
            self.assertIsNone(practice_id_for_legacy_key(connection,"legacy-payment-movement:999999"))
            self.assertIsNone(practice_id_for_legacy_key(connection,"historical-practice:abc:deposit"))
            self.assertIsNone(practice_id_for_legacy_key(connection,"historical-practice:1:qualcosa"))
            self.assertIsNone(practice_id_for_legacy_key(connection,"qualcosa-di-diverso:1"))


class LegacyMovementDeletionTests(unittest.TestCase):
    """Covers the full click-to-query flow for deleting a Bilanci row that
    predates the balance_movements ledger entirely (the 'historical-practice'
    family, synthesized straight from practices columns with no
    payment_movements row at all) — the specific gap left by earlier fixes
    (6d57e23, 9626f26, 8a71a6f): those made the row disappear from the
    ledger view, but never reset the practices columns it came from, so
    get_outstanding_balances (which reads those columns directly whenever a
    practice has zero counted balance_movements rows) kept computing the
    'deleted' amount as still received — and, because it summed a lone
    technical Storno with no replacement into a negative 'received' figure,
    actually inflated the outstanding total instead of correcting it.
    """

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old=(app.DATA,app.DB_PATH,app.DDT_DIR)
        app.DATA=Path(self.temp.name)
        app.DB_PATH=app.DATA/"test.db"
        app.DDT_DIR=app.DATA/"ddt"
        app.init_db()
        self.handler=object.__new__(app.App)
        self.handler.headers={}
        self.redirects=[]
        self.handler.redirect=lambda url:self.redirects.append(url)
        with app.db() as connection:
            self.admin=connection.execute(
                "SELECT * FROM users WHERE username='admin'"
            ).fetchone()

    def tearDown(self):
        app.DATA,app.DB_PATH,app.DDT_DIR=self.old
        self.temp.cleanup()

    def deposit_practice(
        self,number,*,circuit="W",amount="100.00",total=300,
        deposit_paid_at="2026-07-10",status="Acconto",
    ):
        stamp=app.now()
        with app.db() as connection:
            if circuit=="W":
                fields=("price_cremation","total_service","deposit","deposit_paid_at")
                values=(str(total),str(total),amount,deposit_paid_at)
            else:
                fields=("total_text","deposit_final","deposit_paid_at")
                values=(str(total),amount,deposit_paid_at)
            columns=(
                "practice_number,request_origin,destination_branch,status,created_at,"
                "updated_at,created_by,owner_first_name,service_type,payment_status,"
                +",".join(fields)
            )
            placeholders=",".join("?" for _ in range(10+len(fields)))
            pid=connection.execute(
                f"INSERT INTO practices({columns}) VALUES({placeholders})",
                (
                    number,"Privato","Livorno","Ritirato",stamp,stamp,self.admin["id"],
                    "Bilbo","Cremazione singola",status,*values,
                ),
            ).lastrowid
        return pid

    def paid_practice(self,number,*,circuit="W",total=300,paid_at="2026-07-15"):
        stamp=app.now()
        with app.db() as connection:
            if circuit=="W":
                fields=("price_cremation","total_service","paid_at")
                values=(str(total),str(total),paid_at)
            else:
                fields=("total_text","paid_at")
                values=(str(total),paid_at)
            columns=(
                "practice_number,request_origin,destination_branch,status,created_at,"
                "updated_at,created_by,owner_first_name,service_type,payment_status,"
                +",".join(fields)
            )
            placeholders=",".join("?" for _ in range(10+len(fields)))
            pid=connection.execute(
                f"INSERT INTO practices({columns}) VALUES({placeholders})",
                (
                    number,"Privato","Livorno","Ritirato",stamp,stamp,self.admin["id"],
                    "Bilbo","Cremazione singola","Pagato",*values,
                ),
            ).lastrowid
        return pid

    def legacy_key_for(self,pid,kind):
        with app.db() as connection:
            movements=get_movements(
                connection,filters=normalize_filters(include_technical=True),
                restrict_practice_id=pid,
            )
        matches=[m for m in movements if m.practice_id==pid and m.idempotency_key.endswith(f":{kind}")]
        self.assertEqual(len(matches),1,f"expected exactly one :{kind} row for practice {pid}")
        return matches[0]

    def delete(self,legacy_key):
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":legacy_key}
        self.handler.balance_legacy_movement_delete(self.admin)

    def outstanding_for(self,pid,date_to="2026-12-31"):
        with app.db() as connection:
            rows=get_outstanding_balances(
                connection,filters=normalize_filters(date_to=date_to)
            )
        return next((row for row in rows if row.practice_id==pid),None)

    # 1. modern movement, delete via id -----------------------------------
    def test_modern_movement_deletes_by_id_and_updates_totals(self):
        with app.db() as connection:
            movement=create_movement(
                connection,amount_cents=15000,movement_date="2026-07-10",
                category="W",ledger_section="Entrata",movement_type="Entrata manuale",
                idempotency_key="modern-1",description="Entrata di prova",
                source="manual_income",created_by=self.admin["id"],
            )
        self.handler.form=lambda:{"return_to":"/bilanci"}
        self.handler.balance_movement_delete(self.admin,movement.id)
        self.assertTrue(self.redirects and "movimento_stornato=1" in self.redirects[-1])
        with app.db() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM balance_movements WHERE id=?",(movement.id,)
                ).fetchone()
            )

    # 2/3/4/5. every legacy column family, both circuits -------------------
    def test_legacy_deposit_w_circuit_clears_columns_and_fixes_outstanding(self):
        pid=self.deposit_practice("CR-DEP-W",circuit="W",amount="100.00",total=300)
        legacy=self.legacy_key_for(pid,"deposit")
        self.delete(legacy.idempotency_key)
        with app.db() as connection:
            practice=connection.execute(
                "SELECT payment_status,deposit,deposit_paid_at FROM practices WHERE id=?",(pid,)
            ).fetchone()
        self.assertEqual(practice["payment_status"],"Da saldare")
        self.assertEqual(practice["deposit"],"")
        self.assertEqual(practice["deposit_paid_at"],"")
        outstanding=self.outstanding_for(pid)
        self.assertIsNotNone(outstanding)
        self.assertEqual(outstanding.remaining_cents,30000)

    def test_legacy_deposit_d_circuit_clears_deposit_final_and_fixes_outstanding(self):
        pid=self.deposit_practice("CR-DEP-D",circuit="D",amount="100.00",total=300)
        legacy=self.legacy_key_for(pid,"deposit")
        self.delete(legacy.idempotency_key)
        with app.db() as connection:
            practice=connection.execute(
                "SELECT payment_status,deposit_final,deposit_paid_at FROM practices WHERE id=?",(pid,)
            ).fetchone()
        self.assertEqual(practice["payment_status"],"Da saldare")
        self.assertEqual(practice["deposit_final"],"")
        self.assertEqual(practice["deposit_paid_at"],"")
        outstanding=self.outstanding_for(pid)
        self.assertIsNotNone(outstanding)
        self.assertEqual(outstanding.remaining_cents,30000)

    def test_legacy_balance_w_circuit_reverts_to_da_saldare_and_fixes_outstanding(self):
        pid=self.paid_practice("CR-BAL-W",circuit="W",total=300)
        legacy=self.legacy_key_for(pid,"balance")
        self.delete(legacy.idempotency_key)
        with app.db() as connection:
            practice=connection.execute(
                "SELECT payment_status,paid_at,remaining_balance FROM practices WHERE id=?",(pid,)
            ).fetchone()
        self.assertEqual(practice["payment_status"],"Da saldare")
        self.assertEqual(practice["paid_at"],"")
        self.assertEqual(practice["remaining_balance"],"300.00")
        outstanding=self.outstanding_for(pid)
        self.assertIsNotNone(outstanding)
        self.assertEqual(outstanding.remaining_cents,30000)

    def test_legacy_balance_d_circuit_reverts_to_da_saldare_and_fixes_outstanding(self):
        pid=self.paid_practice("CR-BAL-D",circuit="D",total=300)
        legacy=self.legacy_key_for(pid,"balance")
        self.delete(legacy.idempotency_key)
        with app.db() as connection:
            practice=connection.execute(
                "SELECT payment_status,paid_at,remaining_final FROM practices WHERE id=?",(pid,)
            ).fetchone()
        self.assertEqual(practice["payment_status"],"Da saldare")
        self.assertEqual(practice["paid_at"],"")
        self.assertEqual(practice["remaining_final"],"300.00")
        outstanding=self.outstanding_for(pid)
        self.assertIsNotNone(outstanding)
        self.assertEqual(outstanding.remaining_cents,30000)

    def test_legacy_balance_survives_deleting_only_the_deposit_leg(self):
        # A practice old enough to show BOTH a :deposit and a :balance row
        # (paid via an historical deposit then a separate final settlement,
        # no payment_movements row for either): deleting only the deposit
        # must not touch the still-standing balance leg's own identity, and
        # the practice must stay internally consistent (Pagato, remaining 0)
        # since the balance leg alone is still recorded as received.
        pid=self.deposit_practice("CR-BOTH",circuit="W",amount="100.00",total=300,status="Pagato")
        with app.db() as connection:
            connection.execute("UPDATE practices SET paid_at=? WHERE id=?",("2026-07-15",pid))
        deposit_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.legacy_key_for(pid,"balance")  # sanity: both rows exist before deleting either
        self.delete(deposit_key)
        with app.db() as connection:
            practice=connection.execute(
                "SELECT payment_status,deposit,remaining_balance FROM practices WHERE id=?",(pid,)
            ).fetchone()
            movements=get_movements(connection,filters=normalize_filters())
        self.assertEqual(practice["payment_status"],"Pagato")
        self.assertEqual(practice["deposit"],"")
        self.assertEqual(practice["remaining_balance"],"0.00")
        remaining_rows=[m for m in movements if m.practice_id==pid]
        self.assertEqual(len(remaining_rows),1)
        self.assertTrue(remaining_rows[0].idempotency_key.endswith(":balance"))
        outstanding=self.outstanding_for(pid)
        self.assertIsNone(outstanding,"a Pagato practice with remaining=0 must not show as outstanding")

    # 6. two legacy rows on the same practice, same amount ------------------
    def test_two_legacy_rows_same_practice_same_amount_are_never_confused(self):
        pid=self.deposit_practice("CR-SAMEAMT",circuit="W",amount="150.00",total=300,status="Pagato")
        with app.db() as connection:
            connection.execute("UPDATE practices SET paid_at=? WHERE id=?",("2026-07-15",pid))
        deposit=self.legacy_key_for(pid,"deposit")
        balance=self.legacy_key_for(pid,"balance")
        # total(300)-deposit(150)=150: both legs really do carry the same amount.
        self.assertEqual(deposit.amount_cents,balance.amount_cents)
        self.assertNotEqual(deposit.idempotency_key,balance.idempotency_key)
        self.delete(deposit.idempotency_key)
        with app.db() as connection:
            movements=get_movements(connection,filters=normalize_filters(),restrict_practice_id=pid)
        remaining=[m for m in movements if m.practice_id==pid]
        self.assertEqual(len(remaining),1)
        self.assertTrue(remaining[0].idempotency_key.endswith(":balance"))

    # 7. two different practices, same amount and date ----------------------
    def test_two_different_practices_same_amount_and_date_are_never_confused(self):
        first=self.deposit_practice("CR-TWIN-1",amount="100.00",total=300,deposit_paid_at="2026-07-10")
        second=self.deposit_practice("CR-TWIN-2",amount="100.00",total=300,deposit_paid_at="2026-07-10")
        first_key=self.legacy_key_for(first,"deposit")
        second_key=self.legacy_key_for(second,"deposit")
        self.assertEqual(first_key.amount_cents,second_key.amount_cents)
        self.assertEqual(first_key.movement_date,second_key.movement_date)
        self.assertNotEqual(first_key.idempotency_key,second_key.idempotency_key)
        self.delete(first_key.idempotency_key)
        with app.db() as connection:
            first_practice=connection.execute("SELECT deposit FROM practices WHERE id=?",(first,)).fetchone()
            second_practice=connection.execute("SELECT deposit FROM practices WHERE id=?",(second,)).fetchone()
        self.assertEqual(first_practice["deposit"],"")
        self.assertEqual(second_practice["deposit"],"100.00")

    # 8. second delete attempt on the same row -------------------------------
    def test_second_delete_attempt_is_reported_not_found_and_changes_nothing_further(self):
        pid=self.deposit_practice("CR-TWICE",amount="100.00",total=300)
        legacy_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.delete(legacy_key)
        with app.db() as connection:
            practice_once=dict(connection.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone())
        pages=[]
        self.handler.balances_page=lambda user,error="",expense_draft=None:pages.append(error)
        self.delete(legacy_key)
        self.assertTrue(pages and "non trovato" in pages[-1].lower())
        with app.db() as connection:
            practice_twice=dict(connection.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone())
        self.assertEqual(practice_once,practice_twice)

    # 9. pre-existing manual_delete storno is still recognized --------------
    def test_pre_existing_manual_delete_storno_still_hides_the_row(self):
        pid=self.deposit_practice("CR-OLDDEL",amount="100.00",total=300)
        legacy_key=f"historical-practice:{pid}:deposit"
        with app.db() as connection:
            create_legacy_reversal(
                connection,legacy_key=legacy_key,amount_cents=10000,category="W",
                ledger_section="Entrata",movement_date="2026-07-10",practice_id=pid,
                practice_number_snapshot="CR-OLDDEL",source="manual_delete",
                created_by=self.admin["id"],
            )
            movements=get_movements(connection,filters=normalize_filters())
        self.assertFalse(any(m.practice_id==pid for m in movements))

    # 10. pre-existing manual_void storno is still recognized ---------------
    def test_pre_existing_manual_void_storno_still_hides_the_row(self):
        pid=self.deposit_practice("CR-OLDVOID",amount="100.00",total=300)
        legacy_key=f"historical-practice:{pid}:deposit"
        with app.db() as connection:
            create_legacy_reversal(
                connection,legacy_key=legacy_key,amount_cents=10000,category="W",
                ledger_section="Entrata",movement_date="2026-07-10",practice_id=pid,
                practice_number_snapshot="CR-OLDVOID",source="manual_void",
                created_by=self.admin["id"],
            )
            movements=get_movements(connection,filters=normalize_filters())
        self.assertFalse(any(m.practice_id==pid for m in movements))

    # 11. a duplicate storno cannot be created -------------------------------
    def test_a_duplicate_legacy_reversal_cannot_be_created_twice(self):
        pid=self.deposit_practice("CR-DUPVOID",amount="100.00",total=300)
        legacy_key=f"historical-practice:{pid}:deposit"
        with app.db() as connection:
            first=create_legacy_reversal(
                connection,legacy_key=legacy_key,amount_cents=10000,category="W",
                ledger_section="Entrata",movement_date="2026-07-10",practice_id=pid,
                practice_number_snapshot="CR-DUPVOID",source="manual_void",
                created_by=self.admin["id"],
            )
            second=create_legacy_reversal(
                connection,legacy_key=legacy_key,amount_cents=10000,category="W",
                ledger_section="Entrata",movement_date="2026-07-10",practice_id=pid,
                practice_number_snapshot="CR-DUPVOID",source="manual_void",
                created_by=self.admin["id"],
            )
            self.assertEqual(first.id,second.id)
            count=connection.execute(
                "SELECT COUNT(*) n FROM balance_movements WHERE idempotency_key=?",
                (f"legacy-void:v1:{legacy_key}",),
            ).fetchone()["n"]
        self.assertEqual(count,1)

    # 12. the storno must not appear in the Bilanci list ---------------------
    def test_deleted_legacy_row_never_appears_in_the_default_movements_list(self):
        pid=self.deposit_practice("CR-HIDDEN",amount="100.00",total=300)
        legacy_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.delete(legacy_key)
        with app.db() as connection:
            default_view=get_movements(connection,filters=normalize_filters())
            audit_view=get_movements(connection,filters=normalize_filters(include_technical=True))
        self.assertFalse(any(m.practice_id==pid for m in default_view))
        # even the audit view (which shows technical rows) must not show the
        # deleted row as a positive receipt still counted anywhere.
        self.assertFalse(any(
            m.practice_id==pid and m.idempotency_key==legacy_key for m in audit_view
        ))

    # 13. totals must not be corrupted by the deletion -----------------------
    def test_deleting_a_legacy_row_does_not_corrupt_the_balance_snapshot_totals(self):
        pid=self.deposit_practice("CR-TOTALS",amount="100.00",total=300)
        with app.db() as connection:
            before=get_balance_snapshot(connection,filters=normalize_filters(date_to="2026-12-31"))
        self.assertEqual(before.sections["entrate-w"].total_cents,10000)
        self.assertEqual(before.sections["da-riscuotere-w"].total_cents,20000)
        legacy_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.delete(legacy_key)
        with app.db() as connection:
            after=get_balance_snapshot(connection,filters=normalize_filters(date_to="2026-12-31"))
        self.assertEqual(after.sections["entrate-w"].total_cents,0)
        self.assertEqual(after.sections["da-riscuotere-w"].total_cents,30000)
        for section in after.sections.values():
            self.assertEqual(section.total_cents,sum(section.row_amounts_cents))

    # 14. refresh: querying again must show the same (deleted) state --------
    def test_refresh_after_delete_shows_the_same_deleted_state(self):
        pid=self.deposit_practice("CR-REFRESH",amount="100.00",total=300)
        legacy_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.delete(legacy_key)
        with app.db() as connection:
            first_load=get_movements(connection,filters=normalize_filters())
            second_load=get_movements(connection,filters=normalize_filters())
        self.assertFalse(any(m.practice_id==pid for m in first_load))
        self.assertFalse(any(m.practice_id==pid for m in second_load))

    # 15. filters keep working after the deletion ----------------------------
    def test_filters_apply_correctly_after_a_legacy_deletion(self):
        pid=self.deposit_practice("CR-FILTERED",amount="100.00",total=300,deposit_paid_at="2026-07-10")
        legacy_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.delete(legacy_key)
        with app.db() as connection:
            by_category=get_movements(connection,filters=normalize_filters(category="W"))
            by_date=get_movements(
                connection,
                filters=normalize_filters(date_from="2026-07-01",date_to="2026-07-31"),
            )
        self.assertFalse(any(m.practice_id==pid for m in by_category))
        self.assertFalse(any(m.practice_id==pid for m in by_date))

    # 16. performance on a large database ------------------------------------
    def test_deleting_a_legacy_row_stays_fast_with_thousands_of_practices(self):
        stamp=app.now()
        with app.db() as connection:
            rows=[
                (
                    f"CR-BULK-{i}","Privato","Livorno","Ritirato",stamp,stamp,
                    self.admin["id"],"Fido","Cremazione singola","Acconto",
                    "300","300","100.00","2026-07-01",
                )
                for i in range(4000)
            ]
            connection.executemany(
                """INSERT INTO practices(
                     practice_number,request_origin,destination_branch,status,
                     created_at,updated_at,created_by,animal_name,service_type,
                     payment_status,price_cremation,total_service,deposit,deposit_paid_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        target=self.deposit_practice("CR-BULK-TARGET",amount="150.00",total=300,deposit_paid_at="2026-07-20")
        legacy_key=self.legacy_key_for(target,"deposit").idempotency_key
        started=time.perf_counter()
        self.delete(legacy_key)
        elapsed=time.perf_counter()-started
        self.assertTrue(self.redirects and "movimento_stornato=1" in self.redirects[-1])
        # generous ceiling: this used to scan/resynthesize every practice and
        # payment_movements row in the database on every single delete
        # confirmation; restrict_practice_id turns it into an indexed lookup
        # against one practice, so even a few thousand rows must stay well
        # under a second, not scale with the database's total size.
        self.assertLess(elapsed,2.0,f"legacy delete took {elapsed:.3f}s with 4000 practices in the db")

    # 17. movement not found reports a clear, specific error ----------------
    def test_movement_not_found_reports_a_clear_error_not_a_generic_success(self):
        pages=[]
        self.handler.balances_page=lambda user,error="",expense_draft=None:pages.append(error)
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":"historical-practice:999999:deposit"}
        self.handler.balance_legacy_movement_delete(self.admin)
        self.assertFalse(self.redirects)
        self.assertTrue(pages and "non trovato" in pages[-1].lower())

    def test_garbage_legacy_key_reports_a_clear_error_not_a_crash(self):
        pages=[]
        self.handler.balances_page=lambda user,error="",expense_draft=None:pages.append(error)
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":"totalmente-inventata"}
        self.handler.balance_legacy_movement_delete(self.admin)
        self.assertFalse(self.redirects)
        self.assertTrue(pages and "non trovato" in pages[-1].lower())

    # 18. a db error must not report a false success -------------------------
    def test_a_db_error_never_reports_success_or_silently_swallows_the_failure(self):
        pid=self.deposit_practice("CR-DBERROR",amount="100.00",total=300)
        legacy_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":legacy_key}
        with patch(
            "app.create_balance_legacy_reversal",
            side_effect=app.sqlite3.OperationalError("database is locked"),
        ):
            with self.assertRaises(app.sqlite3.OperationalError):
                self.handler.balance_legacy_movement_delete(self.admin)
        self.assertFalse(self.redirects,"no redirect (=no success signal) may reach the browser on a db error")
        with app.db() as connection:
            practice=connection.execute("SELECT deposit FROM practices WHERE id=?",(pid,)).fetchone()
        # the column-clearing update runs inside the same `with db() as c:`
        # transaction as the reversal insert that then failed: sqlite's
        # context manager must roll the whole block back together, not leave
        # the columns half-updated with no matching ledger-visible deletion.
        self.assertEqual(practice["deposit"],"100.00")

    # concurrent / double-click submission ------------------------------------
    def test_double_submit_of_the_same_delete_is_idempotent_not_duplicated(self):
        pid=self.deposit_practice("CR-DBLCLICK",amount="100.00",total=300)
        legacy_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.delete(legacy_key)
        with app.db() as connection:
            reversal_count_once=connection.execute(
                "SELECT COUNT(*) n FROM balance_movements WHERE idempotency_key=?",
                (f"legacy-void:v1:{legacy_key}",),
            ).fetchone()["n"]
        pages=[]
        self.handler.balances_page=lambda user,error="",expense_draft=None:pages.append(error)
        self.delete(legacy_key)  # the second, near-simultaneous submission
        with app.db() as connection:
            reversal_count_twice=connection.execute(
                "SELECT COUNT(*) n FROM balance_movements WHERE idempotency_key=?",
                (f"legacy-void:v1:{legacy_key}",),
            ).fetchone()["n"]
        self.assertEqual(reversal_count_once,1)
        self.assertEqual(reversal_count_twice,1)
        self.assertTrue(pages and "non trovato" in pages[-1].lower())

    # restore ------------------------------------------------------------------
    def test_restore_puts_back_the_practice_columns_a_legacy_delete_cleared(self):
        pid=self.deposit_practice("CR-RESTORE-DEP",amount="100.00",total=300,deposit_paid_at="2026-07-10")
        legacy_key=self.legacy_key_for(pid,"deposit").idempotency_key
        self.delete(legacy_key)
        with app.db() as connection:
            deletion_id=get_recent_movement_deletions(connection,limit=1)[0]["id"]
            restore_movement_deletion(connection,deletion_id=deletion_id,restored_by=self.admin["id"])
        with app.db() as connection:
            practice=connection.execute(
                "SELECT payment_status,deposit,deposit_paid_at FROM practices WHERE id=?",(pid,)
            ).fetchone()
            movements=get_movements(connection,filters=normalize_filters())
        self.assertEqual(practice["payment_status"],"Acconto")
        self.assertEqual(practice["deposit"],"100.00")
        self.assertEqual(practice["deposit_paid_at"],"2026-07-10")
        self.assertTrue(any(m.idempotency_key==legacy_key for m in movements))
        with app.db() as connection:
            with self.assertRaises(InvalidMovementError):
                restore_movement_deletion(connection,deletion_id=deletion_id,restored_by=self.admin["id"])


if __name__=="__main__":
    unittest.main()
