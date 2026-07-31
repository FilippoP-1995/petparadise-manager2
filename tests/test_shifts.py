import tempfile
import unittest
from datetime import date
from pathlib import Path

import app
from shift_service import oncall_rotation_operator, week_monday


class ShiftsModuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "shifts-test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        self.handler.redirect = lambda path: setattr(self, "redirected", path)
        self.redirected = ""
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            self.operator = conn.execute("SELECT * FROM users WHERE username='filippo'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def save_cell(self, user, **form):
        base = {"operator": "Serena", "data": "2026-08-10", "azione": "salva",
                "branch": "Livorno", "start_time": "08:00", "end_time": "16:00", "all_day": "0"}
        base.update(form)
        self.handler.form = lambda: base
        responses = []
        self.handler.send_json = lambda obj, status=200: responses.append((obj, status))
        self.handler.save_shift_cell(user)
        return responses[-1]

    # ---- schema ----
    def test_shift_schema_tables_and_indexes(self):
        with app.db() as conn:
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("shifts", tables)
            self.assertIn("shift_vacations", tables)
            self.assertIn("shift_oncall", tables)
            shift_cols = {row["name"] for row in conn.execute("PRAGMA table_info(shifts)")}
            self.assertTrue({"operator_name", "work_date", "branch", "start_time", "end_time", "all_day"} <= shift_cols)

    # ---- cell upsert / delete ----
    def test_save_shift_cell_creates_new_shift(self):
        obj, status = self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno",
                                      start_time="08:30", end_time="17:30")
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["cell"], {"branch": "Livorno", "start_time": "08:30", "end_time": "17:30", "all_day": False})
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shifts WHERE operator_name='Serena' AND work_date='2026-08-10'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["branch"], "Livorno")

    def test_save_shift_cell_updates_existing_instead_of_duplicating(self):
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", start_time="08:00", end_time="12:00")
        obj, status = self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Empoli", start_time="09:00", end_time="13:00")
        self.assertTrue(obj["ok"])
        with app.db() as conn:
            rows = conn.execute("SELECT * FROM shifts WHERE operator_name='Serena' AND work_date='2026-08-10'").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["branch"], "Empoli")

    def test_save_shift_cell_all_day_clears_times(self):
        obj, status = self.save_cell(self.admin, operator="Alessio", data="2026-08-11", branch="Empoli", all_day="1", start_time="", end_time="")
        self.assertTrue(obj["ok"])
        self.assertTrue(obj["cell"]["all_day"])
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shifts WHERE operator_name='Alessio' AND work_date='2026-08-11'").fetchone()
            self.assertIsNone(row["start_time"])
            self.assertIsNone(row["end_time"])

    def test_save_shift_cell_requires_branch(self):
        obj, status = self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="", start_time="08:00", end_time="10:00")
        self.assertEqual(status, 400)
        self.assertFalse(obj["ok"])
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shifts WHERE operator_name='Serena' AND work_date='2026-08-10'").fetchone()
            self.assertIsNone(row)

    def test_save_shift_cell_requires_times_unless_all_day(self):
        obj, status = self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", start_time="", end_time="", all_day="0")
        self.assertEqual(status, 400)
        self.assertFalse(obj["ok"])

    def test_save_shift_cell_removes_shift(self):
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", start_time="08:00", end_time="16:00")
        obj, status = self.save_cell(self.admin, operator="Serena", data="2026-08-10", azione="rimuovi")
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["cell"]["branch"], "")
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shifts WHERE operator_name='Serena' AND work_date='2026-08-10'").fetchone()
            self.assertIsNone(row)

    def test_save_shift_cell_allowed_for_non_admin_operator(self):
        obj, status = self.save_cell(self.operator, operator="Filippo", data="2026-08-12", branch="Empoli", start_time="10:00", end_time="18:00")
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])

    def test_save_shift_cell_rejects_operator_outside_shift_operators(self):
        # Gianluca resta un operatore valido per Calendario/Cremazioni ma non
        # fa parte della sezione Orari (richiesta esplicita dell'utente).
        obj, status = self.save_cell(self.admin, operator="Gianluca", data="2026-08-12", branch="Empoli", start_time="10:00", end_time="18:00")
        self.assertEqual(status, 400)
        self.assertFalse(obj["ok"])

    # ---- consultation views ----
    def test_shifts_page_giorno_groups_by_branch_and_shows_empty_note(self):
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", start_time="08:00", end_time="16:00")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=giorno&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        self.assertIn("Serena", page)
        self.assertIn("08:00", page)
        self.assertIn("Nessun turno assegnato", page)  # per Empoli, vuota quel giorno

    def test_shifts_page_mese_lists_operators_per_branch_per_day(self):
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", start_time="08:00", end_time="16:00")
        self.save_cell(self.admin, operator="Filippo", data="2026-08-10", branch="Livorno", start_time="09:00", end_time="17:00")
        self.save_cell(self.admin, operator="Alessio", data="2026-08-10", branch="Empoli", start_time="09:00", end_time="17:00")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=mese&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        self.assertIn("Serena", page)
        self.assertIn("Filippo", page)
        self.assertIn("Alessio", page)

    def test_shifts_page_giorno_uses_time_pill_and_empty_state_not_gantt_bar(self):
        # Rifinitura grafica su richiesta esplicita dell'utente: niente piu'
        # barra proporzionale sulle 24h (troppo pesante), ora una pill con
        # icona orologio; stato vuoto con icona centrata invece del solo testo.
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", start_time="08:00", end_time="16:00")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=giorno&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        self.assertIn('class="shift-time-pill"', page)
        self.assertIn('class="section shift-sede-card branch-livorno"', page)
        self.assertIn('class="shift-add-btn branch-livorno"', page)
        self.assertIn('class="shift-empty-state"', page)
        self.assertNotIn('class="shift-track"', page)
        self.assertNotIn('class="shift-bar"', page)

    def test_shifts_page_mese_has_horizontal_month_strip_and_legend(self):
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=mese&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        # Stessa striscia orizzontale scorrevole (classi calendar-daybar-*)
        # gia' usata per i giorni nel Calendario operativo/Cremazioni.
        self.assertIn('class="calendar-daybar-wrap"', page)
        self.assertIn('class="calendar-daybar"', page)
        self.assertIn("Agosto", page)
        self.assertIn("Luglio", page)
        self.assertIn("Settembre", page)
        self.assertIn('class="shift-month-legend"', page)
        self.assertIn("Nessun turno", page)

    def test_shifts_page_mese_vacation_band_rounds_only_at_edges(self):
        with app.db() as conn:
            from shift_service import create_vacation
            create_vacation(conn, "Serena", "2026-08-10", "2026-08-12", None, self.admin["id"])
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=mese&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        self.assertGreaterEqual(page.count("shift-vacation-band"), 3)
        self.assertIn("band-start", page)
        self.assertIn("band-end", page)

    # ---- plan page ----
    def test_shifts_plan_page_shows_grid_for_shift_operators_only_and_seven_days(self):
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/pianifica?settimana=2026-08-10"
        self.handler.shifts_plan_page(self.admin)
        page = rendered[-1]
        for name in ("Serena", "Alessio", "Filippo"):
            self.assertIn(name, page)
        # Gianluca resta un operatore di Calendario/Cremazioni ma non fa
        # parte della sezione Orari.
        self.assertNotIn(">Gianluca<", page)
        self.assertEqual(page.count('<button type="button" class="shift-cell'), 21)

    def test_shifts_plan_page_cell_time_fields_use_shared_wheel_picker(self):
        # Stesso identico componente orario (rotella nativa) gia' usato da
        # Calendario/Cremazioni, non un semplice input type=time.
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/pianifica?settimana=2026-08-10"
        self.handler.shifts_plan_page(self.admin)
        page = rendered[-1]
        self.assertIn('id="shiftCellStart"', page)
        self.assertIn('id="shiftCellEnd"', page)
        self.assertNotIn('type="time" id="shiftCellStart"', page)
        self.assertNotIn('type="time" id="shiftCellEnd"', page)
        self.assertIn('class="calendar-time-entry"', page)
        self.assertIn('data-time-wheel', page)
        self.assertIn('calendarTimeFocus(this)', page)

    def test_shifts_plan_page_sede_locked_from_query_hides_dropdown_via_data_attr(self):
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/pianifica?settimana=2026-08-10&sede=Livorno"
        self.handler.shifts_plan_page(self.admin)
        page = rendered[-1]
        # Ogni cella vuota eredita la sede bloccata dal contesto di pagina.
        self.assertIn('data-locked-branch="Livorno"', page)
        self.assertIn('id="shiftCellBranchField"', page)
        self.assertIn('id="shiftCellSedeInfo"', page)

    def test_shifts_plan_page_invalid_sede_shows_error_and_locks_nothing(self):
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/pianifica?settimana=2026-08-10&sede=Roma"
        self.handler.shifts_plan_page(self.admin)
        page = rendered[-1]
        self.assertIn("non valida", page)
        self.assertNotIn('data-locked-branch="Roma"', page)

    def test_shifts_plan_page_existing_shift_locked_branch_wins_over_query_sede(self):
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Empoli", start_time="09:00", end_time="17:00")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/pianifica?settimana=2026-08-10&sede=Livorno"
        self.handler.shifts_plan_page(self.admin)
        page = rendered[-1]
        marker = page.index('data-operator="Serena" data-date="2026-08-10"')
        window = page[marker:marker + 400]
        self.assertIn('data-locked-branch="Empoli"', window)

    def test_shifts_plan_page_back_button_targets_originating_view(self):
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/pianifica?settimana=2026-08-10&ritorno_vista=mese&ritorno_data=2026-08-05"
        self.handler.shifts_plan_page(self.admin)
        page = rendered[-1]
        self.assertIn('href="/turni?vista=mese&data=2026-08-05"', page)

    def test_shifts_plan_page_week_nav_preserves_sede_and_ritorno(self):
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/pianifica?settimana=2026-08-10&sede=Empoli&ritorno_vista=giorno&ritorno_data=2026-08-10"
        self.handler.shifts_plan_page(self.admin)
        page = rendered[-1]
        self.assertIn("sede=Empoli", page)
        self.assertIn("ritorno_vista=giorno", page)
        self.assertIn("ritorno_data=2026-08-10", page)

    def test_shifts_plan_page_evidenzia_marks_the_right_cell(self):
        self.save_cell(self.admin, operator="Serena", data="2026-08-11", branch="Livorno", start_time="08:00", end_time="16:00")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/pianifica?settimana=2026-08-10&evidenzia=Serena:2026-08-11"
        self.handler.shifts_plan_page(self.admin)
        page = rendered[-1]
        self.assertIn('data-operator="Serena" data-date="2026-08-11" data-shift=', page)
        marker = page.index('data-operator="Serena" data-date="2026-08-11"')
        preceding = page[max(0, marker - 200):marker]
        self.assertIn("evidenziata", preceding)

    # ---- ferie ----
    def test_save_shift_vacation_has_no_branch_field_and_persists(self):
        self.handler.form = lambda: {"operator_name": "Alessio", "start_date": "2026-08-20", "end_date": "2026-08-25", "note": "Mare"}
        self.handler.save_shift_vacation(self.admin)
        self.assertEqual(self.redirected, "/turni/ferie")
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shift_vacations WHERE operator_name='Alessio'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["note"], "Mare")

    def test_save_shift_vacation_rejects_end_before_start(self):
        self.handler.form = lambda: {"operator_name": "Alessio", "start_date": "2026-08-25", "end_date": "2026-08-20"}
        self.handler.save_shift_vacation(self.admin)
        self.assertIn("errore=", self.redirected)
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shift_vacations WHERE operator_name='Alessio'").fetchone()
            self.assertIsNone(row)

    def test_shifts_page_has_ferie_button_in_giorno_and_mese_with_return_context(self):
        for vista in ("giorno", "mese"):
            rendered = []
            self.handler.send_html = lambda html, *a: rendered.append(html)
            self.handler.path = f"/turni?vista={vista}&data=2026-08-10"
            self.handler.shifts_page(self.admin)
            page = rendered[-1]
            self.assertIn(f"/turni/ferie?ritorno_vista={vista}&ritorno_data=2026-08-10", page)

    def test_shifts_vacations_page_back_button_and_form_carry_return_context(self):
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/ferie?ritorno_vista=mese&ritorno_data=2026-08-10"
        self.handler.shifts_vacations_page(self.admin)
        page = rendered[-1]
        self.assertIn('href="/turni?vista=mese&data=2026-08-10"', page)
        self.assertIn('name="ritorno_vista" value="mese"', page)
        self.assertIn('name="ritorno_data" value="2026-08-10"', page)

    def test_save_shift_vacation_redirects_back_to_orari_context_when_provided(self):
        self.handler.form = lambda: {"operator_name": "Alessio", "start_date": "2026-08-20", "end_date": "2026-08-25", "ritorno_vista": "mese", "ritorno_data": "2026-08-10"}
        self.handler.save_shift_vacation(self.admin)
        self.assertEqual(self.redirected, "/turni/ferie?ritorno_vista=mese&ritorno_data=2026-08-10")

    def test_delete_shift_vacation_removes_row(self):
        with app.db() as conn:
            from shift_service import create_vacation
            vac_id = create_vacation(conn, "Alessio", "2026-08-20", "2026-08-25", None, self.admin["id"])
        self.handler.path = f"/turni/ferie/{vac_id}/elimina"
        self.handler.delete_shift_vacation(self.admin, vac_id)
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shift_vacations WHERE id=?", (vac_id,)).fetchone()
            self.assertIsNone(row)

    def test_shifts_vacations_page_allowed_for_non_admin_operator(self):
        self.handler.form = lambda: {"operator_name": "Alessio", "start_date": "2026-09-01", "end_date": "2026-09-05"}
        self.handler.save_shift_vacation(self.operator)
        self.assertEqual(self.redirected, "/turni/ferie")
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shift_vacations WHERE operator_name='Alessio'").fetchone()
            self.assertIsNotNone(row)

    def test_save_shift_vacation_rejects_operator_outside_shift_operators(self):
        self.handler.form = lambda: {"operator_name": "Gianluca", "start_date": "2026-09-01", "end_date": "2026-09-05"}
        self.handler.save_shift_vacation(self.admin)
        self.assertIn("errore=", self.redirected)
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shift_vacations WHERE operator_name='Gianluca'").fetchone()
            self.assertIsNone(row)

    # ---- reperibilita ----
    def test_oncall_rotation_is_deterministic_across_calls(self):
        monday = week_monday(date(2026, 8, 10))
        first = oncall_rotation_operator(monday, list(app.SHIFT_OPERATORS))
        second = oncall_rotation_operator(monday, list(app.SHIFT_OPERATORS))
        self.assertEqual(first, second)
        self.assertIn(first, app.SHIFT_OPERATORS)
        self.assertNotIn("Gianluca", app.SHIFT_OPERATORS)

    def test_save_shift_oncall_persists_manual_override_and_page_reflects_it(self):
        self.handler.form = lambda: {"settimana": "2026-08-10", "operator_name": "Alessio"}
        self.handler.save_shift_oncall(self.admin)
        self.assertEqual(self.redirected, "/turni/reperibilita")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni/reperibilita"
        self.handler.shifts_oncall_page(self.admin)
        page = rendered[-1]
        self.assertIn("Assegnazione manuale", page)

    def test_save_shift_oncall_does_not_block_further_manual_changes(self):
        self.handler.form = lambda: {"settimana": "2026-08-10", "operator_name": "Alessio"}
        self.handler.save_shift_oncall(self.admin)
        self.handler.form = lambda: {"settimana": "2026-08-10", "operator_name": "Serena"}
        self.handler.save_shift_oncall(self.admin)
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shift_oncall WHERE week_start='2026-08-10'").fetchone()
            self.assertEqual(row["operator_name"], "Serena")
            self.assertEqual(row["is_manual"], 1)

    def test_save_shift_oncall_rejects_operator_outside_shift_operators(self):
        self.handler.form = lambda: {"settimana": "2026-08-10", "operator_name": "Gianluca"}
        self.handler.save_shift_oncall(self.admin)
        with app.db() as conn:
            row = conn.execute("SELECT * FROM shift_oncall WHERE week_start='2026-08-10'").fetchone()
            self.assertIsNone(row)

    # ---- matita inline sulla pagina principale Orari ----
    def test_shifts_page_giorno_pencil_button_opens_editor_inline_not_a_link(self):
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", start_time="08:30", end_time="13:00")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=giorno&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        self.assertIn('onclick="turniOpenCellEditor(this)"', page)
        self.assertIn('data-locked-branch="Livorno"', page)
        self.assertIn('id="shiftCellEditorBackdrop"', page)  # popup condiviso presente sulla pagina

    def test_shifts_page_mese_shows_operator_names_never_times(self):
        # Vista mensile = solo panoramica (chi e' a Livorno/Empoli): mai
        # orari, richiesta esplicita dell'utente dopo celle deformate.
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", start_time="08:30", end_time="13:00")
        self.save_cell(self.admin, operator="Filippo", data="2026-08-10", branch="Livorno", start_time="14:30", end_time="19:30")
        self.save_cell(self.admin, operator="Alessio", data="2026-08-10", branch="Empoli", start_time="09:15", end_time="12:00")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=mese&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        self.assertIn(">Serena<", page)
        self.assertIn(">Filippo<", page)
        self.assertIn(">Alessio<", page)
        self.assertNotIn("Mattina", page)
        self.assertNotIn("Pomeriggio", page)
        self.assertNotIn("09:15", page)
        self.assertNotIn("Tutto il giorno", page)

    def test_shifts_page_mese_collapses_to_operator_count_above_two(self):
        # Oltre due operatori nella stessa sede/giorno: mostra "N operatori"
        # invece dei nomi, per mantenere la cella di dimensione fissa.
        for operator, start, end in (("Serena", "08:30", "13:00"), ("Filippo", "14:30", "19:30"), ("Alessio", "09:00", "18:00")):
            self.save_cell(self.admin, operator=operator, data="2026-08-10", branch="Livorno", start_time=start, end_time=end)
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=mese&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        self.assertIn("3 operatori", page)
        cell_start = page.index('href="/turni?vista=giorno&data=2026-08-10"')
        cell_html = page[max(0, cell_start - 200):cell_start + 400]
        self.assertNotIn(">Serena<", cell_html)

    def test_shifts_page_mese_cells_have_fixed_uniform_height(self):
        self.save_cell(self.admin, operator="Serena", data="2026-08-10", branch="Livorno", all_day="1", start_time="", end_time="")
        rendered = []
        self.handler.send_html = lambda html, *a: rendered.append(html)
        self.handler.path = "/turni?vista=mese&data=2026-08-10"
        self.handler.shifts_page(self.admin)
        page = rendered[-1]
        self.assertIn(".shift-month-cell{", page)
        css_start = page.index(".shift-month-cell{")
        css_rule = page[css_start:css_start + 200]
        self.assertIn("height:", css_rule)
        self.assertNotIn("min-height:", css_rule)


if __name__ == "__main__":
    unittest.main()
