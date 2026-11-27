"""Backup indipendente del database SQLite di produzione.

Perche' esiste (vedi docs/v2/00-backup-render-verifica.md): lo snapshot
automatico del Persistent Disk Render e' sconsigliato dalla documentazione
Render stessa per un "database custom" come SQLite, soprattutto senza
journal_mode WAL (gia' il caso di questo progetto) - uno snapshot preso a
meta' di una scrittura puo' catturare il file in uno stato incoerente.

Questo modulo produce invece una copia SEMPRE internamente coerente
(VACUUM INTO, eseguito dentro una singola transazione di lettura SQLite,
non un semplice copy del file), la verifica con PRAGMA integrity_check, e
la carica FUORI dal server applicativo (storage S3-compatibile) cosi' un
problema al disco Render non porta via anche l'unica copia del backup.

Eseguito dentro il processo del servizio web (unico processo con accesso
al Persistent Disk), triggerato dall'endpoint /cron/backup - stesso
pattern gia' usato per il cron WhatsApp (cron_whatsapp.py + whatsapp_cron
in app.py), necessario perche' il job Cron di Render e' un processo
separato senza il disco montato.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


class BackupError(Exception):
    """Errore che ha impedito di completare il backup in modo sicuro."""


@dataclass
class BackupResult:
    ok: bool
    backup_name: str = ""
    size_bytes: int = 0
    duration_ms: int = 0
    deleted_remote: int = 0
    error: str = ""
    steps: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "backup_name": self.backup_name,
            "size_bytes": self.size_bytes,
            "duration_ms": self.duration_ms,
            "deleted_remote": self.deleted_remote,
            "error": self.error,
            "steps": self.steps,
        }


def create_local_backup(db_path: Path, dest_dir: Path, *, timestamp: str | None = None) -> Path:
    """Copia coerente del database via VACUUM INTO (mai un semplice copy del
    file): SQLite esegue VACUUM INTO dentro una propria transazione di sola
    lettura, quindi il file risultante e' sempre uno stato valido del
    database anche se ci sono scritture concorrenti in corso in quel
    momento - a differenza di uno snapshot esterno o di una copia grezza,
    che possono catturare una scrittura a meta'."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = dest_dir / f"pet_paradise_{stamp}.db"
    if backup_path.exists():
        backup_path.unlink()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("VACUUM INTO ?", (str(backup_path),))
    finally:
        conn.close()
    return backup_path


def verify_backup_integrity(backup_path: Path) -> None:
    """Apre la copia appena creata e chiede a SQLite stesso di verificarla.
    Solleva BackupError se la copia non e' un database SQLite valido e
    internamente coerente - non ha senso caricare fuori dal server una
    copia che potrebbe gia' essere corrotta."""
    conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True, timeout=30)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"la copia non e' un file SQLite leggibile: {exc}") from exc
    finally:
        conn.close()
    if not row or row[0] != "ok":
        raise BackupError(f"PRAGMA integrity_check ha fallito sulla copia: {row}")


def compress_backup(backup_path: Path) -> Path:
    gz_path = backup_path.with_suffix(backup_path.suffix + ".gz")
    with open(backup_path, "rb") as source, gzip.open(gz_path, "wb", compresslevel=6) as target:
        shutil.copyfileobj(source, target)
    return gz_path


@dataclass
class RemoteConfig:
    endpoint_url: str
    key_id: str
    application_key: str
    bucket: str
    prefix: str = "ppm-backups/"
    retention_days: int = 30

    @classmethod
    def from_env(cls) -> "RemoteConfig | None":
        endpoint = os.environ.get("BACKUP_S3_ENDPOINT", "").strip()
        key_id = os.environ.get("BACKUP_S3_KEY_ID", "").strip()
        secret = os.environ.get("BACKUP_S3_APPLICATION_KEY", "").strip()
        bucket = os.environ.get("BACKUP_S3_BUCKET", "").strip()
        if not (endpoint and key_id and secret and bucket):
            return None
        retention = os.environ.get("BACKUP_RETENTION_DAYS", "30").strip()
        try:
            retention_days = max(1, int(retention))
        except ValueError:
            retention_days = 30
        return cls(
            endpoint_url=endpoint,
            key_id=key_id,
            application_key=secret,
            bucket=bucket,
            prefix=os.environ.get("BACKUP_S3_PREFIX", "ppm-backups/").strip() or "ppm-backups/",
            retention_days=retention_days,
        )


