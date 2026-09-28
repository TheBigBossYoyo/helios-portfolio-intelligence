#!/bin/sh
set -eu

if [ "$#" -eq 0 ]; then
  set -- uvicorn helios.app:app --host 0.0.0.0 --port 8000
fi

# A container built against a drifted dependency set should say so once, here, rather than
# fail deep inside the first request.
helios-preflight
helios-migrate
exec "$@"
