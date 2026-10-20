"""payment ledger append-only trigger

Revision ID: 693d348defb7
Revises: ab009e85b321
Create Date: 2026-08-29 13:51:59.803058

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '693d348defb7'
down_revision = 'ab009e85b321'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # models/payment.py dichiara che il ledger append-only e' "rinforzato
    # da un trigger DB nella migrazione" (FACT V1: balance_movements ha un
    # trigger BEFORE UPDATE identico, balance_service.py) - ma nessuna
    # migration lo creava davvero: l'append-only era garantito solo a
    # livello applicativo (nessun service esegue mai un UPDATE su
    # payments), senza alcun vincolo a livello database. Un UPDATE SQL
    # diretto (bypassando i service, es. da uno strumento di
    # amministrazione o una futura migrazione dati) non veniva impedito.
    # Questa migration chiude quel gap, mirroring esatto del trigger V1
    # balance_movements_no_update: blocca SOLO UPDATE, mai DELETE (la
    # cancellazione fisica resta un percorso legittimo e distinto, vedi
    # services/payment_service.py::delete_payment + payment_deletions).
    # Eccezione, unica e deliberata: practice_id puo' cambiare (solo verso
    # NULL) perche' Payment.practice_id ha ON DELETE SET NULL - un hard
    # delete legittimo di una pratica (permanent_delete_practice-equivalente
    # V2) fa scattare quell'aggiornamento *dal database stesso*, non
    # dall'applicazione, prima ancora che qualunque service possa
    # intervenire. Bloccarlo indiscriminatamente romperebbe quel percorso
    # gia' testato (test_cross_domain.py) senza aggiungere alcuna
    # protezione reale, dato che non e' un percorso che tocca la sostanza
    # economica della riga. Ogni altra colonna resta protetta.
    op.execute(
        """
        CREATE FUNCTION reject_payment_update() RETURNS trigger AS $$
        BEGIN
          IF NEW.payment_uuid IS DISTINCT FROM OLD.payment_uuid
             OR NEW.practice_number_snapshot IS DISTINCT FROM OLD.practice_number_snapshot
             OR NEW.movement_date IS DISTINCT FROM OLD.movement_date
             OR NEW.channel IS DISTINCT FROM OLD.channel
             OR NEW.ledger_section IS DISTINCT FROM OLD.ledger_section
             OR NEW.movement_type IS DISTINCT FROM OLD.movement_type
             OR NEW.amount_cents IS DISTINCT FROM OLD.amount_cents
             OR NEW.payment_method IS DISTINCT FROM OLD.payment_method
             OR NEW.description IS DISTINCT FROM OLD.description
             OR NEW.related_payment_id IS DISTINCT FROM OLD.related_payment_id
             OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
             OR NEW.collaborator_id IS DISTINCT FROM OLD.collaborator_id
             OR NEW.created_by IS DISTINCT FROM OLD.created_by
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.source IS DISTINCT FROM OLD.source
             OR NEW.metadata_json IS DISTINCT FROM OLD.metadata_json
          THEN
            RAISE EXCEPTION 'payments is append-only: use a reversal (Storno) or delete_payment, never UPDATE the financial substance of a row (only practice_id may be nulled, via ON DELETE SET NULL when the linked practice is hard-deleted)';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payments_no_update
        BEFORE UPDATE ON payments
        FOR EACH ROW EXECUTE FUNCTION reject_payment_update();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_payments_no_update ON payments;")
    op.execute("DROP FUNCTION IF EXISTS reject_payment_update();")
