from __future__ import annotations

import os
import smtplib
import ssl
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
    from_address = str(env["EMAIL_FROM_ADDRESS"]).strip().lower()
    if from_address != REQUIRED_FROM_ADDRESS:
        raise EmailConfigurationError(f"EMAIL_FROM_ADDRESS deve essere {REQUIRED_FROM_ADDRESS}.")
    return SMTPConfig(
        host=str(env["SMTP_HOST"]).strip(),
        port=port,
        username=str(env["SMTP_USERNAME"]).strip(),
        password=str(env["SMTP_PASSWORD"]),
        use_tls=_env_bool(env["SMTP_USE_TLS"]),
        from_name=str(env["EMAIL_FROM_NAME"]).strip(),
        from_address=from_address,
    )


def send_email(recipient: str, subject: str, body: str, environ=None, timeout=20, from_name=None) -> None:
    config = smtp_config(environ)
    message = EmailMessage()
    display_name = str(from_name or config.from_name).replace("\r", " ").replace("\n", " ").strip()
    message["From"] = f"{display_name} <{config.from_address}>"
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(config.host, config.port, timeout=timeout) as smtp:
            smtp.ehlo()
            if config.use_tls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(config.username, config.password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailDeliveryError("Autenticazione SMTP non riuscita. Verifica utente e password su Render.") from exc
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise EmailDeliveryError(f"Invio SMTP non riuscito: {type(exc).__name__}.") from exc
