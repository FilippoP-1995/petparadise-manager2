import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import app
import partner_service as ps


def tomorrow():
    return (ps._rome_today() + timedelta(days=1)).isoformat()


class PartnerBase(unittest.TestCase):
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
            self.admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            self.serena = c.execute("SELECT * FROM users WHERE username='serena'").fetchone()
            self.vet_a = self.add_vet(c, "CLINICA ALFA", "ALFA", "Livorno")
            self.vet_b = self.add_vet(c, "CLINICA BETA", "BETA", "Pisa")
            self.vet_c = self.add_vet(c, "CLINICA GAMMA", "GAMMA", "Empoli")
            self.clinic_a = ps.create_clinic(c, veterinarian_id=self.vet_a, has_freezer=True, vouchers_enabled=True)
            self.clinic_b = ps.create_clinic(c, veterinarian_id=self.vet_b)
            self.user_a = ps.add_partner_user(c, clinic_id=self.clinic_a, email="Dott.A@Alfa.it", role="titolare")
            self.user_b = ps.add_partner_user(c, clinic_id=self.clinic_b, email="dott.b@beta.it")
        self.counter = 0

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    @staticmethod
    def add_vet(c, name, short, city):
        stamp = "2026-01-01T00:00:00"
        return c.execute(
            """INSERT INTO veterinarians(clinic_name,short_name,doctor_name,phone,address,city,active,created_at,updated_at)
               VALUES(?,?,?,?,?,?,1,?,?)""",
            (name, short, "", "0586123456", f"Via {short} 1", city, stamp, stamp)).lastrowid

    def make(self, clinic_id=None, user_id=None, **overrides):
        self.counter += 1
        params = dict(
            clinic_id=clinic_id or self.clinic_a,
            user_id=user_id if user_id is not None else (self.user_a if (clinic_id or self.clinic_a) == self.clinic_a else self.user_b),
            client_request_id=overrides.pop("client_request_id", f"tok-{self.counter}"),
            mode="ritiro_clinica", service_type="Cremazione singola",
            owner_first_name="Mario", owner_last_name="Rossi", owner_phone="333 1234567",
            species="Cane", weight="20 kg", animal_name="Fido",
            proposed_date=tomorrow(), proposed_from="09:00", proposed_to="12:00",
        )
        params.update(overrides)
        with app.db() as c:
            return ps.create_request(c, **params)

    def events(self, request_id):
        with app.db() as c:
            return [(r["kind"], r["public_status"]) for r in
                    c.execute("SELECT * FROM partner_events WHERE request_id=? ORDER BY id", (request_id,))]

    def outbox(self, request_id):
        with app.db() as c:
            return [r["public_status"] for r in c.execute(
                """SELECT e.public_status FROM partner_outbox o JOIN partner_events e ON e.id=o.event_id
                   WHERE o.request_id=? ORDER BY o.id""", (request_id,))]

    def event_of(self, request):
        with app.db() as c:
            return c.execute("SELECT * FROM calendar_events WHERE id=?", (request["calendar_event_id"],)).fetchone()

    def add_practice(self, c, status="Ritirato", number="CR-PRT-1"):
        stamp = "2026-07-15T10:00:00"
        return c.execute(
            """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
               service_type,payment_status) VALUES(?,?,?,?,?,?,?,?,?)""",
            (number, "Veterinario", "Livorno", status, stamp, stamp, self.admin["id"], "Cremazione singola", "Da saldare")).lastrowid

    def add_voucher(self, vet_id, n=1):
        ids = []
        with app.db() as c:
            for i in range(n):
                ids.append(c.execute(
                    "INSERT INTO veterinarian_vouchers(veterinarian_id,status,created_at,note) VALUES(?,?,?,?)",
                    (vet_id, "Maturato", f"2026-06-0{i + 1}T10:00:00", "Manuale: test")).lastrowid)
        return ids


