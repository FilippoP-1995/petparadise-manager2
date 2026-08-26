import pytest

from domain.errors import NotFoundError, ValidationDomainError
from models.practice import PracticeStatus
from schemas.practice import (
    AnimalInput,
    CorrectionRequest,
    LineItemInput,
    OverrideTotalRequest,
    PracticeCreate,
    PracticeUpdate,
    TransitionRequest,
    TrashRequest,
)
from services import client_service, practice_service


def _create_data(sample_location, sample_client, **overrides):
    base = dict(
        client_id=sample_client.id,
        destination_branch_id=sample_location.id,
        request_origin="Collaboratore",
        service_type="Cremazione singola",
    )
    base.update(overrides)
    return PracticeCreate(**base)


async def test_create_practice_percorso_b_starts_at_ritirato(db_session, admin_user, sample_client, sample_location):
    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    assert practice.status == PracticeStatus.ritirato
    assert practice.practice_number.startswith("COL-")


async def test_create_practice_consegna_in_sede_also_starts_at_ritirato(
    db_session, admin_user, sample_client, sample_location
):
    practice = await practice_service.create_practice(
        db_session,
        _create_data(sample_location, sample_client, request_origin="Consegna in sede", service_type="Da decidere"),
        actor_user_id=admin_user.id,
    )
    assert practice.status == PracticeStatus.ritirato
    assert practice.practice_number.startswith("PP-")


@pytest.mark.parametrize("origin", ["Privato", "Veterinario"])
async def test_create_practice_rejects_percorso_a_origins(db_session, admin_user, sample_client, sample_location, origin):
    """Percorso A (Ritiro -> Pratica) non e' disponibile in questa fase."""
    with pytest.raises(ValidationDomainError):
        await practice_service.create_practice(
            db_session, _create_data(sample_location, sample_client, request_origin=origin), actor_user_id=admin_user.id
        )


async def test_create_practice_rejects_invalid_service_type(db_session, admin_user, sample_client, sample_location):
    with pytest.raises(ValidationDomainError):
        await practice_service.create_practice(
            db_session,
            _create_data(sample_location, sample_client, service_type="Non esiste"),
            actor_user_id=admin_user.id,
        )


async def test_create_practice_rejects_unknown_destination_branch(db_session, admin_user, sample_client, sample_location):
    with pytest.raises(NotFoundError):
        await practice_service.create_practice(
            db_session,
            _create_data(sample_location, sample_client, destination_branch_id=999_999),
            actor_user_id=admin_user.id,
        )


async def test_create_practice_rejects_unknown_client(db_session, admin_user, sample_location, sample_client):
    with pytest.raises(NotFoundError):
        await practice_service.create_practice(
            db_session, _create_data(sample_location, sample_client, client_id=999_999), actor_user_id=admin_user.id
        )


async def test_owner_snapshot_captures_client_and_never_changes_after(
    db_session, admin_user, sample_client, sample_location
):
    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    assert practice.owner_snapshot["first_name"] == "Mario"
    assert practice.owner_snapshot["last_name"] == "Rossi"

    from schemas.client import ClientUpdate

    await client_service.update_client(
        db_session, sample_client.id, ClientUpdate(first_name="ModificatoDopo", last_name="Rossi"), actor_user_id=admin_user.id
    )

    from repositories.practice_repository import PracticeRepository

    reloaded = await PracticeRepository(db_session).get_by_id(practice.id)
    assert reloaded.owner_snapshot["first_name"] == "Mario", "owner_snapshot deve restare immutato dopo la creazione"


async def test_create_practice_persists_animals_tags_and_line_items(
    db_session, admin_user, sample_client, sample_location
):
    from models.tag import Tag

    tag = Tag(code="urgente", label="Urgente", category="operativo")
    db_session.add(tag)
    await db_session.flush()

    data = _create_data(
        sample_location,
        sample_client,
        animals=[
            AnimalInput(name="Fido", species="Cane"),
            AnimalInput(name="Micio", species="Gatto"),
            AnimalInput(name="Terzo", species="Coniglio"),
        ],
        line_items=[
            LineItemInput(category="cremazione", description="Cremazione singola", amount_cents=12000),
            LineItemInput(category="urna", description="Urna standard", amount_cents=3000, channel="D"),
        ],
        tag_ids=[tag.id],
    )
    practice = await practice_service.create_practice(db_session, data, actor_user_id=admin_user.id)

    assert len(practice.animals) == 3, "nessun limite artificiale sul numero di animali per pratica"
    assert {a.name for a in practice.animals} == {"Fido", "Micio", "Terzo"}
    assert len(practice.line_items) == 2
    assert {li.channel.value for li in practice.line_items} == {"W", "D"}
    assert [t.id for t in practice.tags] == [tag.id]


async def test_create_practice_with_ddt_fields(db_session, admin_user, sample_client, sample_location):
    practice = await practice_service.create_practice(
        db_session,
        _create_data(
            sample_location,
            sample_client,
            ddt_number=42,
            transport_method="Furgone refrigerato",
            vehicle_plate="AB123CD",
            signatory_identity_document_number="CI12345",
        ),
        actor_user_id=admin_user.id,
    )
    assert practice.ddt_number == 42
    assert practice.transport_method == "Furgone refrigerato"
    assert practice.vehicle_plate == "AB123CD"
    assert practice.signatory_identity_document_number == "CI12345"


