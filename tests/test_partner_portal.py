import io
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import app
import partner_portal as pp
import partner_service as ps


def tomorrow():
    return (ps._rome_today() + timedelta(days=1)).isoformat()


class FakeHandler:
    """Quanto basta di BaseHTTPRequestHandler per pilotare partner_portal.dispatch."""

    def __init__(self, path, cookie="", form=None, headers=None):
        self.path = path
        self.headers = {"Cookie": cookie, **(headers or {})}
        self._form = form or {}
        self.client_address = ("127.0.0.1", 5000)
        self.status = None
        self.out_headers = []
        self.html = None
        self.json = None
        self.wfile = io.BytesIO()

    def form(self):
        return self._form

    def send_html(self, content, status=200):
        self.status, self.html = status, content

    def send_json(self, obj, status=200):
        self.status, self.json = status, obj

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.out_headers.append((key, value))

    def end_headers(self):
        pass

    def header(self, name):
        return next((v for k, v in self.out_headers if k.lower() == name.lower()), "")


class PortalBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        with app.db() as c:
            self.demo_clinic = ps.ensure_demo_clinic(c, app.password_hash)
            self.demo_user = c.execute("SELECT id FROM partner_users WHERE username='villadeipini'").fetchone()["id"]
            stamp = "2026-01-01T00:00:00"
            vet = c.execute(
                """INSERT INTO veterinarians(clinic_name,short_name,doctor_name,phone,address,city,active,created_at,updated_at)
                   VALUES('CLINICA ALTRA','ALTRA','','0586000','Via Altra 1','Pisa',1,?,?)""", (stamp, stamp)).lastrowid
            self.other_clinic = ps.create_clinic(c, veterinarian_id=vet)
            self.other_user = ps.add_partner_user(c, clinic_id=self.other_clinic, email="altra@altra.it", role="titolare")
            c.execute("UPDATE partner_users SET username='altravet',password_hash=? WHERE id=?",
                      (app.password_hash("altra.pass1"), self.other_user))
            self.admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def call(self, method, path, form=None, cookie="", staff=False, headers=None):
        h = FakeHandler(path, cookie, form, headers)
        pp.dispatch(h, method, urlparse(path).path, db=app.db, password_ok=app.password_ok,
                    staff_user=lambda: self.admin if staff else None, db_path=app.DB_PATH)
        return h

    def login(self, username="villadeipini", password="pini.vet26", staff=True):
        h = self.call("POST", "/partner/accedi", {"username": username, "password": password}, staff=staff)
        raw = h.header("Set-Cookie")
        return h, (raw.split(";")[0] if raw else "")

    def new_form(self, **overrides):
        form = {
            "token": f"t-{len(overrides)}-{id(overrides)}", "mode": "ritiro_clinica", "service_type": "Cremazione singola",
            "owner_first_name": "Luca", "owner_last_name": "Verdi", "owner_phone": "333 1234567",
            "species": "Cane", "animal_name": "Birba", "weight": "12 kg",
            "proposed_date": tomorrow(), "fascia": "mattina", "notes": "",
        }
        form.update(overrides)
        return form

    def create(self, cookie, **overrides):
        h = self.call("POST", "/partner/nuova", self.new_form(**overrides), cookie)
        return h

    def request_id(self, h):
        return int(h.header("Location").split("/partner/richieste/")[1].split("?")[0])


