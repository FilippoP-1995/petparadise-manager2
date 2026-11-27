import gzip
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app
import backup_service


class FakeS3:
    def __init__(self, files):
        self.files = files  # key -> local path

    def get_paginator(self, _name):
        files = self.files

        class Pager:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": key, "Size": Path(path).stat().st_size,
                                     "LastModified": datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)}
                                    for key, path in files.items() if key.startswith(Prefix)]}
        return Pager()

    def download_file(self, bucket, key, dest):
        shutil.copyfile(self.files[key], dest)


class BackupLookupTests(unittest.TestCase):
    # Richiesta esplicita dell'utente (27/11/2026): ritrovare una pratica
    # (Rachele Celentano, cane Rey) che non c'e' piu' ne' in archivio ne' nel
    # Cestino, cercandola dentro un backup notturno. Solo amministratore,
    # sola lettura.

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
        self.handler.send_html = lambda html, *a, **k: self.pages.append(html)
        self.errors = []
        self.handler.send_error = lambda code, msg=None: self.errors.append(code)
        with app.db() as conn:
            self.admin = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            stamp = app.now()
            self.pid = conn.execute(
                """INSERT INTO practices(practice_number,request_origin,destination_branch,status,created_at,updated_at,created_by,
                   animal_name,species,owner_first_name,owner_last_name,owner_phone) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("CR-000470", "Privato", "Livorno", "Ritirato", "2026-11-17T10:00:00", stamp, self.admin["id"], "Rey", "Cane", "Rachele", "Celentano", "3331234567"),
            ).lastrowid
            conn.execute("INSERT INTO practice_history(practice_id,event_type,old_value,new_value,user_id,created_at) VALUES(?,?,?,?,?,?)",
                         (self.pid, "Creazione", "", "CR-000470", self.admin["id"], "2026-11-17T10:00:00"))
        # backup notturno del 18/11 (copia compressa del database), poi la
        # pratica viene eliminata definitivamente dal gestionale
        raw = backup_service.create_local_backup(app.DB_PATH, app.DATA / "fake_remote", timestamp="20261118T010000Z")
        self.gz = backup_service.compress_backup(raw)
        with app.db() as conn:
            conn.execute("DELETE FROM practice_history WHERE practice_id=?", (self.pid,))
            conn.execute("DELETE FROM practices WHERE id=?", (self.pid,))
        self.key = "ppm-backups/" + self.gz.name
        self.config = backup_service.RemoteConfig("https://s3.example.com", "id", "secret", "bucket")
        self.fake = FakeS3({self.key: str(self.gz)})

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def open(self, path, user=None):
        self.handler.path = path
        with patch("backup_service.RemoteConfig.from_env", return_value=self.config), \
             patch("backup_service._s3_client", return_value=self.fake):
            self.handler.backup_lookup_page(user or self.admin)
        return self.pages[-1] if self.pages else ""

    def test_finds_a_practice_deleted_for_good_and_shows_all_its_data(self):
        page = self.open("/cestino/backup")
        self.assertIn("18/11/2026", page)
        page = self.open(f"/cestino/backup?backup={app.quote(self.key)}&q=celentano+rey")
        self.assertIn("1 pratica trovata nel backup", page)
        self.assertIn("<b>CR-000470</b>", page)
        self.assertNotIn("Ricerca avanzata", page)
        self.assertIn("NON PIÙ PRESENTE OGGI", page)
        page = self.open(f"/cestino/backup?backup={app.quote(self.key)}&q=celentano&pratica={self.pid}")
        self.assertIn("3331234567", page)
        self.assertIn("Creazione", page)
        page = self.open(f"/cestino/backup?backup={app.quote(self.key)}&q=Bianchi")
        self.assertIn("Nessuna pratica trovata", page)

    def test_read_only_and_admin_only(self):
        with app.db() as conn:
            before = conn.execute("SELECT count(*) FROM practices").fetchone()[0]
        self.open(f"/cestino/backup?backup={app.quote(self.key)}&q=rey")
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM practices").fetchone()[0], before)
        operator = dict(self.admin); operator["role"] = "operatore"
        self.pages.clear()
        self.open("/cestino/backup", user=operator)
        self.assertEqual(self.errors, [403])
        self.assertEqual(self.pages, [])
        # chiave non del bucket dei backup: rifiutata
        with self.assertRaises(backup_service.BackupError):
            backup_service.fetch_backup_for_lookup(self.config, "../pet_paradise.db", app.DATA)

    def test_trash_page_links_to_backup_search_for_admin(self):
        self.handler.path = "/cestino"
        self.handler.trash_page(self.admin)
        self.assertIn('href="/cestino/backup"', self.pages[-1])


if __name__ == "__main__":
    unittest.main()
