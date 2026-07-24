import os
import io
import json
import re
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
        rendered=[];self.handler.path=f"/pratiche/{pid}/modifica?return_to=%2Farchivio%2Fpratiche%3Fstato%3DRitirato";self.handler.send_html=lambda html,*args:rendered.append(html)
        self.handler.edit_page(admin,pid)
        page=rendered[-1]
        self.assertIn(f'data-autosave-url="/api/pratiche/{pid}/autosave"',page)
        self.assertIn("Ultimo salvataggio",page)
        # ANNULLA must let the user leave without saving, going straight back to where
        # they came from (not through the practice detail page), and be reachable both
        # from the sticky top bar and the bottom of the form.
        self.assertEqual(page.count('href="/archivio/pratiche?stato=Ritirato">Annulla</a>'),2)
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

    def test_invoice_total_recomputes_on_preventivo_changes_unless_manually_edited(self):
        stamp = "2026-07-15T10:00:00"
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                   created_by,animal_name,tag_da_richiamare,price_cremation,total_service,invoice_total,invoice_total_manual,payment_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-INVAUTO", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido", "Si",
                 "100", "100.00", "100.00", "", "Da saldare"),
            ).lastrowid

        # Bumping an economic field must re-flow into invoice_total automatically (auto mode, not yet edited by hand).
        captured = []
        self.handler.send_json = lambda obj, status=200: captured.append((obj, status))
        self.handler.form = lambda: {"updated_at": stamp, "changes_json": json.dumps({"price_cremation": "150"})}
        self.handler.practice_autosave(admin, pid)
        self.assertEqual(captured[-1][1], 200)
        version = captured[-1][0]["updated_at"]
        with app.db() as conn:
            row = conn.execute("SELECT total_service,invoice_total,invoice_total_manual FROM practices WHERE id=?", (pid,)).fetchone()
            self.assertEqual((row["total_service"], row["invoice_total"], row["invoice_total_manual"]), ("150.00", "150.00", ""))

        # The user now types a custom invoice total by hand: the manual flag flips and future
        # preventivo edits must no longer silently overwrite what they typed.
        self.handler.form = lambda: {"updated_at": version, "changes_json": json.dumps({"invoice_total": "999.00", "invoice_total_manual": "Si"})}
        self.handler.practice_autosave(admin, pid)
        version = captured[-1][0]["updated_at"]
        with app.db() as conn:
            row = conn.execute("SELECT invoice_total,invoice_total_manual FROM practices WHERE id=?", (pid,)).fetchone()
            self.assertEqual((row["invoice_total"], row["invoice_total_manual"]), ("999.00", "Si"))

        self.handler.form = lambda: {"updated_at": version, "changes_json": json.dumps({"price_cremation": "200"})}
        self.handler.practice_autosave(admin, pid)
        with app.db() as conn:
            row = conn.execute("SELECT total_service,invoice_total FROM practices WHERE id=?", (pid,)).fetchone()
            self.assertEqual((row["total_service"], row["invoice_total"]), ("200.00", "999.00"))

    def test_autosave_clears_catalog_checkboxes_when_urn_is_filled_in(self):
        stamp = "2026-07-15T10:00:00"
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,owner_first_name,owner_last_name,owner_phone,tag_da_richiamare,send_catalog)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-CATALOGO", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido", "Mario", "Rossi", "333111", "Si", "Si"),
            ).lastrowid
        captured = []
        self.handler.send_json = lambda obj, status=200: captured.append((obj, status))
        # Filling in urn_notes with a real choice must clear send_catalog too, in the
        # same autosave write — not just in the in-memory normalization.
        self.handler.form = lambda: {"updated_at": stamp, "changes_json": json.dumps({"urn_notes": "Urna in legno chiaro"})}
        self.handler.practice_autosave(admin, pid)
        self.assertEqual(captured[-1][1], 200)
        self.assertIn("send_catalog", captured[-1][0]["saved_fields"])
        with app.db() as conn:
            row = conn.execute("SELECT urn_notes,send_catalog,catalog_sent FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["urn_notes"], "Urna in legno chiaro")
        self.assertEqual(row["send_catalog"], "")
        self.assertFalse(row["catalog_sent"])

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
        invalid_pages=[];self.handler.send_html=lambda content,*a:invalid_pages.append(content);self.handler.redirect=lambda path:None
        self.handler.form=lambda:{"order_recipient_email":"non valida"};self.handler.save_order_settings(admin)
        self.assertIn("indirizzo email destinatario valido",invalid_pages[-1])
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

    def test_second_calco_naso_polpastrello_fields_and_possibile_tags(self):
        html = self.handler.fields_html()
        for expected in (
            'name="price_paw_cast_2"', 'name="price_nose_cast_2"',
            "POSSIBILE ASSISTITA STREAMING", "POSSIBILE CALCO",
            "POSSIBILE CALCO POLPASTRELLO", "POSSIBILE CALCO NASO",
            'name="tag_possibile_assistita_streaming"', 'name="tag_possibile_calco"',
            'name="tag_possibile_calco_paw"', 'name="tag_possibile_calco_nose"',
        ):
            self.assertIn(expected, html)
        self.assertIn("+ Aggiungi calco polpastrello", app.APP_JS)
        self.assertIn("+ Aggiungi calco naso", app.APP_JS)
        self.assertIn("Secondo calco polpastrello", app.APP_JS)
        self.assertIn("Secondo calco naso", app.APP_JS)
        self.assertIn('price_paw_cast_2', app.MONEY_FIELDS)
        self.assertIn('price_nose_cast_2', app.MONEY_FIELDS)
        data = self.handler.normalized_fields({
            "price_paw_cast_2": "12,50", "price_nose_cast_2": "8",
            "tag_possibile_assistita_streaming": "Si", "tag_possibile_calco": "Si",
            "tag_possibile_calco_paw": "Si", "tag_possibile_calco_nose": "Si",
        })
        self.assertEqual(data["price_paw_cast_2"], "12.50")
        self.assertEqual(data["price_nose_cast_2"], "8")
        self.assertEqual(data["tag_possibile_assistita_streaming"], "Si")
        self.assertEqual(data["tag_possibile_calco"], "Si")
        self.assertEqual(data["tag_possibile_calco_paw"], "Si")
        self.assertEqual(data["tag_possibile_calco_nose"], "Si")
        empty = self.handler.normalized_fields({})
        for key in ("tag_possibile_assistita_streaming", "tag_possibile_calco", "tag_possibile_calco_paw", "tag_possibile_calco_nose"):
            self.assertEqual(empty[key], "")

    def test_possibile_tags_render_as_badges(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,service_type,payment_status,tag_possibile_assistita_streaming,tag_possibile_calco,tag_possibile_calco_paw,tag_possibile_calco_nose)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-TAGS", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido", "Cane", "Cremazione singola", "Da saldare", "Si", "Si", "Si", "Si")).lastrowid
            row = conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
        badges = self.handler.tag_badges(row)
        for expected in ("POSSIBILE ASSISTITA STREAMING", "POSSIBILE CALCO", "POSSIBILE CALCO POLPASTRELLO", "POSSIBILE CALCO NASO"):
            self.assertIn(expected, badges)

    def test_richiesta_animale_speditore_reorder_and_relabel(self):
        html = self.handler.fields_html()
        self.assertIn('<label>Servizio *</label><select name="service_type" required>', html)
        self.assertIn('<label>Specie *</label><input name="species" value="" required>', html)
        for expected in ('<label>Peso</label>', '<label>Anni</label>', '<label>Mesi</label>', 'name="owner_phone_note"'):
            self.assertIn(expected, html)
        species_pos = html.index('name="species"')
        animal_name_pos = html.index('name="animal_name"')
        self.assertLess(species_pos, animal_name_pos)
        selectors = "['[name=\"owner_veterinarian_id\"]','#clientSearch','[name=\"owner_first_name\"]','[name=\"owner_last_name\"]','[name=\"owner_phone\"]','[name=\"owner_phone_2\"]','[name=\"owner_phone_note\"]'"
        self.assertIn(selectors, app.APP_JS)

    def test_notes_field_moved_out_of_preventivo_into_its_own_section(self):
        html = self.handler.fields_html()
        preventivo_start = html.index('<section class="section"><h2>Preventivo</h2>')
        preventivo_end = html.index('</section>', preventivo_start)
        preventivo_html = html[preventivo_start:preventivo_end]
        self.assertNotIn('name="notes"', preventivo_html, "NOTE must no longer live inside the Preventivo section")
        self.assertIn('<section class="section"><h2>Note</h2><div class="fields"><div class="field full"><label>NOTE</label><textarea name="notes">', html)
        notes_section_pos = html.index('<section class="section"><h2>Note</h2>')
        self.assertGreater(notes_section_pos, preventivo_end, "the Note section must come after Preventivo")

    def test_cremazione_collettiva_relaxes_required_fields(self):
        self.assertIn("const exempt = !!(callBack?.checked || (service && service.value === 'Cremazione collettiva') || (origin && origin.value === 'Collaboratore'));", app.APP_JS)
        no_error = self.handler.validation_error({"service_type": "Cremazione collettiva"})
        self.assertEqual(no_error, "")
        self.assertEqual(self.handler.is_complete({"service_type": "Cremazione collettiva"}), 1)
        error = self.handler.validation_error({"service_type": "Cremazione singola"})
        self.assertIn("Nome", error)

    def test_calco_naso_polpastrello_type_dropdowns_autofill_price(self):
        html = self.handler.fields_html()
        for expected in ('name="nose_cast_type"', 'name="nose_cast_type_2"', 'name="paw_cast_type"'):
            self.assertIn(expected, html)
        self.assertIn("NOSE_CAST_OPTIONS=[['Bronzo S',220],['Bronzo M',260],['Bronzo G',300],['Argento S',300],['Argento M',380],['Argento G',500]]", app.APP_JS)
        self.assertIn("PAW_CAST_OPTIONS=[['Argento',200]]", app.APP_JS)
        data = self.handler.normalized_fields({"nose_cast_type": "Bronzo M", "nose_cast_type_2": "Argento G", "paw_cast_type": "Argento"})
        self.assertEqual(data["nose_cast_type"], "Bronzo M")
        self.assertEqual(data["nose_cast_type_2"], "Argento G")
        self.assertEqual(data["paw_cast_type"], "Argento")

    def test_accessori_dropdown_reduced_with_conditional_detail_field(self):
        self.assertIn("const options=['','Braccialetto','Collana','Calco inchiostro']", app.APP_JS)
        self.assertIn("['Collana','Braccialetto'].includes(select.value)", app.APP_JS)
        html = self.handler.fields_html()
        for expected in ('name="accessory_detail"', 'name="accessory_detail_2"'):
            self.assertIn(expected, html)
        data = self.handler.normalized_fields({"accessory_detail": "Nome inciso", "accessory_detail_2": "Altro testo"})
        self.assertEqual(data["accessory_detail"], "Nome inciso")
        self.assertEqual(data["accessory_detail_2"], "Altro testo")

    def test_totale_w_and_totale_d_groups_are_independent(self):
        html = self.handler.fields_html()
        for expected in ('<label>Acconto D €</label><input name="deposit_final"', '<label>Rimanenza D €</label><input name="remaining_final"'):
            self.assertIn(expected, html)
        self.assertIn("ESTREMI INVIATI", app.APP_JS)
        self.assertEqual(app.MONEY_FIELDS.get("deposit_final"), "Acconto D")
        self.assertEqual(app.MONEY_FIELDS.get("remaining_final"), "Rimanenza D")
        # remaining_final is derived server-side from total_text (Totale D) minus
        # deposit_final, not passed through as-is: with no Totale D on this practice,
        # there is nothing owed on that circuit regardless of what was submitted.
        data = self.handler.normalized_fields({"deposit_final": "50,25", "remaining_final": "100", "estremi_sent": "Si", "send_estremi": "Si"})
        self.assertEqual(data["deposit_final"], "50.25")
        self.assertEqual(data["remaining_final"], "")
        self.assertEqual(data["estremi_sent"], "Si")
        self.assertEqual(data["send_estremi"], "", "estremi_sent=Si must clear send_estremi like catalog_sent does")
        data_with_d = self.handler.normalized_fields({"total_text": "360", "deposit_final": "100"})
        self.assertEqual(data_with_d["remaining_final"], "260.00")

    def test_catalog_checkboxes_auto_uncheck_when_urn_is_decided(self):
        # Placeholder/undecided urn text must NOT clear the checkboxes.
        for placeholder in ("", "/", "Da decidere", "da decidere", "  /  "):
            data = self.handler.normalized_fields({"send_catalog": "Si", "urn_notes": placeholder})
            self.assertEqual(data["send_catalog"], "Si", f"placeholder {placeholder!r} should not clear send_catalog")
        # A real free-text urn choice clears both checkboxes.
        data = self.handler.normalized_fields({"send_catalog": "Si", "catalog_sent": "Si", "urn_notes": "Urna in legno chiaro"})
        self.assertEqual(data["send_catalog"], "")
        self.assertEqual(data["catalog_sent"], "")
        # Selecting a catalog urn (urn_id set) also clears both, even with empty notes.
        data = self.handler.normalized_fields({"send_catalog": "Si", "catalog_sent": "Si", "urn_id": "5"})
        self.assertEqual(data["send_catalog"], "")
        self.assertEqual(data["catalog_sent"], "")

    def test_invoice_total_always_sources_from_totale_w_never_totale_d(self):
        # Even when Totale D is present (and larger), the auto-computed invoice total
        # must always come from Totale W, never from the D circuit.
        data = self.handler.normalized_fields({
            "price_cremation": "450", "total_text": "360", "deposit": "450", "payment_status": "Pagato",
        })
        self.assertEqual(data["total_service"], "450.00")
        self.assertEqual(data["invoice_total"], "450.00")

    def test_pagato_forces_remaining_w_and_d_to_zero_even_if_deposit_is_short(self):
        # A practice flipped to Pagato by hand, without the deposit fields being
        # updated to match, must never show a leftover balance on either circuit.
        data = self.handler.normalized_fields({
            "price_cremation": "450", "total_text": "360", "deposit": "50", "deposit_final": "10",
            "payment_status": "Pagato",
        })
        self.assertEqual(data["remaining_balance"], "0.00")
        self.assertEqual(data["remaining_final"], "0.00")

    def test_quick_payment_zeroes_remaining_final_too_when_marked_pagato(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   owner_first_name,service_type,payment_status,total_service,total_text,deposit,deposit_final,remaining_balance,remaining_final)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-PAY-WD", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Mario",
                 "Cremazione singola", "Acconto", "450", "360", "50", "10", "400.00", "350.00"),
            ).lastrowid
            self.handler.add_payment_movement(conn, pid, "acconto_d", "D", 10, admin["id"], "Acconto precedente", "2026-07-13")
        self.handler.form = lambda: {"payment_status": "Pagato", "payment_method": "Pos", "payment_amount": "350,00",
                                      "invoice_number": "", "invoice_total": "", "invoice_date": "2026-07-14",
                                      "economic_at": "2026-07-14"}
        self.handler.redirect = lambda path: None; self.handler.headers = {}
        self.handler.quick_payment(admin, pid)
        with app.db() as conn:
            row = conn.execute("SELECT remaining_balance,remaining_final FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual((row["remaining_balance"], row["remaining_final"]), ("0.00", "0.00"))

    def test_startup_backfill_zeroes_stale_remaining_on_existing_pagato_practices(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,payment_status,total_service,total_text,remaining_balance,remaining_final)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-000064", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido",
                 "Pagato", "450", "360", "450.00", "360.00"),
            ).lastrowid
        app.init_db()  # idempotent startup migration must clean up stale data on every run
        with app.db() as conn:
            row = conn.execute("SELECT remaining_balance,remaining_final FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual((row["remaining_balance"], row["remaining_final"]), ("0.00", "0.00"))

    def test_practice_summary_shows_notes_in_own_section_between_riepilogo_and_economic_data(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            with_note = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,notes) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("CR-NOTE", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido", "Attenzione: cliente da richiamare"),
            ).lastrowid
            without_note = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name) VALUES(?,?,?,?,?,?,?,?)""",
                ("CR-NONOTE", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Rex"),
            ).lastrowid
        rendered = []; self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.practice(admin, with_note)
        page = rendered[-1]
        # Notes are no longer a kv inside the Riepilogo grid: they get their own
        # section, appearing after Riepilogo (whose grid must stay untouched) and
        # before Dati economici.
        self.assertNotIn('<div class="kv"><small>Nota</small>', page)
        riepilogo_pos = page.index("<h2>Riepilogo</h2>")
        note_section_pos = page.index('<div class="section"><h2>Note</h2>')
        note_text_pos = page.index("Attenzione: cliente da richiamare")
        economic_pos = page.index("<h2>Dati economici</h2>")
        self.assertLess(riepilogo_pos, note_section_pos)
        self.assertLess(note_section_pos, note_text_pos)
        self.assertLess(note_text_pos, economic_pos)
        self.assertEqual(page.count('<div class="section"><h2>Note</h2>'), 1, "notes must render in exactly one section, not duplicated")
        self.handler.practice(admin, without_note)
        page_without = rendered[-1]
        self.assertIn('<div class="section"><h2>Note</h2><p><span class="sub">Nessuna nota.</span></p></div>', page_without)

    def test_practice_summary_speditore_shows_address_and_tax_code(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,owner_first_name,owner_last_name,owner_street,owner_city,owner_province,owner_zip,owner_tax_code)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-ADDR", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido",
                 "Mario", "Rossi", "Via Roma 1", "Livorno", "LI", "57100", "RSSMRA80A01H501U"),
            ).lastrowid
        rendered = []; self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.practice(admin, pid)
        page = rendered[-1]
        self.assertIn("Via Roma 1, 57100 Livorno (LI)", page)
        self.assertIn("CF: RSSMRA80A01H501U", page)

    def test_practice_summary_shows_total_due_or_paid_matching_active_channel(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            due_w = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,payment_status,price_cremation,total_service,deposit)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-RIEP-W", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido",
                 "Acconto", "300", "300.00", "100"),
            ).lastrowid
            paid_d = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,payment_status,total_text,payment_amount)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-RIEP-D", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Rex",
                 "Pagato", "360", "360.00"),
            ).lastrowid
        rendered = []; self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.practice(admin, due_w)
        page = rendered[-1]
        self.assertIn('<small>Totale da pagare W</small><b>€ 200,00</b>', page)
        self.handler.practice(admin, paid_d)
        page = rendered[-1]
        self.assertIn('<small>Totale pagato D</small><b>€ 360,00</b>', page)
        self.assertIn("e.target.name === 'deposit_final'", app.APP_JS)
        self.assertNotIn("definitive > 0 ? definitive : ppmNumber(totalField ? totalField.value : 0);\n  const remaining", app.APP_JS)

    def test_fare_fattura_unchecked_when_invoice_number_filled(self):
        self.assertIn("if(makeInvoice&&invoiceNumber.value.trim())makeInvoice.checked=false;", app.APP_JS)

    def test_unaccent_helper_folds_accents_case_and_handles_empty_input(self):
        self.assertEqual(app.unaccent("Milù"), "milu")
        self.assertEqual(app.unaccent("MILÙ"), "milu")
        self.assertEqual(app.unaccent("Città è perché"), "citta e perche")
        self.assertEqual(app.unaccent(None), "")
        self.assertEqual(app.unaccent(""), "")
        self.assertEqual(app.like_term("Milù"), "%milu%")

    def test_header_search_is_accent_insensitive(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         owner_first_name,owner_last_name,animal_name,species,estimated_weight,service_type,pickup_date,payment_status)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-ACCENT", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Anna", "Verdi", "Milù", "Gatto", "4", "Cremazione singola", "2026-07-20", "Da saldare"))
        self.handler.path = "/api/calendario/pratiche/search?q=Milu"
        payload = []
        self.handler.send_json = lambda obj, status=200: payload.append(obj)
        self.handler.api_calendar_practices_search(None)
        results = payload[0]["results"]
        self.assertTrue(any(r["display"].endswith("Milù") for r in results))

    def test_client_search_is_accent_insensitive(self):
        with app.db() as conn:
            stamp = app.now()
            conn.execute(
                """INSERT INTO clients(first_name,last_name,phone,created_at,updated_at) VALUES(?,?,?,?,?)""",
                ("Milù", "Città", "0501234567", stamp, stamp),
            )
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        response = {}
        self.handler.path = "/api/clienti/search?q=Milu Citta"
        self.handler.send_json = lambda obj, status=200: response.update(obj=obj, status=status)
        self.handler.api_clients_search(admin)
        results = response["obj"]["results"]
        self.assertTrue(any(r["display"] == "Milù Città" for r in results))

    def test_urn_catalog_search_is_accent_insensitive(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.form = lambda: {"category": "Urna", "name": "Urna Perù", "material": "Legno", "price": "50.00", "quantity": "1", "low_stock_threshold": "1"}
        self.handler.redirect = lambda path: setattr(self, "redirected", path)
        self.handler.save_urn(admin)
        rendered = []
        self.handler.send_html = lambda html, *args: rendered.append(html)
        self.handler.path = "/catalogo-urne?q=Peru"
        self.handler.urn_catalog_page(admin)
        page = rendered[-1]
        self.assertIn("Urna Perù", page)

    def test_archive_search_is_accent_insensitive(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,payment_status) VALUES(?,?,?,?,?,?,?,?,?)""",
                         ("CR-ARCH-ACCENT", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Milù", "Da saldare"))
        self.handler.path = "/archivio/pratiche?q=Milu"
        rendered = []
        self.handler.send_html = lambda html, *args: rendered.append(html)
        self.handler.archive(admin)
        self.assertIn("Milù", rendered[-1])

    def test_header_search_result_field_order(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         owner_first_name,owner_last_name,animal_name,species,estimated_weight,service_type,pickup_date,payment_status)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-ORDER", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Anna", "Verdi", "Search-Order-Animal", "Gatto", "9", "Cremazione collettiva", "2026-07-20", "Da saldare"))
        self.handler.path = "/api/calendario/pratiche/search?q=Search-Order-Animal"
        payload = []
        self.handler.send_json = lambda obj, status=200: payload.append(obj)
        self.handler.api_calendar_practices_search(None)
        result = payload[0]["results"][0]
        self.assertEqual(result["display"], "20/07/2026 · Search-Order-Animal")
        self.assertEqual(result["subtitle"], "Anna Verdi · 9 kg · Collettiva · CR-ORDER")

    def test_riepilogo_shows_call_whatsapp_buttons_and_tag_badges(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         owner_first_name,owner_last_name,owner_phone,owner_phone_2,animal_name,species,service_type,payment_status,tag_saluto,data_complete)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-RIEP", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Luca", "Bianchi", "333 111 2222", "0586 123456", "Fido", "Cane", "Cremazione singola", "Da saldare", "Si", 1)).lastrowid
        rendered = []
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.path = f"/pratiche/{pid}"
        self.handler.practice(admin, pid)
        page = rendered[-1]
        self.assertIn('href="tel:3331112222"', page)
        self.assertIn('href="https://wa.me/393331112222"', page)
        self.assertIn('href="tel:0586123456"', page)
        self.assertIn("SALUTO", page)
        self.assertNotIn(">CHIAMA<", page)
        self.assertNotIn(">WHATSAPP<", page)
        self.assertIn('class="icon-btn phone-action-btn call-btn"', page)
        self.assertIn('class="icon-btn phone-action-btn whatsapp-btn"', page)

    def test_dati_economici_shows_preventivo_items_before_totals(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,service_type,payment_status,price_cremation)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-ECON", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido", "Cane", "Cremazione singola", "Da saldare", "150")).lastrowid
        rendered = []
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.path = f"/pratiche/{pid}"
        self.handler.practice(admin, pid)
        page = rendered[-1]
        estimate_pos = page.index("Voci del preventivo")
        totals_pos = page.index("Totale pratica")
        self.assertLess(estimate_pos, totals_pos)

    def test_owner_veterinarian_field_is_a_search_bar(self):
        html = self.handler.fields_html()
        self.assertIn('id="ownerVetSearch"', html)
        self.assertIn('id="ownerVetResults"', html)
        self.assertIn('<select name="owner_veterinarian_id" class="hidden"', html)
        self.assertIn("function setupOwnerVetLookup(){", app.APP_JS)
        self.assertIn("setupOwnerVetLookup();", app.APP_JS)

    def test_clear_client_selection_also_clears_autofilled_fields(self):
        self.assertIn(
            "['owner_first_name','owner_last_name','owner_company','owner_phone','owner_phone_2','owner_email','owner_tax_code','owner_vat','owner_sdi','owner_street','owner_city','owner_province','owner_zip','owner_notes'].forEach(name=>setField(name,''));",
            app.APP_JS,
        )

    def test_invoice_block_positioned_between_estremi_and_totale_d(self):
        self.assertIn(
            "addRow([field('total_service'),field('deposit'),field('remaining_balance')],[field('send_estremi'),field('estremi_sent')]);\n"
            "  addRow([field('invoice_number'),field('invoice_date'),field('invoice_total')],[field('make_invoice')]);\n"
            "  addRow([field('total_text'),field('deposit_final'),field('remaining_final')]);",
            app.APP_JS,
        )

    def test_calco_naso_polpastrello_type_before_price_and_expandable(self):
        html = self.handler.fields_html()
        for expected in (
            'name="nose_cast_type_3"', 'name="nose_cast_type_4"',
            'name="paw_cast_type_2"', 'name="paw_cast_type_3"', 'name="paw_cast_type_4"',
            'name="price_nose_cast_3"', 'name="price_nose_cast_4"',
            'name="price_paw_cast_3"', 'name="price_paw_cast_4"',
        ):
            self.assertIn(expected, html)
        self.assertIn("const setupExpandableCast=(config)=>{", app.APP_JS)
        self.assertIn("primaryTypeWrap=buildSelect(hiddenType,config.primaryTypeName,config.primaryTypeLabel,priceInput);\n    priceWrap.parentNode.insertBefore(primaryTypeWrap,priceWrap);", app.APP_JS)
        self.assertIn("addFirstLabel:'+ Aggiungi calco naso', addMoreLabel:'+ Aggiungi altro calco naso',", app.APP_JS)
        self.assertIn("addFirstLabel:'+ Aggiungi calco polpastrello', addMoreLabel:'+ Aggiungi altro calco polpastrello',", app.APP_JS)
        self.assertIn("const buttons=(text)=>original.filter(node=>node.matches?.('button')&&node.textContent.includes(text));", app.APP_JS)
        data = self.handler.normalized_fields({
            "nose_cast_type_3": "Bronzo G", "price_nose_cast_3": "300",
            "paw_cast_type_4": "Argento", "price_paw_cast_4": "200,00",
        })
        self.assertEqual(data["nose_cast_type_3"], "Bronzo G")
        self.assertEqual(data["price_nose_cast_3"], "300")
        self.assertEqual(data["paw_cast_type_4"], "Argento")
        self.assertEqual(data["price_paw_cast_4"], "200.00")

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

    def test_riconsegna_delivery_location_checkboxes(self):
        html=self.handler.fields_html()
        for expected in ('name="delivery_at_clinic" value="Si"','name="delivery_at_home" value="Si"','IN AMBULATORIO','A CASA'):
            self.assertIn(expected,html)
        data=self.handler.normalized_fields({"delivery_at_clinic":"Si","delivery_at_home":""})
        self.assertEqual(data["delivery_at_clinic"],"Si")
        self.assertEqual(data["delivery_at_home"],"")
        data2=self.handler.normalized_fields({"delivery_at_clinic":"bogus","delivery_at_home":"Si"})
        self.assertEqual(data2["delivery_at_clinic"],"")
        self.assertEqual(data2["delivery_at_home"],"Si")
        self.assertIn("addRow([field('price_delivery')],[field('delivery_at_clinic'),field('delivery_at_home')]);",app.APP_JS)

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

    def test_invoices_page_shows_legacy_practice_invoice_alongside_movement_invoice(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            legacy_pid=conn.execute("""INSERT INTO practices(practice_number,invoice_number,invoice_date,invoice_total,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name,owner_first_name)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-LEGACYINV","FT-OLD","2026-06-01","180.00","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Fufi","Elena")).lastrowid
            new_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-NEWINV","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Marta","Cremazione singola","Da saldare","120","120")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"120,00","payment_channel":"W","economic_at":"2026-07-01","saldo_invoice_number":"FT-NEW","saldo_invoice_total":"120,00","ajax":"1"}
        self.handler.quick_payment(admin,new_pid)
        self.assertTrue(responses[-1][0]["ok"])
        rendered=[];self.handler.send_html=lambda content:rendered.append(content);self.handler.path="/fatture"
        self.handler.invoices_page(admin)
        page=rendered[-1]
        self.assertIn("FT-OLD",page)
        self.assertIn("FT-NEW",page)
        with app.db() as conn:
            legacy=conn.execute("SELECT invoice_number FROM practices WHERE id=?",(legacy_pid,)).fetchone()
            self.assertEqual(legacy["invoice_number"],"FT-OLD")

    def test_practice_page_shows_movement_invoice_selection_form(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-PAGEFORM","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Gino","Cremazione singola","Da saldare","90","90")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"90,00","payment_channel":"W","economic_at":"2026-07-10","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            movement_id=conn.execute("SELECT id FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()["id"]
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.practice(admin,pid)
        page=rendered[-1]
        self.assertIn("Fatture per movimento",page)
        self.assertIn(f'name="movement_{movement_id}"',page)
        self.assertIn(f'action="/pratiche/{pid}/fatture-movimenti"',page)
        self.assertIn("Non fatturato",page)
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
                          (datetime.now(app.ROME_TZ).date()+timedelta(days=1)).isoformat(), "23:59", stamp, stamp, admin))
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
        self.assertIn("pet-paradise-shell-__SW_VERSION__", source)

    def test_service_worker_cache_name_is_versioned_and_old_caches_are_cleared(self):
        source = (app.ASSETS / "sw.js").read_text(encoding="utf-8")
        install_block = source.split("addEventListener('install'", 1)[1].split("addEventListener('activate'", 1)[0]
        install_code = "\n".join(line for line in install_block.splitlines() if not line.strip().startswith("//"))
        self.assertNotIn("self.skipWaiting()", install_code)
        self.assertIn("keys.filter(key => key !== CACHE).map(key => caches.delete(key))", source)
        self.assertIn("self.clients.claim();", source)
        self.assertIn("event.data.type === 'SKIP_WAITING'", source)

    def test_sw_route_serves_versioned_script_with_no_cache_header(self):
        self.assertTrue(app.APP_VERSION and app.APP_VERSION != "dev")
        sent = {}
        self.handler.send_response = lambda status: sent.update(status=status)
        self.handler.send_header = lambda k, v: sent.setdefault("headers", {}).__setitem__(k, v)
        self.handler.end_headers = lambda: None
        written = []
        self.handler.wfile = type("W", (), {"write": staticmethod(lambda data: written.append(data))})()
        self.handler.service_worker()
        self.assertEqual(sent["status"], 200)
        self.assertEqual(sent["headers"]["Cache-Control"], "no-cache")
        body = written[0].decode("utf-8")
        self.assertIn(f"pet-paradise-shell-{app.APP_VERSION}", body)
        self.assertNotIn("__SW_VERSION__", body)

    def test_app_version_is_stable_and_derived_from_source_when_no_commit_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RENDER_GIT_COMMIT", None)
            os.environ.pop("SOURCE_VERSION", None)
            first = app._compute_app_version()
            second = app._compute_app_version()
        self.assertEqual(first, second)
        self.assertNotEqual(first, "dev")

    def test_app_version_changes_when_commit_env_is_set(self):
        with patch.dict(os.environ, {"RENDER_GIT_COMMIT": "abcdef1234567890"}):
            self.assertEqual(app._compute_app_version(), "abcdef123456")

    def test_service_worker_update_flow_never_auto_applies_while_page_is_visible(self):
        js = app.APP_JS
        self.assertIn("function showSwUpdateBanner(", js)
        self.assertIn("function applySwUpdateWhenSafe(", js)
        self.assertIn("navigator.serviceWorker.addEventListener('controllerchange'", js)
        self.assertIn("registration.waiting", js)
        self.assertIn("registration.addEventListener('updatefound'", js)
        # The banner path (foreground) must not postMessage immediately; only the
        # hidden/background path is allowed to call activate() straight away.
        apply_fn = js.split("function applySwUpdateWhenSafe(worker){", 1)[1].split("\n}", 1)[0]
        self.assertIn("showSwUpdateBanner(activate)", apply_fn)
        self.assertIn("document.querySelector('.sw-update-banner')?.remove()", apply_fn)

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







    def test_collaborator_practice_gets_separate_col_numbering_and_billing_status(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        redirects = []; self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {"operator_name": "ALESSIO", "service_type": "Cremazione singola", "request_origin": "Collaboratore", "destination_branch": "Livorno", "collaborator_name": "HUMANITAS CROCE VERDE"}
        self.handler.create_practice(admin)
        collab_pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            collab_practice = conn.execute("SELECT * FROM practices WHERE id=?", (collab_pid,)).fetchone()
        self.assertTrue(collab_practice["practice_number"].startswith("COL-"))
        self.assertEqual(collab_practice["billing_status"], "Da fatturare")

        redirects.clear()
        self.handler.form = lambda: {"operator_name": "ALESSIO", "service_type": "Cremazione singola", "request_origin": "Privato", "destination_branch": "Livorno", "tag_da_richiamare": "Si"}
        self.handler.create_practice(admin)
        normal_pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            normal_practice = conn.execute("SELECT * FROM practices WHERE id=?", (normal_pid,)).fetchone()
        self.assertTrue(normal_practice["practice_number"].startswith("CR-"))
        self.assertEqual(normal_practice["billing_status"], "")

        # The COL- counter is independent from CR-: creating a normal practice in between
        # must not consume a collaborator number.
        redirects.clear()
        self.handler.form = lambda: {"operator_name": "ALESSIO", "service_type": "Cremazione singola", "request_origin": "Collaboratore", "destination_branch": "Livorno", "collaborator_name": "HUMANITAS CROCE VERDE", "confirm_new_client": "SI"}
        self.handler.create_practice(admin)
        second_collab_pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            second_collab = conn.execute("SELECT practice_number FROM practices WHERE id=?", (second_collab_pid,)).fetchone()
        first_num = int(collab_practice["practice_number"].split("-")[1])
        second_num = int(second_collab["practice_number"].split("-")[1])
        self.assertEqual(second_num, first_num + 1)

    def test_collaborator_detail_groups_by_month_and_marks_month_billing_status(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            collab_id = conn.execute("SELECT id FROM collaborators WHERE UPPER(name)='HUMANITAS CROCE VERDE'").fetchone()["id"]
            p1 = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,animal_name,price_cremation,total_service,payment_status,
                   collaborator_id,collaborator_name,billing_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("COL-100001", "Collaboratore", "Livorno", "Ritirato", "2026-06-05", stamp, stamp, admin["id"], "Rex",
                 "100", "100", "Da saldare", collab_id, "HUMANITAS CROCE VERDE", "Da fatturare"),
            ).lastrowid
            p2 = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,animal_name,price_cremation,total_service,payment_status,
                   collaborator_id,collaborator_name,billing_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("COL-100002", "Collaboratore", "Livorno", "Ritirato", "2026-06-20", stamp, stamp, admin["id"], "Otto",
                 "150", "150", "Da saldare", collab_id, "HUMANITAS CROCE VERDE", "Da fatturare"),
            ).lastrowid
        rendered = []; self.handler.send_html = lambda content: rendered.append(content)
        self.handler.collaborator_detail(admin, collab_id)
        page = rendered[-1]
        self.assertIn("Giugno 2026", page)
        self.assertIn("Rex", page)
        self.assertIn("Otto", page)
        self.assertIn("€ 250,00", page)
        self.assertIn("Segna mese come fatturato (2)", page)

        redirects = []; self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {"mese": "2026-06"}
        self.handler.collaborator_mark_month(admin, collab_id, "fatturato")
        self.assertEqual(redirects[-1], f"/collaboratori/{collab_id}")
        with app.db() as conn:
            row1 = conn.execute("SELECT billing_status,billing_invoiced_at FROM practices WHERE id=?", (p1,)).fetchone()
            row2 = conn.execute("SELECT billing_status FROM practices WHERE id=?", (p2,)).fetchone()
        self.assertEqual(row1["billing_status"], "Fatturato")
        self.assertIsNotNone(row1["billing_invoiced_at"])
        self.assertNotEqual(row1["billing_invoiced_at"], "")
        self.assertEqual(row2["billing_status"], "Fatturato")

        rendered.clear()
        self.handler.collaborator_detail(admin, collab_id)
        page = rendered[-1]
        self.assertIn("Segna mese come incassato (2)", page)
        self.assertNotIn("Segna mese come fatturato", page)

        self.handler.form = lambda: {"mese": "2026-06"}
        self.handler.collaborator_mark_month(admin, collab_id, "incassato")
        with app.db() as conn:
            row1 = conn.execute("SELECT billing_status FROM practices WHERE id=?", (p1,)).fetchone()
            row2 = conn.execute("SELECT billing_status FROM practices WHERE id=?", (p2,)).fetchone()
        self.assertEqual(row1["billing_status"], "Incassato")
        self.assertEqual(row2["billing_status"], "Incassato")
        rendered.clear()
        self.handler.collaborator_detail(admin, collab_id)
        page = rendered[-1]
        self.assertIn("Mese completamente incassato.", page)

    def test_disposal_page_groups_by_branch_and_channel_and_excludes_ineligible(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            def collettiva(number, branch, total_text, pickup, status="Ritirato"):
                return conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                       created_at,updated_at,created_by,service_type,total_text)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (number, "Privato", branch, status, pickup, stamp, stamp, admin["id"], "Cremazione collettiva", total_text),
                ).lastrowid
            collettiva("PP-DISP-LI-W1", "Livorno", "", "2026-07-15")
            collettiva("PP-DISP-LI-W2", "Livorno", "", "2026-07-16")
            collettiva("PP-DISP-LI-D1", "Livorno", "300", "2026-07-17")
            collettiva("PP-DISP-EM-W1", "Empoli", "", "2026-07-18")
            collettiva("PP-DISP-ALREADY", "Livorno", "", "2026-07-16", status="Smaltito")
            collettiva("PP-DISP-OUTSIDE", "Livorno", "", "2026-06-01")
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                            created_at,updated_at,created_by,service_type,animal_name)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         ("PP-DISP-SINGOLA", "Privato", "Livorno", "Ritirato", "2026-07-15", stamp, stamp, admin["id"], "Cremazione singola", "Fido"))
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/smaltimenti?dal=2026-07-01&al=2026-07-31"
        self.handler.disposal_page(admin)
        page = rendered[-1]
        self.assertIn("PP-DISP-LI-W1", page)
        self.assertIn("PP-DISP-LI-W2", page)
        self.assertIn("PP-DISP-LI-D1", page)
        self.assertIn("PP-DISP-EM-W1", page)
        self.assertIn("PP-DISP-ALREADY", page)
        self.assertNotIn("PP-DISP-OUTSIDE", page)
        self.assertNotIn("PP-DISP-SINGOLA", page)
        self.assertIn("Livorno · Circuito W", page)
        self.assertIn("Livorno · Circuito D", page)
        self.assertIn("Empoli · Circuito W", page)
        self.assertIn("Conferma scarico", page)
        self.assertIn("cambierà lo stato di 4 pratiche in Smaltito", page)
        self.assertIn("Da confermare", page)
        self.assertIn("Già smaltita", page)
        self.assertIn("4 da confermare · 1 già smaltite", page)

    def test_disposal_confirm_updates_statuses_records_history_and_excludes_from_future_periods(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            w_id = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,service_type) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("PP-CONF-W", "Privato", "Livorno", "Ritirato", "2026-07-15", stamp, stamp, admin["id"], "Cremazione collettiva"),
            ).lastrowid
            d_id = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,service_type,total_text) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("PP-CONF-D", "Privato", "Empoli", "Ritirato", "2026-07-16", stamp, stamp, admin["id"], "Cremazione collettiva", "250"),
            ).lastrowid
        redirects = []; self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {"dal": "2026-07-01", "al": "2026-07-31"}
        self.handler.disposal_confirm(admin)
        self.assertEqual(len(redirects), 1)
        self.assertTrue(redirects[-1].startswith("/smaltimenti/storico/"))
        batch_id = int(redirects[-1].rsplit("/", 1)[-1])
        with app.db() as conn:
            for pid in (w_id, d_id):
                row = conn.execute("SELECT status FROM practices WHERE id=?", (pid,)).fetchone()
                self.assertEqual(row["status"], "Smaltito")
            batch = conn.execute("SELECT * FROM disposal_batches WHERE id=?", (batch_id,)).fetchone()
            self.assertEqual(batch["total_count"], 2)
            self.assertEqual(batch["period_from"], "2026-07-01")
            self.assertEqual(batch["period_to"], "2026-07-31")
            breakdown = app.json.loads(batch["breakdown_json"])
            self.assertEqual(breakdown, {"Livorno|W": 1, "Empoli|D": 1})
            linked = {row["practice_id"] for row in conn.execute("SELECT practice_id FROM disposal_batch_practices WHERE batch_id=?", (batch_id,))}
            self.assertEqual(linked, {w_id, d_id})
            history_events = conn.execute("SELECT event_type,new_value FROM practice_history WHERE practice_id=?", (w_id,)).fetchall()
            self.assertTrue(any(h["event_type"] == "Smaltimento" and h["new_value"] == "Smaltito" for h in history_events))
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/smaltimenti?dal=2026-08-01&al=2026-08-31"
        self.handler.disposal_page(admin)
        page = rendered[-1]
        self.assertNotIn("PP-CONF-W", page)
        self.assertNotIn("PP-CONF-D", page)
        self.assertIn("Nessuna pratica di cremazione collettiva da smaltire", page)
        rendered_same = []; self.handler.send_html = lambda content, *a: rendered_same.append(content)
        self.handler.path = "/smaltimenti?dal=2026-07-01&al=2026-07-31"
        self.handler.disposal_page(admin)
        same_period_page = rendered_same[-1]
        self.assertIn("PP-CONF-W", same_period_page)
        self.assertIn("PP-CONF-D", same_period_page)
        self.assertIn("Già smaltita", same_period_page)
        self.assertNotIn("Conferma scarico", same_period_page)
        self.assertIn("Nessuna pratica da confermare nel periodo selezionato", same_period_page)

    def test_disposal_page_shows_animal_weight_and_group_kg_totals(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            for number, weight in (("PP-KG-1", "10"), ("PP-KG-2", "5,5")):
                conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                       created_at,updated_at,created_by,service_type,estimated_weight)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (number, "Privato", "Livorno", "Ritirato", "2026-07-15", stamp, stamp, admin["id"], "Cremazione collettiva", weight),
                )
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/smaltimenti?dal=2026-07-01&al=2026-07-31"
        self.handler.disposal_page(admin)
        page = rendered[-1]
        self.assertIn("10 kg", page)
        self.assertIn("5,5 kg", page)
        self.assertIn("15,5 kg", page)
        self.assertIn("<th>Peso</th>", page)

    def test_disposal_page_shows_species_in_animal_column_not_da_inserire(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,service_type,species)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("PP-SPECIE", "Privato", "Livorno", "Ritirato", "2026-07-15", stamp, stamp, admin["id"], "Cremazione collettiva", "Cane"),
            )
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/smaltimenti?dal=2026-07-01&al=2026-07-31"
        self.handler.disposal_page(admin)
        page = rendered[-1]
        self.assertIn("<td>Cane</td>", page)
        self.assertNotIn("Da inserire", page)

    def test_disposal_page_filters_by_practice_status(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,service_type) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("PP-STATOF-PENDING", "Privato", "Livorno", "Ritirato", "2026-07-15", stamp, stamp, admin["id"], "Cremazione collettiva"),
            )
            conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,service_type) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("PP-STATOF-DONE", "Privato", "Livorno", "Smaltito", "2026-07-16", stamp, stamp, admin["id"], "Cremazione collettiva"),
            )
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/smaltimenti?dal=2026-07-01&al=2026-07-31&stato=da_confermare"
        self.handler.disposal_page(admin)
        pending_only_page = rendered[-1]
        self.assertIn("PP-STATOF-PENDING", pending_only_page)
        self.assertNotIn("PP-STATOF-DONE", pending_only_page)
        self.assertIn("1 da confermare · 1 già smaltite", pending_only_page)
        rendered_done = []; self.handler.send_html = lambda content, *a: rendered_done.append(content)
        self.handler.path = "/smaltimenti?dal=2026-07-01&al=2026-07-31&stato=smaltito"
        self.handler.disposal_page(admin)
        done_only_page = rendered_done[-1]
        self.assertNotIn("PP-STATOF-PENDING", done_only_page)
        self.assertIn("PP-STATOF-DONE", done_only_page)
        self.assertIn("1 da confermare · 1 già smaltite", done_only_page)

    def test_disposal_confirm_rejects_empty_period_without_creating_batch(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/smaltimenti"
        self.handler.form = lambda: {"dal": "2026-09-01", "al": "2026-09-30"}
        self.handler.disposal_confirm(admin)
        self.assertIn("Nessuna pratica", rendered[-1])
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM disposal_batches").fetchone()["n"], 0)

    def test_disposal_batch_detail_shows_frozen_breakdown_and_linked_practices(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,service_type) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("PP-BATCH", "Privato", "Livorno", "Ritirato", "2026-07-15", stamp, stamp, admin["id"], "Cremazione collettiva"),
            )
        redirects = []; self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {"dal": "2026-07-01", "al": "2026-07-31"}
        self.handler.disposal_confirm(admin)
        batch_id = int(redirects[-1].rsplit("/", 1)[-1])
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.disposal_batch_detail(admin, batch_id)
        page = rendered[-1]
        self.assertIn("PP-BATCH", page)
        self.assertIn("Livorno · Circuito W", page)
        self.assertIn("<b>1</b>", page)

    def test_disposal_rows_are_clickable_and_tables_scroll_horizontally_on_mobile(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,
                   created_at,updated_at,created_by,service_type) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("PP-ROWCLICK", "Privato", "Livorno", "Ritirato", "2026-07-15", stamp, stamp, admin["id"], "Cremazione collettiva"),
            ).lastrowid

        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/smaltimenti?dal=2026-07-01&al=2026-07-31"
        self.handler.disposal_page(admin)
        page = rendered[-1]
        url = f"/pratiche/{pid}"
        self.assertIn(f'''<tr class="practice-row-link" tabindex="0" role="link" aria-label="Apri pratica PP-ROWCLICK" onclick="practiceRowSelect(this,event,'{url}')"''', page)
        # The disposal group must be a scrollable .tablebox (like every other list in the app),
        # not a plain .section, so mobile users can swipe right to see the trailing columns.
        self.assertIn('<section class="tablebox disposal-group">', page)
        self.assertNotIn('<section class="section disposal-group">', page)

        redirects = []; self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {"dal": "2026-07-01", "al": "2026-07-31"}
        self.handler.disposal_confirm(admin)
        batch_id = int(redirects[-1].rsplit("/", 1)[-1])
        rendered.clear()
        self.handler.disposal_batch_detail(admin, batch_id)
        detail_page = rendered[-1]
        self.assertIn(f'''<tr class="practice-row-link" tabindex="0" role="link" aria-label="Apri pratica PP-ROWCLICK" onclick="practiceRowSelect(this,event,'{url}')"''', detail_page)


    def test_cremation_schedule_lists_ritirato_single_cremations_sorted_with_counter_urn_and_filter(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            urn_id = conn.execute(
                "INSERT INTO urns(name,price,quantity,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("Cornice Bianca", "80", 5, 1, stamp, stamp),
            ).lastrowid

            def practice(code, status, service_type, pickup, provenance="", weight="", urn=None,
                         send_catalog="", tag_avvisare="", urn_notes="", owner_first="", owner_last=""):
                return conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                       pickup_date,created_at,updated_at,created_by,animal_name,estimated_weight,provenance,
                       urn_id,send_catalog,tag_avvisare,urn_notes,owner_first_name,owner_last_name)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, "Privato", "Livorno", status, service_type, pickup, stamp, stamp, admin["id"],
                     code, weight, provenance, urn, send_catalog, tag_avvisare, urn_notes, owner_first, owner_last),
                ).lastrowid

            newer_id = practice("CR-CREM-NEW", "Ritirato", "Cremazione singola", "2026-07-16", provenance="E",
                                 owner_first="Anna", owner_last="Verdi")
            older_id = practice("CR-CREM-OLD", "Ritirato", "Cremazione singola", "2026-07-14", provenance="L",
                                 weight="8", urn=urn_id)
            catalog_id = practice("CR-CREM-CAT", "Ritirato", "Cremazione singola", "2026-07-15",
                                   send_catalog="Si", tag_avvisare="Si")
            freetext_id = practice("CR-CREM-FREETEXT", "Ritirato", "Cremazione singola", "2026-07-17",
                                    urn_notes="Urna scelta a voce, non ancora in catalogo")
            practice("CR-CREM-COLLETTIVA", "Ritirato", "Cremazione collettiva", "2026-07-10")
            practice("CR-CREM-DONE", "Cremato", "Cremazione singola", "2026-07-10")

        rendered = []
        self.handler.path = "/programma-cremazioni"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]
        self.assertNotIn("CR-CREM-COLLETTIVA", page)
        self.assertNotIn("CR-CREM-DONE", page)
        self.assertIn("CR-CREM-NEW", page)
        self.assertIn("CR-CREM-OLD", page)
        self.assertIn("CR-CREM-CAT", page)
        self.assertIn("CR-CREM-FREETEXT", page)
        self.assertLess(page.index("CR-CREM-OLD"), page.index("CR-CREM-CAT"))
        self.assertLess(page.index("CR-CREM-CAT"), page.index("CR-CREM-NEW"))
        self.assertLess(page.index("CR-CREM-NEW"), page.index("CR-CREM-FREETEXT"))
        self.assertIn("<strong>4</strong>", page)  # counter: 4 practices match both criteria
        self.assertIn("Cornice Bianca", page)
        self.assertIn("INVIARE CATALOGO", page)
        self.assertIn("AVVISARE", page)
        self.assertIn("L · Livorno", page)
        self.assertIn("E · Empoli", page)
        self.assertIn("Anna Verdi", page)
        self.assertIn("Urna scelta a voce, non ancora in catalogo", page)  # unmatched free-typed urn text still shown
        self.assertIn(f'/pratiche/{older_id}', page)
        self.assertIn(f'/pratiche/{newer_id}', page)
        self.assertIn(f'/pratiche/{catalog_id}', page)
        self.assertIn(f'/pratiche/{freetext_id}', page)
        # Data recupero -> Pagamento -> Inserito must be the trailing column order.
        header = page[page.index("<thead>"):page.index("</thead>")]
        self.assertLess(header.index(">Urna<"), header.index(">Cliente<"))
        self.assertLess(header.index(">Cliente<"), header.index(">Data recupero<"))
        self.assertLess(header.index(">Data recupero<"), header.index(">Pagamento<"))
        self.assertLess(header.index(">Pagamento<"), header.index(">Inserito<"))
        self.assertIn(f'data-cremation-id="{older_id}"', page)
        self.assertIn("onchange=\"toggleCremationQueue(this)\"", page)
        self.assertIn("async function toggleCremationQueue(input)", app.APP_JS)
        self.assertIn("Nessun animale spuntato come INSERITO da completare.", page)
        self.assertNotIn('practice-row-link cremation-row-done', page)  # nothing queued yet
        self.assertIn("Da saldare", page)  # default payment badge for practices with no payment set

        rendered.clear()
        self.handler.path = "/programma-cremazioni?provenienza=L"
        self.handler.cremation_schedule(admin)
        filtered = rendered[-1]
        self.assertIn("CR-CREM-OLD", filtered)
        self.assertNotIn("CR-CREM-NEW", filtered)
        self.assertNotIn("CR-CREM-CAT", filtered)

    def test_cremation_toggle_queue_persists_checkbox_without_changing_status(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                   pickup_date,created_at,updated_at,created_by,animal_name) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("CR-QUEUE", "Privato", "Livorno", "Ritirato", "Cremazione singola", "2026-07-15", stamp, stamp,
                 admin["id"], "CR-QUEUE"),
            ).lastrowid

        responses = []
        self.handler.send_json = lambda payload, status=200: responses.append((payload, status))
        self.handler.form = lambda: {"queued": "1"}
        self.handler.cremation_toggle_queue(admin, pid)
        self.assertEqual(responses[-1], ({"ok": True, "queued": True}, 200))
        with app.db() as conn:
            row = conn.execute("SELECT status,cremation_queued FROM practices WHERE id=?", (pid,)).fetchone()
            self.assertEqual((row["status"], row["cremation_queued"]), ("Ritirato", "Si"))

        # Still checked and still in the list on a fresh page load (survives navigation without an explicit finalize).
        rendered = []
        self.handler.path = "/programma-cremazioni"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]
        self.assertIn("CR-QUEUE", page)
        self.assertIn(f'data-cremation-id="{pid}" checked', page)
        self.assertIn('practice-row-link cremation-row-done', page)
        self.assertIn("1 animali spuntati INSERITO passeranno allo stato In programma", page)

        # Unchecking clears the persisted flag.
        responses.clear()
        self.handler.form = lambda: {"queued": "0"}
        self.handler.cremation_toggle_queue(admin, pid)
        self.assertEqual(responses[-1], ({"ok": True, "queued": False}, 200))
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT cremation_queued FROM practices WHERE id=?", (pid,)).fetchone()["cremation_queued"], "")

    def test_complete_cremation_session_moves_only_queued_animals_to_in_programma(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()

            def practice(code, queued=""):
                return conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                       pickup_date,created_at,updated_at,created_by,animal_name,cremation_queued) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, "Privato", "Livorno", "Ritirato", "Cremazione singola", "2026-07-15", stamp, stamp,
                     admin["id"], code, queued),
                ).lastrowid

            queued_id = practice("CR-INSERITO", queued="Si")
            unqueued_id = practice("CR-NON-INSERITO")

        self.handler.path = "/programma-cremazioni"
        self.handler.form = lambda: {"return_to": "/programma-cremazioni"}
        redirected = []
        self.handler.redirect = lambda url: redirected.append(url)
        self.handler.complete_cremation_session(admin)
        self.assertEqual(redirected[-1], "/programma-cremazioni")
        with app.db() as conn:
            queued_row = conn.execute("SELECT status,cremation_registered,cremation_queued FROM practices WHERE id=?", (queued_id,)).fetchone()
            unqueued_row = conn.execute("SELECT status,cremation_registered,cremation_queued FROM practices WHERE id=?", (unqueued_id,)).fetchone()
            self.assertEqual((queued_row["status"], queued_row["cremation_registered"], queued_row["cremation_queued"]), ("In programma", "Si", ""))
            self.assertEqual((unqueued_row["status"], unqueued_row["cremation_registered"]), ("Ritirato", None))
            history = conn.execute(
                "SELECT event_type,old_value,new_value FROM practice_history WHERE practice_id=?", (queued_id,)
            ).fetchone()
            self.assertEqual((history["event_type"], history["old_value"], history["new_value"]), ("Cambio stato rapido", "Ritirato", "In programma"))

        # The finalized practice moved out of Ritirato, so it must disappear from the list on reload.
        rendered = []
        self.handler.path = "/programma-cremazioni"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        self.assertNotIn("CR-INSERITO", rendered[-1])
        self.assertIn("CR-NON-INSERITO", rendered[-1])

    def test_cremation_schedule_shows_payment_status_amount_and_channel(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                   pickup_date,created_at,updated_at,created_by,animal_name,payment_status,price_cremation)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-PAID-W", "Privato", "Livorno", "Ritirato", "Cremazione singola", "2026-07-15", stamp, stamp,
                 admin["id"], "CR-PAID-W", "Pagato", "150"),
            )
            conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                   pickup_date,created_at,updated_at,created_by,animal_name,payment_status,total_text)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-DUE-D", "Privato", "Livorno", "Ritirato", "Cremazione singola", "2026-07-16", stamp, stamp,
                 admin["id"], "CR-DUE-D", "Da saldare", "200"),
            )
        rendered = []
        self.handler.path = "/programma-cremazioni"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]
        self.assertIn("€ 150,00", page)
        self.assertIn("€ 200,00", page)
        self.assertIn('badge pay-green">Pagato</span> € 150,00 · W', page)
        self.assertIn('badge pay-yellow">Da saldare</span> € 200,00 · D', page)

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

    def test_sidebar_menu_follows_requested_order(self):
        rendered = []
        self.handler.send_html = lambda html, *args: rendered.append(html)
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.path = "/"
        self.handler.dashboard(admin)
        page = rendered[-1]
        expected_order = [
            "Dashboard", "Calendario", "Bilanci", "Notifiche", "Archivio", "Catalogo Urne",
            "Conversazioni WhatsApp", "Veterinari", "Prodotti", "Ordini", "Gestionale", "Clienti",
        ]
        positions = [page.index(f">{label}</span>") for label in expected_order]
        self.assertEqual(positions, sorted(positions))
        for label in ("Animali", "Pagamenti", "Fatture", "Impostazioni", "Assistenza"):
            self.assertGreater(page.index(f">{label}</span>"), positions[-1])

    def test_desktop_sidebar_is_narrower_and_wrap_has_a_readable_max_width(self):
        self.assertIn(".top{width:212px", app.CSS)
        self.assertIn(".app-header{position:fixed;left:212px", app.CSS)
        self.assertIn(".wrap{max-width:1600px;margin-left:212px;margin-right:auto", app.CSS)
        # Mobile/tablet breakpoints stay untouched (sidebar collapses independently there).
        self.assertIn("@media(max-width:900px)", app.CSS)
        self.assertIn(".wrap{margin-left:0;padding:calc(88px + var(--safe-top)) 14px 22px}", app.CSS)

    def test_shared_lookup_panel_controller_is_defined_and_used_everywhere(self):
        js = app.APP_JS
        self.assertIn("function ppmCloseLookupPanel(panel)", js)
        self.assertIn("function ppmRegisterLookupPanel(input,panel)", js)
        self.assertIn("function ppmBindLookupEmptyClose(input,panel,fetcher)", js)
        self.assertIn("function ppmLookupFetcher()", js)
        self.assertIn("ppmLookupPanels.forEach(entry=>{", js)
        # Every lookup input registers itself with the shared outside-click/close controller.
        self.assertGreaterEqual(js.count("ppmRegisterLookupPanel(input,results)"), 3)
        self.assertIn("ppmRegisterLookupPanel(vet,vetResults)", js)
        self.assertIn("ppmRegisterLookupPanel(deliveryAnimal,deliveryResults)", js)
        self.assertIn("ppmRegisterLookupPanel(input,panel)", js)
        # Async lookups guard against stale/late responses via the shared token+abort fetcher.
        self.assertGreaterEqual(js.count("fetcher.stale(token)"), 6)
        # The old ad-hoc per-function sequence counter was removed, not duplicated further.
        self.assertNotIn("calendarDeliveryAnimalLookupSequence", js)

    def test_lookup_panels_reopen_after_being_closed_once(self):
        # Regression test: ppmCloseLookupPanel used to set the native `hidden`
        # attribute, but every "show" path only ever cleared the CSS class, so a
        # panel closed once (outside click, empty input, selection) could never
        # be shown again even though classList said it wasn't hidden.
        js = app.APP_JS
        self.assertIn("function ppmOpenLookupPanel(panel)", js)
        self.assertIn("panel.hidden=false", js)
        # No lookup "show" path should bypass the shared opener by touching
        # classList directly (that was exactly the source of the bug).
        self.assertNotIn("results.classList.remove('hidden')", js)
        self.assertGreaterEqual(js.count("ppmOpenLookupPanel(results)"), 6)
        # The zone field mixes the native attribute (its own show/hide logic)
        # with the shared close/open helpers, so both must stay in sync too.
        self.assertIn("function calendarZoneInput(input){", js)
        self.assertNotIn("results.hidden=!input.value.trim()||!matches.length", js)

    def test_calendar_time_blur_dispatches_change_for_end_time_sync(self):
        # Regression test: calendarTimeBlur reformats the typed digits but used
        # to never fire a change event, so calendarInitDateTimeSync's `change`
        # listener on start_time could run before (or never see) the final
        # formatted value, breaking "end follows start" for typed times.
        js = app.APP_JS
        self.assertIn("function calendarTimeBlur(input){", js)
        blur_start = js.index("function calendarTimeBlur(input){")
        blur_end = js.index("\n", blur_start)
        blur_body = js[blur_start:blur_end]
        self.assertIn("input.dispatchEvent(new Event('change',{bubbles:true}))", blur_body)

    def test_day_view_swipe_navigation_removed(self):
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.path = "/calendario?vista=giorno"
        self.handler.calendar_page(admin)
        page = rendered[-1]
        self.assertNotIn('class="calendar-day-timeline" data-calendar-swipe', page)
        self.assertNotIn('class="calendar-day-list" data-calendar-swipe', page)

    def test_header_search_input_uses_16px_font_to_avoid_ios_zoom(self):
        self.assertIn('.app-header .header-search input{min-width:0;height:40px;min-height:40px;font-size:16px}', app.CSS)

    def test_header_search_has_live_suggestions(self):
        rendered = []
        self.handler.send_html = lambda html, *args: rendered.append(html)
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.path = "/"
        self.handler.dashboard(admin)
        page = rendered[-1]
        self.assertIn('id="globalSearchResults"', page)
        self.assertIn("ppmRegisterLookupPanel(globalSearch,globalSearchResults)", app.APP_JS)
        self.assertIn("/api/calendario/pratiche/search", app.APP_JS)

    def test_invoice_total_formats_two_decimals_without_euro_sign_in_value(self):
        js = app.APP_JS
        self.assertIn("function ppmFormatInvoiceTotal(value){", js)
        self.assertIn("return number.toFixed(2).replace('.', ',');", js)
        self.assertNotIn("`${number.toFixed(2).replace('.', ',')} €`", js)
        self.assertIn("invoiceTotal.value=ppmFormatInvoiceTotal(seedTotal)", js)
        self.assertIn("invoiceTotal.addEventListener('blur'", js)

    def test_invoice_total_accepts_plain_number_with_euro_sign_or_comma(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",("CR-INV","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","150")).lastrowid
        for raw_value in ("150,00 €", "150.00", "150,00", "150"):
            redirects = []; self.handler.redirect = lambda path: redirects.append(path)
            self.handler.form = lambda value=raw_value: {"invoice_number": "FT-1", "invoice_date": "2026-07-14", "invoice_total": value}
            self.handler.save_invoice(admin, pid)
            self.assertTrue(redirects, f"il valore {raw_value!r} avrebbe dovuto essere accettato")
            with app.db() as conn:
                saved = conn.execute("SELECT invoice_total FROM practices WHERE id=?", (pid,)).fetchone()["invoice_total"]
            self.assertEqual(app.money_value(saved), 150.0)

    def test_invoice_total_invalid_text_shows_inline_error_on_practice_page(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",("CR-INV2","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","150")).lastrowid
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.form = lambda: {"invoice_number": "FT-2", "invoice_date": "2026-07-14", "invoice_total": "abc"}
        self.handler.save_invoice(admin, pid)
        self.assertIn("Totale fattura non valido", rendered[-1])
        self.assertIn("CR-INV2", rendered[-1])
        with app.db() as conn:
            unchanged = conn.execute("SELECT invoice_number FROM practices WHERE id=?", (pid,)).fetchone()["invoice_number"]
        self.assertIsNone(unchanged)

    def test_dashboard_quick_action_buttons_share_equal_width(self):
        self.assertIn(".calendar-quick-actions .btn{flex:1}", app.CSS)

    def test_table_top_scrollbar_stays_sticky_while_scrolling(self):
        self.assertIn(".tablebox-scroll-top{overflow-x:auto;overflow-y:hidden;height:16px;margin-bottom:6px;position:sticky;top:76px;z-index:10;background:var(--paper)}", app.CSS)

    def test_table_header_row_is_sticky_app_wide(self):
        self.assertIn("thead th{position:sticky;top:0;z-index:2;background:#101620}", app.CSS)
        self.assertIn(".light-theme thead th{background:#fff}", app.CSS)
        self.assertNotIn("position:static;top:auto", app.CSS)

    def test_wide_scrollable_tables_use_bounded_internal_scroll_for_reliable_sticky(self):
        # position:sticky on <th> inside a table wrapped by an overflow-x
        # scroll container renders with a permanent top offset in Chromium
        # when the sticky offset is relative to the page (the header cell
        # overlaps the first data row instead of tracking scroll). The fix
        # is to give every .tablebox a bounded height with its own real
        # scroll container (overflow:auto), so thead sticky top:0 is always
        # relative to that container and never breaks, on any table.
        self.assertIn(".tablebox{background:white;border:1px solid var(--line);border-radius:15px;max-height:min(65vh,620px);overflow:auto}", app.CSS)

    def test_archive_wide_table_keeps_horizontal_scroll_wrapper(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name,pickup_date)
                            VALUES(?,?,?,?,?,?,?,?,?)""",("CR-ARCHIVESCROLL","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","2026-07-20"))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/archivio/pratiche"
        self.handler.archive(admin)
        page=rendered[-1]
        self.assertIn('<div class="tablebox dashboard-table-scroll"><table class="practice-list-table">',page)

    def test_list_scroll_and_filter_state_restore_is_wired_for_all_target_pages(self):
        for path in ("/archivio/pratiche", "/calendario", "/clienti", "/veterinari", "/catalogo-urne", "/ordini/storico"):
            self.assertIn(f"'{path}':", app.APP_JS)
        self.assertIn("extraInputs:['urnCatalogSearch']", app.APP_JS)
        self.assertIn("function setupListStateRestore(){", app.APP_JS)
        self.assertIn("document.addEventListener('DOMContentLoaded', setupListStateRestore);", app.APP_JS)
        self.assertIn("sessionStorage.setItem(key,JSON.stringify(state));", app.APP_JS)
        self.assertIn("location.replace(location.pathname+state.search);", app.APP_JS)

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
                """INSERT INTO urns(name,material,internal_code,price,quantity,low_stock_threshold,image_path,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                ("Urna prova", "Legno", "URN-TEST", "85.00", 2, 3, "/assets/urns/urna-prova.jpg", stamp, stamp),
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
        # Autocomplete suggestions must carry the urn's image so it shows next to each match.
        self.assertIn('data-image="/assets/urns/urna-prova.jpg"', html)
        self.assertIn("lookup-item-thumb", app.APP_JS)
        self.assertIn("option.dataset.image", app.APP_JS)

    def test_trash_and_restore_release_both_urn_slots(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            u1 = conn.execute(
                """INSERT INTO urns(name,material,internal_code,price,quantity,low_stock_threshold,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("Urna prima", "Legno", "URN-TRASH-1", "80.00", 3, 3, stamp, stamp),
            ).lastrowid
            u2 = conn.execute(
                """INSERT INTO urns(name,material,internal_code,price,quantity,low_stock_threshold,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("Urna seconda", "Legno", "URN-TRASH-2", "60.00", 2, 3, stamp, stamp),
            ).lastrowid
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,urn_id,urn_id_2)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                ("CR-TRASH1", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], u1, u2),
            ).lastrowid
            self.handler.adjust_urn_stock(conn, u1, -1, "Utilizzata nella pratica", pid, admin["id"])
            self.handler.adjust_urn_stock(conn, u2, -1, "Utilizzata nella pratica", pid, admin["id"])
        self.handler.redirect = lambda path: None
        self.handler.delete_practice(admin, pid)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u1,)).fetchone()["quantity"], 3)
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u2,)).fetchone()["quantity"], 2)
        self.handler.restore_practice(admin, pid)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u1,)).fetchone()["quantity"], 2)
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u2,)).fetchone()["quantity"], 1)

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


    def test_practice_changes_never_reconcile_or_rewrite_payment_movements(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,price_cremation,total_service,total_text,deposit,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-MOVIMENTI","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"410","410","330","100","Acconto")).lastrowid
            self.handler.add_payment_movement(conn,pid,"acconto","D",100,admin["id"],"Acconto iniziale","2026-07-10",payment_method="Contanti",movement_category="D")
            before=[tuple(row) for row in conn.execute("SELECT id,amount,paid_at,payment_method,movement_category FROM payment_movements WHERE practice_id=?",(pid,))]
            conn.execute("UPDATE practices SET deposit='999',payment_status='Pagato' WHERE id=?",(pid,))
            conn.execute("UPDATE practices SET deposit='100',payment_status='Acconto' WHERE id=?",(pid,))
            after=[tuple(row) for row in conn.execute("SELECT id,amount,paid_at,payment_method,movement_category FROM payment_movements WHERE practice_id=?",(pid,))]
            self.assertEqual(after,before)

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

    def test_practice_summary_shows_every_multiple_urn_cast_and_accessory_item(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,service_type,urn_notes,price_urn,urn_notes_2,price_urn_2,
                                price_cast,price_cast_2,price_paw_cast,price_paw_cast_2,price_paw_cast_3,price_paw_cast_4,
                                price_nose_cast,price_nose_cast_2,price_nose_cast_3,price_nose_cast_4,
                                price_accessories,price_accessories_2,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-MULTI","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Rex","Cremazione singola",
                              "Urna base","80","Urna scorta","90",
                              "50","55","20","21","22","23",
                              "30","31","32","33",
                              "10","11","Da saldare")).lastrowid
        rendered=[]; self.handler.send_html=lambda content,*args: rendered.append(content)
        self.handler.practice(admin,pid)
        page=rendered[-1]
        self.assertIn("Urna base",page)
        self.assertIn("Urna scorta",page)
        for label,amount in (("Calco","€ 50,00"),("Secondo calco","€ 55,00"),
                              ("Calco polpastrello","€ 20,00"),("Secondo calco polpastrello","€ 21,00"),
                              ("Calco naso","€ 30,00"),("Secondo calco naso","€ 31,00"),
                              ("Accessori","€ 10,00"),("Secondi accessori","€ 11,00")):
            self.assertIn(f'<small>{label}</small><b>{amount}</b>',page)
        paw_alt_count=page.count('<small>Altro calco polpastrello</small>')
        nose_alt_count=page.count('<small>Altro calco naso</small>')
        self.assertEqual(paw_alt_count,2)
        self.assertEqual(nose_alt_count,2)
        self.assertIn("€ 22,00",page)
        self.assertIn("€ 23,00",page)
        self.assertIn("€ 32,00",page)
        self.assertIn("€ 33,00",page)

    def test_practice_summary_shows_delivery_location_next_to_riconsegna(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp=app.now()
            pid_home=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,service_type,price_delivery,delivery_at_home,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-DELIVHOME","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Rex","Cremazione singola","40","Si","Da saldare")).lastrowid
            pid_clinic=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,service_type,price_delivery,delivery_at_clinic,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-DELIVCLINIC","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Fido","Cremazione singola","40","Si","Da saldare")).lastrowid
            pid_neither=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,service_type,price_delivery,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-DELIVNONE","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cremazione singola","40","Da saldare")).lastrowid
        rendered=[]; self.handler.send_html=lambda content,*args: rendered.append(content)
        self.handler.practice(admin,pid_home)
        self.assertIn('<small>Riconsegna</small><b>€ 40,00</b><br><small class="sub">A CASA</small>',rendered[-1])
        self.handler.practice(admin,pid_clinic)
        self.assertIn('<small>Riconsegna</small><b>€ 40,00</b><br><small class="sub">IN AMBULATORIO</small>',rendered[-1])
        self.handler.practice(admin,pid_neither)
        self.assertIn('<small>Riconsegna</small><b>€ 40,00</b></div>',rendered[-1])

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
        # Regression test: a single click on a practice row only selects it (colored
        # outline) the first time; a plain second click on an already-selected row (not
        # necessarily a fast double-click) opens it, and a real double-click still works
        # too. Explicit inner links (code, "Apri" button) must keep working on one click.
        self.assertNotIn("onclick=\"window.location.href=", page)
        self.assertIn("onclick=\"practiceRowSelect(this,event,'", page)
        self.assertIn("ondblclick=\"practiceRowOpen(", page)
        self.assertIn("function practiceRowSelect(row,event,url)", app.APP_JS)
        self.assertIn("if(row.classList.contains('row-selected')){practiceRowOpen(url);return;}", app.APP_JS)
        self.assertIn("function practiceRowOpen(url)", app.APP_JS)
        self.assertIn(".row-selected", app.CSS)

    def test_archive_and_dashboard_show_elimina_button_on_every_practice_row(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            for number,date in (("CR-OLD001","2020-01-15"),("CR-NEW001","2026-07-20")):
                stamp=f"{date}T10:00:00"
                conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,pickup_date)
                                VALUES(?,?,?,?,?,?,?,?,?)""",
                             (number,"Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna",date))
            old_pid=conn.execute("SELECT id FROM practices WHERE practice_number='CR-OLD001'").fetchone()["id"]
            new_pid=conn.execute("SELECT id FROM practices WHERE practice_number='CR-NEW001'").fetchone()["id"]
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/archivio/pratiche"
        self.handler.archive(admin)
        archive_page=rendered[-1]
        self.assertIn("<th>Azione</th>",archive_page)
        for pid in (old_pid,new_pid):
            self.assertIn(
                f'''<form onclick="event.stopPropagation()" method="post" action="/pratiche/{pid}/elimina" onsubmit="return confirm('Spostare questa pratica nel Cestino? Potrai ripristinarla in seguito.')"><button class="btn danger-btn" type="submit">Elimina</button></form>''',
                archive_page,
            )
        self.handler.path="/"
        self.handler.dashboard(admin)
        dashboard_page=rendered[-1]
        self.assertIn("<th>Azione</th>",dashboard_page)
        self.assertIn(f'action="/pratiche/{new_pid}/elimina"',dashboard_page)

    def test_elimina_button_reuses_existing_soft_delete_route(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name)
                                VALUES(?,?,?,?,?,?,?,?)""",("CR-ROWDELETE","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna")).lastrowid
        redirects=[];self.handler.redirect=lambda path:redirects.append(path)
        self.handler.delete_practice(admin,pid)
        self.assertEqual(redirects[-1],"/cestino")
        with app.db() as conn:
            row=conn.execute("SELECT deleted_at FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertTrue(row["deleted_at"])

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
        opted_out='<form class="section no-advanced-collapse" method="get"><input name="q"><select name="stato"></select></form>'
        self.assertEqual(app.collapse_advanced_search(opted_out),opted_out)

    def test_urn_catalog_search_bar_is_always_visible_not_collapsed(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda html, *args: rendered.append(html)
        self.handler.path = "/catalogo-urne"
        self.handler.urn_catalog_page(admin)
        page = rendered[-1]
        self.assertIn('id="urnCatalogSearch"', page)
        self.assertNotIn('<details class="advanced-search"><summary>Ricerca avanzata</summary><form class="section urn-filter', page)

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
        self.assertIn("pagamento-movimento",page)
        self.assertIn("Totale acconto",page)
        self.assertIn("Numero fattura",page)
        self.assertIn("practice-list-table td:first-child",app.CSS)
        self.assertIn("width:132px;min-width:132px;max-width:132px",app.CSS)

    def test_practice_rows_shows_species_not_slash_for_collective_cremation(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         species,service_type) VALUES(?,?,?,?,?,?,?,?,?)""",
                         ("CR-COLLETTIVA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Gatto","Cremazione collettiva"))
            rows=conn.execute("SELECT * FROM practices WHERE practice_number='CR-COLLETTIVA'").fetchall()
        self.handler.path="/archivio/pratiche"
        page=self.handler.practice_rows(rows)
        self.assertIn("<td>Gatto</td>",page)
        self.assertNotIn("<td>/</td>",page)

    def test_archive_list_shows_inline_catalog_estremi_and_invoice_controls(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,service_type,payment_status,send_catalog,total_service)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-INLINE","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cane","Cremazione singola","Da saldare","Si","150")).lastrowid
            rows=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchall()
        self.handler.path="/archivio/pratiche"
        page=self.handler.practice_rows(rows,True)
        self.assertIn(f'/pratiche/{pid}/catalogo-inviato',page)
        self.assertIn(f'/pratiche/{pid}/estremi-inviati',page)
        self.assertIn('data-tag-field="catalog"',page)
        self.assertIn('data-tag-field="estremi"',page)
        self.assertIn('value="send" selected',page)
        self.assertIn(f'/pratiche/{pid}/fattura-rapida',page)
        self.assertIn('class="invoice-inline-input"',page)
        self.assertIn("Acconto W",page)
        self.assertIn("Rimanenza W",page)
        self.assertIn("saveTagState",app.APP_JS)
        self.assertIn("saveInvoiceNumber",app.APP_JS)

    def test_catalog_estremi_dropdowns_are_colored_by_selected_state(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid_send=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,service_type,payment_status,send_catalog,send_estremi)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-COLOR1","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cremazione singola","Da saldare","Si","Si")).lastrowid
            pid_sent=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,service_type,payment_status,catalog_sent,estremi_sent)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-COLOR2","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Rex","Cremazione singola","Da saldare","Si","Si")).lastrowid
            rows_send=conn.execute("SELECT * FROM practices WHERE id=?",(pid_send,)).fetchall()
            rows_sent=conn.execute("SELECT * FROM practices WHERE id=?",(pid_sent,)).fetchall()
        self.handler.path="/archivio/pratiche"
        page_send=self.handler.practice_rows(rows_send,True)
        page_sent=self.handler.practice_rows(rows_sent,True)
        self.assertIn('class="inline-state-select tag-select-orange"',page_send)
        self.assertIn('class="inline-state-select tag-select-green"',page_sent)
        self.assertIn(".tag-select-orange{color:#fb923c!important}",app.CSS)
        self.assertIn(".tag-select-green{color:#4ade80!important}",app.CSS)
        self.assertIn("select.classList.add('tag-select-orange')",app.APP_JS)

    def test_row_selection_deselects_on_outside_click_and_sticky_column_stays_opaque(self):
        self.assertIn("document.addEventListener('click',(event)=>{",app.APP_JS)
        self.assertIn("if(event.target.closest('tr.practice-row-link.row-selected'))return;",app.APP_JS)
        self.assertIn(".practice-row-link.row-selected td:first-child{background:#502d40!important}",app.CSS)
        self.assertIn(".light-theme .practice-row-link.row-selected td:first-child{background:#fde3e7!important}",app.CSS)

    def test_archive_page_always_shows_financial_columns(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,service_type,payment_status,total_service) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-NOFILTER","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Rex","Cane","Cremazione singola","Da saldare","120"))
        self.handler.path="/archivio/pratiche"
        rendered=[];self.handler.send_html=lambda content,*a:rendered.append(content)
        self.handler.archive(admin)
        body=rendered[-1]
        self.assertIn("<th>Totale W</th>",body)
        self.assertIn("<th>Acconto W</th>",body)
        self.assertIn("<th>Rimanenza W</th>",body)

    def test_catalog_sent_and_estremi_sent_ajax_and_invoice_quick_save(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,service_type,payment_status) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-AJAXTAG","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cremazione singola","Da saldare")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        with patch("app.emit_notification",return_value=[]):
            self.handler.form=lambda:{"catalog_sent":"Si","ajax":"1"};self.handler.catalog_sent(admin,pid)
        self.assertEqual(responses[-1],({"ok":True,"send_catalog":"","catalog_sent":"Si"},200))
        self.handler.form=lambda:{"send_estremi":"Si","ajax":"1"};self.handler.estremi_sent(admin,pid)
        self.assertEqual(responses[-1],({"ok":True,"send_estremi":"Si","estremi_sent":""},200))
        with app.db() as conn:
            row=conn.execute("SELECT catalog_sent,send_estremi FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((row["catalog_sent"],row["send_estremi"]),("Si","Si"))
        self.handler.form=lambda:{"invoice_number":"FT-INLINE-1","ajax":"1"};self.handler.quick_invoice(admin,pid)
        self.assertEqual(responses[-1],({"ok":True,"invoice_number":"FT-INLINE-1","make_invoice":"Si"},200))
        with app.db() as conn:
            row=conn.execute("SELECT invoice_number,make_invoice FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((row["invoice_number"],row["make_invoice"]),("FT-INLINE-1","Si"))
            other_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         invoice_number) VALUES(?,?,?,?,?,?,?,?)""",("CR-OTHERINV","Privato","Livorno","Ritirato",app.now(),app.now(),admin["id"],"FT-INLINE-1")).lastrowid
        self.handler.form=lambda:{"invoice_number":"FT-INLINE-1","ajax":"1"};self.handler.quick_invoice(admin,other_pid)
        self.assertEqual(responses[-1][1],400)
        self.assertIn("già usato",responses[-1][0]["error"])

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

    def test_use_voucher_checkbox_triggers_payment_date_prompt(self):
        # Setting payment_status='Pagato' via JS on the USA BUONO checkbox must fire a
        # real change event, so the existing date-prompt listener (setupPaymentStatusDatePrompt)
        # actually asks for the payment date instead of silently skipping it.
        js = app.APP_JS
        self.assertIn("if(e.target.checked && pay){pay.value='Pagato';pay.dispatchEvent(new Event('change',{bubbles:true}));}", js)

    def test_no_notification_when_status_set_to_da_consegnare(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid_quick=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,service_type)
                                VALUES(?,?,?,?,?,?,?,?)""",("CR-NONOTIF1","Privato","Livorno","Cremato",stamp,stamp,admin["id"],"Cremazione singola")).lastrowid
            pid_full=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,service_type)
                                VALUES(?,?,?,?,?,?,?,?)""",("CR-NONOTIF2","Privato","Livorno","Cremato",stamp,stamp,admin["id"],"Cremazione singola")).lastrowid
            notifications_before=conn.execute("SELECT count(*) n FROM notifications").fetchone()["n"]
        # Quick inline status change (archive/list dropdown).
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"status":"Da consegnare","ajax":"1"}
        self.handler.quick_state(admin,pid_quick)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications").fetchone()["n"],notifications_before)
        # Full status-change form (practice detail page).
        self.handler.form=lambda:{"status":"Da consegnare","payment_status":"Da saldare"}
        self.handler.redirect=lambda path:setattr(self,"redirected",path)
        self.handler.change_state(admin,pid_full)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications").fetchone()["n"],notifications_before)
            self.assertEqual(conn.execute("SELECT status FROM practices WHERE id=?",(pid_full,)).fetchone()["status"],"Da consegnare")
        # Sanity check: "Consegnato" still emits its own notification as before.
        self.handler.form=lambda:{"status":"Consegnato","ajax":"1"}
        self.handler.quick_state(admin,pid_quick)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications").fetchone()["n"],notifications_before+1)

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
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-PAY","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","200","200")).lastrowid
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"200,00","saldo_invoice_number":"FT-200","saldo_invoice_total":"200,00","saldo_invoice_date":"2026-07-14","economic_at":"2026-07-14","return_to":"/archivio/pratiche?stato=Ritirato"}
        redirects=[];self.handler.redirect=lambda path:redirects.append(path);self.handler.headers={}
        self.handler.quick_payment(admin,pid)
        with app.db() as conn:
            row=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((row["payment_status"],row["payment_method"],row["payment_amount"]),("Pagato","Pos","200.00"))
            # Legacy whole-practice invoice fields are no longer written from the
            # popover: invoicing now goes through movement_invoices instead.
            self.assertIsNone(row["invoice_number"])
            invoice=conn.execute("""SELECT mi.* FROM movement_invoices mi
                                    JOIN movement_invoice_links mil ON mil.invoice_id=mi.id
                                    JOIN payment_movements pm ON pm.id=mil.payment_movement_id
                                    WHERE pm.practice_id=?""",(pid,)).fetchone()
            self.assertEqual((invoice["invoice_number"],invoice["invoice_total"],invoice["invoice_date"]),("FT-200","200.00","2026-07-14"))
        self.assertEqual(redirects[-1],"/archivio/pratiche?stato=Ritirato")

    def test_payment_popover_shows_circuit_field_preselected_from_practice(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid_w=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-CIRCUITW","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","200","200")).lastrowid
            pid_d=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service,total_text)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-CIRCUITD","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Anna","Cremazione singola","Da saldare","200","200","220")).lastrowid
            rows_w=conn.execute("SELECT * FROM practices WHERE id=?",(pid_w,)).fetchall()
            rows_d=conn.execute("SELECT * FROM practices WHERE id=?",(pid_d,)).fetchall()
        self.handler.path="/archivio/pratiche"
        page_w=self.handler.practice_rows(rows_w,True)
        self.assertIn('<select name="acconto_circuito" onchange="ppmSyncMacroareaInvoiceSection(this)"><option value="W" selected>W</option><option value="D" >D</option></select>',page_w)
        self.assertIn('<select name="saldo_circuito" onchange="ppmSyncMacroareaInvoiceSection(this)"><option value="W" selected>W</option><option value="D" >D</option></select>',page_w)
        page_d=self.handler.practice_rows(rows_d,True)
        self.assertIn('<select name="acconto_circuito" onchange="ppmSyncMacroareaInvoiceSection(this)"><option value="W" >W</option><option value="D" selected>D</option></select>',page_d)
        self.assertIn('<select name="saldo_circuito" onchange="ppmSyncMacroareaInvoiceSection(this)"><option value="W" >W</option><option value="D" selected>D</option></select>',page_d)

    def test_quick_payment_honors_explicit_circuit_override(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-CIRCUITOVR","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","200","200")).lastrowid
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"200,00","economic_at":"2026-07-24","payment_channel":"D","ajax":"1"}
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            movement=conn.execute("SELECT category FROM balance_movements WHERE practice_id=?",(pid,)).fetchone()
            self.assertEqual(movement["category"],"D")
            legacy=conn.execute("SELECT movement_category FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()
            self.assertEqual(legacy["movement_category"],"D")

    def test_quick_payment_without_circuit_field_falls_back_to_practice_total_d(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-CIRCUITDEF","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","200","200")).lastrowid
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"200,00","economic_at":"2026-07-24","ajax":"1"}
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            movement=conn.execute("SELECT category FROM balance_movements WHERE practice_id=?",(pid,)).fetchone()
            self.assertEqual(movement["category"],"W")

    def test_movement_invoices_schema_created(self):
        with app.db() as conn:
            tables={row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("movement_invoices",tables)
        self.assertIn("movement_invoice_links",tables)

    def test_full_per_movement_invoicing_scenario_acconto_w_saldo_d(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-MOVINV","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","300","300")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Acconto","payment_method":"Contanti","payment_amount":"100,00","payment_channel":"W","economic_at":"2026-07-19","acconto_invoice_number":"FT-ACC-1","acconto_invoice_total":"100,00","acconto_invoice_date":"2026-07-19","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Bonifico","payment_amount":"200,00","payment_channel":"D","economic_at":"2026-07-24","saldo_invoice_number":"FT-SAL-1","saldo_invoice_total":"200,00","saldo_invoice_date":"2026-07-24","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            movements=conn.execute("SELECT payment_type,payment_channel,amount,paid_at FROM payment_movements WHERE practice_id=? ORDER BY id",(pid,)).fetchall()
            self.assertEqual(len(movements),2)
            self.assertEqual((movements[0]["payment_type"],movements[0]["payment_channel"],float(movements[0]["amount"]),movements[0]["paid_at"]),("acconto","W",100.0,"2026-07-19"))
            self.assertEqual((movements[1]["payment_type"],movements[1]["payment_channel"],float(movements[1]["amount"]),movements[1]["paid_at"]),("saldo","D",200.0,"2026-07-24"))
            invoices=conn.execute("""SELECT mi.invoice_number,mi.invoice_total,mi.payment_channel,mi.payment_method,pm.payment_type
                                     FROM movement_invoices mi
                                     JOIN movement_invoice_links mil ON mil.invoice_id=mi.id
                                     JOIN payment_movements pm ON pm.id=mil.payment_movement_id
                                     WHERE mi.practice_id=? ORDER BY mi.id""",(pid,)).fetchall()
            self.assertEqual(len(invoices),2)
            self.assertEqual((invoices[0]["invoice_number"],invoices[0]["invoice_total"],invoices[0]["payment_channel"],invoices[0]["payment_method"],invoices[0]["payment_type"]),("FT-ACC-1","100.00","W","Contanti","acconto"))
            self.assertEqual((invoices[1]["invoice_number"],invoices[1]["invoice_total"],invoices[1]["payment_channel"],invoices[1]["payment_method"],invoices[1]["payment_type"]),("FT-SAL-1","200.00","D","Bonifico","saldo"))
            practice=conn.execute("SELECT invoice_number FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertIsNone(practice["invoice_number"])
            rows=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchall()
        self.handler.path="/archivio/pratiche"
        list_page=self.handler.practice_rows(rows,True)
        self.assertIn("FT-ACC-1",list_page)
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/fatture?q=MOVINV"
        self.handler.invoices_page(admin)
        invoices_page=rendered[-1]
        self.assertIn("FT-ACC-1",invoices_page)
        self.assertIn("FT-SAL-1",invoices_page)
        self.assertEqual(invoices_page.count(">CR-MOVINV<"),2)

    def test_quick_payment_invoice_number_blank_leaves_movement_uninvoiced(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-NOINV","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Anna","Cremazione singola","Da saldare","150","150")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"150,00","economic_at":"2026-07-20","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            movements=conn.execute("SELECT count(*) n FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()["n"]
            self.assertEqual(movements,1)
            invoices=conn.execute("SELECT count(*) n FROM movement_invoices WHERE practice_id=?",(pid,)).fetchone()["n"]
            self.assertEqual(invoices,0)

    def test_create_multi_movement_invoice_combines_selected_movements(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-COMBOINV","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luca","Cremazione singola","Da saldare","300","300")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Acconto","payment_method":"Contanti","payment_amount":"100,00","payment_channel":"W","economic_at":"2026-07-19","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Contanti","payment_amount":"200,00","payment_channel":"W","economic_at":"2026-07-24","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        with app.db() as conn:
            movement_ids=[row["id"] for row in conn.execute("SELECT id FROM payment_movements WHERE practice_id=? ORDER BY id",(pid,))]
        self.assertEqual(len(movement_ids),2)
        form_data={f"movement_{mid}":"1" for mid in movement_ids}
        form_data.update({"invoice_number":"FT-COMBO","invoice_date":"2026-07-25","payment_method":"Bonifico","payment_channel":"W","practice_view":f"/pratiche/{pid}"})
        self.handler.form=lambda:form_data
        redirects=[];self.handler.redirect=lambda path:redirects.append(path)
        self.handler.create_multi_movement_invoice(admin,pid)
        self.assertEqual(redirects[-1],f"/pratiche/{pid}")
        with app.db() as conn:
            invoice=conn.execute("SELECT * FROM movement_invoices WHERE practice_id=?",(pid,)).fetchone()
            self.assertEqual((invoice["invoice_number"],invoice["invoice_total"],invoice["payment_channel"]),("FT-COMBO","300.00","W"))
            links=conn.execute("SELECT count(*) n FROM movement_invoice_links WHERE invoice_id=?",(invoice["id"],)).fetchone()["n"]
            self.assertEqual(links,2)

    def test_invoice_conflict_blocks_duplicate_movement_invoice_number(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid1=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-DUPINV1","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Sara","Cremazione singola","Da saldare","100","100")).lastrowid
            pid2=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-DUPINV2","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Piero","Cremazione singola","Da saldare","100","100")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"100,00","payment_channel":"W","economic_at":"2026-07-19","saldo_invoice_number":"FT-DUP","saldo_invoice_total":"100,00","ajax":"1"}
        self.handler.quick_payment(admin,pid1)
        self.assertTrue(responses[-1][0]["ok"])
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"100,00","payment_channel":"W","economic_at":"2026-07-20","saldo_invoice_number":"FT-DUP","saldo_invoice_total":"100,00","ajax":"1"}
        self.handler.quick_payment(admin,pid2)
        self.assertFalse(responses[-1][0]["ok"])
        self.assertIn("già usato",responses[-1][0]["error"])
        # Re-saving pid1's own saldo with the same number must still succeed (no false self-conflict)
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"100,00","payment_channel":"W","economic_at":"2026-07-21","saldo_invoice_number":"FT-DUP","saldo_invoice_total":"100,00","ajax":"1"}
        self.handler.quick_payment(admin,pid1)
        self.assertTrue(responses[-1][0]["ok"])

    def test_practice_summary_shows_editable_metodo_dropdown_saved_via_ajax(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,payment_method,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-METODO","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","Pos","200")).lastrowid
        rendered=[]; self.handler.send_html=lambda content,*args: rendered.append(content)
        self.handler.practice(admin,pid)
        page=rendered[-1]
        self.assertIn(f'data-method-endpoint="/pratiche/{pid}/pagamento-rapido"',page)
        self.assertIn('name="payment_method" class="inline-state-select"',page)
        self.assertIn('<option value="Pos" selected>Pos</option>',page)
        self.assertNotIn("<b>Pos</b>",page)
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Da saldare","payment_method":"Bonifico","payment_amount":"","invoice_number":"","invoice_total":"","invoice_date":"","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertEqual(responses[-1],({"ok":True,"payment_method":"Bonifico","payment_status":"Da saldare"},200))
        with app.db() as conn:
            row=conn.execute("SELECT payment_method FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual(row["payment_method"],"Bonifico")
        self.assertIn("saveMethodSelect",app.APP_JS)

    def test_payment_status_needs_date_helper(self):
        self.assertTrue(app.payment_status_needs_date("Da saldare","Acconto",""))
        self.assertTrue(app.payment_status_needs_date("Da saldare","Acconto","not-a-date"))
        self.assertFalse(app.payment_status_needs_date("Da saldare","Acconto","2026-07-14"))
        self.assertTrue(app.payment_status_needs_date("Acconto","Acconto",""))
        self.assertFalse(app.payment_status_needs_date("Da saldare","Da saldare",""))
        self.assertTrue(app.payment_status_needs_date("Acconto","Pagato",""))
        self.assertFalse(app.payment_status_needs_date("Acconto","Pagato","2026-07-15"))

    def test_quick_payment_requires_date_when_transitioning_to_paid_and_uses_supplied_date(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-PAYDATE","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","300","300")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"300,00","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertEqual(responses[-1][1],400)
        self.assertIn("data pagamento/acconto",responses[-1][0]["error"])
        with app.db() as conn:
            row=conn.execute("SELECT payment_status,payment_method FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((row["payment_status"],row["payment_method"]),("Da saldare",None))
            movements=conn.execute("SELECT count(*) n FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()["n"]
            self.assertEqual(movements,0)
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Pos","payment_amount":"300,00","economic_at":"2026-06-01","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertEqual(responses[-1],({"ok":True,"payment_method":"Pos","payment_status":"Pagato"},200))
        with app.db() as conn:
            row=conn.execute("SELECT payment_status,paid_at FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual(row["payment_status"],"Pagato")
            self.assertEqual(row["paid_at"],"2026-06-01")
            movement=conn.execute("SELECT paid_at,amount FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()
            self.assertEqual(movement["paid_at"],"2026-06-01")
            self.assertEqual(float(movement["amount"]),300.0)

    def test_split_acconto_and_saldo_record_their_own_distinct_payment_dates(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-SPLITDATE","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","500","500")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Acconto","payment_method":"Contanti","payment_amount":"200,00","economic_at":"2026-06-01","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Contanti","payment_amount":"500,00","economic_at":"2026-06-20","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertEqual(responses[-1][1],400)
        self.assertIn("rimanenza",responses[-1][0]["error"])
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Contanti","payment_amount":"300,00","economic_at":"2026-06-20","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            row=conn.execute("SELECT deposit_paid_at,paid_at FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual(row["deposit_paid_at"],"2026-06-01")
            self.assertEqual(row["paid_at"],"2026-06-20")
            movements=conn.execute("SELECT payment_type,paid_at,amount FROM payment_movements WHERE practice_id=? ORDER BY id",(pid,)).fetchall()
            self.assertEqual(len(movements),2)
            self.assertEqual((movements[0]["payment_type"],movements[0]["paid_at"],float(movements[0]["amount"])),("acconto","2026-06-01",200.0))
            self.assertEqual((movements[1]["payment_type"],movements[1]["paid_at"],float(movements[1]["amount"])),("saldo","2026-06-20",300.0))


    def test_payment_ledger_classifies_each_cash_movement_once(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,animal_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("CR-NEW-LEDGER","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Fido","Cremazione singola","Da saldare","300","300")).lastrowid
            collab_id=conn.execute("SELECT id FROM collaborators ORDER BY id LIMIT 1").fetchone()["id"]
            collab_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                       animal_name,service_type,payment_status,price_cremation,total_service,collaborator_id)
                                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                    ("COL-NEW-LEDGER","Collaboratore","Livorno","Ritirato",stamp,stamp,admin["id"],"Rex","Cremazione singola","Da saldare","150","150",collab_id)).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Acconto","payment_method":"Pos","payment_amount":"100","economic_at":"2026-07-10","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Bonifico","payment_amount":"200","economic_at":"2026-07-20","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        # Reinvio della stessa richiesta: lo stato non cambia e non nasce un terzo movimento.
        self.handler.quick_payment(admin,pid)
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Contanti","payment_amount":"150","economic_at":"2026-07-20","ajax":"1"}
        self.handler.quick_payment(admin,collab_pid)
        with app.db() as conn:
            rows=conn.execute("""SELECT practice_id,payment_type,payment_method,movement_category,amount,paid_at
                                 FROM payment_movements WHERE practice_id IN (?,?) ORDER BY id""",(pid,collab_pid)).fetchall()
            self.assertEqual(len(rows),3)
            self.assertEqual(tuple(rows[0]),(pid,"acconto","Pos","W",100.0,"2026-07-10"))
            self.assertEqual(tuple(rows[1]),(pid,"saldo","Bonifico","W",200.0,"2026-07-20"))
            self.assertEqual(tuple(rows[2]),(collab_pid,"saldo","Contanti","Collaboratori",150.0,"2026-07-20"))
            conn.execute("UPDATE practices SET notes='Modifica senza pagamento',updated_at=? WHERE id=?",(app.now(),pid))
            self.assertEqual(conn.execute("SELECT count(*) n FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()["n"],2)


    def test_payment_dialog_is_identical_in_list_and_inside_practice(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                animal_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("CR-DIALOG","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Fido","Cremazione singola","Da saldare","200","200")).lastrowid
            row=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        dialog=self.handler.status_badges(row)
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path=f"/pratiche/{pid}";self.handler.practice(admin,pid)
        page=rendered[-1]
        for token in ('action="/pratiche/{}/pagamento-movimento"'.format(pid),'name="acconto_totale"','name="acconto_data"','name="acconto_modalita"','name="saldo_totale"','name="saldo_data"','name="saldo_modalita"'):
            self.assertIn(token,dialog)
            self.assertIn(token,page)

    def test_payment_macroareas_are_independent_always_visible_and_precompiled(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service,deposit)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-MACRO","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Elio","Cremazione singola","Da saldare","350","350","200")).lastrowid
            row=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        # both macroareas are always rendered, with pre-filled totals from
        # the deposit set at creation (200) and the remainder (150)
        dialog=self.handler.status_badges(row)
        self.assertIn('<section class="payment-macroarea" data-macroarea="acconto">',dialog)
        self.assertIn('<section class="payment-macroarea" data-macroarea="saldo">',dialog)
        self.assertIn('name="acconto_totale" value="200"',dialog)
        self.assertIn('name="saldo_totale" value="150.00"',dialog)
        # save ACCONTO alone, circuito W, with its own invoice
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"macroarea":"acconto","acconto_data":"2026-07-19","acconto_totale":"200,00","acconto_circuito":"W","acconto_modalita":"Contanti","acconto_fattura_numero":"FT-ACC-MACRO","acconto_fattura_totale":"200,00","acconto_fattura_data":"2026-07-19","ajax":"1"}
        self.handler.save_payment_macroarea(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        self.assertEqual(responses[-1][0]["payment_status"],"Acconto")
        with app.db() as conn:
            practice=conn.execute("SELECT payment_status,deposit FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual(practice["payment_status"],"Acconto")
            movements=conn.execute("SELECT payment_type,payment_channel,amount,paid_at FROM payment_movements WHERE practice_id=?",(pid,)).fetchall()
            self.assertEqual(len(movements),1)
            self.assertEqual((movements[0]["payment_type"],movements[0]["payment_channel"],float(movements[0]["amount"]),movements[0]["paid_at"]),("acconto","W",200.0,"2026-07-19"))
            balance=conn.execute("SELECT category,movement_type,amount_cents FROM balance_movements WHERE practice_id=?",(pid,)).fetchone()
            self.assertEqual((balance["category"],balance["movement_type"],balance["amount_cents"]),("W","Acconto",20000))
        # saving SALDO does not require touching acconto, and can use a
        # different circuit; the invoice section only exists for circuito W
        # (per spec), so a D saldo never gets its own movement invoice
        self.handler.form=lambda:{"macroarea":"saldo","saldo_data":"2026-07-24","saldo_totale":"150,00","saldo_circuito":"D","saldo_modalita":"Bonifico","ajax":"1"}
        self.handler.save_payment_macroarea(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        self.assertEqual(responses[-1][0]["payment_status"],"Pagato")
        with app.db() as conn:
            practice=conn.execute("SELECT payment_status FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual(practice["payment_status"],"Pagato")
            movements=conn.execute("SELECT payment_type,payment_channel,amount,paid_at FROM payment_movements WHERE practice_id=? ORDER BY id",(pid,)).fetchall()
            self.assertEqual(len(movements),2)
            self.assertEqual((movements[0]["payment_type"],movements[0]["payment_channel"],float(movements[0]["amount"])),("acconto","W",200.0))
            self.assertEqual((movements[1]["payment_type"],movements[1]["payment_channel"],float(movements[1]["amount"])),("saldo","D",150.0))
            balances={row["category"]:row for row in conn.execute("SELECT category,movement_type,amount_cents FROM balance_movements WHERE practice_id=? AND amount_cents>0",(pid,))}
            self.assertEqual(balances["W"]["movement_type"],"Acconto")
            self.assertEqual(balances["D"]["movement_type"],"Saldo")
            self.assertEqual(balances["D"]["amount_cents"],15000)
            invoices=conn.execute("""SELECT mi.invoice_number,mi.payment_channel FROM movement_invoices mi
                                     JOIN movement_invoice_links mil ON mil.invoice_id=mi.id
                                     JOIN payment_movements pm ON pm.id=mil.payment_movement_id
                                     WHERE pm.practice_id=? ORDER BY mi.id""",(pid,)).fetchall()
            self.assertEqual([(r["invoice_number"],r["payment_channel"]) for r in invoices],[("FT-ACC-MACRO","W")])
        # correcting the acconto afterwards must not touch the saldo movement
        self.handler.form=lambda:{"macroarea":"acconto","acconto_data":"2026-07-20","acconto_totale":"210,00","acconto_circuito":"W","acconto_modalita":"Pos","ajax":"1"}
        self.handler.save_payment_macroarea(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            acconto=conn.execute("SELECT amount,paid_at,payment_method FROM payment_movements WHERE practice_id=? AND payment_type='acconto'",(pid,)).fetchone()
            self.assertEqual((float(acconto["amount"]),acconto["paid_at"],acconto["payment_method"]),(210.0,"2026-07-20","Pos"))
            saldo=conn.execute("SELECT amount,paid_at,payment_method FROM payment_movements WHERE practice_id=? AND payment_type='saldo'",(pid,)).fetchone()
            self.assertEqual((float(saldo["amount"]),saldo["paid_at"],saldo["payment_method"]),(150.0,"2026-07-24","Bonifico"))

    def test_removing_a_macroarea_moves_payment_status_backward_and_subtracts_from_bilanci(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,animal_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-000039-TEST","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Test","Rubio","Cremazione singola","Da saldare","300","300")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        # Da saldare -> Acconto (100 W POS, with its own invoice)
        self.handler.form=lambda:{"macroarea":"acconto","acconto_data":"2026-07-17","acconto_totale":"100,00","acconto_circuito":"W","acconto_modalita":"Pos","acconto_fattura_numero":"FT-RUBIO-ACC","acconto_fattura_totale":"100,00","acconto_fattura_data":"2026-07-17","ajax":"1"}
        self.handler.save_payment_macroarea(admin,pid)
        self.assertEqual(responses[-1][0]["payment_status"],"Acconto")
        # Acconto -> Pagato (200 D Contanti)
        self.handler.form=lambda:{"macroarea":"saldo","saldo_data":"2026-07-24","saldo_totale":"200,00","saldo_circuito":"D","saldo_modalita":"Contanti","ajax":"1"}
        self.handler.save_payment_macroarea(admin,pid)
        self.assertEqual(responses[-1][0]["payment_status"],"Pagato")
        with app.db() as conn:
            practice=conn.execute("SELECT payment_status,deposit,remaining_balance FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((practice["payment_status"],practice["deposit"],practice["remaining_balance"]),("Pagato","100.00","0.00"))
            open_d=sum(row.amount_cents for row in app.get_balance_movements(conn,filters=app.normalize_balance_filters()) if row.practice_id==pid and row.category=="D")
            self.assertEqual(open_d,20000)
        # Pagato -> Acconto: removing the saldo must subtract exactly that
        # movement from Bilanci (D circuit) without touching the acconto
        self.handler.form=lambda:{"macroarea":"saldo","ajax":"1"}
        self.handler.remove_payment_macroarea(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        self.assertEqual(responses[-1][0]["payment_status"],"Acconto")
        with app.db() as conn:
            practice=conn.execute("SELECT payment_status,deposit FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((practice["payment_status"],practice["deposit"]),("Acconto","100.00"))
            saldo_row=conn.execute("SELECT * FROM payment_movements WHERE practice_id=? AND payment_type='saldo'",(pid,)).fetchone()
            self.assertIsNone(saldo_row)
            acconto_row=conn.execute("SELECT amount FROM payment_movements WHERE practice_id=? AND payment_type='acconto'",(pid,)).fetchone()
            self.assertEqual(float(acconto_row["amount"]),100.0)
            open_d_after=sum(row.amount_cents for row in app.get_balance_movements(conn,filters=app.normalize_balance_filters()) if row.practice_id==pid and row.category=="D")
            self.assertEqual(open_d_after,0)
            acconto_invoice=conn.execute("""SELECT mi.invoice_number FROM movement_invoices mi
                                            JOIN movement_invoice_links mil ON mil.invoice_id=mi.id
                                            JOIN payment_movements pm ON pm.id=mil.payment_movement_id
                                            WHERE pm.practice_id=? AND pm.payment_type='acconto'""",(pid,)).fetchone()
            self.assertEqual(acconto_invoice["invoice_number"],"FT-RUBIO-ACC")
        # Acconto -> Da saldare: removing the acconto too must subtract it
        # from Bilanci (W circuit) and also drop its now-orphaned invoice
        self.handler.form=lambda:{"macroarea":"acconto","ajax":"1"}
        self.handler.remove_payment_macroarea(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        self.assertEqual(responses[-1][0]["payment_status"],"Da saldare")
        with app.db() as conn:
            practice=conn.execute("SELECT payment_status,deposit,remaining_balance FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual((practice["payment_status"],practice["deposit"]),("Da saldare","0.00"))
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()["n"],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM movement_invoices WHERE practice_id=?",(pid,)).fetchone()["n"],0)
            open_w=sum(row.amount_cents for row in app.get_balance_movements(conn,filters=app.normalize_balance_filters()) if row.practice_id==pid and row.category=="W")
            self.assertEqual(open_w,0)
        # removing an already-absent macroarea is a harmless no-op
        self.handler.form=lambda:{"macroarea":"acconto","ajax":"1"}
        self.handler.remove_payment_macroarea(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        self.assertEqual(responses[-1][0]["payment_status"],"Da saldare")

    def test_acconto_and_saldo_keep_their_own_movement_dates(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-PAYMENT-DATE","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","350","350")).lastrowid
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"payment_status":"Acconto","payment_method":"Contanti","payment_amount":"100,00","economic_at":"2026-07-19","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"Contanti","payment_amount":"250,00","economic_at":"2026-07-24","ajax":"1"}
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            movements=conn.execute("SELECT payment_type,paid_at,amount FROM payment_movements WHERE practice_id=? ORDER BY id",(pid,)).fetchall()
            self.assertEqual(len(movements),2)
            self.assertEqual((movements[0]["payment_type"],movements[0]["paid_at"],float(movements[0]["amount"])),("acconto","2026-07-19",100.0))
            self.assertEqual((movements[1]["payment_type"],movements[1]["paid_at"],float(movements[1]["amount"])),("saldo","2026-07-24",250.0))

    def test_create_and_edit_practice_require_payment_date_on_transition(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.form=lambda:{"operator_name":"FILIPPO","service_type":"Da decidere","request_origin":"Privato","owner_first_name":"Anna","owner_last_name":"Neri",
                                   "owner_phone":"3331112222","owner_tax_code":"NRIANN80A01H501U","owner_street":"Via Test","owner_city":"Livorno",
                                   "owner_province":"LI","owner_zip":"57100","payment_status":"Pagato","calendar_event_id":""}
        pages=[];self.handler.new_page=lambda user,draft=None,error="":pages.append(error)
        self.handler.create_practice(admin)
        self.assertIn("importo, data e metodo",pages[-1])
        with app.db() as conn:
            count=conn.execute("SELECT count(*) n FROM practices WHERE practice_number='CR-EDITDATE' OR owner_first_name='Anna'").fetchone()["n"]
            self.assertEqual(count,0)
        with app.db() as conn:
            stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,owner_last_name,owner_phone,owner_tax_code,owner_street,owner_city,owner_province,owner_zip,
                                service_type,payment_status,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                ("CR-EDITDATE","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Anna","Neri","3331112222","NRIANN80A01H501U",
                                 "Via Test","Livorno","LI","57100","Da decidere","Da saldare","250")).lastrowid
        self.handler.form=lambda:{"operator_name":"FILIPPO","service_type":"Da decidere","request_origin":"Privato","owner_first_name":"Anna","owner_last_name":"Neri",
                                   "owner_phone":"3331112222","owner_tax_code":"NRIANN80A01H501U","owner_street":"Via Test","owner_city":"Livorno",
                                   "owner_province":"LI","owner_zip":"57100","payment_status":"Acconto"}
        edit_pages=[];self.handler.edit_page=lambda user,pid,draft=None,error="":edit_pages.append(error)
        self.handler.edit_submit(admin,pid)
        self.assertIn("data pagamento/acconto",edit_pages[-1])
        with app.db() as conn:
            row=conn.execute("SELECT payment_status FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual(row["payment_status"],"Da saldare")

    def test_dashboard_period_bounds_are_today_saturday_friday_and_month(self):
        reference=date(2026,7,15)
        self.assertEqual(app.dashboard_period_bounds("oggi",reference),("oggi",reference,reference))
        self.assertEqual(app.dashboard_period_bounds("settimana",reference),("settimana",date(2026,7,11),date(2026,7,17)))
        self.assertEqual(app.dashboard_period_bounds("mese",reference),("mese",date(2026,7,1),date(2026,7,31)))
        self.assertEqual(app.dashboard_period_bounds("mese",date(2026,12,8)),("mese",date(2026,12,1),date(2026,12,31)))

    def test_dashboard_uses_operational_and_economic_dates_without_double_counting(self):
        today=datetime.now().date();_,week_start,week_end=app.dashboard_period_bounds("settimana",today)
        # Pick a day inside the current week that is not "today" itself, regardless of which
        # weekday the suite happens to run on (this app's week starts on Saturday, so "today"
        # can itself be week_start and the two must not collide).
        week_other_day=week_end if week_start==today else week_start
        old_day=(today-timedelta(days=35)).isoformat();today_text=today.isoformat();week_day=week_other_day.isoformat();stamp=app.now()
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
        self.assertNotIn("Entrate anno in corso",page)
        self.assertNotIn("data-balance-card",page)
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
        for token in (".dashboard-section-head",".period-selector","min-height:44px","var(--safe-bottom)"):
            self.assertIn(token,app.CSS)
        self.assertNotIn(".dashboard-chart-only",app.CSS)
        dashboard_constants="".join(value for value in app.App.dashboard.__code__.co_consts if isinstance(value,str))
        self.assertIn("localStorage.getItem('ppm_'+key)",dashboard_constants)

    def test_balances_interface_and_payment_pages_still_render(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            tables={row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("expenses",tables)
        self.assertNotIn("incomes",tables)
        self.assertIn("balance_movements",tables)
        self.assertTrue(hasattr(app.App,"balances_page"))
        self.assertIn(("/bilanci","chart","Bilanci"),app.SIDEBAR_LINKS)
        rendered=[]; self.handler.send_html=lambda content,*args: rendered.append(content)
        self.handler.dashboard(admin)
        self.assertNotIn("Entrate anno in corso",rendered[-1])
        self.assertIn('href="/bilanci"',rendered[-1])
        self.assertNotIn("data-balance-card",rendered[-1])
        self.assertIn("Totale W",rendered[-1])
        self.assertNotIn("Totale calcolato",rendered[-1])
        self.handler.require_user=lambda:admin
        self.handler.path="/bilanci"
        self.handler.do_GET()
        balances_page=rendered[-1]
        self.assertIn("<h1>Bilanci</h1>",balances_page)
        for label in (
            "Periodo","Data da","Data a","Categoria","Collaboratore","Metodo pagamento","Stato","Operatore","Ricerca",
            "Entrate W","Entrate D","Collaboratori Incassato","Da riscuotere W",
            "Da riscuotere D","Collaboratori Da riscuotere","Uscite W","Uscite D",
            "Totale W attuale","Totale D attuale","Saldo Netto",
        ):
            self.assertIn(label,balances_page)
        self.assertEqual(balances_page.count('data-balance-card="'),11)
        self.assertEqual(balances_page.count('data-balance-total-cents="0"'),11)
        self.assertIn('aria-current="true"',balances_page)
        self.assertIn("<h2>Entrate W</h2>",balances_page)
        self.assertIn("Nessun dato da visualizzare.",balances_page)
        self.assertIn('method="get" action="/bilanci"',balances_page)
        self.assertIn('method="post" action="/bilanci/uscite?',balances_page)
        self.assertIn("Registra uscita manuale",balances_page)
        for responsive_rule in (
            ".balance-grid{display:grid;grid-template-columns:repeat(2",
            "@media(max-width:900px)",
            ".balance-grid{grid-template-columns:repeat(2",
            "@media(max-width:560px){.balance-filters .fields{grid-template-columns:1fr}.balance-grid{grid-template-columns:repeat(2",
            "calc(92px + var(--safe-bottom))",
        ):
            self.assertIn(responsive_rule,app.CSS)
        self.handler.payment_overview(admin,"da-saldare")
        self.assertIn("Da saldare D",rendered[-1])
        self.assertIn("Totale W e Totale D",rendered[-1])

    def test_bilanci_elimina_button_works_on_legacy_synthesized_rows(self):
        # Practices created before the balance_movements ledger existed only
        # have their payment history in payment_movements, so Bilanci
        # synthesizes a row on the fly with a negative synthetic id. Elimina
        # must work on those rows too, not just real balance_movements ones.
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-LEGACYVOID","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Bilbo","Cremazione singola","Pagato","150","150")).lastrowid
            conn.execute("""INSERT INTO payment_movements(practice_id,payment_type,payment_channel,payment_method,movement_category,amount,paid_at,user_id,notes,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",(pid,"saldo","W","Contanti","W",150.0,"2026-07-10",admin["id"],"","2026-07-10T10:00:00"))
        with app.db() as conn:
            movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters(include_technical=True))
        legacy=[m for m in movements if m.practice_id==pid and m.id<0]
        self.assertEqual(len(legacy),1)
        legacy_key=legacy[0].idempotency_key
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/bilanci?view=entrate-w&periodo=tutto"
        self.handler.balances_page(admin)
        page=rendered[-1]
        self.assertIn('action="/bilanci/movimenti/storna-storico"',page)
        self.assertIn(f'value="{legacy_key}"',page)
        redirects=[];self.handler.redirect=lambda url:redirects.append(url)
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":legacy_key}
        self.handler.balance_legacy_movement_void(admin)
        self.assertTrue(redirects and "movimento_stornato=1" in redirects[-1])
        with app.db() as conn:
            movements_after=app.get_balance_movements(conn,filters=app.normalize_balance_filters(include_technical=True))
        self.assertFalse(any(m.idempotency_key==legacy_key for m in movements_after))
        with app.db() as conn:
            default_movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters())
        self.assertFalse(any(m.practice_id==pid for m in default_movements))
        # Retrying the void must stay idempotent, not create a second storno.
        self.handler.balance_legacy_movement_void(admin)
        with app.db() as conn:
            void_count=conn.execute(
                "SELECT COUNT(*) FROM balance_movements WHERE idempotency_key=?",
                (f"legacy-void:v1:{legacy_key}",),
            ).fetchone()[0]
        self.assertEqual(void_count,1)

    def test_dashboard_reminders_panel_replaces_old_flash_and_supports_full_lifecycle(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name)
                                VALUES(?,?,?,?,?,?,?,?)""",("CR-REMIND","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Nuvola")).lastrowid
            article_id=conn.execute("SELECT id,name FROM articles WHERE active=1 LIMIT 1").fetchone()
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        self.assertNotIn("hanno dati ancora da completare",page)
        self.assertIn('<section class="section reminders-panel"><h2>Promemoria</h2>',page)
        self.assertIn(f'href="/pratiche/{pid}"',page)
        self.assertIn("Completa i dati della pratica CR-REMIND",page)
        with app.db() as conn:
            reminder_id=conn.execute(
                "SELECT id FROM reminders WHERE dedupe_key=?",(f"practice_incomplete:{pid}",)
            ).fetchone()["id"]
        # calling dashboard again must not duplicate the same open reminder
        self.handler.dashboard(admin)
        with app.db() as conn:
            count=conn.execute(
                "SELECT COUNT(*) FROM reminders WHERE dedupe_key=?",(f"practice_incomplete:{pid}",)
            ).fetchone()[0]
        self.assertEqual(count,1)
        # ordering a product creates its own reminder, independent type
        self.handler.form=lambda:{}
        redirects=[];self.handler.redirect=lambda url:redirects.append(url)
        self.handler.order_article(admin,article_id["id"])
        with app.db() as conn:
            product_reminder=conn.execute(
                "SELECT * FROM reminders WHERE reminder_type='product_reorder' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIn(article_id["name"],product_reminder["title"])
        self.assertEqual(product_reminder["url"],"/prodotti")
        self.handler.dashboard(admin)
        self.assertIn(f"Riordinare: {article_id['name']}",rendered[-1])
        # completing the practice reminder via AJAX marks it done, with an audit trail
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"ajax":"1"}
        self.handler.complete_reminder(admin,reminder_id)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            completed=conn.execute("SELECT completed_at,completed_by FROM reminders WHERE id=?",(reminder_id,)).fetchone()
        self.assertIsNotNone(completed["completed_at"])
        self.assertEqual(completed["completed_by"],admin["id"])
        self.handler.dashboard(admin)
        self.assertNotIn(f'href="/pratiche/{pid}"',rendered[-1])
        # completing an already-completed reminder is a harmless no-op
        first_completed_at=completed["completed_at"]
        self.handler.complete_reminder(admin,reminder_id)
        with app.db() as conn:
            still=conn.execute("SELECT completed_at FROM reminders WHERE id=?",(reminder_id,)).fetchone()
        self.assertEqual(still["completed_at"],first_completed_at)

    def test_must_change_password_gate_and_change_password_flow(self):
        with app.db() as conn:
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()
            self.assertEqual(serena["must_change_password"], 1)
            self.assertTrue(app.password_ok("petparadise", serena["password_hash"]))
            token = "test-session-token"
            conn.execute("INSERT INTO sessions VALUES(?,?,?)", (token, serena["id"], app.now()))
        self.handler.headers = {"Cookie": f"ppm_session={token}"}
        redirects = []
        self.handler.redirect = lambda path: redirects.append(path)
        self.handler.dashboard = lambda user: self.fail("La dashboard non deve essere renderizzata prima del cambio password obbligatorio")

        self.handler.path = "/"
        self.handler.do_GET()
        self.assertEqual(redirects, ["/imposta-password"])

        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/imposta-password"
        self.handler.do_GET()
        self.assertIn("Imposta la tua nuova password", rendered[-1])

        self.handler.form = lambda: {"new_password": "nuovapassword123", "confirm_password": "nuovapassword123", "return_to": "/"}
        redirects.clear()
        self.handler.do_POST()
        self.assertEqual(redirects, ["/"])

        with app.db() as conn:
            updated = conn.execute("SELECT * FROM users WHERE id=?", (serena["id"],)).fetchone()
        self.assertEqual(updated["must_change_password"], 0)
        self.assertFalse(app.password_ok("petparadise", updated["password_hash"]))
        self.assertTrue(app.password_ok("nuovapassword123", updated["password_hash"]))

        dashboard_calls = []
        self.handler.dashboard = lambda user: dashboard_calls.append(user)
        redirects.clear()
        self.handler.path = "/"
        self.handler.do_GET()
        self.assertEqual(redirects, [])
        self.assertEqual(dashboard_calls[0]["id"], serena["id"])

    def test_service_type_is_required_and_not_preselected(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        form_html = app.App._fields_html(self.handler, None, admin)
        self.assertIn('<option value="" selected>SELEZIONA</option>', form_html)
        self.assertNotIn('<option selected>Da decidere</option>', form_html)

        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.form = lambda: {
            "operator_name": "SERENA", "owner_first_name": "Anna", "owner_last_name": "Bianchi",
            "owner_phone": "333", "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno",
            "owner_province": "LI", "owner_zip": "57100", "request_origin": "Privato",
        }
        self.handler.create_practice(admin)
        self.assertIn("Campi obbligatori mancanti", rendered[-1])
        self.assertIn("Servizio", rendered[-1])

    def test_arrange_budget_layout_places_payment_status_after_remaining_final(self):
        js = app.APP_JS
        remaining_final_idx = js.index("addRow([field('total_text'),field('deposit_final'),field('remaining_final')]);")
        payment_status_idx = js.index("addRow([field('payment_status'),field('economic_at')],[field('payment_method')]);")
        self.assertLess(remaining_final_idx, payment_status_idx)
        self.assertNotIn("addRow([field('price_cremation')],[field('payment_status')]);", js)
        self.assertNotIn("addRow([field('price_pickup')],[field('payment_method')]);", js)
        # notes moved out of the Preventivo section entirely, so it's no longer part
        # of the budget-layout row arrangement.
        self.assertNotIn("addRow([field('notes')]);", js)

    def test_accessory_field_labels_are_renamed(self):
        js = app.APP_JS
        for label in ("Note accessorio", "Tipo secondo accessorio", "Altro accessorio €", "Note altro accessorio"):
            self.assertIn(label, js)
        self.assertNotIn("'Secondo accessorio'", js)
        self.assertNotIn("'Secondi accessori €'", js)

    def test_practice_detail_page_uses_inline_status_dropdown_and_moves_no_whatsapp(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",("CR-STATO","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","200")).lastrowid
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = f"/pratiche/{pid}"
        self.handler.practice(admin, pid)
        page = rendered[-1]
        self.assertNotIn("Stati pratica", page)
        self.assertIn('class="inline-state-select practice-status', page)
        whatsapp_index = page.index("WhatsApp ringraziamento")
        no_msg_index = page.index("NO MESSAGGIO")
        self.assertGreater(no_msg_index, whatsapp_index)
        fattura_index = page.index('class="invoice-inline"')
        metodo_index = page.index("<small>Metodo</small>")
        self.assertGreater(fattura_index, metodo_index)

    def test_practice_created_notification_includes_animal_weight(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        redirects = []
        self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {
            "operator_name": "SERENA", "service_type": "Cremazione collettiva", "destination_branch": "Livorno",
            "owner_first_name": "Anna", "estimated_weight": "7",
        }
        self.handler.create_practice(admin)
        with app.db() as conn:
            notif = conn.execute("SELECT * FROM notifications WHERE type='practice_created' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("⚖️ 7 kg", notif["text"])

    def test_new_practice_from_calendar_event_prefers_client_address_over_vet(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            client_id = conn.execute("""INSERT INTO clients(first_name,last_name,phone,street,city,province,zip,tax_code,created_at,updated_at)
                                         VALUES(?,?,?,?,?,?,?,?,?,?)""",("Anna","Bruni","3331112222","Via Cliente 9","Pisa","PI","56100","BRNANN80A01G702U",stamp,stamp)).lastrowid
            vet_id = conn.execute("""INSERT INTO veterinarians(clinic_name,phone,address,city,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)""",("Clinica Vet","0500000000","Via Veterinario 1","Pisa",stamp,stamp)).lastrowid
            event_id = conn.execute("""INSERT INTO calendar_events(event_type,title,start_at,end_at,client_id,client_first_name,client_last_name,client_phone,
                                        address,zone,veterinarian_id,veterinarian_name,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                     ("Ritiro","Ritiro test","2026-07-20T10:00:00","2026-07-20T11:00:00",client_id,"Anna","Bruni","3331112222","Via Veterinario 1 - Pisa","Pisa",vet_id,"Clinica Vet",admin["id"],stamp,stamp)).lastrowid
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = f"/nuova?calendar_event_id={event_id}"
        self.handler.new_page(admin)
        page = rendered[-1]
        self.assertIn('value="Via Cliente 9"', page)
        self.assertNotIn('value="Via Veterinario 1', page)

    def test_dashboard_greeting_uses_logged_in_user_name_and_drops_quick_action_buttons(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/"
        self.handler.dashboard(admin)
        admin_page = rendered[-1]
        self.assertIn(f"{admin['display_name']} <span", admin_page)
        self.assertNotIn(", Pet Paradise <span", admin_page)
        self.assertNotIn(">+ Nuova pratica<", admin_page)
        self.assertNotIn(">+ Nuovo evento<", admin_page)

        self.handler.dashboard(serena)
        serena_page = rendered[-1]
        self.assertIn(f"{serena['display_name']} <span", serena_page)

    def test_operator_field_is_automatic_for_non_admin_and_manual_for_admin(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()

        admin_form = app.App._fields_html(self.handler, None, admin)
        self.assertIn('name="operator_name" required', admin_form)
        self.assertIn(">SERENA<", admin_form)

        operator_form = app.App._fields_html(self.handler, None, serena)
        self.assertNotIn("Seleziona operatore", operator_form)
        self.assertIn('<input type="hidden" name="operator_name" value="SERENA">', operator_form)

        redirects = []
        self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {"operator_name": "ALESSIO", "service_type": "Cremazione collettiva", "destination_branch": "Livorno"}
        self.handler.create_practice(serena)
        pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            created = conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(created["operator_name"], "SERENA")

        redirects.clear()
        self.handler.form = lambda: {"operator_name": "GIANLUCA", "service_type": "Cremazione collettiva", "destination_branch": "Livorno", "return_to": f"/pratiche/{pid}"}
        self.handler.edit_submit(serena, pid)
        with app.db() as conn:
            edited = conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(edited["operator_name"], "SERENA")

        self.handler.form = lambda: {"updated_at": edited["updated_at"], "changes_json": json.dumps({"operator_name": "FILIPPO", "notes": "Controllo autosave"})}
        responses = []
        self.handler.send_json = lambda obj, status=200: responses.append((obj, status))
        self.handler.practice_autosave(serena, pid)
        with app.db() as conn:
            autosaved = conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(autosaved["operator_name"], "SERENA")
        self.assertEqual(autosaved["notes"], "Controllo autosave")

        redirects.clear()
        self.handler.form = lambda: {"operator_name": "GIANLUCA", "service_type": "Cremazione collettiva", "destination_branch": "Livorno"}
        self.handler.create_practice(admin)
        admin_pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            admin_created = conn.execute("SELECT * FROM practices WHERE id=?", (admin_pid,)).fetchone()
        self.assertEqual(admin_created["operator_name"], "GIANLUCA")

    def test_personal_preferences_are_saved_per_user_and_default_unchanged_otherwise(self):
        with app.db() as conn:
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()
            alessio = conn.execute("SELECT * FROM users WHERE username='alessio'").fetchone()

        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/"
        self.handler.dashboard(alessio)
        default_page = rendered[-1]
        for section_text in ("Pratiche / Ritiri", "Pagamenti", "Ultime 10 pratiche per data recupero"):
            self.assertIn(section_text, default_page)
        self.assertNotIn("light-theme", default_page.split("<body", 1)[1].split(">", 1)[0])

        redirects = []
        self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {
            "theme": "light",
            "return_to": "/il-mio-profilo",
            "dash_show__payments": "1",
            "dash_pos__payments": "0",
            "dash_show__recent_practices": "1",
            "dash_pos__recent_practices": "1",
            "bottom_slot_1": "Calendario",
            "bottom_slot_2": "Dashboard",
            "bottom_slot_3": "Archivio",
        }
        self.handler.save_preferences(serena)
        self.assertEqual(redirects, ["/il-mio-profilo"])

        self.handler.path = "/"
        self.handler.dashboard(serena)
        serena_page = rendered[-1]
        self.assertNotIn("Pratiche / Ritiri", serena_page)
        self.assertNotIn("Entrate anno in corso", serena_page)
        payments_index = serena_page.index('<h2 class="dashboard-heading">Pagamenti</h2>')
        recent_index = serena_page.index("<h2>Ultime 10 pratiche per data recupero</h2>")
        self.assertLess(payments_index, recent_index)
        self.assertIn('class="light-theme"', serena_page.split("<body", 1)[1].split(">", 1)[0])

        self.handler.path = "/"
        self.handler.dashboard(alessio)
        alessio_page = rendered[-1]
        for section_text in ("Pratiche / Ritiri", "Pagamenti", "Ultime 10 pratiche per data recupero"):
            self.assertIn(section_text, alessio_page)
        self.assertNotIn("light-theme", alessio_page.split("<body", 1)[1].split(">", 1)[0])

        with app.db() as conn:
            saved = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM user_preferences WHERE user_id=?", (serena["id"],))}
        self.assertEqual(saved["theme"], "light")
        self.assertEqual(json.loads(saved["dashboard_sections"]), ["payments", "recent_practices"])
        self.assertEqual(json.loads(saved["bottom_nav_slots"]), ["Calendario", "Dashboard", "Archivio"])

    def test_profile_page_renders_password_theme_sidebar_and_notification_sections(self):
        with app.db() as conn:
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.profile_page(serena)
        page = rendered[-1]
        self.assertIn("Il mio profilo", page)
        self.assertIn('href="/imposta-password?return_to=/il-mio-profilo"', page)
        self.assertIn('action="/il-mio-profilo/salva"', page)
        self.assertIn('action="/impostazioni/notifiche"', page)
        self.assertIn("Barra di navigazione mobile", page)

    def test_change_password_voluntary_requires_correct_current_password(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.form = lambda: {"current_password": "sbagliata", "new_password": "altranuova123", "confirm_password": "altranuova123", "return_to": "/impostazioni"}
        self.handler.change_password_submit(admin)
        self.assertIn("Password attuale non corretta.", rendered[-1])
        with app.db() as conn:
            unchanged = conn.execute("SELECT * FROM users WHERE id=?", (admin["id"],)).fetchone()
        self.assertTrue(app.password_ok("petparadise", unchanged["password_hash"]))

    def test_clients_crud_add_edit_delete(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.form = lambda: {"first_name": "Mario", "last_name": "Rossi", "phone": "3331112222", "email": "mario@example.it", "tax_code": "RSSMRA80A01H501U", "city": "Livorno"}
        self.handler.redirect = lambda path: setattr(self, "redirected", path)
        self.handler.save_client(admin)
        client_id = int(self.redirected.rsplit("/", 1)[-1])

        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/clienti"
        self.handler.clients_page(admin)
        self.assertIn("Mario", rendered[-1])
        self.assertIn("Rossi", rendered[-1])

        rendered.clear()
        self.handler.path = f"/clienti/{client_id}"
        self.handler.client_detail(admin, client_id)
        self.assertIn('value="Mario"', rendered[-1])
        self.assertIn("Pratiche collegate", rendered[-1])

        self.handler.form = lambda: {"id": str(client_id), "first_name": "Mario", "last_name": "Verdi", "phone": "3331112222", "city": "Pisa"}
        self.handler.save_client(admin)
        with app.db() as conn:
            updated = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        self.assertEqual(updated["last_name"], "Verdi")
        self.assertEqual(updated["city"], "Pisa")

        self.handler.delete_client(admin, client_id)
        with app.db() as conn:
            deleted = conn.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
        self.assertEqual(deleted["active"], 0)
        rendered.clear()
        self.handler.path = "/clienti"
        self.handler.clients_page(admin)
        self.assertIn("Nessun cliente trovato.", rendered[-1])

    def test_collaborators_crud_and_humanitas_seeded(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            seeded = conn.execute("SELECT * FROM collaborators WHERE UPPER(name)='HUMANITAS CROCE VERDE'").fetchone()
        self.assertIsNotNone(seeded)
        self.assertEqual(seeded["vat_number"], "01762490462")
        self.assertEqual(seeded["sdi_code"], "M5UXCR1")

        self.handler.form = lambda: {"name": "Rifugio Test", "address": "Via Prova 9", "city": "Empoli", "province": "FI", "zip": "50053", "vat_number": "12345678901", "sdi_code": "ABCD123"}
        self.handler.redirect = lambda path: setattr(self, "redirected", path)
        self.handler.save_collaborator(admin)
        collab_id = int(self.redirected.rsplit("/", 1)[-1])

        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/collaboratori"
        self.handler.collaborators_page(admin)
        self.assertIn("Rifugio Test", rendered[-1])
        self.assertIn("HUMANITAS CROCE VERDE", rendered[-1])

        self.handler.form = lambda: {"id": str(collab_id), "name": "Rifugio Test Aggiornato", "city": "Empoli"}
        self.handler.save_collaborator(admin)
        with app.db() as conn:
            updated = conn.execute("SELECT name FROM collaborators WHERE id=?", (collab_id,)).fetchone()
        self.assertEqual(updated["name"], "Rifugio Test Aggiornato")

        self.handler.delete_collaborator(admin, collab_id)
        with app.db() as conn:
            deleted = conn.execute("SELECT active FROM collaborators WHERE id=?", (collab_id,)).fetchone()
        self.assertEqual(deleted["active"], 0)

    def test_collaborator_detail_groups_linked_practices(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            collab_id = conn.execute("INSERT INTO collaborators(name,active,created_at,updated_at) VALUES(?,?,?,?)", ("Canile Amico", 1, stamp, stamp)).lastrowid
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,service_type,payment_status,collaborator_id)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-COLLAB", "Collaboratore", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Rex", "Cane", "Cremazione singola", "Da saldare", collab_id))
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = f"/collaboratori/{collab_id}"
        self.handler.collaborator_detail(admin, collab_id)
        self.assertIn("CR-COLLAB", rendered[-1])
        self.assertIn("Rex", rendered[-1])

    def test_api_collaborators_search_returns_autofill_fields(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        response = {}
        self.handler.path = "/api/collaboratori/search?q=humanitas"
        self.handler.send_json = lambda obj, status=200: response.update(obj=obj, status=status)
        self.handler.api_collaborators_search(admin)
        result = response["obj"]["results"][0]
        self.assertEqual(result["name"], "HUMANITAS CROCE VERDE")
        self.assertEqual(result["vat_number"], "01762490462")
        self.assertEqual(result["sdi_code"], "M5UXCR1")

    def test_practice_form_has_collaborator_search_and_dynamic_sections(self):
        html = self.handler.fields_html()
        self.assertIn('id="collaboratorSearch"', html)
        self.assertIn('id="collaboratorResults"', html)
        self.assertIn('name="collaborator_id"', html)
        self.assertIn('name="owner_sdi"', html)
        self.assertIn('id="originFirstNameBox"', html)
        self.assertIn('id="originLastNameBox"', html)
        self.assertNotIn('id="collaboratorBox"', html)
        self.assertIn("function setupCollaboratorLookup(){", app.APP_JS)
        self.assertIn("function applyRequestOriginMode(){", app.APP_JS)
        self.assertIn("/api/collaboratori/search", app.APP_JS)
        self.assertNotIn("function toggleCollaboratorBox(){", app.APP_JS)

    def test_normalized_fields_handles_collaborator_and_origin_name_fields(self):
        data = self.handler.normalized_fields({
            "collaborator_id": "5", "collaborator_name": "Rifugio Test",
            "owner_sdi": "ABCD123", "origin_first_name": "Anna", "origin_last_name": "Bianchi",
        })
        self.assertEqual(data["collaborator_id"], "5")
        self.assertEqual(data["owner_sdi"], "ABCD123")
        self.assertEqual(data["origin_first_name"], "Anna")
        self.assertEqual(data["origin_last_name"], "Bianchi")
        empty = self.handler.normalized_fields({})
        self.assertIsNone(empty["collaborator_id"])

    def test_sidebar_nav_links_to_clients_and_collaborators_crud(self):
        self.assertTrue(any(href == "/clienti" for href, icon, label in app.SIDEBAR_LINKS))
        self.assertTrue(any(href == "/collaboratori" for href, icon, label in app.SIDEBAR_LINKS))
        self.assertNotIn("/archivio/clienti", [href for href, icon, label in app.SIDEBAR_LINKS])

    def test_tables_get_a_synced_top_scrollbar_on_desktop(self):
        self.assertIn(".tablebox-scroll-top{overflow-x:auto;overflow-y:hidden;height:16px", app.CSS)
        self.assertIn("@media(max-width:900px){.tablebox-scroll-top{display:none}}", app.CSS)
        self.assertIn("function setupTableTopScrollbars(){", app.APP_JS)
        self.assertIn("box.parentNode.insertBefore(topScroll, box);", app.APP_JS)
        self.assertIn("box.scrollLeft=topScroll.scrollLeft", app.APP_JS)
        self.assertIn("topScroll.scrollLeft=box.scrollLeft", app.APP_JS)
        self.assertIn("document.addEventListener('DOMContentLoaded', setupTableTopScrollbars);", app.APP_JS)

    def test_client_search_api_also_returns_matching_collaborators(self):
        with app.db() as conn:
            stamp = app.now()
            conn.execute(
                "INSERT INTO collaborators(name,address,city,province,zip,tax_code,vat_number,sdi_code,phone,email,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("Canile Sperandio", "Via dei Cani 3", "Pisa", "PI", "56100", "", "98765432100", "XYZ999", "0501112222", "canile@example.it", 1, stamp, stamp),
            )
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        response = {}
        self.handler.path = "/api/clienti/search?q=sperandio"
        self.handler.send_json = lambda obj, status=200: response.update(obj=obj, status=status)
        self.handler.api_clients_search(admin)
        results = response["obj"]["results"]
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["kind"], "collaborator")
        self.assertEqual(result["name"], "Canile Sperandio")
        self.assertEqual(result["vat_number"], "98765432100")
        self.assertEqual(result["sdi_code"], "XYZ999")

    def test_client_lookup_js_fills_collaborator_fields_and_clears_client_id(self):
        js = app.APP_JS
        self.assertIn("if(c.kind==='collaborator'){", js)
        self.assertIn("if(clientId) clientId.value='';\n      if(collaboratorId) collaboratorId.value=c.id || '';", js)
        self.assertIn("setField('owner_sdi', c.sdi_code);", js)
        self.assertIn("if(collaboratorId) collaboratorId.value='';\n      if(collaboratorName) collaboratorName.value='';\n      ppmSetCollaboratorTiers([]);\n      if(clientId) clientId.value=c.id || '';", js)

    def test_humanitas_is_seeded_with_code_and_weight_tiers(self):
        with app.db() as conn:
            co = conn.execute("SELECT * FROM collaborators WHERE UPPER(name)='HUMANITAS CROCE VERDE'").fetchone()
            tiers = conn.execute("SELECT * FROM collaborator_price_tiers WHERE collaborator_id=? ORDER BY CAST(weight_min AS REAL)", (co["id"],)).fetchall()
        self.assertEqual(co["code"], "CV")
        self.assertEqual(len(tiers), 5)
        expected = [("0", "1", "146.40"), ("1.1", "10", "183.00"), ("10.1", "25", "244.00"), ("25.1", "45", "305.00"), ("45.1", None, "390.40")]
        for tier, (weight_min, weight_max, price) in zip(tiers, expected):
            self.assertEqual(tier["weight_min"], weight_min)
            self.assertEqual(tier["weight_max"], weight_max)
            self.assertEqual(tier["price"], price)

    def test_collaborator_price_tier_crud(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            collab_id = conn.execute("SELECT id FROM collaborators WHERE UPPER(name)='HUMANITAS CROCE VERDE'").fetchone()["id"]
        self.handler.form = lambda: {"weight_min": "0", "weight_max": "5", "price": "100,50"}
        self.handler.redirect = lambda path: setattr(self, "redirected", path)
        self.handler.save_collaborator_price_tier(admin, collab_id)
        self.assertEqual(self.redirected, f"/collaboratori/{collab_id}")
        with app.db() as conn:
            tier = conn.execute("SELECT * FROM collaborator_price_tiers WHERE collaborator_id=? AND weight_min='0' AND weight_max='5'", (collab_id,)).fetchone()
        self.assertEqual(tier["price"], "100.50")

        self.handler.form = lambda: {"weight_min": "0", "weight_max": "5", "price": "120,00"}
        self.handler.edit_collaborator_price_tier(admin, tier["id"])
        with app.db() as conn:
            updated = conn.execute("SELECT price FROM collaborator_price_tiers WHERE id=?", (tier["id"],)).fetchone()
        self.assertEqual(updated["price"], "120.00")

        self.handler.delete_collaborator_price_tier(admin, tier["id"])
        with app.db() as conn:
            gone = conn.execute("SELECT 1 FROM collaborator_price_tiers WHERE id=?", (tier["id"],)).fetchone()
        self.assertIsNone(gone)

        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = f"/collaboratori/{collab_id}"
        self.handler.collaborator_detail(admin, collab_id)
        self.assertIn("Listino dedicato", rendered[-1])
        self.assertIn('value="146.40"', rendered[-1])

    def test_api_collaborator_price_tiers_endpoint_and_search_includes_tiers(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            collab_id = conn.execute("SELECT id FROM collaborators WHERE UPPER(name)='HUMANITAS CROCE VERDE'").fetchone()["id"]
        response = {}
        self.handler.path = f"/api/collaboratori/{collab_id}/listino"
        self.handler.send_json = lambda obj, status=200: response.update(obj=obj, status=status)
        self.handler.api_collaborator_price_tiers(admin, collab_id)
        self.assertEqual(len(response["obj"]["tiers"]), 5)

        response2 = {}
        self.handler.path = "/api/collaboratori/search?q=humanitas"
        self.handler.send_json = lambda obj, status=200: response2.update(obj=obj, status=status)
        self.handler.api_collaborators_search(admin)
        self.assertEqual(len(response2["obj"]["results"][0]["tiers"]), 5)

        response3 = {}
        self.handler.path = "/api/clienti/search?q=humanitas"
        self.handler.send_json = lambda obj, status=200: response3.update(obj=obj, status=status)
        self.handler.api_clients_search(admin)
        collab_result = next(r for r in response3["obj"]["results"] if r["kind"] == "collaborator")
        self.assertEqual(len(collab_result["tiers"]), 5)

    def test_collaborators_crud_form_has_sigla_field(self):
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/collaboratori"
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.collaborators_page(admin)
        self.assertIn('name="code"', rendered[-1])
        self.assertIn(">CV<", rendered[-1])

    def test_collaborators_add_form_is_collapsed_behind_a_button(self):
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.path = "/collaboratori"
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.collaborators_page(admin)
        page = rendered[-1]
        self.assertIn('<details class="advanced-search"><summary>Aggiungi collaboratore</summary>', page)
        self.assertNotIn('<section class="section"><h2>Aggiungi collaboratore</h2>', page)

    def test_weight_field_triggers_collaborator_price_autofill_js(self):
        js = app.APP_JS
        self.assertIn("function ppmApplyCollaboratorWeightPrice(){", js)
        self.assertIn("weightField.addEventListener('input', ppmApplyCollaboratorWeightPrice);", js)
        self.assertIn("ppmSetCollaboratorTiers(co.tiers);", js)
        self.assertIn("ppmApplyCollaboratorWeightPrice();", js)
        self.assertIn("fetch(`/api/collaboratori/${collaboratorId.value}/listino`", js)

    def test_practice_lists_show_sigla_prefix_and_collaborator_name(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            collab_id = conn.execute("SELECT id FROM collaborators WHERE UPPER(name)='HUMANITAS CROCE VERDE'").fetchone()["id"]
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,service_type,payment_status,collaborator_id,collaborator_name,owner_first_name)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-SIGLA", "Collaboratore", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido", "Cane", "Cremazione singola", "Da saldare", collab_id, "HUMANITAS CROCE VERDE", "HUMANITAS CROCE VERDE"))
            rows = conn.execute("SELECT * FROM practices WHERE practice_number='CR-SIGLA'").fetchall()
        self.handler.path = "/archivio/pratiche"
        html = self.handler.practice_rows(rows)
        self.assertIn("CV Fido", html)
        self.assertIn("HUMANITAS CROCE VERDE", html)


if __name__ == "__main__":
    unittest.main()
