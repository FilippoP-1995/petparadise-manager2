import inspect
import json
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app
import notification_service as ns
import partner_service as ps


def tomorrow():
    return (ps._rome_today() + timedelta(days=1)).isoformat()


class NotificationBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        with app.db() as c:
            c.execute("UPDATE users SET must_change_password=0")
            self.admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            self.serena = c.execute("SELECT * FROM users WHERE username='serena'").fetchone()
            stamp = "2026-01-01T00:00:00"
            vet = c.execute(
                """INSERT INTO veterinarians(clinic_name,short_name,doctor_name,phone,address,city,active,created_at,updated_at)
                   VALUES('CLINICA ALFA','ALFA','','0586123','Via Alfa 1','Livorno',1,?,?)""", (stamp, stamp)).lastrowid
            self.clinic = ps.create_clinic(c, veterinarian_id=vet, has_freezer=True)
            self.vet_user = ps.add_partner_user(c, clinic_id=self.clinic, email="v@alfa.it", role="titolare")
        self.counter = 0

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def make(self, **overrides):
        self.counter += 1
        params = dict(
            clinic_id=self.clinic, user_id=self.vet_user, client_request_id=f"tok-{self.counter}", mode="ritiro_clinica",
            service_type="Cremazione singola", owner_first_name="Mario", owner_last_name="Rossi", owner_phone="333 1234567",
            species="Cane", weight="12 kg", animal_name="Fido", proposed_date=tomorrow(), proposed_from="09:00", proposed_to="12:00",
        )
        params.update(overrides)
        with app.db() as c:
            return ps.create_request(c, db_path=app.DB_PATH, **params)[0]

    def notes(self, kind=None, user=None):
        with app.db() as c:
            sql = "SELECT * FROM notifications WHERE user_id=?"
            args = [(user or self.admin)["id"]]
            if kind:
                sql += " AND type=?"
                args.append(kind)
            return c.execute(sql + " ORDER BY id", args).fetchall()

    def layout_html(self, user=None):
        return app.layout("Prova", '<main class="wrap">XYZCORPO</main>', user or self.admin)


class IdentityAndPriorityTests(NotificationBase):
    def test_portal_types_have_their_own_brand_and_icons(self):
        for kind in ("partner_request_created", "partner_request_urgent", "partner_request_reminder",
                     "partner_request_cancelled", "partner_freezer_update"):
            label, icon = ns.NOTIFICATION_TYPES[kind]
            self.assertTrue(label.startswith("PORTALE VETERINARI"), kind)
            self.assertTrue(icon)
        self.assertEqual(ns.NOTIFICATION_TYPES["partner_request_created"][1], "🩺")

    def test_new_requests_and_reminders_are_critical_the_rest_are_high(self):
        for kind in ("partner_request_created", "partner_request_urgent", "partner_request_reminder"):
            self.assertEqual(ns.notification_priority(kind), "critica")
        for kind in ("partner_request_cancelled",):
            self.assertEqual(ns.notification_priority(kind), "normale")
        self.assertEqual(ns.notification_priority("partner_freezer_update"), "alta")
        self.assertEqual(ns.notification_priority("payment_due"), "alta")  # il resto invariato
        self.assertEqual(ns.notification_priority("practice_created"), "normale")

    def test_all_portal_types_are_mandatory_and_never_grouped(self):
        for kind in [k for k in ns.NOTIFICATION_TYPES if k.startswith("partner_")]:
            self.assertIn(kind, ns.MANDATORY_NOTIFICATION_TYPES, kind)
            self.assertIn(kind, ns.NON_GROUPABLE_NOTIFICATION_TYPES, kind)

    def test_push_title_is_unmistakable(self):
        self.assertEqual(ns.notification_push_title("partner_request_created", "PORTALE VETERINARI · Nuova richiesta"),
                         "🩺 PORTALE VETERINARI · Nuova richiesta")
        self.assertTrue(ns.notification_push_title("partner_request_urgent", "x").startswith("🚨🩺"))
        self.assertTrue(ns.notification_push_title("partner_request_reminder", "x").startswith("⏰🩺"))

    def test_stored_titles_all_carry_the_brand(self):
        self.make()
        self.make(urgent=True)
        self.make(service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="")
        titles = [n["title"] for n in self.notes()]
        self.assertEqual(len(titles), 3)
        for title in titles:
            self.assertTrue(title.startswith("PORTALE VETERINARI · "), title)
        self.assertIn("RICHIESTA URGENTE", titles[1])
        kinds = [n["type"] for n in self.notes()]
        self.assertEqual(kinds, ["partner_request_created", "partner_request_urgent", "partner_freezer_update"])