def _s3_client(config: RemoteConfig):
    # Import qui, non in cima al file: boto3 e' una dipendenza usata SOLO
    # da questo modulo (il resto del progetto usa deliberatamente solo
    # urllib.request, vedi docs/v2/01-architettura-attuale.md) - un
    # import fallito qui non deve impedire al resto dell'app di avviarsi.
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.key_id,
        aws_secret_access_key=config.application_key,
    )


def upload_backup(local_path: Path, config: RemoteConfig) -> str:
    remote_key = f"{config.prefix}{local_path.name}"
    client = _s3_client(config)
    client.upload_file(str(local_path), config.bucket, remote_key)
    return remote_key


def cleanup_old_remote_backups(config: RemoteConfig) -> int:
    """Elimina dal bucket remoto le copie piu' vecchie della retention
    configurata. Non tocca mai la copia locale appena creata (quella la
    gestisce run_database_backup)."""
    client = _s3_client(config)
    cutoff = time.time() - config.retention_days * 86400
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.bucket, Prefix=config.prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"].timestamp() < cutoff:
                client.delete_object(Bucket=config.bucket, Key=obj["Key"])
                deleted += 1
    return deleted


def run_database_backup(db_path: Path, *, tmp_dir: Path | None = None) -> BackupResult:
    """Orchestrazione completa: copia coerente -> verifica -> compressione
    -> upload -> pulizia locale -> retention remota. Non solleva mai
    un'eccezione: ogni fallimento torna in BackupResult.error, cosi'
    l'endpoint HTTP puo' sempre rispondere con un JSON valido (stesso
    principio del resto dell'app: mai un 500 non gestito su un'operazione
    schedulata)."""
    started = time.monotonic()
    steps: list[str] = []
    local_dir = tmp_dir or (db_path.parent / "backup_tmp")
    backup_path: Path | None = None
    gz_path: Path | None = None
    try:
        config = RemoteConfig.from_env()
        if config is None:
            raise BackupError(
                "Configurazione backup incompleta: servono le variabili d'ambiente "
                "BACKUP_S3_ENDPOINT, BACKUP_S3_KEY_ID, BACKUP_S3_APPLICATION_KEY, BACKUP_S3_BUCKET"
            )
        backup_path = create_local_backup(db_path, local_dir)
        steps.append(f"copia locale creata: {backup_path.name}")
        verify_backup_integrity(backup_path)
        steps.append("integrity_check: ok")
        gz_path = compress_backup(backup_path)
        steps.append(f"compresso: {gz_path.name} ({gz_path.stat().st_size} byte)")
        remote_key = upload_backup(gz_path, config)
        steps.append(f"caricato su storage esterno: {remote_key}")
        deleted = cleanup_old_remote_backups(config)
        if deleted:
            steps.append(f"rimosse {deleted} copie remote piu' vecchie di {config.retention_days} giorni")
        size_bytes = gz_path.stat().st_size
        name = gz_path.name
        return BackupResult(
            ok=True,
            backup_name=name,
            size_bytes=size_bytes,
            duration_ms=int((time.monotonic() - started) * 1000),
            deleted_remote=deleted,
            steps=steps,
        )
    except Exception as exc:  # noqa: BLE001 - un job schedulato non deve mai propagare
        return BackupResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
            steps=steps,
        )
    finally:
        # La copia locale (sia .db sia .db.gz) non deve restare sul disco
        # applicativo: e' solo un passaggio intermedio verso lo storage
        # esterno, non un secondo posto dove il backup "vive".
        for path in (backup_path, gz_path):
            if path and path.exists():
                path.unlink()


