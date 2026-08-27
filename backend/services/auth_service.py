from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.security import verify_password
from auth.sessions import create_session
from domain.errors import RateLimitedError, ValidationDomainError
from models.session import Session
from models.user import User
from repositories.login_attempt_repository import LoginAttemptRepository

# Release hardening - rate limit su /api/auth/login, documentato qui prima
# dell'implementazione (nessuna dipendenza esterna, nessuna nuova
# infrastruttura: solo Postgres/SQLAlchemy, gia' presenti):
#
# - Chiave di limitazione: lo username COSI' COME INVIATO, normalizzato
#   (trim + lowercase) - non l'IP (un'unica rete/NAT aziendale non deve
#   poter bloccare tutti gli utenti dietro di essa - vincolo esplicito
#   contro il contatore globale), non lo user_id (l'utente potrebbe non
#   esistere). Stessa chiave sia per utente esistente sia inesistente: il
#   comportamento del rate limit non deve rivelare quale dei due e' il
#   caso, altrimenti diventa un canale per enumerare gli username validi.
# - Finestra temporale: 15 minuti, scorrevole (non un blocco a tempo fisso
#   separato - il limite si allenta naturalmente man mano che i tentativi
#   piu' vecchi escono dalla finestra).
# - Soglia: 5 tentativi FALLITI entro la finestra.
# - Durata del blocco: implicita nella finestra scorrevole - una volta
#   raggiunta la soglia, ogni nuovo tentativo resta bloccato finche' il
#   conteggio nella finestra torna sotto soglia (il tentativo piu' vecchio
#   esce dalla finestra). Nessun countdown esplicito memorizzato, nessun
#   lockout permanente dell'account.
# - Risposta HTTP: 429 (RateLimitedError -> mappato dalla route), messaggio
#   generico, nessuna indicazione di username esistente/inesistente.
# - Dopo un login riuscito: il contatore per quella chiave viene azzerato
#   immediatamente (le righe di fallimento precedenti vengono cancellate).
# - Con username inesistente: stesso identico trattamento di uno esistente
#   con password sbagliata (stessa chiave, stesso conteggio, stesso 429).
# - Dopo logout: nessuna relazione - il rate limit riguarda solo i
#   tentativi di login, non e' mai azzerato ne' influenzato dal logout
#   (che presuppone gia' un login riuscito, che a sua volta ha gia'
#   azzerato il contatore).
LOGIN_RATE_LIMIT_WINDOW = timedelta(minutes=15)
LOGIN_RATE_LIMIT_THRESHOLD = 5


def _normalize_username_key(username: str) -> str:
    return username.strip().lower()


async def login(db: AsyncSession, *, username: str, password: str, ip: str | None, user_agent: str | None) -> Session:
    username_key = _normalize_username_key(username)
    attempts = LoginAttemptRepository(db)

    since = datetime.now(timezone.utc) - LOGIN_RATE_LIMIT_WINDOW
    recent_failures = await attempts.count_recent_failures(username_key, since=since)
    if recent_failures >= LOGIN_RATE_LIMIT_THRESHOLD:
        raise RateLimitedError("Troppi tentativi falliti. Riprova tra qualche minuto.")

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    # Stesso messaggio generico sia per utente inesistente sia per password
    # errata (mai rivelare quale dei due e' sbagliato).
    if user is None or not user.active or not verify_password(password, user.password_hash):
        attempts.record_failure(username_key)
        await db.commit()
        raise ValidationDomainError("Credenziali non valide")

    await attempts.clear(username_key)
    session = await create_session(db, user_id=user.id, ip=ip, user_agent=user_agent)
    await db.commit()
    return session