class MandatoryTests(NotificationBase):
    def test_portal_notifications_ignore_personal_switches_but_others_do_not(self):
        with app.db() as c:
            for kind in ("partner_request_created", "partner_request_urgent", "partner_request_reminder",
                         "partner_request_cancelled", "partner_freezer_update", "practice_created"):
                c.execute("INSERT INTO notification_preferences(user_id,type,enabled) VALUES(?,?,0)", (self.serena["id"], kind))
            for kind in ns.MANDATORY_NOTIFICATION_TYPES:
                self.assertTrue(ns.preference_enabled(c, self.serena["id"], kind), kind)
            self.assertFalse(ns.preference_enabled(c, self.serena["id"], "practice_created"))
        self.make()
        self.make(urgent=True)
        self.assertEqual(len(self.notes(user=self.serena)), 2)  # arrivano anche a chi aveva "spento" tutto
        with app.db() as c:
            ns.emit_notification(c, "practice_created", "x", "y")
        self.assertEqual(len([n for n in self.notes(user=self.serena) if n["type"] == "practice_created"]), 0)

    def test_saving_profile_preferences_cannot_switch_them_off(self):
        self.handler.form = lambda: {}  # nessuna casella spuntata
        self.handler.redirect = lambda url: None
        self.handler.save_notification_preferences(self.serena)
        with app.db() as c:
            saved = {r["type"]: r["enabled"] for r in c.execute(
                "SELECT type,enabled FROM notification_preferences WHERE user_id=?", (self.serena["id"],))}
        for kind in ns.MANDATORY_NOTIFICATION_TYPES:
            self.assertEqual(saved[kind], 1, kind)
        self.assertEqual(saved["practice_created"], 0)

    def test_profile_page_shows_them_locked(self):
        pages = []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.path = "/il-mio-profilo"
        self.handler.profile_page(self.serena)
        html = pages[-1]
        for kind in ns.MANDATORY_NOTIFICATION_TYPES:
            index = html.index(f'name="{kind}"')
            self.assertIn('onclick="return false"', html[index:index + 220], kind)
            self.assertIn("checked", html[index:index + 120], kind)
        self.assertIn("Obbligatoria · da gestire subito", html)
        index = html.index('name="practice_created"')
        self.assertNotIn("return false", html[index:index + 220])

    def test_every_active_user_receives_them(self):
        self.make()
        with app.db() as c:
            active = [r["id"] for r in c.execute("SELECT id FROM users WHERE active=1")]
            received = {r["user_id"] for r in c.execute("SELECT user_id FROM notifications WHERE type='partner_request_created'")}
        self.assertEqual(received, set(active))
        self.assertGreaterEqual(len(active), 4)


