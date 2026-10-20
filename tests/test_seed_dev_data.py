import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "seed_dev_data.py"


def run_seed(data_dir, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RENDER", "PPM_", "WHATSAPP_", "SMTP_"))}
    env["TZ"] = "Europe/Rome"
    if data_dir is not None:
        env["PPM_DATA_DIR"] = str(data_dir)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)


class SeedDevDataTests(unittest.TestCase):
    # Richiesta esplicita dell'utente: dati finti realistici per lo sviluppo
    # (clienti, pratiche in tutti gli stati, cliniche del portale con
    # richieste), mai sul database di produzione.

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.data_dir = Path(cls.temp.name) / "devdata"
        cls.result = run_seed(cls.data_dir)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def query(self, sql):
        conn = sqlite3.connect(self.data_dir / "pet_paradise.db")
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_seed_runs_and_covers_every_practice_state(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr[-2000:])
        states = {row[0] for row in self.query("SELECT DISTINCT status FROM practices")}
        self.assertEqual(states, set(app.STATES))
        payments = {row[0] for row in self.query("SELECT DISTINCT payment_status FROM practices")}
        self.assertEqual(payments, set(app.PAYMENT_STATES))
        self.assertGreater(self.query("SELECT COUNT(*) FROM clients")[0][0], 20)
        self.assertGreater(self.query("SELECT COUNT(*) FROM balance_movements")[0][0], 0)
        self.assertGreater(self.query("SELECT COUNT(*) FROM cremation_cycles")[0][0], 0)

    def test_seed_creates_partner_clinics_with_requests_in_every_public_state(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr[-2000:])
        self.assertEqual(self.query("SELECT COUNT(*) FROM partner_clinics WHERE is_demo=0")[0][0], 3)
        statuses = {row[0] for row in self.query("SELECT DISTINCT public_status FROM partner_request_status")}
        self.assertTrue({"ricevuta", "programmato", "ritirato", "in_lavorazione", "pronto_riconsegna",
                         "completata", "annullata", "in_congelatore"} <= statuses, statuses)
        logins = self.query("SELECT COUNT(*) FROM partner_users WHERE username LIKE 'prova.%' AND password_hash<>''")
        self.assertEqual(logins[0][0], 3)

    def test_seed_uses_only_fake_contacts_and_sends_nothing(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr[-2000:])
        emails = {row[0] for row in self.query("SELECT owner_email FROM practices WHERE owner_email<>''")}
        self.assertTrue(emails and all(e.endswith("@example.com") for e in emails), emails)
        pending = self.query("SELECT COUNT(*) FROM whatsapp_messages WHERE status IN ('programmato','in_invio')")
        self.assertEqual(pending[0][0], 0)

    def test_seed_refuses_production_disk_missing_dir_render_and_existing_data(self):
        self.assertIn("/var/data", run_seed("/var/data").stderr)
        self.assertNotEqual(run_seed(None).returncode, 0)
        refused = run_seed(Path(self.temp.name) / "other", {"RENDER": "true"})
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("Render", refused.stderr)
        again = run_seed(self.data_dir)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("contiene gia'", again.stderr)


if __name__ == "__main__":
    unittest.main()
