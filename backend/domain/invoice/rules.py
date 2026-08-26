from domain.errors import ValidationDomainError
from models.practice import PaymentChannel

# doc06 '1. Fatture: fonte unica' - channel invoices e' 'W' | 'D', mai
# 'Collaboratori': la fatturazione al collaboratore e' un flag di processo
# interno separato (Addendum F, practices.collaborator_billing_status),
# mai un documento fiscale rappresentato da questa tabella.
INVOICE_CHANNELS = {PaymentChannel.W, PaymentChannel.D}


def ensure_invoice_channel_valid(channel: PaymentChannel) -> None:
    if channel not in INVOICE_CHANNELS:
        raise ValidationDomainError(
            f"Canale fattura non valido: '{channel.value}'. Ammessi solo W o D "
            "(la fatturazione al collaboratore e' un processo interno separato, non un documento fiscale)."
        )


def ensure_positive_total(total_amount_cents: int) -> None:
    if total_amount_cents <= 0:
        raise ValidationDomainError("L'importo della fattura deve essere maggiore di zero.")


def classify_payment_status(total_amount_cents: int, paid_cents: int) -> str:
    """doc06 Addendum O: stato calcolato, mai memorizzato - le due cifre
    (fattura, pagato) non collassano mai l'una sull'altra. Il sovrapagamento
    non viene mai corretto automaticamente: e' uno stato visibile che
    richiede intervento umano esplicito (storno o correzione fattura)."""
    if paid_cents <= 0:
        return "non_pagata"
    if paid_cents < total_amount_cents:
        return "parziale"
    if paid_cents == total_amount_cents:
        return "pagata"
    return "sovrapagata"