class PushPayloadTests(NotificationBase):
    def run_with_capture(self, fn):
        sent = []
        with app.db() as c:
            for uid in (self.admin["id"],):
                c.execute("INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                          (uid, f"https://push.example/{uid}", "k", "a", "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
        sync = lambda target, args=(), daemon=None, **kw: types.SimpleNamespace(start=lambda: target(*args))
        with patch.object(ns, "_deliver_batch", side_effect=lambda db_path, queued: sent.extend(queued)), \
                patch.object(ns.threading, "Thread", side_effect=sync):
            fn()
        return [item["data"] for item in sent]

    def test_push_is_sticky_critical_and_uniquely_tagged(self):
        data = self.run_with_capture(lambda: (self.make(), self.make(urgent=True)))
        self.assertEqual(len(data), 2)
        for item in data:
            self.assertEqual((item["priority"], item["sticky"]), ("critica", True))
            self.assertTrue(item["title"].startswith(("🩺 PORTALE VETERINARI", "🚨🩺 PORTALE VETERINARI")), item["title"])
            self.assertTrue(item["url"].startswith("/notifiche/"))
        self.assertNotEqual(data[0]["tag"], data[1]["tag"])  # una non sostituisce l'altra

    def test_other_notifications_stay_as_before(self):
        def emit():
            with app.db() as c:
                ns.emit_notification(c, "practice_created", "Nuova pratica", "x", db_path=app.DB_PATH)
        data = self.run_with_capture(emit)
        self.assertEqual(len(data), 1)
        self.assertFalse(data[0]["sticky"])
        self.assertEqual(data[0]["priority"], "normale")
        self.assertEqual(data[0]["title"], "Nuova pratica")

    def test_service_worker_keeps_critical_notifications_on_screen(self):
        source = (app.ASSETS / "sw.js").read_text(encoding="utf-8")
        self.assertIn("data.priority === 'critica'", source)
        self.assertIn("requireInteraction: isCritical", source)
        self.assertIn("[500, 200, 500, 200, 500, 200, 500]", source)
        self.assertIn("silent: !isHighPriority", source)  # critica e' anche "alta": mai silenziosa


class ReminderTests(NotificationBase):
    ROME = ZoneInfo("Europe/Rome")

    def aged(self, request, minutes, now):
        born = (now.replace(tzinfo=self.ROME).astimezone(ZoneInfo("UTC")) - timedelta(minutes=minutes))
        with app.db() as c:
            c.execute("UPDATE partner_requests SET created_at=? WHERE id=?", (born.strftime("%Y-%m-%dT%H:%M:%SZ"), request["id"]))

    def run_pending(self, now):
        with app.db() as c:
            return ns.process_partner_pending(c, app.DB_PATH, current=now)

    def reminders(self):
        return self.notes("partner_request_reminder")

    def test_normal_request_is_reminded_after_30_minutes_then_every_hour(self):
        now = datetime(2026, 10, 5, 10, 0)
        req = self.make()
        self.aged(req, 29, now)
        self.assertEqual(self.run_pending(now), 0)
        self.aged(req, 30, now)
        self.assertEqual(self.run_pending(now), 1)
        self.assertEqual(self.run_pending(now), 0)  # stessa scadenza: una sola volta
        self.aged(req, 89, now)
        self.assertEqual(self.run_pending(now), 0)
        self.aged(req, 90, now)
        self.assertEqual(self.run_pending(now), 1)
        self.aged(req, 150, now)
        self.assertEqual(self.run_pending(now), 1)
        self.assertEqual(len(self.reminders()), 3)

    def test_reminder_content_and_recipients(self):
        now = datetime(2026, 10, 5, 10, 0)
        req = self.make(animal_name="Birba")
        self.aged(req, 45, now)
        self.run_pending(now)
        note = self.reminders()[0]
        self.assertEqual(note["title"], "PORTALE VETERINARI · ANCORA DA CONFERMARE")
        for part in ("ALFA", "Birba", "in attesa da 45 min"):
            self.assertIn(part, note["text"])
        self.assertEqual(json.loads(note["payload"])["url"], "/richieste-portale")
        self.assertEqual(len(self.reminders()), 1)
        self.assertEqual(len(self.notes("partner_request_reminder", self.serena)), 1)

    def test_urgent_request_is_reminded_every_15_minutes_at_any_hour(self):
        now = datetime(2026, 10, 5, 23, 30)  # notte
        req = self.make(urgent=True)
        self.aged(req, 14, now)
        self.assertEqual(self.run_pending(now), 0)
        counts = []
        for minutes in (15, 30, 45, 60, 119):
            self.aged(req, minutes, now)
            counts.append(self.run_pending(now))
        self.assertEqual(counts, [1, 1, 1, 1, 1])
        self.aged(req, 120, now)
        self.assertEqual(self.run_pending(now), 1)
        self.aged(req, 140, now)
        self.assertEqual(self.run_pending(now), 0)  # dopo 2 ore: ogni 30 minuti
        self.aged(req, 150, now)
        self.assertEqual(self.run_pending(now), 1)
        self.assertIn("URGENTE", self.reminders()[-1]["text"])

    def test_normal_reminders_stay_silent_at_night_but_catch_up_in_the_morning(self):
        night = datetime(2026, 10, 5, 22, 0)
        req = self.make()
        self.aged(req, 120, night)
        self.assertEqual(self.run_pending(night), 0)
        morning = datetime(2026, 10, 6, 7, 0)
        self.aged(req, 600, morning)
        self.assertEqual(self.run_pending(morning), 1)  # una sola, non una per ogni ora persa
        self.assertEqual(self.run_pending(morning), 0)

    def test_reminders_stop_when_the_request_is_handled(self):
        now = datetime(2026, 10, 5, 10, 0)
        confirmed = self.make()
        cancelled = self.make()
        freezer = self.make(service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="")
        for req in (confirmed, cancelled, freezer):
            self.aged(req, 200, now)
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (confirmed["calendar_event_id"],))
            ps.cancel_request(c, clinic_id=self.clinic, request_id=cancelled["id"], user_id=self.vet_user)
        self.aged(confirmed, 200, now)
        self.aged(cancelled, 200, now)
        self.assertEqual(self.run_pending(now), 0)
        self.assertEqual(self.reminders(), [])  # congelatore: nessuna fretta, nessun sollecito

    def test_cron_calls_the_reminder_job(self):
        self.assertIn("process_partner_pending(c,DB_PATH)", inspect.getsource(app.App.whatsapp_cron))