class PortalAccessTests(PortalBase):
    def test_every_page_requires_a_session(self):
        for method, path in (("GET", "/partner"), ("GET", "/partner/nuova"), ("GET", "/partner/info"),
                             ("GET", "/partner/richieste/1"), ("POST", "/partner/nuova"),
                             ("POST", "/partner/congelatore"), ("POST", "/partner/richieste/1/annulla")):
            with self.subTest(path=path):
                h = self.call(method, path)
                self.assertEqual((h.status, h.header("Location")), (303, "/partner/accedi"))
        h = self.call("GET", "/partner/v1/eventi")
        self.assertEqual(h.status, 401)
        # il login e il manifest sono pubblici
        self.assertEqual(self.call("GET", "/partner/accedi").status, 200)
        self.assertEqual(self.call("GET", "/partner/manifest.json").status, 200)

    def test_staff_session_does_not_grant_portal_access(self):
        h = self.call("GET", "/partner", staff=True)
        self.assertEqual(h.header("Location"), "/partner/accedi")

    def test_login_page_shows_demo_hint_and_banner_only_to_staff(self):
        staff_page = self.call("GET", "/partner/accedi", staff=True).html
        self.assertIn("villadeipini", staff_page)
        self.assertIn("pini.vet26", staff_page)
        self.assertIn("Anteprima staff", staff_page)
        self.assertIn("Gestionale</a>", staff_page)
        public_page = self.call("GET", "/partner/accedi").html
        self.assertNotIn("pini.vet26", public_page)
        self.assertNotIn("Anteprima staff", public_page)
        self.assertIn("Portale Veterinari", public_page)
        self.assertIn('name="password"', public_page)

    def test_demo_login_works_only_from_a_staff_browser(self):
        h, cookie = self.login(staff=True)
        self.assertEqual((h.status, h.header("Location")), (303, "/partner"))
        raw = h.header("Set-Cookie")
        for flag in ("HttpOnly", "SameSite=Lax", "Path=/partner"):
            self.assertIn(flag, raw)
        self.assertIn(f"Max-Age={ps.DEMO_SESSION_HOURS * 3600}", raw)
        home = self.call("GET", "/partner", cookie=cookie, staff=True)
        self.assertEqual(home.status, 200)
        self.assertIn("Giulia Ferretti", home.html)
        self.assertRegex(home.html, r"Buongiorno|Buon pomeriggio|Buonasera")
        # senza sessione staff le stesse credenziali non funzionano (e il messaggio e' generico)
        h2, cookie2 = self.login(staff=False)
        self.assertEqual(cookie2, "")
        self.assertIn("Credenziali non valide", h2.html)
        self.assertNotIn("pini.vet26", h2.html)  # nessun suggerimento a chi non e' staff

    def test_wrong_credentials_and_secure_cookie_behind_https(self):
        for user, pwd in (("villadeipini", "sbagliata"), ("nonesiste", "pini.vet26"), ("", ""), ("villadeipini", "")):
            with self.subTest(user=user):
                h, cookie = self.login(user, pwd)
                self.assertEqual(cookie, "")
                self.assertIn("Credenziali non valide", h.html)
        h = self.call("POST", "/partner/accedi", {"username": "altravet", "password": "altra.pass1"},
                      headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", h.header("Set-Cookie"))

    def test_non_demo_clinic_user_logs_in_without_staff_session_with_long_session(self):
        h, cookie = self.login("altravet", "altra.pass1", staff=False)
        self.assertEqual(h.status, 303)
        self.assertIn(f"Max-Age={ps.SESSION_DAYS * 86400}", h.header("Set-Cookie"))
        self.assertEqual(self.call("GET", "/partner", cookie=cookie).status, 200)
        # username non sensibile alle maiuscole
        h2, cookie2 = self.login("AltraVet", "altra.pass1", staff=False)
        self.assertTrue(cookie2)

    def test_login_is_blocked_after_too_many_failures_even_with_the_right_password(self):
        for _ in range(ps.MAX_FAILED_PER_USERNAME):
            self.login("altravet", "no", staff=False)
        h, cookie = self.login("altravet", "altra.pass1", staff=False)
        self.assertEqual(cookie, "")
        self.assertIn("Troppi tentativi", h.html)
        # un altro utente non e' bloccato
        h2, cookie2 = self.login("villadeipini", "pini.vet26", staff=True)
        self.assertTrue(cookie2)

    def test_logout_session_expiry_and_deactivation(self):
        _, cookie = self.login()
        out = self.call("POST", "/partner/esci", cookie=cookie)
        self.assertEqual(out.header("Location"), "/partner/accedi")
        self.assertIn("Max-Age=0", out.header("Set-Cookie"))
        self.assertEqual(self.call("GET", "/partner", cookie=cookie).header("Location"), "/partner/accedi")
        # sessione scaduta
        _, cookie = self.login()
        with app.db() as c:
            c.execute("UPDATE partner_sessions SET expires_at='2000-01-01T00:00:00Z'")
        self.assertEqual(self.call("GET", "/partner", cookie=cookie).header("Location"), "/partner/accedi")
        # utente disattivato / clinica disattivata
        _, cookie = self.login()
        with app.db() as c:
            ps.set_partner_user_active(c, self.demo_user, False)
        self.assertEqual(self.call("GET", "/partner", cookie=cookie).header("Location"), "/partner/accedi")
        with app.db() as c:
            ps.set_partner_user_active(c, self.demo_user, True)
        _, cookie = self.login()
        with app.db() as c:
            ps.update_clinic(c, self.demo_clinic, active=False)
        self.assertEqual(self.call("GET", "/partner", cookie=cookie).header("Location"), "/partner/accedi")

    def test_session_tokens_are_stored_hashed(self):
        _, cookie = self.login()
        token = cookie.split("=", 1)[1]
        with app.db() as c:
            hashes = [r[0] for r in c.execute("SELECT token_hash FROM partner_sessions")]
        self.assertNotIn(token, hashes)
        self.assertIn(ps._hash_token(token), hashes)

    def test_manifest_is_a_separate_installable_app(self):
        h = self.call("GET", "/partner/manifest.json")
        manifest = json.loads(h.wfile.getvalue())
        self.assertEqual((manifest["start_url"], manifest["scope"], manifest["display"]), ("/partner", "/partner", "standalone"))
        self.assertEqual(manifest["short_name"], "PP Partners")

    def test_unknown_partner_page_is_404_and_trailing_slash_is_ok(self):
        _, cookie = self.login()
        self.assertEqual(self.call("GET", "/partner/boh", cookie=cookie).status, 404)
        self.assertEqual(self.call("GET", "/partner/", cookie=cookie).status, 200)


class PortalRequestFlowTests(PortalBase):
    def setUp(self):
        super().setUp()
        _, self.cookie = self.login()

    def test_new_request_page_has_all_required_fields_and_options(self):
        page = self.call("GET", "/partner/nuova", cookie=self.cookie).html
        for text in ("Ritiro in clinica", "Ritiro a domicilio", "Cliente in sede", "Cremazione singola",
                     "Cremazione collettiva", "Nome", "Cognome", "Telefono", "Specie", "Peso o taglia",
                     "Mattina 9-13", "Pomeriggio 14-18", "Orario preciso", "Urgente", "nel congelatore",
                     "Usa un buono", "Indirizzo del ritiro", "Sede", 'name="token"', "Domani", "Oggi", "Note"):
            self.assertIn(text, page)
        # un'altra clinica (senza congelatore ne' buoni) non vede quelle opzioni
        _, other = self.login("altravet", "altra.pass1", staff=False)
        other_page = self.call("GET", "/partner/nuova", cookie=other).html
        self.assertNotIn("congelatore", other_page)
        self.assertNotIn("Usa un buono", other_page)

    def test_single_request_lands_on_the_real_calendar_and_shows_in_the_list(self):
        h = self.create(self.cookie, token="a1")
        self.assertEqual(h.status, 303)
        rid = self.request_id(h)
        self.assertIn("nuova=1", h.header("Location"))
        with app.db() as c:
            req = c.execute("SELECT * FROM partner_requests WHERE id=?", (rid,)).fetchone()
            ev = c.execute("SELECT * FROM calendar_events WHERE id=?", (req["calendar_event_id"],)).fetchone()
            animal = c.execute("SELECT * FROM calendar_event_animals WHERE event_id=?", (ev["id"],)).fetchone()
            note = c.execute("SELECT COUNT(*) FROM notifications WHERE type='partner_request_created' AND user_id=?",
                             (self.admin["id"],)).fetchone()[0]
        self.assertEqual((ev["event_type"], ev["event_status"]), ("Ritiro", "Da confermare"))
        self.assertTrue(ev["title"].startswith("PORTALE (PROVA)"))  # chiaramente distinguibile dalle richieste vere
        self.assertEqual((ev["start_at"], ev["end_at"]), (f"{tomorrow()}T09:00:00", f"{tomorrow()}T13:00:59"))
        self.assertEqual((animal["weight"], animal["species"], animal["name"]), ("12", "Cane", "Birba"))  # niente "12 kg kg"
        self.assertEqual(note, 1)
        home = self.call("GET", "/partner", cookie=self.cookie).html
        self.assertIn("Birba", home)
        self.assertIn("Richiesta ricevuta", home)
        self.assertIn(req["request_code"], home)
        detail = self.call("GET", f"/partner/richieste/{rid}?nuova=1", cookie=self.cookie).html
        self.assertIn("Richiesta inviata", detail)
        self.assertIn(req["request_code"], detail)
        self.assertIn("Cronologia", detail)
        self.assertIn("Annulla richiesta", detail)

    def test_taglia_in_words_goes_to_the_animal_notes(self):
        rid = self.request_id(self.create(self.cookie, token="a2", weight="media"))
        with app.db() as c:
            animal = c.execute("""SELECT a.* FROM calendar_event_animals a JOIN partner_requests r ON r.calendar_event_id=a.event_id
                                  WHERE r.id=?""", (rid,)).fetchone()
        self.assertEqual((animal["weight"], animal["notes"]), ("", "Taglia: media"))

    def test_three_modes_through_the_portal(self):
        home = self.request_id(self.create(self.cookie, token="m1", mode="ritiro_domicilio", pickup_address="Via Roma 5, Livorno"))
        site = self.request_id(self.create(self.cookie, token="m2", mode="invio_in_sede", destination_site="Empoli"))
        with app.db() as c:
            e_home = c.execute("SELECT * FROM calendar_events WHERE id=(SELECT calendar_event_id FROM partner_requests WHERE id=?)", (home,)).fetchone()
            e_site = c.execute("SELECT * FROM calendar_events WHERE id=(SELECT calendar_event_id FROM partner_requests WHERE id=?)", (site,)).fetchone()
        self.assertEqual((e_home["event_type"], e_home["location_type"], e_home["address"]), ("Ritiro", "Privato", "Via Roma 5, Livorno"))
        self.assertEqual((e_site["event_type"], e_site["destination_site"]), ("Ritiro in sede", "Empoli"))
        page = self.call("GET", f"/partner/richieste/{site}", cookie=self.cookie).html
        self.assertIn("Presenta questo codice in sede", page)
        self.assertIn("Cliente in sede", self.call("GET", "/partner", cookie=self.cookie).html)
        self.assertNotIn("Presenta questo codice", self.call("GET", f"/partner/richieste/{home}", cookie=self.cookie).html)

    def test_time_windows_urgent_and_validation_errors(self):
        rid = self.request_id(self.create(self.cookie, token="w1", fascia="pomeriggio"))
        with app.db() as c:
            r = c.execute("SELECT * FROM partner_requests WHERE id=?", (rid,)).fetchone()
        self.assertEqual((r["proposed_from"], r["proposed_to"]), ("14:00", "18:00"))
        rid = self.request_id(self.create(self.cookie, token="w2", fascia="preciso", proposed_from="10:30", proposed_to="11:30"))
        with app.db() as c:
            r = c.execute("SELECT * FROM partner_requests WHERE id=?", (rid,)).fetchone()
        self.assertEqual((r["proposed_from"], r["proposed_to"]), ("10:30", "11:30"))
        # urgente senza data: oggi, il prima possibile (nessuna fascia gia' trascorsa)
        rid = self.request_id(self.create(self.cookie, token="w3", urgent="1", proposed_date=""))
        with app.db() as c:
            r = c.execute("SELECT * FROM partner_requests WHERE id=?", (rid,)).fetchone()
            ev = c.execute("SELECT * FROM calendar_events WHERE id=?", (r["calendar_event_id"],)).fetchone()
            urgent_notes = c.execute("SELECT COUNT(*) FROM notifications WHERE type='partner_request_urgent' AND user_id=?",
                                     (self.admin["id"],)).fetchone()[0]
        self.assertEqual((r["urgent"], r["proposed_date"], r["proposed_from"]), (1, ps._rome_today().isoformat(), ""))
        self.assertEqual(ev["all_day"], 1)
        self.assertEqual(urgent_notes, 1)
        self.assertIn("URGENTE", self.call("GET", "/partner", cookie=self.cookie).html)
        # errori: il modulo si riapre con i dati gia' scritti
        h = self.create(self.cookie, token="w4", fascia="preciso")
        self.assertEqual(h.status, 200)
        self.assertIn("orario preciso", h.html)
        self.assertIn('value="Luca"', h.html)
        h = self.create(self.cookie, token="w5", owner_phone="12")
        self.assertIn("telefono", h.html)
        self.assertIn('value="Birba"', h.html)
        h = self.create(self.cookie, token="w6", species="")
        self.assertIn("specie", h.html)
        h = self.create(self.cookie, token="w7", proposed_date="2020-01-01")
        self.assertIn("passato", h.html)
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0], 3)

    def test_double_submit_creates_one_request(self):
        first = self.create(self.cookie, token="dup")
        second = self.create(self.cookie, token="dup")
        self.assertEqual(self.request_id(first), self.request_id(second))
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0], 1)

    def test_user_text_is_escaped(self):
        rid = self.request_id(self.create(self.cookie, token="xss", animal_name="<script>alert(1)</script>",
                                          notes="<img src=x onerror=alert(2)>"))
        page = self.call("GET", f"/partner/richieste/{rid}", cookie=self.cookie).html
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert(1)", self.call("GET", "/partner", cookie=self.cookie).html)

    def test_cancel_from_the_portal_only_while_received(self):
        rid = self.request_id(self.create(self.cookie, token="c1"))
        h = self.call("POST", f"/partner/richieste/{rid}/annulla", cookie=self.cookie)
        self.assertIn("Richiesta annullata", h.header("Location").replace("%20", " "))
        with app.db() as c:
            ev = c.execute("SELECT event_status FROM calendar_events WHERE id=(SELECT calendar_event_id FROM partner_requests WHERE id=?)",
                           (rid,)).fetchone()
        self.assertEqual(ev["event_status"], "Annullato")
        self.assertIn("Annullata", self.call("GET", f"/partner/richieste/{rid}", cookie=self.cookie).html)
        # gia' programmata dallo staff: niente annullo dal portale, nessun pulsante
        rid2 = self.request_id(self.create(self.cookie, token="c2"))
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=(SELECT calendar_event_id FROM partner_requests WHERE id=?)", (rid2,))
        detail = self.call("GET", f"/partner/richieste/{rid2}", cookie=self.cookie).html
        self.assertNotIn("Annulla richiesta", detail)
        self.assertIn("contatta Pet Paradise", detail)
        h = self.call("POST", f"/partner/richieste/{rid2}/annulla", cookie=self.cookie)
        self.assertIn("err=", h.header("Location"))
        with app.db() as c:
            self.assertIsNone(c.execute("SELECT cancelled_at FROM partner_requests WHERE id=?", (rid2,)).fetchone()["cancelled_at"])

    def test_freezer_flow_from_request_to_pickup_request(self):
        coll = dict(token="f1", service_type="Cremazione collettiva", freezer="1", proposed_date="", species="Gatto",
                    animal_name="Micio", weight="4")
        rid = self.request_id(self.create(self.cookie, **coll))
        with app.db() as c:
            self.assertIsNone(c.execute("SELECT calendar_event_id FROM partner_requests WHERE id=?", (rid,)).fetchone()[0])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0], 0)
        home = self.call("GET", "/partner", cookie=self.cookie).html
        self.assertIn("1 animale in congelatore", home)
        self.assertIn("In congelatore", home)
        self.assertIn("Richiedi il ritiro", home)
        detail = self.call("GET", f"/partner/richieste/{rid}", cookie=self.cookie).html
        self.assertIn("nessuna fretta", detail)
        self.assertIn("congelatore", detail)
        # congelatore pieno: la clinica chiede il ritiro con una fascia
        h = self.call("POST", "/partner/congelatore", {"proposed_date": tomorrow(), "fascia": "pomeriggio"}, self.cookie)
        self.assertIn("ok=", h.header("Location"))
        with app.db() as c:
            ev = c.execute("SELECT * FROM calendar_events").fetchone()
            row = ps.get_request(c, self.demo_clinic, rid)
        self.assertEqual((ev["event_status"], ev["start_at"]), ("Da confermare", f"{tomorrow()}T14:00:00"))
        self.assertEqual(row["public_status"], "ricevuta")
        self.assertNotIn("1 animale in congelatore", self.call("GET", "/partner", cookie=self.cookie).html)
        # nulla da ritirare / altra clinica senza congelatore
        h = self.call("POST", "/partner/congelatore", {"proposed_date": tomorrow(), "fascia": "mattina"}, self.cookie)
        self.assertIn("err=", h.header("Location"))
        _, other = self.login("altravet", "altra.pass1", staff=False)
        h = self.call("POST", "/partner/congelatore", {"proposed_date": tomorrow(), "fascia": "mattina"}, other)
        self.assertIn("err=", h.header("Location"))
        # il congelatore non e' accettato fuori dalle regole
        h = self.create(self.cookie, token="f2", service_type="Cremazione singola", freezer="1")
        self.assertEqual(self.call("GET", "/partner", cookie=self.cookie).status, 200)
        with app.db() as c:
            self.assertEqual(c.execute("SELECT freezer FROM partner_requests WHERE client_request_id='f2'").fetchone()[0], 0)

    def test_voucher_flow_balance_and_reservation(self):
        home = self.call("GET", "/partner", cookie=self.cookie).html
        self.assertIn("Buoni", home)
        self.assertRegex(home, r"<b>2</b><span>Buoni disponibili")
        rid = self.request_id(self.create(self.cookie, token="v1", service_type="Cremazione collettiva", use_voucher="1"))
        self.assertRegex(self.call("GET", "/partner", cookie=self.cookie).html, r"<b>1</b><span>Buoni disponibili")
        self.assertIn("Usa un buono", self.call("GET", f"/partner/richieste/{rid}", cookie=self.cookie).html)
        self.create(self.cookie, token="v2", service_type="Cremazione collettiva", use_voucher="1")
        self.assertNotIn("Usa un buono", self.call("GET", "/partner/nuova", cookie=self.cookie).html)  # esauriti
        h = self.create(self.cookie, token="v3", service_type="Cremazione collettiva", use_voucher="1")
        self.assertEqual(h.status, 200)  # richiesta rifiutata, modulo riaperto
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0], 2)
        # annullando si libera il buono e il saldo risale
        self.call("POST", f"/partner/richieste/{rid}/annulla", cookie=self.cookie)
        self.assertRegex(self.call("GET", "/partner", cookie=self.cookie).html, r"<b>1</b><span>Buoni disponibili")
        self.assertIn("1</b> disponibile", self.call("GET", "/partner/account", cookie=self.cookie).html)

    def test_info_page_lists_branches_and_contact(self):
        page = self.call("GET", "/partner/info", cookie=self.cookie).html
        self.assertIn("Livorno", page)
        self.assertIn("Empoli", page)
        self.assertIn(pp.CONTACT_EMAIL, page)
        account = self.call("GET", "/partner/account", cookie=self.cookie).html
        self.assertIn("Esci", account)
        self.assertIn("segreteria@villadeipini.example", account)