class PartnerSchemaAndAdminTests(PartnerBase):
    def test_init_db_is_idempotent_and_creates_no_system_user_or_rows(self):
        app.init_db()
        app.init_db()
        with app.db() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM users WHERE username=?", (ps.SYSTEM_USERNAME,)).fetchone())
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_events").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_clinics").fetchone()[0], 2)

    def test_clinic_rules_and_users(self):
        with app.db() as c:
            with self.assertRaises(ps.PartnerError):
                ps.create_clinic(c, veterinarian_id=self.vet_a)  # gia' attivata
            with self.assertRaises(ps.PartnerError):
                ps.create_clinic(c, veterinarian_id=99999)
            with self.assertRaises(ps.PartnerError):
                ps.add_partner_user(c, clinic_id=self.clinic_a, email="dott.a@alfa.it")  # duplicata, case-insensitive
            with self.assertRaises(ps.PartnerError):
                ps.add_partner_user(c, clinic_id=self.clinic_a, email="non-una-email")
            with self.assertRaises(ps.PartnerError):
                ps.add_partner_user(c, clinic_id=self.clinic_a, email="x@y.it", role="boss")
            self.assertEqual(c.execute("SELECT email FROM partner_users WHERE id=?", (self.user_a,)).fetchone()[0], "dott.a@alfa.it")
            ps.update_clinic(c, self.clinic_b, has_freezer=True, vouchers_enabled=True, notify_email="Info@Beta.it")
            row = c.execute("SELECT * FROM partner_clinics WHERE id=?", (self.clinic_b,)).fetchone()
            self.assertEqual((row["has_freezer"], row["vouchers_enabled"], row["notify_email"]), (1, 1, "info@beta.it"))

    def test_admin_page_and_actions_only_for_admin(self):
        pages, errors, redirects = [], [], []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.send_error = lambda code, *a: errors.append(code)
        self.handler.redirect = redirects.append
        self.handler.path = "/portale-partner"
        self.handler.portal_partner_page(self.serena)
        self.assertEqual(errors, [403])
        self.handler.form = lambda: {"veterinarian_id": str(self.vet_c)}
        self.handler.portal_partner_action(self.serena, "create_clinic")
        self.assertEqual(errors, [403, 403])
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_clinics").fetchone()[0], 2)
        self.handler.portal_partner_page(self.admin)
        html = pages[-1]
        self.assertIn("CLINICA ALFA".split()[1], html)
        self.assertIn("Attiva una clinica", html)
        self.assertIn("GAMMA", html)  # veterinario ancora attivabile nel menu
        self.assertIn("Ha il congelatore", html)

    def test_admin_creates_updates_clinic_adds_user_and_plans_freezer(self):
        pages, redirects = [], []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.redirect = redirects.append
        self.handler.path = "/portale-partner"
        self.handler.form = lambda: {"veterinarian_id": str(self.vet_c), "has_freezer": "1", "vouchers_enabled": "1", "notify_email": "g@gamma.it"}
        self.handler.portal_partner_action(self.admin, "create_clinic")
        self.assertEqual(redirects[-1], "/portale-partner?ok=1")
        with app.db() as c:
            clinic = c.execute("SELECT * FROM partner_clinics WHERE veterinarian_id=?", (self.vet_c,)).fetchone()
        self.assertEqual((clinic["has_freezer"], clinic["vouchers_enabled"], clinic["notify_email"]), (1, 1, "g@gamma.it"))
        cid = clinic["id"]
        self.handler.form = lambda: {"has_freezer": "1", "notify_email": "g@gamma.it"}  # active e buoni tolti
        self.handler.portal_partner_action(self.admin, "update_clinic", cid)
        with app.db() as c:
            clinic = c.execute("SELECT * FROM partner_clinics WHERE id=?", (cid,)).fetchone()
        self.assertEqual((clinic["active"], clinic["vouchers_enabled"]), (0, 0))
        self.handler.form = lambda: {"email": "Nuovo@Gamma.it", "display_name": "Nuovo", "role": "staff"}
        self.handler.portal_partner_action(self.admin, "add_user", cid)
        with app.db() as c:
            user = c.execute("SELECT * FROM partner_users WHERE clinic_id=?", (cid,)).fetchone()
        self.assertEqual((user["email"], user["role"], user["active"]), ("nuovo@gamma.it", "staff", 1))
        self.handler.form = lambda: {"active": "0"}
        self.handler.portal_partner_action(self.admin, "toggle_user", user["id"])
        with app.db() as c:
            self.assertEqual(c.execute("SELECT active FROM partner_users WHERE id=?", (user["id"],)).fetchone()[0], 0)
        # errore di dominio: ricompare il messaggio, nessun redirect "ok"
        redirects.clear()
        self.handler.form = lambda: {"email": "nuovo@gamma.it"}
        self.handler.portal_partner_action(self.admin, "add_user", cid)
        self.assertFalse(redirects)
        self.assertIn("Esiste gia", pages[-1])
        # congelatore: animali in attesa -> pianificazione dalla pagina interna
        self.make(clinic_id=self.clinic_a, service_type="Cremazione collettiva", freezer=True,
                  proposed_date="", proposed_from="", proposed_to="")
        self.handler.portal_partner_page(self.admin)
        self.assertIn("Congelatore: 1 in attesa", pages[-1])
        self.assertIn("Pianifica svuotamento", pages[-1])
        self.handler.form = lambda: {"start_date": tomorrow(), "start_time": "09:00", "end_time": "12:00", "operator_name": "Serena"}
        self.handler.portal_partner_action(self.admin, "plan_freezer", self.clinic_a)
        self.assertEqual(redirects[-1], "/portale-partner?ok=1")
        with app.db() as c:
            self.assertEqual(len(ps.pending_freezer_requests(c, self.clinic_a)), 0)

    def test_veterinari_page_shows_portal_button_only_to_admin(self):
        pages = []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.path = "/veterinari"
        self.handler.veterinarians_page(self.admin)
        self.assertIn('href="/portale-partner"', pages[-1])
        self.handler.veterinarians_page(self.serena)
        self.assertNotIn('href="/portale-partner"', pages[-1])