class BannerAndBadgeTests(NotificationBase):
    def test_no_alert_when_nothing_is_pending(self):
        for user in (self.admin, self.serena):
            html = self.layout_html(user)
            self.assertNotIn('id="partnerAlert"', html)
            self.assertNotIn('id="partnerPill"', html)
            self.assertNotIn("partner-badge", html.split("<style>")[0] + html.split("</style>")[1])
            self.assertIn('data-portal-newest="0"', html)

    def test_banner_pill_and_menu_badge_appear_on_every_page_for_every_user(self):
        self.make()
        self.make(urgent=True, animal_name="Rex")
        for user in (self.admin, self.serena):
            html = self.layout_html(user)
            self.assertIn('id="partnerAlert"', html)
            self.assertIn("PORTALE VETERINARI · 2 richieste da confermare · 1 URGENTE", html)
            self.assertIn('class="partner-alert urgent"', html)
            self.assertIn("ALFA", html)
            self.assertIn('id="partnerPill"', html)
            self.assertIn("🩺 2 da confermare · 1 URGENTE", html)
            self.assertIn('class="notification-badge partner-badge">2<', html)
            self.assertIn('href="/richieste-portale"', html)
            self.assertLess(html.index('id="partnerAlert"'), html.index('XYZCORPO'))  # in cima alla pagina
        self.assertIn('data-portal-newest="2"', self.layout_html())

    def test_alert_is_teal_when_nothing_is_urgent(self):
        self.make()
        html = self.layout_html()
        self.assertIn('class="partner-alert"', html)
        self.assertNotIn('class="partner-alert urgent"', html)
        self.assertIn("1 richiesta da confermare", html)

    def test_alert_disappears_when_staff_confirms_or_request_is_cancelled(self):
        a, b = self.make(), self.make()
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (a["calendar_event_id"],))
        self.assertIn("1 richiesta da confermare", self.layout_html())
        with app.db() as c:
            ps.cancel_request(c, clinic_id=self.clinic, request_id=b["id"], user_id=self.vet_user)
        self.assertNotIn('id="partnerAlert"', self.layout_html())

    def test_freezer_requests_do_not_raise_the_alert(self):
        self.make(service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="")
        self.assertNotIn('id="partnerAlert"', self.layout_html())

    def test_old_unhandled_requests_leave_the_banner_but_not_the_inbox(self):
        req = self.make()
        with app.db() as c:
            c.execute("UPDATE partner_requests SET created_at='2020-01-01T00:00:00Z' WHERE id=?", (req["id"],))
            self.assertEqual(ps.pending_summary(c)["pending"], 0)
            self.assertEqual(len(ps.pending_requests(c)), 1)

    def test_clinic_names_are_escaped_in_the_banner(self):
        with app.db() as c:
            c.execute("UPDATE veterinarians SET short_name='<b>X</b>' WHERE short_name='ALFA'")
        self.make()
        html = self.layout_html()
        self.assertNotIn("<b>X</b>", html.split('id="partnerAlert"')[1].split("</div>")[2])
        self.assertIn("&lt;b&gt;X&lt;/b&gt;", html)

    def test_login_page_has_no_alert(self):
        self.make()
        self.assertNotIn('id="partnerAlert"', app.layout("Accesso", "<main>login</main>"))

    def test_client_script_polls_and_alerts(self):
        self.assertIn("/api/richieste-portale/stato", app.APP_JS)
        self.assertIn("setInterval(poll,20000)", app.APP_JS)
        self.assertIn("navigator.vibrate([300,120,300,120,300])", app.APP_JS)
        self.assertIn("createOscillator", app.APP_JS)
        self.assertIn("partner-toast", app.APP_JS)
        self.assertIn("data-portal-newest", app.APP_JS)


