import hashlib
import tempfile
import unittest
from pathlib import Path

import app
from balance_migration import (
    dry_run_database,
    migrate_historical_data,
    plan_historical_migration,
)
from balance_service import (
    create_movement,
    get_outstanding_balances,
    normalize_filters,
)


class HistoricalBalanceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        with app.db() as connection:
            self.admin_id = connection.execute(
                "SELECT id FROM users WHERE username='admin'"
            ).fetchone()["id"]

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def practice(
        self,
        connection,
        number,
        status,
        *,
        total_w="300",
        total_d="",
        deposit="",
        deposit_final="",
        deposit_paid_at="",
        paid_at="",
        request_origin="Privato",
        collaborator_id=None,
    ):
        stamp = "2026-01-01T09:00:00"
        return connection.execute(
            """
            INSERT INTO practices(
              practice_number,request_origin,destination_branch,status,
              created_at,updated_at,created_by,animal_name,payment_status,
              total_service,total_text,deposit,deposit_final,deposit_paid_at,
              paid_at,payment_method,collaborator_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                number,request_origin,"Livorno","Ritirato",stamp,stamp,
                self.admin_id,"Fido",status,total_w,total_d,deposit,
                deposit_final,deposit_paid_at,paid_at,"Pos",collaborator_id,
            ),
        ).lastrowid

    def old_payment(self, connection, practice_id, kind, amount, paid_at):
        return connection.execute(
            """
            INSERT INTO payment_movements(
              practice_id,payment_type,payment_channel,payment_method,
              movement_category,amount,paid_at,user_id,notes,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                practice_id,kind,"ordinario","Pos","W",amount,paid_at,
                self.admin_id,"movimento storico","2026-01-01T09:00:00",
            ),
        ).lastrowid

    def seed_history(self, connection):
        due = self.practice(connection,"CR-H-DUE","Da saldare")
        deposit_old = self.practice(connection,"CR-H-DEP-OLD","Acconto")
        self.old_payment(
            connection,deposit_old,"acconto_ordinario",100,
            "2026-01-10T10:30:00",
        )
        deposit_fallback = self.practice(
            connection,"CR-H-DEP-FIELD","Acconto",deposit="80",
            deposit_paid_at="2026-01-11T11:00:00",
        )
        missing_deposit_date = self.practice(
            connection,"CR-H-DEP-NODATE","Acconto",deposit="50",
        )
        paid_split = self.practice(connection,"CR-H-PAID-SPLIT","Pagato")
        self.old_payment(
            connection,paid_split,"acconto_ordinario",100,
            "2026-01-12T09:00:00",
        )
        self.old_payment(
            connection,paid_split,"saldo_ordinario",200,
            "2026-01-20T15:00:00",
        )
        paid_full = self.practice(
            connection,"CR-H-PAID-FULL","Pagato",total_w="400",
            total_d="330",
        )
        self.old_payment(
            connection,paid_full,"saldo_d",330,"2026-01-21T15:00:00",
        )
        paid_missing_date = self.practice(
            connection,"CR-H-PAID-NODATE","Pagato",
        )
        return {
            "due":due,
            "deposit_old":deposit_old,
            "deposit_fallback":deposit_fallback,
            "missing_deposit_date":missing_deposit_date,
            "paid_split":paid_split,
            "paid_full":paid_full,
            "paid_missing_date":paid_missing_date,
        }

    def test_plan_uses_real_legacy_dates_and_reports_missing_dates(self):
        with app.db() as connection:
            ids = self.seed_history(connection)
            report = plan_historical_migration(connection)
        self.assertEqual(report.historical_practices_found,7)
        self.assertEqual(report.due_practices,1)
        self.assertEqual(report.migratable_movements,5)
        self.assertEqual(
            [item.movement_type for item in report.candidates],
            ["Acconto","Acconto","Acconto","Saldo","Incasso completo"],
        )
        by_number = {}
        for item in report.candidates:
            by_number.setdefault(item.practice_number,[]).append(item)
        self.assertEqual(
            by_number["CR-H-DEP-OLD"][0].movement_date,"2026-01-10",
        )
        self.assertEqual(
            by_number["CR-H-DEP-FIELD"][0].movement_date,"2026-01-11",
        )
        self.assertEqual(
            [item.amount_cents for item in by_number["CR-H-PAID-SPLIT"]],
            [10000,20000],
        )
        self.assertEqual(by_number["CR-H-PAID-FULL"][0].category,"D")
        anomaly_codes = {
            (item.practice_id,item.code) for item in report.anomalies
        }
        self.assertIn(
            (ids["missing_deposit_date"],"MISSING_DEPOSIT_DATE"),
            anomaly_codes,
        )
        self.assertIn(
            (ids["paid_missing_date"],"MISSING_PAID_DATE"),anomaly_codes,
        )

    def test_dry_run_is_read_only_and_repeatable(self):
        with app.db() as connection:
            self.seed_history(connection)
            before = {
                table:connection.execute(
                    f"SELECT count(*) n FROM {table}"
                ).fetchone()["n"]
                for table in (
                    "practices","payment_movements","balance_movements"
                )
            }
            first = migrate_historical_data(connection,dry_run=True)
            second = migrate_historical_data(connection,dry_run=True)
            after = {
                table:connection.execute(
                    f"SELECT count(*) n FROM {table}"
                ).fetchone()["n"]
                for table in before
            }
        self.assertEqual(before,after)
        self.assertEqual(first.candidates,second.candidates)
        self.assertEqual(first.created_movements,0)
        self.assertTrue(first.dry_run)

    def test_read_only_database_dry_run_does_not_change_file(self):
        with app.db() as connection:
            self.seed_history(connection)
        before_hash = hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest()
        report = dry_run_database(app.DB_PATH)
        after_hash = hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest()
        self.assertEqual(before_hash,after_hash)
        self.assertEqual(report.migratable_movements,5)

    def test_apply_on_test_database_is_idempotent_and_preserves_legacy(self):
        with app.db() as connection:
            self.seed_history(connection)
            old_snapshot = [
                tuple(row) for row in connection.execute(
                    "SELECT * FROM payment_movements ORDER BY id"
                )
            ]
            practices_before = connection.execute(
                "SELECT count(*) n FROM practices"
            ).fetchone()["n"]
            first = migrate_historical_data(connection,dry_run=False)
            second = migrate_historical_data(connection,dry_run=False)
            old_after = [
                tuple(row) for row in connection.execute(
                    "SELECT * FROM payment_movements ORDER BY id"
                )
            ]
            count = connection.execute(
                "SELECT count(*) n FROM balance_movements"
            ).fetchone()["n"]
            practices_after = connection.execute(
                "SELECT count(*) n FROM practices"
            ).fetchone()["n"]
        self.assertEqual(first.created_movements,5)
        self.assertEqual(second.created_movements,0)
        self.assertEqual(second.duplicates_avoided,5)
        self.assertEqual(count,5)
        self.assertEqual(old_snapshot,old_after)
        self.assertEqual(practices_before,practices_after)

    def test_existing_semantic_movements_are_not_duplicated(self):
        with app.db() as connection:
            ids = self.seed_history(connection)
            create_movement(
                connection,
                practice_id=ids["deposit_old"],
                practice_number_snapshot="CR-H-DEP-OLD",
                category="W",
                ledger_section="Entrata",
                movement_type="Acconto",
                amount_cents=10000,
                movement_date="2026-01-10",
                payment_method="Pos",
                created_by=self.admin_id,
                idempotency_key="already-created-with-new-key",
                source="test",
            )
            report = plan_historical_migration(connection)
        self.assertEqual(report.duplicates_avoided,1)
        self.assertEqual(report.migratable_movements,4)

    def test_outstanding_uses_legacy_evidence_without_double_counting(self):
        with app.db() as connection:
            ids = self.seed_history(connection)
            filters = normalize_filters(
                date_from="2026-01-15",date_to="2026-01-15",
                category=None,payment_method=None,operator_id=None,search="",
            )
            rows = get_outstanding_balances(connection,filters=filters)
            by_number = {row.practice_number:row for row in rows}
            self.assertEqual(
                by_number["CR-H-DEP-OLD"].remaining_cents,20000,
            )
            self.assertEqual(
                by_number["CR-H-DEP-FIELD"].remaining_cents,22000,
            )
            self.assertEqual(
                by_number["CR-H-DEP-NODATE"].remaining_cents,30000,
            )
            self.assertEqual(
                by_number["CR-H-PAID-SPLIT"].remaining_cents,20000,
            )
            self.assertEqual(by_number["CR-H-DUE"].remaining_cents,30000)
            migrate_historical_data(connection,dry_run=False)
            migrated_rows = get_outstanding_balances(
                connection,filters=filters
            )
            migrated = {
                row.practice_number:row for row in migrated_rows
            }
        self.assertEqual(
            migrated["CR-H-DEP-OLD"].remaining_cents,20000,
        )
        self.assertEqual(
            migrated["CR-H-PAID-SPLIT"].remaining_cents,20000,
        )


if __name__ == "__main__":
    unittest.main()
