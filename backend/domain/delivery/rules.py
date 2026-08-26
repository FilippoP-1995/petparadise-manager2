"""doc06 Addendum C/P: stesso principio 'niente viene mai dedotto' gia'
applicato al Ritiro, qui per la Riconsegna. doc14 §3: nessuna macchina a
stati per la Riconsegna - solo regole di consistenza dei campi e la
riconciliazione del pagamento preliminare al momento del collegamento a
una pratica."""

from domain.errors import ValidationDomainError
from models.calendar_event import DeliveryType


def ensure_delivery_fields_consistent(
    delivery_type: DeliveryType,
    *,
    delivery_veterinarian_id: int | None,
    delivery_zone_id: int | None,
    delivery_location_id: int | None,
    delivery_address: str | None,
) -> None:
    if delivery_type == DeliveryType.ambulatorio and delivery_veterinarian_id is None:
        raise ValidationDomainError("delivery_type='ambulatorio' richiede delivery_veterinarian_id.")
    if delivery_type == DeliveryType.domicilio and delivery_zone_id is None:
        raise ValidationDomainError("delivery_type='domicilio' richiede delivery_zone_id.")
    if delivery_type == DeliveryType.sede_aziendale and delivery_location_id is None:
        raise ValidationDomainError("delivery_type='sede_aziendale' richiede delivery_location_id.")
    if delivery_type == DeliveryType.altro and not (delivery_address or "").strip():
        raise ValidationDomainError("delivery_type='altro' richiede delivery_address.")


def preliminary_payment_diverges(preliminary_amount_cents: int | None, practice_effective_total_cents: int) -> bool:
    """doc06 Addendum P: 'se preliminary_payment_amount differisce dal
    totale calcolato sulla pratica..., l'operatore vede un avviso esplicito
    prima di confermare il collegamento - mai un collegamento silenzioso
    che fa sparire la discrepanza'."""
    if preliminary_amount_cents is None:
        return False
    return preliminary_amount_cents != practice_effective_total_cents