class PortalIsolationAndLiveTests(PortalBase):
    def test_clinics_never_see_or_touch_each_others_requests(self):
        _, demo = self.login()
        _, other = self.login("altravet", "altra.pass1", staff=False)
        rid = self.request_id(self.create(demo, token="i1"))
        self.assertEqual(self.call("GET", f"/partner/richieste/{rid}", cookie=other).status, 404)
        self.assertNotIn("Birba", self.call("GET", "/partner", cookie=other).html)
        h = self.call("POST", f"/partner/richieste/{rid}/annulla", cookie=other)
        self.assertIn("err=", h.header("Location"))
        with app.db() as c:
            self.assertIsNone(c.execute("SELECT cancelled_at FROM partner_requests WHERE id=?", (rid,)).fetchone()["cancelled_at"])
        self.assertEqual(self.call("GET", "/partner/v1/eventi?dopo=0", cookie=other).json["events"], [])
        self.assertEqual(len(self.call("GET", "/partner/v1/eventi?dopo=0", cookie=demo).json["events"]), 1)
        # una richiesta della seconda clinica e' visibile solo a lei
        rid2 = self.request_id(self.create(other, token="i2"))
        self.assertEqual(self.call("GET", f"/partner/richieste/{rid2}", cookie=demo).status, 404)
        self.assertEqual(self.call("GET", f"/partner/richieste/{rid2}", cookie=other).status, 200)
        self.assertNotIn("PROVA", self.call("GET", "/partner/info", cookie=other).html.replace("Pet Paradise", ""))

    def test_events_api_cursor_and_labels(self):
        _, cookie = self.login()
        rid = self.request_id(self.create(cookie, token="e1"))
        first = self.call("GET", "/partner/v1/eventi?dopo=0", cookie=cookie).json
        self.assertEqual([e["public_status"] for e in first["events"]], ["ricevuta"])
        self.assertEqual(first["events"][0]["label"], "Richiesta ricevuta")
        cursor = first["cursor"]
        self.assertEqual(self.call("GET", f"/partner/v1/eventi?dopo={cursor}", cookie=cookie).json,
                         {"cursor": cursor, "events": []})
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=(SELECT calendar_event_id FROM partner_requests WHERE id=?)", (rid,))
        new = self.call("GET", f"/partner/v1/eventi?dopo={cursor}", cookie=cookie).json
        self.assertEqual([e["label"] for e in new["events"]], ["Ritiro programmato"])
        self.assertGreater(new["cursor"], cursor)

    def test_staff_changes_show_up_in_the_portal_timeline_and_page(self):
        _, cookie = self.login()
        rid = self.request_id(self.create(cookie, token="s1"))
        with app.db() as c:
            eid = c.execute("SELECT calendar_event_id FROM partner_requests WHERE id=?", (rid,)).fetchone()[0]
            c.execute("UPDATE calendar_events SET event_status='Da ritirare' WHERE id=?", (eid,))
        page = self.call("GET", f"/partner/richieste/{rid}", cookie=cookie).html
        self.assertIn("Ritiro programmato", page)
        self.assertIn("Ritiro confermato", page)
        with app.db() as c:
            c.execute("UPDATE calendar_events SET start_at=?,end_at=? WHERE id=?", (f"{tomorrow()}T15:00:00", f"{tomorrow()}T16:00:59", eid))
        page = self.call("GET", f"/partner/richieste/{rid}", cookie=cookie).html
        self.assertIn("Ritiro riprogrammato", page)
        self.assertIn("15:00-16:00", page)
        with app.db() as c:
            c.execute("UPDATE calendar_events SET event_status='Ritirato' WHERE id=?", (eid,))
        self.assertIn(">Ritirato<", self.call("GET", f"/partner/richieste/{rid}", cookie=cookie).html)
        # il pulsante di annullo sparisce, e la home la mette tra le richieste in corso
        self.assertNotIn("Annulla richiesta", self.call("GET", f"/partner/richieste/{rid}", cookie=cookie).html)

    def test_page_polls_for_changes_every_20_seconds(self):
        _, cookie = self.login()
        self.create(cookie, token="p1")
        page = self.call("GET", "/partner", cookie=cookie).html
        self.assertIn("data-live", page)
        self.assertIn("/partner/v1/eventi", page)
        self.assertIn("setInterval(poll,20000)", page)
        self.assertIn('data-cursor="1"', page)


