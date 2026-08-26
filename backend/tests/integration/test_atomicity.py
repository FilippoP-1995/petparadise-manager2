"""Verifica comportamentale (non solo di lettura del codice) della regola
vincolante doc09 'atomicita' modifica di dominio + audit': scrittura di
dominio e relativa riga di audit_log vivono nella stessa transazione, quindi
o vengono committate entrambe o nessuna delle due.

I service (client_service/veterinarian_service) non espongono un modo per
rompere solo meta' della transazione - ENTITY_TYPE e' una costante interna,
non un input del chiamante, quindi nessun payload puo' corromperla. Questo
e' di per se' un segnale positivo (nessuna via pubblica per violare
l'invarianza), ma rende impossibile provare il rollback chiamando i service
cosi' come sono. Per dimostrare il comportamento reale, questi test guidano
Repository/AuditRepository esattamente come fa il service (stesso session,
stesso pattern add->flush->record->commit) ma forzano un vincolo NOT NULL
reale sulla riga di audit per simulare un fallimento a meta' transazione."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models.audit_log import AuditLog
from models.client import Client
from repositories.audit_repository import AuditRepository
from repositories.client_repository import ClientRepository


async def test_audit_write_failure_rolls_back_the_domain_write_too(db_session, admin_user):
    repo = ClientRepository(db_session)
    audit = AuditRepository(db_session)

    client = Client(first_name="Prova", last_name="Rollback")
    repo.add(client)
    await db_session.flush()  # assegna l'id, non e' ancora un commit
    client_id = client.id
    assert client_id is not None

    # entity_type e' NOT NULL sul DB reale (models/audit_log.py) - questo
    # simula un fallimento nella meta' "audit" della transazione atomica.
    audit.record(entity_type=None, entity_id=client_id, action="created", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()

    # Ne' il cliente ne' l'audit devono sopravvivere: la scrittura di
    # dominio "committata" con l'audit "non committato" deve essere
    # impossibile, e lo e': un solo db.commit() copre entrambe le insert.
    persisted_client = await db_session.get(Client, client_id)
    assert persisted_client is None

    audit_rows = (
        await db_session.execute(select(AuditLog).where(AuditLog.entity_id == client_id))
    ).scalars().all()
    assert audit_rows == []


async def test_audit_write_failure_during_update_rolls_back_the_field_change(db_session, admin_user):
    from schemas.client import ClientCreate
    from services import client_service

    client = await client_service.create_client(
        db_session, ClientCreate(first_name="Anna", last_name="Bianchi", city="Pisa"), actor_user_id=admin_user.id
    )
    client_id = client.id

    repo = ClientRepository(db_session)
    audit = AuditRepository(db_session)

    persisted = await repo.get_by_id(client_id)
    persisted.city = "Livorno"
    audit.record(entity_type=None, entity_id=client_id, action="field_changed", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()

    # La modifica al campo non deve essere sopravvissuta al rollback: non
    # deve mai poter esistere un city="Livorno" senza la riga di audit che
    # lo documenta.
    reloaded = await db_session.get(Client, client_id)
    assert reloaded.city == "Pisa"
