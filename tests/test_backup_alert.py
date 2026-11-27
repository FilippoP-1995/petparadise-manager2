import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app
import backup_service


class BackupAlertTests(unittest.TestCase):
    # Richiesta esplicita dell'utente (27/11/2026): avviso ben visibile se il
    # backup notturno non riesce (i backup su S3 non partivano da tempo e
    # nessuno se n'era accorto).

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        self.config = backup_service.RemoteConfig("https://s3.example.com", "id", "secret", "bucket")

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def problem(self, config, when=datetime(2026, 11, 27, 9, 0)):
        with patch.dict(os.environ, {"RENDER": "true"}), patch("app.BackupRemoteConfig.from_env", return_value=config), app.db() as conn:
            return app.backup_health_problem(conn, when)

    def test_missing_s3_settings_on_render_is_reported(self):
        self.assertIn("NON configurato", self.problem(None))

    def test_no_recent_successful_backup_is_reported_with_the_last_error(self):
        self.assertIn("Ultimo backup riuscito: mai", self.problem(self.config))
        with app.db() as conn:
            app.record_backup_outcome(conn, True, "pet_paradise_20261126T010000Z.db.gz")
            conn.execute("UPDATE settings SET value='2026-11-27T02:00:00' WHERE key='backup_last_ok_at'")
        self.assertEqual(self.problem(self.config), "")
        # due notti senza backup riuscito: avviso con l'errore dell'ultima notte
        with app.db() as conn:
            app.record_backup_outcome(conn, False, "upload rifiutato")
        problem = self.problem(self.config, datetime(2026, 11, 28, 9, 0))
        self.assertIn("Ultimo backup riuscito: 27/11/2026 02:00", problem)
        self.assertIn("upload rifiutato", problem)

    def test_banner_only_for_admin_and_never_in_local_development(self):
        with patch.dict(os.environ, {"RENDER": "true"}), patch("app.BackupRemoteConfig.from_env", return_value=None):
            page = app.layout("Prova", '<main class="wrap">contenuto</main>', self.admin)
            self.assertIn('id="backupAlert"', page)
            operator = dict(self.admin); operator["role"] = "operatore"
            self.assertNotIn('id="backupAlert"', app.layout("Prova", '<main class="wrap">contenuto</main>', operator))
        env = {k: v for k, v in os.environ.items() if k != "RENDER"}
        with patch.dict(os.environ, env, clear=True):
            self.assertNotIn('id="backupAlert"', app.layout("Prova", '<main class="wrap">contenuto</main>', self.admin))

    def test_cron_records_success_failure_and_refused_calls(self):
        handler = object.__new__(app.App)
        handler.headers = {"X-Cron-Secret": "giusto"}
        handler.path = "/cron/backup"
        handler.send_json = lambda obj, status=200: None
        with patch.dict(os.environ, {"BACKUP_CRON_SECRET": "giusto"}), patch("app.emit_notification", return_value=[]):
            with patch("app.run_database_backup", return_value=backup_service.BackupResult(ok=True, backup_name="b.db.gz")):
                handler.backup_cron()
            with patch("app.run_database_backup", return_value=backup_service.BackupResult(ok=False, error="disco pieno")):
                handler.backup_cron()
            handler.headers = {"X-Cron-Secret": "sbagliato"}
            handler.backup_cron()
        with app.db() as conn:
            saved = dict(conn.execute("SELECT key,value FROM settings WHERE key LIKE 'backup_last_%'").fetchall())
        self.assertEqual(saved["backup_last_ok_name"], "b.db.gz")
        self.assertIn("Chiamata notturna rifiutata", saved["backup_last_error"])


if __name__ == "__main__":
    unittest.main()
