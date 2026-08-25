import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup_service
from backup_service import (
    BackupError,
    RemoteConfig,
    compress_backup,
    create_local_backup,
    run_database_backup,
    verify_backup_integrity,
)


class BackupServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "source.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE practices(id INTEGER PRIMARY KEY, practice_number TEXT NOT NULL)")
        conn.execute("INSERT INTO practices(practice_number) VALUES ('CR-000001'), ('CR-000002')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_create_local_backup_produces_a_valid_copy_with_the_same_data(self):
        # VACUUM INTO deve produrre una copia leggibile e con gli stessi
        # dati, indipendentemente da scritture concorrenti sull'originale
        # (qui non simulate, ma la copia deve comunque essere corretta nel
        # caso semplice).
        dest_dir = self.root / "backups"
        backup_path = create_local_backup(self.db_path, dest_dir, timestamp="20260101T000000Z")
        self.assertTrue(backup_path.exists())
        self.assertEqual(backup_path.name, "pet_paradise_20260101T000000Z.db")
        conn = sqlite3.connect(backup_path)
        rows = conn.execute("SELECT practice_number FROM practices ORDER BY id").fetchall()
        conn.close()
        self.assertEqual([r[0] for r in rows], ["CR-000001", "CR-000002"])

    def test_create_local_backup_overwrites_a_stale_file_with_the_same_name(self):
        dest_dir = self.root / "backups"
        dest_dir.mkdir()
        stale = dest_dir / "pet_paradise_20260101T000000Z.db"
        stale.write_text("not a real database")
        backup_path = create_local_backup(self.db_path, dest_dir, timestamp="20260101T000000Z")
        conn = sqlite3.connect(backup_path)
        count = conn.execute("SELECT COUNT(*) FROM practices").fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)

    def test_verify_backup_integrity_passes_on_a_real_database(self):
        backup_path = create_local_backup(self.db_path, self.root / "backups")
        verify_backup_integrity(backup_path)  # non deve sollevare

    def test_verify_backup_integrity_rejects_a_corrupted_file(self):
        fake = self.root / "fake.db"
        fake.write_bytes(b"questo non e' un file sqlite valido" * 50)
        with self.assertRaises(BackupError):
            verify_backup_integrity(fake)

    def test_compress_backup_roundtrips_the_exact_bytes(self):
        backup_path = create_local_backup(self.db_path, self.root / "backups")
        original_bytes = backup_path.read_bytes()
        gz_path = compress_backup(backup_path)
        self.assertTrue(gz_path.name.endswith(".db.gz"))
        with gzip.open(gz_path, "rb") as f:
            self.assertEqual(f.read(), original_bytes)

    def test_remote_config_from_env_returns_none_when_incomplete(self):
        with patch.dict("os.environ", {}, clear=False):
            for key in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_KEY_ID", "BACKUP_S3_APPLICATION_KEY", "BACKUP_S3_BUCKET"):
                os_environ_pop(key)
            self.assertIsNone(RemoteConfig.from_env())

    def test_remote_config_from_env_reads_all_fields_with_defaults(self):
        env = {
            "BACKUP_S3_ENDPOINT": "https://s3.example.com",
            "BACKUP_S3_KEY_ID": "key123",
            "BACKUP_S3_APPLICATION_KEY": "secret456",
            "BACKUP_S3_BUCKET": "ppm-backups-bucket",
        }
        with patch.dict("os.environ", env, clear=False):
            config = RemoteConfig.from_env()
        self.assertIsNotNone(config)
        self.assertEqual(config.endpoint_url, "https://s3.example.com")
        self.assertEqual(config.bucket, "ppm-backups-bucket")
        self.assertEqual(config.prefix, "ppm-backups/")
        self.assertEqual(config.retention_days, 30)

    def test_run_database_backup_fails_cleanly_without_configuration(self):
        with patch.dict("os.environ", {}, clear=False):
            for key in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_KEY_ID", "BACKUP_S3_APPLICATION_KEY", "BACKUP_S3_BUCKET"):
                os_environ_pop(key)
            result = run_database_backup(self.db_path, tmp_dir=self.root / "backups")
        self.assertFalse(result.ok)
        self.assertIn("BACKUP_S3_ENDPOINT", result.error)

    def test_run_database_backup_uploads_and_cleans_up_local_files_on_success(self):
        env = {
            "BACKUP_S3_ENDPOINT": "https://s3.example.com",
            "BACKUP_S3_KEY_ID": "key123",
            "BACKUP_S3_APPLICATION_KEY": "secret456",
            "BACKUP_S3_BUCKET": "ppm-backups-bucket",
        }
        seen_paths = []

        def fake_upload(local_path, config):
            seen_paths.append(Path(local_path))
            self.assertTrue(local_path.exists())
            return f"{config.prefix}{local_path.name}"

        with patch.dict("os.environ", env, clear=False), \
             patch.object(backup_service, "upload_backup", side_effect=fake_upload), \
             patch.object(backup_service, "cleanup_old_remote_backups", return_value=2):
            result = run_database_backup(self.db_path, tmp_dir=self.root / "backups")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.deleted_remote, 2)
        self.assertTrue(result.backup_name.endswith(".db.gz"))
        self.assertGreater(result.size_bytes, 0)
        # la copia locale (.db e .db.gz) non deve restare sul disco dopo il run
        self.assertEqual(len(seen_paths), 1)
        self.assertFalse(seen_paths[0].exists())

    def test_run_database_backup_reports_error_when_upload_fails(self):
        env = {
            "BACKUP_S3_ENDPOINT": "https://s3.example.com",
            "BACKUP_S3_KEY_ID": "key123",
            "BACKUP_S3_APPLICATION_KEY": "secret456",
            "BACKUP_S3_BUCKET": "ppm-backups-bucket",
        }
        with patch.dict("os.environ", env, clear=False), \
             patch.object(backup_service, "upload_backup", side_effect=RuntimeError("rete non disponibile")):
            result = run_database_backup(self.db_path, tmp_dir=self.root / "backups")
        self.assertFalse(result.ok)
        self.assertIn("rete non disponibile", result.error)
        # anche in caso di errore, nessun file temporaneo deve restare
        leftovers = list((self.root / "backups").glob("*"))
        self.assertEqual(leftovers, [])


def os_environ_pop(key):
    import os
    os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main()