class QuotePortalTests(PortalBase):
    def setUp(self):
        super().setUp()
        _, self.cookie = self.login()

    def quote(self, query="", cookie=None, frag=False):
        suffix = ("&" if query else "") + "frag=1" if frag else ""
        return self.call("GET", f"/partner/preventivo?{query}{suffix}" if (query or frag) else "/partner/preventivo",
                         cookie=self.cookie if cookie is None else cookie)

    def total_of(self, html):
        import re
        match = re.search(r'class="qtotal">([^<]+)<', html)
        return match.group(1) if match else None

    def test_requires_a_portal_session(self):
        for path in ("/partner/preventivo", "/partner/preventivo?peso=5&frag=1"):
            h = self.call("GET", path)
            self.assertEqual((h.status, h.header("Location")), (303, "/partner/accedi"))

    def test_empty_page_has_the_form_and_a_prompt(self):
        page = self.quote().html
        self.assertIn("Inserisci il peso", page)
        for name in ('name="peso"', 'name="ritiro"', 'name="luogo"', 'name="comune"', 'name="riconsegna"',
                     'name="comune_riconsegna"', 'name="data"', 'name="ora"'):
            self.assertIn(name, page)
        for text in ("Ambulatorio / clinica", "Domicilio, piano terra", "Domicilio, piano con ascensore",
                     "Domicilio, 1° o 2° piano senza ascensore", "Domicilio, 3° piano o oltre senza ascensore",
                     "Stesso comune del ritiro", "Altro comune (fuori circondario)", "Circondario Empolese Valdelsa",
                     "Montelupo Fiorentino", 'optgroup label="Livorno"', "Serve il ritiro", "Serve la riconsegna"):
            self.assertIn(text, page)
        self.assertRegex(page, r'name="ritiro" value="1" checked')  # prima apertura: ritiro attivo
        self.assertIn("Preventivo indicativo: il prezzo definitivo", page)

    def test_navigation_has_the_quote_entry_on_phone_and_desktop(self):
        page = self.quote().html
        self.assertEqual(page.count('href="/partner/preventivo"'), 2)  # barra in alto (desktop) + barra in basso (telefono)
        home = self.call("GET", "/partner", cookie=self.cookie).html
        self.assertEqual(home.count('href="/partner/preventivo"'), 2)

    def test_agreed_examples_through_the_page(self):
        cases = [
            ("peso=22&ritiro=1&luogo=piano12&comune=Livorno", "€ 380,00"),
            ("peso=22&ritiro=1&luogo=piano12&comune=__altro__", "€ 390,00"),
            ("peso=4&ritiro=1&luogo=ambulatorio&comune=Empoli", "€ 260,00"),
            ("peso=22&ritiro=1&luogo=piano12&comune=__altro__&riconsegna=1&comune_riconsegna=__altro__", "€ 440,00"),
            ("peso=22&ritiro=1&luogo=piano12&comune=Livorno&riconsegna=1&comune_riconsegna=__stesso__", "€ 420,00"),
            ("peso=12,5", "€ 270,00"),
            ("peso=80&riconsegna=1&comune_riconsegna=Vinci", "€ 660,00"),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(self.total_of(self.quote(query).html), expected)

    def test_result_shows_breakdown_urn_note_and_vat(self):
        page = self.quote("peso=22&ritiro=1&luogo=piano12&comune=__altro__&riconsegna=1&comune_riconsegna=Livorno").html
        for text in ("Preventivo indicativo", "IVA inclusa · urna esclusa", "Cremazione singola", "20,1-25 kg",
                     "Ritiro: Domicilio, 1° o 2° piano senza ascensore", "10,1-30 kg", "Ritiro fuori circondario",
                     "Riconsegna", "nel circondario", "€ 310,00", "€ 70,00", "€ 10,00", "€ 40,00", "Totale",
                     "L&#x27;urna non è inclusa nel preventivo", "€ 20,00", "modello standard"):
            self.assertIn(text, page)

    def test_fragment_mode_returns_only_the_result_blocks(self):
        h = self.quote("peso=22&ritiro=1&luogo=terra&comune=Livorno", frag=True)
        self.assertEqual(h.status, 200)
        self.assertNotIn("<html", h.html)
        self.assertIn("qtotal", h.html)
        self.assertEqual(self.total_of(h.html), "€ 370,00")
        empty = self.quote("", frag=True)
        self.assertIn("Inserisci il peso", empty.html)
        self.assertNotIn("<form", empty.html)

    def test_incomplete_or_invalid_input_shows_a_friendly_message(self):
        cases = [
            ("peso=22&ritiro=1&comune=Livorno", "Seleziona il luogo del ritiro"),
            ("peso=22&ritiro=1&luogo=terra", "Seleziona il comune"),
            ("peso=22&ritiro=1", "Seleziona il luogo del ritiro"),
            ("peso=22&ritiro=1&luogo=terra&comune=Atlantide", "Comune non valido"),
            ("peso=abc", "Peso: numero non valido"),
            ("peso=0", "maggiore di zero"),
            ("peso=999", "non sembra corretto"),
            ("peso=22&riconsegna=1", "Seleziona il comune della riconsegna"),
            ("peso=22&data=31/12/2026", "Data non valida"),
            ("peso=22&data=2026-10-05&ora=25:99", "Ora non valida"),
        ]
        for query, message in cases:
            with self.subTest(query=query):
                h = self.quote(query)
                self.assertEqual(h.status, 200)
                self.assertIn(message, h.html)
                self.assertIsNone(self.total_of(h.html))

    def test_pickup_unchecked_ignores_the_pickup_fields(self):
        h = self.quote("peso=22&luogo=piano3&comune=Atlantide")  # ritiro non spuntato: il cliente porta l'animale
        self.assertEqual(self.total_of(h.html), "€ 310,00")

    def test_supplements_through_the_page(self):
        sunday_night = "peso=5&ritiro=1&luogo=terra&comune=Livorno&data=2026-10-04&ora=22:30"
        h = self.quote(sunday_night)
        self.assertEqual(self.total_of(h.html), "€ 460,00")  # 210 + 50 + 80 festivo + 120 notturno
        self.assertIn("Servizio festivo", h.html)
        self.assertIn("Servizio notturno", h.html)
        evening = self.quote("peso=5&ritiro=1&luogo=terra&comune=Livorno&data=2026-10-05&ora=19:30")
        self.assertEqual(self.total_of(evening.html), "€ 310,00")
        self.assertIn("Servizio serale", evening.html)
        no_time = self.quote("peso=5&ritiro=1&luogo=terra&comune=Livorno&data=2026-10-05")
        self.assertEqual(self.total_of(no_time.html), "€ 260,00")
        self.assertIn("Indica anche l&#x27;ora", no_time.html)
        # senza ritiro ne' riconsegna nessun supplemento
        self.assertEqual(self.total_of(self.quote("peso=5&data=2026-10-04&ora=22:30").html), "€ 210,00")

    def test_changed_price_list_is_reflected(self):
        with app.db() as c:
            pricelist = qs_get(c)
            pricelist["cremation"][4]["price"] = 333
            pricelist["delivery"]["inside"] = 45
            qs_save(c, pricelist)
        self.assertEqual(self.total_of(self.quote("peso=22").html), "€ 333,00")
        self.assertEqual(self.total_of(self.quote("peso=22&riconsegna=1&comune_riconsegna=Livorno").html), "€ 378,00")

    def test_user_input_is_escaped(self):
        h = self.quote("peso=%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertNotIn("<script>alert(1)", h.html)
        page = self.quote("peso=22&comune=%22%3E%3Cimg%20src%3Dx%3E&ritiro=1&luogo=terra").html
        self.assertNotIn('"><img src=x>', page)

    def test_available_to_every_clinic_and_in_the_staff_preview(self):
        _, other = self.login("altravet", "altra.pass1", staff=False)
        self.assertEqual(self.total_of(self.quote("peso=22", cookie=other).html), "€ 310,00")
        staff_page = self.call("GET", "/partner/preventivo?peso=22", cookie=self.cookie, staff=True).html
        self.assertIn("Anteprima staff", staff_page)

    def test_page_updates_live_through_the_fragment_endpoint(self):
        page = self.quote().html
        self.assertIn('id="quoteForm"', page)
        self.assertIn('id="quoteResult"', page)
        self.assertIn("/partner/preventivo?", page)
        self.assertIn("p.set('frag','1')", page)
        self.assertIn("qchip", page)


def qs_get(connection):
    import quote_service
    return quote_service.get_pricelist(connection)


def qs_save(connection, pricelist):
    import quote_service
    return quote_service.save_pricelist(connection, pricelist, updated_by="test", updated_at="2026-10-02T10:00:00")


class RequestFormTests(PortalBase):
    def setUp(self):
        super().setUp()
        _, self.cookie = self.login()

    def form_page(self, **kw):
        return self.call("GET", "/partner/nuova", cookie=self.cookie).html

    def test_required_fields_are_marked_and_the_legend_explains_it(self):
        page = self.form_page()
        self.assertIn("Campo obbligatorio. Proprietario, nome dell&#x27;animale e note sono facoltativi."
                      if "&#x27;" in page else "Campo obbligatorio. Proprietario, nome dell'animale e note sono facoltativi.", page)
        for heading in ("Tipo di servizio", "Tipo di cremazione", "Quando"):
            index = page.index(f"</span>{heading}")
            self.assertIn('class="req"', page[index:index + 140], heading)
        for label in ("<label>Specie", "<label>Peso o taglia", "<label>Fascia oraria", "<label>Sede", "<label>Indirizzo del ritiro"):
            index = page.index(label)
            self.assertIn('class="req"', page[index:index + 140], label)
        for heading in ("Proprietario", "Note"):
            index = page.index(f"</span>{heading} ")
            self.assertIn('<span class="opt">(facoltativ', page[index:index + 80], heading)
        self.assertIn("Nome <span class=\"opt\">(facoltativo)</span>", page)

    def test_required_inputs_have_the_native_required_attribute_and_nothing_is_preselected(self):
        import re
        page = self.form_page()
        for name in ("mode", "service_type"):
            radios = re.findall(rf'<input type="radio" name="{name}"[^>]*>', page)
            self.assertGreaterEqual(len(radios), 3, name)
            for radio in radios:
                self.assertIn(" required", radio)
                self.assertNotIn("checked", radio)  # scelta consapevole, niente valori di default
        for radio in re.findall(r'<input type="radio" name="fascia"[^>]*>', page):
            self.assertNotIn("checked", radio)
        self.assertRegex(page, r'name="species"[^>]*required')
        self.assertRegex(page, r'name="weight"[^>]*required')
        for name in ("owner_first_name", "owner_last_name", "owner_phone", "animal_name", "notes"):
            tag = re.search(rf'<(?:input|textarea)[^>]*name="{name}"[^>]*>', page).group(0)
            self.assertNotIn("required", tag, name)
        self.assertIn("q('[name=\"pickup_address\"]').required=(mode==='ritiro_domicilio')", page)

    def test_sections_start_with_the_required_ones(self):
        page = self.form_page()
        order = [page.index(x) for x in ("</span>Tipo di servizio", "</span>Tipo di cremazione", "</span>Animale",
                                         "</span>Quando", "</span>Proprietario", "</span>Note")]
        self.assertEqual(order, sorted(order))

    def test_cremation_options_and_all_day_chip(self):
        page = self.form_page()
        for value, label in (("Cremazione singola", "Singola"), ("Cremazione collettiva", "Collettiva"),
                             ("Cremazione da decidere", "Da decidere")):
            self.assertIn(f'value="{value}"', page)
            self.assertIn(f"<span>{label}</span>", page)
        for text in ("Mattina 9-13", "Pomeriggio 14-18", "Tutto il giorno", "Orario preciso", 'value="tutto_giorno"',
                     "Scegli \"Da decidere\" se il cliente non ha ancora scelto."):
            self.assertIn(text, page)

    def test_request_without_owner_and_with_decide_later(self):
        h = self.call("POST", "/partner/nuova", {
            "token": "o1", "mode": "ritiro_clinica", "service_type": "Cremazione da decidere", "species": "Gatto",
            "weight": "4", "proposed_date": tomorrow(), "fascia": "mattina"}, self.cookie)
        self.assertEqual(h.status, 303)
        rid = int(h.header("Location").split("/partner/richieste/")[1].split("?")[0])
        detail = self.call("GET", f"/partner/richieste/{rid}", cookie=self.cookie).html
        self.assertNotIn("<dt>Proprietario</dt>", detail)
        self.assertIn("<dt>Cremazione</dt><dd>Da decidere</dd>", detail)
        self.assertIn("<dt>Tipo di servizio</dt>", detail)
        card = self.call("GET", "/partner", cookie=self.cookie).html
        self.assertIn("Ritiro in clinica</div>", card)  # nessun " · " finale senza proprietario
        with app.db() as c:
            req = c.execute("SELECT * FROM partner_requests WHERE id=?", (rid,)).fetchone()
        self.assertEqual((req["owner_first_name"], req["owner_phone"], req["animal_name"]), ("", "", ""))

    def test_missing_required_choices_show_clear_messages_and_keep_the_form(self):
        base = {"token": "m1", "mode": "ritiro_clinica", "service_type": "Cremazione singola", "species": "Cane",
                "weight": "10", "proposed_date": tomorrow(), "fascia": "mattina", "animal_name": "Fido"}
        cases = [({"mode": ""}, "tipo di servizio"), ({"service_type": ""}, "tipo di cremazione"),
                 ({"species": ""}, "specie"), ({"weight": ""}, "peso"), ({"fascia": ""}, "fascia oraria"),
                 ({"proposed_date": ""}, "data")]
        for override, fragment in cases:
            with self.subTest(override=override):
                h = self.call("POST", "/partner/nuova", {**base, **override}, self.cookie)
                self.assertEqual(h.status, 200)
                self.assertIn(fragment, h.html.lower())
                self.assertIn('value="Fido"', h.html)
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0], 0)

    def test_all_day_option_through_the_portal(self):
        h = self.call("POST", "/partner/nuova", {
            "token": "ad1", "mode": "ritiro_clinica", "service_type": "Cremazione singola", "species": "Cane",
            "weight": "10", "proposed_date": tomorrow(), "fascia": "tutto_giorno"}, self.cookie)
        rid = int(h.header("Location").split("/partner/richieste/")[1].split("?")[0])
        with app.db() as c:
            req = c.execute("SELECT * FROM partner_requests WHERE id=?", (rid,)).fetchone()
            ev = c.execute("SELECT * FROM calendar_events WHERE id=?", (req["calendar_event_id"],)).fetchone()
        self.assertEqual((req["proposed_from"], req["proposed_to"]), ("00:00", "23:59"))
        self.assertEqual((ev["all_day"], ev["start_at"][11:16], ev["end_at"][11:16]), (1, "00:00", "23:59"))
        detail = self.call("GET", f"/partner/richieste/{rid}", cookie=self.cookie).html
        self.assertIn("tutto il giorno", detail)
        self.assertNotIn("00:00-23:59", detail)
        self.assertIn("tutto il giorno", self.call("GET", "/partner", cookie=self.cookie).html)

    def test_freezer_pickup_form_has_the_all_day_choice(self):
        self.call("POST", "/partner/nuova", {
            "token": "fz1", "mode": "ritiro_clinica", "service_type": "Cremazione collettiva", "freezer": "1",
            "species": "Gatto", "weight": "4"}, self.cookie)
        page = self.call("GET", "/partner", cookie=self.cookie).html
        self.assertIn('name="fascia" value="tutto_giorno"', page)
        self.call("POST", "/partner/congelatore", {"proposed_date": tomorrow(), "fascia": "tutto_giorno"}, self.cookie)
        with app.db() as c:
            ev = c.execute("SELECT * FROM calendar_events ORDER BY id DESC").fetchone()
        self.assertEqual(ev["all_day"], 1)

    def test_staff_inbox_handles_missing_owner_and_all_day(self):
        self.call("POST", "/partner/nuova", {
            "token": "si1", "mode": "ritiro_clinica", "service_type": "Cremazione da decidere", "species": "Cane",
            "weight": "10", "proposed_date": tomorrow(), "fascia": "tutto_giorno"}, self.cookie)
        handler = object.__new__(app.App)
        handler.headers = {}
        handler.path = "/richieste-portale"
        with app.db() as c:
            c.execute("UPDATE users SET must_change_password=0")
            admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        handler.user = lambda: admin
        pages = []
        handler.send_html = lambda content, status=200: pages.append(content)
        handler.redirect = lambda url: pages.append("REDIRECT " + url)
        handler._route_get()
        self.assertIn("Proprietario non indicato", pages[-1])
        self.assertIn("tutto il giorno", pages[-1])
        self.assertIn("Cremazione da decidere", pages[-1])


