from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from balance_service import classify_category, create_movement


@dataclass(frozen=True, slots=True)
class HistoricalMovementCandidate:
    practice_id: int
    practice_number: str
    movement_date: str
    category: str
    movement_type: str
    amount_cents: int
    payment_method: str
    description: str
    idempotency_key: str
    created_by: int | None


@dataclass(frozen=True, slots=True)
class HistoricalAnomaly:
    practice_id: int
    practice_number: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class HistoricalMigrationReport:
    historical_practices_found: int
    migratable_movements: int
    due_practices: int
    anomalies: tuple[HistoricalAnomaly, ...]
    duplicates_avoided: int
    candidates: tuple[HistoricalMovementCandidate, ...]
    created_movements: int = 0
    dry_run: bool = True

    def summary(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "historical_practices_found": self.historical_practices_found,
            "migratable_movements": self.migratable_movements,
            "due_practices": self.due_practices,
            "anomalies_found": len(self.anomalies),
            "duplicates_avoided": self.duplicates_avoided,
            "created_movements": self.created_movements,
            "anomalies": [asdict(item) for item in self.anomalies],
        }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection,table):
        return set()
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _cents(value: object) -> int | None:
    text=str(value or "").strip().replace(",",".")
    if not text:
        return None
    try:
        amount=Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount<=0:
        return None
    cents=amount*100
    if cents!=cents.to_integral_value():
        return None
    return int(cents)


def _date(value: object) -> str | None:
    text=str(value or "").strip()
    if not text:
        return None
    try:
        parsed=datetime.fromisoformat(text.replace("Z","+00:00")).date()
    except ValueError:
        try:
            parsed=date.fromisoformat(text)
        except ValueError:
            return None
    return parsed.isoformat()


def _value(row: sqlite3.Row, key: str, default: object = "") -> object:
    return row[key] if key in row.keys() and row[key] is not None else default


def _category(practice: sqlite3.Row) -> str:
    return classify_category(
        has_total_d=(_cents(_value(practice,"total_text")) or 0)>0,
        is_collaborator=(
            str(_value(practice,"request_origin"))=="Collaboratore"
            or bool(_value(practice,"collaborator_id",None))
        ),
    )


def _total_cents(practice: sqlite3.Row) -> int | None:
    total_d=_cents(_value(practice,"total_text"))
    return total_d if total_d else _cents(_value(practice,"total_service"))


def _deposit_cents(practice: sqlite3.Row) -> int | None:
    if (_cents(_value(practice,"total_text")) or 0)>0:
        return _cents(_value(practice,"deposit_final")) or _cents(_value(practice,"deposit"))
    return _cents(_value(practice,"deposit"))


def _legacy_kind(payment_type: object) -> str | None:
    text=str(payment_type or "").strip().lower()
    if text.startswith("acconto"):
        return "Acconto"
    if text.startswith("saldo"):
        return "Saldo"
    return None


def _candidate_from_legacy(
    practice: sqlite3.Row,
    movement: sqlite3.Row,
    *,
    movement_type: str,
) -> HistoricalMovementCandidate | None:
    amount=_cents(_value(movement,"amount"))
    movement_date=_date(_value(movement,"paid_at"))
    if amount is None or movement_date is None:
        return None
    movement_id=int(movement["id"])
    return HistoricalMovementCandidate(
        practice_id=int(practice["id"]),
        practice_number=str(_value(practice,"practice_number")),
        movement_date=movement_date,
        category=_category(practice),
        movement_type=movement_type,
        amount_cents=amount,
        payment_method=str(
            _value(movement,"payment_method")
            or _value(practice,"payment_method")
        ),
        description=str(_value(movement,"notes") or f"{movement_type} storico"),
        idempotency_key=f"historical-payment-movement:{movement_id}",
        created_by=(
            int(_value(movement,"user_id"))
            if str(_value(movement,"user_id")).isdigit()
            and int(_value(movement,"user_id"))>0
            else (
                int(_value(practice,"created_by"))
                if str(_value(practice,"created_by")).isdigit()
                and int(_value(practice,"created_by"))>0 else None
            )
        ),
    )