# --- Ricerca nei backup (richiesta esplicita dell'utente, 27/11/2026) ------
# Una pratica creata il 17/11 non si trovava piu' da nessuna parte (ne' in
# archivio ne' nel Cestino): per capire se era stata salvata e poi eliminata
# definitivamente serve guardare dentro un backup. Tutto avviene sul server
# (i dati non escono mai da Render) e in SOLA LETTURA: il backup scaricato
# viene aperto con mode=ro e il database reale non viene toccato.

LOOKUP_DIRNAME = "backup_lookup"
PRACTICE_LOOKUP_FIELDS = (
    "owner_first_name", "owner_last_name", "owner_company", "owner_phone", "owner_phone_2",
    "owner_email", "animal_name", "practice_number", "species",
)


def list_remote_backups(config: RemoteConfig) -> list[dict]:
    """Backup presenti nello storage esterno, dal piu' recente."""
    client = _s3_client(config)
    items = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.bucket, Prefix=config.prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith((".db.gz", ".db")):
                continue
            items.append({"key": key, "name": key[len(config.prefix):] if key.startswith(config.prefix) else key,
                          "size": int(obj.get("Size") or 0), "modified": obj["LastModified"]})
    items.sort(key=lambda item: item["modified"], reverse=True)
    return items


def fetch_backup_for_lookup(config: RemoteConfig, key: str, data_dir: Path) -> Path:
    """Scarica (una sola volta) e decomprime un backup in una cartella di
    lavoro sul disco del server; tiene solo l'ultimo backup consultato."""
    if not key.startswith(config.prefix) or "/" in key[len(config.prefix):] or ".." in key:
        raise BackupError("Backup non valido.")
    work = data_dir / LOOKUP_DIRNAME
    work.mkdir(parents=True, exist_ok=True)
    name = key[len(config.prefix):]
    db_name = name[:-3] if name.endswith(".gz") else name
    target = work / db_name
    if target.exists():
        return target
    for old in work.iterdir():
        try:
            old.unlink()
        except OSError:
            pass
    download = work / (name + ".part")
    _s3_client(config).download_file(config.bucket, key, str(download))
    if name.endswith(".gz"):
        with gzip.open(download, "rb") as source, open(target, "wb") as dest:
            shutil.copyfileobj(source, dest)
        download.unlink()
    else:
        download.rename(target)
    verify_backup_integrity(target)
    return target


def search_backup_practices(backup_db: Path, term: str, limit: int = 50) -> list[dict]:
    """Pratiche del backup che contengono il testo cercato (nome, cognome,
    ragione sociale, telefono, email, animale, numero pratica, specie)."""
    term = (term or "").strip()
    if len(term) < 2:
        return []
    conn = sqlite3.connect(f"file:{backup_db}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(practices)")}
        fields = [name for name in PRACTICE_LOOKUP_FIELDS if name in columns]
        words = [word for word in term.lower().split() if word]
        where = " AND ".join("(" + " OR ".join(f"lower(COALESCE({name},'')) LIKE ?" for name in fields) + ")" for _ in words)
        args = [f"%{word}%" for word in words for _ in fields]
        rows = conn.execute(f"SELECT * FROM practices WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?", (*args, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def backup_practice_detail(backup_db: Path, practice_id: int) -> dict | None:
    """Tutti i dati di una pratica nel backup, piu' voci, pagamenti e storico."""
    conn = sqlite3.connect(f"file:{backup_db}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM practices WHERE id=?", (practice_id,)).fetchone()
        if not row:
            return None
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(sql):
            return [dict(r) for r in conn.execute(sql, (practice_id,))]
        return {
            "practice": dict(row),
            "items": rows("SELECT * FROM practice_items WHERE practice_id=? ORDER BY id") if "practice_items" in tables else [],
            "payments": rows("SELECT * FROM payment_movements WHERE practice_id=? ORDER BY id") if "payment_movements" in tables else [],
            "history": rows("SELECT * FROM practice_history WHERE practice_id=? ORDER BY id") if "practice_history" in tables else [],
        }
    finally:
        conn.close()