class PartnerRequestTests(PartnerBase):
    def test_three_modes_create_the_right_calendar_event(self):
        clinic, created = self.make(mode="ritiro_clinica")
        self.assertTrue(created)
        ev = self.event_of(clinic)
        self.assertEqual((ev["event_type"], ev["location_type"], ev["event_status"]), ("Ritiro", "Veterinario", "Da confermare"))
        self.assertEqual(ev["veterinarian_id"], self.vet_a)
        self.assertEqual(ev["zone"], "Livorno")
        self.assertEqual((ev["client_first_name"], ev["client_last_name"], ev["client_phone"]), ("Mario", "Rossi", "333 1234567"))
        self.assertIn(clinic["request_code"], ev["notes"])
        self.assertTrue(clinic["request_code"].startswith("RP-") and len(clinic["request_code"]) == 9)
        with app.db() as c:
            animal = c.execute("SELECT * FROM calendar_event_animals WHERE event_id=?", (ev["id"],)).fetchone()
            sys_user = c.execute("SELECT * FROM users WHERE username=?", (ps.SYSTEM_USERNAME,)).fetchone()
            hist = c.execute("SELECT action FROM calendar_event_history WHERE event_id=?", (ev["id"],)).fetchone()
        self.assertEqual((animal["name"], animal["species"], animal["weight"], animal["cremation_type"]), ("Fido", "Cane", "20", "Singola"))
        self.assertEqual((sys_user["active"], ev["created_by"]), (0, sys_user["id"]))
        self.assertEqual(hist["action"], "Creazione evento")

        home, _ = self.make(mode="ritiro_domicilio", pickup_address="Via Roma 5, Livorno", service_type="Cremazione collettiva")
        ev = self.event_of(home)
        self.assertEqual((ev["event_type"], ev["location_type"], ev["address"]), ("Ritiro", "Privato", "Via Roma 5, Livorno"))
        self.assertEqual(self.event_of(home)["veterinarian_id"], self.vet_a)  # la clinica resta il riferimento
        with app.db() as c:
            self.assertEqual(c.execute("SELECT cremation_type FROM calendar_event_animals WHERE event_id=?", (ev["id"],)).fetchone()[0], "Collettiva")

        in_sede, _ = self.make(mode="invio_in_sede", destination_site="Empoli")
        ev = self.event_of(in_sede)
        self.assertEqual((ev["event_type"], ev["destination_site"], ev["location_type"]), ("Ritiro in sede", "Empoli", ""))
        self.assertTrue(in_sede["request_code"].startswith("RP-"))  # codice da presentare in sede
        codes = {clinic["request_code"], home["request_code"], in_sede["request_code"]}
        self.assertEqual(len(codes), 3)

    def test_required_fields_are_validated_with_nothing_saved(self):
        bad = [
            ({"mode": "x"}, "modalita"),
            ({"service_type": "Altro"}, "servizio"),
            ({"owner_first_name": "", "owner_last_name": ""}, "nome"),
            ({"owner_phone": "12"}, "telefono"),
            ({"species": ""}, "specie"),
            ({"weight": ""}, "peso"),
            ({"mode": "ritiro_domicilio"}, "indirizzo"),
            ({"mode": "invio_in_sede", "destination_site": "Roma"}, "sede"),
            ({"proposed_date": ""}, "data"),
            ({"proposed_date": "2020-01-01"}, "passato"),
            ({"proposed_from": "", "proposed_to": ""}, "fascia"),
            ({"proposed_from": "12:00", "proposed_to": "09:00"}, "fascia"),
            ({"proposed_from": "9", "proposed_to": "10"}, "fascia"),
            ({"client_request_id": ""}, "Richiesta non valida"),
        ]
        for overrides, fragment in bad:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ps.PartnerError) as ctx:
                    self.make(**overrides)
                self.assertIn(fragment.lower(), str(ctx.exception).lower())
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0], 0)

    def test_inactive_clinic_and_foreign_or_inactive_user_are_rejected(self):
        with self.assertRaises(ps.PartnerError):
            self.make(clinic_id=self.clinic_a, user_id=self.user_b)  # utente di un'altra clinica
        with app.db() as c:
            ps.set_partner_user_active(c, self.user_a, False)
        with self.assertRaises(ps.PartnerError):
            self.make(clinic_id=self.clinic_a, user_id=self.user_a)
        with app.db() as c:
            ps.set_partner_user_active(c, self.user_a, True)
            ps.update_clinic(c, self.clinic_a, active=False)
        with self.assertRaises(ps.PartnerError):
            self.make(clinic_id=self.clinic_a, user_id=self.user_a)

    def test_double_submit_is_idempotent(self):
        first, created1 = self.make(client_request_id="same-token")
        second, created2 = self.make(client_request_id="same-token")
        self.assertEqual((created1, created2), (True, False))
        self.assertEqual(first["id"], second["id"])
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0], 1)
            notes = c.execute("SELECT COUNT(*) FROM notifications WHERE type='partner_request_created' AND user_id=?",
                              (self.admin["id"],)).fetchone()[0]
        self.assertEqual(notes, 1)
        self.assertEqual(self.events(first["id"]), [("stato", "ricevuta")])
        # lo stesso token di un'ALTRA clinica e' una richiesta diversa
        other, created3 = self.make(clinic_id=self.clinic_b, client_request_id="same-token")
        self.assertTrue(created3)
        self.assertNotEqual(other["id"], first["id"])

    def test_proposed_window_and_urgent_flag_are_kept_separate_from_the_event(self):
        req, _ = self.make(urgent=True)
        self.assertEqual((req["proposed_date"], req["proposed_from"], req["proposed_to"], req["urgent"]),
                         (tomorrow(), "09:00", "12:00", 1))
        ev = self.event_of(req)
        self.assertEqual((ev["start_at"], ev["end_at"], ev["all_day"]), (f"{tomorrow()}T09:00:00", f"{tomorrow()}T12:00:59", 0))
        self.assertTrue(ev["title"].startswith("PORTALE · URGENTE"))
        self.assertIn("URGENTE", ev["notes"])
        # staff sposta l'orario: la proposta originale resta intatta
        with app.db() as c:
            c.execute("UPDATE calendar_events SET start_at=?,end_at=? WHERE id=?",
                      (f"{tomorrow()}T15:00:00", f"{tomorrow()}T16:00:59", ev["id"]))
            row = ps.get_request(c, self.clinic_a, req["id"])
        self.assertEqual((row["proposed_from"], row["proposed_to"]), ("09:00", "12:00"))
        self.assertEqual(row["event_start"], f"{tomorrow()}T15:00:00")

    def test_urgent_without_window_defaults_to_today_all_day(self):
        req, _ = self.make(urgent=True, proposed_date="", proposed_from="", proposed_to="")
        ev = self.event_of(req)
        self.assertEqual(req["proposed_date"], ps._rome_today().isoformat())
        self.assertEqual(ev["all_day"], 1)
        self.assertEqual(ev["start_at"][:10], ps._rome_today().isoformat())

    def test_internal_notifications_normal_and_urgent_priority(self):
        self.make()
        self.make(urgent=True)
        with app.db() as c:
            kinds = [r["type"] for r in c.execute(
                "SELECT type FROM notifications WHERE user_id=? ORDER BY id", (self.admin["id"],))]
        self.assertEqual(kinds, ["partner_request_created", "partner_request_urgent"])
        import notification_service as ns
        self.assertEqual(ns.notification_priority("partner_request_urgent"), "alta")
        self.assertEqual(ns.notification_priority("partner_request_created"), "normale")
        self.assertIn("partner_request_created", ns.NON_GROUPABLE_NOTIFICATION_TYPES)

    def test_cancel_only_before_taken_in_charge_and_only_by_own_clinic(self):
        req, _ = self.make()
        with app.db() as c:
            with self.assertRaises(ps.PartnerError):
                ps.cancel_request(c, clinic_id=self.clinic_b, request_id=req["id"], user_id=self.user_b)
            done = ps.cancel_request(c, clinic_id=self.clinic_a, request_id=req["id"], user_id=self.user_a)
        self.assertEqual(done["public_status"], "annullata")
        self.assertEqual(self.event_of(req)["event_status"], "Annullato")
        self.assertEqual(self.events(req["id"]), [("stato", "ricevuta"), ("stato", "annullata")])
        # una richiesta gia' programmata dallo staff non si annulla dal portale
        req2, _ = self.make()
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (req2["calendar_event_id"],))
            with self.assertRaises(ps.PartnerError):
                ps.cancel_request(c, clinic_id=self.clinic_a, request_id=req2["id"], user_id=self.user_a)


