import os
import tempfile
import unittest
from pathlib import Path

import app
from notification_service import emit_notification, process_scheduled_notifications
from pypdf import PdfReader


class PetParadiseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def test_notification_schema_and_preferences(self):
        with app.db() as conn:
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("notifications", tables)
            self.assertIn("push_subscriptions", tables)
            subscription_columns = {row["name"] for row in conn.execute("PRAGMA table_info(push_subscriptions)")}
            self.assertTrue({"endpoint", "p256dh", "auth", "user_id", "device_name", "platform", "created_at"}.issubset(subscription_columns))
            admin = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
            emit_notification(conn, "system_error", "Test", "Messaggio", target_user_ids=[admin], db_path=None)
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications").fetchone()["n"], 1)
            conn.execute("INSERT INTO notification_preferences(user_id,type,enabled) VALUES(?,?,0)", (admin, "backup_completed"))
            emit_notification(conn, "backup_completed", "Backup", "OK", target_user_ids=[admin], db_path=None)
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications").fetchone()["n"], 1)

    def test_form_extensions_and_normalization(self):
        html = self.handler.fields_html()
        for expected in ("GIANLUCA", "CALCO PER URNA", "CALCO POLPASTRELLO", "CALCO NASO", "price_paw_cast", "price_nose_cast", "Fiat Fiorino", "Renault Captur", "Dr PK8", "Smaltito"):
            self.assertIn(expected, html)
        data = self.handler.normalized_fields({"owner_tax_code": "rssmra80a01h501u", "service_type": "Da decidere"})
        self.assertEqual(data["owner_tax_code"], "RSSMRA80A01H501U")
        extras = self.handler.normalized_fields({"price_paw_cast":"25,50", "price_nose_cast":"30", "tag_calco_paw":"Si", "tag_calco_nose":"Si"})
        self.assertEqual(extras["price_paw_cast"], "25.50")
        self.assertEqual(extras["tag_calco_nose"], "Si")

    def test_new_budget_invoice_and_transport_fields(self):
        html=self.handler.fields_html()
        for expected in ('name="catalog_sent"','name="payment_method"','name="invoice_number"','name="invoice_date"','name="make_invoice"','Mezzo proprio'):
            self.assertIn(expected,html)
        data=self.handler.normalized_fields({
            "request_origin":"Consegna in sede","send_catalog":"Si","catalog_sent":"Si",
            "payment_method":"Contanti","invoice_number":"F-2026-19","invoice_date":"2026-07-14","make_invoice":"Si",
        })
        self.assertEqual(data["transport_method"],"Mezzo proprio")
        self.assertEqual(data["payment_method"],"Contanti")
        self.assertEqual(data["catalog_sent"],"Si")
        self.assertEqual(data["send_catalog"],"")
        self.assertEqual(data["invoice_number"],"F-2026-19")
        self.assertEqual(data["make_invoice"],"Si")
        self.assertEqual(self.handler.normalized_fields({"owner_city":"livorno"})["owner_city"],"Livorno")

    def test_call_back_practice_can_be_saved_without_required_client_data(self):
        data=self.handler.normalized_fields({"tag_da_richiamare":"Si","service_type":"Da decidere"})
        self.assertEqual(self.handler.validation_error(data),"")
        self.assertEqual(self.handler.is_complete(data),0)
        self.assertFalse(any(data[key] for key in ("owner_first_name","owner_last_name","owner_phone","owner_tax_code","owner_street","owner_city","owner_province","owner_zip")))
        self.assertIn("callBack?.checked",app.APP_JS)
        invalid=self.handler.normalized_fields({"tag_da_richiamare":"Si","price_cremation":"non numerico"})
        self.assertIn("solo numeri",self.handler.validation_error(invalid))

    def test_invoice_page_search_and_unique_code(self):
        with app.db() as conn:
            user=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,invoice_number,invoice_date,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name,owner_first_name)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",("CR-000001","FT-77","2026-07-14","Privato","Livorno","Ritirato",stamp,stamp,user["id"],"Luna","Mario")).lastrowid
            conflict=self.handler.invoice_conflict(conn,"ft-77")
            self.assertEqual(conflict["id"],pid)
        rendered=[];self.handler.send_html=lambda content:rendered.append(content);self.handler.path="/fatture?q=FT-77"
        self.handler.invoices_page(user)
        self.assertIn("FT-77",rendered[-1])
        self.assertIn(f'/pratiche/{pid}',rendered[-1])

    def test_cr_codes_shift_on_delete_and_restore(self):
        with app.db() as conn:
            user=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now();ids=[]
            for number,name in ((3,"Mario"),(4,"Giuseppe"),(5,"Fabio")):
                ids.append(conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,owner_first_name)
                                         VALUES(?,?,?,?,?,?,?,?)""",(f"CR-{number:06d}","Privato","Livorno","Ritirato",stamp,stamp,user["id"],name)).lastrowid)
            conn.execute("UPDATE settings SET value='6' WHERE key='next_cr_number'")
        redirects=[];self.handler.redirect=lambda path:redirects.append(path)
        self.handler.delete_practice(user,ids[0])
        with app.db() as conn:
            deleted=conn.execute("SELECT * FROM practices WHERE id=?",(ids[0],)).fetchone()
            self.assertEqual(deleted["original_practice_number"],"CR-000003")
            self.assertEqual(conn.execute("SELECT practice_number FROM practices WHERE id=?",(ids[1],)).fetchone()["practice_number"],"CR-000003")
            self.assertEqual(conn.execute("SELECT practice_number FROM practices WHERE id=?",(ids[2],)).fetchone()["practice_number"],"CR-000004")
            self.assertEqual(conn.execute("SELECT value FROM settings WHERE key='next_cr_number'").fetchone()["value"],"5")
        self.handler.restore_practice(user,ids[0])
        with app.db() as conn:
            self.assertEqual([conn.execute("SELECT practice_number FROM practices WHERE id=?",(pid,)).fetchone()["practice_number"] for pid in ids],["CR-000003","CR-000004","CR-000005"])
            self.assertEqual(conn.execute("SELECT value FROM settings WHERE key='next_cr_number'").fetchone()["value"],"6")

    def test_whatsapp_is_blocked_for_vet_and_collective(self):
        collective = {"service_type": "Cremazione collettiva", "owner_veterinarian_id": None}
        veterinarian = {"service_type": "Cremazione singola", "owner_veterinarian_id": 2}
        self.assertTrue(self.handler.whatsapp_block_reason(collective))
        self.assertTrue(self.handler.whatsapp_block_reason(veterinarian))

    def test_scheduled_notification_is_idempotent(self):
        with app.db() as conn:
            admin = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
            stamp = app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,data_complete,
                         owner_first_name,owner_last_name,animal_name,pickup_date,pickup_time,created_at,updated_at,created_by)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("PP-TEST", "Privato", "Livorno", "Ritirato", 1, "Mario", "Rossi", "Luna",
                          stamp[:10], "23:59", stamp, stamp, admin))
            first = process_scheduled_notifications(conn, app.DB_PATH)
            second = process_scheduled_notifications(conn, app.DB_PATH)
            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications WHERE type='pickup_today'").fetchone()["n"], 0)

    def test_opening_notification_center_clears_unread_badge(self):
        with app.db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            emit_notification(conn, "system_error", "Badge test", "Da leggere", target_user_ids=[user["id"]])
        self.handler.path = "/notifiche"
        self.handler.send_html = lambda content: None
        self.handler.notifications(user)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications WHERE user_id=? AND is_read=0", (user["id"],)).fetchone()["n"], 0)

    def test_service_worker_handles_push_and_click(self):
        source = (app.ASSETS / "sw.js").read_text(encoding="utf-8")
        self.assertIn("addEventListener('push'", source)
        self.assertIn("addEventListener('notificationclick'", source)
        self.assertIn("pet-paradise-shell-v7", source)

    def test_effective_total_and_cash_flow_use_total_d_once(self):
        whisky = {
            "price_cremation": "410", "total_text": "330", "deposit": "0",
            "payment_status": "Pagato",
        }
        self.assertEqual(app.calculated_service_total(whisky), 410)
        self.assertEqual(app.effective_total(whisky), 330)
        self.assertEqual(app.received_amount(whisky), 330)
        self.assertEqual(app.outstanding_amount(whisky), 0)

        partial = dict(whisky, deposit="100", payment_status="Acconto")
        self.assertEqual(app.received_amount(partial), 100)
        self.assertEqual(app.outstanding_amount(partial), 230)

        ordinary = dict(whisky, total_text="0", deposit="100", payment_status="Acconto")
        self.assertEqual(app.effective_total(ordinary), 410)
        self.assertEqual(app.outstanding_amount(ordinary), 310)

    def test_total_w_is_only_a_visible_rename(self):
        html=self.handler.fields_html()
        self.assertIn("Totale W €",html)
        self.assertNotIn("Totale calcolato",html)
        self.assertNotIn("Totale servizio €",html)
        self.assertEqual(app.MONEY_FIELDS["total_service"],"Totale W")
        with app.db() as conn:
            columns={row["name"] for row in conn.execute("PRAGMA table_info(practices)")}
        self.assertIn("total_service",columns)
        self.assertNotIn("totale_w",columns)

    def test_generated_pdf_shows_total_w_without_changing_technical_field(self):
        output=Path(self.temp.name)/"totale-w.pdf"
        practice={
            "destination_branch":"Livorno","total_service":"410.00","deposit":"100.00",
            "owner_first_name":"Mario","owner_last_name":"Rossi","animal_name":"Whisky",
        }
        app.generate_ddt(practice,app.ASSETS/"DCS_NUOVO.pdf",output)
        text="\n".join(page.extract_text() or "" for page in PdfReader(str(output)).pages)
        self.assertIn("TOTALE W",text)
        self.assertIn("410.00",text)

    def test_historical_w_and_d_practices_keep_their_saved_amounts(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now();before=conn.execute("SELECT count(*) n FROM practices").fetchone()["n"]
            w_id=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,total_service,total_text,deposit,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",("CR-W-STORICA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"410","","100","Acconto")).lastrowid
            d_id=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,total_service,total_text,deposit,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",("CR-D-STORICA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"410","330","100","Acconto")).lastrowid
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.edit_page(admin,w_id);self.assertIn("Totale W",rendered[-1]);self.assertIn('name="total_service" value="410"',rendered[-1])
        self.handler.edit_page(admin,d_id);self.assertIn("Totale W",rendered[-1]);self.assertIn('name="total_text"',rendered[-1]);self.assertIn('>330</textarea>',rendered[-1])
        self.handler.new_page(admin);self.assertIn("Totale W",rendered[-1])
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM practices").fetchone()["n"],before+2)
            saved=conn.execute("SELECT total_service,total_text,deposit FROM practices WHERE id=?",(d_id,)).fetchone()
        self.assertEqual(tuple(saved),("410","330","100"))

    def test_balances_total_d_filter_includes_whisky_and_partial_cash_flow(self):
        today = app.datetime.now().date().isoformat()
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            base = ("Privato", "Livorno", "Ritirato", today, stamp, stamp, admin["id"])
            paid_id=conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,animal_name,price_cremation,total_service,total_text,deposit,payment_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-WHISKY", *base, "Whisky", "410", "410", "330", "0", "Pagato"),
            ).lastrowid
            partial_id=conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,animal_name,price_cremation,total_service,total_text,deposit,payment_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-PARZIALE", *base, "Parziale", "410", "410", "330", "100", "Acconto"),
            ).lastrowid
            self.handler.add_payment_movement(conn,paid_id,"saldo_d","D",330,admin["id"],"Test",stamp)
            self.handler.add_payment_movement(conn,partial_id,"acconto_d","D",100,admin["id"],"Test",stamp)

        rendered = []
        self.handler.send_html = lambda content: rendered.append(content)
        self.handler.path = f"/bilanci?dal={today}&al={today}&voce=totale_d"
        self.handler.balances_v2(admin)
        self.assertIn("PP-WHISKY", rendered[-1])
        self.assertIn("PP-PARZIALE", rendered[-1])
        self.assertIn("€ 430,00", rendered[-1])
        self.assertIn('/pratiche/', rendered[-1])

        self.handler.path = f"/bilanci?dal={today}&al={today}&voce=da_entrare_d"
        self.handler.balances_v2(admin)
        self.assertIn("PP-PARZIALE", rendered[-1])
        self.assertIn("€ 230,00", rendered[-1])

    def test_normalization_keeps_custom_plate_and_calculates_remaining(self):
        data = self.handler.normalized_fields({
            "transport_method": "Fiat Fiorino", "vehicle_plate": "TARGA LIBERA",
            "price_cremation": "300", "price_paw_cast": "30", "total_text": "250",
            "deposit": "100", "payment_status": "Acconto",
        })
        self.assertEqual(data["vehicle_plate"], "TARGA LIBERA")
        self.assertEqual(data["total_service"], "330.00")
        self.assertEqual(data["remaining_balance"], "150.00")

    def test_articles_and_new_notification_types_are_initialized(self):
        with app.db() as conn:
            names = {row["name"] for row in conn.execute("SELECT name FROM articles")}
        self.assertEqual(names, {
            "Sacchi per ritiro", "Boccette pelo", "Certificati",
            "Sacchetti riconsegna", "Sacchetti ceneri",
        })
        self.assertIn("catalog_sent", app.NOTIFICATION_TYPES)
        self.assertIn("article_ordered", app.NOTIFICATION_TYPES)

    def test_pdf_urn_inventory_is_imported_once_with_exact_totals(self):
        with app.db() as conn:
            rows = conn.execute("SELECT name,material,price,quantity FROM urns WHERE active=1").fetchall()
            movements = conn.execute("SELECT count(*) n FROM urn_movements WHERE movement_type='Importazione inventario'").fetchone()["n"]
        self.assertEqual(len(rows), 85)
        self.assertEqual(sum(row["quantity"] for row in rows), 80)
        self.assertEqual(sum(row["quantity"] * app.money_value(row["price"]) for row in rows), 5900)
        self.assertEqual(movements, 85)
        self.assertEqual({row["material"] for row in rows}, {"Legno", "Ceramica", "Metallo"})
        self.assertIn("Salto d’Amore Bianca", {row["name"] for row in rows})

        app.init_db()
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM urns WHERE active=1").fetchone()["n"], 85)
            self.assertEqual(conn.execute("SELECT count(*) n FROM urn_movements WHERE movement_type='Importazione inventario'").fetchone()["n"], 85)

    def test_urn_catalog_schema_selection_and_stock_movements(self):
        with app.db() as conn:
            admin = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
            stamp = app.now()
            cur = conn.execute(
                """INSERT INTO urns(name,material,internal_code,price,quantity,low_stock_threshold,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("Urna prova", "Legno", "URN-TEST", "85.00", 2, 3, stamp, stamp),
            )
            urn_id = cur.lastrowid
            self.handler.adjust_urn_stock(conn, urn_id, -1, "Utilizzata nella pratica", None, admin)
            self.handler.adjust_urn_stock(conn, urn_id, 1, "Restituita dalla pratica", None, admin)
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (urn_id,)).fetchone()["quantity"], 2)
            self.assertEqual(conn.execute("SELECT count(*) n FROM urn_movements WHERE urn_id=?", (urn_id,)).fetchone()["n"], 2)

        data = self.handler.normalized_fields({
            "urn_id": str(urn_id), "urn_id_2": str(urn_id), "price_cremation": "200", "deposit": "50",
        })
        self.assertEqual(data["urn_id"], urn_id)
        self.assertEqual(data["urn_id_2"], urn_id)
        self.assertEqual(data["urn_notes"], "Urna prova")
        self.assertEqual(data["urn_notes_2"], "Urna prova")
        self.assertEqual(data["price_urn"], "85.00")
        self.assertEqual(data["price_urn_2"], "85.00")
        self.assertEqual(data["total_service"], "370.00")
        self.assertEqual(data["invoice_total"], "370.00")

        html = self.handler.fields_html()
        self.assertNotIn("<h2>Catalogo Urne</h2>", html)
        self.assertIn('name="urn_id" class="hidden"', html)
        self.assertIn('name="urn_id_2" class="hidden"', html)
        self.assertIn('name="invoice_total"', html)
        self.assertIn("Urna prova", html)

    def test_payment_movements_use_real_dates_and_separate_channels(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            created="2026-07-10T09:00:00"; paid="2026-07-15T11:30:00"
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,price_cremation,total_service,total_text,deposit,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-RAFFAELE","Privato","Livorno","Ritirato",created,created,admin["id"],"Raffaele","410","410","330","100","Acconto")).lastrowid
            self.handler.add_payment_movement(conn,pid,"acconto_d","D",100,admin["id"],"Acconto",created)
            self.handler.add_payment_movement(conn,pid,"saldo_d","D",230,admin["id"],"Saldo",paid)
            paid_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                     created_by,animal_name,price_cremation,total_service,total_text,deposit,payment_status)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  ("PP-DATA-SALDO","Privato","Livorno","Ritirato",created,created,admin["id"],"Whisky","410","410","330","0","Pagato")).lastrowid
            self.handler.add_payment_movement(conn,paid_pid,"saldo_d","D",330,admin["id"],"Pagamento completo",paid)
            totals={row["day"]:row["amount"] for row in conn.execute("SELECT date(paid_at) day,sum(amount) amount FROM payment_movements WHERE practice_id=? GROUP BY date(paid_at)",(pid,))}
        self.assertEqual(totals,{"2026-07-10":100.0,"2026-07-15":230.0})

        rendered=[]; self.handler.send_html=lambda content: rendered.append(content)
        self.handler.path="/bilanci?dal=2026-07-15&al=2026-07-15&voce=totale_d"
        self.handler.balances_v2(admin)
        self.assertIn("PP-RAFFAELE",rendered[-1])
        self.assertIn("PP-DATA-SALDO",rendered[-1])
        self.assertIn("230,00",rendered[-1])
        self.assertNotIn("100,00</b>",rendered[-1])
        self.handler.path="/bilanci?dal=2026-07-10&al=2026-07-10&voce=totale_d"
        self.handler.balances_v2(admin)
        self.assertIn("PP-RAFFAELE",rendered[-1])
        self.assertNotIn("PP-DATA-SALDO",rendered[-1])
        self.assertIn("100,00",rendered[-1])
        self.assertNotIn("230,00</b>",rendered[-1])

    def test_balance_components_show_entered_values_without_proration(self):
        today=app.datetime.now().date().isoformat(); stamp=f"{today}T10:00:00"
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            first=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                 created_by,price_cremation,price_night,total_service,deposit,payment_status)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                               ("CR-000018","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"380","120","420","120","Acconto")).lastrowid
            second=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                  created_by,price_cremation,total_service,deposit,payment_status)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                ("CR-000019","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"360","420","90","Acconto")).lastrowid
            self.handler.add_payment_movement(conn,first,"acconto_ordinario","ordinario",40,admin["id"],"Prima parte",stamp)
            self.handler.add_payment_movement(conn,first,"acconto_ordinario","ordinario",80,admin["id"],"Seconda parte",stamp)
            self.handler.add_payment_movement(conn,second,"acconto_ordinario","ordinario",90,admin["id"],"Acconto",stamp)

        rendered=[]; self.handler.send_html=lambda content: rendered.append(content)
        self.handler.path=f"/bilanci?dal={today}&al={today}&voce=price_night"
        self.handler.balances_v2(admin)
        self.assertIn("CR-000018",rendered[-1])
        self.assertIn(app.money_it(120),rendered[-1])
        self.assertNotIn(app.money_it(34.29),rendered[-1])
        self.assertEqual(rendered[-1].count("CR-000018"),1)

        self.handler.path=f"/bilanci?dal={today}&al={today}&voce=price_cremation"
        self.handler.balances_v2(admin)
        self.assertIn(app.money_it(740),rendered[-1])
        self.assertIn(app.money_it(380),rendered[-1])
        self.assertIn(app.money_it(360),rendered[-1])
        self.assertNotIn(app.money_it(108.62),rendered[-1])
        self.assertNotIn(app.money_it(77.14),rendered[-1])

        self.handler.path=f"/bilanci?dal={today}&al={today}&voce=totale_calcolato"
        self.handler.balances_v2(admin)
        self.assertIn("Entrate W",rendered[-1])
        self.assertIn("Acconto W",rendered[-1])
        self.assertIn("Totale W",rendered[-1])

    def test_payment_reconciliation_caps_due_and_removes_paid_income(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,price_cremation,total_service,total_text,deposit,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-MOVIMENTI","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"410","410","330","100","Acconto")).lastrowid
            partial=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.handler.reconcile_payment_movements(conn,pid,None,partial,admin["id"],"Acconto iniziale")
            conn.execute("UPDATE practices SET deposit='999',payment_status='Pagato' WHERE id=?",(pid,))
            paid=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.handler.reconcile_payment_movements(conn,pid,partial,paid,admin["id"],"Saldo")
            self.assertAlmostEqual(conn.execute("SELECT sum(amount) n FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()["n"],330)
            conn.execute("UPDATE practices SET deposit='100',payment_status='Acconto' WHERE id=?",(pid,))
            reopened=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.handler.reconcile_payment_movements(conn,pid,paid,reopened,admin["id"],"Riapertura")
            self.assertAlmostEqual(conn.execute("SELECT sum(amount) n FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()["n"],100)
            self.assertGreater(conn.execute("SELECT count(*) n FROM payment_movements WHERE practice_id=? AND amount<0",(pid,)).fetchone()["n"],0)

    def test_practice_summary_opens_without_mutating_payments(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,species,breed,age_years,age_months,service_type,urn_notes,price_urn,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-APERTURA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cane","Meticcio","7","3","Cremazione singola","Urna doppia","85","Da saldare")).lastrowid
        rendered=[]; self.handler.send_html=lambda content,*args: rendered.append(content)
        self.handler.practice(admin,pid)
        self.assertIn("PP-APERTURA",rendered[-1])
        self.assertIn(f'action="/pratiche/{pid}/fattura"',rendered[-1])
        self.assertIn("FARE FATTURA",rendered[-1])
        self.assertIn('name="invoice_total"',rendered[-1])
        self.assertIn("Età: 7 anni, 3 mesi",rendered[-1])
        self.assertIn("Urna doppia",rendered[-1])
        self.assertNotIn("Firma su telefono",rendered[-1])
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()["n"],0)

    def test_archive_tables_show_age_invoice_and_collapsible_months(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            for number,date,age,invoice in (("CR-000101","2026-07-10","8","FT-101"),("CR-000102","2026-06-10","3","")):
                stamp=f"{date}T10:00:00"
                conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,age_years,invoice_number,invoice_total,pickup_date)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (number,"Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna",age,invoice,"240.00",date))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/archivio/pratiche?stato=Ritirato"
        self.handler.archive(admin)
        page=rendered[-1]
        self.assertIn("Età",page);self.assertIn("Fattura",page);self.assertIn("FT-101",page)
        self.assertIn("8 anni",page);self.assertEqual(page.count('class="month-toggle"'),2)
        self.assertIn("toggleArchiveMonth",page);self.assertNotIn("Aggiorna pagamento",page);self.assertNotIn('class="quick-payment"',page)

    def test_origin_veterinarian_lookup_and_safe_return_link(self):
        html=self.handler.fields_html()
        self.assertIn('id="originVetSearch"',html)
        self.assertIn('id="originVetResults"',html)
        self.assertIn('name="origin_veterinarian_id"',html)
        self.assertIn("setupOriginVetLookup",app.APP_JS)
        self.assertIn("/api/veterinari/search",app.APP_JS)
        self.assertEqual(app.safe_return_path("https://example.test/evil","/"),"/")
        self.assertEqual(app.safe_return_path("/archivio/pratiche?stato=Ritirato","/"),"/archivio/pratiche?stato=Ritirato")
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by)
                                VALUES(?,?,?,?,?,?,?)""",("CR-RETURN","Privato","Livorno","Ritirato",stamp,stamp,admin["id"])).lastrowid
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path=f"/pratiche/{pid}?return_to=%2Fdashboard%3Fstato%3DRitirato"
        self.handler.practice(admin,pid)
        self.assertIn('href="/dashboard?stato=Ritirato"',rendered[-1])
        self.assertIn("Torna alla pagina precedente",rendered[-1])

    def test_practice_list_order_sticky_urn_and_inline_statuses(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            urn_id=conn.execute("INSERT INTO urns(name,price,quantity,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",("Doppia Quercia","95.00",2,1,stamp,stamp)).lastrowid
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,estimated_weight,age_years,owner_first_name,owner_last_name,service_type,urn_id,payment_status,total_service)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-LISTA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cane","12","8","Mario","Rossi","Cremazione singola",urn_id,"Da saldare","230"))
            rows=conn.execute("SELECT * FROM practices WHERE practice_number='CR-LISTA'").fetchall()
        self.handler.path="/dashboard?stato=Ritirato"
        page=self.handler.practice_rows(rows)
        self.assertLess(page.index("Luna"),page.index("8 anni"))
        self.assertLess(page.index("8 anni"),page.index("Mario Rossi"))
        self.assertLess(page.index("Mario Rossi"),page.index(">CR-LISTA</b>"))
        self.assertIn("Doppia Quercia",page)
        self.assertIn("stato-rapido",page)
        self.assertIn("pagamento-rapido",page)
        self.assertIn("Totale incassato",page)
        self.assertIn("Numero fattura",page)
        self.assertIn("practice-list-table td:first-child",app.CSS)

    def test_scheduled_whatsapp_appears_in_conversations(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,owner_last_name,owner_phone,animal_name)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",("CR-WA","Privato","Livorno","Consegnato",stamp,stamp,admin["id"],"Mario","Rossi","393331234567","Luna")).lastrowid
            conn.execute("""INSERT INTO whatsapp_messages(practice_id,scheduled_at,status,template_name,recipient_phone,manual,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?)""",(pid,"2026-07-15T10:00:00","programmato","grazie_cliente","393331234567",0,stamp,stamp))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/conversazioni-whatsapp"
        self.handler.whatsapp_conversations(admin)
        self.assertIn("Programmato per",rendered[-1])
        self.assertIn("CR-WA",rendered[-1])
        self.assertIn("message-programmato",rendered[-1])

    def test_quick_payment_saves_details_and_returns_to_list(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",("CR-PAY","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","200")).lastrowid
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"200,00","invoice_number":"FT-200","invoice_total":"200,00","invoice_date":"2026-07-14","return_to":"/archivio/pratiche?stato=Ritirato"}
        redirects=[];self.handler.redirect=lambda path:redirects.append(path);self.handler.headers={}
        self.handler.quick_payment(admin,pid)
        with app.db() as conn:
            row=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((row["payment_status"],row["payment_method"],row["payment_amount"]),("Pagato","Pos","200.00"))
            self.assertEqual((row["invoice_number"],row["invoice_total"],row["invoice_date"]),("FT-200","200.00","2026-07-14"))
        self.assertEqual(redirects[-1],"/archivio/pratiche?stato=Ritirato")

    def test_dashboard_balances_and_payment_pages_render(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered=[]; self.handler.send_html=lambda content,*args: rendered.append(content)
        self.handler.dashboard(admin)
        self.assertIn("Entrate settimana in corso",rendered[-1])
        self.assertIn("Totale W",rendered[-1])
        self.assertNotIn("Totale calcolato",rendered[-1])
        self.handler.path="/bilanci"
        self.handler.balances_v2(admin)
        self.assertIn("Data economica",rendered[-1])
        self.assertIn("Entrate W",rendered[-1])
        self.assertIn("Da entrare W",rendered[-1])
        self.handler.payment_overview(admin,"da-saldare")
        self.assertIn("Da saldare D",rendered[-1])
        self.assertIn("Totale W e Totale D",rendered[-1])


if __name__ == "__main__":
    unittest.main()