class NavigationAndZoomTests(PortalBase):
    def setUp(self):
        super().setUp()
        _, self.cookie = self.login()

    def test_tab_bar_has_the_plus_exactly_in_the_middle(self):
        import re
        html = self.call("GET", "/partner", cookie=self.cookie).html
        bar = html[html.index('<nav class="tabbar"'):html.index("</nav>", html.index('<nav class="tabbar"'))]
        links = re.findall(r'<a href="([^"]+)" class="([^"]*)">', bar)
        self.assertEqual([href for href, _ in links],
                         ["/partner", "/partner/preventivo", "/partner/nuova", "/partner/info", "/partner/account"])
        self.assertEqual(len(links), 5)
        self.assertIn("plus", links[2][1])  # terza di cinque = al centro
        self.assertEqual(sum("plus" in cls for _, cls in links), 1)
        self.assertIn("grid-template-columns:repeat(5,1fr)", pp.CSS)
        self.assertIn(".tabbar a.plus .pbtn", pp.CSS)

    def test_desktop_nav_has_all_sections(self):
        html = self.call("GET", "/partner", cookie=self.cookie).html
        top = html[html.index('<nav class="topnav"'):html.index("</nav>", html.index('<nav class="topnav"'))]
        for href in ("/partner", "/partner/preventivo", "/partner/info", "/partner/account", "/partner/nuova"):
            self.assertIn(f'href="{href}"', top)

    def test_new_request_button_is_visible_on_every_page(self):
        for path in ("/partner", "/partner/preventivo", "/partner/info", "/partner/account", "/partner/nuova"):
            with self.subTest(path=path):
                html = self.call("GET", path, cookie=self.cookie).html
                self.assertIn('class="plus', html)
                self.assertIn('href="/partner/nuova"', html)

    def test_account_page_has_the_account_and_logout_and_vouchers(self):
        page = self.call("GET", "/partner/account", cookie=self.cookie)
        self.assertEqual(page.status, 200)
        for text in ("Il tuo account", "Esci", "segreteria@villadeipini.example", "I tuoi buoni", "2</b>"):
            self.assertIn(text, page.html)
        self.assertEqual(self.call("GET", "/partner/account").header("Location"), "/partner/accedi")
        info = self.call("GET", "/partner/info", cookie=self.cookie).html
        self.assertNotIn("Esci", info.split("</main>")[0])
        self.assertIn(pp.CONTACT_EMAIL, info)

    def test_no_zoom_on_focus_inputs_are_at_least_16px(self):
        import re
        # il vecchio "font:16px inherit" era una dichiarazione non valida: i campi restavano a 13px e iOS zoomava
        self.assertIsNone(re.search(r"font:\s*[\d.]+px[^;}]*\binherit", pp.CSS))
        rule = re.search(r"input,select,textarea\{[^}]*\}", pp.CSS).group(0)
        self.assertIn("font-size:16px", rule)
        self.assertIn("font-family:inherit", rule)
        self.assertIn("input,select,textarea{font-size:max(16px,1em)}", pp.CSS)
        self.assertIn("-webkit-text-size-adjust:100%", pp.CSS)
        self.assertIn("touch-action:manipulation", pp.CSS)
        self.assertIn("font-size:16px;line-height:1;font-family:inherit", pp.CSS)  # anche i pulsanti
        page = self.call("GET", "/partner/nuova", cookie=self.cookie).html
        self.assertIn("width=device-width,initial-scale=1,viewport-fit=cover", page)
        self.assertNotIn("user-scalable=no", page)  # lo zoom manuale resta possibile


