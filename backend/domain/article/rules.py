from domain.errors import NotFoundError, ValidationDomainError


def ensure_orderable(article) -> None:
    if article is None:
        raise NotFoundError("Prodotto non trovato")
    if not article.active:
        raise ValidationDomainError("Questo prodotto non e' piu' disponibile per la richiesta d'ordine.")
