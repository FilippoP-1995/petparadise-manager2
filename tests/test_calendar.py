import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import app
from calendar_service import (
    DEFAULT_ZONES,
    normalize_event,
    normalize_time,
    overlap_rows,
    parse_items,
    period_bounds,
)
from notification_service import process_calendar_notifications


class OperationalCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "calendar-test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        self.handler.redirect = lambda path: setattr(self, "redirected", path)
        self.redirected = ""
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def event_form(self, event_type="Ritiro", **changes):
        data = {
            "event_type": event_type,
            "operator_name": "Serena",
            "title": "",
            "zone": "Livorno" if event_type in ("Ritiro", "Riconsegna") else "",
            "destination_site": "Livorno" if "in sede" in event_type else "",
            "animal_name": "Fido" if event_type in ("Riconsegna", "Riconsegna in sede") else "",
            "start_date": "2026-07-15",
            "start_time": "09:30",
            "end_date": "2026-07-15",
            "end_time": "10:30",
            "event_status": "Da confermare" if event_type.startswith("Ritiro") else "In programma",
            "payment_status": "Da pagare",
            "payment_amount": "0",
            "animals_json": "[]",
            "estimate_json": "[]",
        }
        if event_type == "Appuntamento":
            data["title"] = "APPUNTAMENTO FORNITORE"
        data.update(changes)
        return data

    def save(self, form):
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin)
        return int(self.redirected.rsplit("/", 1)[-1])

    def test_additive_schema_tables_indexes_and_default_zones(self):
        expected = {
            "calendar_events", "calendar_event_animals", "calendar_event_estimate_items",
            "calendar_event_comments", "calendar_event_history", "calendar_event_notifications",
            "calendar_zones",
        }
        with app.db() as conn:
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            indexes = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            zones = {row["name"] for row in conn.execute("SELECT name FROM calendar_zones")}
            event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(calendar_events)")}
        self.assertTrue(expected.issubset(tables))
        self.assertTrue({"idx_calendar_events_range", "idx_calendar_events_status", "idx_calendar_events_vet", "idx_calendar_events_assigned"}.issubset(indexes))
        self.assertEqual(zones, set(DEFAULT_ZONES))
        self.assertIn("operator_name", event_columns)

    def test_all_event_types_auto_titles_colors_and_states(self):
        expected = {
            "Ritiro": "RITIRO LIVORNO",
            "Ritiro in sede": "RITIRO IN SEDE LIVORNO",
            "Riconsegna": "RICONSEGNA FIDO",
            "Riconsegna in sede": "RICONSEGNA FIDO IN SEDE LIVORNO",
            "Appuntamento": "APPUNTAMENTO FORNITORE",
        }
        ids = [self.save(self.event_form(kind)) for kind in expected]
        with app.db() as conn:
            rows = conn.execute("SELECT * FROM calendar_events ORDER BY id").fetchall()
        self.assertEqual([row["title"] for row in rows], list(expected.values()))
        self.assertEqual(rows[-1]["event_status"], "")
        self.assertEqual(rows[-1]["payment_status"], "")
        self.assertTrue(all(row["operator_name"]=="Serena" for row in rows))
        self.assertTrue(all(ids))

    def test_validation_is_specific_to_event_type_and_empty_animals_are_ignored(self):
        pickup=normalize_event(self.event_form("Ritiro",destination_site=""))
        self.assertEqual((pickup["zone"],pickup["destination_site"]),("Livorno",""))
        with self.assertRaisesRegex(ValueError,"zona"):
            normalize_event(self.event_form("Ritiro",zone=""))
        for site in ("Livorno","Empoli"):
            event=normalize_event(self.event_form("Ritiro in sede",destination_site=site))
            self.assertEqual(event["destination_site"],site)
        with self.assertRaisesRegex(ValueError,"sede"):
            normalize_event(self.event_form("Ritiro in sede",destination_site=""))
        delivery=normalize_event(self.event_form("Riconsegna",zone="Livorno"))
        self.assertEqual((delivery["zone"],delivery["title"]),("Livorno","RICONSEGNA FIDO"))
        delivery_in_sede=normalize_event(self.event_form("Riconsegna in sede",zone="Livorno"))
        self.assertEqual(delivery_in_sede["zone"],"")
        appointment=normalize_event(self.event_form("Appuntamento",zone="Livorno"))
        self.assertEqual(appointment["zone"],"")
        self.assertEqual(parse_items(json.dumps([{"name":"","species":"","weight":"","cremation_type":"","notes":""}]),"animal"),[])

    def test_invalid_calendar_save_stays_in_wizard_without_raw_http_error(self):
        rendered=[];errors=[]
        self.handler.path="/calendario/nuovo"
        self.handler.form=lambda:self.event_form("Ritiro",zone="")
        self.handler.send_html=lambda html,status=200:rendered.append((html,status))
        self.handler.send_error=lambda *args:errors.append(args)
        self.handler.save_calendar_event(self.admin)
        self.assertFalse(errors)
        self.assertEqual(rendered[-1][1],200)
        self.assertIn("La zona è obbligatoria",rendered[-1][0])
        self.assertIn('name="start_date" value="2026-07-15"',rendered[-1][0])

    def test_calendar_contact_search_includes_razzauti_and_first_animal_is_open(self):
        stamp=datetime.now().isoformat(timespec="seconds")
        with app.db() as conn:
            conn.execute("INSERT INTO veterinarians(short_name,clinic_name,doctor_name,phone,address,city,notes,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?)",("Razzauti","Ambulatorio Razzauti","Dott. Razzauti","0586123456","Via Roma 8","Livorno","9-19",stamp,stamp))
        response={};self.handler.path="/api/veterinari/search?q=razzauti"
        self.handler.send_json=lambda obj,status=200:response.update(obj=obj,status=status)
        self.handler.api_veterinarians_search(self.admin)
        self.assertEqual(response["status"],200)
        self.assertIn("Razzauti",[row["display"] for row in response["obj"]["results"]])
        rendered=[];self.handler.path="/calendario/nuovo";self.handler.send_html=lambda html,status=200:rendered.append(html)
        self.handler.calendar_event_form(self.admin)
        self.assertIn("Cerca cliente o veterinario",rendered[-1])
        self.assertIn("calendarAddRow('animal',{})",rendered[-1])
        self.assertIn("Veterinario/Ambulatorio",app.APP_JS)
        self.assertIn("history.back()",app.APP_JS)
        self.assertIn("Le modifiche non salvate andranno perse",app.APP_JS)

    def test_manual_time_normalization_and_quarter_hour_suggestions(self):
        self.assertEqual(normalize_time("9"), "09:00")
        self.assertEqual(normalize_time("18"), "18:00")
        self.assertEqual(normalize_time("1830"), "18:30")
        self.assertEqual(normalize_time("945"), "09:45")
        result=normalize_event(self.event_form("Ritiro",start_time="1830",end_time="19"))
        self.assertEqual((result["start_at"][11:16],result["end_at"][11:16]),("18:30","19:00"))
        self.assertIn("calendarSyncTimeWheel", app.APP_JS)
        self.assertIn("Math.round(minute/5)*5", app.APP_JS)
        self.assertIn("padStart(2,'0')", app.APP_JS)

    def test_multi_day_all_day_and_invalid_ranges(self):
        result = normalize_event(self.event_form("Appuntamento", all_day="1", start_date="2026-07-15", end_date="2026-07-18"))
        self.assertEqual(result["start_at"], "2026-07-15T00:00:00")
        self.assertEqual(result["end_at"], "2026-07-18T23:59:59")
        self.assertEqual(result["all_day"], 1)
        with self.assertRaisesRegex(ValueError, "fine"):
            normalize_event(self.event_form("Ritiro", start_date="2026-07-16", end_date="2026-07-15"))

    def test_animals_estimate_and_new_zone_are_persisted_without_practice_side_effects(self):
        form = self.event_form(
            "Ritiro", zone="Cecina", save_zone="1",
            animals_json=json.dumps([
                {"name": "Luna", "species": "Cane", "weight": "12", "cremation_type": "Singola", "notes": ""},
                {"name": "Milo", "species": "Gatto", "weight": "6", "cremation_type": "Collettiva", "notes": "Fragile"},
            ]),
            estimate_json=json.dumps([
                {"description": "Cremazione", "amount": "350"},
                {"description": "Ritiro", "amount": "50"},
                {"description": "Notturno", "amount": "30"},
            ]),
        )
        event_id = self.save(form)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM calendar_event_animals WHERE event_id=?", (event_id,)).fetchone()["n"], 2)
            self.assertEqual(conn.execute("SELECT sum(amount) n FROM calendar_event_estimate_items WHERE event_id=?", (event_id,)).fetchone()["n"], 430)
            self.assertIsNotNone(conn.execute("SELECT 1 FROM calendar_zones WHERE name='Cecina'").fetchone())
            self.assertEqual(conn.execute("SELECT count(*) n FROM practices").fetchone()["n"], 0)

    def test_overlap_query_multiday_search_and_period_views(self):
        event_id = self.save(self.event_form("Ritiro", title="RITIRO SPECIALE LIVORNO", start_date="2026-07-14", end_date="2026-07-17", notes="cliente urgente"))
        with app.db() as conn:
            rows = overlap_rows(conn, "2026-07-15", "2026-07-15", {"q": "urgente"})
            filtered = overlap_rows(conn, "2026-07-01", "2026-07-31", {"zone": "Livorno", "venue_scope": "fuori", "date_from": "2026-07-15", "date_to": "2026-07-16"})
            none = overlap_rows(conn, "2026-07-18", "2026-07-18")
        self.assertEqual([row["id"] for row in rows], [event_id])
        self.assertEqual([row["id"] for row in filtered], [event_id])
        self.assertEqual(none, [])
        self.assertEqual(tuple(map(str, period_bounds("giorno", "2026-07-15"))), ("2026-07-15", "2026-07-15"))
        self.assertEqual(tuple(map(str, period_bounds("settimana", "2026-07-15"))), ("2026-07-13", "2026-07-19"))
        self.assertEqual(tuple(map(str, period_bounds("mese", "2026-07-15"))), ("2026-07-01", "2026-07-31"))

    def test_client_and_veterinarian_snapshots_do_not_modify_registries(self):
        stamp = datetime.now().isoformat(timespec="seconds")
        with app.db() as conn:
            client_id = conn.execute("INSERT INTO clients(first_name,last_name,phone,created_at,updated_at) VALUES(?,?,?,?,?)", ("Mario", "Rossi", "333123", stamp, stamp)).lastrowid
            vet_id = conn.execute("INSERT INTO veterinarians(clinic_name,doctor_name,phone,notes,active,created_at,updated_at,address) VALUES(?,?,?,?,1,?,?,?)", ("Clinica Test", "Dott. Blu", "0586000", "9-18", stamp, stamp, "Via Roma 1")).lastrowid
        event_id = self.save(self.event_form("Ritiro", client_id=str(client_id), client_first_name="Mario", client_last_name="Rossi", client_phone="333123", veterinarian_id=str(vet_id), location_type="Veterinario"))
        with app.db() as conn:
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
            client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
            vet = conn.execute("SELECT * FROM veterinarians WHERE id=?", (vet_id,)).fetchone()
        self.assertEqual((event["client_id"], event["veterinarian_id"]), (client_id, vet_id))
        self.assertEqual(event["veterinarian_address"], "Via Roma 1")
        self.assertEqual((client["first_name"], vet["clinic_name"]), ("Mario", "Clinica Test"))

    def test_delivery_zone_and_clinic_are_persisted_and_shown_without_growing_the_card(self):
        stamp = datetime.now().isoformat(timespec="seconds")
        with app.db() as conn:
            vet_id = conn.execute(
                "INSERT INTO veterinarians(clinic_name,short_name,doctor_name,phone,address,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)",
                ("Ambulatorio Riconsegna", "AmbRicons", "Dott. Bianchi", "0586999999", "Via Test 1", stamp, stamp),
            ).lastrowid
        event_id = self.save(self.event_form(
            "Riconsegna", zone="Livorno",
            delivery_clinic_id=str(vet_id), delivery_clinic_name="Ambulatorio Riconsegna",
        ))
        with app.db() as conn:
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual(event["zone"], "Livorno")
        self.assertEqual(event["delivery_clinic_id"], vet_id)
        self.assertEqual(event["delivery_clinic_name"], "Ambulatorio Riconsegna")
        self.assertEqual(event["delivery_clinic_phone"], "0586999999")

        card = self.handler.calendar_event_card(dict(event, animal_species="", animal_weight_total=0, cremation_types="", estimate_total=0, payment_channel="", operator_name="Serena", assigned_name="", creator_name=""))
        self.assertIn("Livorno", card)
        self.assertIn("Ambulatorio Riconsegna", card)
        self.assertEqual(card.count("<p>"), 2)  # details line + operator line only, no extra row added per detail

        pages = []
        self.handler.send_html = lambda html, status=200: pages.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertIn("Ambulatorio Riconsegna", pages[-1])
        self.assertIn(">Zona<", pages[-1])

    def test_delivery_clinic_optional_and_ambulatorio_field_present_in_wizard(self):
        event_id = self.save(self.event_form("Riconsegna"))
        with app.db() as conn:
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertIsNone(event["delivery_clinic_id"])
        self.assertEqual(event["delivery_clinic_name"], "")
        rendered = []
        self.handler.path = "/calendario/nuovo"
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.calendar_event_form(self.admin)
        self.assertIn("Ambulatorio riconsegna (facoltativo)", rendered[-1])
        self.assertIn("calendarDeliveryClinicSearch", rendered[-1])
        self.assertIn("function calendarSelectDeliveryClinic(form,item)", app.APP_JS)

    def test_client_name_resolution_priority_and_empty_row(self):
        base = {"client_id": None, "client_first_name": "", "client_last_name": "", "person_company": "", "linked_practice_id": None}
        self.assertEqual(self.handler.calendar_event_client_name(base), "")
        manual = dict(base, client_first_name="Anna", client_last_name="Verdi")
        self.assertEqual(self.handler.calendar_event_client_name(manual), "Anna Verdi")
        company = dict(base, person_company="Studio Veterinario XYZ")
        self.assertEqual(self.handler.calendar_event_client_name(company), "Studio Veterinario XYZ")
        via_practice = dict(base, linked_practice_id=7, client_first_name="Ignored")
        self.assertEqual(self.handler.calendar_event_client_name(via_practice, practice_owner_names={7: "Proprietario Pratica"}), "Proprietario Pratica")
        via_client = dict(base, client_id=3, linked_practice_id=7, client_first_name="Ignored")
        self.assertEqual(
            self.handler.calendar_event_client_name(via_client, client_names={3: "Cliente Anagrafica"}, practice_owner_names={7: "Proprietario Pratica"}),
            "Cliente Anagrafica",
        )

    def test_calendar_pages_render_all_views_navigation_filters_and_mobile_hooks(self):
        self.save(self.event_form("Ritiro", event_status="Da ritirare",animals_json=json.dumps([{"name":"Luna","species":"Cane","weight":"18","cremation_type":"Singola","notes":""}])))
        self.save(self.event_form("Appuntamento", title="APPUNTAMENTO FORNITORE", end_time="10:00"))
        pages = {}
        self.handler.send_html = lambda html, status=200: pages.update(current=html, status=status)
        for view in ("giorno", "settimana", "mese", "mista_settimana", "mista_mese", "compatto"):
            self.handler.path = f"/calendario?vista={view}&data=2026-07-15"
            self.handler.calendar_page(self.admin)
            pages[view] = pages["current"]
        self.assertIn("Calendario operativo", pages["giorno"])
        self.assertIn("data-calendar-swipe", pages["giorno"])
        self.assertIn("calendar-week-scroll", pages["settimana"])
        self.assertNotIn('calendar-week-scroll" data-calendar-swipe', pages["settimana"])
        self.assertIn("calendar-week-time-column", pages["settimana"])
        self.assertIn("calendar-week-grid-line", pages["settimana"])
        self.assertIn(".calendar-week-scroll{max-height:calc(100dvh - 210px);overflow:auto", app.CSS)
        self.assertIn(".calendar-week-time-column>header,.calendar-day-column>header{top:0;z-index:8", app.CSS)
        self.assertIn("calendar-month", pages["mese"])
        self.assertIn("calendar-dot-red", pages["mese"])
        self.assertIn("calendar-event-icon", pages["giorno"])
        self.assertIn("--event-lanes:2", pages["giorno"])
        self.assertIn("--event-lane:0", pages["giorno"])
        self.assertIn("--event-lane:1", pages["giorno"])
        self.assertEqual(pages["giorno"].count('data-calendar-view="'), 3)
        for label in ("Giorno", "Settimana", "Mese"):
            self.assertIn(f">{label}</a>", pages["giorno"])
        self.assertIn("18 kg", pages["giorno"])
        self.assertIn("Singola", pages["settimana"])
        self.assertIn("calendar-mixed", pages["mista_settimana"])
        self.assertIn("calendar-dots", pages["compatto"])
        self.assertIn("Ricerca e filtri", pages["giorno"])
        self.assertIn("safe-bottom", app.CSS)
        self.assertIn("15 Luglio 2026",pages["giorno"])
        self.assertIn(">OGGI<",pages["giorno"])
        self.assertIn("create-sheet",pages["giorno"])
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))",app.CSS)
        self.assertIn("min-height:46px",app.CSS)
        self.assertIn("linear-gradient(135deg,#fb4c67,#d9284c)",app.CSS)
        self.assertIn("create-sheet-backdrop",pages["giorno"])
        self.handler.path = "/"
        self.handler.dashboard(self.admin)
        self.assertIn("+ Nuova pratica", pages["current"])
        self.assertIn("+ Nuovo evento", pages["current"])

    def test_new_event_shortcut_prefills_currently_viewed_day(self):
        pages = {}
        self.handler.send_html = lambda html, status=200: pages.update(current=html)
        self.handler.path = "/calendario?vista=giorno&data=2026-07-20"
        self.handler.calendar_page(self.admin)
        self.assertIn('data-calendar-new-event', pages["current"])
        self.assertIn('href="/calendario/nuovo?data=2026-07-20"', pages["current"])
        self.assertIn("function calendarInitDateTimeSync()", app.APP_JS)
        self.assertIn("location.pathname==='/calendario'", app.APP_JS)
        self.assertIn("dataset.manualEdit", app.APP_JS)

    def test_end_date_and_time_stay_manually_editable_after_first_change(self):
        self.assertIn("form.start_date.addEventListener('change',sync)", app.APP_JS)
        self.assertIn("if(form.end_date&&!form.end_date.dataset.manualEdit)form.end_date.value=form.start_date.value;", app.APP_JS)
        # Server-side normalize_event already rejects an end before the start regardless of client sync bugs.
        with self.assertRaisesRegex(ValueError, "fine"):
            normalize_event(self.event_form("Ritiro", start_date="2026-07-16", end_date="2026-07-15"))

    def test_link_existing_practice_search_covers_all_four_criteria(self):
        stamp = datetime.now().isoformat(timespec="seconds")
        with app.db() as conn:
            admin_id = self.admin["id"]
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,species,owner_first_name,owner_last_name,clinic_name,pickup_date)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-LINK-01", "Veterinario", "Livorno", "Ritirato", stamp, stamp, admin_id,
                 "Luna", "Cane", "Anna", "Verdi", "Clinica Aurelia", "2026-07-10"),
            ).lastrowid
        for term in ("Luna", "Verdi", "Aurelia", "PP-LINK-01"):
            response = {}
            self.handler.path = f"/api/calendario/pratiche/search?q={term}"
            self.handler.send_json = lambda obj, status=200: response.update(obj=obj, status=status)
            self.handler.api_calendar_practices_search(self.admin)
            practice_ids = [r["practice_id"] for r in response["obj"]["results"]]
            self.assertIn(pid, practice_ids, f"search for {term!r} did not find the practice")

    def test_link_and_unlink_existing_practice_to_ritiro_prevents_duplicates(self):
        stamp = datetime.now().isoformat(timespec="seconds")
        with app.db() as conn:
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("PP-LINK-02", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"], "Fido"),
            ).lastrowid
            other_pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("PP-LINK-03", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"], "Rex"),
            ).lastrowid
        event_id = self.save(self.event_form("Ritiro", event_status="Ritirato"))

        # Non-admin cannot unlink even before a link exists (route requires linked_practice_id anyway).
        self.handler.form = lambda: {"practice_id": str(pid)}
        self.handler.calendar_event_action(self.admin, event_id, "collega-pratica")
        with app.db() as conn:
            event = conn.execute("SELECT linked_practice_id FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual(event["linked_practice_id"], pid)

        # Linking again (accidentally, e.g. to a different practice) must not overwrite the existing link.
        errors = []
        self.handler.send_error = lambda *args: errors.append(args)
        self.handler.form = lambda: {"practice_id": str(other_pid)}
        self.handler.calendar_event_action(self.admin, event_id, "collega-pratica")
        self.assertTrue(errors and errors[0][0] == 409)
        with app.db() as conn:
            event = conn.execute("SELECT linked_practice_id FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual(event["linked_practice_id"], pid)

        non_admin = dict(self.admin)
        non_admin["role"] = "staff"
        errors.clear()
        self.handler.form = lambda: {"confirm": "SCOLLEGA"}
        self.handler.calendar_event_action(non_admin, event_id, "scollega-pratica")
        self.assertTrue(errors and errors[0][0] == 403)

        self.handler.calendar_event_action(self.admin, event_id, "scollega-pratica")
        with app.db() as conn:
            event = conn.execute("SELECT linked_practice_id FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertIsNone(event["linked_practice_id"])

    def test_link_practice_section_shown_only_when_ritirato_and_unlinked(self):
        event_id = self.save(self.event_form("Ritiro", event_status="Da confermare"))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertNotIn("Collega pratica esistente", rendered[-1])

        self.handler.form = lambda: {"status": "Ritirato"}
        with patch("app.emit_notification", return_value=[]):
            self.handler.calendar_event_action(self.admin, event_id, "stato")
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertIn("Collega pratica esistente", rendered[-1])
        self.assertIn("calendarLinkPracticeSearch", rendered[-1])

        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("PP-LINK-04", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"], "Milo"),
            ).lastrowid
        self.handler.form = lambda: {"practice_id": str(pid)}
        self.handler.calendar_event_action(self.admin, event_id, "collega-pratica")
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertNotIn('id="calendarLinkPracticeSearch"', rendered[-1])
        self.assertIn("Apri pratica", rendered[-1])
        self.assertIn("PP-LINK-04", rendered[-1])

    def test_form_is_guided_supports_contacts_maps_autocomplete_and_touch_layout(self):
        captured = []
        self.handler.path = "/calendario/nuovo?data=2026-07-15"
        self.handler.send_html = lambda html, status=200: captured.append(html)
        self.handler.calendar_event_form(self.admin)
        html = captured[0]
        for text in ("Tipo evento", "Data e titolo", "Cerca cliente", "Cerca veterinario", "+ Aggiungi animale", "Preventivo previsto", "Tutto il giorno"):
            self.assertIn(text, html)
        for hook in ("calendarZoneOffer", "/api/clienti/search", "/api/veterinari/search"):
            self.assertIn(hook, html if hook == "calendarZoneOffer" else app.APP_JS + html)
        for operator in ("Serena","Alessio","Filippo"):
            self.assertIn(operator,html)
        for preset in ("Cremazione","Ritiro","Riconsegna","Urna","Altro"):
            self.assertIn(preset,html)
        self.assertNotIn("Persona o azienda",html)
        self.assertNotIn("Nome animale *",html)
        self.assertNotIn("Operatore assegnato",html)
        self.assertIn("calendarTypeSelected(this)",html)
        self.assertIn("calendarWizardSwipe",app.APP_JS)
        self.assertIn("calendarTimeInput",app.APP_JS)
        self.assertIn("calendarSyncTimeWheel",app.APP_JS)
        self.assertIn("calendarInitTimeWheel",app.APP_JS)
        self.assertIn('class="calendar-datetime-stack"',html)
        self.assertIn('class="calendar-time-wheel"',html)
        self.assertIn('data-wheel-part="hour"',html)
        self.assertIn('data-wheel-part="minute"',html)
        self.assertIn('onfocus="calendarTimeFocus(this)"',html)
        self.assertLess(html.index('name="start_date"'),html.index('name="start_time"'))
        self.assertIn("calendar-zone-results",html)
        self.assertEqual(html.count('class="calendar-event-type-icon"'),5)
        self.assertIn(".calendar-form [data-calendar-types][hidden]",app.CSS)
        self.assertIn("padding:calc(88px + var(--safe-top))",app.CSS)
        self.assertIn("@media(max-width:900px)", app.CSS)
        self.assertIn("bottom:calc(88px + var(--safe-bottom))", app.CSS)

    def test_calendar_settings_store_preferred_view(self):
        captured=[];self.handler.send_html=lambda html,status=200:captured.append(html)
        self.handler.calendar_settings(self.admin)
        html=captured[0]
        for value in ("giorno","settimana","mese","mista_settimana","mista_mese","compatto"):
            self.assertIn(f'value="{value}"',html)
        self.assertIn("localStorage.setItem('ppm_calendar_view'",html)

    def test_comments_history_status_soft_delete_and_restore(self):
        event_id = self.save(self.event_form("Ritiro"))
        with patch("app.emit_notification", return_value=[]):
            self.handler.form = lambda: {"status": "Ritirato", "return_to": "/calendario?data=2026-07-15"}
            self.handler.calendar_event_action(self.admin, event_id, "stato")
            self.handler.form = lambda: {"message": "Cliente avvisato"}
            self.handler.calendar_event_action(self.admin, event_id, "commento")
            with app.db() as conn:comment_id=conn.execute("SELECT id FROM calendar_event_comments WHERE event_id=?",(event_id,)).fetchone()["id"]
            self.handler.form = lambda: {"message": "Cliente confermato"}
            self.handler.calendar_comment_action(self.admin,event_id,comment_id,"modifica")
            self.handler.form = lambda: {}
            self.handler.calendar_event_action(self.admin, event_id, "elimina")
            self.handler.calendar_event_action(self.admin, event_id, "ripristina")
        with app.db() as conn:
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
            comment = conn.execute("SELECT * FROM calendar_event_comments WHERE event_id=?", (event_id,)).fetchone()
            actions = {row["action"] for row in conn.execute("SELECT action FROM calendar_event_history WHERE event_id=?", (event_id,))}
        self.assertEqual(event["event_status"], "Ritirato")
        self.assertIsNone(event["deleted_at"])
        self.assertEqual(comment["message"], "Cliente confermato")
        self.assertTrue(comment["updated_at"])
        self.assertTrue({"Creazione evento", "Cambio stato", "Aggiunta commento", "Modifica commento", "Eliminazione", "Ripristino"}.issubset(actions))

    def test_due_reminder_and_daily_summary_are_idempotent(self):
        future_date = (datetime.now() + timedelta(days=1)).date()
        date_text = future_date.isoformat()
        event_id = self.save(self.event_form("Ritiro", start_date=date_text, end_date=date_text, start_time="10:00", end_time="11:00"))
        moment = datetime.combine(future_date, datetime.min.time()).replace(hour=9, minute=30)
        with app.db() as conn:
            first = process_calendar_notifications(conn, None, moment)
            second = process_calendar_notifications(conn, None, moment)
            reminder = conn.execute("SELECT * FROM calendar_event_notifications WHERE event_id=?", (event_id,)).fetchone()
            count = conn.execute("SELECT count(*) n FROM notifications WHERE type='calendar_reminder_30m'").fetchone()["n"]
            summary_first = process_calendar_notifications(conn, None, moment.replace(minute=45))
            summary_second = process_calendar_notifications(conn, None, moment.replace(minute=50))
            summaries = conn.execute("SELECT count(*) n FROM notifications WHERE type='calendar_daily_summary'").fetchone()["n"]
        self.assertEqual((first, second, count), (2, 0, 1))
        self.assertEqual(reminder["status"], "inviato")
        self.assertTrue(reminder["sent_at"])
        self.assertEqual((summary_first, summary_second, summaries), (0, 0, 1))

    def test_cancelled_and_deleted_events_never_emit_pending_reminders(self):
        future_date = (datetime.now() + timedelta(days=1)).date()
        date_text = future_date.isoformat()
        cancelled = self.save(self.event_form("Ritiro", event_status="Annullato", start_date=date_text, end_date=date_text, start_time="10:00"))
        active = self.save(self.event_form("Ritiro", start_date=date_text, end_date=date_text, start_time="10:00"))
        self.handler.form = lambda: {}
        self.handler.calendar_event_action(self.admin, active, "elimina")
        with app.db() as conn:
            process_calendar_notifications(conn, None, datetime.combine(future_date, datetime.min.time()).replace(hour=12))
            statuses = {row["event_id"]: row["status"] for row in conn.execute("SELECT event_id,status FROM calendar_event_notifications WHERE event_id IN (?,?)", (cancelled, active))}
            sent = conn.execute("SELECT count(*) n FROM notifications WHERE type='calendar_reminder_30m'").fetchone()["n"]
        self.assertNotIn(cancelled, statuses)
        self.assertEqual(statuses, {active: "annullato"})
        self.assertEqual(sent, 0)

    def test_create_practice_prefill_link_and_duplicate_prevention(self):
        event_id = self.save(self.event_form(
            "Ritiro", event_status="Ritirato", client_first_name="Anna", client_last_name="Verdi",
            client_phone="333111", address="Via Roma 10", notes="Nota calendario",
            animals_json=json.dumps([{"name": "Luna", "species": "Cane", "weight": "12", "cremation_type": "Singola", "notes": ""}]),
        ))
        pages = []
        self.handler.path = f"/nuova?calendar_event_id={event_id}"
        self.handler.send_html = lambda html, status=200: pages.append(html)
        self.handler.new_page(self.admin)
        self.assertIn('name="calendar_event_id"', pages[0])
        self.assertIn("Anna", pages[0])
        self.assertIn("Luna", pages[0])
        form = {
            "calendar_event_id": str(event_id), "operator_name": "SERENA", "request_origin": "Privato",
            "destination_branch": "Livorno", "owner_first_name": "Anna", "owner_last_name": "Verdi",
            "owner_phone": "333111", "owner_tax_code": "VRDNNA80A01F205X", "owner_street": "Via Roma 10",
            "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100", "animal_name": "Luna",
            "species": "Cane", "estimated_weight": "12", "service_type": "Cremazione singola",
            "pickup_date": "2026-07-15", "pickup_time": "09:30", "status": "Ritirato",
        }
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]):
            self.handler.create_practice(self.admin)
        with app.db() as conn:
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
            practice = conn.execute("SELECT * FROM practices WHERE id=?", (event["linked_practice_id"],)).fetchone()
        self.assertEqual((practice["animal_name"], practice["pickup_time"]), ("Luna", "09:30"))
        self.handler.create_practice(self.admin)
        self.assertEqual(self.redirected, f'/pratiche/{practice["id"]}')

    def test_existing_services_remain_untouched_by_calendar_estimates(self):
        before = {}
        with app.db() as conn:
            for table in ("practices", "payment_movements", "email_orders", "whatsapp_messages", "urns", "clients"):
                before[table] = conn.execute(f"SELECT count(*) n FROM {table}").fetchone()["n"]
        self.save(self.event_form("Ritiro", estimate_json=json.dumps([{"description": "Cremazione", "amount": "999"}])))
        with app.db() as conn:
            after = {table: conn.execute(f"SELECT count(*) n FROM {table}").fetchone()["n"] for table in before}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
