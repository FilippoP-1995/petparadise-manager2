from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, require_role
from database import get_session
from domain.errors import NotFoundError, ValidationDomainError
from models.user import User, UserRole
from repositories.payment_repository import PaymentDeletionRepository, PaymentRepository
from schemas.payment import (
    DeletePaymentRequest,
    PaymentCreate,
    PaymentDeletionRead,
    PaymentRead,
    PracticeReconciliationRead,
    ReversePaymentRequest,
)
from services import payment_service

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _domain_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, ValidationDomainError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("", response_model=list[PaymentRead])
async def list_payments(
    practice_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    return await PaymentRepository(db).list_for_practice(practice_id)


@router.get("/practice/{practice_id}/riconciliazione", response_model=PracticeReconciliationRead)
async def get_practice_reconciliation(
    practice_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)
):
    try:
        return await payment_service.get_practice_reconciliation(db, practice_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{payment_id}", response_model=PaymentRead)
async def get_payment(payment_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    payment = await PaymentRepository(db).get_by_id(payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pagamento non trovato")
    return payment


@router.post("", response_model=PaymentRead, status_code=status.HTTP_201_CREATED)
async def register_payment(payload: PaymentCreate, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    try:
        return await payment_service.register_payment(db, payload, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{payment_id}/storna", response_model=PaymentRead)
async def reverse_payment(
    payment_id: int, payload: ReversePaymentRequest, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        return await payment_service.reverse_payment(db, payment_id, payload.reason, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.post("/{payment_id}/elimina", response_model=PaymentDeletionRead)
async def delete_payment(
    payment_id: int,
    payload: DeletePaymentRequest,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.admin)),
):
    """Release hardening: cancellazione fisica di un pagamento - solo Admin
    (stessa barriera di correct_invoice_total/correct_practice_state,
    decisione esplicita del gate di rilascio)."""
    try:
        return await payment_service.delete_payment(
            db, payment_id, payload.deletion_kind, payload.reason, actor_user_id=user.id
        )
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc


@router.get("/deletions/{deletion_id}", response_model=PaymentDeletionRead)
async def get_payment_deletion(deletion_id: int, db: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    deletion = await PaymentDeletionRepository(db).get_by_id(deletion_id)
    if deletion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cancellazione non trovata")
    return deletion


@router.post("/deletions/{deletion_id}/ripristina", response_model=PaymentRead)
async def restore_payment_deletion(
    deletion_id: int, db: AsyncSession = Depends(get_session), user: User = Depends(require_role(UserRole.admin))
):
    """Release hardening: ripristino di un pagamento cancellato - solo Admin,
    stessa barriera dell'operazione di cancellazione che ripristina."""
    try:
        return await payment_service.restore_payment_deletion(db, deletion_id, actor_user_id=user.id)
    except (ValidationDomainError, NotFoundError) as exc:
        raise _domain_error_to_http(exc) from exc