class PartnerWeightAndDemoTests(PartnerBase):
    def test_weight_is_stored_numeric_for_the_calendar_and_taglia_goes_to_notes(self):
        for text, weight, note in (("12 kg", "12", ""), ("12,5kg", "12.5", ""), ("8", "8", ""), ("20 KG", "20", ""),
                                   ("media", "", "Taglia: media"), ("taglia piccola 5kg", "", "Taglia: taglia piccola 5kg")):
            with self.subTest(text=text):
                self.assertEqual(ps._split_weight(text), (weight, note))
        req, _ = self.make(weight="media")
        with app.db() as c:
            animal = c.execute("SELECT * FROM calendar_event_animals WHERE event_id=?", (req["calendar_event_id"],)).fetchone()
        self.assertEqual((animal["weight"], animal["notes"]), ("", "Taglia: media"))
        self.assertEqual(req["weight_text"], "media")  # il testo scritto dal veterinario resta com'e'

    def test_demo_clinic_events_are_marked_as_trial(self):
        with app.db() as c:
            ps.update_clinic(c, self.clinic_b)  # no-op
            c.execute("UPDATE partner_clinics SET is_demo=1 WHERE id=?", (self.clinic_b,))
        demo, _ = self.make(clinic_id=self.clinic_b)
        real, _ = self.make(clinic_id=self.clinic_a)
        self.assertTrue(self.event_of(demo)["title"].startswith("PORTALE (PROVA)"))
        self.assertTrue(self.event_of(real)["title"].startswith("PORTALE ·"))

    def test_request_freezer_pickup_service_rules(self):
        freezer_kw = dict(service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="")
        a, _ = self.make(**freezer_kw)
        b, _ = self.make(**freezer_kw)
        with app.db() as c:
            with self.assertRaises(ps.PartnerError):  # data nel passato
                ps.request_freezer_pickup(c, clinic_id=self.clinic_a, user_id=self.user_a, proposed_date="2020-01-01",
                                          proposed_from="09:00", proposed_to="13:00")
            with self.assertRaises(ps.PartnerError):  # utente di un'altra clinica
                ps.request_freezer_pickup(c, clinic_id=self.clinic_a, user_id=self.user_b, proposed_date=tomorrow(),
                                          proposed_from="09:00", proposed_to="13:00")
            ids = ps.request_freezer_pickup(c, clinic_id=self.clinic_a, user_id=self.user_a, proposed_date=tomorrow(),
                                            proposed_from="14:00", proposed_to="18:00", urgent=True)
            with self.assertRaises(ps.PartnerError):  # piu' nulla in attesa
                ps.request_freezer_pickup(c, clinic_id=self.clinic_a, user_id=self.user_a, proposed_date=tomorrow(),
                                          proposed_from="14:00", proposed_to="18:00")
            rows = [ps.get_request(c, self.clinic_a, r["id"]) for r in (a, b)]
            kinds = [r["type"] for r in c.execute("SELECT type FROM notifications WHERE user_id=? ORDER BY id", (self.admin["id"],))]
        self.assertEqual(len(ids), 2)
        for row in rows:
            self.assertEqual((row["public_status"], row["urgent"], row["proposed_from"], row["event_status"]),
                             ("ricevuta", 1, "14:00", "Da confermare"))
        self.assertEqual(self.events(a["id"]), [("stato", "in_congelatore"), ("stato", "ricevuta")])
        self.assertEqual(kinds[-1], "partner_request_urgent")


class PartnerIsolationTests(PartnerBase):
    def test_every_read_is_scoped_to_the_clinic(self):
        a1, _ = self.make(clinic_id=self.clinic_a)
        a2, _ = self.make(clinic_id=self.clinic_a)
        b1, _ = self.make(clinic_id=self.clinic_b)
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (a1["calendar_event_id"],))
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (b1["calendar_event_id"],))
            self.assertEqual({r["id"] for r in ps.list_requests(c, self.clinic_a)}, {a1["id"], a2["id"]})
            self.assertEqual({r["id"] for r in ps.list_requests(c, self.clinic_b)}, {b1["id"]})
            self.assertIsNone(ps.get_request(c, self.clinic_a, b1["id"]))
            self.assertIsNone(ps.get_request(c, self.clinic_b, a1["id"]))
            self.assertEqual(ps.request_timeline(c, self.clinic_a, b1["id"]), [])
            self.assertEqual(ps.request_timeline(c, self.clinic_b, a1["id"]), [])
            self.assertTrue(ps.request_timeline(c, self.clinic_a, a1["id"]))
            seen_a = {r["request_id"] for r in ps.events_since(c, self.clinic_a, 0)}
            seen_b = {r["request_id"] for r in ps.events_since(c, self.clinic_b, 0)}
        self.assertEqual(seen_a, {a1["id"], a2["id"]})
        self.assertEqual(seen_b, {b1["id"]})

    def test_events_since_cursor_returns_only_new_events(self):
        req, _ = self.make()
        with app.db() as c:
            first = ps.events_since(c, self.clinic_a, 0)
            cursor = first[-1]["id"]
            self.assertEqual(ps.events_since(c, self.clinic_a, cursor), [])
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (req["calendar_event_id"],))
            new = ps.events_since(c, self.clinic_a, cursor)
        self.assertEqual([(r["public_status"], r["request_code"]) for r in new], [("programmato", req["request_code"])])

    def test_clinic_cannot_see_other_clinic_vouchers(self):
        self.add_voucher(self.vet_a, 2)
        self.add_voucher(self.vet_b, 1)
        with app.db() as c:
            self.assertEqual(len(ps.available_vouchers(c, self.clinic_a)), 2)
            self.assertEqual(len(ps.available_vouchers(c, self.clinic_b)), 1)