async def test_update_practice_replaces_children_and_logs_field_changes(
    db_session, admin_user, sample_client, sample_location
):
    practice = await practice_service.create_practice(
        db_session,
        _create_data(sample_location, sample_client, animals=[AnimalInput(name="Fido")]),
        actor_user_id=admin_user.id,
    )

    update_data = PracticeUpdate(
        destination_branch_id=sample_location.id,
        request_origin="Collaboratore",
        service_type="Cremazione collettiva",
        animals=[AnimalInput(name="Fido2"), AnimalInput(name="Nuovo")],
    )
    updated = await practice_service.update_practice(db_session, practice.id, update_data, actor_user_id=admin_user.id)

    assert updated.service_type == "Cremazione collettiva"
    assert {a.name for a in updated.animals} == {"Fido2", "Nuovo"}

    from sqlalchemy import select

    from models.audit_log import AuditLog

    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "practice", AuditLog.entity_id == practice.id, AuditLog.action == "field_changed"
            )
        )
    ).scalars().all()
    assert any(r.field_name == "service_type" for r in rows)


async def test_transition_workflow_moves_forward_one_step(db_session, admin_user, sample_client, sample_location):
    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    updated = await practice_service.transition_practice_state(
        db_session, practice.id, TransitionRequest(target_status=PracticeStatus.in_programma), actor_user_id=admin_user.id
    )
    assert updated.status == PracticeStatus.in_programma


async def test_transition_workflow_rejects_a_skip(db_session, admin_user, sample_client, sample_location):
    from domain.errors import InvalidTransitionError

    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    with pytest.raises(InvalidTransitionError):
        await practice_service.transition_practice_state(
            db_session, practice.id, TransitionRequest(target_status=PracticeStatus.cremato), actor_user_id=admin_user.id
        )


async def test_correction_allows_regression_with_reason_and_audits_it(
    db_session, admin_user, sample_client, sample_location
):
    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    await practice_service.transition_practice_state(
        db_session, practice.id, TransitionRequest(target_status=PracticeStatus.in_programma), actor_user_id=admin_user.id
    )
    corrected = await practice_service.correct_practice_state(
        db_session,
        practice.id,
        CorrectionRequest(target_status=PracticeStatus.ritirato, reason="errore di battitura operatore"),
        actor_user_id=admin_user.id,
    )
    assert corrected.status == PracticeStatus.ritirato

    from sqlalchemy import select

    from models.audit_log import AuditLog

    row = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.entity_type == "practice", AuditLog.action == "state_corrected")
        )
    ).scalar_one()
    assert row.reason == "errore di battitura operatore"
    assert row.old_value == "in_programma"
    assert row.new_value == "ritirato"


async def test_correction_without_reason_is_rejected(db_session, admin_user, sample_client, sample_location):
    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    # una stringa vuota e' gia' respinta da Pydantic (Field(min_length=1));
    # il livello di dominio copre il caso che Pydantic non vede - solo
    # spazi bianchi, non un motivo reale.
    whitespace_only = CorrectionRequest.model_construct(target_status=PracticeStatus.cremato, reason="   ")
    with pytest.raises(ValidationDomainError):
        await practice_service.correct_practice_state(db_session, practice.id, whitespace_only, actor_user_id=admin_user.id)


async def test_trash_and_restore_practice(db_session, admin_user, sample_client, sample_location):
    from repositories.practice_repository import PracticeRepository

    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    trashed = await practice_service.trash_practice(
        db_session, practice.id, "richiesta cliente", actor_user_id=admin_user.id
    )
    assert trashed.deleted_at is not None

    repo = PracticeRepository(db_session)
    assert await repo.get_by_id(practice.id) is None, "una pratica cestinata non e' piu' visibile nella lista attiva"

    restored = await practice_service.restore_practice(db_session, practice.id, actor_user_id=admin_user.id)
    assert restored.deleted_at is None
    assert await repo.get_by_id(practice.id) is not None


async def test_override_total_set_and_clear(db_session, admin_user, sample_client, sample_location):
    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    overridden = await practice_service.set_total_override(
        db_session,
        practice.id,
        OverrideTotalRequest(amount_cents=5000, reason="sconto concordato telefonicamente"),
        actor_user_id=admin_user.id,
    )
    assert overridden.computed_total_override_cents == 5000
    assert overridden.computed_total_override_reason == "sconto concordato telefonicamente"
    assert overridden.computed_total_override_by == admin_user.id

    cleared = await practice_service.clear_total_override(db_session, practice.id, actor_user_id=admin_user.id)
    assert cleared.computed_total_override_cents is None
    assert cleared.computed_total_override_reason is None


async def test_mark_collaborator_billed_requires_a_collaborator(db_session, admin_user, sample_client, sample_location):
    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    with pytest.raises(ValidationDomainError):
        await practice_service.mark_collaborator_billed(db_session, practice.id, actor_user_id=admin_user.id)


async def test_mark_owner_notified(db_session, admin_user, sample_client, sample_location):
    from models.practice import OwnerNotifiedStatus

    practice = await practice_service.create_practice(
        db_session, _create_data(sample_location, sample_client), actor_user_id=admin_user.id
    )
    assert practice.owner_notified_status == OwnerNotifiedStatus.da_avvisare

    updated = await practice_service.mark_owner_notified(db_session, practice.id, actor_user_id=admin_user.id)
    assert updated.owner_notified_status == OwnerNotifiedStatus.avvisato
    assert updated.owner_notified_by == admin_user.id
    assert updated.owner_notified_at is not None