def _candidate_from_practice(
    practice: sqlite3.Row,
    *,
    movement_type: str,
    amount_cents: int,
    movement_date: str,
    suffix: str,
) -> HistoricalMovementCandidate:
    return HistoricalMovementCandidate(
        practice_id=int(practice["id"]),
        practice_number=str(_value(practice,"practice_number")),
        movement_date=movement_date,
        category=_category(practice),
        movement_type=movement_type,
        amount_cents=amount_cents,
        payment_method=str(_value(practice,"payment_method")),
        description=f"{movement_type} storico dalla pratica",
        idempotency_key=f"historical-practice:{practice['id']}:{suffix}",
        created_by=(
            int(_value(practice,"created_by"))
            if str(_value(practice,"created_by")).isdigit()
            and int(_value(practice,"created_by"))>0 else None
        ),
    )


def _semantic_duplicate(
    candidate: HistoricalMovementCandidate,
    existing: list[sqlite3.Row],
    consumed_ids: set[int],
) -> bool:
    for row in existing:
        row_id=int(row["id"])
        if row_id in consumed_ids:
            continue
        if (
            int(_value(row,"practice_id",0) or 0)==candidate.practice_id
            and str(_value(row,"movement_date"))==candidate.movement_date
            and str(_value(row,"category"))==candidate.category
            and int(_value(row,"amount_cents",0) or 0)==candidate.amount_cents
            and str(_value(row,"ledger_section"))=="Entrata"
            and str(_value(row,"movement_type"))==candidate.movement_type
        ):
            consumed_ids.add(row_id)
            return True
    return False


