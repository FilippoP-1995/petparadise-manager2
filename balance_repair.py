from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from balance_service import (
    TECHNICAL_REVERSAL_SOURCES,
    correct_movement_amount,
    create_reversal,
    ensure_balance_schema,
)


PROCEDURE_VERSION = "1"
ECONOMIC_TYPES = ("Acconto", "Saldo", "Incasso completo")
PRACTICE_SOURCES = (
    "practice_payment_transition",
    "practice_creation",
    "amount_correction",
    "payment_date_correction",
)


def _dict_rows(connection: sqlite3.Connection, sql: str, params=()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def _cents(value: object) -> int:
    text=str(value or "").strip().replace(",", ".")
    if not text:
        return 0
    try:
        amount=Decimal(text)
    except InvalidOperation:
        return 0
    if not amount.is_finite():
        return 0
    return max(0, int((amount*100).quantize(Decimal("1"))))


def _phase(movement_type: str) -> str:
    if movement_type=="Acconto":
        return "deposit"
    if movement_type=="Saldo":
        return "settlement"
    return "full_payment"


def _source_family(source: str) -> str:
    return "practice_economic" if source in PRACTICE_SOURCES else source


def _active_economic_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    technical=",".join("?" for _ in TECHNICAL_REVERSAL_SOURCES)
    columns={
        row["name"] if isinstance(row,sqlite3.Row) else row[1]
        for row in connection.execute("PRAGMA table_info(balance_movements)")
    }
    metadata_suffix = "" if "metadata_json" in columns else ",'' AS metadata_json"
    return _dict_rows(
        connection,
        f"""
        SELECT b.*{metadata_suffix},p.practice_number,p.animal_name,p.payment_status,
               p.total_service,p.total_text,p.deposit,p.deposit_final,
               p.request_origin,p.collaborator_id AS practice_collaborator_id
        FROM balance_movements b
        JOIN practices p ON p.id=b.practice_id
        WHERE b.ledger_section='Entrata'
          AND b.movement_type IN ('Acconto','Saldo','Incasso completo')
          AND b.amount_cents>0
          AND NOT EXISTS(
            SELECT 1 FROM balance_movements reversal
            WHERE reversal.related_movement_id=b.id
              AND reversal.movement_type='Storno'
          )
          AND NOT (b.source IN ({technical}) AND b.related_movement_id IS NOT NULL)
        ORDER BY b.practice_id,b.id
        """,
        TECHNICAL_REVERSAL_SOURCES,
    )


def _duplicate_plan(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    possible: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source_family=_source_family(str(row["source"] or ""))
        if source_family!="practice_economic":
            continue
        exact_key=(
            row["practice_id"], _phase(row["movement_type"]), row["movement_type"],
            row["amount_cents"], row["movement_date"], row["category"],
            row["ledger_section"], row["payment_method"] or "", source_family,
        )
        groups[exact_key].append(row)
        possible_key=(
            row["practice_id"], _phase(row["movement_type"]), row["movement_type"],
            row["amount_cents"], row["category"], source_family,
        )
        possible[possible_key].append(row)
    duplicates=[]
    exact_ids=set()
    for key, members in groups.items():
        if len(members)<2:
            continue
        members=sorted(members,key=lambda item:item["id"])
        canonical=members[0]
        duplicate_rows=members[1:]
        exact_ids.update(row["id"] for row in members)
        duplicates.append({
            "practice_id":canonical["practice_id"],
            "practice_number":canonical["practice_number"],
            "animal_name":canonical["animal_name"],
            "economic_phase":_phase(canonical["movement_type"]),
            "movement_type":canonical["movement_type"],
            "movement_date":canonical["movement_date"],
            "amount_cents":canonical["amount_cents"],
            "canonical_id":canonical["id"],
            "canonical_idempotency_key":canonical["idempotency_key"],
            "duplicate_ids":[row["id"] for row in duplicate_rows],
            "duplicate_idempotency_keys":[row["idempotency_key"] for row in duplicate_rows],
            "source_values":sorted({str(row["source"] or "") for row in members}),
            "metadata_values":[str(row.get("metadata_json") or "") for row in members],
            "correction_cents":-sum(row["amount_cents"] for row in duplicate_rows),
        })
    ambiguous=[]
    for members in possible.values():
        dates={row["movement_date"] for row in members}
        if len(members)>1 and len(dates)>1 and not any(row["id"] in exact_ids for row in members):
            first=min(members,key=lambda item:item["id"])
            ambiguous.append({
                "practice_id":first["practice_id"],
                "practice_number":first["practice_number"],
                "animal_name":first["animal_name"],
                "reason":"same_phase_and_amount_but_different_dates",
                "movement_ids":[row["id"] for row in members],
                "movement_dates":sorted(dates),
                "amount_cents":first["amount_cents"],
            })
    return duplicates,ambiguous


def _rows_after_duplicate_plan(
    rows: list[dict[str, Any]], duplicate_groups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    excluded={
        movement_id
        for group in duplicate_groups
        for movement_id in group["duplicate_ids"]
    }
    return [row for row in rows if row["id"] not in excluded]


def _practice_category(row: dict[str, Any]) -> str:
    if (row["request_origin"] or "")=="Collaboratore" or row["practice_collaborator_id"]:
        return "Collaboratori"
    return "D" if _cents(row["total_text"])>0 else "W"


def _amount_plan(
    rows: list[dict[str, Any]], ambiguous_practice_ids: set[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_practice: dict[int,list[dict[str,Any]]]=defaultdict(list)
    for row in rows:
        by_practice[int(row["practice_id"])].append(row)
    corrections=[]
    ambiguous=[]
    for practice_id,members in by_practice.items():
        if practice_id in ambiguous_practice_ids:
            continue
        practice=members[0]
        status=practice["payment_status"] or "Da saldare"
        if status not in ("Acconto","Pagato"):
            continue
        deposits=[row for row in members if row["movement_type"]=="Acconto"]
        settlements=[
            row for row in members
            if row["movement_type"] in ("Saldo","Incasso completo")
        ]
        total=_cents(practice["total_text"]) or _cents(practice["total_service"])
        target_row=None
        expected=0
        phase=""
        if status=="Acconto":
            expected=(
                _cents(practice["deposit_final"])
                if _cents(practice["total_text"])>0
                else _cents(practice["deposit"])
            )
            if len(deposits)==1:
                target_row=deposits[0];phase="deposit"
            elif len(deposits)>1:
                ambiguous.append({
                    "practice_id":practice_id,
                    "practice_number":practice["practice_number"],
                    "animal_name":practice["animal_name"],
                    "reason":"multiple_distinct_deposits",
                    "movement_ids":[row["id"] for row in deposits],
                })
                continue
        else:
            deposit_total=sum(row["amount_cents"] for row in deposits)
            expected=max(0,total-deposit_total)
            if len(settlements)==1:
                target_row=settlements[0]
                phase="settlement" if deposits else "full_payment"
            elif len(settlements)>1:
                ambiguous.append({
                    "practice_id":practice_id,
                    "practice_number":practice["practice_number"],
                    "animal_name":practice["animal_name"],
                    "reason":"multiple_distinct_settlements",
                    "movement_ids":[row["id"] for row in settlements],
                })
                continue
        if target_row and target_row["amount_cents"]!=expected:
            corrections.append({
                "practice_id":practice_id,
                "practice_number":practice["practice_number"],
                "animal_name":practice["animal_name"],
                "economic_phase":phase,
                "movement_id":target_row["id"],
                "movement_type":target_row["movement_type"],
                "movement_date":target_row["movement_date"],
                "ledger_amount_cents":target_row["amount_cents"],
                "correct_amount_cents":expected,
                "difference_cents":expected-target_row["amount_cents"],
                "category":_practice_category(practice),
            })
    return corrections,ambiguous


def repair_duplicate_balance_movements(
    connection: sqlite3.Connection,
    *,
    apply: bool = False,
    repaired_at: str | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    """Plan or append technical reversals/replacements without mutating originals."""
    repaired_at=repaired_at or date.today().isoformat()
    rows=_active_economic_rows(connection)
    duplicate_groups,duplicate_ambiguous=_duplicate_plan(rows)
    simulated=_rows_after_duplicate_plan(rows,duplicate_groups)
    ambiguous_ids={int(item["practice_id"]) for item in duplicate_ambiguous}
    amount_corrections,amount_ambiguous=_amount_plan(simulated,ambiguous_ids)
    created_reversals=[]
    created_replacements=[]
    if apply:
        for group in duplicate_groups:
            for duplicate_id in group["duplicate_ids"]:
                key=(
                    f"duplicate-repair:v{PROCEDURE_VERSION}:{duplicate_id}:"
                    f"canonical:{group['canonical_id']}"
                )
                reversal=create_reversal(
                    connection,
                    original_movement_id=duplicate_id,
                    movement_date=group["movement_date"],
                    idempotency_key=key,
                    description=(
                        f"Bonifica duplicato tecnico; canonico "
                        f"#{group['canonical_id']}"
                    ),
                    source="duplicate_repair",
                    created_by=created_by,
                    metadata={
                        "reason":"duplicate_repair",
                        "duplicate_movement_id":duplicate_id,
                        "canonical_movement_id":group["canonical_id"],
                        "repair_date":repaired_at,
                        "procedure_version":PROCEDURE_VERSION,
                    },
                )
                created_reversals.append(reversal.id)
        # Re-read after duplicate reversals so amount reconciliation uses the net ledger.
        rows=_active_economic_rows(connection)
        amount_corrections,amount_ambiguous=_amount_plan(rows,set())
        for correction in amount_corrections:
            key=(
                f"amount-repair:v{PROCEDURE_VERSION}:{correction['practice_id']}:"
                f"{correction['movement_id']}:{correction['ledger_amount_cents']}:"
                f"{correction['correct_amount_cents']}:{correction['economic_phase']}"
            )
            replacement=correct_movement_amount(
                connection,
                original_movement_id=correction["movement_id"],
                new_amount_cents=correction["correct_amount_cents"],
                idempotency_key=key,
                category=correction["category"],
                created_by=created_by,
                reason="Bonifica storica importo pratica",
            )
            if replacement:
                created_replacements.append(replacement.id)
    return {
        "mode":"apply" if apply else "dry-run",
        "procedure_version":PROCEDURE_VERSION,
        "duplicate_groups":duplicate_groups,
        "ambiguous_groups":duplicate_ambiguous+amount_ambiguous,
        "amount_corrections":amount_corrections,
        "duplicate_count":sum(len(group["duplicate_ids"]) for group in duplicate_groups),
        "duplicate_reversal_total_cents":sum(
            group["correction_cents"] for group in duplicate_groups
        ),
        "amount_correction_total_cents":sum(
            row["difference_cents"] for row in amount_corrections
        ),
        "created_reversal_ids":created_reversals,
        "created_replacement_ids":created_replacements,
    }


def _main() -> int:
    parser=argparse.ArgumentParser(
        description="Dry-run/apply della bonifica ledger Bilanci."
    )
    parser.add_argument("--database",required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    path=Path(args.database)
    if not path.exists():
        parser.error(f"database non trovato: {path}")
    connection=sqlite3.connect(path)
    connection.row_factory=sqlite3.Row
    try:
        dry_run=repair_duplicate_balance_movements(connection,apply=False)
        print(json.dumps(dry_run,ensure_ascii=False,indent=2))
        if args.apply:
            ensure_balance_schema(connection)
            report=repair_duplicate_balance_movements(connection,apply=True)
            connection.commit()
            print(json.dumps(report,ensure_ascii=False,indent=2))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return 0


if __name__=="__main__":
    raise SystemExit(_main())
