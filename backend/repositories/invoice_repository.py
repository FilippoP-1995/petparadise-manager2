from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.invoice import Invoice
from models.payment import InvoicePaymentLink, Payment


class InvoiceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, invoice_id: int) -> Invoice | None:
        return await self._session.get(Invoice, invoice_id)

    async def get_by_invoice_number(self, invoice_number: str) -> Invoice | None:
        stmt = select(Invoice).where(Invoice.invoice_number == invoice_number)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def add(self, invoice: Invoice) -> None:
        self._session.add(invoice)

    async def list_all(self, *, q: str | None, practice_id: int | None, limit: int, offset: int) -> list[Invoice]:
        stmt = select(Invoice)
        if practice_id is not None:
            stmt = stmt.where(Invoice.practice_id == practice_id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Invoice.invoice_number.ilike(like), Invoice.practice_number_snapshot.ilike(like)))
        stmt = stmt.order_by(Invoice.created_at.desc(), Invoice.id.desc()).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().all())

    async def paid_cents_for_invoice(self, invoice_id: int) -> int:
        """doc06 Addendum O: 'totale pagato' e' sempre calcolato live dal
        ledger via invoice_payment_links, mai memorizzato. Stessa esatta
        formula di esclusione degli storni gia' verificata nel codice V1
        (recompute_practice_channel_balances/channel_paid_amount): somma
        solo le righe con amount_cents>0 che non hanno un pagamento di
        storno collegato tramite related_payment_id."""
        reversed_target_ids = (
            select(Payment.related_payment_id).where(Payment.movement_type == "Storno", Payment.related_payment_id.isnot(None))
        )
        stmt = (
            select(func.coalesce(func.sum(Payment.amount_cents), 0))
            .select_from(InvoicePaymentLink)
            .join(Payment, Payment.id == InvoicePaymentLink.payment_id)
            .where(
                InvoicePaymentLink.invoice_id == invoice_id,
                Payment.amount_cents > 0,
                Payment.id.notin_(reversed_target_ids),
            )
        )
        return (await self._session.execute(stmt)).scalar_one()
