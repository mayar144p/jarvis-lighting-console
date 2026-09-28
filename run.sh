#!/usr/bin/env sh
# Jarvis - Mac/Linux launcher (run.bat is the Windows one).
cd "$(dirname "$0")" || exit 1

if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env
    echo "Created .env from .env.example - edit it to add your LLM_API_KEY."
fi

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "Python 3 is required: https://www.python.org/downloads/" >&2
    exit 1
fi

PORT=$(sed -n 's/^PORT=\([0-9]*\).*/\1/p' .env 2>/dev/null | head -n1)
URL="http://localhost:${PORT:-8787}/"
( sleep 1
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1
  fi ) &

exec "$PY" app/main.py