class PartnerStatusTriggerTests(PartnerBase):
    def test_full_lifecycle_through_real_code_paths(self):
        req, _ = self.make()
        rid, eid = req["id"], req["calendar_event_id"]
        self.assertEqual(self.events(rid), [("stato", "ricevuta")])
        with app.db() as c:
            # lo staff conferma dal calendario (stesso UPDATE di save_calendar_event, tutte le colonne)
            c.execute("UPDATE calendar_events SET event_status='Da ritirare',operator_name='Serena',start_at=start_at,end_at=end_at WHERE id=?", (eid,))
            # stesso valore riscritto: nessun evento duplicato
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (eid,))
        self.assertEqual(self.events(rid), [("stato", "ricevuta"), ("stato", "programmato")])
        with app.db() as c:
            last = c.execute("SELECT * FROM partner_events WHERE request_id=? ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
        self.assertEqual((last["scheduled_start"], last["scheduled_end"]), (f"{tomorrow()}T09:00:00", f"{tomorrow()}T12:00:59"))
        # ritiro spostato
        with app.db() as c:
            c.execute("UPDATE calendar_events SET start_at=?,end_at=? WHERE id=?", (f"{tomorrow()}T14:00:00", f"{tomorrow()}T15:00:59", eid))
        self.assertEqual(self.events(rid)[-1], ("riprogrammato", "programmato"))
        with app.db() as c:
            c.execute("UPDATE calendar_events SET start_at=? WHERE id=?", (f"{tomorrow()}T14:00:00", eid))  # invariato
        self.assertEqual(len(self.events(rid)), 3)
        # ritirato
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Ritirato' WHERE id=?", (eid,))
        self.assertEqual(self.events(rid)[-1], ("stato", "ritirato"))
        # creazione pratica dal flusso esistente: UPDATE linked_practice_id, pratica 'Ritirato' -> stesso stato pubblico
        with app.db() as c:
            pid = self.add_practice(c, "Ritirato")
            c.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, eid))
        self.assertEqual(self.events(rid)[-1], ("stato", "ritirato"))
        self.assertEqual(len(self.events(rid)), 4)
        # cambi di stato pratica dai veri punti di scrittura (ciclo di cremazione, cambio rapido)
        with app.db() as c:
            app.cremation_log_status_change(c, pid, "Ritirato", "In programma", self.admin["id"], app.now())
        self.assertEqual(self.events(rid)[-1], ("stato", "in_lavorazione"))
        with app.db() as c:
            app.cremation_log_status_change(c, pid, "In programma", "Cremato", self.admin["id"], app.now())
        self.assertEqual(self.events(rid)[-1], ("stato", "in_lavorazione"))  # Cremato resta "in lavorazione"
        self.assertEqual(len(self.events(rid)), 5)
        with app.db() as c:
            c.execute("UPDATE practices SET status='Da consegnare' WHERE id=?", (pid,))
        self.assertEqual(self.events(rid)[-1], ("stato", "pronto_riconsegna"))
        with app.db() as c:
            c.execute("UPDATE practices SET status='Consegnato' WHERE id=?", (pid,))
        self.assertEqual(self.events(rid)[-1], ("stato", "completata"))
        with app.db() as c:
            c.execute("UPDATE practices SET status='Smaltito' WHERE id=?", (pid,))
        self.assertEqual(len(self.events(rid)), 7)  # Smaltito e' ancora "completata": nessun nuovo evento
        with app.db() as c:
            row = ps.get_request(c, self.clinic_a, rid)
        self.assertEqual((row["public_status"], row["practice_id"]), ("completata", pid))
        # email in coda: tutti gli eventi tranne "in lavorazione"
        self.assertEqual(self.outbox(rid), ["ricevuta", "programmato", "programmato", "ritirato", "pronto_riconsegna", "completata"])

    def staff_form(self, req, **changes):
        form = {
            "event_type": "Ritiro", "operator_name": "Serena", "title": "", "zone": "Livorno",
            "location_type": "Veterinario", "address": "", "veterinarian_id": str(self.vet_a),
            "client_first_name": "Mario", "client_last_name": "Rossi", "client_phone": "333 1234567",
            "start_date": tomorrow(), "start_time": "09:00", "end_date": tomorrow(), "end_time": "12:00",
            "event_status": "Da ritirare", "animals_json": json.dumps([
                {"name": "Fido", "species": "Cane", "weight": "20 kg", "cremation_type": "Singola", "notes": ""}]),
            "estimate_json": "[]", "payment_status": "", "payment_amount": "0",
        }
        form.update(changes)
        return form

    def test_staff_confirms_and_reschedules_through_the_real_calendar_form(self):
        from unittest.mock import patch
        req, _ = self.make()
        eid = req["calendar_event_id"]
        pages = []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.path = f"/calendario/{eid}/modifica"
        self.handler.calendar_event_form(self.admin, eid)
        self.assertIn("PORTALE", pages[-1])  # lo staff apre l'evento senza errori
        self.handler.path = f"/calendario/{eid}"
        self.handler.calendar_event_detail(self.admin, eid)
        self.assertIn(req["request_code"], pages[-1])
        redirected = []
        self.handler.redirect = redirected.append
        # conferma: stato "Da ritirare" con la fascia proposta
        self.handler.form = lambda: self.staff_form(req)
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin, eid)
        self.assertEqual(self.events(req["id"]), [("stato", "ricevuta"), ("stato", "programmato")])
        # riprogrammazione: stessa pagina, orario diverso
        self.handler.form = lambda: self.staff_form(req, start_time="15:00", end_time="16:30")
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin, eid)
        self.assertEqual(self.events(req["id"])[-1], ("riprogrammato", "programmato"))
        with app.db() as c:
            last = c.execute("SELECT * FROM partner_events WHERE request_id=? ORDER BY id DESC LIMIT 1", (req["id"],)).fetchone()
            row = ps.get_request(c, self.clinic_a, req["id"])
        self.assertEqual(last["scheduled_start"], f"{tomorrow()}T15:00:00")
        self.assertEqual((row["public_status"], row["proposed_from"]), ("programmato", "09:00"))
        # salvataggio ripetuto identico: nessun evento in piu'
        count = len(self.events(req["id"]))
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin, eid)
        self.assertEqual(len(self.events(req["id"])), count)
        # ritirato dal form
        self.handler.form = lambda: self.staff_form(req, start_time="15:00", end_time="16:30", event_status="Ritirato")
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin, eid)
        self.assertEqual(self.events(req["id"])[-1], ("stato", "ritirato"))

    def test_staff_cancels_through_the_real_calendar_form(self):
        from unittest.mock import patch
        req, _ = self.make()
        self.handler.redirect = lambda url: None
        self.handler.form = lambda: self.staff_form(req, event_status="Annullato")
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin, req["calendar_event_id"])
        self.assertEqual(self.events(req["id"])[-1], ("stato", "annullata"))

    def test_practice_created_before_pickup_uses_event_status_until_it_moves_on(self):
        req, _ = self.make()
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (req["calendar_event_id"],))
            pid = self.add_practice(c, "Da ritirare", "CR-PRT-2")
            c.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, req["calendar_event_id"]))
        self.assertEqual(self.events(req["id"]), [("stato", "ricevuta"), ("stato", "programmato")])
        with app.db() as c:
            c.execute("UPDATE practices SET status='Ritirato' WHERE id=?", (pid,))
        self.assertEqual(self.events(req["id"])[-1], ("stato", "ritirato"))

    def test_staff_cancels_or_deletes_the_event(self):
        a, _ = self.make()
        b, _ = self.make()
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Annullato' WHERE id=?", (a["calendar_event_id"],))
            c.execute("UPDATE calendar_events SET deleted_at='2026-07-20T10:00:00' WHERE id=?", (b["calendar_event_id"],))
        self.assertEqual(self.events(a["id"])[-1], ("stato", "annullata"))
        self.assertEqual(self.events(b["id"])[-1], ("stato", "annullata"))
        self.assertEqual(self.outbox(a["id"])[-1], "annullata")

    def test_trashed_practice_falls_back_to_event_status(self):
        req, _ = self.make()
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Ritirato' WHERE id=?", (req["calendar_event_id"],))
            pid = self.add_practice(c, "Consegnato", "CR-PRT-3")
            c.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, req["calendar_event_id"]))
        self.assertEqual(self.events(req["id"])[-1], ("stato", "completata"))
        with app.db() as c:
            c.execute("UPDATE practices SET deleted_at='2026-07-20T10:00:00' WHERE id=?", (pid,))
        self.assertEqual(self.events(req["id"])[-1], ("stato", "ritirato"))

    def test_other_clinics_and_unrelated_practices_generate_no_events(self):
        a, _ = self.make(clinic_id=self.clinic_a)
        b, _ = self.make(clinic_id=self.clinic_b)
        with app.db() as c:
            pid = self.add_practice(c, "Ritirato", "CR-OTHER")  # pratica non legata al portale
            c.execute("UPDATE practices SET status='Consegnato' WHERE id=?", (pid,))
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (a["calendar_event_id"],))
        self.assertEqual(self.events(a["id"]), [("stato", "ricevuta"), ("stato", "programmato")])
        self.assertEqual(self.events(b["id"]), [("stato", "ricevuta")])

    def test_events_are_append_only(self):
        req, _ = self.make()
        with app.db() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE partner_events SET public_status='completata' WHERE request_id=?", (req["id"],))
        with app.db() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM partner_events WHERE request_id=?", (req["id"],))
        self.assertEqual(self.events(req["id"]), [("stato", "ricevuta")])

    def test_status_labels_for_in_sede_mode(self):
        self.assertEqual(ps.public_status_label("ritirato", "ritiro_clinica"), "Ritirato")
        self.assertEqual(ps.public_status_label("ritirato", "invio_in_sede"), "Arrivato in sede")
        self.assertEqual(ps.public_status_label("in_lavorazione"), "In lavorazione")
        # "Cremato" non e' uno stato pubblico
        self.assertNotIn("cremato", ps.PUBLIC_STATUS_LABELS)


