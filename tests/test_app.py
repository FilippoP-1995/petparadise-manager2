import os
import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import app
import email_service
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

    def test_practice_autosave_debounce_success_conflict_and_no_side_effects(self):
        stamp="2026-07-15T10:00:00"
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
              animal_name,owner_first_name,owner_last_name,owner_phone,tag_da_richiamare,total_service,total_text,deposit,remaining_balance,payment_status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-AUTO","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Fido","Mario","Rossi","333111","Si","250","330","100","230","Acconto")).lastrowid
            notifications_before=conn.execute("SELECT count(*) n FROM notifications").fetchone()["n"]
            whatsapp_before=conn.execute("SELECT count(*) n FROM whatsapp_messages").fetchone()["n"]
        rendered=[];self.handler.path=f"/pratiche/{pid}/modifica";self.handler.send_html=lambda html,*args:rendered.append(html)
        self.handler.edit_page(admin,pid)
        page=rendered[-1]
        self.assertIn(f'data-autosave-url="/api/pratiche/{pid}/autosave"',page)
        self.assertIn("Ultimo salvataggio",page)
        self.assertIn("setTimeout(save,1800)",app.APP_JS)
        captured=[];self.handler.send_json=lambda obj,status=200:captured.append((obj,status))
        self.handler.form=lambda:{"updated_at":stamp,"changes_json":json.dumps({"animal_name":"Fido Junior","owner_phone":"333222"})}
        self.handler.practice_autosave(admin,pid)
        self.assertEqual(captured[-1][1],200)
        new_version=captured[-1][0]["updated_at"]
        with app.db() as conn:
            row=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((row["animal_name"],row["owner_phone"]),("Fido Junior","333222"))
            self.assertEqual((row["total_service"],row["total_text"],row["deposit"],row["remaining_balance"]),("250","330","100","230"))
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications").fetchone()["n"],notifications_before)
            self.assertEqual(conn.execute("SELECT count(*) n FROM whatsapp_messages").fetchone()["n"],whatsapp_before)
            self.assertEqual(conn.execute("SELECT count(*) n FROM practice_history WHERE practice_id=? AND event_type='Salvataggio automatico'",(pid,)).fetchone()["n"],1)
        self.handler.form=lambda:{"updated_at":stamp,"changes_json":json.dumps({"animal_name":"Versione vecchia"})}
        self.handler.practice_autosave(admin,pid)
        self.assertEqual(captured[-1][1],409)
        self.assertTrue(captured[-1][0]["conflict"])
        with app.db() as conn:self.assertEqual(conn.execute("SELECT animal_name FROM practices WHERE id=?",(pid,)).fetchone()["animal_name"],"Fido Junior")
        self.assertNotEqual(new_version,stamp)

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

    def test_smtp_service_uses_tls_authentication_and_company_sender(self):
        calls={}
        class FakeSMTP:
            def __init__(self,host,port,timeout):calls.update(host=host,port=port,timeout=timeout)
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def ehlo(self):calls["ehlo"]=calls.get("ehlo",0)+1
            def starttls(self,context):calls["tls"]=True
            def login(self,username,password):calls.update(username=username,password=password)
            def send_message(self,message):calls["message"]=message
        env={"SMTP_HOST":"smtp.titan.email","SMTP_PORT":"587","SMTP_USERNAME":"info@petparadisempoli.com","SMTP_PASSWORD":"secret-test","SMTP_USE_TLS":"true","EMAIL_FROM_NAME":"Pet Paradise","EMAIL_FROM_ADDRESS":"info@petparadisempoli.com"}
        with patch("email_service.smtplib.SMTP",FakeSMTP):
            email_service.send_email("supplier@example.com","Ordine test","Testo",env)
        self.assertEqual((calls["host"],calls["port"],calls["username"]),("smtp.titan.email",587,"info@petparadisempoli.com"))
        self.assertTrue(calls["tls"]);self.assertEqual(calls["ehlo"],2)
        self.assertEqual(calls["message"]["From"],"Pet Paradise <info@petparadisempoli.com>")
        self.assertEqual(calls["message"]["To"],"supplier@example.com")

    def test_smtp_configuration_and_authentication_errors_are_safe(self):
        with self.assertRaises(email_service.EmailConfigurationError) as missing:
            email_service.smtp_config({})
        self.assertIn("SMTP_PASSWORD",str(missing.exception));self.assertNotIn("secret",str(missing.exception))
        env={"SMTP_HOST":"smtp.titan.email","SMTP_PORT":"587","SMTP_USERNAME":"info@petparadisempoli.com","SMTP_PASSWORD":"wrong","SMTP_USE_TLS":"true","EMAIL_FROM_NAME":"Pet Paradise","EMAIL_FROM_ADDRESS":"info@petparadisempoli.com"}
        class BadSMTP:
            def __init__(self,*args,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def ehlo(self):pass
            def starttls(self,context):pass
            def login(self,*args):raise email_service.smtplib.SMTPAuthenticationError(535,b"Authentication failed")
        with patch("email_service.smtplib.SMTP",BadSMTP),self.assertRaises(email_service.EmailDeliveryError) as failed:
            email_service.send_email("supplier@example.com","Test","Test",env)
        self.assertIn("Autenticazione SMTP non riuscita",str(failed.exception));self.assertNotIn("wrong",str(failed.exception))

    def test_smtp_port_465_uses_ssl_and_debug_logs_are_safe(self):
        calls={}
        class FakeSMTPSSL:
            def __init__(self,host,port,timeout,context):calls.update(host=host,port=port,timeout=timeout,context=context)
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def ehlo(self):calls["ehlo"]=calls.get("ehlo",0)+1
            def starttls(self,context):raise AssertionError("STARTTLS non deve essere usato sulla porta 465")
            def login(self,username,password):calls.update(username=username,password=password)
            def send_message(self,message):calls["message"]=message
        env={"SMTP_HOST":" \u200bsmtp.titan.email\t","SMTP_PORT":"465","SMTP_USERNAME":"info@petparadisempoli.com","SMTP_PASSWORD":"secret-test","SMTP_USE_TLS":"false","EMAIL_FROM_NAME":"Pet Paradise","EMAIL_FROM_ADDRESS":"info@petparadisempoli.com"}
        logs=io.StringIO()
        with patch("email_service.smtplib.SMTP_SSL",FakeSMTPSSL),patch("email_service.smtplib.SMTP",side_effect=AssertionError("SMTP semplice non atteso")),redirect_stderr(logs):
            email_service.send_email("supplier@example.com","Test SSL","Corpo",env)
        output=logs.getvalue()
        self.assertEqual((calls["host"],calls["port"]),("smtp.titan.email",465));self.assertEqual(calls["ehlo"],1)
        self.assertIn("smtplib.SMTP_SSL",output);self.assertIn("SSL=True STARTTLS=False",output);self.assertIn("SMTP_PASSWORD presente=True",output)
        self.assertNotIn("secret-test",output)

    def test_smtp_dns_failure_logs_raw_host_and_full_traceback(self):
        raw_host="  smtp.non-risolvibile.invalid  "
        env={"SMTP_HOST":raw_host,"SMTP_PORT":"587","SMTP_USERNAME":"info@petparadisempoli.com","SMTP_PASSWORD":"secret-test","SMTP_USE_TLS":"false","EMAIL_FROM_NAME":"Pet Paradise","EMAIL_FROM_ADDRESS":"info@petparadisempoli.com"}
        logs=io.StringIO()
        with patch("email_service.smtplib.SMTP",side_effect=socket.gaierror(-2,"Name or service not known")),redirect_stderr(logs),self.assertRaises(email_service.EmailDeliveryError) as failed:
            email_service.send_email("supplier@example.com","Test DNS","Corpo",env)
        output=logs.getvalue()
        self.assertIn("tipo_connessione=smtplib.SMTP + STARTTLS",output);self.assertIn("SSL=False STARTTLS=True",output)
        self.assertIn(f"SMTP_HOST esatto letto dall'ambiente={raw_host!r}",output);self.assertIn("Traceback (most recent call last)",output);self.assertIn("socket.gaierror",output)
        self.assertIn("gaierror",str(failed.exception));self.assertNotIn("secret-test",output)

    def test_order_schema_default_recipient_and_email_validation(self):
        with app.db() as conn:
            columns={row["name"] for row in conn.execute("PRAGMA table_info(email_orders)")}
            recipient=conn.execute("SELECT value FROM settings WHERE key='order_recipient_email'").fetchone()["value"]
            settings=app.order_email_settings(conn)
        self.assertTrue({"quantity","recipient","subject","body","status","error_message","operator_id","parent_order_id","archived_at","sent_at"}.issubset(columns))
        self.assertEqual(recipient,"[QUI INSERIRÒ IL MIO INDIRIZZO EMAIL]")
        self.assertEqual(app.render_order_email(5,settings),("Ordine boccioni acqua - Pet Paradise","Buongiorno,\n\ndesideriamo ordinare 5 boccioni di acqua.\n\nVi chiediamo gentilmente di confermare disponibilità e consegna.\n\nGrazie.\n\nPet Paradise"))
        self.assertTrue(app.valid_email_address("fornitore@example.com"))
        for invalid in ("","non-valida","a@localhost","a@example.com\nBcc:x@y.it"):
            self.assertFalse(app.valid_email_address(invalid))

    def test_water_order_one_and_five_are_sent_and_saved(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            conn.execute("UPDATE settings SET value='supplier@example.com' WHERE key='order_recipient_email'")
        sent=[];statuses_during=[];redirects=[];self.handler.redirect=lambda path:redirects.append(path)
        def capture_send(recipient,subject,body,**kwargs):
            sent.append((recipient,subject,body))
            with app.db() as conn:statuses_during.append(conn.execute("SELECT status FROM email_orders ORDER BY id DESC LIMIT 1").fetchone()["status"])
        with patch("app.send_email",side_effect=capture_send):
            for quantity in (1,5):
                self.handler.form=lambda q=quantity:{"confirm_send":"SI","quantity":str(q)}
                self.handler.send_water_order(admin)
        self.assertEqual(len(sent),2);self.assertIn("1 boccioni di acqua",sent[0][2]);self.assertIn("5 boccioni di acqua",sent[1][2])
        self.assertIn("confermare disponibilità e consegna",sent[1][2]);self.assertTrue(all(item[0]=="supplier@example.com" for item in sent))
        with app.db() as conn:
            rows=conn.execute("SELECT * FROM email_orders ORDER BY id").fetchall()
        self.assertEqual([row["status"] for row in rows],["Inviato","Inviato"])
        self.assertEqual(statuses_during,["Invio in corso","Invio in corso"])
        self.assertTrue(all(row["sent_at"] and row["operator_id"]==admin["id"] for row in rows))
        self.assertEqual(redirects,["/ordini?esito=inviato&ordine=1","/ordini?esito=inviato&ordine=2"])

    def test_order_blocks_zero_negative_missing_confirmation_and_invalid_recipient(self):
        with app.db() as conn:admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        errors=[];self.handler.error_page=lambda title,message,back="/":errors.append((title,message,back))
        for form in ({"confirm_send":"SI","quantity":"0"},{"confirm_send":"SI","quantity":"-2"},{"quantity":"5"}):
            self.handler.form=lambda value=form:value;self.handler.send_water_order(admin)
        with app.db() as conn:self.assertEqual(conn.execute("SELECT count(*) n FROM email_orders").fetchone()["n"],0)
        self.assertEqual(len(errors),3)
        self.handler.form=lambda:{"confirm_send":"SI","quantity":"5"};self.handler.send_water_order(admin)
        self.assertIn("destinatario",errors[-1][1].lower())
        with app.db() as conn:self.assertEqual(conn.execute("SELECT count(*) n FROM email_orders").fetchone()["n"],0)

    def test_missing_smtp_and_wrong_password_are_recorded_as_failed(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();conn.execute("UPDATE settings SET value='supplier@example.com' WHERE key='order_recipient_email'")
        with patch.dict(os.environ,{},clear=True):
            order_id,error=self.handler._create_and_send_order(admin,5,"")
        self.assertTrue(order_id);self.assertIn("Configurazione email incompleta",error)
        with patch("app.send_email",side_effect=email_service.EmailDeliveryError("Autenticazione SMTP non riuscita. Verifica utente e password su Render.")):
            second_id,error=self.handler._create_and_send_order(admin,1,"Nota")
        with app.db() as conn:
            first=conn.execute("SELECT * FROM email_orders WHERE id=?",(order_id,)).fetchone();second=conn.execute("SELECT * FROM email_orders WHERE id=?",(second_id,)).fetchone()
        self.assertEqual((first["status"],second["status"]),("Fallito","Fallito"));self.assertIn("SMTP_PASSWORD",first["error_message"]);self.assertNotIn("wrong",second["error_message"])

    def test_order_resend_duplicate_filters_detail_and_soft_archive(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();conn.execute("UPDATE settings SET value='supplier@example.com' WHERE key='order_recipient_email'")
        with patch("app.send_email",side_effect=email_service.EmailDeliveryError("Errore SMTP di prova")):
            original,_=self.handler._create_and_send_order(admin,5,"")
            self.handler.form=lambda:{"confirm_send":"SI"};redirects=[];self.handler.redirect=lambda path:redirects.append(path)
        with patch("app.send_email"):
            self.handler.order_action(admin,original,"reinvia")
        with app.db() as conn:
            resent=conn.execute("SELECT * FROM email_orders WHERE parent_order_id=? AND status='Inviato' ORDER BY id DESC",(original,)).fetchone()
        self.assertIsNotNone(resent);self.assertEqual((resent["quantity"],resent["notes"]),(5,""))
        self.handler.order_action(admin,original,"duplica")
        with app.db() as conn:
            draft=conn.execute("SELECT * FROM email_orders WHERE parent_order_id=? AND status='Bozza'",(original,)).fetchone()
            conn.execute("""INSERT INTO email_orders(order_type,quantity,recipient,subject,body,status,operator_id,created_at,updated_at)
                            VALUES('water',2,'old-supplier@example.com','Ordine storico','CORPO-STORICO','Inviato',?,'2020-01-01T10:00:00','2020-01-01T10:00:00')""",(admin["id"],))
        self.assertIsNotNone(draft);self.assertIn(f"bozza={draft['id']}",redirects[-1])
        today=datetime.now().date().isoformat();rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path=f"/ordini/storico?dal={today}&al={today}&stato=Inviato"
        self.handler.orders_history_page(admin);self.assertIn("Storico ordini",rendered[-1]);self.assertNotIn("old-supplier@example.com",rendered[-1])
        self.handler.order_detail_page(admin,original);self.assertIn("Reinvia ordine",rendered[-1]);self.assertIn("Duplica ordine",rendered[-1])
        self.handler.order_action(admin,original,"archivia")
        with app.db() as conn:archived=conn.execute("SELECT archived_at FROM email_orders WHERE id=?",(original,)).fetchone()["archived_at"]
        self.assertTrue(archived)

    def test_order_settings_validate_and_save_recipient(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();conn.execute("INSERT INTO users(username,password_hash,display_name,role) VALUES('operatore','x','Operatore','operator')");operator=conn.execute("SELECT * FROM users WHERE username='operatore'").fetchone()
        errors=[];self.handler.error_page=lambda title,message,back="/":errors.append(message);self.handler.redirect=lambda path:None
        self.handler.form=lambda:{"order_recipient_email":"non valida"};self.handler.save_order_settings(admin)
        self.assertTrue(errors)
        values={"order_recipient_email":"Supplier@Example.com","order_email_subject":"Ordine personalizzato","order_email_template":"Servono {{quantita}} boccioni. {{note_predefinite}}","order_email_signature":"Firma Azienda","order_sender_name":"Ufficio ordini","order_phone":"0571 000000","order_default_notes":"Consegna mattina"}
        self.handler.form=lambda:values;self.handler.save_order_settings(admin)
        with app.db() as conn:
            saved=app.order_email_settings(conn);subject,body=app.render_order_email(5,saved)
        self.assertEqual(saved["order_recipient_email"],"supplier@example.com");self.assertEqual(subject,"Ordine personalizzato");self.assertIn("Servono 5 boccioni",body);self.assertIn("Consegna mattina",body);self.assertIn("Firma Azienda",body)
        forbidden=[];self.handler.send_error=lambda *args:forbidden.append(args);self.handler.save_order_settings(operator);self.assertEqual(forbidden[0][0],403)
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.order_settings_page(admin)
        for field in ("order_recipient_email","order_email_subject","order_email_template","order_email_signature","order_sender_name","order_phone","order_default_notes"):self.assertIn(field,rendered[-1])
        self.assertNotIn("SMTP_PASSWORD",rendered[-1])

    def test_orders_desktop_mobile_and_confirmation_markup(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();conn.execute("UPDATE settings SET value='supplier@example.com' WHERE key='order_recipient_email'")
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/ordini"
        self.handler.orders_page(admin);page=rendered[-1]
        for text in ("Ordina boccioni d’acqua","Seleziona la quantità","Ordina adesso","3 boccioni","5 boccioni","10 boccioni","Modifica impostazioni","Vedi tutti gli ordini","Ultimi ordini"):
            self.assertIn(text,page)
        self.assertNotIn('name="order_recipient_email"',page);self.assertNotIn('name="order_email_subject"',page);self.assertNotIn('<textarea',page)
        self.assertIn("openOrderConfirmation(this,event)",page);self.assertIn("Conferma e invia",page);self.assertIn("closeOrderConfirmation()",page)
        for token in (".water-order-card",".quantity-stepper","@media(max-width:620px)","var(--safe-bottom)","min-height:44px"):
            self.assertIn(token,app.CSS)
        self.assertIn('href="/ordini/storico"',page)

    def test_order_operator_can_send_and_view_but_cannot_open_settings(self):
        with app.db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,display_name,role) VALUES('operatore','x','Operatore','operator')");operator=conn.execute("SELECT * FROM users WHERE username='operatore'").fetchone();conn.execute("UPDATE settings SET value='supplier@example.com' WHERE key='order_recipient_email'")
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/ordini";self.handler.orders_page(operator)
        self.assertIn("Ordina adesso",rendered[-1]);self.assertNotIn("Modifica impostazioni",rendered[-1])
        forbidden=[];self.handler.send_error=lambda *args:forbidden.append(args);self.handler.order_settings_page(operator);self.assertEqual(forbidden[0][0],403)

    def test_order_main_shows_only_latest_five_and_failure_keeps_quantity(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();conn.execute("UPDATE settings SET value='supplier@example.com' WHERE key='order_recipient_email'")
            for quantity in range(1,7):conn.execute("""INSERT INTO email_orders(order_type,quantity,recipient,subject,body,status,operator_id,created_at,updated_at) VALUES('water',?,'supplier@example.com','S','B','Inviato',?,?,?)""",(quantity,admin["id"],f"2026-07-1{quantity}T10:00:00",f"2026-07-1{quantity}T10:00:00"))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/ordini?quantita=5";self.handler.orders_page(admin)
        self.assertIn('value="5"',rendered[-1]);self.assertIn('>6</td>',rendered[-1]);self.assertNotIn('>1</td>',rendered[-1])

    def test_form_extensions_and_normalization(self):
        html = self.handler.fields_html()
        for expected in ("GIANLUCA", "CALCO PER URNA", "CALCO POLPASTRELLO", "CALCO NASO", "price_paw_cast", "price_nose_cast", "Fiat Fiorino", "Renault Captur", "Dr PK8", "Cremato", "Smaltito"):
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
            "Sacchetti riconsegna", "Sacchetti ceneri", "Cerniere e viti urne",
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

    def test_urn_category_column_is_idempotent_and_seed_defaults_to_urna(self):
        with app.db() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(urns)")}
            self.assertIn("category", columns)
            categories = {row["category"] for row in conn.execute("SELECT category FROM urns WHERE active=1")}
        self.assertEqual(categories, {"Urna"})
        app.init_db()
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM urns WHERE active=1 AND category='Urna'").fetchone()["n"], 85)

    def test_urn_catalog_tabs_filter_by_category_and_use_prefixed_codes(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

        self.handler.form = lambda: {"category": "Accessorio", "name": "Collana prova", "material": "Metallo", "price": "12.00", "quantity": "3", "low_stock_threshold": "1"}
        self.handler.redirect = lambda path: setattr(self, "redirected", path)
        self.handler.save_urn(admin)
        with app.db() as conn:
            accessory = conn.execute("SELECT * FROM urns WHERE name='Collana prova'").fetchone()
        self.assertEqual(accessory["category"], "Accessorio")
        self.assertTrue(accessory["internal_code"].startswith("ACC-"))

        self.handler.form = lambda: {"category": "Calco", "name": "Calco naso prova", "material": "", "price": "20.00", "quantity": "1", "low_stock_threshold": "1"}
        self.handler.save_urn(admin)
        with app.db() as conn:
            cast = conn.execute("SELECT * FROM urns WHERE name='Calco naso prova'").fetchone()
        self.assertEqual(cast["category"], "Calco")
        self.assertTrue(cast["internal_code"].startswith("CALCO-"))

        rendered = []
        self.handler.send_html = lambda html, *args: rendered.append(html)
        self.handler.path = "/catalogo-urne?categoria=accessori"
        self.handler.urn_catalog_page(admin)
        page = rendered[-1]
        self.assertIn("Collana prova", page)
        self.assertNotIn("Calco naso prova", page)
        self.assertIn('class="active">Accessori</a>', page)

        self.handler.path = "/catalogo-urne?categoria=calchi"
        self.handler.urn_catalog_page(admin)
        page = rendered[-1]
        self.assertIn("Calco naso prova", page)
        self.assertNotIn("Collana prova", page)

        self.handler.path = "/catalogo-urne"
        self.handler.urn_catalog_page(admin)
        page = rendered[-1]
        self.assertNotIn("Collana prova", page)
        self.assertNotIn("Calco naso prova", page)

    def test_urn_edit_page_has_quantity_stepper_buttons(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda html, *args: rendered.append(html)
        self.handler.path = "/catalogo-urne/nuova?categoria=accessori"
        self.handler.urn_edit_page(admin)
        page = rendered[-1]
        self.assertIn('onclick="adjustUrnQuantity(this.form,-1)"', page)
        self.assertIn('onclick="adjustUrnQuantity(this.form,1)"', page)
        self.assertIn('<option value="Accessorio" selected>Accessorio</option>', page)
        self.assertIn("function adjustUrnQuantity(form,delta)", app.APP_JS)

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
                                created_by,animal_name,species,breed,age_years,age_months,service_type,urn_notes,price_urn,price_pickup,
                                price_night,send_catalog,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-APERTURA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cane","Meticcio","7","3","Cremazione singola","Urna doppia","85","40","","Si","Da saldare")).lastrowid
        rendered=[]; self.handler.send_html=lambda content,*args: rendered.append(content)
        self.handler.practice(admin,pid)
        self.assertIn("PP-APERTURA",rendered[-1])
        self.assertIn(f'action="/pratiche/{pid}/fattura"',rendered[-1])
        self.assertIn("FARE FATTURA",rendered[-1])
        self.assertIn('name="invoice_total"',rendered[-1])
        self.assertIn("Età: 7 anni, 3 mesi",rendered[-1])
        self.assertIn("Urna doppia",rendered[-1])
        self.assertIn("Dati economici",rendered[-1])
        self.assertIn("Totale pagato",rendered[-1])
        self.assertIn("Da pagare",rendered[-1])
        self.assertIn("Rimanenza W",rendered[-1])
        self.assertIn("Voci del preventivo",rendered[-1])
        self.assertIn("Ritiro",rendered[-1])
        self.assertIn("INVIARE CATALOGO",rendered[-1])
        self.assertIn('name="send_catalog" value="Si" checked',rendered[-1])
        self.assertIn('name="catalog_sent"',rendered[-1])
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

    def test_provenance_mapping_manual_selection_and_automatic_veterinarian(self):
        expected={
            "V":["VARIGNANO","CAMPO D'AVIAZIONE","GLI AMICI DI BLU"],
            "E":["Lucy","Frediani","Matteini","La Fenice","Croce Azzurra","Bellucci","Bartoli","Gennari","Giulia Frati","Sanminianimal","Parlanti","Dante delle Rose"],
            "F":["Il Poggetto","Ariosto"],"P":["Barbaricina"],"L":["Qualsiasi altro veterinario"],
        }
        for code,names in expected.items():
            for name in names:self.assertEqual(app.veterinarian_provenance(name),code,name)
        html=self.handler.fields_html()
        self.assertIn('name="provenance"',html);self.assertIn('V · Viareggio',html);self.assertIn('P · Pisa',html)
        with app.db() as conn:
            stamp=app.now();vet_id=conn.execute("INSERT INTO veterinarians(short_name,clinic_name,active,created_at,updated_at) VALUES(?,?,?,?,?)",("Barbaricina","Clinica Barbaricina",1,stamp,stamp)).lastrowid
        automatic=self.handler.normalized_fields({"veterinarian_id":str(vet_id)})
        automatic_origin=self.handler.normalized_fields({"origin_mode":"Veterinario","origin_veterinarian_id":str(vet_id)})
        manual=self.handler.normalized_fields({"veterinarian_id":str(vet_id),"provenance":"F"})
        self.assertEqual(automatic["provenance"],"P");self.assertEqual(automatic_origin["provenance"],"P");self.assertEqual(manual["provenance"],"F")
        self.assertIn('data-provenance="P"',self.handler.fields_html())
        self.assertIn("setProvenanceFromVeterinarian",app.APP_JS)

    def test_catalog_flags_are_mutually_exclusive_from_form_and_summary(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,send_catalog)
                                VALUES(?,?,?,?,?,?,?,?)""",("CR-CATALOGO","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Si")).lastrowid
        self.handler.redirect=lambda path:None
        with patch("app.emit_notification",return_value=[]):
            self.handler.form=lambda:{"catalog_sent":"Si"};self.handler.catalog_sent(admin,pid)
        with app.db() as conn:
            row=conn.execute("SELECT send_catalog,catalog_sent FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((row["send_catalog"],row["catalog_sent"]),("","Si"))
        self.handler.form=lambda:{"send_catalog":"Si"};self.handler.catalog_sent(admin,pid)
        with app.db() as conn:
            row=conn.execute("SELECT send_catalog,catalog_sent FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((row["send_catalog"],row["catalog_sent"]),("Si",""))
        self.assertIn("e.target.name === 'catalog_sent'",app.APP_JS)
        self.assertIn("arrangeBudgetLayout",app.APP_JS)

    def test_advanced_search_forms_are_collapsed_behind_button(self):
        source='<form class="section" method="get"><input name="q"><select name="stato"></select></form>'
        collapsed=app.collapse_advanced_search(source)
        self.assertIn('<details class="advanced-search">',collapsed);self.assertIn('<summary>Ricerca avanzata</summary>',collapsed)
        self.assertNotIn(' open',collapsed);self.assertIn('advanced-search-form',collapsed)
        self.assertEqual(app.collapse_advanced_search('<form method="get"><input name="q"></form>'),'<form method="get"><input name="q"></form>')

    def test_practice_list_order_sticky_urn_and_inline_statuses(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            urn_id=conn.execute("INSERT INTO urns(name,price,quantity,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",("Doppia Quercia","95.00",2,1,stamp,stamp)).lastrowid
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,estimated_weight,age_years,owner_first_name,owner_last_name,service_type,urn_id,payment_status,total_service,provenance)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-LISTA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cane","12","8","Mario","Rossi","Cremazione singola",urn_id,"Da saldare","230","V"))
            rows=conn.execute("SELECT * FROM practices WHERE practice_number='CR-LISTA'").fetchall()
        self.handler.path="/dashboard?stato=Ritirato"
        page=self.handler.practice_rows(rows)
        self.assertLess(page.index("Luna"),page.index("8 anni"))
        self.assertLess(page.index("8 anni"),page.index("Mario Rossi"))
        self.assertLess(page.index("Mario Rossi"),page.index(">CR-LISTA</b>"))
        self.assertIn("Doppia Quercia",page)
        self.assertIn("<td><b>V</b></td>",page)
        rendered=app.layout("Test",'<table><thead><tr><th>Veterinario</th><th>Sede</th></tr></thead></table>')
        self.assertIn("<th>Veterinario</th><th>Provenienza</th><th>Sede</th>",rendered)
        self.assertIn("stato-rapido",page)
        self.assertIn("pagamento-rapido",page)
        self.assertIn("Totale incassato",page)
        self.assertIn("Numero fattura",page)
        self.assertIn("practice-list-table td:first-child",app.CSS)
        self.assertIn("width:132px;min-width:132px;max-width:132px",app.CSS)

    def test_cremated_status_colors_only_label_and_ritirato_is_yellow(self):
        self.assertIn("Cremato",app.STATES)
        self.assertEqual(app.practice_status_class("Ritirato"),"practice-status-yellow")
        self.assertEqual(app.practice_status_class("Cremato"),"practice-status-blue")
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,service_type,payment_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-CREMATO","Privato","Livorno","Cremato",stamp,stamp,admin["id"],"Luna","Cane","Cremazione singola","Da saldare"))
            rows=conn.execute("SELECT * FROM practices WHERE practice_number='CR-CREMATO'").fetchall()
        self.handler.path="/archivio/pratiche"
        page=self.handler.practice_rows(rows)
        self.assertIn('class="practice-row-link"',page)
        self.assertIn("practice-status-blue",page)
        self.assertNotIn("practice-row-cremated",page)
        self.assertNotIn("practice-row-cremated",app.CSS)

    def test_urn_word_search_and_frame_urn_enable_cast_tag(self):
        self.assertIn("urnMatchesWords",app.APP_JS)
        self.assertIn("words.every",app.APP_JS)
        self.assertIn("markCastForFrameUrn",app.APP_JS)
        with app.db() as conn:
            stamp=app.now()
            urn_id=conn.execute("INSERT INTO urns(name,price,quantity,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",("Doppia Cornice Bianca L","120",3,1,stamp,stamp)).lastrowid
        data=self.handler.normalized_fields({"urn_id":str(urn_id),"service_type":"Cremazione singola"})
        self.assertEqual(data["tag_calco_urna"],"Si")

    def test_quick_state_ajax_saves_without_redirect(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,service_type)
                                VALUES(?,?,?,?,?,?,?,?)""",("CR-AJAX","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Cremazione singola")).lastrowid
        self.handler.form=lambda:{"status":"Cremato","ajax":"1","return_to":"/archivio/pratiche?stato=Ritirato"}
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status));self.handler.redirect=lambda path:self.fail("Il salvataggio AJAX non deve reindirizzare")
        self.handler.quick_state(admin,pid)
        with app.db() as conn:self.assertEqual(conn.execute("SELECT status FROM practices WHERE id=?",(pid,)).fetchone()["status"],"Cremato")
        self.assertEqual(responses[-1][0]["status"],"Cremato")
        self.assertIn("savePracticeState",app.APP_JS)

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
        self.assertIn("Orario programmato",rendered[-1])
        self.assertIn("CR-WA",rendered[-1])
        self.assertIn("message-programmato",rendered[-1])

    def _whatsapp_record(self, scheduled_at, status="programmato", attempts=0, last_attempt_at=None, message_id=None):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            number=f"CR-WA-{conn.execute('SELECT count(*) n FROM practices').fetchone()['n']+1}"
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,owner_last_name,owner_phone,animal_name,service_type)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(number,"Privato","Livorno","Consegnato",stamp,stamp,admin["id"],"Mario","Rossi","3331234567","Luna","Cremazione singola")).lastrowid
            msg_id=conn.execute("""INSERT INTO whatsapp_messages(practice_id,scheduled_at,status,attempts,last_attempt_at,message_id,template_name,recipient_phone,manual,created_at,updated_at)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(pid,scheduled_at,status,attempts,last_attempt_at,message_id,"ringraziamento_livorno","393331234567",0,stamp,stamp)).lastrowid
        return admin,pid,msg_id

    def test_whatsapp_future_message_is_not_processed(self):
        _,_,msg_id=self._whatsapp_record("2026-07-15T15:01:00")
        with patch.object(self.handler,"send_whatsapp_message") as send:
            result=self.handler.process_whatsapp_queue(current_time=datetime(2026,7,15,15,0))
        self.assertEqual(result,[]);send.assert_not_called()
        with app.db() as conn:self.assertEqual(conn.execute("SELECT status FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()["status"],"programmato")

    def test_whatsapp_due_success_and_second_job_do_not_duplicate(self):
        _,_,msg_id=self._whatsapp_record("2026-07-15T14:00:00")
        class MetaResponse:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"messages":[{"id":"wamid.test"}]}'
        env={"WHATSAPP_ACCESS_TOKEN":"token-test","WHATSAPP_PHONE_NUMBER_ID":"phone-test"}
        with patch.dict(os.environ,env),patch("app.urllib.request.urlopen",return_value=MetaResponse()) as post:
            first=self.handler.process_whatsapp_queue(current_time=datetime(2026,7,15,14,0))
            second=self.handler.process_whatsapp_queue(current_time=datetime(2026,7,15,14,1))
        self.assertTrue(first[0]["ok"]);self.assertEqual(second,[]);self.assertEqual(post.call_count,1)
        with app.db() as conn:
            row=conn.execute("SELECT status,message_id,sent_at,last_attempt_at,attempts FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()
        self.assertEqual((row["status"],row["message_id"],row["attempts"]),("accettato_da_meta","wamid.test",1));self.assertTrue(row["sent_at"] and row["last_attempt_at"])

    def test_whatsapp_due_failure_is_recorded_as_failed(self):
        _,_,msg_id=self._whatsapp_record("2026-07-15T14:00:00")
        env={"WHATSAPP_ACCESS_TOKEN":"token-test","WHATSAPP_PHONE_NUMBER_ID":"phone-test"}
        with patch.dict(os.environ,env),patch("app.urllib.request.urlopen",side_effect=OSError("rete non disponibile")):
            result=self.handler.process_whatsapp_queue(current_time=datetime(2026,7,15,14,0))
        self.assertFalse(result[0]["ok"])
        with app.db() as conn:row=conn.execute("SELECT status,last_error,last_attempt_at,failed_at FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()
        self.assertEqual(row["status"],"fallito");self.assertIn("rete non disponibile",row["last_error"]);self.assertTrue(row["last_attempt_at"] and row["failed_at"])

    def test_whatsapp_stale_processing_lock_becomes_failed_without_resend(self):
        _,_,msg_id=self._whatsapp_record("2026-07-15T13:00:00","in_invio",1,"2026-07-15T13:40:00")
        with patch.object(self.handler,"send_whatsapp_message") as send:
            result=self.handler.process_whatsapp_queue(current_time=datetime(2026,7,15,14,0))
        send.assert_not_called();self.assertFalse(result[0]["ok"])
        with app.db() as conn:row=conn.execute("SELECT status,last_error FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()
        self.assertEqual(row["status"],"fallito");self.assertIn("evitare duplicazioni",row["last_error"])

    def test_whatsapp_timezone_is_explicitly_europe_rome(self):
        winter=app.whatsapp_datetime(datetime(2026,1,15,12,0));summer=app.whatsapp_datetime(datetime(2026,7,15,12,0))
        self.assertEqual(winter.tzinfo.key,"Europe/Rome");self.assertEqual(winter.utcoffset(),timedelta(hours=1));self.assertEqual(summer.utcoffset(),timedelta(hours=2))
        self.assertEqual(app.whatsapp_now(summer),"2026-07-15T12:00:00")

    def test_whatsapp_ui_shows_real_timestamps_error_and_contextual_actions(self):
        admin,_,failed_id=self._whatsapp_record("2026-07-15T13:00:00","fallito",1,"2026-07-15T13:01:00")
        with app.db() as conn:conn.execute("UPDATE whatsapp_messages SET last_error='Errore Meta',failed_at='2026-07-15T13:01:00' WHERE id=?",(failed_id,))
        self._whatsapp_record("2026-07-15T15:00:00")
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/conversazioni-whatsapp"
        self.handler.whatsapp_conversations(admin);page=rendered[-1]
        for text in ("Stato reale","Orario programmato","Ultimo tentativo","Data invio","Errore Meta","Riprova","Annulla"):
            self.assertIn(text,page)
        self.assertIn(f'/whatsapp-messaggi/{failed_id}/riprova',page)

    def test_whatsapp_failed_retry_and_scheduled_cancel(self):
        admin,_,failed_id=self._whatsapp_record("2026-07-15T13:00:00","fallito",1,"2026-07-15T13:01:00")
        _,_,scheduled_id=self._whatsapp_record("2026-07-15T15:00:00")
        self.handler.headers={"Referer":"/conversazioni-whatsapp"};self.handler.redirect=lambda path:None
        def accepted(conn,msg_id,**kwargs):
            conn.execute("UPDATE whatsapp_messages SET status='accettato_da_meta',message_id='wamid.retry',sent_at=?,last_error='' WHERE id=?",(app.whatsapp_now(),msg_id));return True,"ok"
        with patch.object(self.handler,"send_whatsapp_message",side_effect=accepted):self.handler.whatsapp_message_action(admin,failed_id,"riprova")
        self.handler.whatsapp_message_action(admin,scheduled_id,"annulla")
        with app.db() as conn:
            states={row["id"]:row["status"] for row in conn.execute("SELECT id,status FROM whatsapp_messages WHERE id IN (?,?)",(failed_id,scheduled_id))}
        self.assertEqual(states[failed_id],"accettato_da_meta");self.assertEqual(states[scheduled_id],"annullato")

    def test_whatsapp_ineligible_due_message_is_cancelled(self):
        _,pid,msg_id=self._whatsapp_record("2026-07-15T14:00:00")
        with app.db() as conn:conn.execute("UPDATE practices SET status='Ritirato' WHERE id=?",(pid,))
        result=self.handler.process_whatsapp_queue(current_time=datetime(2026,7,15,14,0))
        with app.db() as conn:status=conn.execute("SELECT status FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()["status"]
        self.assertFalse(result[0]["ok"]);self.assertEqual(status,"annullato")

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

    def test_dashboard_period_bounds_are_today_saturday_friday_and_month(self):
        reference=date(2026,7,15)
        self.assertEqual(app.dashboard_period_bounds("oggi",reference),("oggi",reference,reference))
        self.assertEqual(app.dashboard_period_bounds("settimana",reference),("settimana",date(2026,7,11),date(2026,7,17)))
        self.assertEqual(app.dashboard_period_bounds("mese",reference),("mese",date(2026,7,1),date(2026,7,31)))
        self.assertEqual(app.dashboard_period_bounds("mese",date(2026,12,8)),("mese",date(2026,12,1),date(2026,12,31)))

    def test_dashboard_uses_operational_and_economic_dates_without_double_counting(self):
        today=datetime.now().date();week_start=app.dashboard_period_bounds("settimana",today)[1]
        old_day=(today-timedelta(days=35)).isoformat();today_text=today.isoformat();week_day=week_start.isoformat();stamp=app.now()
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();uid=admin["id"]
            def practice(code,name,status,pickup,total,total_d="",deposit="0",remaining="0",payment="Da saldare",created=old_day):
                return conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,created_at,updated_at,created_by,
                                      animal_name,service_type,payment_status,total_service,price_cremation,total_text,deposit,remaining_balance,data_complete)
                                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                                    (code,"Privato","Livorno",status,pickup,created+"T08:00:00",stamp,uid,name,"Cremazione singola",payment,total,total,total_d,deposit,remaining)).lastrowid
            w_open=practice("CR-DASH-W","Ritiro oggi W","Ritirato",today_text,"300","","100","200")
            d_open=practice("CR-DASH-D","Ritiro oggi D","Ritirato",today_text,"400","330","100","230")
            paid=practice("CR-DASH-PAID","Consegnata oggi","Consegnato",week_day,"300","","100","0","Pagato")
            outside=practice("CR-DASH-OLD","Fuori periodo","Ritirato",old_day,"50","","0","50")
            conn.execute("INSERT INTO practice_history(practice_id,event_type,new_value,user_id,created_at) VALUES(?,?,?,?,?)",(paid,"Cambio stato rapido","Consegnato",uid,today_text+"T12:00:00"))
            for pid,ptype,channel,amount,paid_at in (
                (w_open,"acconto_ordinario","ordinario",100,today_text+"T09:00:00"),
                (d_open,"acconto_d","D",100,today_text+"T10:00:00"),
                (paid,"acconto_ordinario","ordinario",100,old_day+"T10:00:00"),
                (paid,"saldo_ordinario","ordinario",200,today_text+"T11:00:00"),
            ):
                conn.execute("INSERT INTO payment_movements(practice_id,payment_type,payment_channel,amount,paid_at,user_id,notes,created_at) VALUES(?,?,?,?,?,?,?,?)",(pid,ptype,channel,amount,paid_at,uid,"test",stamp))
            snapshot=(conn.execute("SELECT count(*) n FROM practices").fetchone()["n"],conn.execute("SELECT count(*) n FROM payment_movements").fetchone()["n"],conn.execute("SELECT count(*) n FROM practice_history").fetchone()["n"])
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/?pratiche_periodo=oggi&pagamenti_periodo=oggi";self.handler.dashboard(admin);page=rendered[-1]
        self.assertIn('data-dashboard-card="Ritirato" data-count="2"',page)
        self.assertIn('data-dashboard-card="Consegnato" data-count="1"',page)
        self.assertIn('data-dashboard-payment="Da saldare" data-count="3" data-amount="480.00"',page)
        self.assertIn('data-dashboard-payment="Acconto" data-count="2" data-amount="200.00"',page)
        self.assertIn('data-dashboard-payment="Pagato" data-count="1" data-amount="200.00"',page)
        self.assertIn("Entrate settimana in corso",page);self.assertIn("400,00",page)
        self.assertIn("Ultime 10 pratiche per data recupero",page);self.assertIn("Apri archivio",page)
        self.assertNotIn("Attività recenti",page);self.assertNotIn("Centro notifiche",page)
        self.assertEqual(page.count('class="period-selector"'),2);self.assertIn("/notifiche",page)
        self.assertIn("dashboard_event=ritirati",page);self.assertNotIn("dashboard_event=ritirati&amp;stato=Ritirato",page)
        self.handler.path=f"/?pratiche_periodo=settimana&pagamenti_periodo=settimana";self.handler.dashboard(admin);week_page=rendered[-1]
        self.assertIn('data-dashboard-card="Ritirato" data-count="3"',week_page)
        self.assertIn('data-dashboard-payment="Pagato" data-count="1" data-amount="200.00"',week_page)
        self.handler.path=f"/?pratiche_periodo=mese&pagamenti_periodo=mese";self.handler.dashboard(admin);month_page=rendered[-1]
        self.assertIn('data-dashboard-card="Ritirato" data-count="3"',month_page)
        self.assertIn('data-dashboard-payment="Acconto" data-count="2" data-amount="200.00"',month_page)
        with app.db() as conn:
            self.assertEqual(snapshot,(conn.execute("SELECT count(*) n FROM practices").fetchone()["n"],conn.execute("SELECT count(*) n FROM payment_movements").fetchone()["n"],conn.execute("SELECT count(*) n FROM practice_history").fetchone()["n"]))

    def test_dashboard_card_lists_and_payment_lists_keep_the_selected_period(self):
        today=datetime.now().date().isoformat();old=(datetime.now().date()-timedelta(days=40)).isoformat();stamp=app.now()
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();uid=admin["id"]
            current=conn.execute("INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,created_at,updated_at,created_by,animal_name,total_service,price_cremation,payment_status,remaining_balance,deposit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("CR-CURRENT","Privato","Livorno","Ritirato",today,old+"T08:00:00",stamp,uid,"Visibile",200,200,"Acconto",150,50)).lastrowid
            conn.execute("INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,created_at,updated_at,created_by,animal_name,total_service,price_cremation,payment_status,remaining_balance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",("CR-OLD","Privato","Livorno","Ritirato",old,old+"T08:00:00",stamp,uid,"Nascosta",100,100,"Da saldare",100))
            conn.execute("INSERT INTO payment_movements(practice_id,payment_type,payment_channel,amount,paid_at,user_id,created_at) VALUES(?,?,?,?,?,?,?)",(current,"acconto_ordinario","ordinario",50,today+"T10:00:00",uid,stamp))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path=f"/archivio/pratiche?dashboard_event=ritirati&periodo=oggi&dal={today}&al={today}";self.handler.archive(admin);archive_page=rendered[-1]
        self.assertIn("Visibile",archive_page);self.assertNotIn("Nascosta",archive_page);self.assertIn("Oggi",archive_page)
        self.handler.path=f"/pagamenti/acconti?periodo=oggi&dal={today}&al={today}";self.handler.payment_overview(admin,"acconti");payment_page=rendered[-1]
        self.assertIn("CR-CURRENT",payment_page);self.assertIn("50,00",payment_page);self.assertIn("Incassi registrati",payment_page)
        self.handler.path=f"/pagamenti/pagati?periodo=oggi&dal={today}&al={today}";self.handler.payment_overview(admin,"pagati")
        self.assertNotIn("CR-CURRENT",rendered[-1])

    def test_dashboard_layout_is_compact_responsive_and_ios_safe(self):
        for token in (".dashboard-section-head",".period-selector","min-height:44px","var(--safe-bottom)",".dashboard-chart-only"):
            self.assertIn(token,app.CSS)
        dashboard_constants="".join(value for value in app.App.dashboard.__code__.co_consts if isinstance(value,str))
        self.assertIn("localStorage.getItem('ppm_'+key)",dashboard_constants)

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
