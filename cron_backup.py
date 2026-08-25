"""Render Cron entry point: sveglia il servizio web (unico processo con il
Persistent Disk montato) per eseguire il backup indipendente del database.
Stesso schema di cron_whatsapp.py."""

import json
import os
import sys
import urllib.error
import urllib.request


def main():
    url = os.environ.get("BACKUP_CRON_URL", "").strip()
    secret = os.environ.get("BACKUP_CRON_SECRET", "").strip()
    if not url or not secret:
        print("[BACKUP_CRON_CLIENT] BACKUP_CRON_URL o BACKUP_CRON_SECRET mancante", flush=True)
        return 2
    request = urllib.request.Request(
        url,
        data=b"",
        headers={"X-Cron-Secret": secret, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8", "replace")
            payload = json.loads(body) if body else {}
            print(
                f"[BACKUP_CRON_CLIENT] HTTP {response.status} ok={payload.get('ok', False)} "
                f"backup_name={payload.get('backup_name', '')} size_bytes={payload.get('size_bytes', 0)}",
                flush=True,
            )
            return 0 if response.status == 200 and payload.get("ok") else 1
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        print(f"[BACKUP_CRON_CLIENT] HTTP {exc.code}: {body}", flush=True)
    except Exception as exc:
        print(f"[BACKUP_CRON_CLIENT] {type(exc).__name__}: {exc}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
