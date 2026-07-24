import sqlite3
import unittest
import uuid
from datetime import date

import balance_service


class BalanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys=ON")
        balance_service.ensure_balance_schema(self.connection)

    def tearDown(self):
        self.connection.close()

    def movement(self, key="movement-1", **overrides):
        values = {
            "amount_cents": 12345,
            "movement_date": "2026-07-23",
            "category": "W",
            "ledger_section": "Entrata",
            "movement_type": "Acconto",
            "idempotency_key": key,
            "description": "Acconto di prova",
            "source": "test",
        }
        values.update(overrides)
        return balance_service.create_movement(self.connection, **values)

    def test_additive_schema_is_idempotent_and_contains_append_only_guards(self):
        self.connection.execute("CREATE TABLE unrelated_data(id INTEGER PRIMARY KEY)")
        first = self.movement()
        balance_service.ensure_balance_schema(self.connection)
        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        triggers = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        columns = {
            row[1]: row[2]
            for row in self.connection.execute(
                "PRAGMA table_info(balance_movements)"
            )
        }
        self.assertIn("unrelated_data", tables)
        self.assertIn("balance_movements", tables)
        self.assertEqual(columns["amount_cents"], "INTEGER")
        self.assertIn("balance_movements_no_update", triggers)
        self.assertIn("balance_movements_no_delete", triggers)
        self.assertEqual(balance_service.get_movements(self.connection), [first])

    def test_create_movement_stores_integer_cents_and_unique_uuid(self):
        movement = self.movement(
            amount_cents=10001,
            movement_date=date(2026, 7, 24),
            practice_id=17,
            practice_number_snapshot="CR-000017",
            payment_method="Pos",
            created_by=2,
        )
        self.assertEqual(movement.amount_cents, 10001)
        self.assertIsInstance(movement.amount_cents, int)
        self.assertEqual(movement.movement_date, "2026-07-24")
        self.assertEqual(uuid.UUID(movement.movement_uuid).version, 4)
        stored = self.connection.execute(
            """
            SELECT amount_cents,typeof(amount_cents),practice_id
            FROM balance_movements WHERE id=?
            """,
            (movement.id,),
        ).fetchone()
        self.assertEqual(stored, (10001, "integer", 17))

    def test_euro_conversion_is_exact_and_rejects_fractional_cents(self):
        self.assertEqual(balance_service.euros_to_cents("0.01"), 1)
        self.assertEqual(balance_service.euros_to_cents("120,50"), 12050)
        self.assertEqual(balance_service.euros_to_cents("330"), 33000)
        with self.assertRaises(balance_service.InvalidMovementError):
            balance_service.euros_to_cents("10.999")

    def test_accounting_category_uses_collaborator_d_w_precedence(self):
        self.assertEqual(
            balance_service.classify_category(
                has_total_d=False, is_collaborator=False
            ),
            "W",
        )
        self.assertEqual(
            balance_service.classify_category(
                has_total_d=True, is_collaborator=False
            ),
            "D",
        )
        self.assertEqual(
            balance_service.classify_category(
                has_total_d=True, is_collaborator=True
            ),
            "Collaboratori",
        )

    def test_identical_idempotent_retry_returns_original_without_duplicate(self):
        first = self.movement()
        retry = self.movement()
        self.assertEqual(retry, first)
        count = self.connection.execute(
            "SELECT COUNT(*) FROM balance_movements"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_reusing_idempotency_key_for_different_data_is_rejected(self):
        self.movement()
        with self.assertRaises(balance_service.IdempotencyConflictError):
            self.movement(amount_cents=999)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM balance_movements"
            ).fetchone()[0],
            1,
        )

    def test_duplicate_explicit_uuid_is_rejected(self):
        movement_uuid = str(uuid.uuid4())
        self.movement(movement_uuid=movement_uuid)
        with self.assertRaises(balance_service.DuplicateMovementError):
            self.movement(
                key="movement-2",
                movement_uuid=movement_uuid,
                amount_cents=500,
            )

    def test_invalid_amount_date_category_section_and_reserved_types_are_rejected(self):
        invalid_cases = (
            {"amount_cents": 0},
            {"amount_cents": 12.50},
            {"amount_cents": True},
            {"amount_cents": -100},
            {"movement_date": "23/07/2026"},
            {"movement_date": "2026-02-30"},
            {"category": "Contanti"},
            {"ledger_section": "Altro"},
            {"movement_type": "Rettifica"},
            {"movement_type": "Storno"},
        )
        for index, changes in enumerate(invalid_cases):
            with self.subTest(changes=changes):
                with self.assertRaises(balance_service.InvalidMovementError):
                    self.movement(key=f"invalid-{index}", **changes)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM balance_movements"
            ).fetchone()[0],
            0,
        )

    def test_adjustment_is_new_signed_movement_and_does_not_change_original(self):
        original = self.movement(
            category="D",
            amount_cents=33000,
            practice_id=18,
            practice_number_snapshot="CR-000018",
        )
        adjustment = balance_service.create_adjustment(
            self.connection,
            original_movement_id=original.id,
            amount_cents=-2000,
            movement_date="2026-07-25",
            idempotency_key="adjustment-1",
            description="Riduzione del totale",
            created_by=3,
        )
        self.assertEqual(adjustment.movement_type, "Rettifica")
        self.assertEqual(adjustment.related_movement_id, original.id)
        self.assertEqual(adjustment.amount_cents, -2000)
        self.assertEqual(adjustment.category, "D")
        unchanged = self.connection.execute(
            "SELECT amount_cents,category FROM balance_movements WHERE id=?",
            (original.id,),
        ).fetchone()
        self.assertEqual(unchanged, (33000, "D"))
        retry = balance_service.create_adjustment(
            self.connection,
            original_movement_id=original.id,
            amount_cents=-2000,
            movement_date="2026-07-25",
            idempotency_key="adjustment-1",
            description="Riduzione del totale",
            created_by=3,
        )
        self.assertEqual(retry, adjustment)

    def test_reversal_is_exact_opposite_and_only_one_is_allowed(self):
        original = self.movement(amount_cents=5000)
        reversal = balance_service.create_reversal(
            self.connection,
            original_movement_id=original.id,
            movement_date="2026-07-26",
            idempotency_key="reversal-1",
            description="Storno movimento errato",
        )
        self.assertEqual(reversal.movement_type, "Storno")
        self.assertEqual(reversal.amount_cents, -5000)
        self.assertEqual(reversal.related_movement_id, original.id)
        retry = balance_service.create_reversal(
            self.connection,
            original_movement_id=original.id,
            movement_date="2026-07-26",
            idempotency_key="reversal-1",
            description="Storno movimento errato",
        )
        self.assertEqual(retry, reversal)
        with self.assertRaises(balance_service.MovementAlreadyReversedError):
            balance_service.create_reversal(
                self.connection,
                original_movement_id=original.id,
                movement_date="2026-07-27",
                idempotency_key="reversal-2",
            )

    def test_legacy_reversal_voids_a_synthetic_row_without_related_movement_id(self):
        reversal = balance_service.create_legacy_reversal(
            self.connection,
            legacy_key="legacy-payment-movement:42",
            amount_cents=12000,
            category="W",
            ledger_section="Entrata",
            movement_date="2026-07-24",
            practice_id=9,
            practice_number_snapshot="CR-000009",
            payment_method="Contanti",
            description="Storno manuale: Acconto storico",
            created_by=1,
        )
        self.assertEqual(reversal.movement_type, "Storno")
        self.assertEqual(reversal.amount_cents, -12000)
        self.assertIsNone(reversal.related_movement_id)
        self.assertEqual(reversal.idempotency_key, "legacy-void:v1:legacy-payment-movement:42")
        retry = balance_service.create_legacy_reversal(
            self.connection,
            legacy_key="legacy-payment-movement:42",
            amount_cents=12000,
            category="W",
            ledger_section="Entrata",
            movement_date="2026-07-24",
            practice_id=9,
        )
        self.assertEqual(retry, reversal)
        count = self.connection.execute(
            "SELECT COUNT(*) FROM balance_movements WHERE idempotency_key=?",
            ("legacy-void:v1:legacy-payment-movement:42",),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_database_rejects_direct_update_and_delete(self):
        movement = self.movement()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.connection.execute(
                "UPDATE balance_movements SET amount_cents=1 WHERE id=?",
                (movement.id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.connection.execute(
                "DELETE FROM balance_movements WHERE id=?", (movement.id,)
            )
        stored = balance_service.get_movements(self.connection)
        self.assertEqual(stored, [movement])

    def test_get_movements_returns_complete_ledger_without_phase_two_filters(self):
        oldest = self.movement(
            key="oldest", amount_cents=100, movement_date="2026-07-20"
        )
        newest_first = self.movement(
            key="newest-first", amount_cents=200, movement_date="2026-07-22"
        )
        newest_second = self.movement(
            key="newest-second", amount_cents=300, movement_date="2026-07-22"
        )
        self.assertEqual(
            balance_service.get_movements(self.connection),
            [newest_second, newest_first, oldest],
        )


if __name__ == "__main__":
    unittest.main()
