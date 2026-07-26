from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import app
from balance_service import (
    TECHNICAL_REVERSAL_SOURCES,
    ensure_balance_schema,
    get_movements,
    get_outstanding_balances,
    normalize_filters,
)

PROCEDURE_VERSION = "1"

# Only ever produced by app.py's balance_legacy_movement_delete for a practice
# old enough to predate payment_movements entirely (its acconto/saldo lived
# only as plain columns on practices). Anything else is not this script's
# concern (real balance_movements rows and payment_movements-backed legacy
# rows are deleted for real, with no columns left dangling behind).
LEGACY_KEY_PATTERN = re.compile(r"^historical-practice:(\d+):(deposit|balance)$")

_ONE_TIME_APP_HANDLER = object.__new__(app.App)


def _dict_rows(connection: sqlite3.Connection, sql: str, params=()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def _valid_iso_date(connection: sqlite3.Connection, value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    row = connection.execute("SELECT date(?) AS d", (text,)).fetchone()
    return bool(row["d"])


def _cents(value: object) -> int:
    return round(app.money_value(value) * 100)


def _has_modern_rows(connection: sqlite3.Connection, practice_id: int, kind: str) -> bool:
    """True if the practice already has a *real* (non-technical, non-reversed)
    movement for the same macroarea, whether in the old payment_movements
    table or the current balance_movements ledger. If so, whatever the
    practices columns show today may already be governed by that modern
    record (apply_payment_macroarea overwrites deposit/deposit_final/paid_at
    on every registration) rather than by the deleted historical one — never
    touch the columns in that situation, no matter what the numbers look
    like, since clearing them could stomp on genuinely current data."""
    prefix = "acconto" if kind == "deposit" else "saldo"
    if connection.execute(
        "SELECT 1 FROM payment_movements WHERE practice_id=? AND payment_type LIKE ? LIMIT 1",
        (practice_id, f"{prefix}%"),
    ).fetchone():
        return True
    movement_types = ("Acconto",) if kind == "deposit" else ("Saldo", "Incasso completo")
    placeholders = ",".join("?" for _ in movement_types)
    technical = ",".join("?" for _ in TECHNICAL_REVERSAL_SOURCES)
    row = connection.execute(
        f"""
        SELECT 1 FROM balance_movements b
        WHERE b.practice_id=? AND b.ledger_section='Entrata' AND b.amount_cents>0
          AND b.movement_type IN ({placeholders})
          AND NOT EXISTS(
            SELECT 1 FROM balance_movements r
            WHERE r.related_movement_id=b.id AND r.movement_type='Storno'
          )
          AND NOT (b.source IN ({technical}) AND b.related_movement_id IS NOT NULL)
        LIMIT 1
        """,
        (practice_id, *movement_types, *TECHNICAL_REVERSAL_SOURCES),
    ).fetchone()
    return bool(row)


def _classify_one(connection: sqlite3.Connection, deletion_row: dict[str, Any]) -> dict[str, Any]:
    """Pure analysis for a single balance_movement_deletions row: never
    writes anything. Returns a dict with at least a "status" key — one of
    gia_corretta / gia_coerente / riparabile / ambigua / incoerente /
    non_trovata — plus enough detail to explain and (for riparabile) later
    apply the fix."""
    base = {
        "deletion_id": deletion_row["id"],
        "deleted_at": deletion_row["deleted_at"],
        "deleted_by": deletion_row["deleted_by"],
        "practice_number_snapshot": deletion_row["practice_number_snapshot"],
    }
    try:
        snapshot = json.loads(deletion_row["snapshot_json"] or "{}")
    except (TypeError, ValueError):
        return {**base, "status": "incoerente", "reason": "snapshot_json non è JSON valido"}
    legacy_key = snapshot.get("legacy_key")
    if not legacy_key:
        return {**base, "status": "incoerente", "reason": "manca legacy_key nello storico eliminazione"}
    base["legacy_key"] = legacy_key
    if "practice_before" in snapshot:
        return {
            **base, "status": "gia_corretta",
            "reason": "lo storico eliminazione contiene già practice_before (creato dalla PR #82): nulla da fare",
        }
    match = LEGACY_KEY_PATTERN.fullmatch(legacy_key)
    if not match:
        return {**base, "status": "incoerente", "reason": f"formato legacy_key non riconosciuto: {legacy_key!r}"}
    parsed_pid, kind = int(match.group(1)), match.group(2)
    base["practice_id"] = parsed_pid
    base["kind"] = kind
    if deletion_row["practice_id"] and int(deletion_row["practice_id"]) != parsed_pid:
        return {
            **base, "status": "incoerente",
            "reason": f"practice_id della riga di log ({deletion_row['practice_id']}) non combacia con la chiave ({parsed_pid})",
        }
    practice = connection.execute("SELECT * FROM practices WHERE id=?", (parsed_pid,)).fetchone()
    if practice is None:
        return {**base, "status": "non_trovata", "reason": "la pratica non esiste più nel database"}
    practice = dict(practice)
    base["practice_number"] = practice.get("practice_number", "")
    reversal_key = f"legacy-void:v1:{legacy_key}"
    reversal = connection.execute(
        "SELECT * FROM balance_movements WHERE idempotency_key=? AND movement_type='Storno'",
        (reversal_key,),
    ).fetchone()
    if reversal is None:
        return {**base, "status": "incoerente", "reason": "nessuno storno tecnico trovato per questa chiave nonostante il log di eliminazione"}
    if reversal["source"] not in TECHNICAL_REVERSAL_SOURCES:
        return {
            **base, "status": "incoerente",
            "reason": f"lo storno esiste ma con source non tecnica ({reversal['source']!r})",
        }
    if _has_modern_rows(connection, parsed_pid, kind):
        return {
            **base, "status": "ambigua",
            "reason": "la pratica ha anche un movimento moderno (payment_movements o balance_movements reale) per la stessa macroarea: lo stato attuale potrebbe già dipendere da quello",
        }
    expected_amount_cents = int(deletion_row["amount_cents"])
    expected_date = str(deletion_row["movement_date"] or "").strip()
    if not expected_date or not _valid_iso_date(connection, expected_date):
        return {**base, "status": "ambigua", "reason": "la data del movimento eliminato nello storico è mancante o non valida: impossibile verificare con certezza"}
    if kind == "deposit":
        current_date = str(practice.get("deposit_paid_at") or "").strip()
        if not current_date or not _valid_iso_date(connection, current_date):
            return {**base, "status": "gia_coerente", "reason": "deposit_paid_at è già vuoto: la pratica sembra già pulita"}
        if current_date != expected_date:
            return {
                **base, "status": "ambigua",
                "reason": f"deposit_paid_at attuale ({current_date}) non combacia con la data storica ({expected_date}): probabile modifica successiva",
            }
        deposit_cents = _cents(practice.get("deposit"))
        deposit_final_cents = _cents(practice.get("deposit_final"))
        matches = [
            name for name, cents in (("deposit", deposit_cents), ("deposit_final", deposit_final_cents))
            if cents > 0 and cents == expected_amount_cents
        ]
        if len(matches) == 0:
            return {
                **base, "status": "ambigua",
                "reason": f"nessuna colonna (deposit={deposit_cents}, deposit_final={deposit_final_cents}) combacia con l'importo storico ({expected_amount_cents} centesimi)",
            }
        if len(matches) > 1:
            return {
                **base, "status": "ambigua",
                "reason": "sia deposit che deposit_final combaciano con lo stesso importo storico: impossibile determinare con certezza quale sia quello eliminato",
            }
        matched_column = matches[0]
        expected_column = "deposit_final" if app.uses_total_d(practice) else "deposit"
        if matched_column != expected_column:
            return {
                **base, "status": "ambigua",
                "reason": (
                    f"la colonna che combacia con lo storico ({matched_column}) non è quella indicata dal "
                    f"circuito attuale della pratica ({expected_column}): probabile cambio di circuito dopo l'eliminazione"
                ),
            }
        return {
            **base, "status": "riparabile",
            "matched_column": matched_column,
            "current_values": {"deposit": practice.get("deposit"), "deposit_final": practice.get("deposit_final"), "deposit_paid_at": current_date, "payment_status": practice.get("payment_status")},
            "historical_values": {"amount_cents": expected_amount_cents, "movement_date": expected_date},
            "reason": f"{matched_column} e deposit_paid_at combaciano esattamente con quanto registrato nello storico eliminazione, nessun movimento moderno presente",
        }
    # kind == "balance"
    if (practice.get("payment_status") or "") != "Pagato":
        return {**base, "status": "gia_coerente", "reason": "payment_status non è (più) Pagato: il saldo storico non risulta più attivo"}
    current_date = str(practice.get("paid_at") or "").strip()
    if not current_date or not _valid_iso_date(connection, current_date):
        return {**base, "status": "gia_coerente", "reason": "paid_at è già vuoto: la pratica sembra già pulita"}
    if current_date != expected_date:
        return {
            **base, "status": "ambigua",
            "reason": f"paid_at attuale ({current_date}) non combacia con la data storica ({expected_date}): probabile modifica successiva",
        }
    recomputed_cents = round((app.effective_total(practice) - app.channel_deposit(practice)) * 100)
    if recomputed_cents != expected_amount_cents:
        return {
            **base, "status": "ambigua",
            "reason": (
                f"l'importo saldo ricalcolato ora ({recomputed_cents} centesimi) non combacia più con quanto "
                f"registrato nello storico ({expected_amount_cents} centesimi): la pratica sembra modificata dopo l'eliminazione"
            ),
        }
    return {
        **base, "status": "riparabile",
        "matched_column": "paid_at",
        "current_values": {
            "paid_at": current_date, "payment_status": practice.get("payment_status"),
            "remaining_balance": practice.get("remaining_balance"), "remaining_final": practice.get("remaining_final"),
        },
        "historical_values": {"amount_cents": expected_amount_cents, "movement_date": expected_date},
        "reason": "paid_at e l'importo saldo ricalcolato combaciano esattamente con quanto registrato nello storico eliminazione, nessun movimento moderno presente",
    }


def plan_legacy_repairs(
    connection: sqlite3.Connection, *, practice_id: int | None = None
) -> dict[str, Any]:
    """Read-only analysis. Never writes to the database."""
    where = "deletion_kind='legacy_void' AND (restored_at IS NULL OR restored_at='')"
    params: list[Any] = []
    if practice_id is not None:
        where += " AND practice_id=?"
        params.append(practice_id)
    rows = _dict_rows(
        connection,
        f"SELECT * FROM balance_movement_deletions WHERE {where} ORDER BY id",
        params,
    )
    classified = [_classify_one(connection, row) for row in rows]
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in classified:
        if item.get("legacy_key"):
            by_key[item["legacy_key"]].append(item)
    duplicate_groups = []
    demoted_ids: set[int] = set()
    for legacy_key, members in by_key.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda item: item["deletion_id"])
        keep, rest = members_sorted[0], members_sorted[1:]
        duplicate_groups.append({
            "legacy_key": legacy_key,
            "kept_deletion_id": keep["deletion_id"],
            "duplicate_deletion_ids": [item["deletion_id"] for item in rest],
        })
        demoted_ids.update(item["deletion_id"] for item in rest)
    final_items = []
    for item in classified:
        if item["deletion_id"] in demoted_ids:
            final_items.append({**item, "status": "duplicata", "reason": f"eliminazione duplicata dello stesso legacy_key (già gestita da deletion #{next(g['kept_deletion_id'] for g in duplicate_groups if item['deletion_id'] in g['duplicate_deletion_ids'])})"})
        else:
            final_items.append(item)
    by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in final_items:
        by_status[item["status"]].append(item)
    practices_involved = {item["practice_id"] for item in final_items if item.get("practice_id") is not None}
    return {
        "analyzed": len(rows),
        "practices_involved": len(practices_involved),
        "duplicate_groups": duplicate_groups,
        "items": final_items,
        "by_status": {status: items for status, items in by_status.items()},
        "counts": {status: len(items) for status, items in by_status.items()},
    }


def _verify_repair(
    connection: sqlite3.Connection, *, practice_id: int, legacy_key: str,
) -> list[str]:
    """Post-write checks. Returns a list of problems (empty = all good)."""
    problems = []
    movements = get_movements(
        connection, filters=normalize_filters(include_technical=True),
        restrict_practice_id=practice_id,
    )
    if any(m.idempotency_key == legacy_key for m in movements):
        problems.append("il movimento risulta ancora presente nella generazione Bilanci (anche nella vista tecnica)")
    default_movements = get_movements(
        connection, filters=normalize_filters(), restrict_practice_id=practice_id,
    )
    if any(m.practice_id == practice_id and m.idempotency_key == legacy_key for m in default_movements):
        problems.append("il movimento risulta ancora presente nella vista di default di Bilanci")
    outstanding = get_outstanding_balances(
        connection, filters=normalize_filters(date_to=date.today().isoformat()),
    )
    match = next((row for row in outstanding if row.practice_id == practice_id), None)
    if match is not None and match.remaining_cents < 0:
        problems.append(f"get_outstanding_balances restituisce un residuo negativo ({match.remaining_cents} centesimi)")
    return problems


def apply_legacy_repairs(
    connection: sqlite3.Connection, plan: dict[str, Any], *, default_created_by: int | None = None,
) -> dict[str, Any]:
    """Repairs every item plan marked "riparabile", each in its own SAVEPOINT
    (committed independently on success), verifying afterwards and rolling
    back+recording an error for that single practice if anything looks
    wrong — a bad row never blocks the rest of the run."""
    repaired = []
    errors = []
    for item in plan["items"]:
        if item["status"] != "riparabile":
            continue
        deletion_id = item["deletion_id"]
        pid = item["practice_id"]
        legacy_key = item["legacy_key"]
        kind = item["kind"]
        savepoint = f"legacy_repair_{deletion_id}"
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            # Re-classify from scratch, inside the savepoint, right before
            # writing — never trust the plan handed in (it may be a moment
            # stale, or come from a caller that built it earlier): if
            # anything about this row no longer looks safely repairable
            # (concurrent edit, double run, already fixed meanwhile), bail
            # out for this practice only instead of acting on stale data.
            deletion_row = connection.execute(
                "SELECT * FROM balance_movement_deletions WHERE id=?", (deletion_id,)
            ).fetchone()
            if deletion_row is None:
                raise RuntimeError("la riga di log eliminazione è sparita tra l'analisi e la riparazione")
            fresh = _classify_one(connection, dict(deletion_row))
            if fresh["status"] != "riparabile":
                raise RuntimeError(f"non più riparabile al momento della scrittura (stato attuale: {fresh['status']}: {fresh.get('reason','')})")
            practice_before_row = connection.execute(
                "SELECT * FROM practices WHERE id=?", (pid,)
            ).fetchone()
            if practice_before_row is None:
                raise RuntimeError("la pratica è sparita tra l'analisi e la riparazione")
            other_columns_before = {
                key: practice_before_row[key] for key in (
                    "owner_first_name", "owner_last_name", "animal_name", "total_service", "total_text",
                )
            }
            snapshot = json.loads(deletion_row["snapshot_json"] or "{}")
            created_by = deletion_row["deleted_by"] if deletion_row["deleted_by"] is not None else default_created_by
            result = _ONE_TIME_APP_HANDLER.delete_legacy_practice_column_movement(
                connection, practice_before_row, kind, created_by,
            )
            new_snapshot = {**snapshot, **result}
            connection.execute(
                "UPDATE balance_movement_deletions SET snapshot_json=? WHERE id=?",
                (json.dumps(new_snapshot, ensure_ascii=False), deletion_id),
            )
            practice_after_row = connection.execute(
                "SELECT * FROM practices WHERE id=?", (pid,)
            ).fetchone()
            other_columns_after = {
                key: practice_after_row[key] for key in other_columns_before
            }
            if other_columns_before != other_columns_after:
                raise RuntimeError(f"colonne non correlate sono cambiate: {other_columns_before} -> {other_columns_after}")
            problems = _verify_repair(connection, practice_id=pid, legacy_key=legacy_key)
            if problems:
                raise RuntimeError("verifica post-riparazione fallita: " + "; ".join(problems))
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            connection.commit()
            repaired.append({
                "deletion_id": deletion_id,
                "practice_id": pid,
                "practice_number": item.get("practice_number", ""),
                "kind": kind,
                "matched_column": item.get("matched_column"),
                "before": result["practice_before"],
                "after": {key: practice_after_row[key] for key in result["practice_before"]},
            })
        except Exception as exc:  # noqa: broad on purpose — one bad practice must never abort the run
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            connection.commit()
            errors.append({
                "deletion_id": deletion_id, "practice_id": pid,
                "practice_number": item.get("practice_number", ""),
                "error": str(exc),
            })
    return {"repaired": repaired, "errors": errors}


def _summarize_for_print(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "analizzate": plan["analyzed"],
        "pratiche_coinvolte": plan["practices_involved"],
        "conteggi_per_stato": plan["counts"],
        "gruppi_duplicati": plan["duplicate_groups"],
        "dettaglio_riparabili": plan["by_status"].get("riparabile", []),
        "dettaglio_ambigue": plan["by_status"].get("ambigua", []),
        "dettaglio_incoerenti": plan["by_status"].get("incoerente", []),
        "dettaglio_non_trovate": plan["by_status"].get("non_trovata", []),
        "dettaglio_gia_corrette": [
            {"deletion_id": i["deletion_id"], "practice_id": i.get("practice_id")}
            for i in plan["by_status"].get("gia_corretta", [])
        ],
        "dettaglio_gia_coerenti": [
            {"deletion_id": i["deletion_id"], "practice_id": i.get("practice_id")}
            for i in plan["by_status"].get("gia_coerente", [])
        ],
    }


def _create_backup(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = db_path.with_name(f"{db_path.stem}.backup-{timestamp}{db_path.suffix}")
    suffix = 2
    while candidate.exists():
        candidate = db_path.with_name(f"{db_path.stem}.backup-{timestamp}-{suffix}{db_path.suffix}")
        suffix += 1
    shutil.copy2(db_path, candidate)
    if not candidate.exists() or candidate.stat().st_size == 0:
        raise RuntimeError(f"Backup non riuscito o vuoto: {candidate}")
    return candidate


def _main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run/apply della bonifica una tantum per le pratiche pre-payment_movements "
            "il cui movimento storico è stato eliminato da Bilanci prima della PR #82 "
            "(colonne pratica mai pulite dopo lo storno tecnico)."
        )
    )
    parser.add_argument("--database", required=True, help="percorso del file SQLite")
    parser.add_argument("--apply", action="store_true", help="applica le riparazioni (default: solo anteprima)")
    parser.add_argument("--practice-id", type=int, default=None, help="limita l'analisi/riparazione a una sola pratica")
    parser.add_argument("--user-id", type=int, default=None, help="utente da attribuire alle riparazioni prive di un deleted_by originale")
    parser.add_argument("--report", default=None, help="percorso del file di report JSON (default: accanto al database)")
    args = parser.parse_args()

    db_path = Path(args.database)
    if not db_path.exists():
        parser.error(f"database non trovato: {db_path}")

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        ensure_balance_schema(connection)
        plan = plan_legacy_repairs(connection, practice_id=args.practice_id)
    finally:
        connection.close()

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "dry-run",
        "database": str(db_path),
        "backup_path": None,
        "practice_id_filter": args.practice_id,
        **_summarize_for_print(plan),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    if args.apply:
        repairable = plan["by_status"].get("riparabile", [])
        if not repairable:
            print("\nNessuna pratica riparabile trovata: nessuna modifica da applicare.")
        else:
            backup_path = _create_backup(db_path)
            print(f"\nBackup creato: {backup_path}")
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                ensure_balance_schema(connection)
                connection.commit()
                # Re-plan against the live connection right before writing,
                # so a stale/adversarial gap between dry-run and apply can
                # never repair a practice that changed in the meantime.
                fresh_plan = plan_legacy_repairs(connection, practice_id=args.practice_id)
                result = apply_legacy_repairs(connection, fresh_plan, default_created_by=args.user_id)
            finally:
                connection.close()
            report["mode"] = "apply"
            report["backup_path"] = str(backup_path)
            report["riparate"] = result["repaired"]
            report["errori_applicazione"] = result["errors"]
            print(json.dumps({"riparate": result["repaired"], "errori": result["errors"]}, ensure_ascii=False, indent=2, default=str))

    report_path = Path(args.report) if args.report else db_path.with_name(
        f"balance_legacy_repair-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nReport scritto in: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
