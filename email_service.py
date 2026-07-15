from __future__ import annotations

import os
import socket
import smtplib
import ssl
import sys
import traceback
import unicodedata
from dataclasses import dataclass
from email.message import EmailMessage


REQUIRED_FROM_ADDRESS = "info@petparadisempoli.com"


class EmailConfigurationError(RuntimeError):
    pass


class EmailDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    from_name: str
    from_address: str


def _env_bool(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "si", "sì", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise EmailConfigurationError("SMTP_USE_TLS deve essere true oppure false.")


def smtp_config(environ=None) -> SMTPConfig:
    env = os.environ if environ is None else environ
    required = ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_USE_TLS", "EMAIL_FROM_NAME", "EMAIL_FROM_ADDRESS")
    missing = [name for name in required if not str(env.get(name, "")).strip()]
    if missing:
        raise EmailConfigurationError("Configurazione email incompleta: mancano " + ", ".join(missing) + ".")
    try:
        port = int(str(env["SMTP_PORT"]).strip())
    except (TypeError, ValueError):
        raise EmailConfigurationError("SMTP_PORT non è valido.") from None
    if not 1 <= port <= 65535:
        raise EmailConfigurationError("SMTP_PORT non è valido.")
    host="".join(char for char in str(env["SMTP_HOST"]).strip() if not char.isspace() and unicodedata.category(char)!="Cf")
    if not host:raise EmailConfigurationError("SMTP_HOST non è valido.")
    from_address = str(env["EMAIL_FROM_ADDRESS"]).strip().lower()
    if from_address != REQUIRED_FROM_ADDRESS:
        raise EmailConfigurationError(f"EMAIL_FROM_ADDRESS deve essere {REQUIRED_FROM_ADDRESS}.")
    return SMTPConfig(
        host=host,
        port=port,
        username=str(env["SMTP_USERNAME"]).strip(),
        password=str(env["SMTP_PASSWORD"]),
        use_tls=_env_bool(env["SMTP_USE_TLS"]),
        from_name=str(env["EMAIL_FROM_NAME"]).strip(),
        from_address=from_address,
    )


def _connection_details(config: SMTPConfig):
    if config.port == 465:
        return "smtplib.SMTP_SSL", True, False
    if config.port == 587:
        return "smtplib.SMTP + STARTTLS", False, True
    return ("smtplib.SMTP + STARTTLS", False, True) if config.use_tls else ("smtplib.SMTP", False, False)


def _log_environment_values(env) -> None:
    password = str(env.get("SMTP_PASSWORD", ""))
    print("[SMTP_DEBUG] Variabili d'ambiente lette dal processo Render:", file=sys.stderr, flush=True)
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_USE_TLS", "EMAIL_FROM_ADDRESS", "EMAIL_FROM_NAME"):
        print(f"[SMTP_DEBUG] {name}={str(env.get(name, ''))!r}", file=sys.stderr, flush=True)
    print(f"[SMTP_DEBUG] SMTP_PASSWORD presente={bool(password)} lunghezza={len(password)}", file=sys.stderr, flush=True)


def send_email(recipient: str, subject: str, body: str, environ=None, timeout=20, from_name=None) -> None:
    env = os.environ if environ is None else environ
    raw_host = str(env.get("SMTP_HOST", ""))
    _log_environment_values(env)
    try:
        config = smtp_config(env)
        connection_type,use_ssl,use_starttls = _connection_details(config)
        print(f"[SMTP_DEBUG] host={config.host!r}", file=sys.stderr, flush=True)
        print(f"[SMTP_DEBUG] porta={config.port}", file=sys.stderr, flush=True)
        print(f"[SMTP_DEBUG] username={config.username!r}", file=sys.stderr, flush=True)
        print(f"[SMTP_DEBUG] tipo_connessione={connection_type} SSL={use_ssl} STARTTLS={use_starttls}", file=sys.stderr, flush=True)
        print(f"[SMTP_DEBUG] SMTP_USE_TLS letto={config.use_tls}", file=sys.stderr, flush=True)
        message = EmailMessage()
        display_name = str(from_name or config.from_name).replace("\r", " ").replace("\n", " ").strip()
        message["From"] = f"{display_name} <{config.from_address}>"
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        kwargs={"timeout":timeout}
        if use_ssl:kwargs["context"]=ssl.create_default_context()
        with smtp_class(config.host, config.port, **kwargs) as smtp:
            smtp.ehlo()
            if use_starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(config.username, config.password)
            smtp.send_message(message)
    except Exception as exc:
        if isinstance(exc,socket.gaierror):
            print(f"[SMTP_DEBUG] Risoluzione DNS fallita. SMTP_HOST esatto letto dall'ambiente={raw_host!r}", file=sys.stderr, flush=True)
        print(f"[SMTP_DEBUG] Eccezione completa tipo={type(exc).__module__}.{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        if isinstance(exc,EmailConfigurationError):raise
        if isinstance(exc,smtplib.SMTPAuthenticationError):
            raise EmailDeliveryError("Autenticazione SMTP non riuscita. Verifica utente e password su Render.") from exc
        raise EmailDeliveryError(f"Invio SMTP non riuscito: {type(exc).__name__}: {exc}") from exc
