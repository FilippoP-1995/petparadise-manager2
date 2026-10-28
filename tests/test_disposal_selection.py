import tempfile
import unittest
from pathlib import Path

import app


class DisposalSelectionTests(unittest.TestCase):
    # Richiesta esplicita dell'utente (28/10/2026): Smaltimenti fluido allo
    # scorrimento e tasto "Programma scarico": una casella per ogni pratica
    # da smaltire, la selezione resta nella pagina (nessun ricaricamento) e
    # solo le pratiche spuntate vengono smaltite, dopo una conferma.

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        self.pages = []
        self.redirects = []
        self.handler.send_html = lambda html, *a: self.pages.append(html)
        self.handler.redirect = lambda path: self.redirects.append(path)
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.ids = [self.practice(f"CR-SM{i}", "Cremato", f"2026-10-{10 + i}", str(5 + i)) for i in range(4)]
        self.done = self.practice("CR-SMDONE", "Smaltito", "2026-10-12", "3")

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def practice(self, number, status, day, weight):
        stamp = app.now()
        with app.db() as conn:
            return conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,pickup_date,created_at,updated_at,created_by,
                   service_type,species,estimated_weight) VALUES(?,'Privato','Livorno',?,?,?,?,?,'Cremazione collettiva','Gatto',?)""",
                (number, status, day, stamp, stamp, self.admin["id"], weight)).lastrowid

    def page(self):
        self.handler.path = "/smaltimenti?dal=2026-10-01&al=2026-10-31"
        self.handler.disposal_page(self.admin)
        return self.pages[-1]

    def statuses(self):
        with app.db() as conn:
            return {row["id"]: row["status"] for row in conn.execute("SELECT id,status FROM practices")}

    def confirm(self, **form):
        self.handler.form = lambda: {"dal": "2026-10-01", "al": "2026-10-31", **form}
        self.handler.path = "/smaltimenti/conferma"
        self.redirects.clear()
        self.pages.clear()
        self.handler.disposal_confirm(self.admin)

    def test_page_offers_the_selection_only_on_practices_still_to_dispose(self):
        page = self.page()
        self.assertIn('<button type="button" class="btn ghost" onclick="disposalStartSelection(this)">Programma scarico</button>', page)
        for pid in self.ids:
            self.assertIn(f'<input type="checkbox" class="disposal-pick-input" value="{pid}"', page)
        self.assertNotIn(f'class="disposal-pick-input" value="{self.done}"', page)
        self.assertEqual(page.count('class="disposal-pick-all"'), 1)
        self.assertIn('<form id="disposalSelectionForm" method="post" action="/smaltimenti/conferma" hidden>', page)
        self.assertIn('data-disposal-submit disabled onclick="disposalSubmitSelection(this)">Smaltisci selezionate</button>', page)
        # Caselle nascoste finche' non si preme "Programma scarico".
        self.assertIn("main:not(.disposal-selecting) .disposal-pick{display:none!important}", app.CSS)
        # La selezione non ricarica la pagina: solo gestori nella pagina, conferma prima dell'invio.
        js = app.APP_JS
        self.assertIn("function disposalUpdateSelection(main){", js)
        self.assertIn("if(!confirm('Sei sicuro? Questa azione cambierà lo stato di '+label+' in Smaltito. Le pratiche non selezionate restano da smaltire.'))return;", js)
        # Filtro "Già smaltite": niente da selezionare, niente tasto.
        self.handler.path = "/smaltimenti?dal=2026-10-01&al=2026-10-31&stato=smaltito"
        self.handler.disposal_page(self.admin)
        self.assertNotIn('onclick="disposalStartSelection(this)"', self.pages[-1])

    def test_pending_and_disposed_practices_are_in_separate_blocks(self):
        # Richiesta esplicita dell'utente (28/10/2026): non piu' mescolate
        # negli stessi riquadri.
        page = self.page()
        pending_at = page.index('<h2 class="disposal-block-title">Da smaltire <span class="badge tag-orange">4</span></h2>')
        done_at = page.index('<h2 class="disposal-block-title disposal-block-title-done">Già smaltite <span class="badge tag-green">1</span></h2>')
        self.assertLess(pending_at, done_at)
        pending_block, done_block = page[pending_at:done_at], page[done_at:page.index("Storico scarichi")]
        for pid in self.ids:
            self.assertIn(f'class="disposal-pick-input" value="{pid}"', pending_block)
            self.assertNotIn(f"/pratiche/{pid}?", done_block)
        self.assertIn(f"/pratiche/{self.done}?", done_block)
        self.assertNotIn(f"/pratiche/{self.done}?", pending_block)
        self.assertNotIn("Già smaltita</span>", pending_block)
        self.assertNotIn("Da confermare</span>", done_block)
        self.assertIn('class="tablebox disposal-tablebox disposal-group disposal-group-pending"', pending_block)
        self.assertIn('class="tablebox disposal-tablebox disposal-group disposal-group-done"', done_block)
        self.assertIn('data-persist-key="Livorno·W"', pending_block)
        self.assertIn('data-persist-key="smaltite·Livorno·W"', done_block)
        self.assertIn('<span class="badge">4 animali</span>', pending_block)
        self.assertIn('<span class="badge">1 animali</span>', done_block)
        # Con il filtro "Da confermare" il blocco delle smaltite non compare.
        self.handler.path = "/smaltimenti?dal=2026-10-01&al=2026-10-31&stato=da_confermare"
        self.handler.disposal_page(self.admin)
        self.assertNotIn('<h2 class="disposal-block-title disposal-block-title-done">', self.pages[-1])

    def test_only_the_selected_practices_are_disposed(self):
        chosen = [self.ids[0], self.ids[2]]
        self.confirm(practice_ids=",".join(str(pid) for pid in chosen))
        batch_id = int(self.redirects[-1].rsplit("/", 1)[-1])
        statuses = self.statuses()
        self.assertEqual([statuses[pid] for pid in self.ids], ["Smaltito", "Cremato", "Smaltito", "Cremato"])
        with app.db() as conn:
            batch = conn.execute("SELECT * FROM disposal_batches WHERE id=?", (batch_id,)).fetchone()
            linked = sorted(r["practice_id"] for r in conn.execute("SELECT practice_id FROM disposal_batch_practices WHERE batch_id=?", (batch_id,)))
            history = conn.execute("SELECT COUNT(*) n FROM practice_history WHERE event_type='Smaltimento'").fetchone()["n"]
        self.assertEqual((batch["total_count"], linked, history), (2, sorted(chosen), 2))
        # Le altre restano selezionabili per lo scarico successivo.
        page = self.page()
        self.assertIn(f'class="disposal-pick-input" value="{self.ids[1]}"', page)
        self.assertNotIn(f'class="disposal-pick-input" value="{self.ids[0]}"', page)

    def test_invalid_selections_change_nothing(self):
        before = self.statuses()
        self.confirm(practice_ids="")
        self.assertIn("Seleziona almeno una pratica da smaltire.", self.pages[-1])
        # Una pratica gia' smaltita (o fuori periodo) nella selezione: nessuna modifica.
        self.confirm(practice_ids=f"{self.ids[0]},{self.done}")
        self.assertIn("Alcune pratiche selezionate non sono più da smaltire in questo periodo", self.pages[-1])
        self.assertEqual(self.statuses(), before)
        self.assertEqual(self.redirects, [])
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM disposal_batches").fetchone()["n"], 0)

    def test_confirm_without_selection_still_disposes_the_whole_period(self):
        self.confirm()
        statuses = self.statuses()
        self.assertEqual({statuses[pid] for pid in self.ids}, {"Smaltito"})

    def test_page_scrolls_natively_and_without_heavy_repaints(self):
        page = self.page()
        # Livorno da smaltire + Livorno gia' smaltite (blocchi separati dal 28/10) + storico
        self.assertEqual(page.count('<section class="tablebox disposal-tablebox'), 3)
        css = app.CSS
        # Niente riquadro con scorrimento interno ne' ombra sui riquadri molto alti.
        self.assertIn(".tablebox.disposal-tablebox{max-height:none;touch-action:auto;box-shadow:none!important}", css)
        self.assertIn(".tablebox:not(.archive-tablebox):not(.disposal-tablebox)", app.APP_JS)
        # Pulsazione degli avvisi urgenti del portale animata solo in opacita'.
        self.assertIn("@keyframes pa-pulse-glow{0%,100%{opacity:0}50%{opacity:1}}", css)
        self.assertNotIn(".partner-pill.urgent{background:#b91c1c;border-color:#fecaca;animation:pa-pulse", css)
        self.assertNotIn("box-shadow:0 10px 28px rgba(239,68,68,.45);animation:pa-pulse", css)


if __name__ == "__main__":
    unittest.main()
