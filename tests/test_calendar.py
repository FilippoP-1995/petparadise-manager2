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
    overlap_rows,
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
        self.assertTrue(expected.issubset(tables))
        self.assertTrue({"idx_calendar_events_range", "idx_calendar_events_status", "idx_calendar_events_vet", "idx_calendar_events_assigned"}.issubset(indexes))
        self.assertEqual(zones, set(DEFAULT_ZONES))

    def test_all_event_types_auto_titles_colors_and_states(self):
        expected = {
            "Ritiro": "RITIRO LIVORNO",
            "Ritiro in sede": "RITIRO IN SEDE LIVORNO",
            "Riconsegna": "RICONSEGNA FIDO LIVORNO",
            "Riconsegna in sede": "RICONSEGNA FIDO IN SEDE LIVORNO",
            "Appuntamento": "APPUNTAMENTO FORNITORE",
        }
        ids = [self.save(self.event_form(kind)) for kind in expected]
        with app.db() as conn:
            rows = conn.execute("SELECT * FROM calendar_events ORDER BY id").fetchall()
        self.assertEqual([row["title"] for row in rows], list(expected.values()))
        self.assertEqual(rows[-1]["event_status"], "")
        self.assertEqual(rows[-1]["payment_status"], "")
        self.assertTrue(all(ids))

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

    def test_calendar_pages_render_all_views_navigation_filters_and_mobile_hooks(self):
        self.save(self.event_form("Ritiro", event_status="Da ritirare"))
        pages = {}
        self.handler.send_html = lambda html, status=200: pages.update(current=html, status=status)
        for view in ("giorno", "settimana", "mese", "mista_settimana", "mista_mese", "compatto"):
            self.handler.path = f"/calendario?vista={view}&data=2026-07-15"
            self.handler.calendar_page(self.admin)
            pages[view] = pages["current"]
        self.assertIn("Calendario operativo", pages["giorno"])
        self.assertIn("data-calendar-swipe", pages["giorno"])
        self.assertIn("calendar-week-scroll", pages["settimana"])
        self.assertIn("calendar-month", pages["mese"])
        self.assertIn("calendar-mixed", pages["mista_settimana"])
        self.assertIn("calendar-dots", pages["compatto"])
        self.assertIn("Ricerca e filtri", pages["giorno"])
        self.assertIn("safe-bottom", app.CSS)
        self.handler.path = "/"
        self.handler.dashboard(self.admin)
        self.assertIn("+ Nuova pratica", pages["current"])
        self.assertIn("+ Nuovo evento", pages["current"])

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
        self.assertIn("@media(max-width:900px)", app.CSS)
        self.assertIn("bottom:calc(88px + var(--safe-bottom))", app.CSS)

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
        event_id = self.save(self.event_form("Ritiro", start_date="2026-07-15", start_time="10:00", end_time="11:00"))
        moment = datetime(2026, 7, 15, 9, 30)
        with app.db() as conn:
            first = process_calendar_notifications(conn, None, moment)
            second = process_calendar_notifications(conn, None, moment)
            reminder = conn.execute("SELECT * FROM calendar_event_notifications WHERE event_id=?", (event_id,)).fetchone()
            count = conn.execute("SELECT count(*) n FROM notifications WHERE type='calendar_reminder_30m'").fetchone()["n"]
            summary_first = process_calendar_notifications(conn, None, datetime(2026, 7, 15, 9, 45))
            summary_second = process_calendar_notifications(conn, None, datetime(2026, 7, 15, 9, 50))
            summaries = conn.execute("SELECT count(*) n FROM notifications WHERE type='calendar_daily_summary'").fetchone()["n"]
        self.assertEqual((first, second, count), (2, 0, 1))
        self.assertEqual(reminder["status"], "inviato")
        self.assertTrue(reminder["sent_at"])
        self.assertEqual((summary_first, summary_second, summaries), (0, 0, 1))

    def test_cancelled_and_deleted_events_never_emit_pending_reminders(self):
        cancelled = self.save(self.event_form("Ritiro", event_status="Annullato", start_time="10:00"))
        active = self.save(self.event_form("Ritiro", start_time="10:00"))
        self.handler.form = lambda: {}
        self.handler.calendar_event_action(self.admin, active, "elimina")
        with app.db() as conn:
            process_calendar_notifications(conn, None, datetime(2026, 7, 15, 12, 0))
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
