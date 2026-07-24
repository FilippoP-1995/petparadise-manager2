from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Final


BALANCE_CATEGORIES: Final = ("W", "D", "Collaboratori")
LEDGER_SECTIONS: Final = ("Entrata", "Uscita")
ADJUSTMENT_TYPE: Final = "Rettifica"
REVERSAL_TYPE: Final = "Storno"
_RESERVED_MOVEMENT_TYPES: Final = frozenset((ADJUSTMENT_TYPE, REVERSAL_TYPE))


class BalanceError(ValueError):
    """Base exception for invalid balance operations."""


class InvalidMovementError(BalanceError):
    """Raised when a movement does not satisfy the ledger rules."""


class IdempotencyConflictError(BalanceError):
    """Raised when an idempotency key is reused for different data."""


class DuplicateMovementError(BalanceError):
    """Raised when a movement UUID is already present."""


class MovementNotFoundError(BalanceError):
    """Raised when an adjustment target does not exist."""


class MovementAlreadyReversedError(BalanceError):
    """Raised when a movement already has a reversal."""


@dataclass(frozen=True, slots=True)
class BalanceMovement:
    id: int
    movement_uuid: str
    practice_id: int | None
    practice_number_snapshot: str
    movement_date: str
    category: str
    ledger_section: str
    movement_type: str
    amount_cents: int
    payment_method: str
    description: str
    source: str
    related_movement_id: int | None
    idempotency_key: str
    collaborator_id: int | None
    created_by: int | None
    created_at: str


@dataclass(frozen=True, slots=True)
class BalanceFilters:
    date_from: str | None = None
    date_to: str | None = None
    category: str | None = None
    collaborator_id: int | None = None
    payment_method: str | None = None
    operator_id: int | None = None
    status: str | None = None
    search: str = ""


@dataclass(frozen=True, slots=True)
class OutstandingBalance:
    practice_id: int
    practice_number: str
    reference: str
    category: str
    payment_method: str
    practice_created_at: str
    species: str
    animal_name: str
    owner_name: str
    payment_status: str
    collaborator_name: str
    total_due_cents: int
    received_cents: int
    remaining_cents: int


@dataclass(frozen=True, slots=True)
class BalanceSection:
    key: str
    title: str
    rows: tuple[BalanceMovement | OutstandingBalance, ...]
    row_amounts_cents: tuple[int, ...]

    @property
    def total_cents(self) -> int:
        return sum(self.row_amounts_cents)


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    filters: BalanceFilters
    sections: dict[str, BalanceSection]


_MOVEMENT_COLUMNS: Final = (
    "id,movement_uuid,practice_id,practice_number_snapshot,movement_date,"
    "category,ledger_section,movement_type,amount_cents,payment_method,"
    "description,source,related_movement_id,idempotency_key,collaborator_id,"
    "created_by,created_at"
)


def ensure_balance_schema(connection: sqlite3.Connection) -> None:
    """Create the additive, append-only balance ledger schema."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS balance_movements (
          id INTEGER PRIMARY KEY,
          movement_uuid TEXT NOT NULL UNIQUE,
          practice_id INTEGER,
          practice_number_snapshot TEXT NOT NULL DEFAULT '',
          movement_date TEXT NOT NULL,
          category TEXT NOT NULL CHECK(category IN ('W','D','Collaboratori')),
          ledger_section TEXT NOT NULL CHECK(ledger_section IN ('Entrata','Uscita')),
          movement_type TEXT NOT NULL,
          amount_cents INTEGER NOT NULL CHECK(
            typeof(amount_cents)='integer' AND amount_cents<>0
          ),
          payment_method TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          related_movement_id INTEGER REFERENCES balance_movements(id) ON DELETE RESTRICT,
          idempotency_key TEXT NOT NULL UNIQUE,
          collaborator_id INTEGER,
          created_by INTEGER,
          created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_balance_movements_date
          ON balance_movements(movement_date, id);
        CREATE INDEX IF NOT EXISTS idx_balance_movements_category
          ON balance_movements(category, movement_date);
        CREATE INDEX IF NOT EXISTS idx_balance_movements_type
          ON balance_movements(movement_type, movement_date);
        CREATE INDEX IF NOT EXISTS idx_balance_movements_practice
          ON balance_movements(practice_id, movement_date);
        CREATE INDEX IF NOT EXISTS idx_balance_movements_related
          ON balance_movements(related_movement_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_balance_movements_one_reversal
          ON balance_movements(related_movement_id)
          WHERE movement_type='Storno';

        CREATE TRIGGER IF NOT EXISTS balance_movements_no_update
        BEFORE UPDATE ON balance_movements
        BEGIN
          SELECT RAISE(ABORT, 'balance_movements is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS balance_movements_no_delete
        BEFORE DELETE ON balance_movements
        BEGIN
          SELECT RAISE(ABORT, 'balance_movements is append-only');
        END;
        """
    )
    columns={
        (row["name"] if isinstance(row,sqlite3.Row) else row[1])
        for row in connection.execute("PRAGMA table_info(balance_movements)")
    }
    if "collaborator_id" not in columns:
        connection.execute(
            "ALTER TABLE balance_movements ADD COLUMN collaborator_id INTEGER"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_balance_movements_collaborator "
        "ON balance_movements(collaborator_id,movement_date)"
    )


def _clean_required(value: object, field: str, max_length: int = 200) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidMovementError(f"{field} is required")
    if len(text) > max_length:
        raise InvalidMovementError(f"{field} is too long")
    return text