class InboxTests(NotificationBase):
    def get(self, path, user=None, anonymous=False):
        handler = object.__new__(app.App)
        handler.headers = {}
        handler.path = path
        handler.user = lambda: None if anonymous else (user or self.admin)
        out = {"html": None, "json": None, "redirect": None, "status": None}
        handler.send_html = lambda content, status=200: out.update(html=content, status=status)
        handler.send_json = lambda obj, status=200: out.update(json=obj, status=status)
        handler.redirect = lambda url: out.update(redirect=url)
        handler._route_get()
        return out

    def test_inbox_is_open_to_every_staff_member_and_requires_login(self):
        self.make()
        for user in (self.admin, self.serena):
            out = self.get("/richieste-portale", user)
            self.assertIn("Richieste dal portale", out["html"])
        self.assertEqual(self.get("/richieste-portale", anonymous=True)["redirect"], "/login")
        self.assertEqual(self.get("/api/richieste-portale/stato", anonymous=True)["redirect"], "/login")

    def test_inbox_lists_pending_requests_urgent_first_with_actions(self):
        first = self.make(animal_name="Primo", owner_phone="333 111 2222")
        urgent = self.make(urgent=True, animal_name="Secondo", notes="Citofono Verdi")
        html = self.get("/richieste-portale", self.serena)["html"]
        self.assertLess(html.index("Secondo"), html.index("Primo"))  # urgente prima
        for text in ("PORTALE VETERINARI", "URGENTE", "ALFA", "Ritiro presso la clinica", "Fascia proposta", "09:00-12:00",
                     "Citofono Verdi", 'href="tel:3331112222"', first["request_code"], urgent["request_code"],
                     f'href="/calendario/{first["calendar_event_id"]}/modifica">Conferma ritiro', "Apri evento",
                     'Da confermare <span class="portal-count">2</span>', "adesso"):
            self.assertIn(text, html)

    def test_inbox_empty_state_and_freezer_section(self):
        html = self.get("/richieste-portale")["html"]
        self.assertIn("Nessuna richiesta da confermare", html)
        self.make(service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="")
        admin_html = self.get("/richieste-portale", self.admin)["html"]
        self.assertIn("1 animale in congelatore (nessuna fretta)", admin_html)
        self.assertIn("pianifica lo svuotamento", admin_html)
        self.assertIn('href="/portale-partner">Cliniche e listino', admin_html)  # scheda di gestione, solo admin
        operator_html = self.get("/richieste-portale", self.serena)["html"]
        self.assertIn("1 animale in congelatore", operator_html)
        self.assertNotIn("pianifica lo svuotamento", operator_html)
        self.assertNotIn("Cliniche e listino", operator_html)

    def test_handled_requests_move_to_the_history_table(self):
        req = self.make(animal_name="Gestito")
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (req["calendar_event_id"],))
        html = self.get("/richieste-portale")["html"]
        self.assertIn("Nessuna richiesta da confermare", html)
        self.assertIn("Gestito", html.split("Ultime richieste gestite")[1])
        self.assertIn("Ritiro programmato", html)

    def test_inbox_escapes_user_text(self):
        self.make(animal_name="<script>alert(1)</script>", notes="<img src=x onerror=alert(2)>")
        html = self.get("/richieste-portale")["html"]
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)

    def test_status_api(self):
        self.assertEqual(self.get("/api/richieste-portale/stato")["json"],
                         {"pending": 0, "urgent": 0, "newest_id": 0, "items": []})
        a = self.make(animal_name="Uno")
        b = self.make(urgent=True, animal_name="Due")
        data = self.get("/api/richieste-portale/stato", self.serena)["json"]
        self.assertEqual((data["pending"], data["urgent"], data["newest_id"]), (2, 1, b["id"]))
        self.assertEqual([i["animal"] for i in data["items"]], ["Due", "Uno"])
        self.assertTrue(data["items"][0]["urgent"])
        self.assertEqual(data["items"][0]["url"], f'/calendario/{b["calendar_event_id"]}')
        self.assertEqual(data["items"][0]["clinic"], "ALFA")

    def test_link_for_the_vets_is_on_the_portal_page_for_every_staff_member(self):
        for user in (self.admin, self.serena):
            html = self.get("/portale-veterinari", user)["html"]
            for text in ("Link del portale da dare ai veterinari", 'id="partnerLinkCard"', "Copia link",
                         "Copia messaggio con istruzioni", "location.origin+'/partner'"):
                self.assertIn(text, html, (user["username"], text))
            self.assertNotIn("pini.vet26", html.split("Link del portale")[1].split("</section>")[0])
            self.assertEqual(html.count('id="partnerLinkCard"'), 1)
            for text in ("navigator.clipboard.writeText(text).then(done,legacy)", "document.execCommand('copy')",
                         'id="partnerLinkText"', "premi Ctrl+C"):
                self.assertIn(text, html)  # se il browser nega la copia automatica, il testo viene selezionato
        # la pagina delle richieste non la duplica: ha solo il pulsante che porta li'
        inbox = self.get("/richieste-portale", self.serena)["html"]
        self.assertNotIn('id="partnerLinkCard"', inbox)
        self.assertIn('href="/portale-veterinari#partnerLinkCard">Link per i veterinari', inbox)

    def test_portal_preview_bar_links_to_the_link_card(self):
        import partner_portal as pp
        page = pp.page("Prova", "<p>x</p>", staff=True)
        self.assertIn('href="/portale-veterinari#partnerLinkCard"', page)
        self.assertIn("Link per i veterinari", page)
        self.assertNotIn("Link per i veterinari", pp.page("Prova", "<p>x</p>", staff=False))  # il veterinario non lo vede

    def test_sidebar_entry_exists_for_everyone(self):
        self.assertIn(("/richieste-portale", "message", "Portale veterinari"), app.SIDEBAR_LINKS)
        self.assertIn("Portale veterinari", app.MENU_CARD_META)
        for user in (self.admin, self.serena):
            html = self.layout_html(user)
            self.assertIn('<span>Portale veterinari</span>', html)