def plan_historical_migration(
    connection: sqlite3.Connection,
) -> HistoricalMigrationReport:
    """Analyze legacy payment evidence without changing any row."""
    connection.row_factory=sqlite3.Row
    if not _table_exists(connection,"practices"):
        return HistoricalMigrationReport(0,0,0,(),0,(),dry_run=True)
    practice_columns=_columns(connection,"practices")
    deleted_clause=(
        "WHERE deleted_at IS NULL OR deleted_at=''"
        if "deleted_at" in practice_columns else ""
    )
    practices=connection.execute(
        f"SELECT * FROM practices {deleted_clause} ORDER BY id"
    ).fetchall()
    old_by_practice: dict[int,list[sqlite3.Row]]={}
    if _table_exists(connection,"payment_movements"):
        for row in connection.execute(
            "SELECT * FROM payment_movements ORDER BY practice_id,paid_at,id"
        ):
            old_by_practice.setdefault(int(row["practice_id"]),[]).append(row)
    existing=(
        connection.execute("SELECT * FROM balance_movements ORDER BY id").fetchall()
        if _table_exists(connection,"balance_movements") else []
    )
    existing_keys={str(_value(row,"idempotency_key")) for row in existing}
    consumed_existing_ids:set[int]=set()
    candidates: list[HistoricalMovementCandidate]=[]
    anomalies: list[HistoricalAnomaly]=[]
    duplicates=0
    due_practices=0

    def anomaly(practice: sqlite3.Row,code: str,message: str) -> None:
        anomalies.append(
            HistoricalAnomaly(
                int(practice["id"]),str(_value(practice,"practice_number")),
                code,message,
            )
        )

    def add(candidate: HistoricalMovementCandidate) -> None:
        nonlocal duplicates
        if candidate.idempotency_key in existing_keys:
            duplicates+=1
            return
        if _semantic_duplicate(candidate,existing,consumed_existing_ids):
            duplicates+=1
            return
        candidates.append(candidate)

    for practice in practices:
        status=str(_value(practice,"payment_status") or "Da saldare")
        legacy=old_by_practice.get(int(practice["id"]),[])
        if status=="Da saldare":
            due_practices+=1
            if any((_cents(_value(row,"amount")) or 0)>0 for row in legacy):
                anomaly(
                    practice,"DUE_WITH_OLD_PAYMENTS",
                    "La pratica è Da saldare ma possiede vecchi movimenti; nessuna entrata è stata pianificata.",
                )
            continue
        total=_total_cents(practice)
        if total is None:
            anomaly(practice,"MISSING_TOTAL","Totale attendibile mancante.")
            continue
        valid_legacy=[]
        for row in legacy:
            kind=_legacy_kind(_value(row,"payment_type"))
            amount=_cents(_value(row,"amount"))
            paid_at=_date(_value(row,"paid_at"))
            if kind is None:
                anomaly(practice,"UNKNOWN_PAYMENT_TYPE",f"Movimento storico {row['id']} con tipo non riconosciuto.")
                continue
            if amount is None:
                anomaly(practice,"INVALID_PAYMENT_AMOUNT",f"Movimento storico {row['id']} senza importo valido.")
                continue
            if paid_at is None:
                anomaly(practice,"MISSING_PAYMENT_DATE",f"Movimento storico {row['id']} senza vera data di incasso.")
                continue
            valid_legacy.append((row,kind,amount,paid_at))

        if status=="Acconto":
            acconti=[item for item in valid_legacy if item[1]=="Acconto"]
            if acconti:
                for row,kind,amount,paid_at in acconti:
                    candidate=_candidate_from_legacy(practice,row,movement_type="Acconto")
                    if candidate:add(candidate)
            else:
                amount=_deposit_cents(practice)
                paid_at=_date(_value(practice,"deposit_paid_at"))
                if amount is None:
                    anomaly(practice,"MISSING_DEPOSIT_AMOUNT","Importo dell'acconto attendibile mancante.")
                if paid_at is None:
                    anomaly(practice,"MISSING_DEPOSIT_DATE","Data reale dell'acconto mancante.")
                if amount is not None and paid_at is not None:
                    add(_candidate_from_practice(
                        practice,movement_type="Acconto",amount_cents=amount,
                        movement_date=paid_at,suffix="deposit",
                    ))
            if any(kind!="Acconto" for _,kind,_,_ in valid_legacy):
                anomaly(practice,"UNEXPECTED_SALDO","Pratica Acconto con un vecchio movimento saldo.")
            continue

        if status!="Pagato":
            anomaly(practice,"UNKNOWN_PAYMENT_STATUS",f"Stato pagamento non riconosciuto: {status}.")
            continue

        acconti=[item for item in valid_legacy if item[1]=="Acconto"]
        saldi=[item for item in valid_legacy if item[1]=="Saldo"]
        if acconti and saldi:
            if sum(item[2] for item in valid_legacy)!=total:
                anomaly(practice,"PAID_TOTAL_MISMATCH","Acconto e saldo storici non coincidono con il totale dovuto.")
                continue
            for row,kind,amount,paid_at in acconti+saldi:
                candidate=_candidate_from_legacy(practice,row,movement_type=kind)
                if candidate:add(candidate)
            continue
        if len(valid_legacy)==1 and valid_legacy[0][2]==total:
            row,kind,amount,paid_at=valid_legacy[0]
            candidate=_candidate_from_legacy(
                practice,row,movement_type="Incasso completo"
            )
            if candidate:add(candidate)
            continue
        if acconti and not saldi:
            paid_at=_date(_value(practice,"paid_at"))
            deposited=sum(item[2] for item in acconti)
            remaining=total-deposited
            if remaining>0 and paid_at:
                for row,kind,amount,movement_date in acconti:
                    candidate=_candidate_from_legacy(practice,row,movement_type="Acconto")
                    if candidate:add(candidate)
                add(_candidate_from_practice(
                    practice,movement_type="Saldo",amount_cents=remaining,
                    movement_date=paid_at,suffix="balance",
                ))
                continue
        if valid_legacy:
            anomaly(
                practice,"PAID_MOVEMENTS_AMBIGUOUS",
                "I vecchi movimenti non permettono di ricostruire con certezza acconto e saldo.",
            )
            continue
        deposit=_deposit_cents(practice)
        deposit_date=_date(_value(practice,"deposit_paid_at"))
        paid_at=_date(_value(practice,"paid_at"))
        if deposit and deposit<total and deposit_date and paid_at:
            add(_candidate_from_practice(
                practice,movement_type="Acconto",amount_cents=deposit,
                movement_date=deposit_date,suffix="deposit",
            ))
            add(_candidate_from_practice(
                practice,movement_type="Saldo",amount_cents=total-deposit,
                movement_date=paid_at,suffix="balance",
            ))
        elif paid_at:
            add(_candidate_from_practice(
                practice,movement_type="Incasso completo",amount_cents=total,
                movement_date=paid_at,suffix="paid",
            ))
        else:
            anomaly(practice,"MISSING_PAID_DATE","Data reale del pagamento completo mancante.")

    return HistoricalMigrationReport(
        historical_practices_found=len(practices),
        migratable_movements=len(candidates),
        due_practices=due_practices,
        anomalies=tuple(anomalies),
        duplicates_avoided=duplicates,
        candidates=tuple(candidates),
        created_movements=0,
        dry_run=True,
    )


