import tempfile
import unittest
from pathlib import Path

import app
from balance_service import (
    create_manual_expense,
    create_manual_income,
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
            "totale-w-attuale":32000,
            "totale-d-attuale":8000,
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
        self.assertEqual(self.snapshot(status="Acconto").sections["entrate-w"].total_cents,35000)
        self.assertEqual(self.snapshot(status="Da saldare").sections["entrate-w"].total_cents,0)
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

    def test_manual_income_w_d_and_current_totals(self):
        with app.db() as connection:
            create_manual_income(
                connection,amount_cents=7000,movement_date="2026-07-14",
                category="W",payment_method="Pos",description="Entrata W extra",
                idempotency_key="income-manual-w",created_by=self.admin["id"],
            )
            create_manual_income(
                connection,amount_cents=9000,movement_date="2026-07-15",
                category="D",payment_method="Contanti",description="Entrata D extra",
                idempotency_key="income-manual-d",created_by=self.admin["id"],
            )
            snapshot=get_balance_snapshot(
                connection,filters=normalize_filters(
                    date_from="2026-07-01",date_to="2026-07-31"
                )
            )
        self.assertEqual(snapshot.sections["entrate-w"].total_cents,42000)
        self.assertEqual(snapshot.sections["entrate-d"].total_cents,19000)
        self.assertEqual(snapshot.sections["totale-w-attuale"].total_cents,39000)
        self.assertEqual(snapshot.sections["totale-d-attuale"].total_cents,17000)
        for key in ("totale-w-attuale","totale-d-attuale"):
            section=snapshot.sections[key]
            self.assertEqual(section.total_cents,sum(section.row_amounts_cents))

    def test_manual_income_post_is_idempotent(self):
        form={
            "entry_type":"income","return_to":"/bilanci",
            "balance_idempotency_key":"manual-income-post",
            "movement_date":"2026-07-19","amount":"12,50","category":"W",
            "payment_method":"Pos","description":"Entrata sportello","notes":"nota",
        }
        self.handler.form=lambda:form
        redirects=[]
        self.handler.redirect=redirects.append
        self.handler.balance_income_submit(self.admin)
        self.handler.balance_income_submit(self.admin)
        with app.db() as connection:
            rows=connection.execute(
                "SELECT * FROM balance_movements WHERE idempotency_key=?",
                ("manual-income:manual-income-post",),
            ).fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["amount_cents"],1250)
        self.assertIn("Entrata sportello",rows[0]["description"])


    # ------------------------------------------------------------------
    # Entrata manuale W con dati fattura
    # ------------------------------------------------------------------
    def post_income(self,**overrides):
        form={
            "entry_type":"income","return_to":"/bilanci",
            "balance_idempotency_key":overrides.pop("key","inv-key"),
            "movement_date":"2026-07-19","amount":"120,00","category":"W",
            "payment_method":"Pos","description":"Vendita sportello",
        }
        form.update(overrides)
        self.handler.form=lambda:form
        self.handler.redirect=lambda url:None
        errors=[]
        self.handler.balances_page=lambda user,error="",expense_draft=None:errors.append(error)
        self.handler.balance_income_submit(self.admin)
        return errors

    def income_row(self,key="inv-key"):
        with app.db() as connection:
            return connection.execute(
                "SELECT * FROM balance_movements WHERE idempotency_key=?",
                (f"manual-income:{key}",),
            ).fetchone()

    def test_manual_income_w_saves_invoice_with_explicit_values(self):
        errors=self.post_income(invoice_number="FT-77/2026",invoice_date="2026-07-18",invoice_total="100,50")
        self.assertEqual(errors,[])
        row=self.income_row()
        invoice=app.manual_income_invoice(row["metadata_json"])
        self.assertEqual(invoice,{"number":"FT-77/2026","date":"2026-07-18","total":"100.50"})

    def test_manual_income_w_invoice_total_defaults_to_entry_amount(self):
        errors=self.post_income(invoice_number="FT-1",invoice_date="2026-07-19",invoice_total="")
        self.assertEqual(errors,[])
        invoice=app.manual_income_invoice(self.income_row()["metadata_json"])
        self.assertEqual(invoice["total"],"120.00")

    def test_manual_income_w_invoice_date_may_stay_blank(self):
        errors=self.post_income(invoice_number="FT-NODATE",invoice_total="")
        self.assertEqual(errors,[])
        invoice=app.manual_income_invoice(self.income_row()["metadata_json"])
        self.assertEqual((invoice["number"],invoice["date"]),("FT-NODATE",""))

    def test_manual_income_invalid_invoice_fields_are_rejected_without_saving(self):
        for key,extra,message in (
            ("bad-total",{"invoice_number":"FT-9","invoice_total":"12abc"},"Importo fattura non valido"),
            ("zero-total",{"invoice_number":"FT-9","invoice_total":"0"},"Importo fattura non valido"),
            ("bad-date",{"invoice_number":"FT-9","invoice_date":"19/07/2026"},"Data fattura non valida"),
            ("impossible-date",{"invoice_number":"FT-9","invoice_date":"2026-02-31"},"Data fattura non valida"),
            ("date-no-number",{"invoice_date":"2026-07-19"},"numero fattura"),
        ):
            with self.subTest(key=key):
                errors=self.post_income(key=key,**extra)
                self.assertEqual(len(errors),1)
                self.assertIn(message,errors[0])
                self.assertIsNone(self.income_row(key))

    def test_manual_income_without_invoice_number_or_non_w_has_no_invoice(self):
        self.assertEqual(self.post_income(key="no-num",invoice_total="120,00"),[])
        self.assertEqual(self.income_row("no-num")["metadata_json"],"")
        self.assertEqual(self.post_income(key="cat-d",category="D",invoice_number="FT-D",invoice_date="2026-07-19",invoice_total="120,00"),[])
        self.assertEqual(self.income_row("cat-d")["metadata_json"],"")
        self.assertIsNone(app.manual_income_invoice(self.income_row("cat-d")["metadata_json"]))

    def test_manual_income_form_has_invoice_section_only_visible_for_w(self):
        pages=[]
        self.handler.send_html=lambda content,*args:pages.append(content)
        self.handler.path="/bilanci"
        self.handler.balances_page(self.admin)
        html=pages[-1]
        for name in ('name="invoice_number"','name="invoice_date"','name="invoice_total"'):
            self.assertIn(name,html)
        self.assertIn("Numero fattura",html)
        self.assertIn("Importo fattura €",html)
        self.assertIn("Data fattura",html)
        section=html[html.index("data-manual-income-invoice"):][:80]
        self.assertNotIn("hidden",section)
        self.assertIn("ppmSyncManualIncomeInvoice(this.form)",html)
        # bozza dopo errore con categoria D: sezione nascosta, valori ripopolati
        pages.clear()
        self.handler.balances_page(self.admin,error="x",expense_draft={
            "entry_type":"income","category":"D","amount":"5","invoice_number":"FT-KEEP",
        })
        html=pages[-1]
        self.assertIn("hidden",html[html.index("data-manual-income-invoice"):][:80])
        self.assertIn('value="FT-KEEP"',html)

    def test_manual_income_invoice_is_shown_in_balance_movements_and_survives_delete_restore(self):
        self.post_income(invoice_number="FT-BIL-5",invoice_date="2026-07-18",invoice_total="100,50",
                         description="Entrata con fattura bilancio")
        self.handler.__dict__.pop("balances_page",None)
        pages=[]
        self.handler.send_html=lambda content,*args:pages.append(content)
        self.handler.path="/bilanci?periodo=personalizzato&data_iniziale=2026-07-01&data_finale=2026-07-31&view=entrate-w"
        self.handler.balances_page(self.admin)
        self.assertIn("Fattura FT-BIL-5",pages[-1])
        self.assertIn("18/07/2026",pages[-1])
        self.assertIn("100,50",pages[-1])
        movement_id=self.income_row()["id"]
        redirects=[];self.handler.redirect=redirects.append
        self.handler.form=lambda:{"return_to":"/bilanci"}
        self.handler.balance_movement_delete(self.admin,movement_id)
        self.assertIsNone(self.income_row())
        with app.db() as connection:
            deletion_id=connection.execute("SELECT id FROM balance_movement_deletions ORDER BY id DESC LIMIT 1").fetchone()["id"]
        self.handler.balance_movement_deletion_restore(self.admin,deletion_id)
        restored=self.income_row()
        self.assertIsNotNone(restored)
        self.assertEqual(app.manual_income_invoice(restored["metadata_json"])["number"],"FT-BIL-5")

    def fatture_html(self,query=""):
        pages=[]
        self.handler.send_html=lambda content,*args:pages.append(content)
        self.handler.path="/fatture"+query
        self.handler.invoices_page(self.admin)
        return pages[-1]

    def test_manual_income_invoice_listed_in_fatture_with_filters(self):
        self.post_income(key="f1",invoice_number="FT-LIST-1",invoice_date="2026-07-18",invoice_total="100,50",
                         description="Cliente Banco Rossi")
        self.post_income(key="f2",invoice_number="FT-LIST-2",invoice_date="2026-09-02",invoice_total="",
                         amount="80,00",description="Altro incasso")
        html=self.fatture_html()
        self.assertIn("FT-LIST-1",html)
        self.assertIn("FT-LIST-2",html)
        self.assertIn("Entrata manuale",html)
        self.assertIn("Cliente Banco Rossi",html)
        self.assertIn("€ 100,50",html)
        self.assertIn("€ 80,00",html)  # importo fattura vuoto = importo entrata
        self.assertIn("/bilanci?",html)
        # filtro testo (anche senza accenti/maiuscole)
        html=self.fatture_html("?q=banco")
        self.assertIn("FT-LIST-1",html);self.assertNotIn("FT-LIST-2",html)
        html=self.fatture_html("?q=ft-list-2")
        self.assertIn("FT-LIST-2",html);self.assertNotIn("FT-LIST-1",html)
        # filtro date sulla data fattura
        html=self.fatture_html("?dal=2026-08-01")
        self.assertIn("FT-LIST-2",html);self.assertNotIn("FT-LIST-1",html)
        html=self.fatture_html("?al=2026-07-31")
        self.assertIn("FT-LIST-1",html);self.assertNotIn("FT-LIST-2",html)
        # "Da fatturare" non mostra le entrate manuali fatturate
        html=self.fatture_html("?tipo=da_fatturare")
        self.assertNotIn("FT-LIST-1",html)

    def test_manual_income_invoice_shares_number_with_practice_invoice_group(self):
        with app.db() as connection:
            connection.execute(
                "UPDATE practices SET invoice_number='FT-SHARED',invoice_date='2026-07-10',invoice_total='50' WHERE id=?",
                (self.w_id,),
            )
        self.post_income(invoice_number="ft-shared",invoice_date="2026-07-19",invoice_total="70,00")
        html=self.fatture_html()
        self.assertIn("Fatture condivise tra più pratiche",html)
        self.assertIn("2 voci",html)
        self.assertIn("€ 120,00",html)  # 50 + 70

    # ------------------------------------------------------------------
    # Entrata manuale D: nessun metodo di pagamento obbligatorio
    # ------------------------------------------------------------------
    def test_manual_income_d_does_not_require_payment_method(self):
        errors=self.post_income(key="d-nomethod",category="D",payment_method="",description="Entrata D senza metodo")
        self.assertEqual(errors,[])
        row=self.income_row("d-nomethod")
        self.assertIsNotNone(row)
        self.assertEqual((row["category"],row["payment_method"],row["amount_cents"]),("D","",12000))
        # con un metodo scelto resta salvato com'e'
        self.assertEqual(self.post_income(key="d-method",category="D",payment_method="Contanti"),[])
        self.assertEqual(self.income_row("d-method")["payment_method"],"Contanti")

    def test_manual_income_w_and_collaboratori_still_need_a_method_with_clear_message(self):
        errors=self.post_income(key="w-nomethod",category="W",payment_method="")
        self.assertEqual(errors,["Seleziona il metodo di pagamento."])
        self.assertIsNone(self.income_row("w-nomethod"))
        errors=self.post_income(key="c-nomethod",category="Collaboratori",payment_method="")
        self.assertEqual(errors,["Seleziona il metodo di pagamento."])
        errors=self.post_income(key="c-nocollab",category="Collaboratori",payment_method="Pos",collaborator_id="")
        self.assertEqual(errors,["Seleziona il collaboratore."])
        self.assertIsNone(self.income_row("c-nocollab"))

    def test_service_level_method_rule(self):
        with app.db() as connection:
            movement=create_manual_income(
                connection,amount_cents=500,movement_date="2026-07-19",category="D",payment_method="",
                description="D senza metodo",idempotency_key="svc-d",created_by=self.admin["id"],
            )
            self.assertEqual(movement.payment_method,"")
            with self.assertRaises(Exception):
                create_manual_income(
                    connection,amount_cents=500,movement_date="2026-07-19",category="W",payment_method="",
                    description="W senza metodo",idempotency_key="svc-w",created_by=self.admin["id"],
                )

    def test_d_income_without_method_is_listed_in_balances(self):
        self.post_income(key="d-list",category="D",payment_method="",description="Entrata D visibile")
        self.handler.__dict__.pop("balances_page",None)
        pages=[]
        self.handler.send_html=lambda content,*args:pages.append(content)
        self.handler.path="/bilanci?periodo=personalizzato&data_iniziale=2026-07-01&data_finale=2026-07-31&view=entrate-d"
        self.handler.balances_page(self.admin)
        self.assertIn("Entrata D visibile",pages[-1])

    def test_method_field_is_hidden_for_d_in_the_form(self):
        self.handler.__dict__.pop("balances_page",None)
        pages=[]
        self.handler.send_html=lambda content,*args:pages.append(content)
        self.handler.path="/bilanci"
        self.handler.balances_page(self.admin)
        self.assertIn('<div class="field" data-manual-income-method',pages[-1])
        self.assertIn("methodField.hidden=category.value==='D'",pages[-1])
        pages.clear()
        self.handler.balances_page(self.admin,error="x",expense_draft={"entry_type":"income","category":"D","amount":"5"})
        field=pages[-1][pages[-1].index('<div class="field" data-manual-income-method'):][:60]
        self.assertIn("hidden",field)
        pages.clear()
        self.handler.balances_page(self.admin,error="x",expense_draft={"entry_type":"income","category":"W","payment_method":"Pos"})
        field=pages[-1][pages[-1].index('<div class="field" data-manual-income-method'):][:60]
        self.assertNotIn("hidden",field)
        self.assertIn('<option value="Pos" selected>',pages[-1])

    def test_reloading_after_a_rejected_income_or_expense_does_not_404(self):
        for path in ("/bilanci/entrate","/bilanci/uscite","/bilanci/entrate?periodo=mese"):
            with self.subTest(path=path):
                handler=object.__new__(app.App)
                handler.headers={}
                handler.path=path
                handler.user=lambda:self.admin
                redirects=[]
                handler.redirect=redirects.append
                handler._route_get()
                self.assertEqual(redirects,["/bilanci"])


if __name__=="__main__":
    unittest.main()
