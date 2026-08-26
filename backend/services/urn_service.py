from sqlalchemy.ext.asyncio import AsyncSession

from domain.errors import NotFoundError
from domain.urn.rules import ensure_valid_price, ensure_valid_quantity
from models.urn import Urn, UrnMovement
from repositories.audit_repository import AuditRepository
from repositories.urn_repository import UrnCatalogRepository, UrnMovementRepository
from schemas.urn import UrnCreate, UrnUpdate

ENTITY_TYPE = "urn"


async def create_urn(db: AsyncSession, data: UrnCreate, *, actor_user_id: int) -> Urn:
    """FACT V1 (save_urn, ramo creazione): codice interno auto-generato per
    categoria, movimento 'Creazione / carico iniziale' registrato per la
    quantita' di partenza - stessa transazione, un solo commit."""
    ensure_valid_price(data.price_cents)
    ensure_valid_quantity(data.quantity)

    catalog = UrnCatalogRepository(db)
    movements = UrnMovementRepository(db)
    audit = AuditRepository(db)

    internal_code = await catalog.next_internal_code(data.category)

    urn = Urn(
        **data.model_dump(),
        internal_code=internal_code,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    catalog.add(urn)
    await db.flush()

    if data.quantity != 0:
        movements.add(
            UrnMovement(
                urn_id=urn.id,
                practice_id=None,
                user_id=actor_user_id,
                movement_type="Creazione / carico iniziale",
                quantity_delta=data.quantity,
                old_quantity=0,
                new_quantity=data.quantity,
                note="Nuovo articolo",
            )
        )

    audit.record(entity_type=ENTITY_TYPE, entity_id=urn.id, action="created", user_id=actor_user_id)

    await db.commit()
    await db.refresh(urn)
    return urn


async def update_urn(db: AsyncSession, urn_id: int, data: UrnUpdate, *, actor_user_id: int) -> Urn:
    """FACT V1 (save_urn, ramo modifica): il codice interno NON cambia
    modificando la scheda; se la quantita' cambia rispetto al valore
    precedente viene registrato un movimento 'Rettifica manuale' - stessa
    logica esatta, non un nuovo endpoint di 'aggiusta scorta' separato
    (in V1 non esiste, la modifica quantita' passa sempre dalla scheda)."""
    ensure_valid_price(data.price_cents)
    ensure_valid_quantity(data.quantity)

    catalog = UrnCatalogRepository(db)
    movements = UrnMovementRepository(db)
    audit = AuditRepository(db)

    urn = await catalog.get_by_id(urn_id)
    if urn is None:
        raise NotFoundError(f"Urna {urn_id} non trovata")

    old_quantity = urn.quantity
    changes = data.model_dump()
    for field_name, new_value in changes.items():
        old_value = getattr(urn, field_name)
        if old_value != new_value:
            audit.record(
                entity_type=ENTITY_TYPE,
                entity_id=urn.id,
                action="field_changed",
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                user_id=actor_user_id,
            )
        setattr(urn, field_name, new_value)
    urn.updated_by = actor_user_id

    if data.quantity != old_quantity:
        movements.add(
            UrnMovement(
                urn_id=urn.id,
                practice_id=None,
                user_id=actor_user_id,
                movement_type="Rettifica manuale",
                quantity_delta=data.quantity - old_quantity,
                old_quantity=old_quantity,
                new_quantity=data.quantity,
                note="Modifica scheda urna",
            )
        )

    await db.commit()
    await db.refresh(urn)
    return urn


async def deactivate_urn(db: AsyncSession, urn_id: int, *, actor_user_id: int) -> Urn:
    """FACT V1 (delete_urn): 'Rimozione dal catalogo' - active=0, mai una
    DELETE reale, movimento a delta zero solo per tracciarne lo storico."""
    catalog = UrnCatalogRepository(db)
    movements = UrnMovementRepository(db)
    audit = AuditRepository(db)

    urn = await catalog.get_by_id(urn_id)
    if urn is None or not urn.active:
        raise NotFoundError(f"Urna {urn_id} non trovata")

    urn.active = False
    urn.updated_by = actor_user_id
    movements.add(
        UrnMovement(
            urn_id=urn.id,
            practice_id=None,
            user_id=actor_user_id,
            movement_type="Rimozione dal catalogo",
            quantity_delta=0,
            old_quantity=urn.quantity,
            new_quantity=urn.quantity,
            note="Articolo disattivato",
        )
    )
    audit.record(entity_type=ENTITY_TYPE, entity_id=urn.id, action="deactivated", user_id=actor_user_id)

    await db.commit()
    await db.refresh(urn)
    return urn
