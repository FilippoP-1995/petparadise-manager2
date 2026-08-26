from domain.errors import ValidationDomainError


def ensure_nonzero_amount(amount_cents: int) -> None:
    """Stesso vincolo del CHECK a livello DB (ck_payments_amount_cents_nonzero)
    - un movimento di importo zero non rappresenta nulla di reale."""
    if amount_cents == 0:
        raise ValidationDomainError("L'importo del movimento non puo' essere zero.")


def ensure_not_already_reversed(existing_reversal) -> None:
    """Previene il doppio storno dello stesso pagamento - la race condition
    esplicitamente richiesta: due richieste concorrenti di storno sullo
    stesso pagamento devono produrre un solo storno reale, mai due."""
    if existing_reversal is not None:
        raise ValidationDomainError("Questo pagamento e' gia' stato stornato.")