class DemoIdentityTests(PortalBase):
    def test_new_demo_clinic_looks_like_a_real_one(self):
        self.assertEqual((ps.DEMO_USERNAME, ps.DEMO_PASSWORD), ("villadeipini", "pini.vet26"))
        with app.db() as c:
            row = c.execute(
                """SELECT v.*, u.email, u.display_name, u.username FROM partner_clinics pc
                   JOIN veterinarians v ON v.id=pc.veterinarian_id JOIN partner_users u ON u.clinic_id=pc.id
                   WHERE pc.is_demo=1""").fetchone()
        self.assertEqual((row["clinic_name"], row["short_name"], row["city"]),
                         ("Clinica Veterinaria Villa dei Pini", "Villa dei Pini", "Livorno"))
        self.assertEqual((row["display_name"], row["address"]), ("Dott.ssa Giulia Ferretti", "Via dei Pini 14"))
        self.assertEqual(row["phone"], "")  # nessun numero che qualcuno possa chiamare per errore
        for text in (row["clinic_name"], row["short_name"], row["display_name"], row["email"], row["username"]):
            self.assertNotRegex(text, r"(?i)prova|demo|test")
        self.assertTrue(row["email"].endswith(".example"))  # dominio riservato: nessuna email reale

    def test_portal_pages_never_say_prova_to_the_vet(self):
        import re
        _, cookie = self.login()
        self.call("POST", "/partner/nuova", {
            "token": "dm1", "mode": "ritiro_clinica", "service_type": "Cremazione singola", "species": "Cane",
            "weight": "12", "proposed_date": tomorrow(), "fascia": "mattina"}, cookie)
        for path in ("/partner", "/partner/nuova", "/partner/preventivo", "/partner/info", "/partner/account",
                     "/partner/richieste/1"):
            with self.subTest(path=path):
                html = self.call("GET", path, cookie=cookie).html
                text = re.sub(r"<style>.*?</style>|<script>.*?</script>", "", html, flags=re.S)
                text = re.sub(r"<[^>]+>", " ", text)
                self.assertNotRegex(text, r"(?i)\bprova\b")
                self.assertIn("Villa dei Pini", html)
        # nel calendario dello staff resta ben riconoscibile come richiesta di dimostrazione
        with app.db() as c:
            title = c.execute("SELECT title FROM calendar_events ORDER BY id DESC").fetchone()["title"]
        self.assertTrue(title.startswith("PORTALE (PROVA)"))

    def test_login_page_shows_the_credentials_and_says_it_is_a_test_account(self):
        staff_page = self.call("GET", "/partner/accedi", staff=True).html
        for text in ("Anteprima staff · account di prova", "villadeipini", "pini.vet26", "Villa dei Pini",
                     "arrivano davvero sul calendario"):
            self.assertIn(text, staff_page)
        public = self.call("GET", "/partner/accedi").html
        for text in ("villadeipini", "pini.vet26", "account di prova"):
            self.assertNotIn(text, public)  # il suggerimento resta visibile solo a chi e' staff

    def test_old_credentials_stop_working_and_new_ones_work(self):
        for user, pwd in (("provavet", "prova.vet1"), ("villadeipini", "prova.vet1"), ("provavet", "pini.vet26")):
            h, cookie = self.login(user, pwd)
            self.assertEqual(cookie, "", (user, pwd))
        _, cookie = self.login("villadeipini", "pini.vet26")
        self.assertTrue(cookie)

    def test_clinic_created_with_the_old_temporary_name_is_upgraded_keeping_its_data(self):
        _, cookie = self.login()
        self.call("POST", "/partner/nuova", {
            "token": "up1", "mode": "ritiro_clinica", "service_type": "Cremazione collettiva", "use_voucher": "1",
            "species": "Cane", "weight": "12", "proposed_date": tomorrow(), "fascia": "mattina"}, cookie)
        old_hash = app.password_hash("prova.vet1")
        with app.db() as c:
            vet_id = c.execute("SELECT veterinarian_id FROM partner_clinics WHERE is_demo=1").fetchone()[0]
            c.execute("UPDATE veterinarians SET clinic_name='PROVA VET - Clinica di prova',short_name='PROVA VET',"
                      "doctor_name='Dott. Prova',address='Via di Prova 1' WHERE id=?", (vet_id,))
            c.execute("UPDATE partner_users SET username='provavet',email='provavet@prova.petparadise.invalid',"
                      "display_name='Dott. Prova',password_hash=? WHERE username='villadeipini'", (old_hash,))
            c.execute("DELETE FROM settings WHERE key='partner_demo_version'")
            requests_before = c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0]
            events_before = c.execute("SELECT COUNT(*) FROM partner_events").fetchone()[0]
            vouchers_before = c.execute("SELECT COUNT(*) FROM veterinarian_vouchers WHERE veterinarian_id=?", (vet_id,)).fetchone()[0]
        h, old_cookie = self.login("provavet", "prova.vet1")
        self.assertTrue(old_cookie)  # prima dell'aggiornamento funziona ancora il vecchio accesso
        with app.db() as c:
            self.assertIsNone(ps.ensure_demo_clinic(c, app.password_hash))
        with app.db() as c:
            vet = c.execute("SELECT * FROM veterinarians WHERE id=?", (vet_id,)).fetchone()
            user = c.execute("SELECT * FROM partner_users WHERE username='villadeipini'").fetchone()
            self.assertEqual((vet["clinic_name"], vet["short_name"], vet["doctor_name"]),
                             ("Clinica Veterinaria Villa dei Pini", "Villa dei Pini", "Dott.ssa Giulia Ferretti"))
            self.assertEqual((user["email"], user["display_name"]), ("segreteria@villadeipini.example", "Dott.ssa Giulia Ferretti"))
            self.assertEqual(c.execute("SELECT value FROM settings WHERE key='partner_demo_version'").fetchone()[0], "2")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_requests").fetchone()[0], requests_before)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_events").fetchone()[0], events_before)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM veterinarian_vouchers WHERE veterinarian_id=?", (vet_id,)).fetchone()[0], vouchers_before)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_clinics WHERE is_demo=1").fetchone()[0], 1)
        # sessioni del vecchio accesso chiuse, vecchie credenziali rifiutate, nuove accettate
        self.assertEqual(self.call("GET", "/partner", cookie=old_cookie).header("Location"), "/partner/accedi")
        self.assertEqual(self.login("provavet", "prova.vet1")[1], "")
        self.assertTrue(self.login("villadeipini", "pini.vet26")[1])
        # idempotente
        with app.db() as c:
            self.assertIsNone(ps.ensure_demo_clinic(c, app.password_hash))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_users WHERE username='villadeipini'").fetchone()[0], 1)

    def test_upgrade_does_nothing_if_the_demo_clinic_was_deleted(self):
        with app.db() as c:
            c.execute("DELETE FROM partner_sessions")
            c.execute("DELETE FROM partner_users WHERE username='villadeipini'")
            c.execute("DELETE FROM partner_clinics WHERE is_demo=1")
            c.execute("DELETE FROM settings WHERE key='partner_demo_version'")
            self.assertIsNone(ps.ensure_demo_clinic(c, app.password_hash))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_clinics WHERE is_demo=1").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT value FROM settings WHERE key='partner_demo_version'").fetchone()[0], "2")