class UnifiedMenuTests(NotificationBase):
    def get(self, path, user=None, anonymous=False):
        handler = object.__new__(app.App)
        handler.headers = {}
        handler.path = path
        handler.user = lambda: None if anonymous else (user or self.admin)
        out = {"html": None, "redirect": None, "status": None}
        handler.send_html = lambda content, status=200: out.update(html=content, status=status)
        handler.send_error = lambda code, *a: out.update(status=code)
        handler.redirect = lambda url: out.update(redirect=url)
        handler._route_get()
        return out

    def tabs(self, html):
        import re
        nav = re.search(r'<nav class="portal-tabs"[^>]*>(.*?)</nav>', html, re.S)
        self.assertIsNotNone(nav, "schede mancanti")
        items = re.findall(r'<a class="([^"]*)" href="([^"]+)">([^<]*)', nav.group(1))
        return [(href, label.strip(), cls.strip() == "on") for cls, href, label in items]

    def test_the_menu_has_one_portal_entry_and_the_vets_registry(self):
        labels = [label for _href, _icon, label in app.SIDEBAR_LINKS]
        hrefs = [href for href, _icon, _label in app.SIDEBAR_LINKS]
        self.assertEqual(labels.count("Portale veterinari"), 1)
        self.assertEqual(labels.count("Veterinari"), 1)  # l'anagrafica resta a parte
        for old in ("Portale Veterinari", "Richieste portale"):
            self.assertNotIn(old, labels)
            self.assertNotIn(old, app.MENU_CARD_META)
        self.assertNotIn("/partner", hrefs)
        self.assertEqual(hrefs.count("/richieste-portale"), 1)
        self.assertEqual(sum(1 for label in labels if "ortale" in label), 1)

    def test_sidebar_and_drawer_show_the_single_entry_to_everyone_with_the_pending_badge(self):
        self.make()
        self.make(urgent=True)
        for user in (self.admin, self.serena):
            html = self.layout_html(user)
            self.assertEqual(html.count("<span>Portale veterinari</span>"), 1)
            self.assertNotIn("<span>Richieste portale</span>", html)
            self.assertNotIn("<span>Portale Veterinari</span>", html)
            self.assertIn('href="/richieste-portale" class="nav-notification">', html)
            self.assertIn('class="notification-badge partner-badge">2<', html)
            self.assertIn('<b>Portale veterinari</b>', html)  # anche nel menu "Altro" del telefono

    def test_old_saved_menu_orders_do_not_resurrect_the_old_entries(self):
        with app.db() as c:
            c.execute("INSERT INTO user_preferences(user_id,key,value) VALUES(?,?,?)",
                      (self.serena["id"], "sidebar_order",
                       json.dumps(["Dashboard", "Portale Veterinari", "Veterinari", "Richieste portale", "Calendario"])))
        html = self.layout_html(self.serena)
        self.assertEqual(html.count("<span>Portale veterinari</span>"), 1)
        self.assertNotIn("<span>Richieste portale</span>", html)
        self.assertNotIn("<span>Portale Veterinari</span>", html)
        self.assertEqual(html.count("<span>Veterinari</span>"), 1)

    def test_tabs_connect_requests_portal_and_management(self):
        self.make()
        operator = {
            "/richieste-portale": [("/richieste-portale", "Richieste", True), ("/portale-veterinari", "Il portale", False)],
            "/portale-veterinari": [("/richieste-portale", "Richieste", False), ("/portale-veterinari", "Il portale", True)],
        }
        for path, expected in operator.items():
            tabs = self.tabs(self.get(path, self.serena)["html"])
            self.assertEqual([(h, l.split(" ")[0] if l.startswith("Richieste") else l, on) for h, l, on in tabs],
                             [(h, l, on) for h, l, on in expected], path)
        admin_tabs = {
            "/richieste-portale": 0, "/portale-veterinari": 1, "/portale-partner": 2, "/portale-partner/listino": 2,
        }
        for path, active in admin_tabs.items():
            tabs = self.tabs(self.get(path, self.admin)["html"])
            self.assertEqual([h for h, _l, _o in tabs], ["/richieste-portale", "/portale-veterinari", "/portale-partner"], path)
            self.assertEqual([i for i, (_h, _l, on) in enumerate(tabs) if on], [active], path)
            self.assertEqual(tabs[2][1], "Cliniche e listino")
        # il numero delle richieste da confermare e' sulla scheda "Richieste"
        self.assertIn('Richieste <span class="portal-count">1</span>', self.get("/portale-veterinari", self.serena)["html"])

    def test_management_stays_admin_only(self):
        for path in ("/portale-partner", "/portale-partner/listino"):
            self.assertEqual(self.get(path, self.serena)["status"], 403, path)
        for path in ("/richieste-portale", "/portale-veterinari"):
            self.assertNotIn("Cliniche e listino", self.get(path, self.serena)["html"])

    def test_portal_page_has_preview_credentials_stats_and_admin_shortcuts(self):
        with app.db() as c:
            ps.ensure_demo_clinic(c, app.password_hash)
        self.make()
        self.make(service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="")
        admin_html = self.get("/portale-veterinari", self.admin)["html"]
        for text in ("Il portale dei veterinari", 'href="/partner" target="_blank" rel="noopener">Apri il portale',
                     "villadeipini", "pini.vet26", "Villa dei Pini", "PORTALE (PROVA)",
                     'href="/portale-partner">Cliniche e utenti', 'href="/portale-partner/listino">Listino preventivi',
                     "Link del portale da dare ai veterinari"):
            self.assertIn(text, admin_html)
        stats = dict((label, value) for value, label in __import__("re").findall(
            r'<div class="portal-stat"><b>(\d+)</b><span>([^<]+)</span>', admin_html))
        self.assertEqual(stats, {"Cliniche attive": "1", "Richieste da confermare": "1", "Animali in congelatore": "1"})
        operator_html = self.get("/portale-veterinari", self.serena)["html"]
        self.assertIn('href="/partner" target="_blank"', operator_html)
        self.assertIn("pini.vet26", operator_html)  # anche lo staff non admin puo' provare il portale
        self.assertNotIn("Cliniche e utenti", operator_html)
        self.assertNotIn("Listino preventivi", operator_html)

    def test_portal_page_without_demo_clinic_and_login_required(self):
        out = self.get("/portale-veterinari", self.admin)
        self.assertEqual(out["status"], 200)
        self.assertNotIn("pini.vet26", out["html"].split("Link del portale")[0])
        self.assertEqual(self.get("/portale-veterinari", anonymous=True)["redirect"], "/login")

    def test_admin_pages_no_longer_duplicate_the_link_card(self):
        self.assertNotIn('id="partnerLinkCard"', self.get("/portale-partner", self.admin)["html"])
        self.assertNotIn('id="partnerLinkCard"', self.get("/richieste-portale", self.admin)["html"])
        self.assertEqual(self.get("/portale-veterinari", self.admin)["html"].count('id="partnerLinkCard"'), 1)

    def test_notifications_and_banner_still_open_the_requests_tab(self):
        self.make()
        html = self.layout_html(self.admin)
        self.assertIn('class="pa-btn" href="/richieste-portale"', html)
        self.assertIn('id="partnerPill"', html)
        self.assertIn('payload={"url": "/richieste-portale"}', inspect.getsource(ns.process_partner_pending))  # solleciti -> Richieste


