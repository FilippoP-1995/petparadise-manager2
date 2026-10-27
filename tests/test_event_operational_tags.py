import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class EventOperationalTagsTests(unittest.TestCase):
    # Richiesta esplicita dell'utente (27/10/2026): le etichette operative
    # (ASSISTITA, SALUTO, CALCO, PELO...) si scelgono gia' creando un Ritiro;
    # passano alla pratica che nasce dall'evento e compaiono nel Programma
    # Cremazioni. Scelte dell'utente: solo Ritiro e Ritiro in sede; se la
    # pratica esiste gia', cambiarle sull'evento aggiorna anche la pratica.

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        self.redirects = []
        self.handler.redirect = lambda path: self.redirects.append(path)
        self.pages = []
        self.handler.send_html = lambda html, *a: self.pages.append(html)
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def event_form(self, event_type="Ritiro", **changes):
        data = {
            "event_type": event_type, "operator_name": "Serena", "title": "APPUNTAMENTO" if event_type == "Appuntamento" else "",
            "zone": "Livorno" if event_type == "Ritiro" else "", "destination_site": "Livorno" if event_type == "Ritiro in sede" else "",
            "location_type": "Veterinario" if event_type == "Ritiro" else "", "address": "Via Test 1" if event_type == "Ritiro" else "",
            "start_date": "2026-11-05", "start_time": "09:30", "end_date": "2026-11-05", "end_time": "10:30",
            "event_status": "Da confermare", "payment_status": "Da pagare", "payment_amount": "0",
            "client_first_name": "Elena", "client_last_name": "Pantani", "client_phone": "3288260640",
            "animals_json": '[{"name":"","species":"Gatto","weight":"2","cremation_type":"Singola"}]', "estimate_json": "[]",
        }
        data.update(changes)
        return data

    def save(self, form, event_id=None):
        self.handler.form = lambda: form
        self.handler.path = f"/calendario/{event_id}/modifica" if event_id else "/calendario/nuovo"
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(self.admin, event_id)
        return int(self.redirects[-1].rsplit("/", 1)[-1])

    def event(self, event_id):
        with app.db() as conn:
            return conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()

    def practice(self, pid):
        with app.db() as conn:
            return conn.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()

    def test_new_column_is_added_and_existing_events_start_empty(self):
        with app.db() as conn:
            columns = {row[1]: row for row in conn.execute("PRAGMA table_info(calendar_events)")}
        self.assertIn("operational_tags", columns)
        event_id = self.save(self.event_form())
        self.assertEqual(self.event(event_id)["operational_tags"], "")

    def test_form_offers_the_tags_only_for_pickups_and_saves_them(self):
        self.handler.path = "/calendario/nuovo"
        self.handler.calendar_event_form(self.admin)
        page = self.pages[-1]
        self.assertIn('class="section collapsible collapsed tone-orange calendar-event-tags" data-calendar-types="Ritiro|Ritiro in sede"', page)
        for key, label, cls in app.EVENT_OPERATIONAL_TAGS:
            self.assertIn(f'<input type="checkbox" name="{key}" value="Si" >', page)
            self.assertIn(f'<span class="badge {cls}">{label}</span>', page)
        event_id = self.save(self.event_form(tag_calco="Si", tag_saluto="Si", tag_inventato="Si"))
        self.assertEqual(self.event(event_id)["operational_tags"], "tag_saluto,tag_calco")
        in_sede = self.save(self.event_form("Ritiro in sede", tag_pelo="Si"))
        self.assertEqual(self.event(in_sede)["operational_tags"], "tag_pelo")
        reminder = self.save(self.event_form("Appuntamento", tag_pelo="Si", animals_json="[]"))
        self.assertEqual(self.event(reminder)["operational_tags"], "")
        # In modifica e nel dettaglio le etichette salvate risultano scelte.
        self.handler.path = f"/calendario/{event_id}/modifica"
        self.handler.calendar_event_form(self.admin, event_id)
        self.assertIn('<input type="checkbox" name="tag_calco" value="Si" checked>', self.pages[-1])
        self.assertIn('<input type="checkbox" name="tag_pelo" value="Si" >', self.pages[-1])
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertIn('<b>Etichette operative</b><small><span class="badge tag-purple">SALUTO</span> <span class="badge tag-yellow">CALCO</span> </small>', self.pages[-1])

    def test_copy_keeps_the_tags(self):
        event_id = self.save(self.event_form(tag_calco_paw="Si"))
        self.handler.path = f"/calendario/nuovo?copia={event_id}"
        self.handler.calendar_event_form(self.admin)
        self.assertIn('<input type="checkbox" name="tag_calco_paw" value="Si" checked>', self.pages[-1])

    def create_practice_from_event(self, event_id):
        self.handler.path = f"/nuova?calendar_event_id={event_id}"
        self.handler.new_page(self.admin)
        page = self.pages[-1]
        form = {"calendar_event_id": str(event_id), "operator_name": "FILIPPO", "service_type": "Cremazione singola", "request_origin": "Privato",
                "provenance": "L", "owner_first_name": "Elena", "owner_last_name": "Pantani", "owner_phone": "3288260640", "owner_tax_code": "X",
                "owner_street": "Via", "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100", "species": "Gatto"}
        # Il modulo della pratica arriva gia' con le etichette dell'evento scelte.
        for key, _, _ in app.EVENT_OPERATIONAL_TAGS:
            if f'name="{key}" value="Si" checked' in page:
                form[key] = "Si"
        self.handler.form = lambda: form
        self.handler.create_practice(self.admin)
        return int(self.redirects[-1].rsplit("/", 1)[-1]), page

    def test_practice_created_from_the_event_starts_with_its_tags(self):
        event_id = self.save(self.event_form(tag_calco="Si", tag_assistita="Si"))
        pid, page = self.create_practice_from_event(event_id)
        self.assertIn('name="tag_calco" value="Si" checked', page)
        self.assertIn('name="tag_assistita" value="Si" checked', page)
        self.assertNotIn('name="tag_pelo" value="Si" checked', page)
        practice = self.practice(pid)
        self.assertEqual((practice["tag_calco"], practice["tag_assistita"], practice["tag_pelo"] or ""), ("Si", "Si", ""))
        self.assertEqual(self.event(event_id)["linked_practice_id"], pid)

    def test_changing_tags_on_a_linked_event_updates_only_the_difference_in_the_practice(self):
        event_id = self.save(self.event_form(tag_calco="Si", tag_saluto="Si"))
        pid, _ = self.create_practice_from_event(event_id)
        with app.db() as conn:
            # Etichetta messa solo nella pratica: non deve sparire.
            conn.execute("UPDATE practices SET tag_avvisare='Si' WHERE id=?", (pid,))
        self.save(self.event_form(tag_calco="Si", tag_pelo="Si"), event_id)
        practice = self.practice(pid)
        self.assertEqual((practice["tag_calco"], practice["tag_pelo"], practice["tag_saluto"] or "", practice["tag_avvisare"]), ("Si", "Si", "", "Si"))
        with app.db() as conn:
            history = conn.execute("SELECT new_value FROM practice_history WHERE practice_id=? AND event_type=?", (pid, "Etichette dall'evento calendario")).fetchall()
        self.assertEqual([row["new_value"] for row in history], ["+PELO, -SALUTO"])
        # Nessun cambio di etichette -> nessuna scrittura sulla pratica.
        stamp = practice["updated_at"]
        self.save(self.event_form(tag_calco="Si", tag_pelo="Si", notes="altra nota"), event_id)
        self.assertEqual(self.practice(pid)["updated_at"], stamp)


if __name__ == "__main__":
    unittest.main()
