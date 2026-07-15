"""Render Cron entry point: trigger the web service that owns the SQLite disk."""

import json
import os
import sys
import urllib.error
import urllib.request


def main():
    url = os.environ.get("WHATSAPP_CRON_URL", "").strip()
    secret = os.environ.get("WHATSAPP_CRON_SECRET", "").strip()
    if not url or not secret:
        print("[WHATSAPP_CRON_CLIENT] WHATSAPP_CRON_URL o WHATSAPP_CRON_SECRET mancante", flush=True)
        return 2
    request = urllib.request.Request(
        url,
        data=b"",
        headers={"X-Cron-Secret": secret, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read().decode("utf-8", "replace")
            payload = json.loads(body) if body else {}
            print(
                f"[WHATSAPP_CRON_CLIENT] HTTP {response.status} processed={payload.get('processed', 0)} ok={payload.get('ok', False)}",
                flush=True,
            )
            return 0 if response.status == 200 and payload.get("ok") else 1
    except urllib.error.HTTPError as exc:
        print(f"[WHATSAPP_CRON_CLIENT] HTTP {exc.code}: endpoint non completato", flush=True)
    except Exception as exc:
        print(f"[WHATSAPP_CRON_CLIENT] {type(exc).__name__}: {exc}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
