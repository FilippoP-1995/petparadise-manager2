"""Test di atomicita' specifici del dominio Urne: la scrittura sulla
scheda urna (quantita') e il movimento di magazzino corrispondente devono
essere nella stessa transazione - se una fallisce, nessuna delle due deve
sopravvivere (stessa tecnica di iniezione di fallimento gia' usata negli
altri domini di questa sessione, vincolo NOT NULL reale su entity_type)."""

import pytest
from sqlalchemy.exc import IntegrityError

from models.urn import Urn, UrnCategory, UrnMovement
from repositories.audit_repository import AuditRepository
from repositories.urn_repository import UrnCatalogRepository, UrnMovementRepository


async def test_failed_movement_write_rolls_back_quantity_change(db_session, admin_user):
    catalog = UrnCatalogRepository(db_session)
    code = await catalog.next_internal_code(UrnCategory.urna)
    urn = Urn(
        category=UrnCategory.urna,
        name="Urna di test",
        internal_code=code,
        price_cents=10000,
        quantity=5,
        created_by=admin_user.id,
        updated_by=admin_user.id,
    )
    catalog.add(urn)
    await db_session.commit()
    urn_id = urn.id

    reloaded = await catalog.get_by_id(urn_id)
    reloaded.quantity = 8

    # Movimento con urn_id NULL - viola la NOT NULL reale della colonna,
    # stessa tecnica gia' usata per gli altri domini di questa sessione.
    UrnMovementRepository(db_session).add(
        UrnMovement(
            urn_id=None,
            practice_id=None,
            user_id=admin_user.id,
            movement_type="Rettifica manuale",
            quantity_delta=3,
            old_quantity=5,
            new_quantity=8,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final = await catalog.get_by_id(urn_id)
    assert final.quantity == 5, "la quantita' non deve cambiare se il movimento corrispondente fallisce"

    movements = await UrnMovementRepository(db_session).list_for_urn(urn_id)
    assert movements == [], "nessun movimento orfano deve sopravvivere al rollback"


async def test_failed_audit_write_rolls_back_urn_creation(db_session, admin_user):
    catalog = UrnCatalogRepository(db_session)
    code = await catalog.next_internal_code(UrnCategory.urna)
    urn = Urn(
        category=UrnCategory.urna,
        name="Urna di test 2",
        internal_code=code,
        price_cents=10000,
        quantity=2,
        created_by=admin_user.id,
        updated_by=admin_user.id,
    )
    catalog.add(urn)
    await db_session.flush()
    urn_id = urn.id

    UrnMovementRepository(db_session).add(
        UrnMovement(
            urn_id=urn_id,
            practice_id=None,
            user_id=admin_user.id,
            movement_type="Creazione / carico iniziale",
            quantity_delta=2,
            old_quantity=0,
            new_quantity=2,
        )
    )
    AuditRepository(db_session).record(entity_type=None, entity_id=urn_id, action="created", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    assert await catalog.get_by_id(urn_id) is None, "nessuna urna orfana deve sopravvivere se l'audit fallisce"
