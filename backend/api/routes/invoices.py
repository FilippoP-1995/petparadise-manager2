from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, require_role
from database import get_session
from domain.errors import NotFoundError, ValidationDomainError
from models.user import User, UserRole
from repositories.invoice_repository import InvoiceRepository
from schemas.invoice import CorrectInvoiceTotalRequest, InvoiceCreate, InvoiceReconciliationRead, InvoiceRead
from schemas.payment import LinkPaymentToInvoiceRequest
from services import invoice_service, payment_service

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


@router.get("", response_model=list[InvoiceRead])
async def list_invoices(
    q: str | None = Query(default=None, description="Ricerca per numero fattura o numero pratica"),
    practice_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    return await InvoiceRepository(db).list_all(q=q, practice_id=practice_id, limit=limit, offset=offset)


@router.get("/{invoice_id}", response_model=InvoiceRead)
async def get_invoice(invoice_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    invoice = await InvoiceRepository(db).get_by_id(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fattura non trovata")
    return invoice


@router.get("/{invoice_id}/riconciliazione", response_model=InvoiceReconciliationRead)
async def get_invoice_reconciliation(
    invoice_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)
):
    try:
        return await invoice_service.get_reconciliation(db, invoice_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
async def create_invoice(payload: InvoiceCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await invoice_service.create_invoice(db, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{invoice_id}/correggi-totale", response_model=InvoiceRead)
async def correct_invoice_total(
    invoice_id: int,
    payload: CorrectInvoiceTotalRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    """doc06 Addendum R - correzione eccezionale: SOLO Admin, motivo
    obbligatorio, azione dedicata (mai un PUT/PATCH generico)."""
    try:
        return await invoice_service.correct_invoice_total(db, invoice_id, payload, actor_user_id=user.id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{invoice_id}/collega-pagamento", response_model=InvoiceReconciliationRead)
async def link_payment(
    invoice_id: int,
    payload: LinkPaymentToInvoiceRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        await payment_service.link_payment_to_invoice(db, invoice_id, payload.payment_id, actor_user_id=user.id)
        return await invoice_service.get_reconciliation(db, invoice_id)
    except ValidationDomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