class PartnerFreezerTests(PartnerBase):
    def freezer(self, **kw):
        params = dict(service_type="Cremazione collettiva", freezer=True, proposed_date="", proposed_from="", proposed_to="")
        params.update(kw)
        return self.make(**params)

    def test_freezer_request_has_no_event_and_no_urgency(self):
        req, created = self.freezer()
        self.assertTrue(created)
        self.assertIsNone(req["calendar_event_id"])
        self.assertEqual(self.events(req["id"]), [("stato", "in_congelatore")])
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0], 0)
            row = ps.get_request(c, self.clinic_a, req["id"])
            self.assertEqual(len(ps.pending_freezer_requests(c, self.clinic_a)), 1)
            overview = {r["id"]: r for r in ps.clinics_overview(c)}
        self.assertEqual(row["public_status"], "in_congelatore")
        self.assertEqual(overview[self.clinic_a]["freezer_pending"], 1)
        self.assertEqual(self.outbox(req["id"]), ["in_congelatore"])

    def test_freezer_is_available_only_where_the_rules_allow(self):
        cases = [
            (dict(clinic_id=self.clinic_b), "non e' abilitata"),                      # clinica senza congelatore
            (dict(service_type="Cremazione singola"), "collettive"),                  # solo collettive
            (dict(mode="ritiro_domicilio", pickup_address="Via X 1"), "presso la clinica"),
            (dict(urgent=True), "urgente"),
        ]
        for overrides, fragment in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ps.PartnerError) as ctx:
                    self.freezer(**overrides)
                self.assertIn(fragment, str(ctx.exception))
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0], 0)

    def test_planning_creates_one_confirmed_event_per_animal_and_clinic_sees_scheduled(self):
        r1, _ = self.freezer(animal_name="Pippo")
        r2, _ = self.freezer(animal_name="Micio", species="Gatto", weight="4 kg")
        other, _ = self.make(clinic_id=self.clinic_a)  # richiesta normale: non coinvolta
        with app.db() as c:
            with self.assertRaises(ps.PartnerError):
                ps.plan_freezer_pickup(c, clinic_id=self.clinic_a, start_date="31/12", start_time="09:00", end_time="12:00",
                                       operator_name="Serena", staff_user_id=self.admin["id"])
            with self.assertRaises(ps.PartnerError):
                ps.plan_freezer_pickup(c, clinic_id=self.clinic_a, start_date=tomorrow(), start_time="12:00", end_time="09:00",
                                       operator_name="Serena", staff_user_id=self.admin["id"])
            with self.assertRaises(ps.PartnerError):
                ps.plan_freezer_pickup(c, clinic_id=self.clinic_a, start_date=tomorrow(), start_time="09:00", end_time="12:00",
                                       operator_name="Nessuno", staff_user_id=self.admin["id"])
            with self.assertRaises(ps.PartnerError):  # clinica senza animali in congelatore
                ps.plan_freezer_pickup(c, clinic_id=self.clinic_b, start_date=tomorrow(), start_time="09:00", end_time="12:00",
                                       operator_name="Serena", staff_user_id=self.admin["id"])
            ids = ps.plan_freezer_pickup(c, clinic_id=self.clinic_a, start_date=tomorrow(), start_time="09:00", end_time="12:00",
                                         operator_name="serena", staff_user_id=self.admin["id"])
        self.assertEqual(len(ids), 2)
        with app.db() as c:
            events = [c.execute("SELECT * FROM calendar_events WHERE id=?", (i,)).fetchone() for i in ids]
            statuses = [ps.get_request(c, self.clinic_a, r["id"])["public_status"] for r in (r1, r2)]
            self.assertEqual(len(ps.pending_freezer_requests(c, self.clinic_a)), 0)
        for ev in events:
            self.assertEqual((ev["event_status"], ev["operator_name"], ev["event_type"], ev["created_by"]), ("Da ritirare", "Serena", "Ritiro", self.admin["id"]))
            self.assertIn("congelatore", ev["notes"].lower())
        self.assertEqual(statuses, ["programmato", "programmato"])
        self.assertEqual(self.events(r1["id"]), [("stato", "in_congelatore"), ("stato", "programmato")])
        self.assertEqual(self.events(other["id"]), [("stato", "ricevuta")])
        # pratica collegata dopo il ritiro -> stati successivi (1 evento = 1 pratica)
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Ritirato' WHERE id=?", (ids[0],))
        self.assertEqual(self.events(r1["id"])[-1], ("stato", "ritirato"))

    def test_clinic_can_cancel_a_freezer_entry(self):
        req, _ = self.freezer()
        with app.db() as c:
            done = ps.cancel_request(c, clinic_id=self.clinic_a, request_id=req["id"], user_id=self.user_a)
            self.assertEqual(len(ps.pending_freezer_requests(c, self.clinic_a)), 0)
        self.assertEqual(done["public_status"], "annullata")
        self.assertEqual(self.events(req["id"]), [("stato", "in_congelatore"), ("stato", "annullata")])


