from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalize_database_url(value: str) -> str:
    """Alcuni provider (Render incluso) espongono DATABASE_URL con lo
    schema postgresql:// o postgres://, senza driver esplicito -
    SQLAlchemy con asyncpg richiede pero' postgresql+asyncpg://. Qui si
    sostituisce SOLO il prefisso di schema (i primi caratteri della
    stringa): utente, password, host, porta, nome database e query string
    restano byte-per-byte invariati, qualunque carattere contengano - non
    e' una trasformazione generica della stringa, solo dello schema."""
    if value.startswith("postgresql+asyncpg://"):
        return value
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value[len("postgresql://"):]
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value[len("postgres://"):]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    session_ttl_days: int = 30
    environment: str = "development"

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        return _normalize_database_url(value)


settings = Settings()