class ThemeTests(PortalBase):
    def theme_of(self, html):
        import re
        return re.search(r'<html lang="it" data-theme="([^"]*)"', html).group(1)

    def post_theme(self, value, cookie="", accept="application/json", headers=None):
        h = FakeHandler("/partner/tema", cookie, {"tema": value}, {"Accept": accept, **(headers or {})})
        pp.dispatch(h, "POST", "/partner/tema", db=app.db, password_ok=app.password_ok,
                    staff_user=lambda: None, db_path=app.DB_PATH)
        return h

    def test_default_is_light_everywhere(self):
        self.assertEqual(self.theme_of(self.call("GET", "/partner/accedi").html), "light")
        _, cookie = self.login()
        for path in ("/partner", "/partner/nuova", "/partner/preventivo", "/partner/info", "/partner/account"):
            self.assertEqual(self.theme_of(self.call("GET", path, cookie=cookie).html), "light", path)

    def test_user_choice_is_saved_on_the_account_and_survives_a_new_login_without_cookies(self):
        _, cookie = self.login()
        h = self.post_theme("dark", cookie)
        self.assertEqual(h.status, 200)
        self.assertEqual(json.loads(h.wfile.getvalue()), {"ok": True, "theme": "dark"})
        with app.db() as c:
            self.assertEqual(c.execute("SELECT theme FROM partner_users WHERE id=?", (self.demo_user,)).fetchone()[0], "dark")
            self.assertEqual(c.execute("SELECT theme FROM partner_users WHERE id=?", (self.other_user,)).fetchone()[0], "")
        for path in ("/partner", "/partner/nuova", "/partner/info", "/partner/account", "/partner/preventivo"):
            self.assertEqual(self.theme_of(self.call("GET", path, cookie=cookie).html), "dark", path)
        # altro dispositivo: nuova sessione, nessun cookie del tema -> il tema segue l'utente
        _, cookie2 = self.login()
        self.assertEqual(self.theme_of(self.call("GET", "/partner", cookie=cookie2).html), "dark")
        # l'altra clinica non e' toccata
        _, other = self.login("altravet", "altra.pass1", staff=False)
        self.assertEqual(self.theme_of(self.call("GET", "/partner", cookie=other).html), "light")

    def test_all_three_choices_and_account_selector(self):
        _, cookie = self.login()
        for choice in ("auto", "dark", "light"):
            self.post_theme(choice, cookie)
            html = self.call("GET", "/partner/account", cookie=cookie).html
            self.assertEqual(self.theme_of(html), choice)
            for key in ("auto", "dark", "light"):
                pressed = "true" if key == choice else "false"
                self.assertIn(f'name="tema" value="{key}" data-theme-choice="{key}" aria-pressed="{pressed}"', html)
        html = self.call("GET", "/partner/account", cookie=cookie).html
        for text in ("Aspetto", "Automatico", "Chiaro", "Scuro", "segue le impostazioni del tuo telefono"):
            self.assertIn(text, html)

    def test_invalid_values_are_rejected_and_change_nothing(self):
        _, cookie = self.login()
        for bad in ("", "nero", "<script>", "DARK", "dark;x"):
            h = self.post_theme(bad, cookie)
            self.assertEqual(h.status, 400, bad)
        with app.db() as c:
            self.assertEqual(c.execute("SELECT theme FROM partner_users WHERE id=?", (self.demo_user,)).fetchone()[0], "")
        with self.assertRaises(ps.PartnerError):
            with app.db() as c:
                ps.set_user_theme(c, self.demo_user, "viola")

    def test_cookie_covers_the_login_page_and_has_safe_flags(self):
        h = self.post_theme("dark")  # senza sessione
        raw = h.header("Set-Cookie")
        for flag in ("pp_theme=dark", "SameSite=Lax", "Path=/partner", "Max-Age=31536000"):
            self.assertIn(flag, raw)
        self.assertNotIn("HttpOnly", raw)  # lo legge anche la pagina
        self.assertIn("Secure", self.post_theme("dark", headers={"X-Forwarded-Proto": "https"}).header("Set-Cookie"))
        self.assertEqual(self.theme_of(self.call("GET", "/partner/accedi", cookie="pp_theme=dark").html), "dark")
        self.assertEqual(self.theme_of(self.call("GET", "/partner/accedi", cookie="pp_theme=auto").html), "auto")
        self.assertEqual(self.theme_of(self.call("GET", "/partner/accedi", cookie="pp_theme=viola").html), "light")
        with app.db() as c:  # senza sessione non si scrive nulla nel database
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_users WHERE theme<>''").fetchone()[0], 0)

    def test_saved_choice_wins_over_the_cookie(self):
        _, cookie = self.login()
        self.post_theme("light", cookie)
        both = f"{cookie}; pp_theme=dark"
        self.assertEqual(self.theme_of(self.call("GET", "/partner", cookie=both).html), "light")

    def test_form_without_javascript_redirects_back(self):
        _, cookie = self.login()
        h = self.post_theme("dark", cookie, accept="text/html")
        self.assertEqual((h.status, h.header("Location")), (303, "/partner/account"))
        self.assertIn("pp_theme=dark", h.header("Set-Cookie"))
        anonymous = self.post_theme("dark", accept="text/html")
        self.assertEqual(anonymous.header("Location"), "/partner/accedi")

    def test_toggle_buttons_and_client_script(self):
        _, cookie = self.login()
        home = self.call("GET", "/partner", cookie=cookie).html
        self.assertIn('class="themebtn" data-theme-toggle', home)
        login = self.call("GET", "/partner/accedi").html
        self.assertIn('class="themebtn float" data-theme-toggle', login)
        for text in ("fetch('/partner/tema'", "prefers-color-scheme: dark", "data-theme-choice", "data-theme-toggle"):
            self.assertIn(text, home)

    def test_dark_palette_exists_for_dark_and_for_automatic_mode(self):
        import re
        css = pp.CSS
        self.assertIn(':root[data-theme="dark"]{--brand:', css)
        self.assertIn('@media(prefers-color-scheme:dark){\n:root[data-theme="auto"]{--brand:', css)
        self.assertEqual(css.count(";color-scheme:dark}"), 2)
        for selector in (".st-wait", ".st-done", ".tag.urgent", ".flash.err", ".hint", ".ticket", ".code", ".prog.ice"):
            self.assertIn(f':root[data-theme="dark"] {selector}{{', css)
            self.assertIn(f':root[data-theme="auto"] {selector}{{', css)
        # i componenti principali usano variabili: nessun bianco fisso che resterebbe chiaro nel tema scuro
        for rule in (r"\.chip span,\.pill\{[^}]*\}", r"\.mode>div\{[^}]*\}", r"\.check\{[^}]*\}", r"input,select,textarea\{width[^}]*\}",
                     r"\.tabbar\{[^}]*\}", r"\.top\{[^}]*\}", r"\.btn\.ghost\{[^}]*\}"):
            block = re.search(rule, css).group(0)
            self.assertNotRegex(block, r"background:#(?:fff|fcfbfa)\b", rule)
        self.assertIn(".hero .btn.light{background:#fff}", css)  # il pulsante bianco della home resta bianco

    def test_no_inline_light_only_colors_left_in_pages(self):
        _, cookie = self.login()
        for path in ("/partner", "/partner/account", "/partner/preventivo?peso=10"):
            html = self.call("GET", path, cookie=cookie).html
            self.assertNotIn('style="background:#e3f2fc', html)
            self.assertNotIn('style="background:#fdf0d3', html)

    def test_schema_upgrade_adds_the_theme_column(self):
        with app.db() as c:
            c.execute("ALTER TABLE partner_users DROP COLUMN theme")
        app.init_db()
        with app.db() as c:
            self.assertIn("theme", {r[1] for r in c.execute("PRAGMA table_info(partner_users)")})
            self.assertEqual(c.execute("SELECT theme FROM partner_users LIMIT 1").fetchone()[0], "")


