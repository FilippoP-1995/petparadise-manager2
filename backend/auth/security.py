"""Hashing password: stesso algoritmo/parametri gia' validati in V1
(doc04 audit: 'PBKDF2-HMAC-SHA256, 210.000 iterazioni, salt casuale 16
byte, verifica a tempo costante - implementazione corretta, algoritmo
adeguato'). Riuso deliberato, non reinvenzione: solo libreria standard,
nessuna dipendenza esterna nuova."""

import hashlib
import hmac
import os

_ITERATIONS = 210_000
_ALGORITHM = "sha256"
_SALT_BYTES = 16


def hash_password(plain_password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(_ALGORITHM, plain_password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_str, salt_hex, derived_hex = password_hash.split("$")
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(derived_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac(algorithm, plain_password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)
