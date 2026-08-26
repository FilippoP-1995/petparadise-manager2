from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from domain.errors import NotFoundError, ValidationDomainError
from domain.pickup.rules import ensure_pickup_fields_consistent
from domain.pickup.state_machine import validate_transition
from models.animal import Animal
from models.calendar_event import CalendarEvent, CalendarEventType, PickupStatus
from repositories.audit_repository import AuditRepository
from repositories.calendar_event_repository import CalendarEventRepository
from repositories.client_repository import ClientRepository
from repositories.reference_repositories import CalendarZoneRepository, CollaboratorRepository, CompanyLocationRepository
from repositories.veterinarian_repository import VeterinarianRepository
from schemas.calendar_event import CreatePracticeFromPickupRequest, PickupCreate, PickupUpdate
from services import practice_service

ENTITY_TYPE = "calendar_event"

_MUTABLE_FIELDS = (
    "start_at",
    "end_at",
    "client_id",
    "veterinarian_id",
    "collaborator_id",
    "pickup_type",
    "pickup_location_id",
    "pickup_zone_id",
    "pickup_address",
    "pickup_contact_name",
    "notes",
)


async def _resolve_references(db: AsyncSession, data) -> None:
    if data.client_id is not None and await ClientRepository(db).get_by_id(data.client_id) is None:
        raise NotFoundError(f"Cliente {data.client_id} non trovato")
    if data.veterinarian_id is not None and await VeterinarianRepository(db).get_by_id(data.veterinarian_id) is None:
        raise NotFoundError(f"Veterinario {data.veterinarian_id} non trovato")
    if data.collaborator_id is not None and await CollaboratorRepository(db).get_by_id(data.collaborator_id) is None:
        raise NotFoundError(f"Collaboratore {data.collaborator_id} non trovato")
    if data.pickup_location_id is not None and await CompanyLocationRepository(db).get_by_id(data.pickup_location_id) is None:
        raise NotFoundError(f"Sede {data.pickup_location_id} non trovata")
    if data.pickup_zone_id is not None and await CalendarZoneRepository(db).get_by_id(data.pickup_zone_id) is None:
        raise NotFoundError(f"Zona {data.pickup_zone_id} non trovata")


def _ensure_not_terminal(pickup: CalendarEvent) -> None:
    """Sezione 6 della richiesta: un Ritiro annullato 'non puo' essere
    riutilizzato come se fosse attivo' - nessuna modifica ai campi e'
    permessa una volta terminale."""
    if pickup.pickup_status == PickupStatus.annullato:
        raise ValidationDomainError("Il ritiro e' annullato (stato terminale): non e' piu' modificabile.")


async def create_pickup(db: AsyncSession, data: PickupCreate, *, actor_user_id: int) -> CalendarEvent:
    ensure_pickup_fields_consistent(
        data.pickup_type,
        pickup_location_id=data.pickup_location_id,
        pickup_zone_id=data.pickup_zone_id,
        veterinarian_id=data.veterinarian_id,
        collaborator_id=data.collaborator_id,
        pickup_contact_name=data.pickup_contact_name,
    )
    await _resolve_references(db, data)

    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)

    pickup = CalendarEvent(
        event_type=CalendarEventType.ritiro,
        pickup_status=PickupStatus.da_confermare,  # FACT V1: default reale, mai un parametro di creazione
        created_by=actor_user_id,
    )
    for field_name in _MUTABLE_FIELDS:
        setattr(pickup, field_name, getattr(data, field_name))
    pickup.animals = [Animal(**a.model_dump()) for a in data.animals]

    repo.add(pickup)
    await db.flush()
    audit.record(entity_type=ENTITY_TYPE, entity_id=pickup.id, action="created", user_id=actor_user_id)

    await db.commit()
    return await repo.get_by_id(pickup.id)


async def update_pickup(db: AsyncSession, pickup_id: int, data: PickupUpdate, *, actor_user_id: int) -> CalendarEvent:
    ensure_pickup_fields_consistent(
        data.pickup_type,
        pickup_location_id=data.pickup_location_id,
        pickup_zone_id=data.pickup_zone_id,
        veterinarian_id=data.veterinarian_id,
        collaborator_id=data.collaborator_id,
        pickup_contact_name=data.pickup_contact_name,
    )
    await _resolve_references(db, data)

    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)

    pickup = await repo.get_by_id(pickup_id)
    if pickup is None or pickup.event_type != CalendarEventType.ritiro:
        raise NotFoundError(f"Ritiro {pickup_id} non trovato")
    _ensure_not_terminal(pickup)

    for field_name in _MUTABLE_FIELDS:
        old_value = getattr(pickup, field_name)
        new_value = getattr(data, field_name)
        if old_value != new_value:
            audit.record(
                entity_type=ENTITY_TYPE,
                entity_id=pickup.id,
                action="field_changed",
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                user_id=actor_user_id,
            )
        setattr(pickup, field_name, new_value)
    pickup.animals = [Animal(**a.model_dump()) for a in data.animals]

    await db.commit()
    return await repo.get_by_id(pickup_id)


