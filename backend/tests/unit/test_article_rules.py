from types import SimpleNamespace

import pytest

from domain.article.rules import ensure_orderable
from domain.errors import NotFoundError, ValidationDomainError


def test_active_article_is_orderable():
    ensure_orderable(SimpleNamespace(active=True))


def test_missing_article_raises_not_found():
    with pytest.raises(NotFoundError):
        ensure_orderable(None)


def test_inactive_article_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_orderable(SimpleNamespace(active=False))
