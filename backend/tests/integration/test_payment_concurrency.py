"""Test di concorrenza REALE (non solo verifica statica del codice) per
payment_service.reverse_payment - la race condition esplicitamente
richiesta: due richieste di storno simultanee sullo stesso pagamento.

A differenza degli altri test di questo progetto, qui NON si usa la
fixture db_session (una singola transazione con SAVEPOINT, mai davvero
committata) perche' due sessioni realmente indipendenti servono per
dimostrare che il lock SELECT...FOR UPDATE in reverse_payment serializza
per davvero due connessioni Postgres distinte, non solo due chiamate
sequenziali nello stesso test. Setup e cleanup usano commit reali sul
database di test, pulizia esplicita a fine test."""

import asyncio
import uuid
from datetime import date

from auth.security import hash_password
from database import async_session_factory
from models.client import Client
from models.company_location import CompanyLocation
from models.payment import LedgerSection, Payment
from models.practice import PaymentChannel, Practice, PracticeStatus
from models.user import User, UserRole
from schemas.payment import PaymentCreate
from services import payment_service


async def test_concurrent_reversal_requests_produce_exactly_one_storno():
    tag = uuid.uuid4().hex[:8]
    async with async_session_factory() as setup:
        user = User(
            username=f"concurrency-{tag}",
            password_hash=hash_password("test-password"),
            display_name="Concurrency Test",
            role=UserRole.admin,
            active=True,
        )
        location = CompanyLocation(name=f"Sede concorrenza {tag}")
        client = Client(first_name="Test", last_name="Concorrenza")
        setup.add_all([user, location, client])
        await setup.flush()

        practice = Practice(
            practice_number=f"CONC-{tag}",
            status=PracticeStatus.ritirato,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            destination_branch_id=location.id,
            client_id=client.id,
            created_by=user.id,
        )
        setup.add(practice)
        await setup.flush()

        original = Payment(
            practice_id=practice.id,
            practice_number_snapshot=practice.practice_number,
            movement_date=date(2026, 1, 1),
            channel=PaymentChannel.W,
            ledger_section=LedgerSection.entrata,
            movement_type="Acconto",
            amount_cents=10000,
            idempotency_key=f"conc-{tag}",
            created_by=user.id,
        )
        setup.add(original)
        await setup.flush()
        await setup.commit()

        payment_id = original.id
        user_id = user.id
        practice_id = practice.id
        client_id = client.id
        location_id = location.id

    try:
        async def _attempt():
            async with async_session_factory() as session:
                try:
                    result = await payment_service.reverse_payment(
                        session, payment_id, "tentativo concorrente", actor_user_id=user_id
                    )
                    return result
                except Exception as exc:  # noqa: BLE001 - vogliamo catturare qualunque esito per confrontarlo
                    return exc

        results = await asyncio.gather(_attempt(), _attempt())

        successes = [r for r in results if isinstance(r, Payment)]
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(successes) == 1, "esattamente una delle due richieste concorrenti di storno deve riuscire"
        assert len(failures) == 1, "l'altra deve fallire in modo esplicito (gia' stornato), non riuscire silenziosamente anch'essa"

        async with async_session_factory() as verify:
            from sqlalchemy import select

            stmt = select(Payment).where(Payment.related_payment_id == payment_id, Payment.movement_type == "Storno")
            stornos = (await verify.execute(stmt)).scalars().all()
            assert len(stornos) == 1, "deve esistere esattamente un solo storno reale nel database, mai due"
    finally:
        async with async_session_factory() as cleanup:
            from sqlalchemy import delete

            from models.audit_log import AuditLog

            await cleanup.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
            await cleanup.execute(delete(Payment).where(Payment.practice_id == practice_id))
            await cleanup.execute(delete(Practice).where(Practice.id == practice_id))
            await cleanup.execute(delete(Client).where(Client.id == client_id))
            await cleanup.execute(delete(CompanyLocation).where(CompanyLocation.id == location_id))
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()


async def test_concurrent_registration_same_idempotency_key_produces_exactly_one_payment():
    """Analisi Fase 10G/213: register_payment generava una idempotency_key
    casuale ad ogni chiamata, quindi nessun controllo impediva a due
    richieste concorrenti (stesso doppio-click, stesso retry di rete) di
    creare due pagamenti distinti per lo stesso incasso reale - lo stesso
    sintomo della pratica 213 (820 invece di 410 attivi), qui riprodotto
    con due connessioni Postgres realmente indipendenti, non due chiamate
    sequenziali nello stesso test (che non eserciterebbero la race reale
    sul vincolo UNIQUE di idempotency_key)."""
    tag = uuid.uuid4().hex[:8]
    async with async_session_factory() as setup:
        user = User(
            username=f"idem-{tag}",
            password_hash=hash_password("test-password"),
            display_name="Idempotency Test",
            role=UserRole.admin,
            active=True,
        )
        location = CompanyLocation(name=f"Sede idem {tag}")
        client = Client(first_name="Test", last_name="Idempotenza")
        setup.add_all([user, location, client])
        await setup.flush()

        practice = Practice(
            practice_number=f"IDEM-{tag}",
            status=PracticeStatus.ritirato,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            destination_branch_id=location.id,
            client_id=client.id,
            created_by=user.id,
        )
        setup.add(practice)
        await setup.flush()
        await setup.commit()

        user_id = user.id
        practice_id = practice.id
        client_id = client.id
        location_id = location.id

    try:
        shared_key = f"idem-key-{tag}"

        async def _attempt():
            async with async_session_factory() as session:
                data = PaymentCreate(
                    practice_id=practice_id, movement_date=date(2026, 1, 1), channel=PaymentChannel.W,
                    ledger_section=LedgerSection.entrata, movement_type="Incasso completo", amount_cents=41000,
                    idempotency_key=shared_key,
                )
                return await payment_service.register_payment(session, data, actor_user_id=user_id)

        results = await asyncio.gather(_attempt(), _attempt())
        assert results[0].id == results[1].id, "due richieste concorrenti con la stessa idempotency_key devono risolversi allo STESSO pagamento"

        async with async_session_factory() as verify:
            from sqlalchemy import select

            stmt = select(Payment).where(Payment.practice_id == practice_id, Payment.movement_type == "Incasso completo")
            rows = (await verify.execute(stmt)).scalars().all()
            assert len(rows) == 1, f"deve esistere esattamente un solo pagamento reale, trovati: {[(r.id, r.amount_cents) for r in rows]}"
            assert rows[0].amount_cents == 41000
    finally:
        async with async_session_factory() as cleanup:
            from sqlalchemy import delete

            from models.audit_log import AuditLog

            await cleanup.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
            await cleanup.execute(delete(Payment).where(Payment.practice_id == practice_id))
            await cleanup.execute(delete(Practice).where(Practice.id == practice_id))
            await cleanup.execute(delete(Client).where(Client.id == client_id))
            await cleanup.execute(delete(CompanyLocation).where(CompanyLocation.id == location_id))
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()
