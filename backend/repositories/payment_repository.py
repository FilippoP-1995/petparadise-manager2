from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.payment import LedgerSection, Payment, PaymentDeletion
from models.practice import PaymentChannel


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, payment_id: int) -> Payment | None:
        return await self._session.get(Payment, payment_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        stmt = select(Payment).where(Payment.idempotency_key == idempotency_key)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id_for_update(self, payment_id: int) -> Payment | None:
        stmt = select(Payment).where(Payment.id == payment_id).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def add(self, payment: Payment) -> None:
        self._session.add(payment)

    async def delete(self, payment: Payment) -> None:
        await self._session.delete(payment)

    async def list_for_practice(self, practice_id: int) -> list[Payment]:
        stmt = select(Payment).where(Payment.practice_id == practice_id).order_by(Payment.movement_date, Payment.id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_active_reversal_for(self, payment_id: int) -> Payment | None:
        """Verifica se un pagamento e' gia' stato stornato - usata per
        impedire il doppio storno (race condition richiesta esplicitamente)."""
        stmt = select(Payment).where(Payment.related_payment_id == payment_id, Payment.movement_type == "Storno")
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def sum_paid_for_practice_channel(self, practice_id: int, channel: PaymentChannel) -> int:
        """FACT V1 (channel_paid_amount/recompute_practice_channel_balances,
        balance_service.py): somma le Entrate positive di quel canale su
        quella pratica, escludendo qualunque riga gia' stornata - stessa
        identica formula, non ricalcolata diversamente."""
        reversed_target_ids = (
            select(Payment.related_payment_id).where(Payment.movement_type == "Storno", Payment.related_payment_id.isnot(None))
        )
        stmt = select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.practice_id == practice_id,
            Payment.channel == channel,
            Payment.ledger_section == LedgerSection.entrata,
            Payment.amount_cents > 0,
            Payment.id.notin_(reversed_target_ids),
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def sum_paid_by_practice_ids(self, practice_ids: list[int]) -> dict[int, int]:
        """Fase 'parita' visiva V2' (Lista Pratiche, colonna Totale pagato):
        stessa identica formula di sum_paid_for_practice_channel (Entrata
        attiva, amount_cents>0, non stornata), qui raggruppata per pratica
        in UNA query invece di N (una per riga) - evita l'N+1 su un
        elenco. Sola lettura: nessuna riga scritta, nessun impatto su
        idempotenza/append-only/riconciliazione per-pratica (che resta
        quella esistente in get_practice_reconciliation, invariata)."""
        if not practice_ids:
            return {}
        reversed_target_ids = select(Payment.related_payment_id).where(
            Payment.movement_type == "Storno", Payment.related_payment_id.isnot(None)
        )
        stmt = (
            select(Payment.practice_id, func.coalesce(func.sum(Payment.amount_cents), 0))
            .where(
                Payment.practice_id.in_(practice_ids),
                Payment.ledger_section == LedgerSection.entrata,
                Payment.amount_cents > 0,
                Payment.id.notin_(reversed_target_ids),
            )
            .group_by(Payment.practice_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {practice_id: total for practice_id, total in rows}

    async def sum_acconto_by_practice_ids(self, practice_ids: list[int]) -> dict[int, int]:
        """Fase 'parita' visiva V2' (Lista Pratiche, colonna 'Acconto' - V1:
        channel_deposit()): stesso filtro attivo di sum_paid_by_practice_ids,
        ristretto a movement_type='Acconto' (V1 mostra qui solo l'acconto,
        mai il saldo). Sola lettura, una query per l'intera pagina."""
        if not practice_ids:
            return {}
        reversed_target_ids = select(Payment.related_payment_id).where(
            Payment.movement_type == "Storno", Payment.related_payment_id.isnot(None)
        )
        stmt = (
            select(Payment.practice_id, func.coalesce(func.sum(Payment.amount_cents), 0))
            .where(
                Payment.practice_id.in_(practice_ids),
                Payment.movement_type == "Acconto",
                Payment.ledger_section == LedgerSection.entrata,
                Payment.amount_cents > 0,
                Payment.id.notin_(reversed_target_ids),
            )
            .group_by(Payment.practice_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {practice_id: total for practice_id, total in rows}


class PaymentDeletionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def add(self, deletion: PaymentDeletion) -> None:
        self._session.add(deletion)

    async def get_by_id(self, deletion_id: int) -> PaymentDeletion | None:
        return await self._session.get(PaymentDeletion, deletion_id)

    async def list_for_payment(self, payment_id: int) -> list[PaymentDeletion]:
        stmt = select(PaymentDeletion).where(PaymentDeletion.payment_id == payment_id).order_by(PaymentDeletion.deleted_at.desc())
        return list((await self._session.execute(stmt)).scalars().all())