def _clean_optional(value: object, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_length:
        raise InvalidMovementError("text value is too long")
    return text


def _normalize_date(value: str | date) -> str:
    if isinstance(value, datetime):
        raise InvalidMovementError("movement_date must be a date, not a datetime")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise InvalidMovementError("movement_date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise InvalidMovementError("movement_date must use YYYY-MM-DD")
    return text


def _normalize_optional_id(value: int | None, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidMovementError(f"{field} must be a positive integer")
    return value


def _normalize_uuid(value: str | None) -> str:
    if value is None:
        return str(uuid.uuid4())
    text = _clean_required(value, "movement_uuid", 36)
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        raise InvalidMovementError("movement_uuid is not valid") from exc
    if str(parsed) != text.lower():
        raise InvalidMovementError("movement_uuid must use canonical UUID format")
    return str(parsed)


def _validate_amount(amount_cents: int) -> int:
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise InvalidMovementError("amount_cents must be an integer")
    if amount_cents == 0:
        raise InvalidMovementError("amount_cents cannot be zero")
    return amount_cents


def euros_to_cents(value: object) -> int:
    """Convert a normalized euro amount to exact integer cents."""
    text = str(value or "").strip().replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise InvalidMovementError("euro amount is not valid") from exc
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise InvalidMovementError("euro amount must have at most two decimals")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise InvalidMovementError("euro amount cannot be represented in cents")
    return int(cents)


def classify_category(*, has_total_d: bool, is_collaborator: bool) -> str:
    """Apply the accounting precedence Collaboratori > D > W."""
    if not isinstance(has_total_d, bool) or not isinstance(is_collaborator, bool):
        raise InvalidMovementError("classification flags must be boolean")
    if is_collaborator:
        return "Collaboratori"
    return "D" if has_total_d else "W"


def normalize_filters(
    *,
    date_from: str | date | None = None,
    date_to: str | date | None = None,
    category: str | None = None,
    collaborator_id: int | None = None,
    payment_method: str | None = None,
    operator_id: int | None = None,
    status: str | None = None,
    search: str = "",
) -> BalanceFilters:
    normalized_from = _normalize_date(date_from) if date_from else None
    normalized_to = _normalize_date(date_to) if date_to else None
    if normalized_from and normalized_to and normalized_from > normalized_to:
        raise InvalidMovementError("date_from cannot be after date_to")
    normalized_category = str(category or "").strip() or None
    if normalized_category and normalized_category not in BALANCE_CATEGORIES:
        raise InvalidMovementError("category must be W, D or Collaboratori")
    normalized_collaborator = _normalize_optional_id(
        collaborator_id, "collaborator_id"
    )
    normalized_method = _clean_optional(payment_method, 80) or None
    normalized_operator = _normalize_optional_id(operator_id, "operator_id")
    normalized_status = _clean_optional(status, 80) or None
    if normalized_status not in (
        None,"Acconto","Pagato","Saldo","Da saldare",
        "Entrata manuale","Uscita manuale","Rettifica","Storno",
    ):
        raise InvalidMovementError("status is not valid")
    normalized_search = _clean_optional(search, 200)
    return BalanceFilters(
        date_from=normalized_from,
        date_to=normalized_to,
        category=normalized_category,
        collaborator_id=normalized_collaborator,
        payment_method=normalized_method,
        operator_id=normalized_operator,
        status=normalized_status,
        search=normalized_search,
    )


def _row_to_movement(row: sqlite3.Row | tuple | None) -> BalanceMovement | None:
    if row is None:
        return None
    return BalanceMovement(*tuple(row))


def _find_by_idempotency(
    connection: sqlite3.Connection, idempotency_key: str
) -> BalanceMovement | None:
    row = connection.execute(
        f"SELECT {_MOVEMENT_COLUMNS} FROM balance_movements WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    return _row_to_movement(row)


def _find_by_uuid(
    connection: sqlite3.Connection, movement_uuid: str
) -> BalanceMovement | None:
    row = connection.execute(
        f"SELECT {_MOVEMENT_COLUMNS} FROM balance_movements WHERE movement_uuid=?",
        (movement_uuid,),
    ).fetchone()
    return _row_to_movement(row)


def _find_by_id(
    connection: sqlite3.Connection, movement_id: int
) -> BalanceMovement | None:
    row = connection.execute(
        f"SELECT {_MOVEMENT_COLUMNS} FROM balance_movements WHERE id=?",
        (movement_id,),
    ).fetchone()
    return _row_to_movement(row)


def _same_payload(existing: BalanceMovement, payload: dict[str, object]) -> bool:
    fields = (
        "practice_id",
        "practice_number_snapshot",
        "movement_date",
        "category",
        "ledger_section",
        "movement_type",
        "amount_cents",
        "payment_method",
        "description",
        "source",
        "related_movement_id",
        "collaborator_id",
        "created_by",
    )
    return all(getattr(existing, field) == payload[field] for field in fields)


def _create_movement(
    connection: sqlite3.Connection,
    *,
    amount_cents: int,
    movement_date: str | date,
    category: str,
    ledger_section: str,
    movement_type: str,
    idempotency_key: str,
    movement_uuid: str | None = None,
    practice_id: int | None = None,
    practice_number_snapshot: str = "",
    payment_method: str = "",
    description: str = "",
    source: str = "",
    related_movement_id: int | None = None,
    collaborator_id: int | None = None,
    created_by: int | None = None,
    allow_reserved_type: bool = False,
) -> BalanceMovement:
    amount = _validate_amount(amount_cents)
    if amount < 0 and not allow_reserved_type:
        raise InvalidMovementError(
            "negative amounts are reserved for adjustments and reversals"
        )
    normalized_date = _normalize_date(movement_date)
    normalized_category = _clean_required(category, "category", 30)
    if normalized_category not in BALANCE_CATEGORIES:
        raise InvalidMovementError("category must be W, D or Collaboratori")
    normalized_section = _clean_required(ledger_section, "ledger_section", 20)
    if normalized_section not in LEDGER_SECTIONS:
        raise InvalidMovementError("ledger_section must be Entrata or Uscita")
    normalized_type = _clean_required(movement_type, "movement_type", 80)
    if normalized_type in _RESERVED_MOVEMENT_TYPES and not allow_reserved_type:
        raise InvalidMovementError(
            "use create_adjustment or create_reversal for reserved movement types"
        )
    normalized_key = _clean_required(idempotency_key, "idempotency_key", 200)
    normalized_practice_id = _normalize_optional_id(practice_id, "practice_id")
    normalized_related_id = _normalize_optional_id(
        related_movement_id, "related_movement_id"
    )
    normalized_created_by = _normalize_optional_id(created_by, "created_by")
    normalized_collaborator_id = _normalize_optional_id(
        collaborator_id, "collaborator_id"
    )
    normalized_uuid = _normalize_uuid(movement_uuid)
    payload: dict[str, object] = {
        "practice_id": normalized_practice_id,
        "practice_number_snapshot": _clean_optional(
            practice_number_snapshot, 100
        ),
        "movement_date": normalized_date,
        "category": normalized_category,
        "ledger_section": normalized_section,
        "movement_type": normalized_type,
        "amount_cents": amount,
        "payment_method": _clean_optional(payment_method, 80),
        "description": _clean_optional(description, 2000),
        "source": _clean_optional(source, 100),
        "related_movement_id": normalized_related_id,
        "collaborator_id": normalized_collaborator_id,
        "created_by": normalized_created_by,
    }
    existing = _find_by_idempotency(connection, normalized_key)
    if existing:
        if _same_payload(existing, payload):
            return existing
        raise IdempotencyConflictError(
            "idempotency_key is already associated with different movement data"
        )
    if movement_uuid is not None and _find_by_uuid(connection, normalized_uuid):
        raise DuplicateMovementError("movement_uuid already exists")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        cursor = connection.execute(
            """
            INSERT INTO balance_movements(
              movement_uuid,practice_id,practice_number_snapshot,movement_date,
              category,ledger_section,movement_type,amount_cents,payment_method,
              description,source,related_movement_id,idempotency_key,
              collaborator_id,created_by,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                normalized_uuid,
                payload["practice_id"],
                payload["practice_number_snapshot"],
                payload["movement_date"],
                payload["category"],
                payload["ledger_section"],
                payload["movement_type"],
                payload["amount_cents"],
                payload["payment_method"],
                payload["description"],
                payload["source"],
                payload["related_movement_id"],
                normalized_key,
                payload["collaborator_id"],
                payload["created_by"],
                created_at,
            ),
        )
    except sqlite3.IntegrityError as exc:
        concurrent = _find_by_idempotency(connection, normalized_key)
        if concurrent:
            if _same_payload(concurrent, payload):
                return concurrent
            raise IdempotencyConflictError(
                "idempotency_key is already associated with different movement data"
            ) from exc
        if _find_by_uuid(connection, normalized_uuid):
            raise DuplicateMovementError("movement_uuid already exists") from exc
        raise InvalidMovementError(f"movement violates ledger constraints: {exc}") from exc
    created = _find_by_id(connection, cursor.lastrowid)
    if created is None:
        raise RuntimeError("created movement could not be read")
    return created


def create_movement(
    connection: sqlite3.Connection,
    *,
    amount_cents: int,
    movement_date: str | date,
    category: str,
    ledger_section: str,
    movement_type: str,
    idempotency_key: str,
    movement_uuid: str | None = None,
    practice_id: int | None = None,
    practice_number_snapshot: str = "",
    payment_method: str = "",
    description: str = "",
    source: str = "",
    collaborator_id: int | None = None,
    created_by: int | None = None,
) -> BalanceMovement:
    """Append one movement, returning the existing row for an identical retry."""
    return _create_movement(
        connection,
        amount_cents=amount_cents,
        movement_date=movement_date,
        category=category,
        ledger_section=ledger_section,
        movement_type=movement_type,
        idempotency_key=idempotency_key,
        movement_uuid=movement_uuid,
        practice_id=practice_id,
        practice_number_snapshot=practice_number_snapshot,
        payment_method=payment_method,
        description=description,
        source=source,
        collaborator_id=collaborator_id,
        created_by=created_by,
    )


def create_adjustment(
    connection: sqlite3.Connection,
    *,
    original_movement_id: int,
    amount_cents: int,
    movement_date: str | date,
    idempotency_key: str,
    description: str = "",
    source: str = "adjustment",
    created_by: int | None = None,
) -> BalanceMovement:
    """Append a signed adjustment without changing the original movement."""
    normalized_original_id = _normalize_optional_id(
        original_movement_id, "original_movement_id"
    )
    original = _find_by_id(connection, normalized_original_id)
    if original is None:
        raise MovementNotFoundError("original movement does not exist")
    if original.movement_type == REVERSAL_TYPE:
        raise InvalidMovementError("a reversal cannot be adjusted")
    return _create_movement(
        connection,
        amount_cents=amount_cents,
        movement_date=movement_date,
        category=original.category,
        ledger_section=original.ledger_section,
        movement_type=ADJUSTMENT_TYPE,
        idempotency_key=idempotency_key,
        practice_id=original.practice_id,
        practice_number_snapshot=original.practice_number_snapshot,
        payment_method=original.payment_method,
        description=description,
        source=source,
            related_movement_id=original.id,
            collaborator_id=original.collaborator_id,
            created_by=created_by,
        allow_reserved_type=True,
    )


def create_reversal(
    connection: sqlite3.Connection,
    *,
    original_movement_id: int,
    movement_date: str | date,
    idempotency_key: str,
    description: str = "",
    source: str = "reversal",
    created_by: int | None = None,
) -> BalanceMovement:
    """Append the exact opposite of a movement; only one reversal is allowed."""
    normalized_original_id = _normalize_optional_id(
        original_movement_id, "original_movement_id"
    )
    original = _find_by_id(connection, normalized_original_id)
    if original is None:
        raise MovementNotFoundError("original movement does not exist")
    if original.movement_type == REVERSAL_TYPE:
        raise InvalidMovementError("a reversal cannot be reversed")
    normalized_key = _clean_required(idempotency_key, "idempotency_key", 200)
    retry = _find_by_idempotency(connection, normalized_key)
    if retry is not None:
        return _create_movement(
            connection,
            amount_cents=-original.amount_cents,
            movement_date=movement_date,
            category=original.category,
            ledger_section=original.ledger_section,
            movement_type=REVERSAL_TYPE,
            idempotency_key=normalized_key,
            practice_id=original.practice_id,
            practice_number_snapshot=original.practice_number_snapshot,
            payment_method=original.payment_method,
            description=description,
            source=source,
        related_movement_id=original.id,
        collaborator_id=original.collaborator_id,
        created_by=created_by,
            allow_reserved_type=True,
        )
    already_reversed = connection.execute(
        """
        SELECT id FROM balance_movements
        WHERE related_movement_id=? AND movement_type=?
        LIMIT 1
        """,
        (original.id, REVERSAL_TYPE),
    ).fetchone()
    if already_reversed:
        raise MovementAlreadyReversedError("movement already has a reversal")
    try:
        return _create_movement(
            connection,
            amount_cents=-original.amount_cents,
            movement_date=movement_date,
            category=original.category,
            ledger_section=original.ledger_section,
            movement_type=REVERSAL_TYPE,
            idempotency_key=normalized_key,
            practice_id=original.practice_id,
            practice_number_snapshot=original.practice_number_snapshot,
            payment_method=original.payment_method,
            description=description,
            source=source,
            related_movement_id=original.id,
            collaborator_id=original.collaborator_id,
            created_by=created_by,
            allow_reserved_type=True,
        )
    except InvalidMovementError as exc:
        if connection.execute(
            """
            SELECT 1 FROM balance_movements
            WHERE related_movement_id=? AND movement_type=?
            """,
            (original.id, REVERSAL_TYPE),
        ).fetchone():
            raise MovementAlreadyReversedError(
                "movement already has a reversal"
            ) from exc
        raise


def correct_movement_date(
    connection: sqlite3.Connection,
    *,
    practice_id: int,
    movement_type: str,
    movement_date: str | date,
    idempotency_key: str,
    created_by: int | None = None,
) -> BalanceMovement | None:
    """Move one economic event without mutating the append-only ledger.

    The original is reversed on its original economic date and an equivalent
    replacement is appended on the corrected date. Query functions collapse
    this technical pair, so the user sees one effective economic row.
    """
    normalized_practice_id=_normalize_optional_id(practice_id,"practice_id")
    normalized_type=_clean_required(movement_type,"movement_type",80)
    normalized_date=_normalize_date(movement_date)
    original_row=connection.execute(
        f"""
        SELECT {_MOVEMENT_COLUMNS}
        FROM balance_movements b
        WHERE b.practice_id=? AND b.movement_type=?
          AND b.ledger_section='Entrata'
          AND NOT EXISTS(
            SELECT 1 FROM balance_movements reversal
            WHERE reversal.related_movement_id=b.id
              AND reversal.movement_type=?
          )
        ORDER BY b.id DESC LIMIT 1
        """,
        (normalized_practice_id,normalized_type,REVERSAL_TYPE),
    ).fetchone()
    original=_row_to_movement(original_row)
    if original is None:
        return None
    if original.movement_date==normalized_date:
        return original
    base=_clean_required(idempotency_key,"idempotency_key",170)
    create_reversal(
        connection,
        original_movement_id=original.id,
        movement_date=original.movement_date,
        idempotency_key=f"{base}:reverse",
        description=f"Correzione data da {original.movement_date} a {normalized_date}",
        source="payment_date_correction",
        created_by=created_by,
    )
    return create_movement(
        connection,
        amount_cents=original.amount_cents,
        movement_date=normalized_date,
        category=original.category,
        ledger_section=original.ledger_section,
        movement_type=original.movement_type,
        idempotency_key=f"{base}:replacement",
        practice_id=original.practice_id,
        practice_number_snapshot=original.practice_number_snapshot,
        payment_method=original.payment_method,
        description=original.description,
        source="payment_date_correction",
        collaborator_id=original.collaborator_id,
        created_by=created_by,
    )


def get_movements(
    connection: sqlite3.Connection,
    *,
    filters: BalanceFilters | None = None,
    date_from: str | date | None = None,
    date_to: str | date | None = None,
    category: str | None = None,
    collaborator_id: int | None = None,
    payment_method: str | None = None,
    operator_id: int | None = None,
    status: str | None = None,
    search: str = "",
) -> list[BalanceMovement]:
    """Return immutable ledger rows matching the supplied inclusive filters."""
    selected = filters or normalize_filters(
        date_from=date_from,
        date_to=date_to,
        category=category,
        collaborator_id=collaborator_id,
        payment_method=payment_method,
        operator_id=operator_id,
        status=status,
        search=search,
    )
    has_legacy=(
        _balance_table_exists(connection,"practices")
        and _balance_table_exists(connection,"payment_movements")
    )
    clauses: list[str] = []
    arguments: list[object] = []
    if selected.date_from:
        clauses.append("m.movement_date>=?")
        arguments.append(selected.date_from)
    if selected.date_to:
        clauses.append("m.movement_date<=?")
        arguments.append(selected.date_to)
    if selected.category:
        clauses.append("m.category=?")
        arguments.append(selected.category)
    if selected.collaborator_id:
        clauses.append("m._collaborator_id=?")
        arguments.append(selected.collaborator_id)
    if selected.payment_method:
        clauses.append("m.payment_method=?")
        arguments.append(selected.payment_method)
    if selected.operator_id:
        clauses.append("m.created_by=?")
        arguments.append(selected.operator_id)
    if selected.status:
        if selected.status=="Pagato":
            clauses.append("m.movement_type='Incasso completo'")
        elif selected.status=="Da saldare":
            clauses.append("1=0")
        else:
            clauses.append("m.movement_type=?")
            arguments.append(selected.status)
    if selected.search:
        pattern=f"%{selected.search.lower()}%"
        clauses.append(
            """(
              lower(COALESCE(m.practice_number_snapshot,'')) LIKE ?
              OR lower(COALESCE(m.description,'')) LIKE ?
              OR lower(COALESCE(m.movement_type,'')) LIKE ?
              OR lower(COALESCE(m.payment_method,'')) LIKE ?
              OR lower(COALESCE(m._practice_search,'')) LIKE ?
              OR lower(COALESCE(m._operator_name,'')) LIKE ?
            )"""
        )
        arguments.extend((pattern,pattern,pattern,pattern,pattern,pattern))
    where="WHERE "+" AND ".join(clauses) if clauses else ""
    qualified_columns=",".join(f"m.{name}" for name in _MOVEMENT_COLUMNS.split(","))
    if has_legacy:
        category_sql=(
            "CASE WHEN p.request_origin='Collaboratore' OR p.collaborator_id IS NOT NULL "
            "THEN 'Collaboratori' "
            "WHEN CAST(REPLACE(COALESCE(NULLIF(p.total_text,''),'0'),',','.') AS REAL)>0 "
            "THEN 'D' ELSE 'W' END"
        )
        kind_sql=(
            "CASE WHEN lower(pm.payment_type) LIKE 'acconto%' THEN 'Acconto' "
            "WHEN lower(pm.payment_type) LIKE 'saldo%' AND EXISTS("
            "SELECT 1 FROM payment_movements pa "
            "WHERE pa.practice_id=pm.practice_id AND pa.amount>0 "
            "AND date(pa.paid_at) IS NOT NULL "
            "AND lower(pa.payment_type) LIKE 'acconto%') THEN 'Saldo' "
            "WHEN lower(pm.payment_type) LIKE 'saldo%' THEN 'Incasso completo' END"
        )
        total_cents_sql=(
            "CASE WHEN CAST(REPLACE(COALESCE(NULLIF(p.total_text,''),'0'),',','.') AS REAL)>0 "
            "THEN CAST(ROUND(CAST(REPLACE(p.total_text,',','.') AS REAL)*100) AS INTEGER) "
            "ELSE CAST(ROUND(CAST(REPLACE(COALESCE(NULLIF(p.total_service,''),'0'),',','.') AS REAL)*100) AS INTEGER) END"
        )
        deposit_cents_sql=(
            "CASE WHEN CAST(REPLACE(COALESCE(NULLIF(p.total_text,''),'0'),',','.') AS REAL)>0 "
            "THEN CASE WHEN CAST(REPLACE(COALESCE(NULLIF(p.deposit_final,''),'0'),',','.') AS REAL)>0 "
            "THEN CAST(ROUND(CAST(REPLACE(p.deposit_final,',','.') AS REAL)*100) AS INTEGER) "
            "ELSE CAST(ROUND(CAST(REPLACE(COALESCE(NULLIF(p.deposit,''),'0'),',','.') AS REAL)*100) AS INTEGER) END "
            "ELSE CAST(ROUND(CAST(REPLACE(COALESCE(NULLIF(p.deposit,''),'0'),',','.') AS REAL)*100) AS INTEGER) END"
        )
        old_acconto_cents_sql=(
            "(SELECT COALESCE(SUM(CAST(ROUND(pa.amount*100) AS INTEGER)),0) "
            "FROM payment_movements pa WHERE pa.practice_id=p.id "
            "AND pa.amount>0 AND date(pa.paid_at) IS NOT NULL "
            "AND lower(pa.payment_type) LIKE 'acconto%')"
        )
        known_deposit_sql=(
            f"CASE WHEN {old_acconto_cents_sql}>0 THEN {old_acconto_cents_sql} "
            f"WHEN date(p.deposit_paid_at) IS NOT NULL THEN {deposit_cents_sql} "
            "ELSE 0 END"
        )
        paid_amount_sql=f"({total_cents_sql}-{known_deposit_sql})"
        paid_kind_sql=(
            f"CASE WHEN {known_deposit_sql}>0 THEN 'Saldo' "
            "ELSE 'Incasso completo' END"
        )
        users_available=_balance_table_exists(connection,"users")
        new_operator=(
            "COALESCE((SELECT display_name FROM users WHERE id=b.created_by),'')"
            if users_available else "''"
        )
        old_operator=(
            "COALESCE((SELECT display_name FROM users WHERE id=pm.user_id),'')"
            if users_available else "''"
        )
        practice_operator=(
            "COALESCE((SELECT display_name FROM users WHERE id=p.created_by),'')"
            if users_available else "''"
        )
        rows=connection.execute(
            f"""
            WITH unified AS (
              SELECT
                b.id,b.movement_uuid,b.practice_id,b.practice_number_snapshot,
                b.movement_date,b.category,b.ledger_section,b.movement_type,
                b.amount_cents,b.payment_method,b.description,b.source,
                b.related_movement_id,b.idempotency_key,b.collaborator_id,
                b.created_by,b.created_at,
                COALESCE(b.collaborator_id,p.collaborator_id) AS _collaborator_id,
                lower(
                  COALESCE(p.owner_first_name,'')||' '||
                  COALESCE(p.owner_last_name,'')||' '||
                  COALESCE(p.owner_company,'')||' '||
                  COALESCE(p.animal_name,'')||' '||
                  COALESCE(p.clinic_name,'')||' '||
                  COALESCE(p.veterinarian_name,'')||' '||
                  COALESCE(p.collaborator_name,'')
                ) AS _practice_search,
                lower({new_operator}) AS _operator_name
              FROM balance_movements b
              LEFT JOIN practices p ON p.id=b.practice_id
              WHERE NOT (
                b.movement_type='Storno'
                AND b.source='payment_date_correction'
              )
              AND NOT EXISTS(
                SELECT 1 FROM balance_movements correction
                WHERE correction.related_movement_id=b.id
                  AND correction.movement_type='Storno'
                  AND correction.source='payment_date_correction'
              )

              UNION ALL

              SELECT
                -pm.id,
                'legacy-payment-'||pm.id,
                pm.practice_id,
                COALESCE(p.practice_number,''),
                date(pm.paid_at),
                {category_sql},
                'Entrata',
                {kind_sql},
                CAST(ROUND(pm.amount*100) AS INTEGER),
                COALESCE(NULLIF(pm.payment_method,''),p.payment_method,''),
                COALESCE(NULLIF(pm.notes,''),{kind_sql}||' storico'),
                'legacy_payment_movements',
                NULL,
                'legacy-payment-movement:'||pm.id,
                p.collaborator_id,
                pm.user_id,
                COALESCE(NULLIF(pm.created_at,''),pm.paid_at),
                p.collaborator_id,
                lower(
                  COALESCE(p.owner_first_name,'')||' '||
                  COALESCE(p.owner_last_name,'')||' '||
                  COALESCE(p.owner_company,'')||' '||
                  COALESCE(p.animal_name,'')||' '||
                  COALESCE(p.clinic_name,'')||' '||
                  COALESCE(p.veterinarian_name,'')||' '||
                  COALESCE(p.collaborator_name,'')
                ),
                lower({old_operator})
              FROM payment_movements pm
              JOIN practices p ON p.id=pm.practice_id
              WHERE pm.amount>0
                AND date(pm.paid_at) IS NOT NULL
                AND (
                  lower(pm.payment_type) LIKE 'acconto%'
                  OR lower(pm.payment_type) LIKE 'saldo%'
                )
                AND NOT EXISTS(
                  SELECT 1 FROM balance_movements existing
                  WHERE existing.practice_id=pm.practice_id
                    AND existing.ledger_section='Entrata'
                    AND (
                      existing.movement_type={kind_sql}
                      OR (
                        {kind_sql}='Incasso completo'
                        AND existing.movement_type='Saldo'
                      )
                    )
                )

              UNION ALL

              SELECT
                -1000000000-(p.id*2),
                'legacy-practice-deposit-'||p.id,
                p.id,
                COALESCE(p.practice_number,''),
                date(p.deposit_paid_at),
                {category_sql},
                'Entrata',
                'Acconto',
                {deposit_cents_sql},
                COALESCE(p.payment_method,''),
                'Acconto storico dalla pratica',
                'legacy_practice_fields',
                NULL,
                'historical-practice:'||p.id||':deposit',
                p.collaborator_id,
                p.created_by,
                COALESCE(NULLIF(p.deposit_paid_at,''),p.created_at),
                p.collaborator_id,
                lower(
                  COALESCE(p.owner_first_name,'')||' '||
                  COALESCE(p.owner_last_name,'')||' '||
                  COALESCE(p.owner_company,'')||' '||
                  COALESCE(p.animal_name,'')||' '||
                  COALESCE(p.clinic_name,'')||' '||
                  COALESCE(p.veterinarian_name,'')||' '||
                  COALESCE(p.collaborator_name,'')
                ),
                lower({practice_operator})
              FROM practices p
              WHERE p.payment_status IN ('Acconto','Pagato')
                AND date(p.deposit_paid_at) IS NOT NULL
                AND {deposit_cents_sql}>0
                AND NOT EXISTS(
                  SELECT 1 FROM payment_movements pm
                  WHERE pm.practice_id=p.id AND pm.amount>0
                    AND date(pm.paid_at) IS NOT NULL
                    AND lower(pm.payment_type) LIKE 'acconto%'
                )
                AND NOT EXISTS(
                  SELECT 1 FROM balance_movements existing
                  WHERE existing.practice_id=p.id
                    AND existing.ledger_section='Entrata'
                    AND existing.movement_type='Acconto'
                )

              UNION ALL

              SELECT
                -1000000001-(p.id*2),
                'legacy-practice-paid-'||p.id,
                p.id,
                COALESCE(p.practice_number,''),
                date(p.paid_at),
                {category_sql},
                'Entrata',
                {paid_kind_sql},
                {paid_amount_sql},
                COALESCE(p.payment_method,''),
                {paid_kind_sql}||' storico dalla pratica',
                'legacy_practice_fields',
                NULL,
                'historical-practice:'||p.id||':balance',
                p.collaborator_id,
                p.created_by,
                COALESCE(NULLIF(p.paid_at,''),p.created_at),
                p.collaborator_id,
                lower(
                  COALESCE(p.owner_first_name,'')||' '||
                  COALESCE(p.owner_last_name,'')||' '||
                  COALESCE(p.owner_company,'')||' '||
                  COALESCE(p.animal_name,'')||' '||
                  COALESCE(p.clinic_name,'')||' '||
                  COALESCE(p.veterinarian_name,'')||' '||
                  COALESCE(p.collaborator_name,'')
                ),
                lower({practice_operator})
              FROM practices p
              WHERE p.payment_status='Pagato'
                AND date(p.paid_at) IS NOT NULL
                AND {paid_amount_sql}>0
                AND NOT EXISTS(
                  SELECT 1 FROM payment_movements pm
                  WHERE pm.practice_id=p.id AND pm.amount>0
                    AND date(pm.paid_at) IS NOT NULL
                    AND lower(pm.payment_type) LIKE 'saldo%'
                )
                AND NOT EXISTS(
                  SELECT 1 FROM balance_movements existing
                  WHERE existing.practice_id=p.id
                    AND existing.ledger_section='Entrata'
                    AND existing.movement_type IN ('Saldo','Incasso completo')
                )
            )
            SELECT {qualified_columns}
            FROM unified m
            {where}
            ORDER BY m.movement_date DESC,m.id DESC
            """,
            arguments,
        ).fetchall()
    else:
        if selected.collaborator_id:
            return []
        rows = connection.execute(
            f"""
            SELECT {qualified_columns}
            FROM (
              SELECT b.*,b.collaborator_id AS _collaborator_id,
                     '' AS _practice_search,'' AS _operator_name
              FROM balance_movements b
              WHERE NOT (
                b.movement_type='Storno'
                AND b.source='payment_date_correction'
              )
              AND NOT EXISTS(
                SELECT 1 FROM balance_movements correction
                WHERE correction.related_movement_id=b.id
                  AND correction.movement_type='Storno'
                  AND correction.source='payment_date_correction'
              )
            ) m
            {where}
            ORDER BY m.movement_date DESC, m.id DESC
            """,
            arguments,
        ).fetchall()
    movements = [_row_to_movement(row) for row in rows]
    return [movement for movement in movements if movement is not None]


def create_manual_expense(
    connection: sqlite3.Connection,
    *,
    amount_cents: int,
    movement_date: str | date,
    category: str,
    description: str,
    idempotency_key: str,
    created_by: int | None = None,
) -> BalanceMovement:
    """Append an immutable manual W/D expense through the common ledger writer."""
    normalized_category=_clean_required(category,"category",30)
    if normalized_category not in ("W","D"):
        raise InvalidMovementError("manual expense category must be W or D")
    normalized_description=_clean_required(description,"description",2000)
    amount=_validate_amount(amount_cents)
    if amount<0:
        raise InvalidMovementError("manual expense amount must be positive")
    return create_movement(
        connection,
        amount_cents=amount,
        movement_date=movement_date,
        category=normalized_category,
        ledger_section="Uscita",
        movement_type="Uscita manuale",
        idempotency_key=idempotency_key,
        description=normalized_description,
        source="manual_expense",
        created_by=created_by,
    )


def create_manual_income(
    connection: sqlite3.Connection,
    *,
    amount_cents: int,
    movement_date: str | date,
    category: str,
    payment_method: str,
    description: str,
    idempotency_key: str,
    collaborator_id: int | None = None,
    created_by: int | None = None,
) -> BalanceMovement:
    """Append a manual receipt without requiring a linked practice."""
    normalized_category=_clean_required(category,"category",30)
    if normalized_category not in BALANCE_CATEGORIES:
        raise InvalidMovementError(
            "manual income category must be W, D or Collaboratori"
        )
    normalized_collaborator=_normalize_optional_id(
        collaborator_id,"collaborator_id"
    )
    if normalized_category=="Collaboratori" and normalized_collaborator is None:
        raise InvalidMovementError(
            "collaborator_id is required for Collaboratori income"
        )
    normalized_description=_clean_required(description,"description",2000)
    normalized_method=_clean_required(payment_method,"payment_method",80)
    amount=_validate_amount(amount_cents)
    if amount<0:
        raise InvalidMovementError("manual income amount must be positive")
    return create_movement(
        connection,
        amount_cents=amount,
        movement_date=movement_date,
        category=normalized_category,
        ledger_section="Entrata",
        movement_type="Entrata manuale",
        idempotency_key=idempotency_key,
        payment_method=normalized_method,
        description=normalized_description,
        source="manual_income",
        collaborator_id=normalized_collaborator,
        created_by=created_by,
    )


def _money_field_to_cents(value: object) -> int:
    text=str(value or "").strip().replace(",",".")
    if not text:
        return 0
    try:
        amount=Decimal(text)
    except InvalidOperation:
        return 0
    if not amount.is_finite():
        return 0
    cents=(amount*100).quantize(Decimal("1"))
    return max(0,int(cents))


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _outstanding_category(row: sqlite3.Row) -> str:
    return classify_category(
        has_total_d=_money_field_to_cents(row["total_text"])>0,
        is_collaborator=(row["request_origin"] or "")=="Collaboratore"
        or bool(row["collaborator_id"]),
    )


def _balance_table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    )


def get_outstanding_balances(
    connection: sqlite3.Connection,
    *,
    filters: BalanceFilters,
) -> list[OutstandingBalance]:
    """Return open practice balances at date_to; date_from is intentionally ignored."""
    if not filters.date_to:
        raise InvalidMovementError("date_to is required for outstanding balances")
    if filters.status and filters.status!="Da saldare":
        return []
    clauses=[
        "date(p.created_at)<=date(?)",
        "(p.deleted_at IS NULL OR p.deleted_at='' OR date(p.deleted_at)>date(?))",
    ]
    arguments: list[object]=[filters.date_to,filters.date_to]
    if filters.payment_method:
        clauses.append("COALESCE(p.payment_method,'')=?")
        arguments.append(filters.payment_method)
    if filters.collaborator_id:
        clauses.append("p.collaborator_id=?")
        arguments.append(filters.collaborator_id)
    if filters.operator_id:
        clauses.append("p.created_by=?")
        arguments.append(filters.operator_id)
    if filters.search:
        pattern=f"%{filters.search.lower()}%"
        clauses.append(
            """(
              lower(COALESCE(p.practice_number,'')) LIKE ?
              OR lower(COALESCE(p.animal_name,'')) LIKE ?
              OR lower(COALESCE(p.owner_first_name,'')||' '||COALESCE(p.owner_last_name,'')) LIKE ?
              OR lower(COALESCE(p.owner_company,'')) LIKE ?
              OR lower(COALESCE(p.clinic_name,'')) LIKE ?
              OR lower(COALESCE(p.veterinarian_name,'')) LIKE ?
              OR lower(COALESCE(p.collaborator_name,'')) LIKE ?
            )"""
        )
        arguments.extend((pattern,)*7)
    rows=connection.execute(
        f"""
        SELECT
          p.id,p.practice_number,p.owner_first_name,p.owner_last_name,
          p.owner_company,p.animal_name,p.clinic_name,p.veterinarian_name,
          p.collaborator_name,p.request_origin,p.collaborator_id,p.total_text,
          p.total_service,p.payment_method,p.payment_status,p.deposit,
          p.deposit_final,p.deposit_paid_at,p.paid_at,p.created_at,p.species,
          COUNT(m.id) AS balance_movement_count,
          COALESCE(SUM(CASE
            WHEN m.ledger_section='Entrata' AND m.movement_date<=?
            THEN m.amount_cents ELSE 0 END),0) AS received_to_date_cents,
          COALESCE(SUM(CASE
            WHEN m.ledger_section='Entrata'
            THEN m.amount_cents ELSE 0 END),0) AS received_all_cents
        FROM practices p
        LEFT JOIN balance_movements m ON m.practice_id=p.id
        WHERE {" AND ".join(clauses)}
        GROUP BY p.id
        ORDER BY p.practice_number,p.id
        """,
        [filters.date_to,*arguments],
    ).fetchall()
    legacy_by_practice: dict[int, tuple[int, int]] = {}
    if _balance_table_exists(connection, "payment_movements"):
        legacy_rows=connection.execute(
            f"""
            SELECT
              practice_id,
              COALESCE(SUM(CASE
                WHEN pm.amount>0 AND date(pm.paid_at) IS NOT NULL
                  AND date(pm.paid_at)<=date(?)
                THEN CAST(ROUND(pm.amount*100) AS INTEGER) ELSE 0 END),0)
                AS received_to_date_cents,
              COALESCE(SUM(CASE
                WHEN pm.amount>0 AND date(pm.paid_at) IS NOT NULL
                THEN CAST(ROUND(pm.amount*100) AS INTEGER) ELSE 0 END),0)
                AS received_all_cents
            FROM payment_movements pm
            JOIN practices p ON p.id=pm.practice_id
            WHERE {" AND ".join(clauses)}
            GROUP BY practice_id
            """,
            (filters.date_to,*arguments),
        ).fetchall()
        legacy_by_practice={
            int(row["practice_id"]):(
                int(row["received_to_date_cents"] or 0),
                int(row["received_all_cents"] or 0),
            )
            for row in legacy_rows
        }
    result=[]
    for row in rows:
        row_category=_outstanding_category(row)
        if filters.category and row_category!=filters.category:
            continue
        total_d=_money_field_to_cents(row["total_text"])
        total_due=total_d if total_d>0 else _money_field_to_cents(row["total_service"])
        if total_due<=0:
            continue
        payment_status=(row["payment_status"] or "Da saldare").strip()
        balance_count=int(row["balance_movement_count"] or 0)
        if balance_count:
            received_to=int(row["received_to_date_cents"] or 0)
            received_all=int(row["received_all_cents"] or 0)
        elif payment_status=="Da saldare":
            # A legacy practice still marked as due has no recognized receipt.
            received_to=received_all=0
        else:
            received_to,received_all=legacy_by_practice.get(int(row["id"]),(0,0))
            if received_all==0:
                total_d=_money_field_to_cents(row["total_text"])
                deposit=(
                    _money_field_to_cents(row["deposit_final"])
                    if total_d>0 else _money_field_to_cents(row["deposit"])
                )
                deposit_date=str(row["deposit_paid_at"] or "")[:10]
                if deposit>0 and _valid_iso_date(deposit_date):
                    received_all=deposit
                    if deposit_date<=filters.date_to:
                        received_to=deposit
                paid_date=str(row["paid_at"] or "")[:10]
                if payment_status=="Pagato":
                    if not _valid_iso_date(paid_date):
                        # The migration report flags this anomaly. Without a real
                        # receipt date it cannot be assigned to a historical snapshot.
                        continue
                    received_all=total_due
                    if paid_date<=filters.date_to:
                        received_to=total_due
        remaining=max(0,total_due-received_to)
        if remaining<=0:
            continue
        owner=" ".join(
            value for value in (row["owner_first_name"],row["owner_last_name"]) if value
        ).strip()
        reference=(
            owner or row["owner_company"] or row["animal_name"] or
            row["collaborator_name"] or row["clinic_name"] or
            row["veterinarian_name"] or ""
        )
        result.append(
            OutstandingBalance(
                practice_id=row["id"],
                practice_number=row["practice_number"] or "",
                reference=reference,
                category=row_category,
                payment_method=row["payment_method"] or "",
                practice_created_at=row["created_at"] or "",
                species=row["species"] or "",
                animal_name=row["animal_name"] or "",
                owner_name=owner or row["owner_company"] or "",
                payment_status=payment_status,
                collaborator_name=row["collaborator_name"] or "",
                total_due_cents=total_due,
                received_cents=received_to,
                remaining_cents=remaining,
            )
        )
    return result


def get_balance_snapshot(
    connection: sqlite3.Connection,
    *,
    filters: BalanceFilters,
) -> BalanceSnapshot:
    """Build every card and its exact detail rows from one shared filtered dataset."""
    movements=get_movements(connection,filters=filters)
    outstanding=get_outstanding_balances(connection,filters=filters)

    def movement_section(
        key: str,
        title: str,
        predicate,
        *,
        signed=False,
    ) -> BalanceSection:
        rows=tuple(movement for movement in movements if predicate(movement))
        amounts=tuple(
            (-movement.amount_cents if signed and movement.ledger_section=="Uscita"
             else movement.amount_cents)
            for movement in rows
        )
        return BalanceSection(key,title,rows,amounts)

    def outstanding_section(key: str,title: str,category: str) -> BalanceSection:
        rows=tuple(row for row in outstanding if row.category==category)
        return BalanceSection(
            key,title,rows,tuple(row.remaining_cents for row in rows)
        )

    sections={}
    sections["entrate-w"]=movement_section(
        "entrate-w","Entrate W",
        lambda row: row.ledger_section=="Entrata" and row.category=="W",
    )
    sections["entrate-d"]=movement_section(
        "entrate-d","Entrate D",
        lambda row: row.ledger_section=="Entrata" and row.category=="D",
    )
    sections["collaboratori-incassato"]=movement_section(
        "collaboratori-incassato","Collaboratori Incassato",
        lambda row: row.ledger_section=="Entrata" and row.category=="Collaboratori",
    )
    sections["da-riscuotere-w"]=outstanding_section(
        "da-riscuotere-w","Da riscuotere W","W"
    )
    sections["da-riscuotere-d"]=outstanding_section(
        "da-riscuotere-d","Da riscuotere D","D"
    )
    sections["collaboratori-da-riscuotere"]=outstanding_section(
        "collaboratori-da-riscuotere","Collaboratori Da riscuotere","Collaboratori"
    )
    sections["uscite-w"]=movement_section(
        "uscite-w","Uscite W",
        lambda row: row.ledger_section=="Uscita" and row.category=="W",
    )
    sections["uscite-d"]=movement_section(
        "uscite-d","Uscite D",
        lambda row: row.ledger_section=="Uscita" and row.category=="D",
    )
    sections["totale-w-attuale"]=movement_section(
        "totale-w-attuale","Totale W attuale",
        lambda row: row.category=="W",
        signed=True,
    )
    sections["totale-d-attuale"]=movement_section(
        "totale-d-attuale","Totale D attuale",
        lambda row: row.category=="D",
        signed=True,
    )
    sections["saldo-netto"]=movement_section(
        "saldo-netto","Saldo Netto",
        lambda row: (
            row.ledger_section=="Entrata"
            or (row.ledger_section=="Uscita" and row.category in ("W","D"))
        ),
        signed=True,
    )
    return BalanceSnapshot(filters=filters,sections=sections)


def get_balance_operators(
    connection: sqlite3.Connection,
) -> list[tuple[int,str]]:
    rows=connection.execute(
        "SELECT id,display_name FROM users WHERE active=1 ORDER BY display_name,id"
    ).fetchall()
    return [(int(row["id"]),str(row["display_name"])) for row in rows]


def get_balance_collaborators(
    connection: sqlite3.Connection,
) -> list[tuple[int,str]]:
    """Return collaborators referenced by at least one practice."""
    if not (
        _balance_table_exists(connection,"collaborators")
        and _balance_table_exists(connection,"practices")
    ):
        return []
    rows=connection.execute(
        """
        SELECT c.id,c.name
        FROM collaborators c
        WHERE EXISTS(SELECT 1 FROM practices p WHERE p.collaborator_id=c.id)
           OR EXISTS(
             SELECT 1 FROM balance_movements b WHERE b.collaborator_id=c.id
           )
        ORDER BY c.name,c.id
        """
    ).fetchall()
    return [(int(row["id"]),str(row["name"])) for row in rows]
