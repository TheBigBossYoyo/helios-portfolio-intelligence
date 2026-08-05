# Helios

Milestone 2 backend sync/runtime foundation.

## Commands

- `helios positions`
- `helios sync [--force-metadata]`
- `helios quality`
- `helios-worker`

## API

- `GET /health`
- `GET /api/v1/t212/positions`
- `POST /api/v1/portfolio/sync?force_metadata=false`
- `GET /api/v1/portfolio/data-quality`

## Safety

- Trading 212 access is read-only and GET-only.
- No trades or account mutations are performed.
- OpenFIGI use is optional and isolated from Trading 212 credentials.

## Credentials

- Keep demo and live Trading 212 credentials separate.
- Do not place secret values in tracked files.
- Local bindings stay on localhost; users often override to `3001/8001` via env.

## Instrument overrides

Use `config/instrument_overrides.yaml` for verified manual mappings only.

- Prefer snake_case fields: `yahoo_ticker`, `preferred_exchange`, `quote_currency`
- Existing camelCase aliases are still accepted
- Never guess Yahoo/LSE mappings

## Docker Compose

`compose.yaml` mounts `/app/config`, keeps API and worker on shared backend image, and
makes the worker wait for a healthy API to reduce first-start migration contention.
The worker still migrates on startup so standalone worker runs remain safe.
