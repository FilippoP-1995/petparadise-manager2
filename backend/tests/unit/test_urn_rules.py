import pytest

from domain.errors import ValidationDomainError
from domain.urn.rules import ensure_valid_price, ensure_valid_quantity


def test_zero_and_positive_price_ok():
    ensure_valid_price(0)
    ensure_valid_price(15000)


def test_negative_price_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_valid_price(-1)


def test_zero_and_positive_quantity_ok():
    ensure_valid_quantity(0)
    ensure_valid_quantity(5)


def test_negative_quantity_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_valid_quantity(-1)
