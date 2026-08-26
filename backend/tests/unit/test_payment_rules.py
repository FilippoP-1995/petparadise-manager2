from unittest.mock import MagicMock

import pytest

from domain.errors import ValidationDomainError
from domain.payment.rules import ensure_not_already_reversed, ensure_nonzero_amount


@pytest.mark.parametrize("amount", [1, -1, 10000, -10000])
def test_nonzero_amount_ok(amount):
    ensure_nonzero_amount(amount)


def test_zero_amount_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_nonzero_amount(0)


def test_not_already_reversed_ok_when_none():
    ensure_not_already_reversed(None)


def test_already_reversed_rejected():
    with pytest.raises(ValidationDomainError):
        ensure_not_already_reversed(MagicMock())
