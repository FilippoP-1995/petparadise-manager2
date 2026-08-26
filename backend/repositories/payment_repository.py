from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.payment import LedgerSection, Payment, PaymentDeletion
from models.practice import PaymentChannel


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, payment_id: int) -> Payment | None:
        return await self._session.get(Payment, payment_id)

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
