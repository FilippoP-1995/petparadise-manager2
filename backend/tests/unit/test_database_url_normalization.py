from config import _normalize_database_url


def test_leaves_already_correct_url_untouched():
    url = "postgresql+asyncpg://user:pass@host:5432/db"
    assert _normalize_database_url(url) == url


def test_adds_the_asyncpg_driver_to_a_plain_postgresql_url():
    assert (
        _normalize_database_url("postgresql://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )


def test_adds_the_asyncpg_driver_to_the_short_postgres_scheme():
    assert (
        _normalize_database_url("postgres://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )


def test_does_not_alter_credentials_host_or_query_string():
    # Password con caratteri speciali e query string: solo lo schema
    # iniziale deve cambiare, il resto della stringa deve restare
    # identico byte per byte.
    url = "postgresql://ppm_v2:p@ss/w:0rd!@db.internal:5432/ppm_v2?sslmode=require"
    normalized = _normalize_database_url(url)
    assert normalized == "postgresql+asyncpg://ppm_v2:p@ss/w:0rd!@db.internal:5432/ppm_v2?sslmode=require"


def test_unrecognized_scheme_is_left_as_is():
    # Nessuna assunzione forzata su schemi non previsti - meglio un errore
    # di connessione chiaro a valle che una riscrittura silenziosa errata.
    url = "sqlite:///local.db"
    assert _normalize_database_url(url) == url