class InstallTests(PortalBase):
    def setUp(self):
        super().setUp()
        _, self.cookie = self.login()

    def test_account_page_explains_how_to_install_on_every_device(self):
        html = self.call("GET", "/partner/account", cookie=self.cookie).html
        for text in ('id="installa"', "Installa l'app", "iPhone o iPad (Safari)", "Condividi", "Aggiungi alla schermata Home",
                     "Android (Chrome)", "Installa app", "Computer (Chrome o Edge)", "icona", "Aggiungi al Dock",
                     'id="installBtn"', 'id="installDone"', 'data-os="ios"', 'data-os="android"', 'data-os="desktop"'):
            self.assertIn(text, html)

    def test_home_has_a_dismissible_install_hint(self):
        html = self.call("GET", "/partner", cookie=self.cookie).html
        self.assertIn('id="installBar"', html)
        self.assertIn('class="card installbar hidden"', html)  # nascosto finche' il browser non dice che serve
        for text in ("data-install-go", "data-install-dismiss", "Più tardi", "beforeinstallprompt", "appinstalled",
                     "display-mode: standalone", "pp_install_dismissed", "/partner/account#installa"):
            self.assertIn(text, html)

    def test_service_worker_is_served_publicly_with_the_right_scope(self):
        h = self.call("GET", "/partner/sw.js")  # senza sessione: lo scarica il browser prima di accedere
        self.assertEqual(h.status, 200)
        self.assertEqual(h.header("Service-Worker-Allowed"), "/partner")
        self.assertEqual(h.header("Cache-Control"), "no-cache")
        self.assertIn("javascript", h.header("Content-Type"))
        source = h.wfile.getvalue().decode("utf-8")
        for text in ("addEventListener('fetch'", "event.request.method !== 'GET'", "Sei offline", "skipWaiting", "clients.claim",
                     "pp-partner-shell-v1", "mode === 'navigate'"):
            self.assertIn(text, source)
        self.assertNotIn("cache.put", source)  # le pagine con dati non vengono mai conservate

    def test_pages_register_the_portal_service_worker_and_have_install_metadata(self):
        html = self.call("GET", "/partner", cookie=self.cookie).html
        self.assertIn("serviceWorker.register('/partner/sw.js',{scope:'/partner'})", html)
        self.assertIn('<link rel="manifest" href="/partner/manifest.json">', html)
        self.assertIn('<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">', html)
        self.assertIn('name="apple-mobile-web-app-capable" content="yes"', html)
        self.assertIn('name="mobile-web-app-capable" content="yes"', html)
        self.assertIn('name="apple-mobile-web-app-title" content="PP Partners"', html)

    def test_manifest_is_installable_and_has_shortcuts(self):
        manifest = json.loads(self.call("GET", "/partner/manifest.json").wfile.getvalue())
        self.assertEqual((manifest["start_url"], manifest["scope"], manifest["display"]), ("/partner", "/partner", "standalone"))
        sizes = {icon["sizes"]: icon for icon in manifest["icons"]}
        self.assertEqual(set(sizes), {"192x192", "512x512"})
        for icon in manifest["icons"]:
            self.assertTrue((app.ASSETS / icon["src"].rsplit("/", 1)[1]).exists(), icon["src"])
            self.assertEqual(icon["type"], "image/png")
        self.assertEqual([s["url"] for s in manifest["shortcuts"]], ["/partner/nuova", "/partner/preventivo"])
        self.assertTrue(manifest["name"] and manifest["short_name"] and manifest["theme_color"] and manifest["background_color"])

    def test_staff_get_a_ready_to_send_link_and_instructions(self):
        with app.db() as c:
            c.execute("UPDATE users SET must_change_password=0")
            admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        handler = object.__new__(app.App)
        handler.headers = {}
        handler.path = "/portale-partner"
        pages = []
        handler.send_html = lambda content, *a: pages.append(content)
        handler.portal_partner_page(admin)
        html = pages[-1]
        for text in ("Link del portale da dare ai veterinari", "Copia link", "Copia messaggio con istruzioni", "Apri il portale",
                     "location.origin+'/partner'", "Safari", "Aggiungi alla schermata Home", "Installa app", "WhatsApp"):
            self.assertIn(text, html)
        self.assertNotIn("pini.vet26", html.split("Link del portale")[1].split("Attiva una clinica")[0])  # nessuna password nel messaggio


class StaffEntryPointTests(PortalBase):
    def test_sidebar_link_is_visible_to_every_user(self):
        self.assertIn(("/partner", "stethoscope", "Portale Veterinari"), app.SIDEBAR_LINKS)
        self.assertIn("Portale Veterinari", app.MENU_CARD_META)
        handler = object.__new__(app.App)
        with app.db() as c:
            operator = c.execute("SELECT * FROM users WHERE username='serena'").fetchone()
        for user in (self.admin, operator):
            html = app.layout("Prova", "<main></main>", user)
            self.assertIn('href="/partner"', html)
            self.assertIn("Portale Veterinari", html)

    def test_partner_routes_do_not_need_a_staff_login(self):
        handler = object.__new__(app.App)
        calls = []
        handler.partner_portal_route = lambda method, path: calls.append((method, path))
        handler.path = "/partner/accedi"
        handler.headers = {}
        handler._route_get()
        handler.path = "/partner"
        handler._route_get()
        handler.path = "/partner/nuova"
        handler._route_post()
        self.assertEqual(calls, [("GET", "/partner/accedi"), ("GET", "/partner"), ("POST", "/partner/nuova")])


class DemoSeedAndMigrationTests(PortalBase):
    def test_demo_clinic_is_created_once_with_two_vouchers(self):
        with app.db() as c:
            self.assertIsNone(ps.ensure_demo_clinic(c, app.password_hash))
            clinic = c.execute("SELECT * FROM partner_clinics WHERE id=?", (self.demo_clinic,)).fetchone()
            vouchers = c.execute("SELECT COUNT(*) FROM veterinarian_vouchers WHERE veterinarian_id=? AND status='Maturato'",
                                 (clinic["veterinarian_id"],)).fetchone()[0]
            user = c.execute("SELECT * FROM partner_users WHERE username='villadeipini'").fetchone()
            vet = c.execute("SELECT * FROM veterinarians WHERE id=?", (clinic["veterinarian_id"],)).fetchone()
        self.assertEqual((clinic["is_demo"], clinic["has_freezer"], clinic["vouchers_enabled"]), (1, 1, 1))
        self.assertEqual(vouchers, 2)
        self.assertTrue(app.password_ok("pini.vet26", user["password_hash"]))
        self.assertNotIn("pini.vet26", user["password_hash"])
        self.assertEqual(vet["short_name"], "Villa dei Pini")
        # eliminata la clinica di prova, non viene ricreata ai riavvii
        with app.db() as c:
            c.execute("DELETE FROM partner_sessions")
            c.execute("DELETE FROM partner_users WHERE username='villadeipini'")
            c.execute("DELETE FROM partner_clinics WHERE id=?", (self.demo_clinic,))
        app.init_db()
        with app.db() as c:
            self.assertIsNone(ps.ensure_demo_clinic(c, app.password_hash))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_clinics WHERE is_demo=1").fetchone()[0], 0)

    def test_init_db_alone_does_not_create_demo_data(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            old = (app.DATA, app.DB_PATH, app.DDT_DIR)
            app.DATA = Path(tmp)
            app.DB_PATH = app.DATA / "fresh.db"
            app.DDT_DIR = app.DATA / "ddt"
            try:
                app.init_db()
                with app.db() as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM partner_clinics").fetchone()[0], 0)
            finally:
                app.DATA, app.DB_PATH, app.DDT_DIR = old

    def test_schema_upgrade_from_phase_one_adds_login_columns(self):
        with app.db() as c:
            c.execute("DROP INDEX IF EXISTS idx_partner_users_username")
            c.execute("ALTER TABLE partner_users DROP COLUMN username")
            c.execute("ALTER TABLE partner_users DROP COLUMN password_hash")
            c.execute("ALTER TABLE partner_clinics DROP COLUMN is_demo")
            c.execute("DROP TABLE partner_sessions")
            c.execute("DROP TABLE partner_login_attempts")
        app.init_db()
        with app.db() as c:
            users = {r[1] for r in c.execute("PRAGMA table_info(partner_users)")}
            clinics = {r[1] for r in c.execute("PRAGMA table_info(partner_clinics)")}
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"username", "password_hash"} <= users)
        self.assertIn("is_demo", clinics)
        self.assertTrue({"partner_sessions", "partner_login_attempts"} <= tables)


if __name__ == "__main__":
    unittest.main()