def migrate_historical_data(
    connection: sqlite3.Connection,
    *,
    dry_run: bool = True,
) -> HistoricalMigrationReport:
    """Plan by default; append candidates only when explicitly called with dry_run=False."""
    report=plan_historical_migration(connection)
    if dry_run:
        return report
    if not _table_exists(connection,"balance_movements"):
        raise RuntimeError("balance_movements does not exist; deploy the additive schema first")
    created=0
    for candidate in report.candidates:
        before=connection.total_changes
        create_movement(
            connection,
            amount_cents=candidate.amount_cents,
            movement_date=candidate.movement_date,
            category=candidate.category,
            ledger_section="Entrata",
            movement_type=candidate.movement_type,
            idempotency_key=candidate.idempotency_key,
            practice_id=candidate.practice_id,
            practice_number_snapshot=candidate.practice_number,
            payment_method=candidate.payment_method,
            description=candidate.description,
            source="historical_migration",
            created_by=candidate.created_by,
        )
        if connection.total_changes>before:
            created+=1
    return HistoricalMigrationReport(
        historical_practices_found=report.historical_practices_found,
        migratable_movements=report.migratable_movements,
        due_practices=report.due_practices,
        anomalies=report.anomalies,
        duplicates_avoided=report.duplicates_avoided,
        candidates=report.candidates,
        created_movements=created,
        dry_run=False,
    )


def dry_run_database(database_path: str | Path) -> HistoricalMigrationReport:
    path=Path(database_path).resolve()
    connection=sqlite3.connect(f"file:{path.as_posix()}?mode=ro",uri=True)
    connection.row_factory=sqlite3.Row
    try:
        return migrate_historical_data(connection,dry_run=True)
    finally:
        connection.close()


def main() -> int:
    parser=argparse.ArgumentParser(
        description="Analizza la migrazione storica Bilanci (dry-run predefinito)."
    )
    parser.add_argument("database",type=Path)
    parser.add_argument(
        "--apply",action="store_true",
        help="Esegue realmente la migrazione append-only (NON usare per il dry-run).",
    )
    args=parser.parse_args()
    if args.apply:
        connection=sqlite3.connect(args.database)
        connection.row_factory=sqlite3.Row
        try:
            report=migrate_historical_data(connection,dry_run=False)
            connection.commit()
        finally:
            connection.close()
    else:
        report=dry_run_database(args.database)
    print(json.dumps(report.summary(),ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