class PartnerVoucherTests(PartnerBase):
    def collective(self, **kw):
        params = dict(service_type="Cremazione collettiva", use_voucher=True)
        params.update(kw)
        return self.make(**params)

    def test_use_voucher_reserves_oldest_available_and_never_double_books(self):
        v1, v2 = self.add_voucher(self.vet_a, 2)
        r1, _ = self.collective()
        r2, _ = self.collective()
        self.assertEqual((r1["reserved_voucher_id"], r2["reserved_voucher_id"]), (v1, v2))
        with app.db() as c:
            self.assertEqual(ps.available_vouchers(c, self.clinic_a), [])
        with self.assertRaises(ps.PartnerError) as ctx:
            self.collective()
        self.assertIn("Nessun buono", str(ctx.exception))
        # il vincolo e' anche a livello di database
        with app.db() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE partner_requests SET reserved_voucher_id=? WHERE id=?", (v1, r2["id"]))

    def test_use_voucher_rules(self):
        self.add_voucher(self.vet_a, 1)
        self.add_voucher(self.vet_b, 1)
        with self.assertRaises(ps.PartnerError) as ctx:
            self.make(service_type="Cremazione singola", use_voucher=True)
        self.assertIn("collettive", str(ctx.exception))
        with self.assertRaises(ps.PartnerError) as ctx:  # clinica non in convenzione
            self.make(clinic_id=self.clinic_b, service_type="Cremazione collettiva", use_voucher=True)
        self.assertIn("buoni attivi", str(ctx.exception).lower())
        # un buono 'Usato' non e' disponibile
        with app.db() as c:
            c.execute("UPDATE veterinarian_vouchers SET status='Usato' WHERE veterinarian_id=?", (self.vet_a,))
        with self.assertRaises(ps.PartnerError):
            self.collective()

    def test_cancelling_the_request_releases_the_voucher(self):
        (v1,) = self.add_voucher(self.vet_a, 1)
        req, _ = self.collective()
        with app.db() as c:
            self.assertEqual(ps.available_vouchers(c, self.clinic_a), [])
            ps.cancel_request(c, clinic_id=self.clinic_a, request_id=req["id"], user_id=self.user_a)
            self.assertEqual([r["id"] for r in ps.available_vouchers(c, self.clinic_a)], [v1])
        again, _ = self.collective()
        self.assertEqual(again["reserved_voucher_id"], v1)

    def test_staff_cancelling_the_event_releases_the_voucher(self):
        (v1,) = self.add_voucher(self.vet_a, 1)
        req, _ = self.collective()
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Annullato' WHERE id=?", (req["calendar_event_id"],))
            self.assertEqual([r["id"] for r in ps.available_vouchers(c, self.clinic_a)], [v1])

    def test_reservation_ends_when_the_practice_is_created(self):
        (v1,) = self.add_voucher(self.vet_a, 1)
        req, _ = self.collective()
        with app.db() as c:
            pid = self.add_practice(c, "Ritirato", "SM-PRT-1")
            c.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, req["calendar_event_id"]))
            # buono NON consumato dalla pratica (lo staff ha tolto la spunta): torna disponibile
            self.assertEqual([r["id"] for r in ps.available_vouchers(c, self.clinic_a)], [v1])

    def test_freezer_request_can_reserve_a_voucher(self):
        (v1,) = self.add_voucher(self.vet_a, 1)
        req, _ = self.make(service_type="Cremazione collettiva", freezer=True, use_voucher=True,
                           proposed_date="", proposed_from="", proposed_to="")
        self.assertEqual(req["reserved_voucher_id"], v1)

    def test_prefill_singola_for_convention_clinic_only(self):
        a, _ = self.make(clinic_id=self.clinic_a, service_type="Cremazione singola")
        b, _ = self.make(clinic_id=self.clinic_b, service_type="Cremazione singola")
        c_coll, _ = self.make(clinic_id=self.clinic_a, service_type="Cremazione collettiva")
        with app.db() as c:
            self.assertEqual(ps.prefill_for_event(c, a["calendar_event_id"]), {"voucher_requested": "Si"})
            self.assertEqual(ps.prefill_for_event(c, b["calendar_event_id"]), {})   # non in convenzione
            self.assertEqual(ps.prefill_for_event(c, c_coll["calendar_event_id"]), {})  # collettiva senza buono
            self.assertEqual(ps.prefill_for_event(c, 99999), {})

    def test_prefill_collective_with_reserved_voucher(self):
        (v1,) = self.add_voucher(self.vet_a, 1)
        req, _ = self.collective()
        with app.db() as c:
            self.assertEqual(ps.prefill_for_event(c, req["calendar_event_id"]),
                             {"use_voucher": "Si", "used_voucher_id": str(v1)})
            c.execute("UPDATE veterinarian_vouchers SET status='Usato' WHERE id=?", (v1,))
            self.assertEqual(ps.prefill_for_event(c, req["calendar_event_id"]), {})


