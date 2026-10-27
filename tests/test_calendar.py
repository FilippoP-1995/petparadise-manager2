import json
import re
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
            "location_type": "Veterinario" if event_type == "Ritiro" else "",
            "address": "Via Test 1" if event_type == "Ritiro" else "",
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

    # --- Calendario ridisegnato (28/07): Giorno e Settimana mostrano la barra
    # dei giorni con una pagina per ciascun giorno della settimana (stesse card
    # in entrambe le viste); il Mese mostra solo i conteggi. Questi aiuti
    # isolano la pagina di un giorno e la card di un evento.
    def render_calendar(self, view, day):
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = f"/calendario?vista={view}&data={day}"
        self.handler.calendar_page(self.admin)
        return rendered[-1]

    @staticmethod
    def day_page(page, day):
        match = re.search(rf'<div class="calendar-day-page" data-day-index="\d+" data-date="{day}">', page)
        rest = page[match.end():]
        end = rest.find('<div class="calendar-day-page')
        return rest if end < 0 else rest[:end]

    @staticmethod
    def card(html, event_id):
        start = html.index(f'data-event-id="{event_id}"')
        return html[start:html.index("</article>", start)]

    @staticmethod
    def card_times(card):
        start = re.search(r'<span class="calendar-appt-time">([^<]*)</span>', card).group(1)
        end = re.search(r'<span class="calendar-appt-time-end">→ ([^<]*)</span>', card)
        return start, end.group(1) if end else None

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
        self.assertEqual((delivery["zone"],delivery["title"]),("Livorno","RICONSEGNA FIDO LIVORNO"))
        delivery_in_sede=normalize_event(self.event_form("Riconsegna in sede",zone="Livorno"))
        self.assertEqual(delivery_in_sede["zone"],"")
        appointment=normalize_event(self.event_form("Appuntamento",zone="Livorno"))
        self.assertEqual(appointment["zone"],"")
        self.assertEqual(parse_items(json.dumps([{"name":"","species":"","weight":"","cremation_type":"","notes":""}]),"animal"),[])
        with self.assertRaisesRegex(ValueError,"luogo del ritiro"):
            normalize_event(self.event_form("Ritiro",location_type=""))
        # L'indirizzo non e' piu' obbligatorio in creazione (richiesta
        # esplicita dell'utente): puo' restare vuoto e venir compilato dopo.
        pickup_no_address=normalize_event(self.event_form("Ritiro",address=""))
        self.assertEqual(pickup_no_address["address"],"")
        pickup_in_sede=normalize_event(self.event_form("Ritiro in sede",location_type="",address=""))
        self.assertEqual((pickup_in_sede["location_type"],pickup_in_sede["address"]),("",""))

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

    # --- Copia evento (richiesta esplicita dell'utente, 26/10/2026): il nuovo
    # evento parte con tutto il contenuto del dettaglio dell'originale, tutto
    # modificabile prima di salvare; niente pratica collegata (scelta
    # dell'utente), commenti e storico restano dell'originale.
    def copy_source_event(self):
        with app.db() as conn:
            urn = conn.execute("SELECT id,name FROM urns WHERE active=1 AND category='Urna' ORDER BY id LIMIT 1").fetchone()
        form = self.event_form(
            "Ritiro", operator_name="Alessio", event_status="Da ritirare", location_type="Privato",
            address="Via delle Prove 3, Livorno", client_first_name="Anna", client_last_name="Bianchi",
            client_phone="3335551234", notes="Citofonare Rossi",
            start_date="2026-11-05", start_time="15:00", end_date="2026-11-05", end_time="15:30",
            animals_json=json.dumps([{"name": "Fido", "species": "Cane", "weight": "12", "cremation_type": "Singola"},
                                     {"name": "Micia", "species": "Gatto", "weight": "4", "cremation_type": "Collettiva"}]),
            estimate_json=json.dumps([{"preset": "Cremazione", "description": "Cremazione", "amount": "220"},
                                      {"preset": "Urna", "description": urn["name"], "amount": "90", "urn_catalog_id": urn["id"]}]),
        )
        event_id = self.save(form)
        with app.db() as conn:
            pid = conn.execute("""INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at)
                                  VALUES('CR-COPIA','Privato','Livorno','Ritirato',?,?)""", (app.now(), app.now())).lastrowid
            conn.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, event_id))
        return event_id, urn

    def render_form(self, path):
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = path
        self.handler.calendar_event_form(self.admin)
        return rendered[-1]

    def test_copy_event_opens_new_event_form_with_everything_from_the_detail(self):
        event_id, urn = self.copy_source_event()
        page = self.render_form(f"/calendario/nuovo?copia={event_id}")
        self.assertIn("<h1>Copia evento</h1>", page)
        self.assertIn("Copia di «", page)
        self.assertIn(f'data-copy-of="{event_id}"', page)
        # Si salva come evento nuovo, non come modifica dell'originale.
        self.assertIn('method="post" action="/calendario/nuovo"', page)
        for field, value in (("client_first_name", "Anna"), ("client_last_name", "Bianchi"), ("client_phone", "3335551234"),
                             ("address", "Via delle Prove 3, Livorno"), ("start_date", "2026-11-05"), ("end_date", "2026-11-05")):
            self.assertRegex(page, rf'name="{field}"[^>]*value="{re.escape(value)}"', field)
        self.assertRegex(page, r'name="start_time"[^>]*value="15:00"')
        self.assertRegex(page, r'name="end_time"[^>]*value="15:30"')
        self.assertIn('<textarea name="notes">Citofonare Rossi', page)
        self.assertIn("<option selected>Alessio", page)
        self.assertIn("<option selected>Da ritirare", page)
        self.assertNotRegex(page, r'name="all_day" value="1" checked')
        # Animali e voci di preventivo (con l'urna del catalogo) come nell'originale.
        self.assertIn('calendarAddRow(\'animal\',{"name": "Fido", "species": "Cane", "weight": "12", "cremation_type": "Singola"', page)
        self.assertIn('calendarAddRow(\'animal\',{"name": "Micia", "species": "Gatto", "weight": "4", "cremation_type": "Collettiva"', page)
        self.assertIn('"description": "Cremazione", "amount": 220.0, "preset": "Cremazione"', page)
        self.assertIn(f'"amount": 90.0, "preset": "Urna", "urn_catalog_id": {urn["id"]}', page)
        # Nessuna pratica collegata nella copia.
        self.assertNotRegex(page, r'name="linked_practice_id" value="\d+"')

    def test_saving_a_copy_creates_a_new_event_and_leaves_the_original_untouched(self):
        event_id, _ = self.copy_source_event()
        with app.db() as conn:
            source = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
            source_animals = [tuple(r) for r in conn.execute("SELECT name,species,weight,cremation_type FROM calendar_event_animals WHERE event_id=? ORDER BY id", (event_id,))]
            source_items = [tuple(r) for r in conn.execute("SELECT description,amount,preset,urn_catalog_id FROM calendar_event_estimate_items WHERE event_id=? ORDER BY sort_order,id", (event_id,))]
        # Dati come li invia il modulo della copia, con la data cambiata dall'utente.
        form = self.handler.calendar_event_base_form(source)
        form.update({"all_day": "", "start_date": "2026-11-12", "end_date": "2026-11-12",
                     "animals_json": json.dumps([dict(zip(("name", "species", "weight", "cremation_type"), a)) for a in source_animals]),
                     "estimate_json": json.dumps([dict(zip(("description", "amount", "preset", "urn_catalog_id"), i)) for i in source_items])})
        new_id = self.save(form)
        self.assertNotEqual(new_id, event_id)
        with app.db() as conn:
            new = conn.execute("SELECT * FROM calendar_events WHERE id=?", (new_id,)).fetchone()
            original = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
            new_animals = [tuple(r) for r in conn.execute("SELECT name,species,weight,cremation_type FROM calendar_event_animals WHERE event_id=? ORDER BY id", (new_id,))]
            new_items = [tuple(r) for r in conn.execute("SELECT description,amount,preset,urn_catalog_id FROM calendar_event_estimate_items WHERE event_id=? ORDER BY sort_order,id", (new_id,))]
        self.assertEqual((new["start_at"], new["end_at"]), ("2026-11-12T15:00:00", "2026-11-12T15:30:59"))
        for column in ("event_type", "operator_name", "event_status", "client_first_name", "client_last_name", "client_phone", "address", "notes", "location_type"):
            self.assertEqual(new[column], source[column], column)
        self.assertEqual(new_animals, source_animals)
        self.assertEqual(new_items, source_items)
        self.assertIsNone(new["linked_practice_id"])
        self.assertEqual(dict(original), dict(source))

    def test_copy_event_is_offered_in_detail_menu_and_card_menu(self):
        event_id, _ = self.copy_source_event()
        detail = []
        self.handler.send_html = lambda html, status=200: detail.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertIn(f'<a href="/calendario/nuovo?copia={event_id}">Copia evento</a>', detail[-1])
        card = self.card(self.day_page(self.render_calendar("giorno", "2026-11-05"), "2026-11-05"), event_id)
        self.assertIn(f'<a href="/calendario/nuovo?copia={event_id}">Copia evento</a>', card)

    def test_copy_of_missing_or_deleted_event_opens_an_ordinary_new_event_form(self):
        event_id, _ = self.copy_source_event()
        with app.db() as conn:
            conn.execute("UPDATE calendar_events SET deleted_at=? WHERE id=?", (app.now(), event_id))
        for path in (f"/calendario/nuovo?copia={event_id}", "/calendario/nuovo?copia=999999", "/calendario/nuovo?copia=abc"):
            page = self.render_form(path)
            self.assertIn("<h1>Nuovo evento</h1>", page, path)
            self.assertNotIn("data-copy-of=", page, path)

    def test_edit_and_copy_move_the_end_keeping_the_duration(self):
        # Richiesta esplicita dell'utente (27/10/2026): in Modifica, in Copia e
        # nel riquadro "Data e ora" del dettaglio la fine segue l'inizio
        # mantenendo la durata (15:00-15:30 spostato alle 16:00 -> 16:00-16:30).
        js = app.APP_JS
        self.assertIn("function ppmInitEventKeepDuration(form,eventTypes,afterShift){", js)
        self.assertIn("if((form.dataset.draftKey||'').includes('_edit_')||form.dataset.copyOf){\n    // Modifica e Copia", js)
        self.assertIn("document.querySelectorAll('form[data-event-keep-duration]')", js)
        self.assertNotIn("form.end_time.dataset.manualEdit='1';\n  }", js)

    def test_keep_duration_script_shifts_end_time_and_date(self):
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node non disponibile")
        js = app.APP_JS
        start = js.index("function ppmEventMinutes(")
        end = js.index("document.addEventListener('DOMContentLoaded',()=>{\n  document.querySelectorAll('form[data-event-keep-duration]')")
        script = js[start:end] + """
function field(value){const listeners={};return {value,addEventListener(type,fn){(listeners[type]=listeners[type]||[]).push(fn);},fire(type){(listeners[type]||[]).forEach(fn=>fn());}};}
const out=[];
function run(sd,st,ed,et,nsd,nst,type){
  const form={start_date:field(sd),start_time:field(st),end_date:field(ed),end_time:field(et)};
  ppmInitEventKeepDuration(form,[type]);
  form.start_date.value=nsd;form.start_date.fire(type);
  form.start_time.value=nst;form.start_time.fire(type);
  out.push([form.end_date.value,form.end_time.value]);
}
run('2026-10-27','15:00','2026-10-27','15:30','2026-10-27','16:00','change');
run('2026-10-27','15:00','2026-10-27','15:30','2026-10-27','14:00','input');
run('2026-10-27','23:30','2026-10-28','00:15','2026-10-27','23:50','change');
run('2026-10-27','09:00','2026-10-29','18:00','2026-11-02','09:00','change');
run('2026-03-28','10:00','2026-03-28','11:00','2026-03-29','10:00','change');
console.log(JSON.stringify(out));
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            ["2026-10-27", "16:30"],
            ["2026-10-27", "14:30"],
            ["2026-10-28", "00:35"],
            ["2026-11-04", "18:00"],
            ["2026-03-29", "11:00"],
        ])

    def test_detail_datetime_form_keeps_the_duration(self):
        event_id = self.save(self.event_form("Appuntamento", start_time="15:00", end_time="15:30"))
        pages = []
        self.handler.send_html = lambda html, status=200: pages.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertIn(f'<form method="post" action="/calendario/{event_id}/data-ora" data-event-keep-duration>', pages[-1])

    def detail_page(self, event_id):
        pages = []
        self.handler.send_html = lambda html, status=200: pages.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        return pages[-1]

    @staticmethod
    def quick_message_phones(html):
        return [json.loads(app.html.unescape(m))["phone"] for m in re.findall(r'data-qwa="([^"]*)"', html)]

    def test_quick_messages_use_the_client_number_not_the_clinic(self):
        # Richiesta esplicita dell'utente (27/10/2026): riconsegna presso una
        # clinica con pratica collegata -> i messaggi preimpostati vanno al
        # cliente, mai alla clinica.
        stamp = app.now()
        with app.db() as conn:
            vet_id = conn.execute(
                "INSERT INTO veterinarians(clinic_name,short_name,phone,address,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                ("Clinica Faro", "Faro", "0586111111", "Via Faro 1", stamp, stamp)).lastrowid
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,species,owner_first_name,owner_last_name,owner_phone,payment_status)
                   VALUES('CR-QWA','Veterinario','Livorno','In programma',?,?,?,'Fido','Cane','Mario','Rossi','3471234567','Da saldare')""",
                (stamp, stamp, self.admin["id"])).lastrowid
        event_id = self.save(self.event_form("Riconsegna", start_date="2026-11-05", end_date="2026-11-05",
                                             delivery_clinic_id=str(vet_id), delivery_clinic_name="Clinica Faro"))
        with app.db() as conn:
            conn.execute("UPDATE calendar_events SET linked_practice_id=?,client_phone='',phone='' WHERE id=?", (pid, event_id))
        # Dalla pratica collegata (l'evento non ha il numero del cliente).
        self.assertEqual(self.quick_message_phones(self.detail_page(event_id)), ["393471234567"])
        card = self.card(self.day_page(self.render_calendar("giorno", "2026-11-05"), "2026-11-05"), event_id)
        self.assertEqual(self.quick_message_phones(card), ["393471234567"])
        # Il numero del cliente scritto sull'evento ha la precedenza.
        with app.db() as conn:
            conn.execute("UPDATE calendar_events SET client_phone='3339998877' WHERE id=?", (event_id,))
        self.assertEqual(self.quick_message_phones(self.detail_page(event_id)), ["393339998877"])

    def test_quick_messages_fall_back_to_the_client_record_and_are_disabled_without_a_client_number(self):
        stamp = app.now()
        with app.db() as conn:
            vet_id = conn.execute(
                "INSERT INTO veterinarians(clinic_name,short_name,phone,address,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                ("Clinica Faro", "Faro", "0586111111", "Via Faro 1", stamp, stamp)).lastrowid
            client_id = conn.execute(
                "INSERT INTO clients(first_name,last_name,phone,created_at,updated_at) VALUES('Anna','Neri','3401112233',?,?)",
                (stamp, stamp)).lastrowid
        event_id = self.save(self.event_form("Riconsegna", start_date="2026-11-05", end_date="2026-11-05",
                                             delivery_clinic_id=str(vet_id), delivery_clinic_name="Clinica Faro"))
        with app.db() as conn:
            conn.execute("UPDATE calendar_events SET client_id=?,client_phone='',phone='' WHERE id=?", (client_id, event_id))
        self.assertEqual(self.quick_message_phones(self.detail_page(event_id)), ["393401112233"])
        card = self.card(self.day_page(self.render_calendar("giorno", "2026-11-05"), "2026-11-05"), event_id)
        self.assertEqual(self.quick_message_phones(card), ["393401112233"])
        # Nessun numero del cliente: pulsante disattivato, mai la clinica.
        with app.db() as conn:
            conn.execute("UPDATE calendar_events SET client_id=NULL WHERE id=?", (event_id,))
        page = self.detail_page(event_id)
        self.assertEqual(self.quick_message_phones(page), [])
        self.assertIn('calendar-detail-qa-disabled"><span class="calendar-detail-qa-icon">', page)
        self.assertIn('href="tel:0586111111"', page)  # Chiama resta com'era
        card = self.card(self.day_page(self.render_calendar("giorno", "2026-11-05"), "2026-11-05"), event_id)
        self.assertEqual(self.quick_message_phones(card), [])
        self.assertIn("<button type=\"button\" disabled title=\"Manca il numero di telefono del cliente\">", card)

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
        self.assertIn("Cerca cliente",rendered[-1])
        self.assertIn("Cerca veterinario",rendered[-1])
        self.assertIn("calendarAddRow('animal',{})",rendered[-1])
        self.assertIn("function calendarPickupClientLookup(",app.APP_JS)
        self.assertNotIn("Veterinario/Ambulatorio",app.APP_JS)
        # Uscita dal modulo: non piu' l'avviso generico con history.back(),
        # ma la scelta esplicita sulla bozza (Salva bozza ed esci / Elimina
        # ed esci / Annulla), introdotta il 31/07.
        self.assertIn('id="calendarExitBackdrop"',rendered[-1])
        for label,handler in (("Salva bozza ed esci","calendarExitKeepDraft("),("Elimina ed esci","calendarExitDiscardDraft("),("Annulla","calendarExitCancel(")):
            self.assertIn(label,rendered[-1])
            self.assertIn(f'onclick="{handler}',rendered[-1])
            self.assertIn(f"function {handler}",app.APP_JS)

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

    def test_edit_wizard_call_quickaction_does_not_add_a_forced_prefix(self):
        # stesso bug/fix della pagina di dettaglio evento: il "+" forzato
        # trasformava un numero italiano in un numero interpretato come
        # francese (+33) dal dialer.
        event_id = self.save(self.event_form("Ritiro", client_phone="3339990000"))
        rendered = []
        self.handler.path = f"/calendario/{event_id}/modifica"
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.calendar_event_form(self.admin, event_id)
        html = rendered[-1]
        self.assertIn("tel:3339990000", html)
        self.assertNotIn("tel:+3339990000", html)

    def test_pickup_location_block_is_hidden_only_for_ritiro_in_sede(self):
        pickup_id = self.save(self.event_form("Ritiro"))
        onsite_id = self.save(self.event_form("Ritiro in sede", location_type="", address=""))
        for event_id, expect_location_block in ((pickup_id, True), (onsite_id, False)):
            rendered = []
            self.handler.path = f"/calendario/{event_id}/modifica"
            self.handler.send_html = lambda html, status=200: rendered.append(html)
            self.handler.calendar_event_form(self.admin, event_id)
            html = rendered[-1]
            self.assertIn("Cliente / Proprietario", html)
            self.assertIn("Nome ambulatorio di riferimento", html)
            if expect_location_block:
                self.assertIn("Luogo del ritiro", html)
                self.assertNotIn('data-calendar-types="Ritiro" hidden', html)
            else:
                self.assertIn('data-calendar-types="Ritiro" hidden', html)

    def test_title_field_hidden_for_auto_titled_types_visible_for_appuntamento(self):
        # Il titolo si genera da solo (zona/sede/animale): mostrarlo comunque
        # come campo libero per Ritiro/Ritiro in sede/Riconsegna/Riconsegna
        # in sede creerebbe due fonti di verita' in conflitto. Resta un
        # campo di testo libero e obbligatorio solo per "Appuntamento", che
        # non ha zona/sede/animale da cui derivarlo.
        for kind, should_be_visible in (
            ("Ritiro", False), ("Ritiro in sede", False),
            ("Riconsegna", False), ("Riconsegna in sede", False),
            ("Appuntamento", True),
        ):
            rendered = []
            self.handler.path = "/calendario/nuovo"
            self.handler.send_html = lambda html, status=200: rendered.append(html)
            self.handler.calendar_event_form(self.admin, draft=self.event_form(kind))
            html = rendered[-1]
            if should_be_visible:
                self.assertNotIn('data-calendar-types="Appuntamento" hidden', html)
            else:
                self.assertIn('data-calendar-types="Appuntamento" hidden', html)
            self.assertIn('name="title"', html)

    def test_pickup_location_pills_map_ambulatorio_domicilio_altro_indirizzo(self):
        # "Luogo recupero" e' ora a pillole (Ambulatorio/Domicilio/Altro
        # indirizzo) invece di un <select>; Domicilio e Altro indirizzo sono
        # solo due etichette per lo stesso valore location_type="Privato"
        # (stesso campo indirizzo libero, nessuna colonna nuova).
        rendered = []
        self.handler.path = "/calendario/nuovo"
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.calendar_event_form(self.admin, draft=self.event_form("Ritiro", location_type=""))
        html = rendered[-1]
        self.assertIn('data-pickup-pill="Veterinario"', html)
        self.assertEqual(html.count('data-pickup-pill="Privato"'), 2)
        self.assertIn(">Ambulatorio<", html)
        self.assertIn(">Domicilio<", html)
        self.assertIn(">Altro indirizzo<", html)
        self.assertIn("function calendarPickupPillClick(", app.APP_JS)

    def test_animal_search_returns_payment_method_service_type_site_and_urn(self):
        stamp = datetime.now().isoformat(timespec="seconds")
        with app.db() as conn:
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,service_type,payment_method,pickup_date)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-URN-01", "Privato", "Empoli", "Ritirato", stamp, stamp, self.admin["id"],
                 "Briciola", "Cremazione singola", "Contanti", "2026-07-10"),
            ).lastrowid
            conn.execute(
                "INSERT INTO practice_items(practice_id,category,label,price,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (pid, "urna", "Urna bianca classica", "50", stamp, stamp),
            )
        response = {}
        self.handler.path = "/api/calendario/animali/search?q=briciola"
        self.handler.send_json = lambda obj, status=200: response.update(obj=obj, status=status)
        self.handler.api_calendar_animals_search(self.admin)
        result = response["obj"]["results"][0]
        self.assertEqual(result["payment_method"], "Contanti")
        self.assertEqual(result["service_type"], "Singola")
        self.assertEqual(result["destination_branch"], "Empoli")
        self.assertEqual(result["urn_summary"], "Urna bianca classica")

    def test_delivery_wizard_shows_dettagli_pratica_summary_card(self):
        rendered = []
        self.handler.path = "/calendario/nuovo"
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.calendar_event_form(self.admin, draft=self.event_form("Riconsegna"))
        html = rendered[-1]
        for marker in (
            "data-delivery-practice-summary", "data-delivery-total", "data-delivery-deposit",
            "data-delivery-remaining", "data-delivery-service", "data-delivery-urn",
        ):
            self.assertIn(marker, html)
        self.assertIn("Cerca pratica, animale o cliente", html)
        self.assertIn("data-delivery-practice-summary", app.APP_JS)

    def test_delivery_wizard_shows_channel_letter_on_amount_when_still_due(self):
        # richiesta esplicita dell'utente: quando si fissa una riconsegna
        # partendo da una pratica, la riga "Importo" deve mostrare anche
        # la sigla del circuito (W/D) se c'e' ancora da pagare.
        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,owner_first_name,owner_last_name,payment_status,total_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-WIZ-D", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"],
                 "Birba", "Mario", "Rossi", "Da saldare", "150"),
            ).lastrowid
        pages = []
        self.handler.path = f"/calendario/nuovo?linked_practice_id={pid}"
        self.handler.send_html = lambda html, status=200: pages.append(html)
        self.handler.calendar_event_form(self.admin)
        self.assertIn("<small>Importo D</small>", pages[0])

    def test_delivery_wizard_hides_channel_letter_when_already_paid(self):
        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,owner_first_name,owner_last_name,payment_status,total_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-WIZ-PAID", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"],
                 "Birba", "Mario", "Rossi", "Pagato", "150"),
            ).lastrowid
        pages = []
        self.handler.path = f"/calendario/nuovo?linked_practice_id={pid}"
        self.handler.send_html = lambda html, status=200: pages.append(html)
        self.handler.calendar_event_form(self.admin)
        self.assertIn("<small>Importo</small>", pages[0])
        self.assertNotIn("<small>Importo D</small>", pages[0])

    def test_delivery_edit_form_shows_channel_letter_from_linked_practice(self):
        event_id = self.save(self.event_form("Riconsegna"))
        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,owner_first_name,owner_last_name,payment_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-EDIT-W", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"],
                 "Birba", "Mario", "Rossi", "Da saldare"),
            ).lastrowid
            conn.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, event_id))
        pages = []
        self.handler.path = f"/calendario/{event_id}/modifica"
        self.handler.send_html = lambda html, status=200: pages.append(html)
        self.handler.calendar_event_form(self.admin, event_id)
        self.assertIn("<small>Importo W</small>", pages[0])

    def test_new_event_notification_title_is_specific_to_event_type_and_location(self):
        # richiesta esplicita dell'utente: il banner della notifica deve
        # mostrare subito il tipo di ritiro/riconsegna e dove si svolge
        # (in sede o zona), senza dover aprire il gestionale — niente piu'
        # titolo generico "Nuovo evento calendario" con solo l'emoji a
        # distinguere il tipo.
        expected_titles = {
            "Ritiro": "Ritiro Livorno", "Ritiro in sede": "Ritiro in sede Livorno",
            "Riconsegna": "Riconsegna Livorno", "Riconsegna in sede": "Riconsegna in sede Livorno",
            "Appuntamento": "📅 Nuovo evento calendario",
        }
        for event_type, expected_title in expected_titles.items():
            self.handler.form = lambda event_type=event_type: self.event_form(event_type)
            with patch("app.emit_notification", return_value=[]) as mock_emit:
                self.handler.save_calendar_event(self.admin)
            kind, title = mock_emit.call_args.args[1], mock_emit.call_args.args[2]
            self.assertEqual(kind, "calendar_event_created")
            self.assertEqual(title, expected_title, f"{event_type}: {title!r}")

    def test_new_and_updated_in_sede_notification_title_uses_the_actual_site(self):
        # richiesta esplicita dell'utente: il titolo deve riportare la
        # sede vera dell'evento (Livorno/Empoli), non solo "in sede".
        form = self.event_form("Riconsegna in sede", destination_site="Empoli")
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        title = mock_emit.call_args.args[2]
        self.assertEqual(title, "Riconsegna in sede Empoli")
        event_id = int(self.redirected.rsplit("/", 1)[-1])

        self.handler.form = lambda: self.event_form("Riconsegna in sede", destination_site="Empoli", payment_status="Pagato")
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin, event_id)
        title = mock_emit.call_args.args[2]
        self.assertEqual(title, "Riconsegna in sede Empoli")

    def test_new_pickup_notification_body_shows_species_weight_and_time(self):
        form = self.event_form("Ritiro", animals_json=json.dumps([
            {"name": "Birba", "species": "Cane", "weight": "18", "notes": ""},
        ]))
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        text = mock_emit.call_args.args[3]
        self.assertEqual(text, "Cane • 18 kg\n15/07/2026 09:30 - 10:30")

    def test_new_pickup_in_sede_notification_omits_missing_end_time(self):
        form = self.event_form("Ritiro in sede", start_time="14:00", end_time="14:00", animals_json=json.dumps([
            {"name": "Micio", "species": "Gatto", "weight": "4", "notes": ""},
        ]))
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        title = mock_emit.call_args.args[2]
        text = mock_emit.call_args.args[3]
        self.assertEqual(title, "Ritiro in sede Livorno")
        self.assertEqual(text, "Gatto • 4 kg\n15/07/2026 14:00")

    def test_promemoria_notifications_show_date_and_time_like_other_events(self):
        # richiesta esplicita dell'utente: nelle notifiche di un promemoria
        # (evento "Appuntamento") deve esserci data e ora, come nelle altre
        # notifiche di evento - stessa funzione calendar_push_time_range.
        self.handler.form = lambda: self.event_form("Appuntamento", title="RIUNIONE FORNITORE")
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        kind, title, text = mock_emit.call_args.args[1], mock_emit.call_args.args[2], mock_emit.call_args.args[3]
        self.assertEqual(kind, "calendar_event_created")
        self.assertEqual(title, "📅 Nuovo evento calendario")
        self.assertEqual(text, "RIUNIONE FORNITORE\n15/07/2026 09:30 - 10:30")
        event_id = int(self.redirected.rsplit("/", 1)[-1])

        # modifica: stesso formato, con la data/ora nuove
        self.handler.form = lambda: self.event_form("Appuntamento", title="RIUNIONE FORNITORE", start_date="2026-07-16", end_date="2026-07-16", start_time="14:00", end_time="14:00")
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin, event_id)
        kind, title, text = mock_emit.call_args.args[1], mock_emit.call_args.args[2], mock_emit.call_args.args[3]
        self.assertEqual(kind, "calendar_event_updated")
        self.assertEqual(title, "📅 Evento calendario aggiornato")
        self.assertEqual(text, "RIUNIONE FORNITORE\n16/07/2026 14:00")

    def test_promemoria_all_day_notification_says_tutto_il_giorno_not_a_fake_time(self):
        self.handler.form = lambda: self.event_form("Appuntamento", title="FERIE", all_day="1", start_date="2026-07-15", end_date="2026-07-15")
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        text = mock_emit.call_args.args[3]
        self.assertEqual(text, "FERIE\n15/07/2026 · Tutto il giorno")
        self.assertNotIn("00:00", text)
        self.assertNotIn("23:59", text)
        self.handler.form = lambda: self.event_form("Appuntamento", title="FERIE", all_day="1", start_date="2026-07-15", end_date="2026-07-18")
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        self.assertEqual(mock_emit.call_args.args[3], "FERIE\n15/07/2026 → 18/07/2026 · Tutto il giorno")

    def test_promemoria_notification_is_really_stored_with_date_and_time(self):
        # senza mock: la notifica realmente salvata (quella che vede
        # l'utente) contiene titolo + data e ora, come un ritiro.
        self.handler.form = lambda: self.event_form("Appuntamento", title="CHIAMARE FORNITORE")
        self.handler.save_calendar_event(self.admin)
        with app.db() as conn:
            rows = conn.execute("SELECT text FROM notification_group_items WHERE title LIKE '%Nuovo evento calendario%' ORDER BY id").fetchall()
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["text"], "CHIAMARE FORNITORE\n15/07/2026 09:30 - 10:30")

    def test_appointment_push_text_uses_the_shared_date_time_formatter(self):
        from calendar_service import calendar_appointment_push_text, calendar_push_time_range
        start, end = "2026-07-15T09:30:00", "2026-07-15T10:30:00"
        self.assertEqual(calendar_appointment_push_text("X", start, end), f"X\n{calendar_push_time_range(start, end)}")
        self.assertEqual(calendar_appointment_push_text("", start, end), "15/07/2026 09:30 - 10:30")
        self.assertEqual(calendar_appointment_push_text("X", "", ""), "X")

    def test_pickup_and_delivery_notification_bodies_are_unchanged_by_promemoria_change(self):
        form = self.event_form("Riconsegna")
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        self.assertEqual(mock_emit.call_args.args[3], "Fido\n15/07/2026 09:30 - 10:30\nPagamento: DA SALDARE")

    def test_new_pickup_notification_body_shows_cremation_type_when_selected(self):
        # richiesta esplicita dell'utente: nei ritiri, sia in sede che non,
        # la notifica deve mostrare anche il tipo di cremazione selezionata.
        form = self.event_form("Ritiro", animals_json=json.dumps([
            {"name": "Birba", "species": "Cane", "weight": "18", "cremation_type": "Singola", "notes": ""},
        ]))
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        text = mock_emit.call_args.args[3]
        self.assertEqual(text, "Cane • 18 kg • Singola\n15/07/2026 09:30 - 10:30")

        form = self.event_form("Ritiro in sede", animals_json=json.dumps([
            {"name": "Micio", "species": "Gatto", "weight": "4", "cremation_type": "Collettiva", "notes": ""},
        ]))
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        text = mock_emit.call_args.args[3]
        self.assertEqual(text, "Gatto • 4 kg • Collettiva\n15/07/2026 09:30 - 10:30")

    def test_new_delivery_notification_shows_animal_and_payment_status(self):
        form = self.event_form("Riconsegna", payment_status="Pagato")
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        title = mock_emit.call_args.args[2]
        text = mock_emit.call_args.args[3]
        self.assertEqual(title, "Riconsegna Livorno")
        self.assertEqual(text, "Fido\n15/07/2026 09:30 - 10:30\nPagamento: SALDATO")

    def test_new_delivery_notification_shows_da_saldare_when_not_paid(self):
        form = self.event_form("Riconsegna in sede", payment_status="Da saldare")
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin)
        text = mock_emit.call_args.args[3]
        self.assertEqual(text, "Fido\n15/07/2026 09:30 - 10:30\nPagamento: DA SALDARE")

    def test_updated_pickup_notification_shows_new_status_only_when_it_changed(self):
        pid = self.save(self.event_form("Ritiro", event_status="Da confermare"))
        form = self.event_form("Ritiro", event_status="Ritirato")
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin, pid)
        kind, title, text = mock_emit.call_args.args[1], mock_emit.call_args.args[2], mock_emit.call_args.args[3]
        self.assertEqual(kind, "calendar_event_updated")
        self.assertEqual(title, "Ritiro Livorno")
        self.assertIn("Stato: RITIRATO", text)

        # nessun cambio di stato: la riga "Stato:" non compare
        self.handler.form = lambda: self.event_form("Ritiro", event_status="Ritirato", zone="Pisa")
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.save_calendar_event(self.admin, pid)
        text = mock_emit.call_args.args[3]
        self.assertNotIn("Stato:", text)

    def test_payment_received_notification_shows_channel_letter(self):
        # richiesta esplicita dell'utente: il banner "Pagamento ricevuto"
        # deve mostrare anche la sigla del circuito (W/D), mantenendo il
        # nome del proprietario gia' presente oggi.
        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,owner_first_name,owner_last_name,payment_status,total_service,total_service_manual)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-CHANNEL-01", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"],
                 "Birba", "Mario", "Rossi", "Da saldare", "150", "Si"),
            ).lastrowid
        self.handler.form = lambda: {"payment_status": "Pagato", "payment_method": "Pos", "payment_amount": "150",
                                      "invoice_number": "", "invoice_total": "", "invoice_date": "",
                                      "economic_at": "2026-07-14", "payment_channel": "W"}
        with patch("app.emit_notification", return_value=[]) as mock_emit:
            self.handler.quick_payment(self.admin, pid)
        text = mock_emit.call_args.args[3]
        self.assertEqual(text, "Mario Rossi • W • € 150,00")

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

        card = self.card(self.day_page(self.render_calendar("giorno", "2026-07-15"), "2026-07-15"), event_id)
        # Sulla card il luogo e' l'ambulatorio di riconsegna (una sola riga),
        # e tutta la card apre il dettaglio dell'evento.
        self.assertIn('<div class="calendar-appt-location">Ambulatorio Riconsegna</div>', card)
        self.assertEqual(card.count('class="calendar-appt-location"'), 1)
        self.assertIn(f"location.href='/calendario/{event_id}?return_to='", card)

        pages = []
        self.handler.send_html = lambda html, status=200: pages.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertIn("Ambulatorio Riconsegna", pages[-1])
        self.assertIn("Livorno", pages[-1])

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
        self.assertIn("Cerca ambulatorio", rendered[-1])
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
        pages = {view: self.render_calendar(view, "2026-07-15")
                 for view in ("giorno", "settimana", "mese", "mista_settimana", "mista_mese", "compatto")}
        for view, page in pages.items():
            self.assertIn("Calendario operativo", page, view)
            self.assertEqual(page.count('data-calendar-view="'), 3, view)
            for label in ("Giorno", "Settimana", "Mese"):
                self.assertIn(f">{label}</a>", page, view)
            self.assertIn(">OGGI<", page, view)
            self.assertIn("create-sheet", page, view)
            self.assertIn("create-sheet-backdrop", page, view)
        # Giorno e Settimana: barra dei giorni e card ricche con l'animale.
        for view in ("giorno", "settimana", "mista_settimana"):
            self.assertIn('id="calendarDaybar"', pages[view], view)
            self.assertIn('class="calendar-appt-card"', pages[view], view)
            self.assertIn("18 kg", pages[view], view)
            self.assertIn("Singola", pages[view], view)
            self.assertNotIn("data-calendar-swipe", pages[view], view)
        self.assertIn("15 Luglio 2026", pages["giorno"])
        # Mese: griglia con i soli conteggi (ritiri/riconsegne), niente card.
        for view in ("mese", "mista_mese", "compatto"):
            self.assertIn('class="calendar-month-v2-grid"', pages[view], view)
            self.assertIn('calendar-month-v2-count-pickup">1<', pages[view], view)
            self.assertNotIn('class="calendar-appt-card"', pages[view], view)
        self.assertNotIn("data-calendar-swipe", app.APP_JS)
        self.assertIn("safe-bottom", app.CSS)
        # Oggi evidenziato sia nella barra dei giorni sia nella griglia del mese.
        today = datetime.now().date().isoformat()
        self.assertRegex(self.render_calendar("giorno", today), rf'class="calendar-daybar-card[^"]*is-today[^"]*" data-day-index="\d+" data-date="{today}"')
        self.assertIn("calendar-month-v2-cell is-today", self.render_calendar("mese", today))
        self.handler.path = "/"
        self.handler.send_html = lambda html, status=200: pages.update(current=html)
        self.handler.dashboard(self.admin)
        self.assertNotIn("+ Nuova pratica", pages["current"])
        self.assertNotIn("+ Nuovo evento", pages["current"])
        self.assertIn(f"{self.admin['display_name']} <span", pages["current"])

    def test_delivery_card_recomputes_amount_live_when_totale_d_added_after_event_creation(self):
        # bug reale segnalato dall'utente: l'importo salvato sull'evento e'
        # uno scatto preso alla creazione (quando Totale D era vuoto, quindi
        # si usava Totale W). Se in seguito viene compilato Totale D — che
        # per regola di business SOSTITUISCE sempre il Totale W — la card
        # deve ricalcolare l'importo dal vivo (330 D), non restare ferma al
        # vecchio importo W (410) etichettato pero' col circuito nuovo (D).
        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   owner_first_name,service_type,payment_status,total_service,total_service_manual)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-GEA", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"],
                 "Leslie", "Cremazione singola", "Da saldare", "410", "Si"),
            ).lastrowid
        event_id = self.save(self.event_form("Riconsegna", payment_status="Da pagare", payment_amount="410"))
        with app.db() as conn:
            conn.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, event_id))
            # Totale D aggiunto in un secondo momento, come nel caso reale
            conn.execute("UPDATE practices SET total_text=? WHERE id=?", ("330", pid))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = "/calendario?vista=giorno&data=2026-07-15"
        self.handler.calendar_page(self.admin)
        page = rendered[-1]
        self.assertIn("€ 330,00 D", page)
        self.assertNotIn("€ 410,00", page)

    def test_event_detail_payment_row_recomputes_amount_live_when_totale_d_added_after_event_creation(self):
        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   owner_first_name,service_type,payment_status,total_service,total_service_manual)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-GEA2", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"],
                 "Leslie", "Cremazione singola", "Da saldare", "410", "Si"),
            ).lastrowid
        event_id = self.save(self.event_form("Riconsegna", payment_status="Da pagare", payment_amount="410"))
        with app.db() as conn:
            conn.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, event_id))
            conn.execute("UPDATE practices SET total_text=? WHERE id=?", ("330", pid))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        page = rendered[-1]
        self.assertIn("Da pagare € 330,00 D", page)

    def test_day_view_shows_status_badges_and_colors_but_no_legend(self):
        ids = {
            "Da confermare": self.save(self.event_form("Ritiro", title="RITIRO FIDO", event_status="Da confermare", start_time="08:30", end_time="09:00")),
            "Da ritirare": self.save(self.event_form("Ritiro", title="RITIRO LUNA", event_status="Da ritirare", start_time="09:30", end_time="10:00")),
            "Ritirato": self.save(self.event_form("Ritiro", title="RITIRO BELLA", event_status="Ritirato", start_time="10:30", end_time="11:00")),
            "Annullato": self.save(self.event_form("Ritiro", title="RITIRO ARGO", event_status="Annullato", start_time="11:30", end_time="12:00")),
        }
        delivery_id = self.save(self.event_form("Riconsegna", title="RICONSEGNA REX", start_time="13:00", end_time="13:30"))
        reminder_id = self.save(self.event_form("Appuntamento", title="APPUNTAMENTO FORNITORE", start_time="14:00", end_time="14:30"))
        for view in ("giorno", "settimana"):
            page = self.render_calendar(view, "2026-07-15")
            day = self.day_page(page, "2026-07-15")
            colors = {}
            for status, event_id in ids.items():
                card = self.card(day, event_id)
                color = app.DEFAULT_CALENDAR_COLOR_SETTINGS["types"][status]
                # Badge di stato (solo per i ritiri) e colore dello stato sulla card.
                self.assertIn(f'>{status.upper()}</span>', card)
                self.assertIn(f'style="border-left-color:{color}"', card)
                colors[status] = color
            self.assertEqual(len(set(colors.values())), 4)
            # Riconsegne e promemoria: nessuna badge di stato.
            for event_id in (delivery_id, reminder_id):
                self.assertNotIn('class="calendar-appt-status"', self.card(day, event_id))
            # Legende colori/operatori solo nelle Impostazioni del calendario.
            body = page.split("</style>", 1)[1]
            for legend in ('<section class="calendar-legend-section">', '<div class="calendar-operator-legend">', '<div class="calendar-day-legend">'):
                self.assertNotIn(legend, body)

    def test_day_view_lists_events_in_chronological_order_without_grid_or_lanes(self):
        self.save(self.event_form("Ritiro", event_status="Da ritirare"))
        self.save(self.event_form("Appuntamento", title="APPUNTAMENTO FORNITORE", end_time="10:00"))
        self.save(self.event_form("Appuntamento", title="EVENTO ISOLATO", start_time="14:00", end_time="15:00"))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = "/calendario?vista=giorno&data=2026-07-15"
        self.handler.calendar_page(self.admin)
        page = rendered[-1]
        self.assertNotIn('style="--event-lane', page)
        self.assertNotIn('style="--event-height', page)
        self.assertNotIn('<div class="calendar-timeline-event"', page)
        self.assertNotIn('<div class="calendar-day-timeline"', page)
        ritiro_pos = page.index("RITIRO LIVORNO")
        fornitore_pos = page.index("PROMEMORIA FORNITORE")
        isolato_pos = page.index("EVENTO ISOLATO")
        self.assertLess(ritiro_pos, fornitore_pos)
        self.assertLess(fornitore_pos, isolato_pos)

    def test_day_view_same_row_structure_for_short_long_and_multiday_events(self):
        short_id = self.save(self.event_form("Appuntamento", title="EVENTO BREVE", start_time="08:10", end_time="08:20"))
        long_id = self.save(self.event_form("Appuntamento", title="EVENTO STESSO GIORNO", start_time="09:00", end_time="17:00"))
        multi_id = self.save(self.event_form("Appuntamento", title="EVENTO MULTI GIORNO",
                                             start_date="2026-07-15", start_time="09:30", end_date="2026-07-17", end_time="10:30"))
        day = self.day_page(self.render_calendar("giorno", "2026-07-15"), "2026-07-15")
        # Stessa card semplice per tutti, in ordine di orario d'inizio.
        self.assertEqual(day.count('class="calendar-appt-card"'), 3)
        self.assertNotIn('style="--event-height', day)
        positions = [day.index(f'data-event-id="{event_id}"') for event_id in (short_id, long_id, multi_id)]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(self.card_times(self.card(day, short_id)), ("08:10", "08:20"))
        self.assertEqual(self.card_times(self.card(day, long_id)), ("09:00", "17:00"))
        # Il giorno in cui inizia, l'evento su piu' giorni mostra l'inizio reale
        # fino a mezzanotte, non l'orario di fine di un altro giorno.
        self.assertEqual(self.card_times(self.card(day, multi_id)), ("09:30", "00:00"))

    def test_day_view_shows_per_day_time_window_for_multiday_event_not_date_range(self):
        # Ritiro dalle 11:00 del 22/07 alle 18:00 del 23/07: ogni giorno mostra
        # l'orario che vale in quel giorno (richiesta esplicita dell'utente,
        # 22/07, ripristinata dopo il ridisegno del calendario).
        event_id = self.save(self.event_form("Ritiro", title="RITIRO FIRENZE",
                                             start_date="2026-07-22", start_time="11:00",
                                             end_date="2026-07-23", end_time="18:00"))
        for view in ("giorno", "settimana"):
            for selected in ("2026-07-22", "2026-07-23"):
                page = self.render_calendar(view, selected)
                first = self.card(self.day_page(page, "2026-07-22"), event_id)
                last = self.card(self.day_page(page, "2026-07-23"), event_id)
                self.assertEqual(self.card_times(first), ("11:00", "00:00"), (view, selected))
                self.assertEqual(self.card_times(last), ("00:00", "18:00"), (view, selected))
                self.assertNotIn("2026-07-22 → 2026-07-23", page)
        # Un evento di un giorno solo resta com'era.
        same_day_id = self.save(self.event_form("Ritiro", title="RITIRO PISA", start_date="2026-07-22", start_time="15:00",
                                                end_date="2026-07-22", end_time="16:00"))
        day = self.day_page(self.render_calendar("giorno", "2026-07-22"), "2026-07-22")
        self.assertEqual(self.card_times(self.card(day, same_day_id)), ("15:00", "16:00"))

    def test_day_view_shows_midnight_to_midnight_on_middle_day_of_multiday_event(self):
        event_id = self.save(self.event_form("Riconsegna", title="RICONSEGNA LUNGA",
                                             start_date="2026-07-10", start_time="08:00",
                                             end_date="2026-07-13", end_time="16:00"))
        # 10/07 e' venerdi', 13/07 lunedi': due settimane diverse.
        first_week = self.render_calendar("giorno", "2026-07-11")
        self.assertEqual(self.card_times(self.card(self.day_page(first_week, "2026-07-10"), event_id)), ("08:00", "00:00"))
        for middle in ("2026-07-11", "2026-07-12"):
            self.assertEqual(self.card_times(self.card(self.day_page(first_week, middle), event_id)), ("00:00", "00:00"))
        second_week = self.render_calendar("giorno", "2026-07-13")
        self.assertEqual(self.card_times(self.card(self.day_page(second_week, "2026-07-13"), event_id)), ("00:00", "16:00"))
        # Un evento di tutto il giorno su piu' giorni resta "Tutto il giorno".
        allday_id = self.save(self.event_form("Appuntamento", title="FERIE", all_day="1",
                                              start_date="2026-07-10", end_date="2026-07-12"))
        card = self.card(self.day_page(self.render_calendar("giorno", "2026-07-11"), "2026-07-11"), allday_id)
        self.assertIn('<span class="calendar-appt-time">Tutto il giorno</span>', card)
        self.assertNotIn("calendar-appt-time-end", card)

    def test_calendar_css_touch_targets_alignment_colors_and_opacity_fixes(self):
        self.assertIn(".calendar-date-nav .btn{min-width:44px;min-height:44px;padding:0;font-size:19px}", app.CSS)
        self.assertIn(".calendar-date-nav{grid-template-columns:44px minmax(0,1fr) 44px}", app.CSS)
        self.assertIn("background:color-mix(in srgb,currentColor 24%,#15202f)", app.CSS)
        self.assertIn("color-mix(in srgb,currentColor 18%,#fff)", app.CSS)

    def test_calendar_draft_autosave_has_separate_keys_and_excludes_sensitive_fields(self):
        rendered = []
        self.handler.path = "/calendario/nuovo"
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.calendar_event_form(self.admin)
        self.assertIn('data-draft-key="ppm_calendar_draft_new"', rendered[-1])
        self.assertIn('id="calendarDraftStatus"', rendered[-1])

        event_id = self.save(self.event_form("Ritiro"))
        rendered.clear()
        self.handler.path = f"/calendario/{event_id}/modifica"
        self.handler.calendar_event_form(self.admin, event_id)
        self.assertIn(f'data-draft-key="ppm_calendar_draft_edit_{event_id}"', rendered[-1])

        js = app.APP_JS
        self.assertIn("function setupCalendarDraftAutosave(form)", js)
        self.assertIn("setupCalendarDraftAutosave(document.getElementById('calendarEventForm'))", js)
        self.assertIn("input.type==='password'", js)
        self.assertIn("/token|session/i.test(name)", js)
        self.assertIn("localStorage.removeItem(key)", js)
        self.assertIn("Bozza salvata", js)
        self.assertIn("Bozza ripristinata", js)

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
        self.assertIn("form.end_date.value=form.start_date.value;", app.APP_JS)
        self.assertIn("!form.end_date.dataset.manualEdit", app.APP_JS)
        # Server-side normalize_event already rejects an end before the start regardless of client sync bugs.
        with self.assertRaisesRegex(ValueError, "fine"):
            normalize_event(self.event_form("Ritiro", start_date="2026-07-16", end_date="2026-07-15"))

    def test_editing_existing_event_preserves_its_saved_end_date_and_time(self):
        # Regression test aggiornato: la versione precedente congelava del
        # tutto end_date impostando manualEdit='1' incondizionatamente in
        # modifica, il che a sua volta impediva di spostare end_date quando
        # l'utente cambiava start_date su un evento multi-giorno gia'
        # esistente (bug segnalato dall'utente). Ora lo scarto di giorni
        # viene preservato tramite dataset.dateSpanDays invece di congelare
        # il campo. Dal 27/10/2026 (richiesta esplicita dell'utente) in
        # modifica la fine non resta ferma: segue l'inizio mantenendo la
        # durata dell'evento salvato (ppmInitEventKeepDuration).
        js = app.APP_JS
        self.assertIn("(form.dataset.draftKey||'').includes('_edit_')", js)
        self.assertIn("form.dataset.dateSpanDays", js)
        self.assertIn("ppmInitEventKeepDuration(form,['change'],f=>{", js)
        event_id = self.save(self.event_form("Appuntamento", start_date="2026-07-19", start_time="14:45", end_date="2026-07-19", end_time="16:00"))
        rendered = []
        self.handler.path = f"/calendario/{event_id}/modifica"
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.calendar_event_form(self.admin, event_id)
        html = rendered[-1]
        self.assertIn(f'"ppm_calendar_draft_edit_{event_id}"', html)

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

    def test_link_practice_section_shown_for_pickup_events_before_ritirato_too(self):
        # Richiesta esplicita dell'utente: si puo' collegare/creare la
        # pratica anche prima che il ritiro sia stato effettuato - resta
        # bloccato solo un evento annullato (vedi test successivo).
        event_id = self.save(self.event_form("Ritiro", event_status="Da confermare"))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertIn("Collega pratica esistente", rendered[-1])
        self.assertIn("calendarLinkPracticeSearch", rendered[-1])

        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("PP-LINK-04", "Privato", "Livorno", "Da ritirare", stamp, stamp, self.admin["id"], "Milo"),
            ).lastrowid
        self.handler.form = lambda: {"practice_id": str(pid)}
        self.handler.calendar_event_action(self.admin, event_id, "collega-pratica")
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertNotIn('id="calendarLinkPracticeSearch"', rendered[-1])
        self.assertIn("Apri pratica", rendered[-1])
        self.assertIn("PP-LINK-04", rendered[-1])
        # promemoria: il ritiro non risulta ancora effettuato
        self.assertIn("ricordati di aggiornare lo stato", rendered[-1])

        self.handler.form = lambda: {"status": "Ritirato"}
        with patch("app.emit_notification", return_value=[]):
            self.handler.calendar_event_action(self.admin, event_id, "stato")
        self.handler.calendar_event_detail(self.admin, event_id)
        # una volta ritirato, il promemoria sparisce
        self.assertNotIn("ricordati di aggiornare lo stato", rendered[-1])

    def test_link_practice_section_hidden_for_annullato_event(self):
        event_id = self.save(self.event_form("Ritiro", event_status="Annullato"))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertNotIn("Collega pratica esistente", rendered[-1])

    def test_riconsegna_event_detail_shows_apri_pratica_when_linked(self):
        # richiesta esplicita dell'utente: da un evento Riconsegna gia'
        # collegato a una pratica deve essere possibile aprirla dal
        # riepilogo, non solo per un Ritiro segnato Ritirato.
        with app.db() as conn:
            stamp = datetime.now().isoformat(timespec="seconds")
            pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,animal_name)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("PP-RICLINK", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"], "Kira"),
            ).lastrowid
        event_id = self.save(self.event_form("Riconsegna in sede"))
        with app.db() as conn:
            conn.execute("UPDATE calendar_events SET linked_practice_id=? WHERE id=?", (pid, event_id))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        page = rendered[-1]
        self.assertIn("Apri pratica", page)
        self.assertIn(f'href="/pratiche/{pid}"', page)

    def test_event_detail_merges_note_into_dettagli_and_removes_separate_tab(self):
        event_id = self.save(self.event_form("Appuntamento", title="APPUNTAMENTO NOTE", notes="Richiamare il cliente prima delle 18"))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = f"/calendario/{event_id}"
        self.handler.calendar_event_detail(self.admin, event_id)
        page = rendered[-1]
        # The note text is now shown directly on the Dettagli tab (the default view).
        self.assertIn("Richiamare il cliente prima delle 18", page)
        # There is no more a standalone "Note" tab link.
        self.assertNotIn(f'href="/calendario/{event_id}?tab=note"', page)
        # Dopo il ridisegno della scheda la nota e' una riga del riquadro
        # principale (titolo "Note" + testo), presente una volta sola.
        self.assertEqual(page.count('<b>Note</b>'), 1)
        self.assertIn("<b>Note</b><small>Richiamare il cliente prima delle 18</small>", page)

        # Fetching the old tab=note URL falls back to the (now merged) Dettagli tab
        # rather than a blank/broken panel.
        self.handler.path = f"/calendario/{event_id}?tab=note"
        self.handler.calendar_event_detail(self.admin, event_id)
        self.assertIn("Richiamare il cliente prima delle 18", rendered[-1])

    def test_event_detail_datetime_field_renamed_and_collapses_same_day_range(self):
        same_day_id = self.save(self.event_form("Appuntamento", title="STESSO GIORNO",
                                                 start_date="2026-07-22", start_time="14:00",
                                                 end_date="2026-07-22", end_time="14:30"))
        multi_day_id = self.save(self.event_form("Appuntamento", title="MULTI GIORNO",
                                                  start_date="2026-07-22", start_time="11:00",
                                                  end_date="2026-07-23", end_time="18:00"))
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = f"/calendario/{same_day_id}"
        self.handler.calendar_event_detail(self.admin, same_day_id)
        page = rendered[-1]
        # Scheda ridisegnata (01/08): orario in grassetto in cima, data come
        # dettaglio piccolo sotto. Stesso giorno: la data compare una volta sola.
        self.assertNotIn("<small>Intervallo</small>", page)
        self.assertIn("<b>14:00 → 14:30</b><small>22/07/2026</small>", page)

        self.handler.path = f"/calendario/{multi_day_id}"
        self.handler.calendar_event_detail(self.admin, multi_day_id)
        page = rendered[-1]
        # Evento su piu' giorni: sotto l'orario anche la data di fine, non
        # solo quella di inizio (sembrerebbe tutto lo stesso giorno).
        self.assertIn("<b>11:00 → 18:00</b><small>22/07/2026 → 23/07/2026</small>", page)

    def test_delivery_clinic_hidden_for_riconsegna_in_sede_but_shown_for_riconsegna(self):
        # Riconsegna (non in sede) ora ha lo stesso selettore Ambulatorio/
        # Domicilio/Altro indirizzo del Ritiro: la ricerca ambulatorio
        # compare solo scegliendo il pill "Ambulatorio", il campo Indirizzo
        # resta sempre visibile per tutta la sezione "Riconsegna".
        rendered = []
        self.handler.path = "/calendario/nuovo"
        self.handler.send_html = lambda html, status=200: rendered.append(html)

        self.handler.calendar_event_form(self.admin, draft=self.event_form("Riconsegna in sede", destination_site="Livorno"))
        in_sede_html = rendered[-1]
        location_marker = in_sede_html.index('class="calendar-delivery-location"')
        self.assertIn("hidden", in_sede_html[location_marker:location_marker + 80])
        self.assertIn('id="calendarDeliveryClinicSearch"', in_sede_html)
        self.assertIn('name="delivery_address"', in_sede_html)

        self.handler.calendar_event_form(self.admin, draft=self.event_form("Riconsegna"))
        riconsegna_html = rendered[-1]
        location_marker = riconsegna_html.index('class="calendar-delivery-location"')
        self.assertNotIn("hidden", riconsegna_html[location_marker:location_marker + 80])
        # Senza un pill scelto la ricerca ambulatorio resta nascosta...
        clinic_marker = riconsegna_html.index('data-delivery-pickup-location="Veterinario"')
        self.assertIn("hidden", riconsegna_html[clinic_marker:clinic_marker + 80])
        self.assertIn('name="delivery_address"', riconsegna_html)
        # ...ma scegliendo "Ambulatorio" (delivery_location_type=Veterinario) compare.
        self.handler.calendar_event_form(self.admin, draft=self.event_form("Riconsegna", delivery_location_type="Veterinario"))
        riconsegna_vet_html = rendered[-1]
        clinic_marker = riconsegna_vet_html.index('data-delivery-pickup-location="Veterinario"')
        self.assertNotIn("hidden", riconsegna_vet_html[clinic_marker:clinic_marker + 80])

    def test_delivery_animal_search_returns_owner_address_for_auto_fill(self):
        stamp = datetime.now().isoformat(timespec="seconds")
        with app.db() as conn:
            conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,owner_first_name,owner_last_name,owner_street,owner_city,pickup_date)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-ADDR-01", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"],
                 "Zampina", "Marco", "Neri", "Via dei Fiori 5", "Livorno", "2026-07-10"),
            )
        response = {}
        self.handler.path = "/api/calendario/animali/search?q=zampina"
        self.handler.send_json = lambda obj, status=200: response.update(obj=obj, status=status)
        self.handler.api_calendar_animals_search(self.admin)
        result = response["obj"]["results"][0]
        self.assertEqual(result["owner_address"], "Via dei Fiori 5, Livorno")
        self.assertIn("form.delivery_address.value=item.owner_address", app.APP_JS)

    def test_calendar_daybar_uses_same_today_active_colors_as_cremation(self):
        # richiesta dell'utente: stessa colorazione di Programma Cremazioni,
        # oggi in rosso e gli altri giorni (selezionato) in blu — prima il
        # Calendario Operativo non aveva alcuna regola CSS per "oggi" (la
        # classe is-today veniva emessa ma non stilizzata) e "selezionato"
        # era rosso invece che blu.
        today = datetime.now().date()
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = f"/calendario?vista=settimana&data={today.isoformat()}"
        self.handler.calendar_page(admin)
        page = rendered[-1]
        self.assertIn(' is-today"', page)
        self.assertIn('.calendar-daybar-card.active{background:linear-gradient(135deg,#3b82f6,#1d4ed8)', app.CSS)
        self.assertIn('.calendar-daybar-card.is-today{background:linear-gradient(135deg,#fb4c67,#d9284c)', app.CSS)
        # la regola "oggi" deve essere dichiarata dopo "selezionato" nel CSS,
        # cosi' quando una card e' sia oggi che selezionata vince il rosso
        # (stessa precedenza gia' usata in Programma Cremazioni)
        self.assertLess(app.CSS.index('.calendar-daybar-card.active{'), app.CSS.index('.calendar-daybar-card.is-today{'))

    def test_calendar_week_stats_drop_unassigned_card_but_month_view_keeps_it(self):
        # richiesta dell'utente: eliminare la card "Senza incaricato" dalle
        # statistiche della vista Settimana/Giorno (resta solo Da effettuare
        # + Completati, griglia a 2 colonne); la vista Mese usa la stessa
        # classe .calendar-appt-stats per un widget diverso (Ritiri/
        # Riconsegne/Completati) e non va toccata.
        with app.db() as conn:
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/calendario?vista=settimana&data=2026-08-12"
        self.handler.calendar_page(admin)
        body = rendered[-1].split('</style>', 1)[1]  # CSS still styles month view's own card, only check the body
        self.assertIn('calendar-appt-stats-2col', body)
        self.assertNotIn('calendar-appt-stat-unassigned', body)
        self.assertNotIn('Senza incaricato', body)
        self.assertIn('.calendar-appt-stats.calendar-appt-stats-2col{grid-template-columns:repeat(2,1fr)}', app.CSS)
        self.handler.path = "/calendario?vista=mese&data=2026-08-12"
        self.handler.calendar_page(admin)
        month_page = rendered[-1]
        self.assertIn('calendar-appt-stat-unassigned', month_page)
        self.assertIn('Senza incaricato', month_page)

    def test_calendar_operator_filter_is_a_shared_select_for_everyone_unfiltered_by_default(self):
        # richiesta dell'utente: calendario condiviso, tutti (admin e non)
        # vedono di default gli eventi inseriti da chiunque - niente piu'
        # auto-filtro forzato sul proprio nome per i non-admin. La tendina
        # "Incaricato" resta disponibile per tutti come filtro manuale
        # opzionale, sempre vuota (nessun operatore) al primo caricamento.
        with app.db() as conn:
            serena = conn.execute("SELECT * FROM users WHERE username='serena'").fetchone()
            admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/calendario?vista=settimana&data=2026-08-12"
        self.handler.calendar_page(serena)
        operator_page = rendered[-1]
        self.assertIn('<select class="calendar-filter-operator"', operator_page)
        self.assertIn('<option value="">Incaricato</option>', operator_page)
        self.assertNotIn('<input type="hidden" class="calendar-filter-operator"', operator_page)
        self.handler.calendar_page(admin)
        admin_page = rendered[-1]
        self.assertIn('<select class="calendar-filter-operator"', admin_page)
        self.assertIn('<option value="">Incaricato</option>', admin_page)

    def test_animal_search_payment_summary_uses_saldo_rimanenza_label_for_w_circuit(self):
        stamp = datetime.now().isoformat(timespec="seconds")
        with app.db() as conn:
            conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,total_service,deposit,payment_status,pickup_date)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PP-SALDO-01", "Privato", "Livorno", "Ritirato", stamp, stamp, self.admin["id"],
                 "Fufi", "300", "100", "Acconto", "2026-07-10"),
            )
        response = {}
        self.handler.path = "/api/calendario/animali/search?q=fufi"
        self.handler.send_json = lambda obj, status=200: response.update(obj=obj, status=status)
        self.handler.api_calendar_animals_search(self.admin)
        result = response["obj"]["results"][0]
        self.assertIn("Saldo/Rimanenza W", result["payment_summary"])

    def test_form_is_guided_supports_contacts_maps_autocomplete_and_touch_layout(self):
        captured = []
        self.handler.path = "/calendario/nuovo?data=2026-07-15"
        self.handler.send_html = lambda html, status=200: captured.append(html)
        self.handler.calendar_event_form(self.admin)
        html = captured[0]
        # Modulo ridisegnato (28/07-01/08): una sola pagina a schede invece
        # della procedura guidata a passi.
        for text in ("Che tipo di evento vuoi creare?", "Dettagli evento", "Cerca veterinario", "Cerca cliente", "<span>Aggiungi animale</span>", "Preventivo</h2>", "Tutto il giorno"):
            self.assertIn(text, html)
        for hook in ("calendarZoneOffer", "/api/clienti/search", "/api/veterinari/search"):
            self.assertIn(hook, html if hook == "calendarZoneOffer" else app.APP_JS + html)
        for operator in ("Serena","Alessio","Filippo"):
            self.assertIn(operator,html)
        for preset in ("Cremazione","Ritiro","Riconsegna","Urna","Altro"):
            self.assertIn(preset,html)
        # Cremazione/Ritiro/Riconsegna stay fixed labels, but Urna must be a free-text
        # field with catalog search + automatic price (pre-filled with "Urna" so an
        # untouched row still saves), since the specific urn chosen varies per
        # practice and can't be a locked preset - richiesta esplicita dell'utente:
        # stessa ricerca con autocomplete e prezzo automatico gia' usata in
        # creazione pratica (setupPracticeUrnRowSearch/PPM_URN_CATALOG).
        self.assertIn("data.preset==='Urna'", app.APP_JS)
        self.assertIn('placeholder="Cerca urna o scrivi liberamente" data-key="description" value="${data.description||\'Urna\'}"', app.APP_JS)
        self.assertIn("setupCalendarUrnRowSearch", app.APP_JS)
        self.assertNotIn("Persona o azienda",html)
        self.assertNotIn("Nome animale *",html)
        self.assertNotIn("Operatore assegnato",html)
        self.assertIn("calendarTypeSelected(this)",html)
        self.assertIn("calendarWizardSwipe",app.APP_JS)
        self.assertIn("calendarTimeInput",app.APP_JS)
        self.assertIn("calendarSyncTimeWheel",app.APP_JS)
        self.assertIn("calendarInitTimeWheel",app.APP_JS)
        self.assertIn('class="calendar-datetime-row"',html)
        self.assertIn('class="calendar-time-wheel"',html)
        self.assertIn('data-wheel-part="hour"',html)
        self.assertIn('data-wheel-part="minute"',html)
        self.assertIn('onfocus="calendarTimeFocus(this)"',html)
        self.assertLess(html.index('name="start_date"'),html.index('name="start_time"'))
        self.assertIn("calendar-zone-results",html)
        self.assertEqual(html.count("calendarTypeSelected(this)"),5)
        for event_type in ("Ritiro","Ritiro in sede","Riconsegna","Riconsegna in sede","Appuntamento"):
            self.assertIn(f'name="event_type" value="{event_type}"',html)
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

    def test_calendar_page_never_overwrites_saved_view_preference_just_by_viewing(self):
        # Visiting a specific vista (e.g. opening a single day from Month) must not
        # silently change the user's saved default -- only the Settings page's own
        # explicit "Salva preferenza" write should do that.
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.path = "/calendario?vista=giorno&data=2026-07-15"
        self.handler.calendar_page(self.admin)
        page = rendered[-1]
        self.assertNotIn("localStorage.setItem('ppm_calendar_view'", page)
        # The read-and-redirect part must still be present, so a bare /calendario
        # visit still restores whatever preference was actually saved.
        self.assertIn("localStorage.getItem('ppm_calendar_view')", page)
        self.assertIn("url.searchParams.set('vista',saved);location.replace(url);", page)

    def test_day_view_shows_back_button_when_opened_from_month_or_week(self):
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        # No back button when Day view is reached without a "da" origin marker.
        self.handler.path = "/calendario?vista=giorno&data=2026-07-15"
        self.handler.calendar_page(self.admin)
        self.assertNotIn('class="btn ghost calendar-back-btn"', rendered[-1])
        # Opened from Month.
        self.handler.path = "/calendario?vista=giorno&data=2026-07-15&da=mese"
        self.handler.calendar_page(self.admin)
        page = rendered[-1]
        self.assertIn('<a class="btn ghost calendar-back-btn" href="/calendario?vista=mese&data=2026-07-15">← Torna a Mese</a>', page)
        # Opened from Week.
        self.handler.path = "/calendario?vista=giorno&data=2026-07-15&da=settimana"
        self.handler.calendar_page(self.admin)
        page = rendered[-1]
        self.assertIn('<a class="btn ghost calendar-back-btn" href="/calendario?vista=settimana&data=2026-07-15">← Torna a Settimana</a>', page)
        # The back button only makes sense on Day view -- not on Week/Month themselves.
        self.handler.path = "/calendario?vista=settimana&data=2026-07-15&da=mese"
        self.handler.calendar_page(self.admin)
        self.assertNotIn('class="btn ghost calendar-back-btn"', rendered[-1])

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
            per_user = conn.execute("SELECT count(DISTINCT user_id) n FROM notifications WHERE type='calendar_reminder_30m'").fetchone()["n"]
            active_users = conn.execute("SELECT count(*) n FROM users WHERE active=1").fetchone()["n"]
            summary_first = process_calendar_notifications(conn, None, moment.replace(minute=45))
            summary_second = process_calendar_notifications(conn, None, moment.replace(minute=50))
            summaries = conn.execute("SELECT count(*) n FROM notifications WHERE type='calendar_daily_summary'").fetchone()["n"]
            summary_users = conn.execute("SELECT count(DISTINCT user_id) n FROM notifications WHERE type='calendar_daily_summary'").fetchone()["n"]
        # Le notifiche raggiungono tutto lo staff attivo (richiesta esplicita
        # dell'utente, 04/08): un solo promemoria per utente, e il secondo
        # passaggio non ne crea altri.
        self.assertEqual((first, second), (2, 0))
        self.assertEqual((count, per_user), (active_users, active_users))
        self.assertEqual(reminder["status"], "inviato")
        self.assertTrue(reminder["sent_at"])
        self.assertEqual((summary_first, summary_second, summaries, summary_users), (0, 0, active_users, active_users))

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
            "owner_city": "Livorno", "owner_province": "LI", "owner_zip": "57100", "provenance": "L", "animal_name": "Luna",
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

    def test_calendar_operator_is_automatic_for_non_admin_and_manual_for_admin(self):
        with app.db() as conn:
            gianluca = conn.execute("SELECT * FROM users WHERE username='gianluca'").fetchone()

        form = self.event_form("Ritiro", operator_name="Serena")
        self.handler.form = lambda: form
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(gianluca)
        event_id = int(self.redirected.rsplit("/", 1)[-1])
        with app.db() as conn:
            event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual(event["operator_name"], "Gianluca")

        edit_form = self.event_form("Ritiro", operator_name="Filippo")
        self.handler.form = lambda: edit_form
        with patch("app.emit_notification", return_value=[]):
            self.handler.save_calendar_event(gianluca, event_id)
        with app.db() as conn:
            edited = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        self.assertEqual(edited["operator_name"], "Gianluca")

        admin_event_id = self.save(self.event_form("Ritiro", operator_name="Filippo"))
        with app.db() as conn:
            admin_event = conn.execute("SELECT * FROM calendar_events WHERE id=?", (admin_event_id,)).fetchone()
        self.assertEqual(admin_event["operator_name"], "Filippo")

    def test_existing_services_remain_untouched_by_calendar_estimates(self):
        before = {}
        with app.db() as conn:
            for table in ("practices", "payment_movements", "email_orders", "whatsapp_messages", "urns", "clients"):
                before[table] = conn.execute(f"SELECT count(*) n FROM {table}").fetchone()["n"]
        self.save(self.event_form("Ritiro", estimate_json=json.dumps([{"description": "Cremazione", "amount": "999"}])))
        with app.db() as conn:
            after = {table: conn.execute(f"SELECT count(*) n FROM {table}").fetchone()["n"] for table in before}
        self.assertEqual(before, after)

    def test_calendar_color_settings_defaults_and_persist_custom_choice(self):
        with app.db() as conn:
            defaults = app.calendar_color_settings(conn)
        self.assertEqual(defaults["operators"]["Serena"], "#f43f5e")
        self.assertEqual(defaults["types"]["Ritirato"], "#4ade80")
        with app.db() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('calendar_colors',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (json.dumps({"operators": {"Serena": "#22d3ee"}, "types": {"Ritirato": "#fb923c"}}),))
        with app.db() as conn:
            updated = app.calendar_color_settings(conn)
        self.assertEqual(updated["operators"]["Serena"], "#22d3ee")
        self.assertEqual(updated["types"]["Ritirato"], "#fb923c")
        self.assertEqual(updated["operators"]["Alessio"], "#14b8a6")  # untouched keys keep the default

    def test_calendar_color_settings_ignores_values_outside_the_palette(self):
        with app.db() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('calendar_colors',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (json.dumps({"operators": {"Serena": "#000000"}, "types": {}}),))
        with app.db() as conn:
            settings = app.calendar_color_settings(conn)
        self.assertEqual(settings["operators"]["Serena"], "#f43f5e")  # rejected, default kept

    def test_save_calendar_colors_requires_admin_and_persists_valid_choices(self):
        with app.db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,display_name,role) VALUES('operatore','x','Operatore','operator')")
            operator = conn.execute("SELECT * FROM users WHERE username='operatore'").fetchone()
        errors = []
        self.handler.send_error = lambda status, message="": errors.append(status)
        self.handler.form = lambda: {"operator_Serena": "#22d3ee", "type_Ritirato": "#fb923c"}
        self.handler.save_calendar_colors(operator)
        self.assertEqual(errors, [403])
        with app.db() as conn:
            self.assertIsNone(conn.execute("SELECT value FROM settings WHERE key='calendar_colors'").fetchone())

        self.handler.save_calendar_colors(self.admin)
        self.assertEqual(self.redirected, "/calendario/impostazioni")
        with app.db() as conn:
            settings = app.calendar_color_settings(conn)
        self.assertEqual(settings["operators"]["Serena"], "#22d3ee")
        self.assertEqual(settings["types"]["Ritirato"], "#fb923c")

        # A value not in the known palette must be rejected, keeping the previous color.
        self.handler.form = lambda: {"operator_Serena": "#123456"}
        self.handler.save_calendar_colors(self.admin)
        with app.db() as conn:
            settings = app.calendar_color_settings(conn)
        self.assertEqual(settings["operators"]["Serena"], "#22d3ee")

    def test_custom_colors_are_reflected_inline_on_rendered_cards_and_legend(self):
        with app.db() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('calendar_colors',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (json.dumps({"operators": {"Serena": "#22d3ee"}, "types": {"Da confermare": "#fb923c"}}),))
        event_id = self.save(self.event_form("Ritiro", title="RITIRO CUSTOM", event_status="Da confermare", operator_name="Serena"))
        card = self.card(self.day_page(self.render_calendar("giorno", "2026-07-15"), "2026-07-15"), event_id)
        # Colore personalizzato dello stato sul bordo, sul pallino e sulla badge;
        # colore personalizzato dell'operatore sul suo avatar.
        self.assertIn('style="border-left-color:#fb923c"', card)
        self.assertIn('class="calendar-appt-time-dot" style="background:#fb923c"', card)
        self.assertIn('style="background:#fb923c22;color:#fb923c"', card)
        self.assertIn('calendar-avatar-serena" style="background:#22d3ee"', card)

    def test_calendar_settings_shows_color_section_only_for_admin(self):
        with app.db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,display_name,role) VALUES('operatore','x','Operatore','operator')")
            operator = conn.execute("SELECT * FROM users WHERE username='operatore'").fetchone()
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        self.handler.calendar_settings(self.admin)
        self.assertIn("Colori Calendario", rendered[-1])
        self.assertIn('action="/calendario/impostazioni"', rendered[-1])
        self.handler.calendar_settings(operator)
        self.assertNotIn("Colori Calendario", rendered[-1])

    def test_calendar_settings_shows_legend_for_every_user_admin_or_not(self):
        # The color/operator legend moved out of the calendar views entirely and now
        # lives only on the settings page, visible to admins and regular users alike.
        with app.db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,display_name,role) VALUES('operatore','x','Operatore','operator')")
            operator = conn.execute("SELECT * FROM users WHERE username='operatore'").fetchone()
        rendered = []
        self.handler.send_html = lambda html, status=200: rendered.append(html)
        for user in (self.admin, operator):
            self.handler.calendar_settings(user)
            page = rendered[-1]
            self.assertIn("Legenda colori", page)
            self.assertIn('<section class="calendar-legend-section">', page)
            self.assertIn('<div class="calendar-operator-legend">', page)
            self.assertIn("Colore = stato del ritiro", page)
            self.assertIn("Colore fisso = altri tipi", page)

    def test_day_view_pins_allday_and_multiday_carryover_events_above_hours(self):
        allday_id = self.save(self.event_form("Appuntamento", title="EVENTO TUTTO IL GIORNO", all_day="1"))
        carry_id = self.save(self.event_form("Riconsegna", title="RICONSEGNA IN CORSO",
                                             start_date="2026-07-13", start_time="09:00", end_date="2026-07-16", end_time="10:00"))
        timed_id = self.save(self.event_form("Appuntamento", title="EVENTO ORARIO", start_time="11:00", end_time="11:30"))
        day = self.day_page(self.render_calendar("giorno", "2026-07-15"), "2026-07-15")
        # Prima l'evento di tutto il giorno, poi quello iniziato in un giorno
        # precedente (in corso), poi quelli con orario: niente si perde.
        positions = [day.index(f'data-event-id="{event_id}"') for event_id in (allday_id, carry_id, timed_id)]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('<span class="calendar-appt-time">Tutto il giorno</span>', self.card(day, allday_id))
        # Il 15 e' un giorno intermedio della riconsegna (13 -> 16): mezzanotte-mezzanotte.
        self.assertEqual(self.card_times(self.card(day, carry_id)), ("00:00", "00:00"))
        self.assertEqual(self.card_times(self.card(day, timed_id)), ("11:00", "11:30"))

    def test_day_view_without_events_shows_empty_message_and_zero_counters(self):
        # La griglia oraria 00-24 non esiste piu' (lista di card): un giorno
        # senza eventi mostra il messaggio vuoto e i contatori a zero, non una
        # pagina bianca.
        day = self.day_page(self.render_calendar("giorno", "2026-07-15"), "2026-07-15")
        self.assertIn("Nessun appuntamento in programma.", day)
        self.assertNotIn('class="calendar-appt-card"', day)
        self.assertIn('<b>0</b><small>Da effettuare</small>', day)
        self.assertIn('<b>0</b><small>Completati</small>', day)

    def test_week_and_month_empty_day_cells_still_link_to_day_view(self):
        # Settimana: ogni giorno, anche vuoto, ha la sua card nella barra (con
        # il conteggio) e la sua pagina, selezionabile con un tocco.
        week_page = self.render_calendar("settimana", "2026-07-15")
        self.assertIn('data-date="2026-07-13" onclick="calendarSelectDay(0)">', week_page)
        self.assertRegex(week_page, r'data-date="2026-07-13" onclick="calendarSelectDay\(0\)">\s*<span class="calendar-daybar-dow">LUN</span>\s*<span class="calendar-daybar-num">13</span>\s*<span class="calendar-daybar-count">0 app.</span>')
        self.assertIn("Nessun appuntamento in programma.", self.day_page(week_page, "2026-07-13"))
        # Mese: il primo tocco su un giorno non ancora selezionato lo seleziona
        # soltanto (resta sul mese), anche se e' vuoto.
        month_page = self.render_calendar("mese", "2026-07-15")
        self.assertIn('<a class="calendar-month-v2-cell" href="/calendario?vista=mese&data=2026-07-02">', month_page)
        self.assertNotIn('<a class="calendar-month-v2-cell" href="/calendario?vista=giorno&data=2026-07-02', month_page)
        # Il secondo tocco, ora che il 02/07 e' selezionato, apre la vista Giorno.
        month_page = self.render_calendar("mese", "2026-07-02")
        self.assertIn('<a class="calendar-month-v2-cell selected" href="/calendario?vista=giorno&data=2026-07-02&da=mese">', month_page)

if __name__ == "__main__":
    unittest.main()
