import pytest

from domain.company_location.rules import ensure_name_valid
from domain.errors import ValidationDomainError


def test_valid_name_does_not_raise():
    ensure_name_valid("Livorno")


@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_name_rejected(name):
    with pytest.raises(ValidationDomainError):
        ensure_name_valid(name)
