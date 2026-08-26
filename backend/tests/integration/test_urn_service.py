import pytest

from domain.errors import NotFoundError, ValidationDomainError
from models.urn import UrnCategory
from schemas.urn import UrnCreate, UrnUpdate
from services import urn_service


def _urn_data(**overrides):
    base = dict(category=UrnCategory.urna, name="Urna in noce", material="Legno", price_cents=15000, quantity=5)
    base.update(overrides)
    return UrnCreate(**base)


async def test_create_urn_generates_internal_code_and_initial_movement(db_session, admin_user):
    urn = await urn_service.create_urn(db_session, _urn_data(), actor_user_id=admin_user.id)
    assert urn.internal_code == "URN-001"
    assert urn.quantity == 5

    from repositories.urn_repository import UrnMovementRepository

    movements = await UrnMovementRepository(db_session).list_for_urn(urn.id)
    assert len(movements) == 1
    assert movements[0].movement_type == "Creazione / carico iniziale"
    assert movements[0].quantity_delta == 5
    assert movements[0].old_quantity == 0
    assert movements[0].new_quantity == 5


async def test_internal_code_sequential_per_category(db_session, admin_user):
    urn1 = await urn_service.create_urn(db_session, _urn_data(), actor_user_id=admin_user.id)
    urn2 = await urn_service.create_urn(db_session, _urn_data(name="Urna in marmo"), actor_user_id=admin_user.id)
    accessorio = await urn_service.create_urn(
        db_session, _urn_data(category=UrnCategory.accessorio, name="Portafoto"), actor_user_id=admin_user.id
    )
    assert urn1.internal_code == "URN-001"
    assert urn2.internal_code == "URN-002"
    assert accessorio.internal_code == "ACC-001", "contatore indipendente per categoria"


async def test_create_urn_with_zero_quantity_has_no_movement(db_session, admin_user):
    urn = await urn_service.create_urn(db_session, _urn_data(quantity=0), actor_user_id=admin_user.id)

    from repositories.urn_repository import UrnMovementRepository

    movements = await UrnMovementRepository(db_session).list_for_urn(urn.id)
    assert movements == []


async def test_create_urn_rejects_negative_price(db_session, admin_user):
    with pytest.raises(ValidationDomainError):
        await urn_service.create_urn(db_session, _urn_data(price_cents=-1), actor_user_id=admin_user.id)


async def test_update_urn_quantity_change_records_rettifica_manuale(db_session, admin_user):
    urn = await urn_service.create_urn(db_session, _urn_data(quantity=5), actor_user_id=admin_user.id)
    updated = await urn_service.update_urn(
        db_session, urn.id, UrnUpdate(**{**_urn_data().model_dump(), "quantity": 8}), actor_user_id=admin_user.id
    )
    assert updated.quantity == 8
    assert updated.internal_code == "URN-001", "il codice interno non cambia modificando la scheda"

    from repositories.urn_repository import UrnMovementRepository

    movements = await UrnMovementRepository(db_session).list_for_urn(urn.id)
    rettifiche = [m for m in movements if m.movement_type == "Rettifica manuale"]
    assert len(rettifiche) == 1
    assert rettifiche[0].quantity_delta == 3
    assert rettifiche[0].old_quantity == 5
    assert rettifiche[0].new_quantity == 8


async def test_update_urn_without_quantity_change_records_no_movement(db_session, admin_user):
    urn = await urn_service.create_urn(db_session, _urn_data(quantity=5), actor_user_id=admin_user.id)
    await urn_service.update_urn(db_session, urn.id, UrnUpdate(**_urn_data(quantity=5).model_dump()), actor_user_id=admin_user.id)

    from repositories.urn_repository import UrnMovementRepository

    movements = await UrnMovementRepository(db_session).list_for_urn(urn.id)
    assert len(movements) == 1, "solo il movimento di creazione, nessuna rettifica se la quantita' non cambia"


async def test_deactivate_urn_records_zero_delta_movement(db_session, admin_user):
    urn = await urn_service.create_urn(db_session, _urn_data(quantity=5), actor_user_id=admin_user.id)
    deactivated = await urn_service.deactivate_urn(db_session, urn.id, actor_user_id=admin_user.id)
    assert deactivated.active is False

    from repositories.urn_repository import UrnMovementRepository

    movements = await UrnMovementRepository(db_session).list_for_urn(urn.id)
    rimozioni = [m for m in movements if m.movement_type == "Rimozione dal catalogo"]
    assert len(rimozioni) == 1
    assert rimozioni[0].quantity_delta == 0


async def test_deactivate_unknown_urn_raises_not_found(db_session, admin_user):
    with pytest.raises(NotFoundError):
        await urn_service.deactivate_urn(db_session, 999999, actor_user_id=admin_user.id)