async def transition_pickup(db: AsyncSession, pickup_id: int, target_status: PickupStatus, *, actor_user_id: int) -> CalendarEvent:
    """doc14 §2: workflow uniforme, Operator o Admin indistintamente -
    nessun livello di correzione separato per il Ritiro (non definito da
    nessun documento, non inventato qui)."""
    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)

    pickup = await repo.get_by_id(pickup_id)
    if pickup is None or pickup.event_type != CalendarEventType.ritiro:
        raise NotFoundError(f"Ritiro {pickup_id} non trovato")

    validate_transition(pickup.pickup_status, target_status)

    old_status = pickup.pickup_status
    pickup.pickup_status = target_status
    audit.record(
        entity_type=ENTITY_TYPE,
        entity_id=pickup.id,
        action="state_changed",
        field_name="pickup_status",
        old_value=old_status.value,
        new_value=target_status.value,
        user_id=actor_user_id,
    )

    await db.commit()
    return await repo.get_by_id(pickup_id)


async def cancel_pickup(db: AsyncSession, pickup_id: int, reason: str | None, *, actor_user_id: int) -> CalendarEvent:
    """Azione A (sezione 6): annullamento normale - la pratica collegata,
    se esiste, NON viene modificata in alcun modo. Il chiamante (route)
    espone al frontend `linked_practice_id` gia' presente nella risposta
    per mostrare l'avviso richiesto."""
    return await transition_pickup(db, pickup_id, PickupStatus.annullato, actor_user_id=actor_user_id)


async def cancel_pickup_and_trash_practice(
    db: AsyncSession, pickup_id: int, reason: str, *, actor_user_id: int
) -> CalendarEvent:
    """Azione B (sezione 6): distinta da A, MAI un suo effetto collaterale.
    Riusa practice_service.trash_practice (dominio Pratica gia' esistente,
    'nessuna DELETE distruttiva', fatture mai toccate) - non duplicata qui.
    Atomica: trash_practice fa il proprio commit, ma essendo la STESSA
    sessione, quel commit include anche la mutazione del ritiro gia'
    pendente in questa funzione (un solo commit fisico)."""
    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)

    pickup = await repo.get_by_id(pickup_id)
    if pickup is None or pickup.event_type != CalendarEventType.ritiro:
        raise NotFoundError(f"Ritiro {pickup_id} non trovato")
    if pickup.linked_practice_id is None:
        raise ValidationDomainError(
            "Questo ritiro non ha una pratica collegata: usa l'azione di annullamento normale."
        )

    validate_transition(pickup.pickup_status, PickupStatus.annullato)
    old_status = pickup.pickup_status
    pickup.pickup_status = PickupStatus.annullato
    audit.record(
        entity_type=ENTITY_TYPE,
        entity_id=pickup.id,
        action="state_changed",
        field_name="pickup_status",
        old_value=old_status.value,
        new_value=PickupStatus.annullato.value,
        user_id=actor_user_id,
        reason=reason,
    )

    await practice_service.trash_practice(db, pickup.linked_practice_id, reason, actor_user_id=actor_user_id)

    return await repo.get_by_id(pickup_id)


async def create_practice_from_pickup_action(
    db: AsyncSession, pickup_id: int, request: CreatePracticeFromPickupRequest, *, actor_user_id: int
):
    """Punto di ingresso Percorso A. Blocca la riga del ritiro
    (SELECT ... FOR UPDATE) per tutta la transazione, poi delega
    interamente a practice_service.create_practice_from_pickup - il
    dominio Ritiro non implementa una propria logica di creazione pratica
    (sezione 5/10 della richiesta)."""
    repo = CalendarEventRepository(db)
    pickup = await repo.get_by_id_for_update(pickup_id)
    if pickup is None or pickup.event_type != CalendarEventType.ritiro:
        raise NotFoundError(f"Ritiro {pickup_id} non trovato")

    return await practice_service.create_practice_from_pickup(
        db,
        pickup,
        destination_branch_id=request.destination_branch_id,
        service_type=request.service_type,
        actor_user_id=actor_user_id,
    )


async def trash_pickup(db: AsyncSession, pickup_id: int, *, actor_user_id: int) -> CalendarEvent:
    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)
    pickup = await repo.get_by_id(pickup_id)
    if pickup is None or pickup.event_type != CalendarEventType.ritiro:
        raise NotFoundError(f"Ritiro {pickup_id} non trovato")

    pickup.deleted_at = datetime.now(timezone.utc)
    pickup.deleted_by = actor_user_id
    audit.record(entity_type=ENTITY_TYPE, entity_id=pickup.id, action="trashed", user_id=actor_user_id)

    await db.commit()
    return await repo.get_by_id(pickup_id, include_deleted=True)


async def restore_pickup(db: AsyncSession, pickup_id: int, *, actor_user_id: int) -> CalendarEvent:
    repo = CalendarEventRepository(db)
    audit = AuditRepository(db)
    pickup = await repo.get_by_id(pickup_id, include_deleted=True)
    if pickup is None or pickup.deleted_at is None or pickup.event_type != CalendarEventType.ritiro:
        raise NotFoundError(f"Ritiro {pickup_id} non trovato nel cestino")

    pickup.deleted_at = None
    pickup.deleted_by = None
    audit.record(entity_type=ENTITY_TYPE, entity_id=pickup.id, action="restored", user_id=actor_user_id)

    await db.commit()
    return await repo.get_by_id(pickup_id)
