from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from domain.delivery.rules import ensure_delivery_fields_consistent, preliminary_payment_diverges
from domain.errors import NotFoundError, ValidationDomainError
from domain.practice.rules import effective_total_cents
from models.calendar_event import CalendarEvent, CalendarEventType
from repositories.audit_repository import AuditRepository
from repositories.calendar_event_repository import CalendarEventRepository
from repositories.client_repository import ClientRepository
from repositories.practice_repository import PracticeRepository
from repositories.reference_repositories import CalendarZoneRepository, CompanyLocationRepository
from repositories.veterinarian_repository import VeterinarianRepository
from schemas.calendar_event import DeliveryCreate, DeliveryUpdate, LinkDeliveryToPracticeRequest

ENTITY_TYPE = "calendar_event"

_MUTABLE_FIELDS = (
    "start_at",
    "end_at",
    "client_id",
    "delivery_type",
    "delivery_veterinarian_id",
    "delivery_location_id",
    "delivery_zone_id",
    "delivery_address",
    "notes",
)


async def _resolve_references(db: AsyncSession, data) -> None:
    if data.client_id is not None and await ClientRepository(db).get_by_id(data.client_id) is None:
        raise NotFoundError(f"Cliente {data.client_id} non trovato")
    if data.delivery_veterinarian_id is not None and await VeterinarianRepository(db).get_by_id(data.delivery_veterinarian_id) is None:
        raise NotFoundError(f"Veterinario {data.delivery_veterinarian_id} non trovato")
    if data.delivery_location_id is not None and await CompanyLocationRepository(db).get_by_id(data.delivery_location_id) is None:
        raise NotFoundError(f"Sede {data.delivery_location_id} non trovata")
    if data.delivery_zone_id is not None and await CalendarZoneRepository(db).get_by_id(data.delivery_zone_id) is None:
        raise NotFoundError(f"Zona {data.delivery_zone_id} non trovata")


async def create_delivery(db: AsyncSession, data: DeliveryCreate, *, actor_user_id: int) -> CalendarEvent:
    ensure_delivery_fields_consistent(
        data.delivery_type,
        delivery_veterinarian_id=data.delivery_veterinarian_id,
        delivery_zone_id=data.delivery_zone_id,
        delivery_location_id=data.delivery_location_id,
        delivery_address=data.delivery_address,
    )
    await _resolve_references(db, data)

    linked_practice_id = None
    if data.linked_practice_id is not None:
        practice = await PracticeRepository(db).get_by_id(data.linked_practice_id)
        if practice is None:
            raise NotFoundError(f"Pratica {data.linked_practice_id} non trovata")
        linked_practice_id = practice.id

    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)

    delivery = CalendarEvent(
        event_type=CalendarEventType.riconsegna,
        linked_practice_id=linked_practice_id,
        # doc06 Addendum P: preliminari SOLO finche' non collegata - se la
        # riconsegna nasce gia' collegata (caso reale piu' comune, "fissa
        # riconsegna" da una pratica esistente), questi valori sono il primo
        # e unico valore mai scritto, non c'e' nulla da riconciliare contro.
        preliminary_payment_status=data.preliminary_payment_status,
        preliminary_payment_amount=data.preliminary_payment_amount,
        created_by=actor_user_id,
    )
    for field_name in _MUTABLE_FIELDS:
        setattr(delivery, field_name, getattr(data, field_name))

    repo.add(delivery)
    await db.flush()
    audit.record(entity_type=ENTITY_TYPE, entity_id=delivery.id, action="created", user_id=actor_user_id)

    await db.commit()
    return await repo.get_by_id(delivery.id)


