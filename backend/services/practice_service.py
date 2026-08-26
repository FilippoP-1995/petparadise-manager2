from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from domain.errors import NotFoundError, ValidationDomainError
from domain.practice.rules import build_owner_snapshot, ensure_direct_creation_origin, ensure_valid_service_type
from domain.practice.state_machine import validate_correction_transition, validate_workflow_transition
from models.animal import Animal
from models.practice import CollaboratorBillingStatus, OwnerNotifiedStatus, Practice, PracticeLineItem, PracticeStatus
from repositories.audit_repository import AuditRepository
from repositories.client_repository import ClientRepository
from repositories.practice_repository import PracticeRepository
from repositories.reference_repositories import (
    CalendarZoneRepository,
    CollaboratorRepository,
    CompanyLocationRepository,
    TagRepository,
    UrnRepository,
)
from repositories.veterinarian_repository import VeterinarianRepository
from schemas.practice import (
    CorrectionRequest,
    OverrideTotalRequest,
    PracticeCreate,
    PracticeUpdate,
    TransitionRequest,
)

ENTITY_TYPE = "practice"


async def _resolve_references(db: AsyncSession, data) -> None:
    """Verifica di dominio, non solo un vincolo FK che fallirebbe con un
    500 generico (doc09/11: 'input invalido -> 422 con dettaglio, mai un
    500 generico')."""
    if await CompanyLocationRepository(db).get_by_id(data.destination_branch_id) is None:
        raise NotFoundError(f"Sede di destinazione {data.destination_branch_id} non trovata")
    if data.collaborator_id is not None and await CollaboratorRepository(db).get_by_id(data.collaborator_id) is None:
        raise NotFoundError(f"Collaboratore {data.collaborator_id} non trovato")
    vet_repo = VeterinarianRepository(db)
    if data.veterinarian_id is not None and await vet_repo.get_by_id(data.veterinarian_id) is None:
        raise NotFoundError(f"Veterinario {data.veterinarian_id} non trovato")
    if data.origin_veterinarian_id is not None and await vet_repo.get_by_id(data.origin_veterinarian_id) is None:
        raise NotFoundError(f"Veterinario di origine {data.origin_veterinarian_id} non trovato")
    if data.pickup_location_id is not None and await CompanyLocationRepository(db).get_by_id(data.pickup_location_id) is None:
        raise NotFoundError(f"Sede di ritiro {data.pickup_location_id} non trovata")
    if data.pickup_zone_id is not None and await CalendarZoneRepository(db).get_by_id(data.pickup_zone_id) is None:
        raise NotFoundError(f"Zona di ritiro {data.pickup_zone_id} non trovata")
    urn_repo = UrnRepository(db)
    for item in data.line_items:
        if item.urn_catalog_id is not None and await urn_repo.get_by_id(item.urn_catalog_id) is None:
            raise NotFoundError(f"Urna {item.urn_catalog_id} non trovata")
    if data.tag_ids:
        found = await TagRepository(db).get_by_ids(data.tag_ids)
        if len(found) != len(set(data.tag_ids)):
            missing = set(data.tag_ids) - {t.id for t in found}
            raise NotFoundError(f"Tag non trovati: {sorted(missing)}")


MUTABLE_FIELD_NAMES = (
    "destination_branch_id",
    "request_origin",
    "service_type",
    "collaborator_id",
    "veterinarian_id",
    "origin_veterinarian_id",
    "pickup_type",
    "pickup_location_id",
    "pickup_zone_id",
    "pickup_address",
    "pickup_contact_name",
    "provenance_code",
    "microchip",
    "notes",
    "ddt_number",
    "ddt_date",
    "ddt_pdf_path",
    "signature_data",
    "transport_method",
    "vehicle_plate",
    "temperature_mode",
    "package_count",
    "container_id",
    "lot_number",
    "treatment_method",
    "delivery_at_clinic",
    "delivery_at_home",
    "signatory_identity_document_number",
    "signatory_identity_document_date",
    "signatory_signing_place",
    "to_invoice",
    "send_catalog",
    "send_estremi",
    "voucher_requested",
    "use_voucher",
    "no_whatsapp_message",
)