class PartnerPracticeIntegrationTests(PartnerBase):
    """Il portale si aggancia al flusso reale "Nuova pratica da evento"."""

    def render_new_page(self, event_id):
        pages = []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.path = f"/nuova?calendar_event_id={event_id}"
        self.handler.new_page(self.admin)
        return pages[-1]

    def test_new_practice_form_from_portal_event_prefills_voucher_and_owner(self):
        req, _ = self.make(service_type="Cremazione singola", owner_first_name="Luca", owner_last_name="Verdi")
        html = self.render_new_page(req["calendar_event_id"])
        self.assertRegex(html, r'name="voucher_requested"[^>]*checked')
        self.assertIn('value="Luca"', html)
        self.assertIn(f'value="{self.vet_a}"', html)

    def test_new_practice_form_without_convention_has_no_prefilled_voucher(self):
        req, _ = self.make(clinic_id=self.clinic_b, service_type="Cremazione singola")
        html = self.render_new_page(req["calendar_event_id"])
        self.assertNotRegex(html, r'name="voucher_requested"[^>]*checked')

    def test_voucher_matures_at_practice_creation_and_status_follows(self):
        req, _ = self.make(service_type="Cremazione singola")
        redirects = []
        self.handler.redirect = redirects.append
        self.handler.form = lambda: {
            "calendar_event_id": str(req["calendar_event_id"]),
            "operator_name": "SERENA", "service_type": "Cremazione singola", "request_origin": "Veterinario",
            "veterinarian_id": str(self.vet_a), "clinic_name": "CLINICA ALFA", "voucher_requested": "Si",
            "animal_name": "Fido", "owner_first_name": "Mario", "owner_last_name": "Rossi", "owner_phone": "3331234567",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "provenance": "L",
        }
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM veterinarian_vouchers").fetchone()[0], 0)
        self.handler.create_practice(self.admin)
        self.assertTrue(redirects and "/pratiche/" in redirects[-1], redirects)
        pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as c:
            voucher = c.execute("SELECT * FROM veterinarian_vouchers WHERE practice_id=?", (pid,)).fetchone()
            ev = c.execute("SELECT linked_practice_id FROM calendar_events WHERE id=?", (req["calendar_event_id"],)).fetchone()
            row = ps.get_request(c, self.clinic_a, req["id"])
            balance = len(ps.available_vouchers(c, self.clinic_a))
        self.assertEqual((voucher["status"], voucher["veterinarian_id"]), ("Maturato", self.vet_a))
        self.assertEqual(ev["linked_practice_id"], pid)
        self.assertEqual((row["public_status"], row["practice_id"]), ("ritirato", pid))
        self.assertEqual(balance, 1)  # il veterinario vede il nuovo buono maturato
        self.assertEqual(self.events(req["id"])[-1], ("stato", "ritirato"))

    def test_collective_with_reserved_voucher_consumes_it_at_practice_creation(self):
        (v1,) = self.add_voucher(self.vet_a, 1)
        req, _ = self.make(service_type="Cremazione collettiva", use_voucher=True)
        html = self.render_new_page(req["calendar_event_id"])
        self.assertRegex(html, r'name="use_voucher"[^>]*checked')
        redirects = []
        self.handler.redirect = redirects.append
        self.handler.form = lambda: {
            "calendar_event_id": str(req["calendar_event_id"]),
            "operator_name": "SERENA", "service_type": "Cremazione collettiva", "request_origin": "Veterinario",
            "veterinarian_id": str(self.vet_a), "clinic_name": "CLINICA ALFA", "use_voucher": "Si", "used_voucher_id": str(v1),
            "animal_name": "Fido", "owner_first_name": "Mario", "owner_last_name": "Rossi", "owner_phone": "3331234567",
            "owner_tax_code": "X", "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100",
            "provenance": "L",
        }
        self.handler.create_practice(self.admin)
        pid = int(redirects[-1].split("/pratiche/")[1])
        with app.db() as c:
            voucher = c.execute("SELECT * FROM veterinarian_vouchers WHERE id=?", (v1,)).fetchone()
            available = ps.available_vouchers(c, self.clinic_a)
        self.assertEqual(voucher["status"], "Usato")
        self.assertEqual(available, [])
        self.assertEqual(voucher["practice_id"] in (None, pid), True)


if __name__ == "__main__":
    unittest.main()