class NotificationCenterTests(NotificationBase):
    def render(self, user):
        pages = []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.path = "/notifiche"
        self.handler.notifications(user)
        return pages[-1]

    def test_portal_notifications_stand_out_in_the_center(self):
        self.make()
        self.make(urgent=True)
        with app.db() as c:
            ns.emit_notification(c, "practice_created", "Nuova pratica", "Fido")
        html = self.render(self.admin)
        cards = html.split('<article class="notification-item')[1:]
        self.assertEqual(len(cards), 3)
        portal = [card for card in cards if "PORTALE VETERINARI" in card]
        other = [card for card in cards if "PORTALE VETERINARI" not in card]
        self.assertEqual((len(portal), len(other)), (2, 1))
        for card in portal:
            self.assertIn("partner critical", card.split(">")[0])
            self.assertIn("DA GESTIRE", card)
            self.assertIn("🩺", card) if "RICHIESTA URGENTE" not in card else self.assertIn("🚨", card)
        self.assertNotIn("partner", other[0].split(">")[0])
        self.assertNotIn("DA GESTIRE", other[0])

    def test_cancelled_and_freezer_updates_have_the_portal_look_without_the_hot_tag(self):
        req = self.make()
        with app.db() as c:
            ps.cancel_request(c, clinic_id=self.clinic, request_id=req["id"], user_id=self.vet_user, db_path=app.DB_PATH)
        html = self.render(self.admin)
        cancelled = [card for card in html.split('<article class="notification-item')[1:] if "annullata" in card][0]
        self.assertIn("partner", cancelled.split(">")[0])
        self.assertNotIn("critical", cancelled.split(">")[0])
        self.assertIn(">PORTALE<", cancelled)
        self.assertNotIn("DA GESTIRE", cancelled)

    def test_styles_exist_for_the_new_look(self):
        for rule in (".partner-alert{", ".partner-alert.urgent{", ".partner-pill{", ".notification-item.partner{",
                     ".notification-brand{", ".portal-card{", "@keyframes pa-pulse", ".notification-badge.partner-badge{"):
            self.assertIn(rule, app.CSS)


if __name__ == "__main__":
    unittest.main()
