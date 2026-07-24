import json
import tempfile
import unittest
from pathlib import Path

import app
from balance_repair import repair_duplicate_balance_movements
from balance_service import (
    create_movement,
    create_reversal,
    get_balance_snapshot,
    get_movements,
    normalize_filters,
)


class BalanceRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old=(app.DATA,app.DB_PATH,app.DDT_DIR)
        app.DATA=Path(self.temp.name)
        app.DB_PATH=app.DATA/"test.db"
        app.DDT_DIR=app.DATA/"ddt"
        app.init_db()
        with app.db() as connection:
            self.admin=connection.execute(
                "SELECT * FROM users WHERE username='admin'"
            ).fetchone()

    def tearDown(self):
        app.DATA,app.DB_PATH,app.DDT_DIR=self.old
        self.temp.cleanup()

    def practice(self,number,animal,status,total,deposit=""):
        with app.db() as connection:
            return connection.execute(
                """
                INSERT INTO practices(
                  practice_number,animal_name,payment_status,total_service,
                  deposit,request_origin,destination_branch,status,created_at,
                  updated_at,created_by
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    number,animal,status,str(total),str(deposit),"Privato",
                    "Livorno","Ritirato","2026-07-01T09:00:00",
                    "2026-07-01T09:00:00",self.admin["id"],
                ),
            ).lastrowid

    def movement(self,practice_id,kind,amount,paid_at,key):
        with app.db() as connection:
            practice=connection.execute(
                "SELECT practice_number FROM practices WHERE id=?",(practice_id,)
            ).fetchone()
            return create_movement(
                connection,
                amount_cents=amount,
                movement_date=paid_at,
                category="W",
                ledger_section="Entrata",
                movement_type=kind,
                idempotency_key=key,
                practice_id=practice_id,
                practice_number_snapshot=practice["practice_number"],
                payment_method="Contanti",
                description="Pagamento pratica",
                source="practice_payment_transition",
                created_by=self.admin["id"],
            )

    def test_three_duplicate_deposits_are_repaired_append_only_and_idempotently(self):
        practice_id=self.practice("CR-000017","Molly","Acconto",300,180)
        movements=[
            self.movement(practice_id,"Acconto",18000,"2026-07-10",f"old-save-{n}")
            for n in range(3)
        ]
        with app.db() as connection:
            dry=repair_duplicate_balance_movements(connection,apply=False)
            raw_before=[dict(row) for row in connection.execute(
                "SELECT * FROM balance_movements ORDER BY id"
            )]
        self.assertEqual(dry["duplicate_count"],2)
        group=dry["duplicate_groups"][0]
        self.assertEqual(group["canonical_id"],movements[0].id)
        self.assertEqual(group["duplicate_ids"],[movements[1].id,movements[2].id])
        self.assertEqual(group["correction_cents"],-36000)
        self.assertEqual(len(raw_before),3)

        with app.db() as connection:
            applied=repair_duplicate_balance_movements(
                connection,apply=True,repaired_at="2026-07-24",
                created_by=self.admin["id"],
            )
            standard=get_movements(connection)
            audit=get_movements(connection,include_technical=True)
            raw_after=[dict(row) for row in connection.execute(
                "SELECT * FROM balance_movements ORDER BY id"
            )]
            snapshot=get_balance_snapshot(
                connection,
                filters=normalize_filters(date_to="2026-07-31"),
            )
        self.assertEqual(len(applied["created_reversal_ids"]),2)
        self.assertEqual([(row.movement_type,row.amount_cents) for row in standard],[("Acconto",18000)])
        self.assertEqual(len(audit),5)
        self.assertEqual(len(raw_after),5)
        self.assertEqual(snapshot.sections["entrate-w"].total_cents,18000)
        for row in raw_after[3:]:
            metadata=json.loads(row["metadata_json"])
            self.assertEqual(metadata["reason"],"duplicate_repair")
            self.assertEqual(metadata["canonical_movement_id"],movements[0].id)

        with app.db() as connection:
            second=repair_duplicate_balance_movements(connection,apply=True)
            count=connection.execute(
                "SELECT COUNT(*) n FROM balance_movements"
            ).fetchone()["n"]
        self.assertEqual(second["created_reversal_ids"],[])
        self.assertEqual(count,5)

    def test_application_startup_repairs_molly_duplicates_on_the_active_database(self):
        practice_id=self.practice("CR-000017","Molly","Acconto",300,180)
        for number in range(3):
            self.movement(
                practice_id,"Acconto",18000,"2026-07-12",
                f"historical-molly-save-{number}",
            )
        app.init_db()
        with app.db() as connection:
            standard=[
                row for row in get_movements(connection)
                if row.practice_id==practice_id
            ]
            audit=[
                row for row in get_movements(connection,include_technical=True)
                if row.practice_id==practice_id
            ]
            count_before=connection.execute(
                "SELECT COUNT(*) n FROM balance_movements WHERE practice_id=?",
                (practice_id,),
            ).fetchone()["n"]
        self.assertEqual(
            [(row.movement_type,row.amount_cents) for row in standard],
            [("Acconto",18000)],
        )
        self.assertEqual(len(audit),5)
        app.init_db()
        with app.db() as connection:
            count_after=connection.execute(
                "SELECT COUNT(*) n FROM balance_movements WHERE practice_id=?",
                (practice_id,),
            ).fetchone()["n"]
        self.assertEqual(count_after,count_before)

    def test_real_deposit_and_balance_or_different_dates_are_not_merged(self):
        practice_id=self.practice("CR-REAL","Reale","Pagato",300,100)
        self.movement(practice_id,"Acconto",10000,"2026-07-10","real-deposit")
        self.movement(practice_id,"Saldo",20000,"2026-07-20","real-balance")
        other_id=self.practice("CR-TWO","Due acconti","Acconto",400,200)
        self.movement(other_id,"Acconto",10000,"2026-07-10","deposit-one")
        self.movement(other_id,"Acconto",10000,"2026-07-12","deposit-two")
        with app.db() as connection:
            report=repair_duplicate_balance_movements(connection,apply=False)
        self.assertEqual(report["duplicate_count"],0)
        self.assertTrue(any(
            row["practice_id"]==other_id
            and row["reason"]=="same_phase_and_amount_but_different_dates"
            for row in report["ambiguous_groups"]
        ))

    def test_already_reversed_duplicate_is_not_repaired_again(self):
        practice_id=self.practice("CR-REVERSED","Stornato","Acconto",300,100)
        self.movement(practice_id,"Acconto",10000,"2026-07-10","canonical")
        duplicate=self.movement(
            practice_id,"Acconto",10000,"2026-07-10","duplicate"
        )
        with app.db() as connection:
            create_reversal(
                connection,
                original_movement_id=duplicate.id,
                movement_date="2026-07-10",
                idempotency_key="already-reversed",
                source="duplicate_repair",
            )
            report=repair_duplicate_balance_movements(connection,apply=False)
        self.assertEqual(report["duplicate_count"],0)

    def test_paid_amount_370_is_replaced_by_320_and_second_run_is_safe(self):
        practice_id=self.practice("CR-000063","Lexy Luna","Pagato",320)
        original=self.movement(
            practice_id,"Incasso completo",37000,"2026-07-15","old-370"
        )
        with app.db() as connection:
            dry=repair_duplicate_balance_movements(connection,apply=False)
        correction=dry["amount_corrections"][0]
        self.assertEqual(correction["practice_number"],"CR-000063")
        self.assertEqual(correction["animal_name"],"Lexy Luna")
        self.assertEqual(correction["ledger_amount_cents"],37000)
        self.assertEqual(correction["correct_amount_cents"],32000)
        self.assertEqual(correction["difference_cents"],-5000)

        with app.db() as connection:
            applied=repair_duplicate_balance_movements(
                connection,apply=True,created_by=self.admin["id"]
            )
            standard=get_movements(connection)
            audit=get_movements(connection,include_technical=True)
            original_after=connection.execute(
                "SELECT * FROM balance_movements WHERE id=?",(original.id,)
            ).fetchone()
        self.assertEqual(applied["amount_correction_total_cents"],-5000)
        self.assertEqual([(row.movement_type,row.amount_cents) for row in standard],[("Incasso completo",32000)])
        self.assertEqual([row.amount_cents for row in audit],[32000,-37000,37000])
        self.assertEqual(original_after["amount_cents"],37000)
        with app.db() as connection:
            second=repair_duplicate_balance_movements(connection,apply=True)
            self.assertEqual(len(get_movements(connection,include_technical=True)),3)
        self.assertEqual(second["created_replacement_ids"],[])

    def test_deposit_is_preserved_and_wrong_balance_is_corrected(self):
        practice_id=self.practice("CR-SPLIT-CORRECT","Split","Pagato",320,180)
        deposit=self.movement(
            practice_id,"Acconto",18000,"2026-07-10","deposit-real"
        )
        self.movement(practice_id,"Saldo",19000,"2026-07-20","balance-wrong")
        with app.db() as connection:
            report=repair_duplicate_balance_movements(connection,apply=True)
            standard=get_movements(connection)
            raw_deposit=connection.execute(
                "SELECT * FROM balance_movements WHERE id=?",(deposit.id,)
            ).fetchone()
        self.assertEqual(report["amount_corrections"][0]["correct_amount_cents"],14000)
        self.assertEqual(
            sorted((row.movement_type,row.amount_cents) for row in standard),
            [("Acconto",18000),("Saldo",14000)],
        )
        self.assertEqual(raw_deposit["amount_cents"],18000)

    def test_wrong_balance_is_fully_reversed_when_correct_remaining_is_zero(self):
        practice_id=self.practice("CR-ZERO-BALANCE","Saldo zero","Pagato",180,180)
        deposit=self.movement(
            practice_id,"Acconto",18000,"2026-07-10","zero-deposit-real"
        )
        wrong_balance=self.movement(
            practice_id,"Saldo",5000,"2026-07-20","zero-balance-wrong"
        )
        with app.db() as connection:
            dry=repair_duplicate_balance_movements(connection,apply=False)
            self.assertEqual(dry["amount_corrections"][0]["correct_amount_cents"],0)
            repair_duplicate_balance_movements(connection,apply=True)
            standard=get_movements(connection)
            audit=get_movements(connection,include_technical=True)
            raw=connection.execute(
                "SELECT amount_cents FROM balance_movements WHERE id IN (?,?) ORDER BY id",
                (deposit.id,wrong_balance.id),
            ).fetchall()
        self.assertEqual(
            [(row.movement_type,row.amount_cents) for row in standard],
            [("Acconto",18000)],
        )
        self.assertEqual([row["amount_cents"] for row in raw],[18000,5000])
        self.assertEqual(sorted(row.amount_cents for row in audit),[-5000,5000,18000])


if __name__=="__main__":
    unittest.main()
