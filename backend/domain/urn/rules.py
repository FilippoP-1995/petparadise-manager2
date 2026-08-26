from domain.errors import ValidationDomainError


def ensure_valid_price(price_cents: int) -> None:
    if price_cents < 0:
        raise ValidationDomainError("Il prezzo non puo' essere negativo.")


def ensure_valid_quantity(quantity: int) -> None:
    """FACT V1 (save_urn): la quantita' e' sempre normalizzata a >= 0
    (max(0, int(...))) - un magazzino non puo' avere scorte negative."""
    if quantity < 0:
        raise ValidationDomainError("La quantita' non puo' essere negativa.")
