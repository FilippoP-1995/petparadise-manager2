#!/bin/bash
# Avvio automatico delle sessioni Claude Code nel cloud.
# Prepara l'ambiente di sviluppo V1: Python 3.12 (come runtime.txt in produzione),
# dipendenze di requirements.txt in .venv e fuso orario Europe/Rome (come Render),
# cosi' i test che dipendono dall'ora girano come in produzione.
# Non tocca mai dati di produzione: crea solo .venv (ignorata da git).
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
VENV="$CLAUDE_PROJECT_DIR/.venv"

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
  rm -rf "$VENV"
  if command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv "$VENV"
  else
    uv venv --python 3.12 --seed "$VENV"
  fi
fi

"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r requirements.txt

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$VENV\""
    echo "export PATH=\"$VENV/bin:\$PATH\""
    echo "export TZ=Europe/Rome"
  } >> "$CLAUDE_ENV_FILE"
fi
