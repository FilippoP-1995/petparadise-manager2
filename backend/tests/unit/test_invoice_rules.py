import pytest

from domain.errors import ValidationDomainError
from domain.invoice.rules import classify_payment_status, ensure_invoice_channel_valid, ensure_positive_total
from models.practice import PaymentChannel


def test_w_and_d_channels_are_valid_for_invoices():
    ensure_invoice_channel_valid(PaymentChannel.W)
    ensure_invoice_channel_valid(PaymentChannel.D)


def test_collaboratori_channel_rejected_for_invoices():
    """doc06: la fatturazione al collaboratore e' un processo interno
    separato (Addendum F), mai un documento fiscale in questa tabella."""
    with pytest.raises(ValidationDomainError):
        ensure_invoice_channel_valid(PaymentChannel.collaboratori)


def test_positive_total_ok():
    ensure_positive_total(100)


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_total_rejected(value):
    with pytest.raises(ValidationDomainError):
        ensure_positive_total(value)


def test_classify_non_pagata():
    assert classify_payment_status(10000, 0) == "non_pagata"


def test_classify_parziale():
    assert classify_payment_status(10000, 4000) == "parziale"


def test_classify_pagata_exact_boundary():
    assert classify_payment_status(10000, 10000) == "pagata"


def test_classify_sovrapagata():
    assert classify_payment_status(10000, 10001) == "sovrapagata"


def test_classify_example_from_doc06():
    """doc06 Addendum O, esempio esplicito: fattura da 340, 120 pagati ->
    parziale, mai fatto collassare su un'unica cifra."""
    assert classify_payment_status(34000, 12000) == "parziale"