async def update_delivery(db: AsyncSession, delivery_id: int, data: DeliveryUpdate, *, actor_user_id: int) -> CalendarEvent:
    ensure_delivery_fields_consistent(
        data.delivery_type,
        delivery_veterinarian_id=data.delivery_veterinarian_id,
        delivery_zone_id=data.delivery_zone_id,
        delivery_location_id=data.delivery_location_id,
        delivery_address=data.delivery_address,
    )
    await _resolve_references(db, data)

    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)

    delivery = await repo.get_by_id(delivery_id)
    if delivery is None or delivery.event_type != CalendarEventType.riconsegna:
        raise NotFoundError(f"Riconsegna {delivery_id} non trovata")

    is_linked = delivery.linked_practice_id is not None
    if is_linked and (
        data.preliminary_payment_status != delivery.preliminary_payment_status
        or data.preliminary_payment_amount != delivery.preliminary_payment_amount
    ):
        raise ValidationDomainError(
            "I dati di pagamento preliminare sono congelati: la riconsegna e' gia' collegata a una pratica "
            "(doc06 Addendum P) - mai piu' scritti dall'interfaccia."
        )

    for field_name in _MUTABLE_FIELDS:
        old_value = getattr(delivery, field_name)
        new_value = getattr(data, field_name)
        if old_value != new_value:
            audit.record(
                entity_type=ENTITY_TYPE,
                entity_id=delivery.id,
                action="field_changed",
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                user_id=actor_user_id,
            )
        setattr(delivery, field_name, new_value)

    if not is_linked:
        delivery.preliminary_payment_status = data.preliminary_payment_status
        delivery.preliminary_payment_amount = data.preliminary_payment_amount

    await db.commit()
    return await repo.get_by_id(delivery_id)


async def link_delivery_to_practice(
    db: AsyncSession, delivery_id: int, request: LinkDeliveryToPracticeRequest, *, actor_user_id: int
) -> CalendarEvent:
    """doc06 Addendum P: 'mai un collegamento silenzioso che fa sparire la
    discrepanza' - se preliminary_payment_amount diverge dal totale
    effettivo della pratica, il collegamento viene rifiutato (422) a meno
    che il chiamante non passi confirm_despite_mismatch=True (l'operatore
    ha visto l'avviso e confermato esplicitamente)."""
    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)

    delivery = await repo.get_by_id(delivery_id)
    if delivery is None or delivery.event_type != CalendarEventType.riconsegna:
        raise NotFoundError(f"Riconsegna {delivery_id} non trovata")
    if delivery.linked_practice_id is not None:
        raise ValidationDomainError("Questa riconsegna e' gia' collegata a una pratica.")

    practice = await PracticeRepository(db).get_by_id(request.practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {request.practice_id} non trovata")

    total = effective_total_cents(practice)
    diverges = preliminary_payment_diverges(delivery.preliminary_payment_amount, total)
    if diverges and not request.confirm_despite_mismatch:
        raise ValidationDomainError(
            f"L'importo preliminare stimato su questa riconsegna ({delivery.preliminary_payment_amount} centesimi) "
            f"differisce dal totale della pratica ({total} centesimi). Conferma esplicitamente per collegare comunque."
        )

    delivery.linked_practice_id = practice.id
    audit.record(
        entity_type=ENTITY_TYPE,
        entity_id=delivery.id,
        action="linked_to_practice",
        new_value=practice.practice_number,
        user_id=actor_user_id,
        reason="discrepanza confermata dall'operatore" if diverges else None,
    )

    await db.commit()
    return await repo.get_by_id(delivery_id)


async def trash_delivery(db: AsyncSession, delivery_id: int, *, actor_user_id: int) -> CalendarEvent:
    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)
    delivery = await repo.get_by_id(delivery_id)
    if delivery is None or delivery.event_type != CalendarEventType.riconsegna:
        raise NotFoundError(f"Riconsegna {delivery_id} non trovata")

    delivery.deleted_at = datetime.now(timezone.utc)
    delivery.deleted_by = actor_user_id
    audit.record(entity_type=ENTITY_TYPE, entity_id=delivery.id, action="trashed", user_id=actor_user_id)

    await db.commit()
    return await repo.get_by_id(delivery_id, include_deleted=True)


async def restore_delivery(db: AsyncSession, delivery_id: int, *, actor_user_id: int) -> CalendarEvent:
    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)
    delivery = await repo.get_by_id(delivery_id, include_deleted=True)
    if delivery is None or delivery.deleted_at is None or delivery.event_type != CalendarEventType.riconsegna:
        raise NotFoundError(f"Riconsegna {delivery_id} non trovata nel cestino")

    delivery.deleted_at = None
    delivery.deleted_by = None
    audit.record(entity_type=ENTITY_TYPE, entity_id=delivery.id, action="restored", user_id=actor_user_id)

    await db.commit()
    return await repo.get_by_id(delivery_id)