def _apply_mutable_fields(practice: Practice, data) -> None:
    for field_name in MUTABLE_FIELD_NAMES:
        setattr(practice, field_name, getattr(data, field_name))


async def create_practice(db: AsyncSession, data: PracticeCreate, *, actor_user_id: int) -> Practice:
    """Percorso B (diretto) - doc06 'Relazione Ritiro -> Pratica'. Il
    Percorso A (da Ritiro) non e' disponibile in questa fase: il dominio
    Ritiro (calendar_events) non e' ancora stato costruito in V2."""
    ensure_valid_service_type(data.service_type)
    ensure_direct_creation_origin(data.request_origin)
    await _resolve_references(db, data)

    client = await ClientRepository(db).get_by_id(data.client_id)
    if client is None or not client.active:
        raise NotFoundError(f"Cliente {data.client_id} non trovato")

    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice_number = await practice_repo.next_practice_number(
        service_type=data.service_type, request_origin=data.request_origin
    )

    practice = Practice(
        practice_number=practice_number,
        status=PracticeStatus.ritirato,  # doc14 SS1: mai un parametro di creazione
        client_id=data.client_id,
        owner_snapshot=build_owner_snapshot(client),
        created_by=actor_user_id,
    )
    _apply_mutable_fields(practice, data)
    practice.animals = [Animal(**a.model_dump()) for a in data.animals]
    practice.line_items = [PracticeLineItem(**li.model_dump()) for li in data.line_items]
    if data.tag_ids:
        practice.tags = await TagRepository(db).get_by_ids(data.tag_ids)

    practice_repo.add(practice)
    await db.flush()  # assegna practice.id, popola gli id delle righe figlie in cascata

    audit.record(entity_type=ENTITY_TYPE, entity_id=practice.id, action="created", user_id=actor_user_id)

    await db.commit()
    return await practice_repo.get_by_id(practice.id)


async def update_practice(db: AsyncSession, practice_id: int, data: PracticeUpdate, *, actor_user_id: int) -> Practice:
    ensure_valid_service_type(data.service_type)
    ensure_direct_creation_origin(data.request_origin)
    await _resolve_references(db, data)

    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")

    for field_name in MUTABLE_FIELD_NAMES:
        old_value = getattr(practice, field_name)
        new_value = getattr(data, field_name)
        if old_value != new_value:
            audit.record(
                entity_type=ENTITY_TYPE,
                entity_id=practice.id,
                action="field_changed",
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                user_id=actor_user_id,
            )
        setattr(practice, field_name, new_value)

    practice.animals = [Animal(**a.model_dump()) for a in data.animals]
    practice.line_items = [PracticeLineItem(**li.model_dump()) for li in data.line_items]
    practice.tags = await TagRepository(db).get_by_ids(data.tag_ids) if data.tag_ids else []

    await db.commit()
    return await practice_repo.get_by_id(practice_id)


async def transition_practice_state(
    db: AsyncSession, practice_id: int, request: TransitionRequest, *, actor_user_id: int
) -> Practice:
    """Workflow normale - Operator o Admin, nessun motivo richiesto (doc14 §1)."""
    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")

    validate_workflow_transition(practice.status, request.target_status, practice.service_type)

    old_status = practice.status
    practice.status = request.target_status
    audit.record(
        entity_type=ENTITY_TYPE,
        entity_id=practice.id,
        action="state_changed",
        field_name="status",
        old_value=old_status.value,
        new_value=request.target_status.value,
        user_id=actor_user_id,
    )

    await db.commit()
    return await practice_repo.get_by_id(practice_id)


async def correct_practice_state(
    db: AsyncSession, practice_id: int, request: CorrectionRequest, *, actor_user_id: int
) -> Practice:
    """Correzione eccezionale - SOLO Admin (verificato a livello route via
    require_role), motivo obbligatorio, audit action='state_corrected'
    (doc14 §1). Non e' un bypass: i vincoli di dominio restano validi."""
    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")

    validate_correction_transition(practice.status, request.target_status, practice.service_type, request.reason)

    old_status = practice.status
    practice.status = request.target_status
    audit.record(
        entity_type=ENTITY_TYPE,
        entity_id=practice.id,
        action="state_corrected",
        field_name="status",
        old_value=old_status.value,
        new_value=request.target_status.value,
        user_id=actor_user_id,
        reason=request.reason,
    )

    await db.commit()
    return await practice_repo.get_by_id(practice_id)


