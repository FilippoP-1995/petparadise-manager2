import os
import io
import json
import re
import socket
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import app
import email_service
import notification_service
from notification_service import (
    emit_notification, process_scheduled_notifications,
    process_daily_summaries, archive_old_notifications, notification_priority,
)
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
        # Filling in an urn item with a real choice must clear send_catalog too, in the
        # same autosave write — not just in the in-memory normalization.
        urna_items = json.dumps([{"label": "Urna in legno chiaro", "price": "50"}])
        self.handler.form = lambda: {"updated_at": stamp, "changes_json": json.dumps({"urna_items_json": urna_items})}
        self.handler.practice_autosave(admin, pid)
        self.assertEqual(captured[-1][1], 200)
        self.assertIn("send_catalog", captured[-1][0]["saved_fields"])
        with app.db() as conn:
            row = conn.execute("SELECT send_catalog,catalog_sent FROM practices WHERE id=?", (pid,)).fetchone()
            items = conn.execute("SELECT label,price FROM practice_items WHERE practice_id=? AND category='urna'", (pid,)).fetchall()
        self.assertEqual([dict(i) for i in items], [{"label": "Urna in legno chiaro", "price": "50"}])
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
        for expected in ("GIANLUCA", "CALCO PER URNA", "CALCO POLPASTRELLO", "CALCO NASO", 'data-practice-list="calco"', "Fiat Fiorino", "Renault Captur", "Dr PK8", "Cremato", "Smaltito"):
            self.assertIn(expected, html)
        data = self.handler.normalized_fields({"owner_tax_code": "rssmra80a01h501u", "service_type": "Da decidere"})
        self.assertEqual(data["owner_tax_code"], "RSSMRA80A01H501U")
        extras = self.handler.normalized_fields({"price_paw_cast":"25,50", "price_nose_cast":"30", "tag_calco_paw":"Si", "tag_calco_nose":"Si"})
        self.assertEqual(extras["price_paw_cast"], "25.50")
        self.assertEqual(extras["tag_calco_nose"], "Si")

    def test_calco_items_are_unlimited_and_possibile_tags_normalize(self):
        html = self.handler.fields_html()
        for expected in (
            "POSSIBILE ASSISTITA STREAMING", "POSSIBILE CALCO",
            "POSSIBILE CALCO POLPASTRELLO", "POSSIBILE CALCO NASO",
            'name="tag_possibile_assistita_streaming"', 'name="tag_possibile_calco"',
            'name="tag_possibile_calco_paw"', 'name="tag_possibile_calco_nose"',
            '+ Aggiungi calco', 'data-practice-list="calco"',
        ):
            self.assertIn(expected, html)
        # calco items are a dynamic, uncapped list (not fixed price_paw_cast_2/_3/_4
        # slots any more) — the client-side row config carries the two named
        # subtypes plus a generic option, with no limit on how many rows exist.
        self.assertIn('["polpastrello","Polpastrello"]', app.APP_JS)
        self.assertIn('["naso","Naso"]', app.APP_JS)
        data = self.handler.normalized_fields({
            "tag_possibile_assistita_streaming": "Si", "tag_possibile_calco": "Si",
            "tag_possibile_calco_paw": "Si", "tag_possibile_calco_nose": "Si",
        })
        self.assertEqual(data["tag_possibile_assistita_streaming"], "Si")
        self.assertEqual(data["tag_possibile_calco"], "Si")
        self.assertEqual(data["tag_possibile_calco_paw"], "Si")
        self.assertEqual(data["tag_possibile_calco_nose"], "Si")
        empty = self.handler.normalized_fields({})
        for key in ("tag_possibile_assistita_streaming", "tag_possibile_calco", "tag_possibile_calco_paw", "tag_possibile_calco_nose"):
            self.assertEqual(empty[key], "")
        # five calco items (well beyond the old 2-generic/4-naso/4-polpastrello caps)
        items = app.parse_practice_items(json.dumps([
            {"subtype": "polpastrello", "label": "Argento", "price": "20"},
            {"subtype": "polpastrello", "label": "Oro", "price": "30"},
            {"subtype": "naso", "label": "Bronzo", "price": "15"},
            {"subtype": "naso", "label": "Altro naso", "price": "10"},
            {"subtype": "", "label": "Calco generico estra", "price": "5"},
        ]), "calco")
        self.assertEqual(len(items), 5)

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

    def test_accessorio_items_are_unlimited_with_fixed_subtype_choices(self):
        html = self.handler.fields_html()
        self.assertIn('data-practice-list="accessorio"', html)
        self.assertIn('+ Aggiungi accessorio', html)
        # subtype choices are still the same fixed set as before, just applied
        # per-row instead of to two hardcoded accessory_type/accessory_type_2 fields
        self.assertIn('["Altro","Altro"],["Collana","Collana"],["Braccialetto","Braccialetto"],["Calco inchiostro","Calco inchiostro"]', app.APP_JS)
        # an invalid/unknown subtype falls back to "Altro"; a valid one and a free
        # label/price both pass through untouched, and the list has no item cap
        items = app.parse_practice_items(json.dumps([
            {"subtype": "Collana", "label": "Nome inciso", "price": "40"},
            {"subtype": "Braccialetto", "label": "Altro testo", "price": "25"},
            {"subtype": "Qualcosa di strano", "label": "Extra", "price": "5"},
        ]), "accessorio")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["subtype"], "Collana")
        self.assertEqual(items[0]["label"], "Nome inciso")
        self.assertEqual(items[2]["subtype"], "Altro")

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
        # Placeholder/undecided urn text must NOT count as a real urn choice.
        for placeholder in ("", "/", "Da decidere", "da decidere", "  /  "):
            items=[{"subtype":"","urn_catalog_id":None,"label":placeholder,"price":"0"}]
            has_urn=app.practice_has_real_urn_item(items)
            self.assertFalse(has_urn, f"placeholder {placeholder!r} should not count as a real urn item")
            data = self.handler.normalized_fields({"send_catalog": "Si"}, has_urn_item=has_urn)
            self.assertEqual(data["send_catalog"], "Si", f"placeholder {placeholder!r} should not clear send_catalog")
        # A real free-text urn choice clears both checkboxes.
        self.assertTrue(app.practice_has_real_urn_item([{"subtype":"","urn_catalog_id":None,"label":"Urna in legno chiaro","price":"0"}]))
        data = self.handler.normalized_fields({"send_catalog": "Si", "catalog_sent": "Si"}, has_urn_item=True)
        self.assertEqual(data["send_catalog"], "")
        self.assertEqual(data["catalog_sent"], "")
        # Selecting a catalog urn (urn_catalog_id set) also counts, even with empty label.
        self.assertTrue(app.practice_has_real_urn_item([{"subtype":"","urn_catalog_id":5,"label":"","price":"0"}]))

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

    def test_d_circuito_acconto_shows_correctly_in_riepilogo_dashboard_and_archive(self):
        # Regression test for a reported bug: a practice created with Totale
        # D=350, Acconto D=100 (Rimanenza D=250 correctly computed at
        # creation) later showed Acconto D=0,00 and Rimanenza D=350,00
        # everywhere (Riepilogo, Dashboard, Archivio), because those pages
        # always read the W columns (deposit/remaining_balance) instead of
        # the D ones (deposit_final/remaining_final) whenever the practice
        # actually uses circuito D. This is the exact scenario from the bug
        # report (Mulan, CR-000075), reproduced with a fresh test practice.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,service_type,payment_status,price_cremation,total_service,total_text,deposit,deposit_final,remaining_balance,remaining_final)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-ACCD", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Mulan",
                 "Cremazione singola", "Acconto", "350", "350", "350", "0.00", "100", "0.00", "250.00"),
            ).lastrowid
        rendered = []; self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.practice(admin, pid)
        page = rendered[-1]
        self.assertIn('<small>Acconto D</small><b>€ 100,00</b>', page)
        self.assertIn('<small>Rimanenza D</small><b>€ 250,00</b>', page)
        self.handler.path = "/"
        self.handler.dashboard(admin)
        dashboard_page = rendered[-1]
        self.assertIn('<small>Acconto D</small><br>€ 100,00', dashboard_page)
        self.assertIn('<small>Rimanenza D</small><br>€ 250,00', dashboard_page)
        self.handler.path = "/archivio/pratiche"
        self.handler.archive(admin)
        archive_page = rendered[-1]
        self.assertIn('<small>Acconto D</small><br>€ 100,00', archive_page)
        self.assertIn('<small>Rimanenza D</small><br>€ 250,00', archive_page)
        self.assertIn('<th>Acconto</th><th>Rimanenza</th>', archive_page)

    def test_w_circuito_acconto_still_shows_correctly_after_the_fix(self):
        # Same scenario but on circuito W, to confirm the fix did not change
        # already-correct W behaviour.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,service_type,payment_status,price_cremation,total_service,deposit,remaining_balance)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-ACCW", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Rex",
                 "Cremazione singola", "Acconto", "350", "350", "100", "250.00"),
            ).lastrowid
        rendered = []; self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.practice(admin, pid)
        page = rendered[-1]
        self.assertIn('<small>Acconto W</small><b>€ 100,00</b>', page)
        self.assertIn('<small>Rimanenza W</small><b>€ 250,00</b>', page)
        self.handler.path = "/archivio/pratiche"
        self.handler.archive(admin)
        archive_page = rendered[-1]
        self.assertIn('<small>Acconto W</small><br>€ 100,00', archive_page)
        self.assertIn('<small>Rimanenza W</small><br>€ 250,00', archive_page)

    def test_payment_macroarea_d_circuito_updates_deposit_final_not_deposit(self):
        # Regression test for a related write-side bug: registering an
        # acconto with circuito D through the Pagamento popover always wrote
        # the amount into the W columns (deposit/remaining_balance), never
        # into deposit_final/remaining_final, silently corrupting a
        # D-circuito practice's acconto every time the popover was used.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,service_type,payment_status,total_text)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-ACCD-POP", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Mulan",
                 "Cremazione singola", "Da saldare", "350"),
            ).lastrowid
        self.handler.form = lambda: {"macroarea": "acconto", "acconto_data": "2026-07-20", "acconto_totale": "100,00", "acconto_circuito": "D", "acconto_modalita": "Contanti"}
        self.handler.headers = {}
        redirects = []; self.handler.redirect = lambda url: redirects.append(url)
        self.handler.save_payment_macroarea(admin, pid)
        with app.db() as conn:
            row = conn.execute("SELECT payment_status,deposit,remaining_balance,deposit_final,remaining_final FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["payment_status"], "Acconto")
        self.assertEqual((row["deposit_final"], row["remaining_final"]), ("100.00", "250.00"))
        self.assertEqual((row["deposit"], row["remaining_balance"]), (None, None))

    def test_payment_macroarea_d_circuito_does_not_require_payment_method(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,service_type,payment_status,total_text)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-ACCD-NOMETODO", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Mulan",
                 "Cremazione singola", "Da saldare", "350"),
            ).lastrowid
        self.handler.form = lambda: {"macroarea": "acconto", "acconto_data": "2026-07-20", "acconto_totale": "100,00", "acconto_circuito": "D", "acconto_modalita": ""}
        self.handler.headers = {}
        redirects = []; self.handler.redirect = lambda url: redirects.append(url)
        self.handler.save_payment_macroarea(admin, pid)
        self.assertTrue(redirects, "il salvataggio non deve fallire per metodo di pagamento mancante quando il circuito e' D")
        with app.db() as conn:
            row = conn.execute("SELECT payment_status,deposit_final FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["payment_status"], "Acconto")
        self.assertEqual(row["deposit_final"], "100.00")

    def test_payment_macroarea_w_circuito_still_requires_payment_method(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,service_type,payment_status,price_cremation,total_service)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-ACCW-NOMETODO", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Rex",
                 "Cremazione singola", "Da saldare", "200", "200"),
            ).lastrowid
        self.handler.form = lambda: {"macroarea": "acconto", "acconto_data": "2026-07-20", "acconto_totale": "100,00", "acconto_circuito": "W", "acconto_modalita": ""}
        self.handler.headers = {}
        rendered_error = []
        self.handler.practice = lambda user, pid, error="": rendered_error.append(error)
        self.handler.save_payment_macroarea(admin, pid)
        self.assertIn("Seleziona il metodo di pagamento.", rendered_error)

    def test_create_practice_with_total_d_does_not_require_payment_method(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        redirects = []; self.handler.redirect = lambda url: redirects.append(url)
        self.handler.form = lambda: {
            "operator_name": "SERENA", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "total_text": "350", "acconto_d_totale": "100,00", "acconto_d_data": "2026-07-20", "acconto_d_modalita": "",
        }
        self.handler.create_practice(admin)
        self.assertTrue(redirects, "la creazione con incasso D non deve richiedere il metodo di pagamento")
        pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            row = conn.execute("SELECT payment_status,deposit_final FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["payment_status"], "Acconto")
        self.assertEqual(row["deposit_final"], "100.00")

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

    def test_login_sets_a_session_cookie_that_survives_backgrounding(self):
        # A cookie with no Max-Age/Expires is a plain session cookie, and iOS
        # Safari (especially the installed PWA) can purge those under memory
        # pressure or after the app sits backgrounded — silently logging the
        # user out mid-flow with no visible explanation (a tap on a confirm
        # button lands on a 303 to /login instead of doing anything). The
        # sessions table row itself never expires server-side, so the cookie
        # must not either.
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        sent={}
        self.handler.send_response=lambda status:sent.update(status=status)
        self.handler.send_header=lambda k,v:sent.setdefault("headers",[]).append((k,v))
        self.handler.end_headers=lambda:None
        self.handler.form=lambda:{"username":admin["username"],"password":"petparadise"}
        self.handler.login_submit()
        self.assertEqual(sent["status"],303)
        cookie_header=next(v for k,v in sent["headers"] if k=="Set-Cookie")
        self.assertIn("Max-Age=",cookie_header)
        max_age=int(cookie_header.split("Max-Age=")[1].split(";")[0])
        self.assertGreaterEqual(max_age,86400*30)

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

        partial = dict(whisky, deposit_final="100", payment_status="Acconto")
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
        url = f"/pratiche/{pid}?return_to={quote(self.handler.path,safe='')}"
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


    def test_cremation_schedule_lists_waiting_animals_with_urn_tags_provenance_and_duplicate_name_surname(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            urn_id = conn.execute(
                "INSERT INTO urns(name,price,quantity,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("Cornice Bianca", "80", 5, 1, stamp, stamp),
            ).lastrowid

            def practice(code, status, service_type, pickup, provenance="", weight="", urn=None,
                         send_catalog="", tag_avvisare="", urn_notes="", owner_first="", owner_last="", animal_name=None):
                pid = conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                       pickup_date,created_at,updated_at,created_by,animal_name,estimated_weight,provenance,
                       send_catalog,tag_avvisare,owner_first_name,owner_last_name)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, "Privato", "Livorno", status, service_type, pickup, stamp, stamp, admin["id"],
                     animal_name or code, weight, provenance, send_catalog, tag_avvisare, owner_first, owner_last),
                ).lastrowid
                # urn/urn_notes are now practice_items rows (category='urna'), not columns;
                # "Cornice Bianca" mirrors what resolve_practice_items would snapshot from the catalog.
                if urn:
                    conn.execute(
                        "INSERT INTO practice_items(practice_id,category,subtype,urn_catalog_id,label,price,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (pid, "urna", "", urn, "Cornice Bianca", "80", 0, stamp, stamp),
                    )
                elif urn_notes:
                    conn.execute(
                        "INSERT INTO practice_items(practice_id,category,subtype,urn_catalog_id,label,price,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (pid, "urna", "", None, urn_notes, "0", 0, stamp, stamp),
                    )
                return pid

            newer_id = practice("CR-CREM-NEW", "Ritirato", "Cremazione singola", "2026-07-16", provenance="E",
                                 owner_first="Anna", owner_last="Verdi", animal_name="Rex")
            older_id = practice("CR-CREM-OLD", "Ritirato", "Cremazione singola", "2026-07-14", provenance="L",
                                 weight="8", urn=urn_id, animal_name="Nuvola")
            catalog_id = practice("CR-CREM-CAT", "Ritirato", "Cremazione singola", "2026-07-15",
                                   send_catalog="Si", tag_avvisare="Si", animal_name="Birba")
            freetext_id = practice("CR-CREM-FREETEXT", "Ritirato", "Cremazione singola", "2026-07-17",
                                    urn_notes="Urna scelta a voce, non ancora in catalogo", animal_name="Luna")
            # same animal name twice -> the owner's surname must be shown to disambiguate
            dup_a = practice("CR-CREM-DUP1", "Ritirato", "Cremazione singola", "2026-07-13", owner_last="Rossi", animal_name="Rex")
            practice("CR-CREM-COLLETTIVA", "Ritirato", "Cremazione collettiva", "2026-07-10")
            practice("CR-CREM-DONE", "Cremato", "Cremazione singola", "2026-07-10")

        rendered = []
        self.handler.path = "/programma-cremazioni"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]
        self.assertNotIn("CR-CREM-COLLETTIVA", page)
        self.assertNotIn("CR-CREM-DONE", page)
        for pid in (newer_id, older_id, catalog_id, freetext_id, dup_a):
            self.assertIn(f'/pratiche/{pid}', page)
        # sorted by pickup date ascending
        self.assertLess(page.index(f'/pratiche/{dup_a}'), page.index(f'/pratiche/{older_id}'))
        self.assertLess(page.index(f'/pratiche/{older_id}'), page.index(f'/pratiche/{catalog_id}'))
        self.assertLess(page.index(f'/pratiche/{catalog_id}'), page.index(f'/pratiche/{newer_id}'))
        self.assertLess(page.index(f'/pratiche/{newer_id}'), page.index(f'/pratiche/{freetext_id}'))
        self.assertIn("Cornice Bianca", page)
        self.assertIn("INVIARE CATALOGO", page)
        self.assertIn("AVVISARE", page)
        self.assertIn('<span class="cremation-provenance-chip avatar-other">L</span>', page)
        self.assertIn('<span class="cremation-provenance-chip avatar-other">E</span>', page)
        self.assertIn("Urna scelta a voce, non ancora in catalogo", page)  # unmatched free-typed urn text still shown
        self.assertIn("(Rossi)", page)  # duplicate "Rex" disambiguated by owner surname
        self.assertIn("(Verdi)", page)  # the other "Rex" too
        # the standalone "Animali in attesa" column is gone: the same waiting cards now
        # live in a panel toggled by the "In attesa" stat card (no separate drop-to-create-cycle zone)
        self.assertIn('id="cremationWaitingPanel"', page)
        self.assertIn('cremationToggleWaitingPanel(this)', page)
        self.assertNotIn('data-cycle-dropzone="new"', page)

    def test_cremation_create_and_assign_to_cycle_enforce_two_animal_limit_and_promote_status(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()

            def practice(code):
                return conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                       pickup_date,created_at,updated_at,created_by,animal_name) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (code, "Privato", "Livorno", "Ritirato", "Cremazione singola", "2026-07-15", stamp, stamp,
                     admin["id"], code),
                ).lastrowid

            first_id = practice("CR-CYC-1")
            second_id = practice("CR-CYC-2")
            third_id = practice("CR-CYC-3")

        responses = []
        self.handler.send_json = lambda payload, status=200: responses.append((payload, status))
        self.handler.form = lambda: {"data": "2026-07-20"}
        self.handler.cremation_create_cycle(admin)
        self.assertTrue(responses[-1][0]["ok"])
        cycle_id = responses[-1][0]["cycle_id"]
        with app.db() as conn:
            cycle = conn.execute("SELECT * FROM cremation_cycles WHERE id=?", (cycle_id,)).fetchone()
        self.assertEqual(cycle["status"], "pianificato")
        self.assertEqual(cycle["planned_start"], "08:00")
        self.assertEqual(cycle["planned_end"], "09:30")

        responses.clear()
        self.handler.form = lambda: {"practice_id": str(first_id)}
        self.handler.cremation_assign_to_cycle(admin, cycle_id)
        self.assertEqual(responses[-1], ({"ok": True}, 200))
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT status FROM cremation_cycles WHERE id=?", (cycle_id,)).fetchone()["status"], "in_attesa")
            first_practice = conn.execute("SELECT cremation_cycle_id,status FROM practices WHERE id=?", (first_id,)).fetchone()
            self.assertEqual(first_practice["cremation_cycle_id"], cycle_id)
            # assigning to a cycle immediately advances the practice: Ritirato -> In programma
            self.assertEqual(first_practice["status"], "In programma")
            history = conn.execute(
                "SELECT old_value,new_value FROM practice_history WHERE practice_id=? ORDER BY id DESC LIMIT 1", (first_id,)
            ).fetchone()
            self.assertEqual((history["old_value"], history["new_value"]), ("Ritirato", "In programma"))

        responses.clear()
        self.handler.form = lambda: {"practice_id": str(second_id)}
        self.handler.cremation_assign_to_cycle(admin, cycle_id)
        self.assertEqual(responses[-1], ({"ok": True}, 200))

        # a third animal must be rejected: a cycle holds at most 2 independent animals
        responses.clear()
        self.handler.form = lambda: {"practice_id": str(third_id)}
        self.handler.cremation_assign_to_cycle(admin, cycle_id)
        payload, status = responses[-1]
        self.assertFalse(payload["ok"])
        self.assertEqual(status, 409)
        with app.db() as conn:
            self.assertIsNone(conn.execute("SELECT cremation_cycle_id FROM practices WHERE id=?", (third_id,)).fetchone()["cremation_cycle_id"])

        # a second cycle the same day auto-schedules right after the first one's end (+ gap)
        responses.clear()
        self.handler.form = lambda: {"data": "2026-07-20"}
        self.handler.cremation_create_cycle(admin)
        second_cycle_id = responses[-1][0]["cycle_id"]
        with app.db() as conn:
            second_cycle = conn.execute("SELECT * FROM cremation_cycles WHERE id=?", (second_cycle_id,)).fetchone()
        self.assertEqual(second_cycle["planned_start"], "09:40")
        self.assertEqual(second_cycle["planned_end"], "11:10")

    def test_cremation_start_and_complete_cycle_moves_animals_to_da_consegnare(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            cycle_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-20", "in_attesa", "08:00", "09:30", stamp, stamp),
            ).lastrowid
            # already assigned to the cycle (as cremation_assign_to_cycle would have left it): In programma
            assigned_id = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                   pickup_date,created_at,updated_at,created_by,animal_name,cremation_cycle_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-INSERITO", "Privato", "Livorno", "In programma", "Cremazione singola", "2026-07-15", stamp, stamp,
                 admin["id"], "CR-INSERITO", cycle_id),
            ).lastrowid

        responses = []
        self.handler.send_json = lambda payload, status=200: responses.append((payload, status))
        # cannot terminate a cycle that hasn't started
        self.handler.cremation_complete_cycle(admin, cycle_id)
        self.assertFalse(responses[-1][0]["ok"])

        responses.clear()
        self.handler.cremation_start_cycle(admin, cycle_id)
        self.assertEqual(responses[-1], ({"ok": True}, 200))
        with app.db() as conn:
            row = conn.execute("SELECT status,actual_start FROM cremation_cycles WHERE id=?", (cycle_id,)).fetchone()
        self.assertEqual(row["status"], "in_corso")
        self.assertIsNotNone(row["actual_start"])

        responses.clear()
        self.handler.cremation_complete_cycle(admin, cycle_id)
        self.assertEqual(responses[-1], ({"ok": True}, 200))
        with app.db() as conn:
            cycle = conn.execute("SELECT status,actual_end FROM cremation_cycles WHERE id=?", (cycle_id,)).fetchone()
            practice = conn.execute("SELECT status,cremation_registered FROM practices WHERE id=?", (assigned_id,)).fetchone()
            history = conn.execute(
                "SELECT event_type,old_value,new_value FROM practice_history WHERE practice_id=? ORDER BY id DESC LIMIT 1", (assigned_id,)
            ).fetchone()
        self.assertEqual(cycle["status"], "completato")
        self.assertIsNotNone(cycle["actual_end"])
        # correct flow: In programma -> Da consegnare (never back to "In programma")
        self.assertEqual((practice["status"], practice["cremation_registered"]), ("Da consegnare", "Si"))
        self.assertEqual((history["event_type"], history["old_value"], history["new_value"]), ("Cambio stato rapido", "In programma", "Da consegnare"))

        # the completed cycle keeps showing on the timeline for that day
        rendered = []
        self.handler.path = "/programma-cremazioni?data=2026-07-20"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]
        self.assertIn("COMPLETATO", page)

    def test_cremation_complete_cycle_also_promotes_animals_stuck_at_ritirato(self):
        # regression: an animal attached to a cycle whose status was never bumped to
        # "In programma" (e.g. legacy data) must still move to "Da consegnare" on completion
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            cycle_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-21", "in_attesa", "08:00", "09:30", stamp, stamp),
            ).lastrowid
            stuck_id = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                   pickup_date,created_at,updated_at,created_by,animal_name,cremation_cycle_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-STUCK", "Privato", "Livorno", "Ritirato", "Cremazione singola", "2026-07-15", stamp, stamp,
                 admin["id"], "CR-STUCK", cycle_id),
            ).lastrowid

        responses = []
        self.handler.send_json = lambda payload, status=200: responses.append((payload, status))
        self.handler.cremation_start_cycle(admin, cycle_id)
        self.assertEqual(responses[-1], ({"ok": True}, 200))

        responses.clear()
        self.handler.cremation_complete_cycle(admin, cycle_id)
        self.assertEqual(responses[-1], ({"ok": True}, 200))
        with app.db() as conn:
            practice = conn.execute("SELECT status,cremation_registered FROM practices WHERE id=?", (stuck_id,)).fetchone()
            history = conn.execute(
                "SELECT event_type,old_value,new_value FROM practice_history WHERE practice_id=? ORDER BY id DESC LIMIT 1", (stuck_id,)
            ).fetchone()
        self.assertEqual((practice["status"], practice["cremation_registered"]), ("Da consegnare", "Si"))
        self.assertEqual((history["event_type"], history["old_value"], history["new_value"]), ("Cambio stato rapido", "Ritirato", "Da consegnare"))

    def test_cremation_remove_from_cycle_reverts_status_to_ritirato(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            cycle_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-20", "in_attesa", "08:00", "09:30", stamp, stamp),
            ).lastrowid
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                   pickup_date,created_at,updated_at,created_by,animal_name,cremation_cycle_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-BACK", "Privato", "Livorno", "In programma", "Cremazione singola", "2026-07-15", stamp, stamp,
                 admin["id"], "CR-BACK", cycle_id),
            ).lastrowid

        responses = []
        self.handler.send_json = lambda payload, status=200: responses.append((payload, status))
        self.handler.cremation_remove_from_cycle(admin, pid)
        self.assertEqual(responses[-1], ({"ok": True}, 200))
        with app.db() as conn:
            practice = conn.execute("SELECT status,cremation_cycle_id FROM practices WHERE id=?", (pid,)).fetchone()
            history = conn.execute(
                "SELECT old_value,new_value FROM practice_history WHERE practice_id=? ORDER BY id DESC LIMIT 1", (pid,)
            ).fetchone()
        self.assertIsNone(practice["cremation_cycle_id"])
        self.assertEqual(practice["status"], "Ritirato")
        self.assertEqual((history["old_value"], history["new_value"]), ("In programma", "Ritirato"))

    def test_cremation_edit_cycle_cascades_to_subsequent_overlapping_cycles(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            first_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-21", "pianificato", "08:00", "09:30", stamp, stamp),
            ).lastrowid
            second_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-21", "pianificato", "09:40", "11:10", stamp, stamp),
            ).lastrowid
            # far enough away that it must NOT be touched by the cascade
            third_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-21", "pianificato", "15:00", "16:30", stamp, stamp),
            ).lastrowid

        responses = []
        self.handler.send_json = lambda payload, status=200: responses.append((payload, status))
        # stretching the first cycle's end to 10:30 now overlaps the second one (09:40-11:10)
        self.handler.form = lambda: {"planned_start": "08:00", "planned_end": "10:30"}
        self.handler.cremation_edit_cycle(admin, first_id)
        self.assertEqual(responses[-1], ({"ok": True}, 200))
        with app.db() as conn:
            first = conn.execute("SELECT planned_start,planned_end FROM cremation_cycles WHERE id=?", (first_id,)).fetchone()
            second = conn.execute("SELECT planned_start,planned_end FROM cremation_cycles WHERE id=?", (second_id,)).fetchone()
            third = conn.execute("SELECT planned_start,planned_end FROM cremation_cycles WHERE id=?", (third_id,)).fetchone()
        self.assertEqual((first["planned_start"], first["planned_end"]), ("08:00", "10:30"))
        # pushed forward by the gap, keeping its own original 90-minute duration
        self.assertEqual((second["planned_start"], second["planned_end"]), ("10:40", "12:10"))
        # untouched: still well after the cascade
        self.assertEqual((third["planned_start"], third["planned_end"]), ("15:00", "16:30"))

    def test_cremation_edit_cycle_updates_planned_times_for_a_planned_cycle(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            cycle_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-20", "pianificato", "08:00", "09:30", stamp, stamp),
            ).lastrowid

        responses = []
        self.handler.send_json = lambda payload, status=200: responses.append((payload, status))
        self.handler.form = lambda: {"planned_start": "10:00", "planned_end": "11:15"}
        self.handler.cremation_edit_cycle(admin, cycle_id)
        self.assertEqual(responses[-1], ({"ok": True}, 200))
        with app.db() as conn:
            row = conn.execute("SELECT planned_start,planned_end FROM cremation_cycles WHERE id=?", (cycle_id,)).fetchone()
        self.assertEqual((row["planned_start"], row["planned_end"]), ("10:00", "11:15"))

    def test_cremation_schedule_offers_quick_insert_and_add_animal_menus_without_drag_and_drop(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()

            def practice(code):
                return conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                       pickup_date,created_at,updated_at,created_by,animal_name) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (code, "Privato", "Livorno", "Ritirato", "Cremazione singola", "2026-07-20", stamp, stamp,
                     admin["id"], code),
                ).lastrowid

            waiting_a = practice("CR-WAIT-A")
            waiting_b = practice("CR-WAIT-B")

            pianificato_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-20", "pianificato", "08:00", "09:30", stamp, stamp),
            ).lastrowid
            in_attesa_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-20", "in_attesa", "09:40", "11:10", stamp, stamp),
            ).lastrowid
            assigned_id = practice("CR-ASSIGNED")
            conn.execute("UPDATE practices SET cremation_cycle_id=? WHERE id=?", (in_attesa_id, assigned_id))
            completato_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,actual_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("2026-07-20", "completato", "07:00", "08:00", stamp, stamp, stamp),
            ).lastrowid

        rendered = []
        self.handler.path = "/programma-cremazioni?data=2026-07-20"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]

        # each waiting card offers a no-drag "+ Inserisci" menu: existing eligible cycles + create-new
        self.assertIn(f'cremationQuickAssign(this,{waiting_a},{in_attesa_id})', page)
        self.assertIn(f'cremationQuickAssign(this,{waiting_b},{in_attesa_id})', page)
        self.assertIn(f'cremationQuickCreateAndAssign(this,{waiting_a})', page)
        # the pianificato cycle (0 animals) is offered too, the completed one never is
        self.assertIn(f'cremationQuickAssign(this,{waiting_a},{pianificato_id})', page)
        self.assertNotIn(f'cremationQuickAssign(this,{waiting_a},{completato_id})', page)

        # the in_attesa cycle (1/2 animals) offers a "+ Aggiungi animale" button opening the shared modal
        self.assertIn(f'cremationOpenAddAnimalModal({in_attesa_id})', page)
        self.assertIn(f'cremationOpenAddAnimalModal({pianificato_id})', page)
        self.assertIn('Aggiungi animale', page)

        # every non-completed cycle exposes Modifica (via the new time-picker modal, not the old prompt()) together with its other actions
        self.assertIn(f"cremationOpenEditModal({pianificato_id},'08:00','09:30')", page)
        self.assertIn(f"cremationOpenEditModal({in_attesa_id},'09:40','11:10')", page)
        self.assertNotIn("cremationEditCycle(", page)
        # pianificato (no animal yet) must not offer "Avvia ciclo"; in_attesa must
        pianificato_card = page[page.index(f'cremationOpenEditModal({pianificato_id}'):page.index(f'cremationOpenEditModal({in_attesa_id}')]
        self.assertNotIn('cremationStartCycle', pianificato_card)
        self.assertIn(f'cremationStartCycle({in_attesa_id})', page)

        # the shared edit modal (reusing the calendar event time-picker widget) is rendered once, hidden
        self.assertEqual(page.count('id="cremationEditOverlay"'), 1)
        self.assertIn('cremationEditOverlay" hidden', page)
        self.assertIn('calendar-time-entry', page)
        self.assertIn('calendar-wheel-option', page)

        # the shared "add animal" modal is a real search + card picker (not a tiny dropdown), rendered once
        self.assertEqual(page.count('id="cremationAddAnimalOverlay"'), 1)
        self.assertIn('cremationAddAnimalOverlay" hidden', page)
        self.assertIn('id="cremationAddAnimalSearch"', page)
        self.assertIn('cremationFilterAddAnimalList(this)', page)
        self.assertIn(f"cremationAddAnimalConfirm(this,{waiting_a})", page)
        self.assertIn(f"cremationAddAnimalConfirm(this,{waiting_b})", page)
        self.assertIn('cremation-add-animal-btn">Aggiungi al ciclo', page)

        # every cycle card is collapsed by default and toggled by clicking its header
        self.assertIn('data-cycle-card', page)
        self.assertIn('cremationToggleCycleCard(this)', page)
        self.assertIn('data-cycle-body', page)
        # animal names stay visible even collapsed
        self.assertIn('cremation-cycle-animal-names', page)

        # a completed cycle can still have its time modified: only Avvia/Termina/Aggiungi animale go away
        self.assertIn(f"cremationOpenEditModal({completato_id},'07:00','08:00')", page)

        # the standalone "Animali in attesa" section is gone; the same waiting cards now live in a
        # panel toggled by the "In attesa" stat card, which shows the count of unassigned animals (2), not cycles
        self.assertNotIn('cremation-waiting-column', page)
        stat_start = page.index('cremationToggleWaitingPanel(this)')
        waiting_stat_card = page[stat_start:stat_start + 700]
        self.assertIn('>In attesa</span>', waiting_stat_card)
        self.assertIn('<strong class="dash-stat-value">2</strong>', waiting_stat_card)
        self.assertIn('id="cremationWaitingPanel"', page)
        self.assertIn(f'data-practice-id="{waiting_a}"', page)
        self.assertIn(f'data-practice-id="{waiting_b}"', page)

        # "Termina ciclo" and "Rimuovi animale" use the shared styled modal, not the native browser confirm()
        self.assertEqual(page.count('id="cremationConfirmOverlay"'), 1)
        self.assertIn('cremationConfirmOverlay" hidden', page)
        self.assertIn('cremationOpenConfirmModal(', page)
        self.assertNotIn("confirm('Confermi il completamento", page)
        self.assertNotIn('confirm(\'Rimuovere questo animale', page)
        self.assertIn('Da consegnare.', page)

    def test_cremation_remove_from_cycle_returns_animal_to_waiting_list_and_reverts_empty_cycle(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            cycle_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("2026-07-20", "in_attesa", "08:00", "09:30", stamp, stamp),
            ).lastrowid
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                   pickup_date,created_at,updated_at,created_by,animal_name,cremation_cycle_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-REMOVE", "Privato", "Livorno", "Ritirato", "Cremazione singola", "2026-07-15", stamp, stamp,
                 admin["id"], "CR-REMOVE", cycle_id),
            ).lastrowid

        responses = []
        self.handler.send_json = lambda payload, status=200: responses.append((payload, status))
        self.handler.cremation_remove_from_cycle(admin, pid)
        self.assertEqual(responses[-1], ({"ok": True}, 200))
        with app.db() as conn:
            practice_row = conn.execute("SELECT cremation_cycle_id FROM practices WHERE id=?", (pid,)).fetchone()
            cycle_row = conn.execute("SELECT status FROM cremation_cycles WHERE id=?", (cycle_id,)).fetchone()
        self.assertIsNone(practice_row["cremation_cycle_id"])
        # the cycle had no other animal left, so it reverts from in_attesa back to pianificato
        self.assertEqual(cycle_row["status"], "pianificato")

        # the animal is back in the "Animali in attesa" list on the board
        rendered = []
        self.handler.path = "/programma-cremazioni?data=2026-07-20"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]
        self.assertIn(f'data-practice-id="{pid}"', page)

        # removing from a completed cycle is refused
        with app.db() as conn:
            done_cycle_id = conn.execute(
                "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,actual_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("2026-07-20", "completato", "10:00", "11:30", stamp, stamp, stamp),
            ).lastrowid
            conn.execute("UPDATE practices SET cremation_cycle_id=? WHERE id=?", (done_cycle_id, pid))
        responses.clear()
        self.handler.cremation_remove_from_cycle(admin, pid)
        payload, status = responses[-1]
        self.assertFalse(payload["ok"])
        self.assertEqual(status, 409)

    def test_cremation_schedule_week_view_groups_cycles_by_day_in_compact_rows(self):
        monday = date(2026, 7, 20)  # a known Monday
        tuesday = monday + timedelta(days=1)
        sunday = monday + timedelta(days=6)
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()

            def cycle(day, status, start, end, actual_end=None):
                return conn.execute(
                    "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,actual_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (day.isoformat(), status, start, end, actual_end, stamp, stamp),
                ).lastrowid

            def practice(code, weight, cycle_id, tag_avvisare="", urn_notes=""):
                pid = conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                       pickup_date,created_at,updated_at,created_by,animal_name,estimated_weight,cremation_cycle_id,
                       tag_avvisare)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, "Privato", "Livorno", "In programma", "Cremazione singola", "2026-07-15", stamp, stamp,
                     admin["id"], code, weight, cycle_id, tag_avvisare),
                ).lastrowid
                if urn_notes:
                    conn.execute(
                        "INSERT INTO practice_items(practice_id,category,subtype,urn_catalog_id,label,price,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (pid, "urna", "", None, urn_notes, "0", 0, stamp, stamp),
                    )
                return pid

            mon_cycle = cycle(monday, "completato", "08:00", "09:30", stamp)
            practice("CR-WEEK-1", "40", mon_cycle, tag_avvisare="Si", urn_notes="Cuore Rosso")
            tue_cycle = cycle(tuesday, "in_attesa", "10:00", "11:20")
            practice("CR-WEEK-2", "12", tue_cycle)
            # a cycle on the last day of the week: drives "fine prevista" for the whole week
            sun_cycle = cycle(sunday, "pianificato", "16:00", "17:30")

        rendered = []
        self.handler.path = f"/programma-cremazioni?vista=settimana&data={monday.isoformat()}"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]

        # header/subtitle switch to the week wording; Settimana tab is the active one
        self.assertIn("della settimana", page)
        self.assertIn('class="active" href="/programma-cremazioni?vista=settimana', page)
        self.assertIn(f"{monday.day} – {sunday.day} Luglio {sunday.year}", page)

        # the redundant Lun/Mar/.../Dom day-strip grid is gone: only the expandable day list remains
        self.assertNotIn('cremation-week-day-chip', page)

        # all 7 days of the week are rendered, each as its own collapsible container
        for i in range(7):
            d = monday + timedelta(days=i)
            self.assertIn(f'data-week-day="{d.isoformat()}"', page)

        # each compact cycle row shows icon + name + weight AND, when present, tag + urn (not just in the expanded view)
        self.assertIn("CICLO 1", page)
        self.assertIn("(40 kg)", page)
        self.assertIn("(12 kg)", page)
        self.assertIn('class="cremation-week-animal-tag"', page)
        self.assertIn('class="cremation-week-animal-urn"', page)
        self.assertIn("AVVISARE", page)
        self.assertIn("Cuore Rosso", page)

        # a completed cycle can still be edited (time), it just no longer offers Avvia/Termina/Aggiungi animale
        self.assertIn(f"cremationOpenEditModal({mon_cycle},'08:00','09:30')", page)

        # clicking a compact cycle row expands it (same mechanism as the day view)
        self.assertIn("cremationToggleCycleCard(this)", page)
        self.assertIn("data-cycle-body", page)

        # week-scoped summary cards
        self.assertIn("Cicli questa settimana", page)
        self.assertIn('<strong class="dash-stat-value">3</strong>', page)  # 3 cycles total this week
        self.assertIn("Questa settimana", page)
        # fine prevista = last cycle of the week (Sunday, 17:30)
        self.assertIn(f"Dom {sunday.day} – 17:30", page)

        # "Aggiungi nuovo ciclo" is present once, at the bottom, and still wired to the same JS
        self.assertEqual(page.count('onclick="cremationCreateEmptyCycle()"'), 1)

    def test_cremation_schedule_week_stat_cards_are_interactive_tools(self):
        monday = date(2026, 7, 20)  # a known Monday
        tuesday = monday + timedelta(days=1)
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()

            def cycle(day, status, start, end, actual_end=None):
                return conn.execute(
                    "INSERT INTO cremation_cycles(cycle_date,status,planned_start,planned_end,actual_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (day.isoformat(), status, start, end, actual_end, stamp, stamp),
                ).lastrowid

            def practice(code, name, status, cycle_id=None):
                return conn.execute(
                    """INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,
                       pickup_date,created_at,updated_at,created_by,animal_name,cremation_cycle_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, "Privato", "Livorno", status, "Cremazione singola", "2026-07-15", stamp, stamp,
                     admin["id"], name, cycle_id),
                ).lastrowid

            done_cycle = cycle(monday, "completato", "08:00", "09:30", stamp)
            practice("CR-BUDDY", "Buddy", "In programma", done_cycle)
            waiting_cycle = cycle(monday, "in_attesa", "13:00", "14:30")
            practice("CR-DAISY", "Daisy", "In programma", waiting_cycle)
            running_cycle = cycle(tuesday, "in_corso", "10:00", "11:30", None)
            practice("CR-ROCKY", "Rocky", "In programma", running_cycle)
            practice("CR-MILO", "Milo", "Ritirato")

        rendered = []
        self.handler.path = f"/programma-cremazioni?vista=settimana&data={monday.isoformat()}"
        self.handler.send_html = lambda content, *args: rendered.append(content)
        self.handler.cremation_schedule(admin)
        page = rendered[-1]

        # all six stat cards are wired to the same click dispatcher with a distinct mode
        for mode in ("tutti", "animali", "in_corso", "in_attesa", "completati", "fine_prevista"):
            self.assertIn(f'data-stat="{mode}"', page)
            self.assertIn(f"cremationWeekStatClick(this,'{mode}')", page)
        self.assertEqual(page.count('data-stat="'), 6)

        # "In attesa" now reflects cycles with status IN ATTESA (1: waiting_cycle), not unassigned animals
        stat_start = page.index('data-stat="in_attesa"')
        in_attesa_card = page[stat_start:stat_start + 900]
        self.assertIn('<strong class="dash-stat-value">1</strong>', in_attesa_card)

        # the "Animali" panel lists every animal assigned to a cycle this week, with day/cycle/status context
        self.assertIn('id="cremationAnimaliPanel"', page)
        self.assertIn('id="cremationAnimaliSearch"', page)
        self.assertIn('cremationFilterAnimaliList(this)', page)
        self.assertIn("Buddy", page)
        self.assertIn("Rocky", page)
        self.assertIn("Daisy", page)
        animali_start = page.index('id="cremationAnimaliPanel"')
        animali_panel = page[animali_start:animali_start + 8000]
        self.assertIn(f"Lun {monday.day:02d}/{monday.month:02d}", animali_panel)
        self.assertIn(f"Mar {tuesday.day:02d}/{tuesday.month:02d}", animali_panel)
        self.assertIn("CICLO 1 · COMPLETATO", animali_panel)
        self.assertIn("CICLO 1 · IN CORSO", animali_panel)
        self.assertIn("CICLO 2 · IN ATTESA", animali_panel)
        # Milo (still waiting, not assigned to any cycle) must not appear in the Animali panel
        self.assertNotIn("Milo", animali_panel)

        # no separate waiting-animals panel exists any more for the week view's "In attesa" card
        self.assertNotIn("cremationWeekWaitingPanel", page)

        # the "Fine prevista" panel gives a full operational recap of the week's tail end
        self.assertIn('id="cremationFinePrevistaPanel"', page)
        fine_start = page.index('id="cremationFinePrevistaPanel"')
        fine_panel = page[fine_start:fine_start + 2000]
        self.assertIn("Tempo residuo", fine_panel)
        self.assertIn(f"Ciclo 1 — 10:00 → 11:30", fine_panel)
        self.assertIn("Rocky", fine_panel)
        self.assertIn("Ritardo accumulato", fine_panel)
        self.assertIn("Cicli rimanenti", fine_panel)
        self.assertIn("<span>2</span>", fine_panel)  # 3 cycles total, 1 completed => 2 remaining

        # a non-intrusive toast placeholder exists for the "no running cycle" message, no new page/route
        self.assertIn('id="cremationToast"', page)
        self.assertIn('cremationToast" class="cremation-toast" hidden', page)

        # the JS dispatcher always resets the full view before applying any new filter/panel
        # (never stacks a filter on top of a previously active one)
        for fn in ("cremationWeekStatClick", "cremationWeekResetView", "cremationFilterCyclesByStatus",
                   "cremationGoToActiveCycle", "cremationSetTimelineHidden",
                   "cremationFilterAnimaliList", "cremationShowToast", "cremationSetActiveStat"):
            self.assertEqual(page.count(f"function {fn}("), 1)
        reset_call_index = page.index("function cremationWeekStatClick(")
        dispatcher_body = page[reset_call_index:reset_call_index + 800]
        self.assertIn("cremationWeekResetView()", dispatcher_body)

    def test_cremation_cycle_border_colors_match_status(self):
        # regression 1: in_corso used to render green (identical to "completed"
        # elsewhere) and completato used to render dim grey instead of green.
        # regression 2: in the week view, the generic .cremation-week-cycle-card
        # base rule (same specificity, later in the stylesheet) silently overrode
        # these status colors, so the compound selector must win regardless of order.
        for selector_prefix, hexcolor in (
            (".cremation-cycle-card.cremation-cycle-in_corso,.cremation-week-cycle-card.cremation-cycle-in_corso", "#3b82f6"),
            (".cremation-cycle-card.cremation-cycle-completato,.cremation-week-cycle-card.cremation-cycle-completato", "#4ade80"),
            (".cremation-cycle-card.cremation-cycle-in_attesa,.cremation-week-cycle-card.cremation-cycle-in_attesa", "#fb923c"),
            (".cremation-cycle-card.cremation-cycle-pianificato,.cremation-week-cycle-card.cremation-cycle-pianificato", "#60a5fa"),
        ):
            start = app.CSS.index(selector_prefix)
            rule = app.CSS[start:app.CSS.index("}", start) + 1]
            self.assertIn(f"border-left-color:{hexcolor}", rule)

    def test_cremation_cycle_cards_get_a_subtle_status_tinted_background(self):
        # a very light, low-opacity background + glow per status, on top of the
        # existing dark card look — never a solid/loud fill, and never applied
        # per animal row (a two-animal cycle must read as one uniform card).
        for status, rgb in (("in_corso", "59,130,246"), ("in_attesa", "251,146,60"), ("completato", "74,222,128")):
            selector_prefix = f".cremation-cycle-card.cremation-cycle-{status},.cremation-week-cycle-card.cremation-cycle-{status}"
            start = app.CSS.index(selector_prefix)
            rule = app.CSS[start:app.CSS.index("}", start) + 1]
            self.assertIn(f"background:rgba({rgb},.08)", rule)
            self.assertIn("box-shadow:0 0", rule)
            self.assertIn(f"rgba({rgb},", rule.split("box-shadow:")[1])

        # pianificato stays subtle with no strong glow, per spec
        pianificato_start = app.CSS.index(".cremation-cycle-card.cremation-cycle-pianificato,.cremation-week-cycle-card.cremation-cycle-pianificato")
        pianificato_rule = app.CSS[pianificato_start:app.CSS.index("}", pianificato_start) + 1]
        self.assertIn("background:rgba(96,165,250,.05)", pianificato_rule)
        self.assertNotIn("box-shadow", pianificato_rule)

        # the expanded detail panel picks up a much more delicate tint of the same hue
        self.assertIn(".cremation-cycle-in_corso .cremation-cycle-body-inner{background:rgba(59,130,246,.04)}", app.CSS)
        self.assertIn(".cremation-cycle-completato .cremation-cycle-body-inner{background:rgba(74,222,128,.04)}", app.CSS)
        self.assertIn(".cremation-cycle-in_attesa .cremation-cycle-body-inner{background:rgba(251,146,60,.04)}", app.CSS)

        # no per-animal-row status coloring exists — a 2-animal cycle must stay one uniform card
        self.assertNotIn("cremation-animal-row.cremation-cycle-", app.CSS)

        # the light theme gets its own (higher-specificity) tint so the plain
        # ".light-theme .cremation-cycle-card{background:#fff}" override can't
        # silently win the cascade and erase the status color again
        self.assertIn(".light-theme .cremation-cycle-card.cremation-cycle-completato,.light-theme .cremation-week-cycle-card.cremation-cycle-completato{background:rgba(74,222,128,.12)", app.CSS)
        self.assertIn(".light-theme .cremation-cycle-card.cremation-cycle-in_corso,.light-theme .cremation-week-cycle-card.cremation-cycle-in_corso{background:rgba(59,130,246,.12)", app.CSS)

    def test_cremation_schedule_remembers_last_selected_view_across_visits(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

        rendered = []
        self.handler.send_html = lambda content, *args: rendered.append(content)

        # by default (no stored preference yet), a bare visit shows the day view
        self.handler.path = "/programma-cremazioni"
        self.handler.cremation_schedule(admin)
        self.assertIn("della giornata", rendered[-1])

        # explicitly switching to Settimana persists the choice
        rendered.clear()
        self.handler.path = "/programma-cremazioni?vista=settimana"
        self.handler.cremation_schedule(admin)
        self.assertIn("della settimana", rendered[-1])

        # a later bare visit (e.g. reopening the app) now lands back on Settimana
        rendered.clear()
        self.handler.path = "/programma-cremazioni"
        self.handler.cremation_schedule(admin)
        self.assertIn("della settimana", rendered[-1])

        # the "Oggi" shortcut inside the week view stays explicit and doesn't depend on the stored preference
        self.assertIn('href="/programma-cremazioni?vista=settimana"', rendered[-1])

        # explicitly switching back to Giorno persists that choice too
        rendered.clear()
        self.handler.path = "/programma-cremazioni?vista=giorno"
        self.handler.cremation_schedule(admin)
        self.assertIn("della giornata", rendered[-1])

        rendered.clear()
        self.handler.path = "/programma-cremazioni"
        self.handler.cremation_schedule(admin)
        self.assertIn("della giornata", rendered[-1])

        with app.db() as conn:
            stored = conn.execute(
                "SELECT value FROM user_preferences WHERE user_id=? AND key='cremation_view'", (admin["id"],)
            ).fetchone()
        self.assertEqual(stored["value"], "giorno")

    def test_normalization_keeps_custom_plate_and_calculates_remaining(self):
        # the calco/urna/accessorio total no longer comes from a fixed column
        # (price_paw_cast etc.) — it's the caller-computed items_total from the
        # parsed practice_items list, added on top of the remaining fixed fields.
        data = self.handler.normalized_fields({
            "transport_method": "Fiat Fiorino", "vehicle_plate": "TARGA LIBERA",
            "price_cremation": "300", "total_text": "250",
            "deposit": "100", "payment_status": "Acconto",
        }, items_total=30.0)
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
        self.assertIn("invoiceTotal.value=ppmFormatInvoiceTotal(serviceTotal);", js)
        self.assertIn("invoiceTotal.addEventListener('blur'", js)

    def test_invoice_total_autofill_seeds_only_from_totale_w_never_totale_d(self):
        js = app.APP_JS
        # Totale fattura must always follow Totale W (total_service): never
        # fall back to or prefer Totale D (total_text), which used to happen
        # here and made the invoice total silently pick up the D circuito's
        # total instead of the W one it actually belongs to.
        self.assertIn("const seed=(totalService?.value||'').trim();", js)
        self.assertNotIn('total_text"]\')?.value||totalService', js)
        self.assertNotIn("definitive > 0 ? definitive : serviceTotal", js)

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

    def test_table_touch_scroll_locks_axis_and_hands_off_to_page_at_the_boundary(self):
        # A diagonal/circular touch drag inside a .tablebox used to scroll it
        # both horizontally and vertically at once (confusing, imprecise).
        # touch-action:none on .tablebox hands full control to this JS, which
        # must: lock the gesture to whichever axis dominates its first few
        # pixels of movement, and — for a vertical gesture — hand off
        # seamlessly to scrolling the page once the table's own internal
        # scroll hits its start/end, requiring a fresh touch inside the table
        # to resume controlling it.
        js=app.APP_JS
        self.assertIn("touch-action:none", app.CSS)
        self.assertIn("function setupTableTouchScroll()", js)
        self.assertIn("axis=Math.abs(dx)>Math.abs(dy)?'x':'y'", js)
        self.assertIn("if(axis==='x'){box.scrollLeft=startScrollLeft-dx;return;}", js)
        self.assertIn("phase='page'", js)
        self.assertIn("window.scrollTo(0,pageStartScroll-(t.clientY-pageAnchorY));", js)
        self.assertIn("document.addEventListener('DOMContentLoaded', setupTableTouchScroll);", js)

    def test_table_touch_scroll_keeps_moving_with_momentum_after_a_fast_flick(self):
        # A strong flick must keep scrolling and ease out after the finger
        # lifts instead of stopping dead, in both the table's own scroll and
        # the handed-off page scroll (same MIN_VELOCITY/DECAY_PER_MS engine).
        js=app.APP_JS
        self.assertIn("const onEnd=()=>{", js)
        self.assertIn("if(axis==='y'&&Math.abs(velocityY)>MIN_VELOCITY)runMomentum('y',phase);", js)
        self.assertIn("else if(axis==='x'&&Math.abs(velocityX)>MIN_VELOCITY)runMomentum('x','table');", js)
        self.assertIn("box.addEventListener('touchend',onEnd,{passive:true});", js)
        self.assertIn("v*=Math.pow(DECAY_PER_MS,dt);", js)
        self.assertIn("const stopMomentum=()=>{if(momentumFrame){cancelAnimationFrame(momentumFrame);momentumFrame=null;}};", js)
        self.assertIn("stopMomentum();", js)

    def test_table_touch_scroll_horizontal_also_gets_momentum(self):
        # Horizontal flicks must ease to a stop the same way vertical ones
        # do — no page handoff for horizontal (that only makes sense for
        # vertical, matching the axis-lock's own existing behavior).
        js=app.APP_JS
        self.assertIn("if(momentumAxis==='x'){", js)
        self.assertIn("const maxScroll=box.scrollWidth-box.clientWidth;", js)
        self.assertIn("if(dt>0){velocityX=(t.clientX-lastX)/dt;velocityY=(t.clientY-lastY)/dt;}", js)

    def test_a_new_touch_anywhere_stops_leftover_table_scroll_momentum(self):
        # A flick inside a table (especially one handed off into page
        # momentum) can still be decelerating when the user touches down
        # again elsewhere — a different table, or plain page content.
        # Without a global stop, that leftover requestAnimationFrame loop
        # keeps calling scrollLeft/scrollTop/scrollTo on top of whatever the
        # new gesture is doing, fighting it every frame ("macchinoso,
        # soprattutto dopo lo scroll da una tabella"). One capture-phase
        # touchstart listener must cancel every table's momentum the
        # instant any new touch begins, anywhere on the page.
        js = app.APP_JS
        self.assertIn("window.ppmMomentumStoppers.forEach(function(stop){stop();});", js)
        self.assertIn("document.addEventListener('touchstart',function(){\n      window.ppmMomentumStoppers.forEach", js)
        self.assertIn("{capture:true,passive:true}", js)
        self.assertIn("window.ppmMomentumStoppers.push(stopMomentum);", js)

    def test_wide_scrollable_tables_use_bounded_internal_scroll_for_reliable_sticky(self):
        # position:sticky on <th> inside a table wrapped by an overflow-x
        # scroll container renders with a permanent top offset in Chromium
        # when the sticky offset is relative to the page (the header cell
        # overlaps the first data row instead of tracking scroll). The fix
        # is to give every .tablebox a bounded height with its own real
        # scroll container (overflow:auto), so thead sticky top:0 is always
        # relative to that container and never breaks, on any table.
        self.assertIn(".tablebox{background:white;border:1px solid var(--line);border-radius:15px;max-height:min(65vh,620px);overflow:auto;-webkit-overflow-scrolling:touch;touch-action:none}", app.CSS)

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

        # picking the same catalog urn twice (two urna items pointing at the same
        # urn_catalog_id) snapshots its name/price onto each item independently
        items_by_category = {
            "urna": app.parse_practice_items(json.dumps([{"urn_catalog_id": urn_id}, {"urn_catalog_id": urn_id}]), "urna"),
            "calco": [], "accessorio": [],
        }
        has_frame_urn = app.resolve_practice_items(items_by_category)
        self.assertEqual(items_by_category["urna"][0]["label"], "Urna prova")
        self.assertEqual(items_by_category["urna"][0]["price"], "85.00")
        self.assertEqual(items_by_category["urna"][1]["label"], "Urna prova")
        self.assertFalse(has_frame_urn)
        items_total = sum(app.money_value(item["price"]) for items in items_by_category.values() for item in items)
        data = self.handler.normalized_fields({"price_cremation": "200", "deposit": "50"}, items_total=items_total)
        self.assertEqual(data["total_service"], "370.00")
        self.assertEqual(data["invoice_total"], "370.00")

        html = self.handler.fields_html()
        self.assertNotIn("<h2>Catalogo Urne</h2>", html)
        self.assertIn('data-practice-list="urna"', html)
        self.assertIn('name="invoice_total"', html)
        # the urn catalog is embedded as JSON for the client-side "+ Aggiungi urna" dropdown
        self.assertIn('"name": "Urna prova"', html)
        self.assertIn('"price": "85.00"', html)

    def test_trash_and_restore_release_both_urn_slots(self):
        # "both slots" is now "however many urna items a practice has" — this
        # practice has three, to also confirm the old 2-slot cap is really gone.
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
            u3 = conn.execute(
                """INSERT INTO urns(name,material,internal_code,price,quantity,low_stock_threshold,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("Urna terza", "Legno", "URN-TRASH-3", "70.00", 4, 3, stamp, stamp),
            ).lastrowid
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by)
                   VALUES(?,?,?,?,?,?,?)""",
                ("CR-TRASH1", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"]),
            ).lastrowid
            for idx, uid in enumerate((u1, u2, u3)):
                conn.execute(
                    "INSERT INTO practice_items(practice_id,category,subtype,urn_catalog_id,label,price,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pid, "urna", "", uid, f"Urna {idx}", "0", idx, stamp, stamp),
                )
            self.handler.adjust_urn_stock(conn, u1, -1, "Utilizzata nella pratica", pid, admin["id"])
            self.handler.adjust_urn_stock(conn, u2, -1, "Utilizzata nella pratica", pid, admin["id"])
            self.handler.adjust_urn_stock(conn, u3, -1, "Utilizzata nella pratica", pid, admin["id"])
        self.handler.redirect = lambda path: None
        self.handler.delete_practice(admin, pid)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u1,)).fetchone()["quantity"], 3)
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u2,)).fetchone()["quantity"], 2)
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u3,)).fetchone()["quantity"], 4)
        self.handler.restore_practice(admin, pid)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u1,)).fetchone()["quantity"], 2)
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u2,)).fetchone()["quantity"], 1)
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u3,)).fetchone()["quantity"], 3)

    def test_init_db_backfills_legacy_urn_calco_accessory_columns_into_practice_items(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,service_type,urn_notes,price_urn,urn_notes_2,price_urn_2,
                   price_cast,price_paw_cast,paw_cast_type,price_nose_cast,nose_cast_type,
                   price_accessories,accessory_type,accessory_detail)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-LEGACY","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Fido","Cremazione singola",
                 "Cuore Rosso","50","Doppia Cornice","30","20","15","Argento","25","Naso oro","40","Collana","Collana blu"),
            ).lastrowid
        # practice_items is still empty at this point (setUp's init_db ran before this
        # legacy-style row existed) — re-running init_db must backfill it from the old columns.
        app.init_db()
        with app.db() as conn:
            rows = conn.execute("SELECT category,subtype,label,price FROM practice_items WHERE practice_id=? ORDER BY category,sort_order",(pid,)).fetchall()
        by_category = {}
        for row in rows:
            by_category.setdefault(row["category"], []).append(dict(row))
        self.assertEqual(len(by_category.get("urna", [])), 2)
        self.assertEqual(by_category["urna"][0]["label"], "Cuore Rosso")
        self.assertEqual(by_category["urna"][0]["price"], "50")
        self.assertEqual(by_category["urna"][1]["label"], "Doppia Cornice")
        self.assertEqual(len(by_category.get("calco", [])), 3)  # generic + polpastrello + naso
        self.assertEqual(len(by_category.get("accessorio", [])), 1)
        self.assertEqual(by_category["accessorio"][0]["subtype"], "Collana")
        self.assertEqual(by_category["accessorio"][0]["label"], "Collana blu")
        # running init_db again must not duplicate the backfilled rows (guarded/idempotent)
        app.init_db()
        with app.db() as conn:
            count_after = conn.execute("SELECT count(*) n FROM practice_items WHERE practice_id=?",(pid,)).fetchone()["n"]
        self.assertEqual(count_after, 6)

    def test_practice_for_ddt_sums_items_per_category_into_the_fixed_pdf_boxes(self):
        # the DDT overlays a scanned paper form with one fixed price box per
        # category — practice_for_ddt must collapse any number of items into
        # a single sum per box (urna/calco/accessorio), not just the first one.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name,service_type)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                ("CR-PDF", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Fido", "Cremazione singola"),
            ).lastrowid
            for category, price in (("urna", "50"), ("urna", "30"), ("calco", "20"), ("accessorio", "10"), ("accessorio", "5")):
                conn.execute(
                    "INSERT INTO practice_items(practice_id,category,subtype,urn_catalog_id,label,price,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pid, category, "", None, "x", price, 0, stamp, stamp),
                )
            p = conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
            result = app.practice_for_ddt(conn, p)
        self.assertEqual(result["price_urn"], "80.00")
        self.assertEqual(result["price_cast"], "20.00")
        self.assertEqual(result["price_accessories"], "15.00")

    def test_create_then_edit_practice_with_unlimited_items_end_to_end(self):
        # full happy path through the real HTTP-style handlers (not direct SQL):
        # create with several urns/calco/accessori across all 3 categories, then
        # edit to add/remove/change some — persistence, total, and per-urn stock
        # must all stay correct at every step, well beyond the old fixed caps.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            u1 = conn.execute("INSERT INTO urns(name,price,quantity,active,category,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("Urna Alfa", "50", 5, 1, "Urna", stamp, stamp)).lastrowid
            u2 = conn.execute("INSERT INTO urns(name,price,quantity,active,category,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("Urna Beta", "40", 5, 1, "Urna", stamp, stamp)).lastrowid
            u3 = conn.execute("INSERT INTO urns(name,price,quantity,active,category,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ("Urna Gamma", "60", 5, 1, "Urna", stamp, stamp)).lastrowid

        base_form = {
            "operator_name": "SERENA", "status": "Ritirato", "service_type": "Cremazione singola", "request_origin": "Privato",
            "destination_branch": "Livorno", "owner_first_name": "Mario", "owner_last_name": "Rossi", "owner_phone": "333123456",
            "owner_tax_code": "RSSMRA80A01H501U", "owner_street": "Via Roma 1", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "animal_name": "Fido", "species": "Cane",
            "urna_items_json": json.dumps([{"urn_catalog_id": u1}, {"urn_catalog_id": u1}, {"urn_catalog_id": u2}]),
            "calco_items_json": json.dumps([
                {"subtype": "polpastrello", "label": "Argento", "price": "20"},
                {"subtype": "naso", "label": "Oro", "price": "15"},
                {"subtype": "", "label": "Generico", "price": "10"},
            ]),
            "accessorio_items_json": json.dumps([
                {"subtype": "Collana", "label": "Collana blu", "price": "40"},
                {"subtype": "Braccialetto", "label": "Braccialetto rosso", "price": "25"},
                {"subtype": "Altro", "label": "Altro extra", "price": "5"},
            ]),
            "balance_idempotency_key": "e2e-create-1",
        }
        self.handler.form = lambda: base_form
        redirected = []
        self.handler.redirect = lambda path: redirected.append(path)
        self.handler.create_practice(admin)
        pid = int(redirected[0].split("/")[-1].split("?")[0])

        with app.db() as conn:
            practice = conn.execute("SELECT total_service FROM practices WHERE id=?", (pid,)).fetchone()
            items = conn.execute("SELECT category FROM practice_items WHERE practice_id=?", (pid,)).fetchall()
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u1,)).fetchone()["quantity"], 3)  # 5-2
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u2,)).fetchone()["quantity"], 4)  # 5-1
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u3,)).fetchone()["quantity"], 5)  # untouched
        self.assertEqual(len(items), 9)  # 3 urna + 3 calco + 3 accessorio
        # 50+50+40 (urne) + 20+15+10 (calco) + 40+25+5 (accessori) = 255
        self.assertEqual(practice["total_service"], "255.00")

        # now edit: drop one urn A, swap the other urn A for urn C, keep urn B,
        # and reduce calco/accessori down to a single item each
        edit_form = dict(base_form)
        edit_form["urna_items_json"] = json.dumps([{"urn_catalog_id": u2}, {"urn_catalog_id": u3}])
        edit_form["calco_items_json"] = json.dumps([{"subtype": "polpastrello", "label": "Argento", "price": "20"}])
        edit_form["accessorio_items_json"] = json.dumps([{"subtype": "Collana", "label": "Collana blu", "price": "40"}])
        edit_form["balance_idempotency_key"] = "e2e-edit-1"
        self.handler.form = lambda: edit_form
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.edit_submit(admin, pid)

        with app.db() as conn:
            practice = conn.execute("SELECT total_service FROM practices WHERE id=?", (pid,)).fetchone()
            urna_items = conn.execute("SELECT urn_catalog_id FROM practice_items WHERE practice_id=? AND category='urna' ORDER BY sort_order", (pid,)).fetchall()
            calco_items = conn.execute("SELECT label FROM practice_items WHERE practice_id=? AND category='calco'", (pid,)).fetchall()
            accessorio_items = conn.execute("SELECT label FROM practice_items WHERE practice_id=? AND category='accessorio'", (pid,)).fetchall()
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u1,)).fetchone()["quantity"], 5)  # both usages returned
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u2,)).fetchone()["quantity"], 4)  # unchanged (still used once)
            self.assertEqual(conn.execute("SELECT quantity FROM urns WHERE id=?", (u3,)).fetchone()["quantity"], 4)  # newly used
        self.assertEqual([r["urn_catalog_id"] for r in urna_items], [u2, u3])
        self.assertEqual(len(calco_items), 1)
        self.assertEqual(len(accessorio_items), 1)
        # 40+60 (urne) + 20 (calco) + 40 (accessori) = 160
        self.assertEqual(practice["total_service"], "160.00")

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
                                created_by,animal_name,species,breed,age_years,age_months,service_type,price_pickup,
                                price_night,send_catalog,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-APERTURA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cane","Meticcio","7","3","Cremazione singola","40","","Si","Da saldare")).lastrowid
            conn.execute(
                "INSERT INTO practice_items(practice_id,category,subtype,urn_catalog_id,label,price,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid,"urna","",None,"Urna doppia","85",0,stamp,stamp),
            )
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
        # deliberately well beyond the old caps (2 urns / 2 generic calco / 4 naso / 4
        # polpastrello / 2 accessori) to prove the new practice_items list has none.
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,
                                created_by,animal_name,service_type,payment_status)
                                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                             ("PP-MULTI","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Rex","Cremazione singola","Da saldare")).lastrowid
            rows=[
                ("urna","","Urna base","80"),("urna","","Urna scorta","90"),("urna","","Urna terza","95"),
                ("calco","","Calco generico","50"),
                ("calco","polpastrello","Argento","20"),("calco","polpastrello","Oro","21"),
                ("calco","polpastrello","Bronzo","22"),("calco","polpastrello","Platino","23"),
                ("calco","naso","Bronzo S","30"),("calco","naso","Bronzo M","31"),
                ("calco","naso","Bronzo G","32"),("calco","naso","Argento S","33"),
                ("accessorio","Collana","Collana blu","10"),("accessorio","Braccialetto","Braccialetto rosso","11"),
                ("accessorio","Altro","Altro accessorio","12"),
            ]
            for idx,(category,subtype,label,price) in enumerate(rows):
                conn.execute(
                    "INSERT INTO practice_items(practice_id,category,subtype,urn_catalog_id,label,price,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pid,category,subtype,None,label,price,idx,stamp,stamp),
                )
        rendered=[]; self.handler.send_html=lambda content,*args: rendered.append(content)
        self.handler.practice(admin,pid)
        page=rendered[-1]
        self.assertIn("Urna base",page)
        self.assertIn("Urna scorta",page)
        self.assertIn("Urna terza",page)
        for label,amount in (
            ("Calco — Calco generico","€ 50,00"),
            ("Calco polpastrello — Argento","€ 20,00"),("Calco polpastrello — Oro","€ 21,00"),
            ("Calco polpastrello — Bronzo","€ 22,00"),("Calco polpastrello — Platino","€ 23,00"),
            ("Calco naso — Bronzo S","€ 30,00"),("Calco naso — Bronzo M","€ 31,00"),
            ("Calco naso — Bronzo G","€ 32,00"),("Calco naso — Argento S","€ 33,00"),
            ("Collana — Collana blu","€ 10,00"),("Braccialetto — Braccialetto rosso","€ 11,00"),
            ("Altro — Altro accessorio","€ 12,00"),
        ):
            self.assertIn(f'<small>{label}</small><b>{amount}</b>',page)

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
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,estimated_weight,age_years,owner_first_name,owner_last_name,service_type,payment_status,total_service,provenance)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-LISTA","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Luna","Cane","12","8","Mario","Rossi","Cremazione singola","Da saldare","230","V")).lastrowid
            conn.execute(
                "INSERT INTO practice_items(practice_id,category,subtype,urn_catalog_id,label,price,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid,"urna","",urn_id,"Doppia Quercia","95.00",0,stamp,stamp),
            )
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
        self.assertIn('<div class="practice-row-animal-copy">Gatto</div>',page)
        self.assertIn('class="practice-row-avatar avatar-cat"',page)
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
        # The header no longer hardcodes a circuito: each row already shows
        # its own "Acconto W"/"Acconto D" label matching whichever circuito
        # that practice actually uses.
        self.assertIn("<th>Acconto</th>",body)
        self.assertIn("<th>Rimanenza</th>",body)

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
        self.assertIn('class="practice-row-link avatar-dog"',page)
        self.assertIn("practice-status-blue",page)
        self.assertNotIn("practice-row-cremated",page)
        self.assertNotIn("practice-row-cremated",app.CSS)

    def test_urn_word_search_and_frame_urn_enable_cast_tag(self):
        self.assertIn("normalizeUrnSearch",app.APP_JS)  # still used by the Catalogo Urne page's own search
        with app.db() as conn:
            stamp=app.now()
            urn_id=conn.execute("INSERT INTO urns(name,price,quantity,active,created_at,updated_at,category) VALUES(?,?,?,?,?,?,?)",("Doppia Cornice Bianca L","120",3,1,stamp,stamp,"Urna")).lastrowid
        items_by_category={"urna":app.parse_practice_items(json.dumps([{"urn_catalog_id":urn_id}]),"urna"),"calco":[],"accessorio":[]}
        has_frame_urn=app.resolve_practice_items(items_by_category)
        self.assertTrue(has_frame_urn)
        data=self.handler.normalized_fields({"service_type":"Cremazione singola"},has_frame_urn=has_frame_urn)
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

    def test_whatsapp_send_does_not_hold_a_database_lock_during_the_network_call(self):
        # Regression test: the Meta API call used to happen while the database
        # connection that had just marked the message "in_invio" was still open
        # (SQLite has no WAL mode here), holding a write lock for the whole
        # network round-trip (up to 18s) and blocking every other request in
        # the app during that window — including, e.g., deleting a movimento
        # in Bilanci. A concurrent write from a brand-new connection, issued
        # from inside the mocked network call itself, must now succeed
        # immediately instead of hitting "database is locked".
        _,pid,msg_id=self._whatsapp_record("2026-07-15T14:00:00")
        concurrent_write=None
        class MetaResponse:
            status=200
            def __enter__(self):
                nonlocal concurrent_write
                try:
                    conn=sqlite3.connect(app.DB_PATH,timeout=0.5)
                    conn.execute("UPDATE practices SET notes='concurrent write during whatsapp call' WHERE id=?",(pid,))
                    conn.commit()
                    conn.close()
                    concurrent_write=True
                except sqlite3.OperationalError as exc:
                    concurrent_write=str(exc)
                return self
            def __exit__(self,*args):pass
            def read(self):return b'{"messages":[{"id":"wamid.test"}]}'
        env={"WHATSAPP_ACCESS_TOKEN":"token-test","WHATSAPP_PHONE_NUMBER_ID":"phone-test"}
        with patch.dict(os.environ,env),patch("app.urllib.request.urlopen",return_value=MetaResponse()):
            result=self.handler.process_whatsapp_queue(current_time=datetime(2026,7,15,14,0))
        self.assertTrue(result[0]["ok"])
        self.assertIs(concurrent_write,True,f"concurrent write was blocked: {concurrent_write}")
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT notes FROM practices WHERE id=?",(pid,)).fetchone()["notes"],"concurrent write during whatsapp call")

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
        def accepted(msg_id,**kwargs):
            with app.db() as conn:
                conn.execute("UPDATE whatsapp_messages SET status='accettato_da_meta',message_id='wamid.retry',sent_at=?,last_error='' WHERE id=?",(app.whatsapp_now(),msg_id))
            return True,"ok"
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

    def _catalog_practice(self, send_catalog="", catalog_sent="", phone="3339990000"):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            number=f"CR-CAT-{conn.execute('SELECT count(*) n FROM practices').fetchone()['n']+1}"
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,owner_last_name,owner_phone,animal_name,service_type,send_catalog,catalog_sent)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (number,"Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Anna","Bianchi",phone,"Luna","Cremazione singola",send_catalog,catalog_sent)).lastrowid
        return admin,pid

    def test_whatsapp_client_name_uses_only_the_first_name_not_the_surname(self):
        # {{1}} in both the ringraziamento and catalogo templates must read
        # like "Ciao Anna", not "Ciao Anna Bianchi" — more natural for a
        # message signed off casually.
        admin,pid=self._catalog_practice()
        with app.db() as conn:
            p=conn.execute("SELECT * FROM practices WHERE id=?",(pid,)).fetchone()
        self.assertEqual(self.handler.whatsapp_client_name(p),"Anna")
        self.assertEqual(self.handler.whatsapp_payload_for_practice(p)["template"]["components"][0]["parameters"][0]["text"],"Anna")
        self.assertEqual(self.handler.whatsapp_catalog_payload_for_practice(p)["template"]["components"][0]["parameters"][0]["text"],"Anna")

    def test_checking_send_catalog_via_catalog_sent_handler_schedules_a_catalogo_urne_message(self):
        admin,pid=self._catalog_practice()
        self.handler.form=lambda:{"send_catalog":"Si"}
        self.handler.redirect=lambda path:None
        self.handler.catalog_sent(admin,pid)
        with app.db() as conn:
            row=conn.execute("SELECT message_type,template_name,status,recipient_phone FROM whatsapp_messages WHERE practice_id=?",(pid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual((row["message_type"],row["template_name"],row["status"]),("catalogo","catalogo_urne","programmato"))
        self.assertEqual(row["recipient_phone"],"393339990000")

    def test_unchecking_send_catalog_cancels_the_pending_catalog_message(self):
        admin,pid=self._catalog_practice()
        self.handler.form=lambda:{"send_catalog":"Si"}
        self.handler.redirect=lambda path:None
        self.handler.catalog_sent(admin,pid)
        self.handler.form=lambda:{"send_catalog":""}
        self.handler.catalog_sent(admin,pid)
        with app.db() as conn:
            status=conn.execute("SELECT status FROM whatsapp_messages WHERE practice_id=?",(pid,)).fetchone()["status"]
        self.assertEqual(status,"annullato")

    def test_create_practice_with_send_catalog_checked_schedules_catalog_message(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        redirects=[];self.handler.redirect=lambda path:redirects.append(path)
        self.handler.form=lambda:{
            "operator_name":"FILIPPO","service_type":"Cremazione singola","request_origin":"Privato",
            "owner_first_name":"Anna","owner_last_name":"Bianchi","owner_phone":"3339990001",
            "owner_tax_code":"X","owner_street":"Via","owner_city":"Livorno","owner_province":"LI","owner_zip":"57100",
            "send_catalog":"Si",
        }
        self.handler.create_practice(admin)
        pid=int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            row=conn.execute("SELECT message_type,template_name FROM whatsapp_messages WHERE practice_id=?",(pid,)).fetchone()
        self.assertEqual((row["message_type"],row["template_name"]),("catalogo","catalogo_urne"))

    def test_edit_submit_send_catalog_transition_schedules_and_uncheck_cancels(self):
        admin,pid=self._catalog_practice()
        base_form={
            "operator_name":"FILIPPO","service_type":"Cremazione singola","request_origin":"Privato",
            "owner_first_name":"Anna","owner_last_name":"Bianchi","owner_phone":"3339990000",
            "owner_tax_code":"X","owner_street":"Via","owner_city":"Livorno","owner_province":"LI","owner_zip":"57100",
            "payment_status":"Da saldare",
        }
        self.handler.redirect=lambda path:None
        self.handler.form=lambda:{**base_form,"send_catalog":"Si"}
        self.handler.edit_submit(admin,pid)
        with app.db() as conn:
            status=conn.execute("SELECT status FROM whatsapp_messages WHERE practice_id=? AND message_type='catalogo'",(pid,)).fetchone()["status"]
        self.assertEqual(status,"programmato")
        self.handler.form=lambda:{**base_form,"send_catalog":""}
        self.handler.edit_submit(admin,pid)
        with app.db() as conn:
            status=conn.execute("SELECT status FROM whatsapp_messages WHERE practice_id=? AND message_type='catalogo'",(pid,)).fetchone()["status"]
        self.assertEqual(status,"annullato")

    def test_thanks_and_catalog_messages_coexist_independently_for_the_same_practice(self):
        # Regression test for the message_type discriminator: before it
        # existed, scheduling a second whatsapp_messages row of a different
        # purpose for the same practice would either violate the old
        # per-practice unique index or be mistaken for the other flow's
        # "already active" check.
        admin,pid=self._catalog_practice()
        with app.db() as conn:
            conn.execute("UPDATE practices SET status='Consegnato' WHERE id=?",(pid,))
            ok_thanks,_=self.handler.schedule_whatsapp_thanks(conn,pid,admin["id"])
            ok_catalog,_=self.handler.schedule_whatsapp_catalog(conn,pid,admin["id"])
        self.assertTrue(ok_thanks);self.assertTrue(ok_catalog)
        with app.db() as conn:
            rows={row["message_type"]:row["status"] for row in conn.execute("SELECT message_type,status FROM whatsapp_messages WHERE practice_id=?",(pid,))}
        self.assertEqual(rows,{"ringraziamento":"programmato","catalogo":"programmato"})

    def test_catalog_message_success_marks_catalog_sent_and_clears_send_catalog(self):
        admin,pid=self._catalog_practice(send_catalog="Si")
        with app.db() as conn:
            self.handler.schedule_whatsapp_catalog(conn,pid,admin["id"])
            msg_id=conn.execute("SELECT id FROM whatsapp_messages WHERE practice_id=? AND message_type='catalogo'",(pid,)).fetchone()["id"]
            conn.execute("UPDATE whatsapp_messages SET scheduled_at='2026-07-15T14:00:00' WHERE id=?",(msg_id,))
        class MetaResponse:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"messages":[{"id":"wamid.catalog"}]}'
        env={"WHATSAPP_ACCESS_TOKEN":"token-test","WHATSAPP_PHONE_NUMBER_ID":"phone-test"}
        with patch.dict(os.environ,env),patch("app.urllib.request.urlopen",return_value=MetaResponse()):
            result=self.handler.process_whatsapp_queue(current_time=datetime(2026,7,15,14,0))
        self.assertTrue(result[0]["ok"])
        with app.db() as conn:
            practice=conn.execute("SELECT send_catalog,catalog_sent FROM practices WHERE id=?",(pid,)).fetchone()
            payload=conn.execute("SELECT payload_json FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()["payload_json"]
        self.assertEqual((practice["send_catalog"],practice["catalog_sent"]),("","Si"))
        self.assertIn('"catalogo_urne"',payload)

    def test_catalog_send_is_cancelled_if_unchecked_before_the_scheduled_send_fires(self):
        admin,pid=self._catalog_practice(send_catalog="Si")
        with app.db() as conn:
            self.handler.schedule_whatsapp_catalog(conn,pid,admin["id"])
            msg_id=conn.execute("SELECT id FROM whatsapp_messages WHERE practice_id=? AND message_type='catalogo'",(pid,)).fetchone()["id"]
            conn.execute("UPDATE practices SET send_catalog='' WHERE id=?",(pid,))
        ok,_=self.handler.send_whatsapp_message(msg_id,manual=False)
        self.assertFalse(ok)
        with app.db() as conn:
            status=conn.execute("SELECT status FROM whatsapp_messages WHERE id=?",(msg_id,)).fetchone()["status"]
        self.assertEqual(status,"annullato")

    def test_resend_whatsapp_thanks_ignores_a_pending_catalog_message_for_the_same_practice(self):
        # resend_whatsapp/whatsapp_confirm_page are ringraziamento-only admin
        # actions: a pending/sent catalog row for the same practice must not
        # be picked up as "the" active/latest whatsapp_messages row.
        admin,pid=self._catalog_practice(send_catalog="Si")
        with app.db() as conn:
            conn.execute("UPDATE practices SET status='Consegnato' WHERE id=?",(pid,))
            self.handler.schedule_whatsapp_catalog(conn,pid,admin["id"])
        self.handler.form=lambda:{"confirm_send":"SI"}
        self.handler.redirect=lambda path:None
        with patch.object(self.handler,"send_whatsapp_message",return_value=(True,"ok")) as send:
            self.handler.resend_whatsapp(admin,pid)
        sent_msg_id=send.call_args[0][0]
        with app.db() as conn:
            message_type=conn.execute("SELECT message_type FROM whatsapp_messages WHERE id=?",(sent_msg_id,)).fetchone()["message_type"]
        self.assertEqual(message_type,"ringraziamento")

    def test_practice_page_shows_resend_catalog_button_only_once_catalog_sent(self):
        admin,pid=self._catalog_practice(catalog_sent="Si")
        rendered=[];self.handler.send_html=lambda content,*a:rendered.append(content)
        self.handler.practice(admin,pid)
        self.assertIn(f'href="/pratiche/{pid}/catalogo-whatsapp-conferma"',rendered[-1])
        self.assertIn("Reinvia catalogo",rendered[-1])
        admin2,pid2=self._catalog_practice(send_catalog="Si")
        rendered2=[];self.handler.send_html=lambda content,*a:rendered2.append(content)
        self.handler.practice(admin2,pid2)
        self.assertNotIn("Reinvia catalogo",rendered2[-1])

    def test_resend_whatsapp_catalog_uses_the_catalog_template_and_ignores_a_pending_thanks_message(self):
        # Mirrors test_resend_whatsapp_thanks_ignores_a_pending_catalog_message_for_the_same_practice,
        # but for the catalog resend button: a pending ringraziamento row for
        # the same practice must not be picked up as "the" active/latest one.
        admin,pid=self._catalog_practice(catalog_sent="Si")
        with app.db() as conn:
            conn.execute("UPDATE practices SET status='Consegnato' WHERE id=?",(pid,))
            self.handler.schedule_whatsapp_thanks(conn,pid,admin["id"])
        self.handler.form=lambda:{"confirm_send":"SI"}
        self.handler.redirect=lambda path:None
        with patch.object(self.handler,"send_whatsapp_message",return_value=(True,"ok")) as send:
            self.handler.resend_whatsapp_catalog(admin,pid)
        sent_msg_id=send.call_args[0][0]
        with app.db() as conn:
            row=conn.execute("SELECT message_type,template_name FROM whatsapp_messages WHERE id=?",(sent_msg_id,)).fetchone()
        self.assertEqual((row["message_type"],row["template_name"]),("catalogo","catalogo_urne"))

    def test_non_admin_users_can_also_resend_the_whatsapp_catalog(self):
        # the resend-catalog button on the practice page is shown to every
        # logged-in user regardless of role; the confirm page and the actual
        # send action must therefore also be reachable by a non-admin
        # operator, not just by admins (this used to 403 for operators).
        admin,pid=self._catalog_practice(catalog_sent="Si")
        with app.db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,display_name,role) VALUES('operatore','x','Operatore','operator')")
            operator=conn.execute("SELECT * FROM users WHERE username='operatore'").fetchone()

        rendered=[];self.handler.send_html=lambda content,*a:rendered.append(content)
        self.handler.catalog_whatsapp_confirm_page(operator,pid)
        self.assertIn("REINVIA CATALOGO",rendered[-1])

        self.handler.form=lambda:{"confirm_send":"SI"}
        self.handler.redirect=lambda path:None
        with patch.object(self.handler,"send_whatsapp_message",return_value=(True,"ok")) as send:
            self.handler.resend_whatsapp_catalog(operator,pid)
        sent_msg_id=send.call_args[0][0]
        with app.db() as conn:
            row=conn.execute("SELECT message_type FROM whatsapp_messages WHERE id=?",(sent_msg_id,)).fetchone()
        self.assertEqual(row["message_type"],"catalogo")

    def test_webhook_inbound_text_message_links_to_the_practice_it_was_sent_to(self):
        admin,pid=self._catalog_practice()
        with app.db() as conn:
            stamp=app.now()
            conn.execute("""INSERT INTO whatsapp_messages(practice_id,scheduled_at,status,template_name,recipient_phone,manual,message_type,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?)""",(pid,stamp,"accettato_da_meta","catalogo_urne","393339990000",0,"catalogo",stamp,stamp))
        payload=json.dumps({"entry":[{"changes":[{"value":{
            "contacts":[{"profile":{"name":"Anna B."},"wa_id":"393339990000"}],
            "messages":[{"id":"wamid.inbound1","from":"393339990000","type":"text","text":{"body":"Grazie, quale urna scelgo?"}}],
        }}]}]})
        self.handler.headers={"Content-Length":str(len(payload.encode()))}
        self.handler.rfile=io.BytesIO(payload.encode())
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.whatsapp_webhook_receive()
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            row=conn.execute("SELECT practice_id,from_phone,contact_name,body FROM whatsapp_inbound_messages WHERE wa_message_id='wamid.inbound1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["practice_id"],pid)
        self.assertEqual(row["contact_name"],"Anna B.")
        self.assertIn("quale urna",row["body"])

    def test_webhook_duplicate_inbound_message_id_is_stored_only_once(self):
        admin,pid=self._catalog_practice()
        payload=json.dumps({"entry":[{"changes":[{"value":{
            "messages":[{"id":"wamid.dup","from":"3339990000","type":"text","text":{"body":"Ciao"}}],
        }}]}]})
        for _ in range(2):
            self.handler.headers={"Content-Length":str(len(payload.encode()))}
            self.handler.rfile=io.BytesIO(payload.encode())
            responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
            self.handler.whatsapp_webhook_receive()
        with app.db() as conn:
            count=conn.execute("SELECT count(*) n FROM whatsapp_inbound_messages WHERE wa_message_id='wamid.dup'").fetchone()["n"]
        self.assertEqual(count,1)

    def test_webhook_inbound_message_from_unknown_number_is_stored_unlinked(self):
        payload=json.dumps({"entry":[{"changes":[{"value":{
            "messages":[{"id":"wamid.unknown","from":"390000000000","type":"text","text":{"body":"Chi sei?"}}],
        }}]}]})
        self.handler.headers={"Content-Length":str(len(payload.encode()))}
        self.handler.rfile=io.BytesIO(payload.encode())
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.whatsapp_webhook_receive()
        with app.db() as conn:
            row=conn.execute("SELECT practice_id FROM whatsapp_inbound_messages WHERE wa_message_id='wamid.unknown'").fetchone()
        self.assertIsNone(row["practice_id"])

    def test_conversations_page_shows_inbound_reply_and_unmatched_messages(self):
        admin,pid=self._catalog_practice()
        with app.db() as conn:
            stamp=app.now()
            conn.execute("""INSERT INTO whatsapp_messages(practice_id,scheduled_at,status,template_name,recipient_phone,manual,message_type,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?)""",(pid,stamp,"accettato_da_meta","catalogo_urne","393339990000",0,"catalogo",stamp,stamp))
            conn.execute("""INSERT INTO whatsapp_inbound_messages(practice_id,wa_message_id,from_phone,contact_name,message_type,body,received_at,created_at)
                            VALUES(?,?,?,?,?,?,?,?)""",(pid,"wamid.reply1","393339990000","Anna B.","text","Va bene, grazie!",stamp,stamp))
            conn.execute("""INSERT INTO whatsapp_inbound_messages(practice_id,wa_message_id,from_phone,contact_name,message_type,body,received_at,created_at)
                            VALUES(?,?,?,?,?,?,?,?)""",(None,"wamid.orphan","390001112222","","text","Messaggio senza pratica collegata",stamp,stamp))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/conversazioni-whatsapp"
        self.handler.whatsapp_conversations(admin)
        page=rendered[-1]
        self.assertIn("Risposte del cliente",page)
        self.assertIn("Va bene, grazie!",page)
        self.assertIn("Messaggi ricevuti non abbinati a nessuna pratica",page)
        self.assertIn("Messaggio senza pratica collegata",page)
        self.assertIn("Invio catalogo urne",page)

    def test_conversations_page_splits_scheduled_and_sent_instead_of_sorting_by_raw_timestamp(self):
        # Regression test: a thank-you is scheduled ~48h in the future while a
        # catalog send is scheduled for "now" — sorting everything by one
        # DESC timestamp let a not-yet-sent, far-future "programmato" row
        # outrank a message that was actually sent minutes ago, making a real
        # send look "missing" from the top of the list.
        admin, future_scheduled_pid = self._catalog_practice(phone="3330000001")
        with app.db() as conn:
            stamp = app.now()
            conn.execute("""INSERT INTO whatsapp_messages(practice_id,scheduled_at,status,template_name,recipient_phone,manual,message_type,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                         (future_scheduled_pid, "2026-09-01T14:00:00", "programmato", "ringraziamento_livorno", "393330000001", 0, "ringraziamento", stamp, stamp))
        _, just_sent_pid = self._catalog_practice(phone="3330000002")
        with app.db() as conn:
            stamp = app.now()
            conn.execute("""INSERT INTO whatsapp_messages(practice_id,scheduled_at,status,sent_at,template_name,recipient_phone,manual,message_type,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         (just_sent_pid, stamp, "accettato_da_meta", stamp, "catalogo_urne", "393330000002", 0, "catalogo", stamp, stamp))
        rendered = []; self.handler.send_html = lambda content, *a: rendered.append(content); self.handler.path = "/conversazioni-whatsapp"
        self.handler.whatsapp_conversations(admin)
        page = rendered[-1]
        self.assertIn("In programma", page)
        self.assertIn("Inviati", page)
        scheduled_section = page.split("In programma", 1)[1].split("Inviati", 1)[0]
        sent_section = page.split("Inviati", 1)[1]
        self.assertIn("393330000001", scheduled_section)
        self.assertNotIn("393330000002", scheduled_section)
        self.assertIn("393330000002", sent_section)
        self.assertNotIn("393330000001", sent_section)

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

    def test_quick_payment_does_not_require_payment_method_when_circuit_is_d(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-QPD-NOMETODO","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Mario","Cremazione singola","Da saldare","200","200")).lastrowid
        self.handler.form=lambda:{"payment_status":"Pagato","payment_method":"","payment_amount":"200,00","economic_at":"2026-07-24","payment_channel":"D","ajax":"1"}
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.quick_payment(admin,pid)
        self.assertTrue(responses[-1][0]["ok"],responses[-1])

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
        # Both removals leave a lone technical Storno per movement in
        # balance_movements with nothing left to offset (the movement it
        # reversed is gone too) — get_outstanding_balances must not sum that
        # orphaned Storno as a negative "received" amount, or the practice's
        # own "Da riscuotere" total would come out *larger* than the full
        # price instead of matching it exactly.
        with app.db() as conn:
            snapshot=app.get_balance_snapshot(conn,filters=app.normalize_balance_filters(date_to="2026-12-31"))
        match=[row for row in snapshot.sections["da-riscuotere-w"].rows if row.practice_id==pid]
        self.assertEqual(len(match),1)
        self.assertEqual(match[0].remaining_cents,30000)
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
                                   "owner_province":"LI","owner_zip":"57100","saldo_w_totale":"250","saldo_w_totale_touched":"1","calendar_event_id":""}
        pages=[];self.handler.new_page=lambda user,draft=None,error="",error_field="":pages.append(error)
        self.handler.create_practice(admin)
        self.assertIn("Indica una data valida",pages[-1])
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
                # A practice's acconto/rimanenza live in deposit/remaining_balance
                # for circuito W, or deposit_final/remaining_final for circuito D
                # (whichever total_d being set implies) — never both.
                deposit_w,remaining_w,deposit_d,remaining_d=("0","0",deposit,remaining) if total_d else (deposit,remaining,"0","0")
                return conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,created_at,updated_at,created_by,
                                      animal_name,service_type,payment_status,total_service,price_cremation,total_text,deposit,remaining_balance,deposit_final,remaining_final,data_complete)
                                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                                    (code,"Privato","Livorno",status,pickup,created+"T08:00:00",stamp,uid,name,"Cremazione singola",payment,total,total,total_d,deposit_w,remaining_w,deposit_d,remaining_d)).lastrowid
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
        # the redesigned cards keep the exact same test hooks (data-dashboard-*)
        # used above, just wrapped in the new compact premium markup
        self.assertIn('class="dash-stat-card state-yellow" data-dashboard-card="Ritirato"',page)
        self.assertIn('class="dash-stat-card payment-due" data-dashboard-payment="Da saldare"',page)
        self.assertNotIn('class="metric-card',page)
        self.assertNotIn('class="payment-card',page)
        # Totale incassato: a new 4th payment card, purely additive (acconto + pagato already computed above)
        self.assertIn('class="dash-stat-card payment-total"',page)
        self.assertIn("Totale incassato",page)
        self.assertIn(app.money_it(200.0+200.0),page)
        with app.db() as conn:
            self.assertEqual(snapshot,(conn.execute("SELECT count(*) n FROM practices").fetchone()["n"],conn.execute("SELECT count(*) n FROM payment_movements").fetchone()["n"],conn.execute("SELECT count(*) n FROM practice_history").fetchone()["n"]))

    def test_dashboard_recent_practices_use_species_avatars_and_premium_row_style(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,pickup_date) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-AVATAR-DOG","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Rex","Cane","2026-07-20"))
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         animal_name,species,pickup_date) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-AVATAR-CAT","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Micio","Gatto","2026-07-21"))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        self.assertIn('class="practice-row-avatar avatar-dog"',page)
        self.assertIn('class="practice-row-avatar avatar-cat"',page)
        self.assertIn("\U0001f436",page)  # 🐶
        self.assertIn("\U0001f431",page)  # 🐱
        # this must be the same shared component the Archivio pratiche page uses
        self.assertIn('<table class="practice-list-table">',page)

    def test_practice_list_table_css_uses_rounded_spaced_premium_rows(self):
        for rule in (
            ".practice-list-table{min-width:1500px;border-collapse:separate;border-spacing:0 10px}",
            ".practice-list-table tbody tr:hover{transform:translateY(-1px);box-shadow:0 10px 26px #03071240}",
            ".practice-list-table tbody td:first-child{border-left:1px solid #334155;border-top-left-radius:14px;border-bottom-left-radius:14px}",
        ):
            self.assertIn(rule,app.CSS)

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
            "Periodo","Data","Tipo","Cerca","Categoria","Collaboratore","Metodo pagamento","Operatore","Filtri avanzati",
            "Entrate W","Entrate D","Collaboratori Incassato","Da riscuotere W",
            "Da riscuotere D","Collaboratori Da riscuotere","Uscite W","Uscite D",
            "Totale W attuale","Totale D attuale","Saldo Netto",
        ):
            self.assertIn(label,balances_page)
        self.assertEqual(balances_page.count('data-balance-card="'),11)
        self.assertEqual(balances_page.count('data-balance-total-cents="0"'),11)
        self.assertIn('aria-current="true"',balances_page)
        self.assertIn('<span class="balance-summary-title">Entrate W</span>',balances_page)
        self.assertNotIn("<table",balances_page)
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

    def test_balances_movements_render_as_color_coded_cards_not_a_table(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            app.create_balance_income(conn,category="W",movement_date="2026-07-10",amount_cents=5000,
                                       payment_method="Contanti",description="Entrata W di prova",
                                       idempotency_key="test-card-w",created_by=admin["id"])
            app.create_balance_income(conn,category="D",movement_date="2026-07-10",amount_cents=7000,
                                       payment_method="Contanti",description="Entrata D di prova",
                                       idempotency_key="test-card-d",created_by=admin["id"])
            collab_id=conn.execute("SELECT id FROM collaborators LIMIT 1").fetchone()["id"]
            app.create_balance_income(conn,category="Collaboratori",movement_date="2026-07-10",amount_cents=2000,
                                       payment_method="Contanti",description="Entrata Collaboratore di prova",
                                       idempotency_key="test-card-collab",collaborator_id=collab_id,created_by=admin["id"])
            app.create_balance_expense(conn,category="W",movement_date="2026-07-10",amount_cents=1500,
                                        description="Uscita W di prova",idempotency_key="test-card-out",created_by=admin["id"])
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        expectations=(
            ("totale-w-attuale","balance-move-w","Entrata W"),
            ("totale-d-attuale","balance-move-d","Entrata D"),
            ("collaboratori-incassato","balance-move-collab","Entrata Collaboratore"),
            ("uscite-w","balance-move-out","Uscita"),
        )
        for view,accent_cls,type_label in expectations:
            self.handler.path=f"/bilanci?view={view}&periodo=tutto"
            self.handler.balances_page(admin)
            page=rendered[-1]
            self.assertNotIn("<table",page)
            self.assertNotIn("<tr",page)
            self.assertIn("balance-move-list",page)
            self.assertIn(f'class="balance-move-card {accent_cls}"',page)
            self.assertIn(f'<div class="balance-move-type">{type_label}</div>',page)

    def test_balances_summary_card_reflects_the_selected_section(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            app.create_balance_income(conn,category="D",movement_date="2026-07-10",amount_cents=732000,
                                       payment_method="Contanti",description="Entrata D grossa",
                                       idempotency_key="test-summary-card",created_by=admin["id"])
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/bilanci?view=entrate-d&periodo=tutto"
        self.handler.balances_page(admin)
        page=rendered[-1]
        self.assertIn('<div class="balance-summary-card balance-tone-d">',page)
        self.assertIn('<span class="balance-summary-title">Entrate D</span>',page)
        self.assertIn(app.money_cents_it(732000),page)
        self.assertIn("1 movimenti",page)

    def test_balances_outstanding_view_uses_the_same_card_component(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                         owner_first_name,species,animal_name,service_type,payment_status,price_cremation,total_service)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         ("CR-DARISC","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Sara","Cane","Fido","Cremazione singola","Da saldare","200","200"))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/bilanci?view=da-riscuotere-w&periodo=tutto"
        self.handler.balances_page(admin)
        page=rendered[-1]
        self.assertNotIn("<table",page)
        self.assertIn('<div class="balance-move-type">Da riscuotere W</div>',page)
        self.assertIn('<span class="balance-move-status balance-status-yellow">Da saldare</span>',page)
        self.assertIn("CR-DARISC",page)

    def test_balances_quick_filters_row_and_advanced_details_keep_every_field(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/bilanci"
        self.handler.balances_page(admin)
        page=rendered[-1]
        form_start=page.index('<form class="section balance-filters')
        self.assertIn('<div class="balance-filters-quick">',page[form_start:])
        quick_block=page[page.index('balance-filters-quick',form_start):page.index('balance-filters-advanced',form_start)]
        self.assertIn(">Data<",quick_block)
        self.assertIn(">Tipo<",quick_block)
        self.assertIn(">Cerca<",quick_block)
        self.assertIn('<details class="balance-filters-advanced">',page[form_start:])
        self.assertIn("Filtri avanzati",page[form_start:])
        advanced_block=page[page.index('balance-filters-advanced',form_start):page.index('balance-filter-actions',form_start)]
        for label in ("Periodo","Categoria","Collaboratore","Metodo pagamento","Operatore"):
            self.assertIn(f">{label}<",advanced_block)
        # an advanced-only field (categoria) must still actually filter results
        redirects=[]
        self.handler.path="/bilanci?categoria=D&periodo=tutto"
        self.handler.balances_page(admin)
        self.assertIn('<option value="D" selected>D</option>',rendered[-1])

    def test_balances_manual_toolbar_buttons_are_compact_and_color_coded(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/bilanci"
        self.handler.balances_page(admin)
        page=rendered[-1]
        self.assertIn('class="btn balance-quick-btn balance-quick-income"',page)
        self.assertIn('class="btn balance-quick-btn balance-quick-expense"',page)

    def test_balance_move_card_menu_js_is_wired(self):
        self.assertIn("function toggleBalanceMoveMenu(btn)", app.APP_JS)
        self.assertIn("balance-move-menu-popover", app.APP_JS)

    def test_balances_date_range_stacks_on_mobile_to_avoid_horizontal_overflow(self):
        # regression: two native date inputs side-by-side never shrink below
        # their own rendering floor (~169px each), so at phone widths they
        # overflowed the filters card by ~12px even with min-width:0 and
        # flex-wrap - only forcing them to stack vertically fixed it.
        self.assertIn(".balance-date-range{flex-direction:column;align-items:stretch}.balance-date-range input{width:100%}",app.CSS)

    def test_bilanci_elimina_button_really_deletes_legacy_synthesized_rows(self):
        # Practices created before the balance_movements ledger existed only
        # have their payment history in payment_movements, so Bilanci
        # synthesizes a row on the fly with a negative synthetic id. Elimina
        # must genuinely delete that payment_movements row, not append a
        # storno, for those rows too, not just real balance_movements ones.
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-LEGACYVOID","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Bilbo","Cremazione singola","Pagato","150","150")).lastrowid
            pm_id=conn.execute("""INSERT INTO payment_movements(practice_id,payment_type,payment_channel,payment_method,movement_category,amount,paid_at,user_id,notes,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",(pid,"saldo","W","Contanti","W",150.0,"2026-07-10",admin["id"],"","2026-07-10T10:00:00")).lastrowid
        with app.db() as conn:
            movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters(include_technical=True))
        legacy=[m for m in movements if m.practice_id==pid and m.id<0]
        self.assertEqual(len(legacy),1)
        legacy_key=legacy[0].idempotency_key
        self.assertEqual(legacy[0].id,-pm_id)
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/bilanci?view=entrate-w&periodo=tutto"
        self.handler.balances_page(admin)
        page=rendered[-1]
        self.assertIn('/bilanci/movimenti/elimina-storico?legacy_key=',page)
        self.assertIn(quote(legacy_key,safe=''),page)
        confirm_rendered=[];self.handler.send_html=lambda content,*args:confirm_rendered.append(content)
        self.handler.path=f"/bilanci/movimenti/elimina-storico?legacy_key={quote(legacy_key,safe='')}&return_to=%2Fbilanci"
        self.handler.confirm_balance_legacy_movement_delete(admin)
        confirm_page=confirm_rendered[-1]
        self.assertIn('action="/bilanci/movimenti/elimina-conferma-storico"',confirm_page)
        self.assertIn(f'value="{legacy_key}"',confirm_page)
        self.assertIn("definitiva e non reversibile",confirm_page)
        redirects=[];self.handler.redirect=lambda url:redirects.append(url)
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":legacy_key}
        self.handler.balance_legacy_movement_delete(admin)
        self.assertTrue(redirects and "movimento_stornato=1" in redirects[-1])
        with app.db() as conn:
            # the payment_movements row is genuinely gone from the table,
            # not just excluded from the ledger view by a storno.
            self.assertIsNone(conn.execute("SELECT id FROM payment_movements WHERE id=?",(pm_id,)).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM balance_movements WHERE idempotency_key LIKE ?",(f"%{legacy_key}%",)).fetchone()[0],0)
            practice=conn.execute("SELECT payment_status FROM practices WHERE id=?",(pid,)).fetchone()
            self.assertEqual(practice["payment_status"],"Da saldare")
        with app.db() as conn:
            movements_after=app.get_balance_movements(conn,filters=app.normalize_balance_filters(include_technical=True))
        self.assertFalse(any(m.idempotency_key==legacy_key for m in movements_after))
        with app.db() as conn:
            default_movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters())
        self.assertFalse(any(m.practice_id==pid for m in default_movements))
        # Retrying must not crash: the row is already gone, so it's reported
        # as "not found" rather than deleted a second time.
        pages=[];self.handler.balances_page=lambda user,error="",expense_draft=None:pages.append(error)
        self.handler.balance_legacy_movement_delete(admin)
        self.assertIn("non trovato",pages[-1])

    def test_bilanci_elimina_button_really_deletes_pre_payment_movements_legacy_rows(self):
        # Practices old enough to predate the payment_movements table entirely
        # store their acconto/saldo straight on the practices row (deposit_final/
        # paid_at etc) with zero payment_movements rows at all. Bilanci still
        # synthesizes a movement for them (the 'historical-practice:' id family),
        # and Elimina on those must genuinely remove them too.
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,total_text,deposit_final,deposit_paid_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-OLDLEGACY","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Bilbo","Cremazione singola","Acconto","350","100.00","2026-07-10")).lastrowid
        with app.db() as conn:
            has_pm=conn.execute("SELECT COUNT(*) FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()[0]
        self.assertEqual(has_pm,0)
        with app.db() as conn:
            movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters(include_technical=True))
        legacy=[m for m in movements if m.practice_id==pid]
        self.assertEqual(len(legacy),1)
        legacy_key=legacy[0].idempotency_key
        self.assertLessEqual(legacy[0].id,-1000000000)
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content)
        self.handler.path="/bilanci?view=entrate-d&periodo=tutto"
        self.handler.balances_page(admin)
        page=rendered[-1]
        self.assertIn(quote(legacy_key,safe=''),page)
        confirm_rendered=[];self.handler.send_html=lambda content,*args:confirm_rendered.append(content)
        self.handler.path=f"/bilanci/movimenti/elimina-storico?legacy_key={quote(legacy_key,safe='')}&return_to=%2Fbilanci"
        self.handler.confirm_balance_legacy_movement_delete(admin)
        confirm_page=confirm_rendered[-1]
        self.assertIn('action="/bilanci/movimenti/elimina-conferma-storico"',confirm_page)
        redirects=[];self.handler.redirect=lambda url:redirects.append(url)
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":legacy_key}
        self.handler.balance_legacy_movement_delete(admin)
        self.assertTrue(redirects and "movimento_stornato=1" in redirects[-1],f"redirects={redirects}")
        with app.db() as conn:
            movements_after=app.get_balance_movements(conn,filters=app.normalize_balance_filters(include_technical=True))
        self.assertFalse(any(m.idempotency_key==legacy_key for m in movements_after),"il movimento dovrebbe sparire dalla lista dopo l'eliminazione")
        with app.db() as conn:
            default_movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters())
        self.assertFalse(any(m.practice_id==pid for m in default_movements),"il movimento (e la sua storno tecnica) non devono restare visibili nella vista di default")
        with app.db() as conn:
            deletions=app.get_recent_balance_movement_deletions(conn,limit=10)
        self.assertEqual(deletions[0]["practice_number_snapshot"],"CR-OLDLEGACY")
        self.assertEqual(deletions[0]["amount_cents"],10000)
        log_rendered=[];self.handler.send_html=lambda content,*args:log_rendered.append(content)
        self.handler.path="/bilanci"
        self.handler.balances_page(admin)
        self.assertIn("Movimenti eliminati di recente",log_rendered[-1])
        self.assertIn("CR-OLDLEGACY",log_rendered[-1])

    def test_restore_undoes_a_real_balance_movement_delete(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            movement = app.create_balance_movement(
                conn, amount_cents=5000, movement_date="2026-07-10", category="W",
                ledger_section="Entrata", movement_type="Entrata manuale",
                idempotency_key="restore-test-1", payment_method="Contanti",
                description="Entrata da ripristinare", source="manual_income", created_by=admin["id"],
            )
        redirects=[];self.handler.redirect=lambda url:redirects.append(url)
        self.handler.form=lambda:{"return_to":"/bilanci"}
        self.handler.balance_movement_delete(admin,movement.id)
        self.assertTrue(redirects and "movimento_stornato=1" in redirects[-1])
        with app.db() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM balance_movements WHERE id=?",(movement.id,)).fetchone())
            deletion_id=conn.execute("SELECT id FROM balance_movement_deletions ORDER BY id DESC LIMIT 1").fetchone()["id"]
        redirects.clear()
        self.handler.form=lambda:{"return_to":"/bilanci"}
        self.handler.balance_movement_deletion_restore(admin,deletion_id)
        self.assertTrue(redirects and "movimento_ripristinato=1" in redirects[-1],f"redirects={redirects}")
        with app.db() as conn:
            restored=conn.execute("SELECT amount_cents,description,movement_type FROM balance_movements WHERE idempotency_key=?",("restore-test-1",)).fetchone()
        self.assertIsNotNone(restored)
        self.assertEqual((restored["amount_cents"],restored["description"],restored["movement_type"]),(5000,"Entrata da ripristinare","Entrata manuale"))
        # restoring the same deletion twice must fail cleanly, not crash or double-insert.
        pages=[];self.handler.balances_page=lambda user,error="",expense_draft=None:pages.append(error)
        self.handler.balance_movement_deletion_restore(admin,deletion_id)
        self.assertTrue(pages and pages[-1])
        with app.db() as conn:
            count=conn.execute("SELECT COUNT(*) FROM balance_movements WHERE idempotency_key=?",("restore-test-1",)).fetchone()[0]
        self.assertEqual(count,1)

    def test_restore_undoes_a_payment_movements_backed_legacy_delete(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,price_cremation,total_service)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-RESTOREPM","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Bilbo","Cremazione singola","Pagato","150","150")).lastrowid
            pm_id=conn.execute("""INSERT INTO payment_movements(practice_id,payment_type,payment_channel,payment_method,movement_category,amount,paid_at,user_id,notes,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",(pid,"saldo","W","Contanti","W",150.0,"2026-07-10",admin["id"],"","2026-07-10T10:00:00")).lastrowid
        with app.db() as conn:
            movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters(include_technical=True))
        legacy_key=[m for m in movements if m.practice_id==pid and m.id<0][0].idempotency_key
        redirects=[];self.handler.redirect=lambda url:redirects.append(url)
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":legacy_key}
        self.handler.balance_legacy_movement_delete(admin)
        self.assertTrue(redirects and "movimento_stornato=1" in redirects[-1])
        with app.db() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM payment_movements WHERE id=?",(pm_id,)).fetchone())
            self.assertEqual(conn.execute("SELECT payment_status FROM practices WHERE id=?",(pid,)).fetchone()["payment_status"],"Da saldare")
            deletion_id=conn.execute("SELECT id FROM balance_movement_deletions ORDER BY id DESC LIMIT 1").fetchone()["id"]
        redirects.clear()
        self.handler.form=lambda:{"return_to":"/bilanci"}
        self.handler.balance_movement_deletion_restore(admin,deletion_id)
        self.assertTrue(redirects and "movimento_ripristinato=1" in redirects[-1],f"redirects={redirects}")
        with app.db() as conn:
            restored_pm=conn.execute("SELECT payment_type,amount FROM payment_movements WHERE practice_id=?",(pid,)).fetchone()
            practice_after=conn.execute("SELECT payment_status FROM practices WHERE id=?",(pid,)).fetchone()
        self.assertIsNotNone(restored_pm)
        self.assertEqual((restored_pm["payment_type"],float(restored_pm["amount"])),("saldo",150.0))
        self.assertEqual(practice_after["payment_status"],"Pagato")

    def test_restore_undoes_a_pre_payment_movements_legacy_void_delete(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                owner_first_name,service_type,payment_status,total_text,deposit_final,deposit_paid_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",("CR-RESTOREVOID","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Bilbo","Cremazione singola","Acconto","350","100.00","2026-07-10")).lastrowid
        with app.db() as conn:
            movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters(include_technical=True))
        legacy_key=[m for m in movements if m.practice_id==pid][0].idempotency_key
        redirects=[];self.handler.redirect=lambda url:redirects.append(url)
        self.handler.form=lambda:{"return_to":"/bilanci","legacy_key":legacy_key}
        self.handler.balance_legacy_movement_delete(admin)
        self.assertTrue(redirects and "movimento_stornato=1" in redirects[-1])
        with app.db() as conn:
            default_movements=app.get_balance_movements(conn,filters=app.normalize_balance_filters())
        self.assertFalse(any(m.practice_id==pid for m in default_movements))
        with app.db() as conn:
            deletion_id=conn.execute("SELECT id FROM balance_movement_deletions ORDER BY id DESC LIMIT 1").fetchone()["id"]
        redirects.clear()
        self.handler.form=lambda:{"return_to":"/bilanci"}
        self.handler.balance_movement_deletion_restore(admin,deletion_id)
        self.assertTrue(redirects and "movimento_ripristinato=1" in redirects[-1],f"redirects={redirects}")
        with app.db() as conn:
            default_movements_after=app.get_balance_movements(conn,filters=app.normalize_balance_filters())
        restored=[m for m in default_movements_after if m.practice_id==pid]
        self.assertEqual(len(restored),1)
        self.assertEqual(restored[0].idempotency_key,legacy_key)

    def test_any_uncaught_exception_in_a_route_shows_an_error_page_instead_of_hanging(self):
        # do_GET/do_POST used to have no top-level exception handling at all:
        # anything other than the specific errors each handler already
        # catches (BalanceError, sqlite3.Error, ...) would propagate out of
        # the method entirely. socketserver's default handle_error then just
        # prints the traceback and drops the connection with zero HTTP
        # response, which from the browser looks exactly like "clicked the
        # button, nothing happened, no error" — a symptom reported repeatedly
        # for Bilanci's Elimina button. This must never happen again: any
        # route that blows up has to render a visible error page instead.
        self.handler.headers={}
        self.handler.path="/bilanci"
        self.handler._route_get=lambda:(_ for _ in ()).throw(KeyError("colonna_inesistente"))
        rendered=[];self.handler.send_html=lambda content,status=200:rendered.append((content,status))
        self.handler.do_GET()
        self.assertTrue(rendered)
        self.assertEqual(rendered[-1][1],500)
        self.assertIn("Errore imprevisto",rendered[-1][0])
        self.assertIn("KeyError",rendered[-1][0])
        self.handler.path="/bilanci/movimenti/1/elimina-conferma"
        self.handler._route_post=lambda:(_ for _ in ()).throw(TypeError("dato non valido"))
        rendered.clear()
        self.handler.do_POST()
        self.assertTrue(rendered)
        self.assertEqual(rendered[-1][1],500)
        self.assertIn("TypeError",rendered[-1][0])

    def test_client_disconnect_mid_response_is_logged_quietly_without_a_second_failed_write(self):
        # Real Render logs showed a request that failed with a BalanceError,
        # got routed to balances_page to render a visible error message, but
        # the phone had already dropped the connection (backgrounded tab /
        # flaky mobile network) by the time the server tried to send it —
        # so writing that error page raised BrokenPipeError too, escaping
        # uncaught and getting logged exactly like a real bug. There is no
        # client left to receive anything, so this must never attempt a
        # second write: just log quietly and stop.
        self.handler.headers={}
        self.handler.path="/bilanci/movimenti/1/elimina-conferma"
        self.handler._route_post=lambda:(_ for _ in ()).throw(BrokenPipeError(32,"Broken pipe"))
        write_attempts=[]
        self.handler.send_html=lambda content,status=200:write_attempts.append((content,status))
        self.handler.error_page=lambda *a,**k:write_attempts.append(("error_page",a,k))
        self.handler.do_POST()
        self.assertEqual(write_attempts,[])

    def test_db_always_closes_the_connection_after_the_with_block(self):
        # sqlite3.Connection's own context-manager protocol only commits or
        # rolls back the transaction on `with conn:` — it does NOT close the
        # connection. Every `with db() as c:` in this codebase (hundreds of
        # call sites) was therefore leaking an open connection/file
        # descriptor for the entire lifetime of the server process, growing
        # without bound as the app kept running. db() must wrap the real
        # connection so it is always closed too, regardless of whether the
        # block raised or not.
        with app.db() as c:
            c.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")
        try:
            with app.db() as c2:
                c2.execute("SELECT 1")
                raise ValueError("boom")
        except ValueError:
            pass
        with self.assertRaises(sqlite3.ProgrammingError):
            c2.execute("SELECT 1")

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
        self.assertIn('id="ppmRemindersCard"',page)
        self.assertIn('id="ppmRemindersToggle"',page)
        self.assertIn(f'href="/pratiche/{pid}"',page)
        self.assertIn("1 pratica con dati da completare",page)
        with app.db() as conn:
            reminder_id=conn.execute(
                "SELECT id FROM reminders WHERE entity_key=?",(f"practice:{pid}",)
            ).fetchone()["id"]
        # calling dashboard again must not duplicate the same open reminder
        self.handler.dashboard(admin)
        with app.db() as conn:
            count=conn.execute(
                "SELECT COUNT(*) FROM reminders WHERE entity_key=?",(f"practice:{pid}",)
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
        self.assertEqual(product_reminder["url"],f"/prodotti#article-{article_id['id']}")
        self.handler.dashboard(admin)
        self.assertIn("1 prodotto da ordinare",rendered[-1])
        self.assertIn(f'href="/prodotti#article-{article_id["id"]}"',rendered[-1])
        # completing the practice reminder via AJAX marks it done, with an audit trail
        responses=[];self.handler.send_json=lambda obj,status=200:responses.append((obj,status))
        self.handler.form=lambda:{"ajax":"1"}
        self.handler.complete_reminder(admin,reminder_id)
        self.assertTrue(responses[-1][0]["ok"])
        with app.db() as conn:
            completed=conn.execute("SELECT completed_at,completed_by FROM reminders WHERE id=?",(reminder_id,)).fetchone()
        self.assertIsNotNone(completed["completed_at"])
        self.assertEqual(completed["completed_by"],admin["id"])
        # the underlying practice is STILL incomplete: marking "Fatto" only
        # hides this occurrence, it must NOT block the reminder forever — a
        # fresh open occurrence for the same practice reappears on next sync
        self.handler.dashboard(admin)
        self.assertIn("1 pratica con dati da completare",rendered[-1])
        with app.db() as conn:
            reopened=conn.execute(
                "SELECT id FROM reminders WHERE entity_key=? AND completed_at IS NULL",(f"practice:{pid}",)
            ).fetchone()
        self.assertIsNotNone(reopened)
        self.assertNotEqual(reopened["id"],reminder_id)
        # completing an already-completed reminder is a harmless no-op
        first_completed_at=completed["completed_at"]
        self.handler.complete_reminder(admin,reminder_id)
        with app.db() as conn:
            still=conn.execute("SELECT completed_at FROM reminders WHERE id=?",(reminder_id,)).fetchone()
        self.assertEqual(still["completed_at"],first_completed_at)
        # once the practice's data is actually completed, the reopened
        # occurrence is auto-resolved by sync_reminders on the next dashboard
        # load — no manual "Fatto" click needed once the condition is gone
        with app.db() as conn:
            conn.execute("UPDATE practices SET data_complete=1 WHERE id=?",(pid,))
        self.handler.dashboard(admin)
        self.assertNotIn("pratica con dati da completare",rendered[-1])
        with app.db() as conn:
            still_open=conn.execute(
                "SELECT id FROM reminders WHERE entity_key=? AND completed_at IS NULL",(f"practice:{pid}",)
            ).fetchone()
        self.assertIsNone(still_open)

    def test_pickup_stalled_reminder_appears_after_4_days_and_clears_on_progression(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            today=date.today()
            stalled_pickup=(today-timedelta(days=6)).isoformat()
            fresh_pickup=(today-timedelta(days=1)).isoformat()
            stalled_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                animal_name,owner_first_name,owner_last_name,pickup_date,data_complete) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-STALL1","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Birba","Mario","Conti",stalled_pickup,1)).lastrowid
            fresh_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                animal_name,owner_first_name,owner_last_name,pickup_date,data_complete) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-STALL2","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Leo","Sara","Bianchi",fresh_pickup,1)).lastrowid
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        self.assertIn("1 animale ritirato ancora da mettere in programma",page)
        self.assertIn(f'href="/pratiche/{stalled_pid}"',page)
        # a practice picked up only recently must not trigger this reminder yet
        self.assertNotIn(f'href="/pratiche/{fresh_pid}"',page)
        # once the practice moves forward, the stalled reminder auto-resolves
        with app.db() as conn:
            conn.execute("UPDATE practices SET status='In programma' WHERE id=?",(stalled_pid,))
        self.handler.dashboard(admin)
        with app.db() as conn:
            still_open=conn.execute(
                "SELECT id FROM reminders WHERE entity_key=? AND reminder_type='pickup_stalled' AND completed_at IS NULL",(f"practice:{stalled_pid}",)
            ).fetchone()
        self.assertIsNone(still_open)

    def test_delivered_unpaid_reminder_shows_remaining_amount_and_clears_when_paid(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                animal_name,owner_first_name,owner_last_name,price_cremation,total_service,deposit,payment_status,data_complete)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-UNPAID","Privato","Livorno","Consegnato",stamp,stamp,admin["id"],"Leo","Sara","Bianchi","150","150","0","Da saldare",1)).lastrowid
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        self.assertIn("1 pratica consegnata ma non pagata",page)
        self.assertIn(f'href="/pratiche/{pid}"',page)
        # once fully paid, the reminder auto-resolves on the next sync
        with app.db() as conn:
            conn.execute("UPDATE practices SET payment_status='Pagato',deposit='150' WHERE id=?",(pid,))
        self.handler.dashboard(admin)
        with app.db() as conn:
            still_open=conn.execute(
                "SELECT id FROM reminders WHERE entity_key=? AND reminder_type='delivered_unpaid' AND completed_at IS NULL",(f"practice:{pid}",)
            ).fetchone()
        self.assertIsNone(still_open)

    def test_cremation_pending_reminder_fires_immediately_for_every_cremazione_singola_ritirata(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            today=date.today()
            waiting_pickup=(today-timedelta(days=9)).isoformat()
            fresh_pickup=today.isoformat()
            pending_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,created_at,updated_at,created_by,
                animal_name,owner_first_name,owner_last_name,pickup_date,data_complete) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-CREM1","Privato","Livorno","Ritirato","Cremazione singola",stamp,stamp,admin["id"],"Nuvola","Franco","Rossi",waiting_pickup,1)).lastrowid
            # fires the same day too: the count must match the full Programma Cremazioni list, not just stalled ones
            fresh_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,created_at,updated_at,created_by,
                animal_name,owner_first_name,owner_last_name,pickup_date,data_complete) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-CREM3","Privato","Livorno","Ritirato","Cremazione singola",stamp,stamp,admin["id"],"Luna","Paolo","Neri",fresh_pickup,1)).lastrowid
            # same wait, but a collective cremation must NOT trigger this specific reminder type
            collettiva_pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,service_type,created_at,updated_at,created_by,
                animal_name,owner_first_name,owner_last_name,pickup_date,data_complete) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-CREM2","Privato","Livorno","Ritirato","Cremazione collettiva",stamp,stamp,admin["id"],"Rex","Anna","Verdi",waiting_pickup,1)).lastrowid
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        self.assertIn("2 cremazioni singole in attesa",page)
        self.assertIn('href="/programma-cremazioni"',page)
        with app.db() as conn:
            fresh_reminder=conn.execute(
                "SELECT title FROM reminders WHERE reminder_type='cremation_pending' AND entity_key=?",(f"practice:{fresh_pid}",)
            ).fetchone()
            collettiva_reminder=conn.execute(
                "SELECT id FROM reminders WHERE reminder_type='cremation_pending' AND entity_key=?",(f"practice:{collettiva_pid}",)
            ).fetchone()
        self.assertIsNotNone(fresh_reminder)
        self.assertIn("da oggi",fresh_reminder["title"])
        self.assertIsNone(collettiva_reminder)
        # once queued into the cremation program (status moves to 'In programma'), it auto-resolves
        with app.db() as conn:
            conn.execute("UPDATE practices SET status='In programma' WHERE id=?",(pending_pid,))
        self.handler.dashboard(admin)
        with app.db() as conn:
            still_open=conn.execute(
                "SELECT id FROM reminders WHERE entity_key=? AND reminder_type='cremation_pending' AND completed_at IS NULL",(f"practice:{pending_pid}",)
            ).fetchone()
        self.assertIsNone(still_open)

    def test_reminder_day_counts_refresh_on_every_sync_without_duplicating_the_open_row(self):
        # An open occurrence's text must stay accurate (6 giorni -> 7 giorni...)
        # even though ensure_reminder() only ever updates it in place instead
        # of inserting a second row for the same still-open condition.
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            pickup=(date.today()-timedelta(days=6)).isoformat()
            pid=conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                animal_name,owner_first_name,owner_last_name,pickup_date,data_complete) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-REFRESH","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Birba","Mario","Conti",pickup,1)).lastrowid
        with app.db() as conn:
            app.sync_reminders(conn)
        with app.db() as conn:
            conn.execute("UPDATE practices SET pickup_date=? WHERE id=?",((date.today()-timedelta(days=8)).isoformat(),pid))
            app.sync_reminders(conn)
            rows=conn.execute("SELECT title FROM reminders WHERE entity_key=? AND completed_at IS NULL",(f"practice:{pid}",)).fetchall()
        self.assertEqual(len(rows),1)
        self.assertIn("da 8 giorni",rows[0]["title"])

    def test_reminder_badge_shows_total_open_count_on_dashboard_nav_icon(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name,data_complete)
                VALUES(?,?,?,?,?,?,?,?,?)""",("CR-BADGE","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],"Fido",0))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        with app.db() as conn:
            open_count=conn.execute("SELECT count(*) n FROM reminders WHERE completed_at IS NULL").fetchone()["n"]
        self.assertGreaterEqual(open_count,1)
        match=re.search(r'href="/" class="nav-notification">.*?<span class="notification-badge">(\d+)</span>',page)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)),open_count)

    def test_weekly_report_section_reuses_bilanci_totals_for_last_7_days(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            today_rome=datetime.now(app.ROME_TZ).date()
            app.create_balance_income(conn,category="W",movement_date=today_rome.isoformat(),
                                       amount_cents=12000,payment_method="Contanti",description="Entrata di prova",
                                       idempotency_key="test-weekly-report-income",created_by=admin["id"])
            filters=app.normalize_balance_filters(date_from=(today_rome-timedelta(days=6)).isoformat(),date_to=today_rome.isoformat())
            expected=app.get_balance_snapshot(conn,filters=filters)
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        self.assertIn("Report della settimana",page)
        self.assertIn(app.money_cents_it(expected.sections["entrate-w"].total_cents),page)
        self.assertIn(app.money_cents_it(expected.sections["entrate-d"].total_cents),page)
        self.assertNotIn("Saldo netto",page)
        self.assertIn('data_iniziale='+(today_rome-timedelta(days=6)).isoformat(),page)

    def test_reminders_card_collapsed_by_default_matching_the_compact_mockup(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        self.assertIn('<section class="reminders-card" id="ppmRemindersCard">',page)
        self.assertIn('aria-expanded="false"',page)
        self.assertIn("Nessuna attività attiva · Report della settimana",page)
        # no popup/overlay of any kind - a plain in-place expanding card
        card_start=page.index('<section class="reminders-card"')
        card_end=page.index('</section>',card_start)
        self.assertNotIn("payment-popover",page[card_start:card_end])

    def test_reminders_card_group_with_multiple_items_links_to_the_list_not_a_single_practice(self):
        with app.db() as conn:
            admin=conn.execute("SELECT * FROM users WHERE username='admin'").fetchone();stamp=app.now()
            today=date.today();pickup=(today-timedelta(days=6)).isoformat()
            for suffix,animal in (("A","Uno"),("B","Due")):
                conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                    animal_name,pickup_date,data_complete) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (f"CR-MULTI{suffix}","Privato","Livorno","Ritirato",stamp,stamp,admin["id"],animal,pickup,1))
        rendered=[];self.handler.send_html=lambda content,*args:rendered.append(content);self.handler.path="/"
        self.handler.dashboard(admin)
        page=rendered[-1]
        self.assertIn("2 animali ritirati ancora da mettere in programma",page)
        self.assertIn('href="/archivio/pratiche?stato=Ritirato"',page)

    def test_reminders_card_js_and_css_use_a_smooth_expanding_card_not_a_popup(self):
        self.assertIn("function setupRemindersCard()", app.APP_JS)
        self.assertIn("ppmRemindersCard", app.APP_JS)
        self.assertIn("ppmRemindersToggle", app.APP_JS)
        self.assertNotIn("ppmRemindersOverlay", app.APP_JS)
        self.assertNotIn("ppmOpenReminders", app.APP_JS)
        self.assertIn(".reminders-card-body{max-height:0;overflow:hidden;transition:max-height .35s ease}", app.CSS)
        self.assertIn("body.style.maxHeight=open?body.scrollHeight+'px':'0px';", app.APP_JS)

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

    def test_payment_area_lists_all_w_fields_before_all_d_fields_with_qualified_labels(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        form_html = app.App._fields_html(self.handler, None, admin)
        ordered_markers = [
            'id="paymentEstremiRow"',
            'id="paymentTotaleWRow"',
            'name="acconto_w_totale"', 'name="acconto_w_data"', 'name="acconto_w_modalita"',
            'name="acconto_w_fattura_numero"', 'name="acconto_w_fattura_data"', 'name="acconto_w_fattura_totale"',
            'name="saldo_w_totale"', 'name="saldo_w_data"', 'name="saldo_w_modalita"',
            'name="saldo_w_fattura_numero"', 'name="saldo_w_fattura_data"', 'name="saldo_w_fattura_totale"',
            'id="paymentTotaleDRow"',
            'name="acconto_d_totale"', 'name="acconto_d_data"',
            'name="saldo_d_totale"', 'name="saldo_d_data"',
        ]
        positions = [form_html.index(marker) for marker in ordered_markers]
        self.assertEqual(positions, sorted(positions), "i campi della sezione Pagamento devono seguire l'ordine: estremi, Totale W, Acconto W, Rimanenza W, Totale D, Acconto D, Rimanenza D")
        self.assertIn('<label>Acconto W €</label>', form_html)
        self.assertIn('<label>Data Acconto W</label>', form_html)
        self.assertIn('<label>Metodo di pagamento Acconto W</label>', form_html)
        self.assertIn('<label>Numero fattura Acconto W</label>', form_html)
        self.assertIn('<label>Data fattura Acconto W</label>', form_html)
        self.assertIn('<label>Totale fattura Acconto W €</label>', form_html)
        self.assertIn('<label>Rimanenza W €</label>', form_html)
        self.assertIn('<label>Data Rimanenza W</label>', form_html)
        self.assertIn('<label>Metodo di pagamento Rimanenza W</label>', form_html)
        self.assertIn('<label>Numero fattura Rimanenza W</label>', form_html)
        self.assertIn('<label>Acconto D €</label>', form_html)
        self.assertIn('<label>Data Acconto D</label>', form_html)
        self.assertIn('<label>Rimanenza D €</label>', form_html)
        self.assertIn('<label>Data Rimanenza D</label>', form_html)
        # The D circuito never shows/requires a payment method or an invoice.
        self.assertNotIn('name="acconto_d_modalita"', form_html)
        self.assertNotIn('name="saldo_d_modalita"', form_html)
        self.assertNotIn('name="acconto_d_fattura_numero"', form_html)
        self.assertNotIn('name="saldo_d_fattura_numero"', form_html)
        # Relocation JS now fires unconditionally: create and edit share the
        # very same Pagamento section, so there is no isEditForm branch left.
        self.assertNotIn("isEditForm", app.APP_JS)
        self.assertIn("paymentSection.append(wrap)", app.APP_JS)
        self.assertIn("function updateMacroRimanenza(){", app.APP_JS)

    def test_saldo_totale_autofills_from_totale_minus_acconto_until_manually_edited(self):
        # SALDO W/D (relabeled Rimanenza W/D) must auto-fill live from
        # Totale-Acconto for its own circuito, and stop being overwritten
        # the moment the user types their own value in that field.
        self.assertIn("if(saldoW && saldoW.dataset.autoFilled!=='0') saldoW.value=ppmFormat(Math.max(0,totalW-accontoW));", app.APP_JS)
        self.assertIn("if(saldoD && saldoD.dataset.autoFilled!=='0') saldoD.value=ppmFormat(Math.max(0,totalD-accontoD));", app.APP_JS)
        self.assertIn("e.target.dataset.autoFilled='0';", app.APP_JS)
        self.assertIn("if(touchedField) touchedField.value='1';", app.APP_JS)

    def test_payment_area_main_fields_are_visually_more_prominent_than_their_sub_fields(self):
        # TOTALE W/D, ACCONTO W/D and RIMANENZA W/D must read as the main
        # voices; Data/Metodo/Numero fattura/etc. are their sub-voci and
        # must look visually subordinate (smaller, muted), purely via CSS —
        # no markup/order/logic change.
        self.assertIn(
            '#paymentTotaleWRow .field label,#paymentTotaleDRow .field label,.payment-macroarea-channel .fields .field:first-child label{font-size:15px;font-weight:800;text-transform:uppercase;letter-spacing:.03em}',
            app.CSS,
        )
        self.assertIn(
            '.payment-macroarea-channel .fields .field:not(:first-child) label{font-size:11px;font-weight:600;color:var(--muted)}',
            app.CSS,
        )

    def test_payment_popover_is_compact_on_mobile_but_stays_scrollable(self):
        # The popup opened from the practice's Riepilogo (and from any list
        # row) must fit a mobile screen without scrolling in the common case;
        # .payment-dialog keeps overflow:auto (see its base rule) so a very
        # small screen can still scroll instead of clipping content.
        self.assertIn("@media(max-width:520px){.payment-popover{padding:6px}", app.CSS)
        self.assertIn(".payment-dialog{padding:12px 10px;max-height:97dvh}", app.CSS)
        self.assertIn(".payment-dialog .sub{display:none}", app.CSS)
        self.assertIn(".payment-dialog{width:min(620px,100%);max-height:90dvh;overflow:auto;", app.CSS)

    def test_new_page_with_error_field_skips_top_banner_and_targets_the_field(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.new_page(admin, draft={"saldo_d_totale": "250"}, error="Indica una data valida per Rimanenza D.", error_field="saldo_d_data")
        page = rendered[-1]
        self.assertNotIn('<div class="flash warning">', page)
        self.assertIn('id="formErrorField" value="saldo_d_data"', page)
        self.assertIn('id="formErrorMessage" value="Indica una data valida per Rimanenza D."', page)

    def test_new_page_without_error_field_still_shows_the_top_banner(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.new_page(admin, draft={}, error="Campi obbligatori mancanti: Cognome")
        page = rendered[-1]
        self.assertIn('<div class="flash warning">Campi obbligatori mancanti: Cognome</div>', page)
        self.assertNotIn('id="formErrorField"', page)

    def test_show_field_error_js_scrolls_and_highlights_the_target_field(self):
        self.assertIn("function showFieldError(){", app.APP_JS)
        self.assertIn("wrap.scrollIntoView({behavior:'smooth',block:'center'});", app.APP_JS)
        self.assertIn(".field-error input,.field-error select,.field-error textarea{border-color:#ef4444}", app.CSS)

    def test_open_payment_popover_moves_itself_to_body_to_escape_tablebox_scroll(self):
        # A row's .payment-popover lives inside .tablebox, which sets
        # -webkit-overflow-scrolling:touch for smooth mobile scroll — on iOS
        # Safari that turns the table into the containing block for any
        # position:fixed descendant, trapping the popover inside the table
        # instead of covering the screen. Moving it to <body> on open avoids
        # that everywhere the payment popover is used (Dashboard list, etc.).
        self.assertIn("if(target.parentElement!==document.body)document.body.appendChild(target);", app.APP_JS)

    def test_create_practice_missing_required_field_also_jumps_to_it(self):
        # Not just the Acconto/Rimanenza macroarea errors: the common
        # "Campi obbligatori mancanti" validation error must also target
        # the first missing field instead of only banner-ing at the top.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_phone": "333", "owner_tax_code": "X",
            "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
        }
        self.handler.create_practice(admin)
        page = rendered[-1]
        self.assertNotIn('<div class="flash warning">', page)
        self.assertIn('id="formErrorField" value="owner_last_name"', page)

    def test_validation_error_field_skips_exempt_cases_and_respects_vet_sender(self):
        self.assertEqual(self.handler.validation_error_field({"tag_da_richiamare":"Si"}), "")
        self.assertEqual(self.handler.validation_error_field({"service_type":"Cremazione collettiva"}), "")
        self.assertEqual(self.handler.validation_error_field({"request_origin":"Collaboratore"}), "")
        # A veterinarian sender doesn't need owner_last_name/phone/etc, so the
        # first real gap should be operator_name, not one of those exempted.
        self.assertEqual(self.handler.validation_error_field({"owner_veterinarian_id":"5"}), "operator_name")

    def test_service_type_is_required_and_not_preselected(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        form_html = app.App._fields_html(self.handler, None, admin)
        self.assertIn('<option value="" selected>SELEZIONA</option>', form_html)
        self.assertNotIn('<option selected>Da decidere</option>', form_html)

    def test_signature_section_sits_right_before_documento_e_accettazione(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        form_html = app.App._fields_html(self.handler, None, admin)
        self.assertIn('<h2>Firma proprietario</h2>', form_html)
        # The signing surface must live behind a button, inside a hidden
        # fullscreen overlay — never directly on the practice form, which
        # carries the owner's personal/medical data and could otherwise be
        # read by whoever is handed the device to sign.
        self.assertIn('id="ppmOpenSignaturePad"', form_html)
        self.assertIn('id="ppmSignatureOverlay" hidden', form_html)
        self.assertIn('id="ppmSignaturePad"', form_html)
        self.assertIn('id="ppmSignatureDataInput"', form_html)
        self.assertIn('id="ppmSaveSignaturePad"', form_html)
        self.assertIn('id="ppmClearSignaturePad"', form_html)
        self.assertIn('id="ppmRemoveSignature"', form_html)
        # not required to save: no `required` attribute anywhere near it.
        signature_pos = form_html.index('<h2>Firma proprietario</h2>')
        acceptance_pos = form_html.index('<h2>Documento e accettazione</h2>')
        self.assertLess(signature_pos, acceptance_pos)
        between = form_html[signature_pos:acceptance_pos]
        self.assertNotIn('required', between)

    def test_signature_pad_js_opens_fullscreen_and_crops_to_the_drawn_ink(self):
        js = app.APP_JS
        self.assertIn("function setupSignaturePad(){", js)
        # opens/closes the overlay via a button, not inline on the form.
        self.assertIn("openBtn.addEventListener('click',open);", js)
        self.assertIn("overlay.hidden=false;document.body.style.overflow='hidden';", js)
        # exports only the drawn ink's bounding box (+padding), not the whole
        # (mostly blank) fullscreen canvas — otherwise a small signature
        # drawn in the middle of a big pad shrinks to near-invisible once
        # preserveAspectRatio scales the full canvas down into the small PDF box.
        self.assertIn("function trackPoint(p){", js)
        self.assertIn("cropCanvas.getContext('2d').drawImage(canvas,x0*d,y0*d,cropW*d,cropH*d,0,0,cropW*d,cropH*d);", js)
        self.assertIn("dataInput.value=cropCanvas.toDataURL('image/png');", js)
        self.assertIn("setupSignaturePad();", js)

    def test_create_practice_stores_signature_data(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        signature = "data:image/png;base64,aGVsbG8="
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI",
            "owner_zip": "57100", "signature_data": signature,
        }
        redirects = []; self.handler.redirect = lambda url: redirects.append(url)
        self.handler.create_practice(admin)
        pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            stored = conn.execute("SELECT signature_data FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(stored["signature_data"], signature)

    def test_create_practice_without_signature_is_not_blocked(self):
        # Facoltativa: creating (and saving) a practice must work exactly
        # the same with no signature at all.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Bianchi", "owner_phone": "333",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI",
            "owner_zip": "57100",
        }
        redirects = []; self.handler.redirect = lambda url: redirects.append(url)
        self.handler.create_practice(admin)
        self.assertTrue(redirects and "/pratiche/" in redirects[-1])
        pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as conn:
            stored = conn.execute("SELECT signature_data FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(stored["signature_data"], "")

    def test_edit_submit_updates_signature_and_logs_a_clean_history_entry(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   owner_first_name,owner_last_name,owner_phone,owner_tax_code,owner_street,owner_city,owner_province,owner_zip,
                   service_type,payment_status,total_service,signature_data)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-SIGN-EDIT", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Anna", "Neri",
                 "3331112222", "NRIANN80A01H501U", "Via Test", "Livorno", "LI", "57100", "Da decidere",
                 "Da saldare", "250", ""),
            ).lastrowid
        signature = "data:image/png;base64,aGVsbG8="
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Da decidere", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Neri", "owner_phone": "3331112222",
            "owner_tax_code": "NRIANN80A01H501U", "owner_street": "Via Test", "owner_city": "Livorno",
            "owner_province": "LI", "owner_zip": "57100", "signature_data": signature,
        }
        self.handler.redirect = lambda url: None
        self.handler.edit_submit(admin, pid)
        with app.db() as conn:
            stored = conn.execute("SELECT signature_data FROM practices WHERE id=?", (pid,)).fetchone()
            history = conn.execute(
                "SELECT event_type,new_value FROM practice_history WHERE practice_id=? ORDER BY id DESC LIMIT 1", (pid,)
            ).fetchone()
        self.assertEqual(stored["signature_data"], signature)
        # the huge base64 blob must never be dumped into practice_history —
        # only this clean, human-readable marker.
        self.assertEqual((history["event_type"], history["new_value"]), ("Firma proprietario", "Firma salvata"))

    def test_edit_submit_unchanged_signature_does_not_log_anything(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            signature = "data:image/png;base64,aGVsbG8="
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   owner_first_name,owner_last_name,owner_phone,owner_tax_code,owner_street,owner_city,owner_province,owner_zip,
                   service_type,payment_status,total_service,signature_data)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-SIGN-NOCHANGE", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Anna", "Neri",
                 "3331112222", "NRIANN80A01H501U", "Via Test", "Livorno", "LI", "57100", "Da decidere",
                 "Da saldare", "250", signature),
            ).lastrowid
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Da decidere", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Neri", "owner_phone": "3331112222",
            "owner_tax_code": "NRIANN80A01H501U", "owner_street": "Via Test", "owner_city": "Livorno",
            "owner_province": "LI", "owner_zip": "57100", "signature_data": signature,
        }
        self.handler.redirect = lambda url: None
        self.handler.edit_submit(admin, pid)
        with app.db() as conn:
            # Only asserting the signature-specific side effect stays quiet;
            # other fields (e.g. invoice_total auto-filling from the now
            # server-recomputed total_service) are free to log their own
            # "Modifica ..." entries — unrelated to what this test covers.
            signature_entries = conn.execute(
                "SELECT COUNT(*) n FROM practice_history WHERE practice_id=? AND event_type='Firma proprietario'", (pid,)
            ).fetchone()["n"]
            stored = conn.execute("SELECT signature_data FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(signature_entries, 0)
        self.assertEqual(stored["signature_data"], signature)

    def test_edit_submit_regenerates_the_finalized_ddt_when_signature_changes(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   owner_first_name,owner_last_name,owner_phone,owner_tax_code,owner_street,owner_city,owner_province,owner_zip,
                   service_type,payment_status,total_service,signature_data,ddt_number,ddt_date,ddt_pdf)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-SIGN-DDT", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Anna", "Neri",
                 "3331112222", "NRIANN80A01H501U", "Via Test", "Livorno", "LI", "57100", "Da decidere",
                 "Da saldare", "250", "", 1, "2026-07-01", "DDT-000001-CR-SIGN-DDT.pdf"),
            ).lastrowid
        signature = "data:image/png;base64,aGVsbG8="
        self.handler.form = lambda: {
            "operator_name": "FILIPPO", "service_type": "Da decidere", "request_origin": "Privato",
            "owner_first_name": "Anna", "owner_last_name": "Neri", "owner_phone": "3331112222",
            "owner_tax_code": "NRIANN80A01H501U", "owner_street": "Via Test", "owner_city": "Livorno",
            "owner_province": "LI", "owner_zip": "57100", "signature_data": signature,
        }
        self.handler.redirect = lambda url: None
        with patch("app.generate_ddt") as mocked:
            self.handler.edit_submit(admin, pid)
        self.assertEqual(mocked.call_count, 1)
        called_practice, template_path, output_path = mocked.call_args[0]
        self.assertEqual(called_practice["practice_number"], "CR-SIGN-DDT")
        self.assertEqual(output_path.name, "DDT-000001-CR-SIGN-DDT.pdf")

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

    def test_owner_veterinarian_as_sender_does_not_require_personal_sender_fields(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone(); stamp = app.now()
            vet_id = conn.execute("""INSERT INTO veterinarians(clinic_name,phone,address,city,active,created_at,updated_at)
                                      VALUES(?,?,?,?,1,?,?)""", ("Clinica Test", "0501234567", "Via Test 5", "Pisa", stamp, stamp)).lastrowid
        redirects = []
        self.handler.redirect = lambda path: redirects.append(path)
        self.handler.form = lambda: {
            "operator_name": "SERENA", "service_type": "Cremazione singola", "request_origin": "Veterinario",
            "owner_veterinarian_id": str(vet_id),
        }
        self.handler.create_practice(admin)
        self.assertTrue(redirects, "la pratica con veterinario come speditore doveva essere creata senza errori di validazione")
        pid = int(redirects[0].rsplit("/", 1)[-1])
        with app.db() as conn:
            created = conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
        self.assertEqual(created["owner_first_name"], "Clinica Test")
        self.assertEqual(created["owner_last_name"], "")

    def test_owner_veterinarian_as_sender_still_requires_operator_and_request_origin(self):
        with app.db() as conn:
            stamp = app.now()
            vet_id = conn.execute("""INSERT INTO veterinarians(clinic_name,phone,address,city,active,created_at,updated_at)
                                      VALUES(?,?,?,?,1,?,?)""", ("Clinica Test", "0501234567", "Via Test 5", "Pisa", stamp, stamp)).lastrowid
        d = self.handler.normalized_fields({
            "service_type": "Cremazione singola", "request_origin": "Veterinario",
            "owner_veterinarian_id": str(vet_id),
        })
        error = self.handler.validation_error(d)
        self.assertIn("Campi obbligatori mancanti", error)
        self.assertIn("Operatore", error)
        self.assertNotIn("Cognome", error)
        self.assertNotIn("Codice fiscale", error)
        self.assertNotIn("Telefono", error)
        self.assertNotIn("Indirizzo", error)

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
            "dashboard_sections_json": json.dumps(["payments", "recent_practices"]),
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

    def test_profile_page_renders_drag_and_drop_lists_instead_of_numeric_fields(self):
        with app.db() as conn:
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.profile_page(serena)
        page = rendered[-1]
        # the old numeric-input reordering must be gone entirely
        self.assertNotIn('name="sidebar_pos__', page)
        self.assertNotIn('name="dash_pos__', page)
        self.assertNotIn('name="dash_show__', page)
        self.assertIn('data-drag-group', page)
        self.assertIn('data-drag-root', page)
        self.assertIn('class="drag-handle"', page)
        self.assertIn('name="sidebar_order_json"', page)
        self.assertIn('name="dashboard_sections_json"', page)
        self.assertIn('class="drag-item-visible"', page)
        # the sidebar list (21 items) no longer sits inline on the page - it
        # was unusable on a real phone (couldn't scroll past ~6 rows) because
        # touch-action:none on the whole row blocked normal touch scrolling;
        # it now lives behind a dedicated popup with a larger scroll area
        self.assertIn('id="ppmOpenSidebarOrder"', page)
        self.assertIn('id="ppmSidebarOrderOverlay"', page)
        # every dashboard section id from DASHBOARD_SECTION_LABELS is a draggable row
        for sid, label in app.DASHBOARD_SECTION_LABELS:
            self.assertIn(f'data-drag-key="{sid}"', page)
            self.assertIn(app.esc(label), page)

    def test_drag_item_rows_allow_normal_touch_scrolling_outside_the_handle(self):
        # regression: touch-action:none on the whole .drag-item (not just the
        # handle) silently blocked normal swipe-to-scroll on touch devices,
        # so a long list (e.g. 21 sidebar entries) couldn't be scrolled past
        # the first few rows. Only the handle may claim the gesture.
        self.assertIn(
            '.drag-item{display:flex;align-items:center;gap:12px;padding:11px 14px;border:1px solid #334155;border-radius:12px;background:#1f2937;-webkit-touch-callout:none',
            app.CSS,
        )
        self.assertIn("touch-action:none", app.CSS)  # still present, but only for .drag-handle
        handle_rule = app.CSS.split(".drag-handle{", 1)[1].split("}", 1)[0]
        self.assertIn("touch-action:none", handle_rule)

    def test_profile_page_renders_daily_summary_controls_and_priority_labelled_notif_types(self):
        with app.db() as conn:
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()
            conn.execute("INSERT INTO user_preferences(user_id,key,value) VALUES(?,?,?)", (serena["id"], "daily_summary_enabled", "1"))
            conn.execute("INSERT INTO user_preferences(user_id,key,value) VALUES(?,?,?)", (serena["id"], "daily_summary_time", "09:30"))
        rendered = []
        self.handler.send_html = lambda content, *a: rendered.append(content)
        self.handler.profile_page(serena)
        page = rendered[-1]
        self.assertIn("Riepilogo del giorno", page)
        self.assertIn('name="daily_summary_section" value="1"', page)
        self.assertIn('name="daily_summary_enabled" value="1" checked', page)
        self.assertIn('name="daily_summary_time" value="09:30"', page)
        self.assertIn("Alta priorità", page)
        self.assertIn("Priorità normale", page)
        self.assertIn('class="notif-type-icon"', page)

    def test_save_preferences_parses_drag_order_json_and_gates_daily_summary_by_marker(self):
        with app.db() as conn:
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()
        redirects = []
        self.handler.redirect = lambda url: redirects.append(url)
        self.handler.form = lambda: {
            "return_to": "/il-mio-profilo",
            "sidebar_order_json": json.dumps(["Calendario", "Dashboard", "Bilanci"]),
        }
        self.handler.save_preferences(serena)
        with app.db() as conn:
            saved = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM user_preferences WHERE user_id=?", (serena["id"],))}
        self.assertEqual(json.loads(saved["sidebar_order"]), ["Calendario", "Dashboard", "Bilanci"])
        self.assertNotIn("daily_summary_enabled", saved)
        # a later, unrelated form submit (no daily_summary_section marker) must not silently reset it
        self.handler.form = lambda: {
            "return_to": "/il-mio-profilo",
            "daily_summary_section": "1",
            "daily_summary_enabled": "1",
            "daily_summary_time": "07:45",
        }
        self.handler.save_preferences(serena)
        self.handler.form = lambda: {"return_to": "/il-mio-profilo", "theme": "light"}
        self.handler.save_preferences(serena)
        with app.db() as conn:
            saved = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM user_preferences WHERE user_id=?", (serena["id"],))}
        self.assertEqual(saved["daily_summary_enabled"], "1")
        self.assertEqual(saved["daily_summary_time"], "07:45")

    def test_drag_reorder_js_uses_pointer_events_for_touch_and_mouse(self):
        self.assertIn("function setupDragReorder(root)", app.APP_JS)
        self.assertIn("addEventListener('pointerdown'", app.APP_JS)
        self.assertIn("function syncDragOrder(root)", app.APP_JS)
        self.assertIn("root.scrollTop", app.APP_JS)

    def test_drag_reorder_suppresses_text_selection_during_the_gesture(self):
        # regression: dragging a row by its handle was instead selecting the
        # text of whichever rows the pointer travelled over mid-drag.
        self.assertIn("ppm-dragging-no-select", app.APP_JS)
        self.assertIn("document.body.classList.add('ppm-dragging-no-select')", app.APP_JS)
        self.assertIn("document.body.classList.remove('ppm-dragging-no-select')", app.APP_JS)
        self.assertIn("body.ppm-dragging-no-select,body.ppm-dragging-no-select *", app.CSS)

    def test_sidebar_order_popup_open_close_js_is_wired(self):
        self.assertIn("function setupSidebarOrderPopup()", app.APP_JS)
        self.assertIn("ppmOpenSidebarOrder", app.APP_JS)
        self.assertIn("ppmSidebarOrderOverlay", app.APP_JS)
        self.assertIn("ppmCloseSidebarOrder", app.APP_JS)

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

    def test_daily_summary_notification_type_is_registered(self):
        self.assertIn("daily_summary", app.NOTIFICATION_TYPES)
        self.assertEqual(app.NOTIFICATION_TYPES["daily_summary"][0], "Riepilogo del giorno")

    def test_notification_priority_classifies_high_and_normal_types(self):
        self.assertEqual(notification_priority("payment_due"), "alta")
        self.assertEqual(notification_priority("system_error"), "alta")
        self.assertEqual(notification_priority("whatsapp_error"), "alta")
        self.assertEqual(notification_priority("practice_created"), "normale")
        self.assertEqual(notification_priority("payment_received"), "normale")

    def test_emit_notification_groups_bursts_within_five_minutes_into_one_row(self):
        # Simulates "più ritiri creati in pochi minuti": instead of 3 separate
        # push notifications, the same row is updated in place with a summary,
        # and each individual occurrence stays inspectable via group_items.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()["id"]
            emit_notification(conn, "practice_created", "🐾 Nuova pratica", "Fido", target_user_ids=[admin])
            emit_notification(conn, "practice_created", "🐾 Nuova pratica", "Luna", target_user_ids=[admin])
            emit_notification(conn, "practice_created", "🐾 Nuova pratica", "Rex", target_user_ids=[admin])
            rows = conn.execute("SELECT * FROM notifications WHERE user_id=? AND type='practice_created'", (admin,)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["group_count"], 3)
            self.assertEqual(rows[0]["text"], "Oggi: 3 nuovi ritiri")
            items = conn.execute("SELECT * FROM notification_group_items WHERE notification_id=? ORDER BY id", (rows[0]["id"],)).fetchall()
            self.assertEqual([item["text"] for item in items], ["Fido", "Luna", "Rex"])

    def test_emit_notification_does_not_group_across_types_or_once_read(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()["id"]
            emit_notification(conn, "practice_created", "🐾 Nuova pratica", "Fido", target_user_ids=[admin])
            emit_notification(conn, "payment_received", "💰 Pagamento ricevuto", "Fido", target_user_ids=[admin])
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications WHERE user_id=?", (admin,)).fetchone()["n"], 2)
            conn.execute("UPDATE notifications SET is_read=1,read_at=? WHERE type='practice_created' AND user_id=?", (app.now(), admin))
            emit_notification(conn, "practice_created", "🐾 Nuova pratica", "Bella", target_user_ids=[admin])
            practice_created_rows = conn.execute("SELECT * FROM notifications WHERE user_id=? AND type='practice_created'", (admin,)).fetchall()
            # already-read notification stays closed as its own entry; a new
            # event after it opens a fresh one instead of reopening the old
            self.assertEqual(len(practice_created_rows), 2)
            self.assertEqual(sum(row["group_count"] for row in practice_created_rows), 2)

    def test_emit_notification_only_attaches_action_url_to_single_occurrences(self):
        class ImmediateThread:
            def __init__(self, target=None, args=(), daemon=None):
                self._target, self._args = target, args
            def start(self):
                self._target(*self._args)
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()["id"]
            stamp = app.now()
            conn.execute("""INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,created_at,updated_at)
                            VALUES(?,?,?,?,?,?)""", (admin, "https://push.example/ep1", "p256dh", "auth", stamp, stamp))
        captured = []
        with patch("notification_service.threading.Thread", ImmediateThread), \
             patch("notification_service._deliver_batch", side_effect=lambda db_path, queued: captured.append(queued)):
            with app.db() as conn:
                emit_notification(conn, "whatsapp_error", "❌ Errore invio WhatsApp", "Prima occorrenza",
                                   target_user_ids=[admin], payload={"action_url": "/whatsapp-messaggi/1/riprova", "action_label": "Riprova invio"},
                                   db_path=str(app.DB_PATH))
            self.assertEqual(captured[-1][0]["data"]["action_url"], "/whatsapp-messaggi/1/riprova")
            self.assertEqual(captured[-1][0]["data"]["action_label"], "Riprova invio")
            self.assertEqual(captured[-1][0]["data"]["priority"], "alta")
            with app.db() as conn:
                emit_notification(conn, "whatsapp_error", "❌ Errore invio WhatsApp", "Seconda occorrenza",
                                   target_user_ids=[admin], payload={"action_url": "/whatsapp-messaggi/2/riprova", "action_label": "Riprova invio"},
                                   db_path=str(app.DB_PATH))
            # grouped (2nd occurrence within the window): no longer a single
            # unambiguous target, so the quick action must not be offered
            self.assertNotIn("action_url", captured[-1][0]["data"])

    def test_whatsapp_error_notifications_carry_a_retry_quick_action(self):
        import inspect
        source = inspect.getsource(app.App.send_whatsapp_message)
        self.assertEqual(source.count('"action_url":f"/whatsapp-messaggi/{msg_id}/riprova"'), 2)
        self.assertEqual(source.count('"action_label":"Riprova invio"'), 2)

    def test_article_ordered_notification_links_to_the_reminder_completion_action(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            article = conn.execute("SELECT id,name FROM articles WHERE active=1 LIMIT 1").fetchone()
        captured = []
        with patch("app.emit_notification", side_effect=lambda *a, **k: captured.append((a, k))):
            self.handler.redirect = lambda url: None
            self.handler.order_article(admin, article["id"])
        with app.db() as conn:
            reminder = conn.execute("SELECT id FROM reminders WHERE entity_key=?", (f"article:{article['id']}",)).fetchone()
        payload = captured[-1][1]["payload"]
        self.assertEqual(payload["action_url"], f"/promemoria/{reminder['id']}/completa")
        self.assertEqual(payload["action_label"], "Segna come ordinato")

    def test_process_daily_summaries_respects_opt_in_and_time_window_once_per_day(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()["id"]
            today = "2026-07-20"
            outside_window = datetime.fromisoformat(f"{today}T07:00:00")
            in_window = datetime.fromisoformat(f"{today}T08:03:00")
            # not opted in: nothing is sent even if the clock matches a plausible time
            created = process_daily_summaries(conn, str(app.DB_PATH), current=in_window)
            self.assertEqual(created, 0)
            conn.execute("INSERT INTO user_preferences(user_id,key,value) VALUES(?,?,?)", (admin, "daily_summary_enabled", "1"))
            conn.execute("INSERT INTO user_preferences(user_id,key,value) VALUES(?,?,?)", (admin, "daily_summary_time", "08:00"))
            # opted in but outside the configured window: still nothing
            created = process_daily_summaries(conn, str(app.DB_PATH), current=outside_window)
            self.assertEqual(created, 0)
            # inside the window: sent once
            created = process_daily_summaries(conn, str(app.DB_PATH), current=in_window)
            self.assertEqual(created, 1)
            row = conn.execute("SELECT * FROM notifications WHERE user_id=? AND type='daily_summary'", (admin,)).fetchone()
            self.assertTrue(row["text"].startswith("Oggi: "))
            # a second check a few minutes later the same day must not duplicate it
            created_again = process_daily_summaries(conn, str(app.DB_PATH), current=in_window + timedelta(minutes=4))
            self.assertEqual(created_again, 0)
            self.assertEqual(conn.execute("SELECT count(*) n FROM notifications WHERE user_id=? AND type='daily_summary'", (admin,)).fetchone()["n"], 1)

    def test_archive_old_notifications_moves_only_notifications_read_over_30_days_ago(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()["id"]
            now_dt = datetime(2026, 7, 26, 12, 0, 0)
            old_read = (now_dt - timedelta(days=45)).isoformat(timespec="seconds")
            recent_read = (now_dt - timedelta(days=2)).isoformat(timespec="seconds")
            old_id = conn.execute("""INSERT INTO notifications(user_id,title,text,type,created_at,read_at,is_read)
                                     VALUES(?,?,?,?,?,?,1)""", (admin, "Vecchia", "Vecchia", "system_error", old_read, old_read)).lastrowid
            recent_id = conn.execute("""INSERT INTO notifications(user_id,title,text,type,created_at,read_at,is_read)
                                        VALUES(?,?,?,?,?,?,1)""", (admin, "Recente", "Recente", "system_error", recent_read, recent_read)).lastrowid
            unread_id = conn.execute("""INSERT INTO notifications(user_id,title,text,type,created_at,is_read)
                                        VALUES(?,?,?,?,?,0)""", (admin, "Non letta", "Non letta", "system_error", old_read)).lastrowid
            changed = archive_old_notifications(conn, current=now_dt)
            self.assertEqual(changed, 1)
            self.assertIsNotNone(conn.execute("SELECT archived_at FROM notifications WHERE id=?", (old_id,)).fetchone()["archived_at"])
            self.assertIsNone(conn.execute("SELECT archived_at FROM notifications WHERE id=?", (recent_id,)).fetchone()["archived_at"])
            self.assertIsNone(conn.execute("SELECT archived_at FROM notifications WHERE id=?", (unread_id,)).fetchone()["archived_at"])

    def test_notifications_page_filters_by_date_range_and_searches_animal_and_owner(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            pid = conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                                animal_name,owner_first_name,owner_last_name) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                ("CR-NOTIFSEARCH", "Privato", "Livorno", "Ritirato", stamp, stamp, admin["id"], "Nuvola", "Franco", "Rossi")).lastrowid
            emit_notification(conn, "practice_created", "🐾 Nuova pratica", "Nuvola", practice_id=pid, target_user_ids=[admin["id"]])
            emit_notification(conn, "system_error", "🚨 Errori di sistema", "Non collegata a nessuna pratica", target_user_ids=[admin["id"]])
        rendered = []
        self.handler.send_html = lambda content: rendered.append(content)
        self.handler.path = "/notifiche?q=Nuvola"
        self.handler.notifications(admin)
        page = rendered[-1]
        self.assertIn("CR-NOTIFSEARCH", page)
        self.assertNotIn("Non collegata a nessuna pratica", page)
        rendered.clear()
        self.handler.path = f"/notifiche?dal={date.today().isoformat()}&al={date.today().isoformat()}"
        self.handler.notifications(admin)
        self.assertIn("CR-NOTIFSEARCH", rendered[-1])
        rendered.clear()
        self.handler.path = f"/notifiche?dal=2099-01-01"
        self.handler.notifications(admin)
        self.assertIn("empty-state", rendered[-1])

    def test_notifications_page_shows_priority_tag_and_group_expand(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            emit_notification(conn, "payment_due", "⚠️ Pratica ancora da saldare", "Fido", target_user_ids=[admin["id"]])
            emit_notification(conn, "practice_created", "🐾 Nuova pratica", "Uno", target_user_ids=[admin["id"]])
            emit_notification(conn, "practice_created", "🐾 Nuova pratica", "Due", target_user_ids=[admin["id"]])
        rendered = []
        self.handler.send_html = lambda content: rendered.append(content)
        self.handler.path = "/notifiche"
        self.handler.notifications(admin)
        page = rendered[-1]
        self.assertIn("Alta priorità", page)
        self.assertIn("Vedi i 2 singoli elementi", page)
        self.assertIn("Uno", page)
        self.assertIn("Due", page)

    def test_notifications_page_archived_toggle_hides_and_shows_archived_notifications(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            emit_notification(conn, "system_error", "🚨 Errori di sistema", "Attiva", target_user_ids=[admin["id"]])
            emit_notification(conn, "backup_completed", "✅ Backup completato", "Archiviata", target_user_ids=[admin["id"]])
            conn.execute("UPDATE notifications SET archived_at=? WHERE text='Archiviata'", (app.now(),))
        rendered = []
        self.handler.send_html = lambda content: rendered.append(content)
        self.handler.path = "/notifiche"
        self.handler.notifications(admin)
        self.assertIn("<p>Attiva</p>", rendered[-1])
        self.assertNotIn("<p>Archiviata</p>", rendered[-1])
        rendered.clear()
        self.handler.path = "/notifiche?mostra_archiviate=1"
        self.handler.notifications(admin)
        self.assertIn("<p>Archiviata</p>", rendered[-1])
        self.assertNotIn("<p>Attiva</p>", rendered[-1])

    def test_service_worker_reads_priority_and_quick_action_from_push_payload(self):
        source = (app.ASSETS / "sw.js").read_text(encoding="utf-8")
        self.assertIn("data.priority === 'alta'", source)
        self.assertIn("silent: !isHighPriority", source)
        self.assertIn("event.action === 'quick'", source)
        self.assertIn("fetch(data.actionUrl", source)

    def test_save_notification_preferences_redirects_back_to_profile(self):
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        redirects = []
        self.handler.redirect = lambda url: redirects.append(url)
        self.handler.form = lambda: {"return_to": "/il-mio-profilo"}
        self.handler.save_notification_preferences(admin)
        self.assertEqual(redirects, ["/il-mio-profilo"])


if __name__ == "__main__":
    unittest.main()
