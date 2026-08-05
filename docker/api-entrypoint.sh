#!/bin/sh
set -eu

if [ "$#" -eq 0 ]; then
  set -- uvicorn helios.app:app --host 0.0.0.0 --port 8000
fi

python -m alembic upgrade head
exec "$@"
