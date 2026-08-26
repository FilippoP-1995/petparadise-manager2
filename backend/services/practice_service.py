from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from domain.errors import NotFoundError, ValidationDomainError
from domain.practice.rules import build_owner_snapshot, ensure_direct_creation_origin, ensure_valid_service_type
from domain.practice.state_machine import validate_correction_transition, validate_workflow_transition
from models.animal import Animal
from models.calendar_event import PickupStatus
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


async def _create_practice_core(
    db: AsyncSession, data: PracticeCreate, *, client, originating_pickup_event_id: int | None, actor_user_id: int
) -> Practice:
    """Nucleo condiviso di creazione pratica - doc09: 'create_practice
    imposta sempre status=ritirato, per ENTRAMBI i percorsi di creazione'.
    Un'unica implementazione di numerazione/owner_snapshot/stato
    hardcoded/audit atomico, usata sia dal Percorso B (create_practice,
    sotto) sia dal Percorso A (create_practice_from_pickup) - la differenza
    tra i due percorsi (quali origini sono ammesse, da dove arrivano
    client/campi di logistica) resta nei rispettivi chiamanti, mai qui."""
    await _resolve_references(db, data)

    practice_repo = PracticeRepository(db)
    audit = AuditRepository(db)

    practice_number = await practice_repo.next_practice_number(
        service_type=data.service_type, request_origin=data.request_origin
    )

    practice = Practice(
        practice_number=practice_number,
        status=PracticeStatus.ritirato,  # doc14 SS1: mai un parametro di creazione
        client_id=client.id,
        owner_snapshot=build_owner_snapshot(client),
        originating_pickup_event_id=originating_pickup_event_id,
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
    return practice


async def create_practice(db: AsyncSession, data: PracticeCreate, *, actor_user_id: int) -> Practice:
    """Percorso B (diretto) - doc06 'Relazione Ritiro -> Pratica'."""
    ensure_valid_service_type(data.service_type)
    ensure_direct_creation_origin(data.request_origin)

    client = await ClientRepository(db).get_by_id(data.client_id)
    if client is None or not client.active:
        raise NotFoundError(f"Cliente {data.client_id} non trovato")

    practice = await _create_practice_core(db, data, client=client, originating_pickup_event_id=None, actor_user_id=actor_user_id)

    await db.commit()
    return await PracticeRepository(db).get_by_id(practice.id)


async def create_practice_from_pickup(
    db: AsyncSession,
    pickup_event,
    *,
    destination_branch_id: int,
    service_type: str,
    actor_user_id: int,
) -> Practice:
    """Percorso A - doc06 'Relazione Ritiro -> Pratica' + sezione 5/10 della
    richiesta 'RITIRO/RICONSEGNA'. Il chiamante (services/pickup_service.py)
    deve aver gia' caricato `pickup_event` con un lock di riga
    (SELECT ... FOR UPDATE, CalendarEventRepository.get_by_id_for_update)
    per tutta la durata di QUESTA transazione - qui non si acquisisce un
    lock nuovo, si assume che il chiamante lo tenga gia'. Riusa
    _create_practice_core: nessuna logica di creazione pratica duplicata
    nel dominio Ritiro (regola esplicita ricevuta)."""
    if pickup_event.pickup_status != PickupStatus.ritirato:
        raise ValidationDomainError("Il ritiro deve essere nello stato 'ritirato' prima di generare una pratica.")
    if pickup_event.linked_practice_id is not None:
        raise ValidationDomainError("Questo ritiro e' gia' collegato a una pratica.")
    if pickup_event.client_id is None:
        raise ValidationDomainError("Il ritiro deve avere un cliente assegnato prima di generare una pratica.")

    ensure_valid_service_type(service_type)

    client = await ClientRepository(db).get_by_id(pickup_event.client_id)
    if client is None or not client.active:
        raise NotFoundError(f"Cliente {pickup_event.client_id} non trovato")

    data = PracticeCreate(
        client_id=client.id,
        destination_branch_id=destination_branch_id,
        # doc06 non definisce request_origin per il Percorso A: stessa
        # logica esatta gia' verificata in V1 (FACT, app.py:15740 -
        # "Veterinario" if event["veterinarian_id"] else "Privato").
        request_origin="Veterinario" if pickup_event.veterinarian_id else "Privato",
        service_type=service_type,
        collaborator_id=pickup_event.collaborator_id,
        origin_veterinarian_id=pickup_event.veterinarian_id,
        pickup_type=pickup_event.pickup_type,
        pickup_location_id=pickup_event.pickup_location_id,
        pickup_zone_id=pickup_event.pickup_zone_id,
        pickup_address=pickup_event.pickup_address,
        pickup_contact_name=pickup_event.pickup_contact_name,
        notes=pickup_event.notes,
    )
    practice = await _create_practice_core(
        db, data, client=client, originating_pickup_event_id=pickup_event.id, actor_user_id=actor_user_id
    )

    # Stessa riga, mai una copia (doc06 'calendar_events + figlie') - risolve
    # anche il bug V1 dove solo il primo animale sopravviveva alla
    # conversione (FACT, app.py:15733,15740). Riparentela via le collezioni
    # ORM (non solo settando la FK a mano): entrambe le relazioni
    # (CalendarEvent.animals e Practice.animals) usano
    # cascade='delete-orphan', che richiede che l'oggetto risulti spostato
    # attraverso le collezioni stesse, non solo con un UPDATE diretto sulla
    # colonna - altrimenti l'animale resta "orfano" della vecchia
    # collezione e viene cancellato al flush invece di essere riassegnato.
    for animal in list(pickup_event.animals):
        pickup_event.animals.remove(animal)
        practice.animals.append(animal)

    pickup_event.linked_practice_id = practice.id
    AuditRepository(db).record(
        entity_type="calendar_event",
        entity_id=pickup_event.id,
        action="practice_created",
        new_value=practice.practice_number,
        user_id=actor_user_id,
    )

    await db.commit()
    return await PracticeRepository(db).get_by_id(practice.id)


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


async def apply_automatic_cycle_side_effect(
    db: AsyncSession,
    practice: Practice,
    target_status: PracticeStatus,
    *,
    cremation_registered: bool,
    cycle_id: int,
    actor_user_id: int,
) -> None:
    """doc14 §1: 'in_programma -> cremato: Sistema (side-effect automatico
    del completamento ciclo) ... audit_log, azione distinta se manuale vs
    automatica'. Non e' ne' un workflow (transition_practice_state - target
    non e' il prossimo bordo dichiarato per il verso 'indietro') ne' una
    correzione umana discrezionale (correct_practice_state, riservata
    all'Admin con motivo a scelta dell'operatore): e' una conseguenza
    deterministica e automatica di un evento di dominio del ciclo di
    cremazione (completamento o ripristino), applicata qui direttamente.
    NON chiama commit() - il chiamante (cremation_cycle_service) puo'
    toccare piu' pratiche nella stessa transazione, un solo commit finale."""
    old_status = practice.status
    practice.status = target_status
    practice.cremation_registered = cremation_registered
    AuditRepository(db).record(
        entity_type=ENTITY_TYPE,
        entity_id=practice.id,
        action="state_changed",
        field_name="status",
        old_value=old_status.value,
        new_value=target_status.value,
        user_id=actor_user_id,
        reason=f"side-effect automatico: ciclo di cremazione #{cycle_id}",
    )


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
