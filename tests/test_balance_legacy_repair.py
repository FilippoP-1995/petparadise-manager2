import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from balance_legacy_repair import (
    _create_backup,
    _main,
    apply_legacy_repairs,
    plan_legacy_repairs,
)
from balance_service import (
    create_legacy_reversal,
    create_movement,
    get_movements,
    get_recent_movement_deletions,
    log_movement_deletion,
    normalize_filters,
    restore_movement_deletion,
)


class LegacyRepairScriptTests(unittest.TestCase):
    """balance_legacy_repair.py: the one-time cleanup for practices whose
    historical Bilanci row was deleted before PR #82 (the practices columns
    were never cleared, only hidden from the ledger view). Every test here
    exercises the real plan/apply functions against a real SQLite file, the
    same way the CLI does — no mocking of the database itself."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        with app.db() as connection:
            self.admin = connection.execute(
                "SELECT * FROM users WHERE username='admin'"
            ).fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    # -- helpers ------------------------------------------------------------

    def deposit_practice(
        self, number, *, amount="100.00", total=300,
        deposit_paid_at="2026-07-10", status="Acconto",
    ):
        stamp = app.now()
        with app.db() as connection:
            pid = connection.execute(
                """INSERT INTO practices(
                     practice_number,request_origin,destination_branch,status,
                     created_at,updated_at,created_by,owner_first_name,
                     service_type,payment_status,price_cremation,total_service,
                     deposit,deposit_paid_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    number, "Privato", "Livorno", "Ritirato", stamp, stamp,
                    self.admin["id"], "Bilbo", "Cremazione singola", status,
                    str(total), str(total), amount, deposit_paid_at,
                ),
            ).lastrowid
        return pid

    def paid_practice(self, number, *, total=300, paid_at="2026-07-15"):
        stamp = app.now()
        with app.db() as connection:
            pid = connection.execute(
                """INSERT INTO practices(
                     practice_number,request_origin,destination_branch,status,
                     created_at,updated_at,created_by,owner_first_name,
                     service_type,payment_status,price_cremation,total_service,paid_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    number, "Privato", "Livorno", "Ritirato", stamp, stamp,
                    self.admin["id"], "Bilbo", "Cremazione singola", "Pagato",
                    str(total), str(total), paid_at,
                ),
            ).lastrowid
        return pid

    def old_style_legacy_delete(self, pid, kind, *, amount_cents, movement_date, category="W"):
        """Reproduces exactly what the pre-PR#82 code did: a legacy-void
        reversal plus a deletion log whose snapshot is only {"legacy_key":
        ...} — no practice_before, columns left untouched. This is the
        broken state the repair script exists to fix."""
        legacy_key = f"historical-practice:{pid}:{kind}"
        section = "Entrata"
        movement_type = "Acconto" if kind == "deposit" else "Saldo"
        with app.db() as connection:
            create_legacy_reversal(
                connection, legacy_key=legacy_key, amount_cents=amount_cents,
                category=category, ledger_section=section, movement_date=movement_date,
                practice_id=pid, practice_number_snapshot="", source="manual_void",
                created_by=self.admin["id"],
            )
            log_movement_deletion(
                connection, movement_date=movement_date, category=category,
                ledger_section=section, movement_type=movement_type,
                amount_cents=amount_cents, practice_id=pid, practice_number_snapshot="",
                deleted_by=self.admin["id"], deletion_kind="legacy_void",
                snapshot={"legacy_key": legacy_key},
            )
        return legacy_key

    def item_for(self, plan, deletion_id):
        return next(item for item in plan["items"] if item["deletion_id"] == deletion_id)

    # -- 1. dry-run never writes ---------------------------------------------

    def test_dry_run_never_modifies_the_database(self):
        pid = self.deposit_practice("CR-DRYRUN", amount="100.00", total=300)
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            before = dict(connection.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone())
            deletions_before = get_recent_movement_deletions(connection, limit=10)
            plan = plan_legacy_repairs(connection)
        self.assertEqual(plan["counts"].get("riparabile"), 1)
        with app.db() as connection:
            after = dict(connection.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone())
            deletions_after = get_recent_movement_deletions(connection, limit=10)
        self.assertEqual(before, after)
        self.assertEqual(
            [dict(row) for row in deletions_before], [dict(row) for row in deletions_after],
        )

    # -- 2. apply repairs a safely-repairable practice -----------------------

    def test_apply_repairs_a_safely_repairable_practice(self):
        pid = self.deposit_practice("CR-APPLY", amount="100.00", total=300)
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection)
            result = apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
        self.assertEqual(len(result["repaired"]), 1)
        self.assertEqual(result["errors"], [])
        with app.db() as connection:
            practice = connection.execute(
                "SELECT payment_status,deposit,deposit_paid_at,remaining_balance FROM practices WHERE id=?", (pid,)
            ).fetchone()
        self.assertEqual(practice["payment_status"], "Da saldare")
        self.assertEqual(practice["deposit"], "")
        self.assertEqual(practice["deposit_paid_at"], "")
        self.assertEqual(practice["remaining_balance"], "300.00")

    # -- 3. backup is mandatory before any --apply write --------------------

    def test_backup_is_created_before_apply_and_is_a_real_copy(self):
        pid = self.deposit_practice("CR-BACKUP", amount="100.00", total=300)
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        db_path = app.DB_PATH
        with patch(
            "sys.argv",
            ["balance_legacy_repair.py", "--database", str(db_path), "--apply"],
        ):
            _main()
        backups = sorted(db_path.parent.glob(f"{db_path.stem}.backup-*{db_path.suffix}"))
        self.assertEqual(len(backups), 1)
        self.assertGreater(backups[0].stat().st_size, 0)
        # the backup must be the pre-repair state (still showing the old data).
        import sqlite3
        backup_conn = sqlite3.connect(backups[0])
        backup_conn.row_factory = sqlite3.Row
        try:
            backup_deposit = backup_conn.execute(
                "SELECT deposit FROM practices WHERE id=?", (pid,)
            ).fetchone()["deposit"]
        finally:
            backup_conn.close()
        self.assertEqual(backup_deposit, "100.00")

    # -- 4. abort if the backup fails ----------------------------------------

    def test_apply_aborts_and_changes_nothing_if_the_backup_fails(self):
        pid = self.deposit_practice("CR-BACKUPFAIL", amount="100.00", total=300)
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        db_path = app.DB_PATH
        with self.assertRaises(OSError):
            with patch("balance_legacy_repair.shutil.copy2", side_effect=OSError("disco pieno")):
                with patch(
                    "sys.argv",
                    ["balance_legacy_repair.py", "--database", str(db_path), "--apply"],
                ):
                    _main()
        with app.db() as connection:
            practice = connection.execute(
                "SELECT deposit FROM practices WHERE id=?", (pid,)
            ).fetchone()
        self.assertEqual(practice["deposit"], "100.00", "il DB non deve essere toccato se il backup fallisce")
        backups = list(db_path.parent.glob(f"{db_path.stem}.backup-*{db_path.suffix}"))
        self.assertEqual(backups, [])

    # -- 5. deleting only the acconto, saldo still valid ---------------------

    def test_deleting_only_the_acconto_with_saldo_still_valid_repairs_only_that_leg(self):
        pid = self.deposit_practice("CR-ONLYACC", amount="100.00", total=300, status="Pagato")
        with app.db() as connection:
            connection.execute("UPDATE practices SET paid_at=? WHERE id=?", ("2026-07-15", pid))
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection)
            item = next(i for i in plan["items"] if i["kind"] == "deposit")
            self.assertEqual(item["status"], "riparabile")
            result = apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
        self.assertEqual(len(result["repaired"]), 1)
        with app.db() as connection:
            practice = connection.execute(
                "SELECT payment_status,deposit,paid_at,remaining_balance FROM practices WHERE id=?", (pid,)
            ).fetchone()
        # the saldo leg (paid_at) must be untouched by repairing only the acconto.
        self.assertEqual(practice["paid_at"], "2026-07-15")
        self.assertEqual(practice["deposit"], "")
        self.assertEqual(practice["payment_status"], "Pagato")
        self.assertEqual(practice["remaining_balance"], "0.00")

    # -- 6. deleting only the saldo, acconto still valid ---------------------

    def test_deleting_only_the_saldo_with_acconto_still_valid_repairs_only_that_leg(self):
        pid = self.deposit_practice("CR-ONLYSAL", amount="100.00", total=300, status="Pagato")
        with app.db() as connection:
            connection.execute("UPDATE practices SET paid_at=? WHERE id=?", ("2026-07-15", pid))
        self.old_style_legacy_delete(pid, "balance", amount_cents=20000, movement_date="2026-07-15")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection)
            item = next(i for i in plan["items"] if i["kind"] == "balance")
            self.assertEqual(item["status"], "riparabile")
            result = apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
        self.assertEqual(len(result["repaired"]), 1)
        with app.db() as connection:
            practice = connection.execute(
                "SELECT payment_status,deposit,deposit_paid_at,paid_at,remaining_balance FROM practices WHERE id=?", (pid,)
            ).fetchone()
        # the acconto leg (deposit/deposit_paid_at) must be untouched.
        self.assertEqual(practice["deposit"], "100.00")
        self.assertEqual(practice["deposit_paid_at"], "2026-07-10")
        self.assertEqual(practice["paid_at"], "")
        self.assertEqual(practice["payment_status"], "Acconto")
        self.assertEqual(practice["remaining_balance"], "200.00")

    # -- 7. practice with both a modern and a legacy row: ambiguous ---------

    def test_practice_with_a_modern_row_for_the_same_macroarea_is_ambiguous(self):
        pid = self.deposit_practice("CR-MODERNMIX", amount="100.00", total=300)
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            create_movement(
                connection, amount_cents=15000, movement_date="2026-08-01", category="W",
                ledger_section="Entrata", movement_type="Acconto",
                idempotency_key="modern-acconto-1", practice_id=pid,
                practice_number_snapshot="CR-MODERNMIX", source="practice_payment_transition",
                created_by=self.admin["id"],
            )
            plan = plan_legacy_repairs(connection)
        item = next(i for i in plan["items"] if i.get("kind") == "deposit")
        self.assertEqual(item["status"], "ambigua")
        self.assertIn("movimento moderno", item["reason"])

    # -- 8. already-correct practice is left alone ---------------------------

    def test_already_repaired_deletion_is_reported_as_gia_corretta(self):
        pid = self.deposit_practice("CR-ALREADYOK", amount="100.00", total=300)
        legacy_key = self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection)
            apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
            second_plan = plan_legacy_repairs(connection)
        self.assertEqual(second_plan["counts"], {"gia_corretta": 1})
        self.assertEqual(second_plan["by_status"]["gia_corretta"][0]["legacy_key"], legacy_key)

    # -- 9. duplicate historical log entries ---------------------------------

    def test_duplicate_deletion_log_rows_only_repair_through_the_first_one(self):
        pid = self.deposit_practice("CR-DUPLOG", amount="100.00", total=300)
        legacy_key = self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            # A second, stray log row for the very same legacy_key (should
            # never happen through the live UI — create_legacy_reversal's own
            # idempotency prevents it — but old data or a bug elsewhere could
            # still leave one behind; the script must not repair it twice).
            log_movement_deletion(
                connection, movement_date="2026-07-10", category="W", ledger_section="Entrata",
                movement_type="Acconto", amount_cents=10000, practice_id=pid, practice_number_snapshot="",
                deleted_by=self.admin["id"], deletion_kind="legacy_void",
                snapshot={"legacy_key": legacy_key},
            )
            plan = plan_legacy_repairs(connection)
        self.assertEqual(len(plan["duplicate_groups"]), 1)
        statuses = sorted(item["status"] for item in plan["items"])
        self.assertEqual(statuses, ["duplicata", "riparabile"])
        with app.db() as connection:
            result = apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
        self.assertEqual(len(result["repaired"]), 1)

    # -- 10. practice edited after the deletion: ambiguous -------------------

    def test_practice_edited_after_the_deletion_is_reported_ambiguous(self):
        pid = self.deposit_practice("CR-EDITED", amount="100.00", total=300)
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            # Someone later typed a *different* deposit into the same
            # (still-unrepaired) columns — the amount no longer matches what
            # the deletion log says was removed.
            connection.execute(
                "UPDATE practices SET deposit=? WHERE id=?", ("250.00", pid)
            )
            plan = plan_legacy_repairs(connection)
        item = plan["items"][0]
        self.assertEqual(item["status"], "ambigua")
        with app.db() as connection:
            practice_before = connection.execute(
                "SELECT deposit FROM practices WHERE id=?", (pid,)
            ).fetchone()["deposit"]
            apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
            practice_after = connection.execute(
                "SELECT deposit FROM practices WHERE id=?", (pid,)
            ).fetchone()["deposit"]
        self.assertEqual(practice_before, practice_after, "una pratica ambigua non deve mai essere modificata")

    # -- 11. an error during the UPDATE rolls back cleanly -------------------

    def test_an_error_during_the_repair_rolls_back_and_is_reported_as_an_error(self):
        pid = self.deposit_practice("CR-DBERR", amount="100.00", total=300)
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection)
            with patch(
                "balance_legacy_repair._ONE_TIME_APP_HANDLER.delete_legacy_practice_column_movement",
                side_effect=RuntimeError("boom"),
            ):
                result = apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
        self.assertEqual(result["repaired"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("boom", result["errors"][0]["error"])
        with app.db() as connection:
            practice = connection.execute(
                "SELECT deposit,payment_status FROM practices WHERE id=?", (pid,)
            ).fetchone()
            deletions = get_recent_movement_deletions(connection, limit=1)
        self.assertEqual((practice["deposit"], practice["payment_status"]), ("100.00", "Acconto"))
        self.assertNotIn("practice_before", json.loads(deletions[0]["snapshot_json"]))

    # -- 12. re-running the script changes nothing further -------------------

    def test_rerunning_after_a_successful_repair_makes_no_further_changes(self):
        pid = self.deposit_practice("CR-RERUN", amount="100.00", total=300)
        self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection)
            apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
            after_first = dict(connection.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone())
            second_plan = plan_legacy_repairs(connection)
            second_result = apply_legacy_repairs(connection, second_plan, default_created_by=self.admin["id"])
            after_second = dict(connection.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone())
        self.assertEqual(second_result, {"repaired": [], "errors": []})
        self.assertEqual(after_first, after_second)

    # -- 13. --practice-id filter --------------------------------------------

    def test_practice_id_filter_only_analyzes_the_requested_practice(self):
        first = self.deposit_practice("CR-FILTER-1", amount="100.00", total=300, deposit_paid_at="2026-07-10")
        second = self.deposit_practice("CR-FILTER-2", amount="120.00", total=300, deposit_paid_at="2026-07-11")
        self.old_style_legacy_delete(first, "deposit", amount_cents=10000, movement_date="2026-07-10")
        self.old_style_legacy_delete(second, "deposit", amount_cents=12000, movement_date="2026-07-11")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection, practice_id=second)
        self.assertEqual(plan["analyzed"], 1)
        self.assertEqual(plan["items"][0]["practice_id"], second)
        with app.db() as connection:
            apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
            first_practice = connection.execute("SELECT deposit FROM practices WHERE id=?", (first,)).fetchone()
            second_practice = connection.execute("SELECT deposit FROM practices WHERE id=?", (second,)).fetchone()
        self.assertEqual(first_practice["deposit"], "100.00", "la pratica non filtrata non deve essere toccata")
        self.assertEqual(second_practice["deposit"], "")

    # -- 14. payment_status is correctly recomputed, not hardcoded ----------

    def test_payment_status_recompute_accounts_for_surviving_receipts(self):
        # A fully-paid practice (deposit + balance both historical) whose
        # *balance* leg was already deleted before PR #82 (deposit leg
        # untouched): repairing the balance leg must fall back to "Acconto"
        # (the deposit is still genuinely valid), never a hardcoded status.
        pid = self.deposit_practice("CR-STATUSRECALC", amount="100.00", total=300, status="Pagato")
        with app.db() as connection:
            connection.execute("UPDATE practices SET paid_at=? WHERE id=?", ("2026-07-15", pid))
        self.old_style_legacy_delete(pid, "balance", amount_cents=20000, movement_date="2026-07-15")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection)
            apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
            practice = connection.execute(
                "SELECT payment_status,remaining_balance FROM practices WHERE id=?", (pid,)
            ).fetchone()
        self.assertEqual(practice["payment_status"], "Acconto")
        self.assertEqual(practice["remaining_balance"], "200.00")

    # -- 15. the movement can still be restored afterwards -------------------

    def test_the_movement_can_still_be_restored_after_the_repair(self):
        pid = self.deposit_practice("CR-STILLRESTORABLE", amount="100.00", total=300)
        legacy_key = self.old_style_legacy_delete(pid, "deposit", amount_cents=10000, movement_date="2026-07-10")
        with app.db() as connection:
            plan = plan_legacy_repairs(connection)
            apply_legacy_repairs(connection, plan, default_created_by=self.admin["id"])
            deletion_id = get_recent_movement_deletions(connection, limit=1)[0]["id"]
            restore_movement_deletion(connection, deletion_id=deletion_id, restored_by=self.admin["id"])
            practice = connection.execute(
                "SELECT payment_status,deposit,deposit_paid_at FROM practices WHERE id=?", (pid,)
            ).fetchone()
            movements = get_movements(connection, filters=normalize_filters())
        self.assertEqual(practice["payment_status"], "Acconto")
        self.assertEqual(practice["deposit"], "100.00")
        self.assertEqual(practice["deposit_paid_at"], "2026-07-10")
        self.assertTrue(any(m.idempotency_key == legacy_key for m in movements))


if __name__ == "__main__":
    unittest.main()