async def trash_practice(db: AsyncSession, practice_id: int, reason: str | None, *, actor_user_id: int) -> Practice:
    """doc06 '4. Cancellazione pratica coerente': soft-delete, mai una
    DELETE reale nel flusso normale. Lo scollegamento degli eventi
    calendario collegati (PracticeTrashed) non e' applicabile in questa
    fase: il dominio Ritiro/calendar_events non e' ancora stato costruito."""
    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")

    practice.deleted_at = datetime.now(timezone.utc)
    practice.deleted_by = actor_user_id
    audit.record(
        entity_type=ENTITY_TYPE, entity_id=practice.id, action="trashed", user_id=actor_user_id, reason=reason
    )

    await db.commit()
    return await practice_repo.get_by_id(practice_id, include_deleted=True)


async def restore_practice(db: AsyncSession, practice_id: int, *, actor_user_id: int) -> Practice:
    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id, include_deleted=True)
    if practice is None or practice.deleted_at is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata nel cestino")

    practice.deleted_at = None
    practice.deleted_by = None
    audit.record(entity_type=ENTITY_TYPE, entity_id=practice.id, action="restored", user_id=actor_user_id)

    await db.commit()
    return await practice_repo.get_by_id(practice_id)


async def set_total_override(
    db: AsyncSession, practice_id: int, request: OverrideTotalRequest, *, actor_user_id: int
) -> Practice:
    """doc06 Addendum D: azione dedicata, tracciata, mai una sovrascrittura
    silenziosa del calcolo automatico."""
    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")

    practice.computed_total_override_cents = request.amount_cents
    practice.computed_total_override_reason = request.reason
    practice.computed_total_override_by = actor_user_id
    practice.computed_total_override_at = datetime.now(timezone.utc)
    audit.record(
        entity_type=ENTITY_TYPE,
        entity_id=practice.id,
        action="total_overridden",
        new_value=str(request.amount_cents),
        user_id=actor_user_id,
        reason=request.reason,
    )

    await db.commit()
    return await practice_repo.get_by_id(practice_id)


async def clear_total_override(db: AsyncSession, practice_id: int, *, actor_user_id: int) -> Practice:
    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")

    practice.computed_total_override_cents = None
    practice.computed_total_override_reason = None
    practice.computed_total_override_by = None
    practice.computed_total_override_at = None
    audit.record(entity_type=ENTITY_TYPE, entity_id=practice.id, action="total_override_cleared", user_id=actor_user_id)

    await db.commit()
    return await practice_repo.get_by_id(practice_id)


async def mark_owner_notified(db: AsyncSession, practice_id: int, *, actor_user_id: int) -> Practice:
    """doc06 Addendum G."""
    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")

    practice.owner_notified_status = OwnerNotifiedStatus.avvisato
    practice.owner_notified_at = datetime.now(timezone.utc)
    practice.owner_notified_by = actor_user_id
    audit.record(entity_type=ENTITY_TYPE, entity_id=practice.id, action="owner_notified", user_id=actor_user_id)

    await db.commit()
    return await practice_repo.get_by_id(practice_id)


async def mark_collaborator_billed(db: AsyncSession, practice_id: int, *, actor_user_id: int) -> Practice:
    """doc06 Addendum F: flag di processo interno, mai un documento
    fiscale."""
    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice = await practice_repo.get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")
    if practice.collaborator_id is None:
        raise ValidationDomainError("La pratica non ha un collaboratore associato.")

    practice.collaborator_billing_status = CollaboratorBillingStatus.fatturato
    practice.collaborator_billing_invoiced_at = datetime.now(timezone.utc)
    audit.record(entity_type=ENTITY_TYPE, entity_id=practice.id, action="collaborator_billed", user_id=actor_user_id)

    await db.commit()
    return await practice_repo.get_by_id(practice_id)
