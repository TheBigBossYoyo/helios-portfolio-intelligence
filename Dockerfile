FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip wheel --no-deps --wheel-dir /dist .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system helios \
    && adduser --system --ingroup helios --home /home/helios helios

COPY --from=builder /dist/*.whl /tmp/dist/
COPY constraints.txt /tmp/constraints.txt

# Installed against the same pins CI verifies, so the image cannot resolve to a different
# dependency set than the one that went green.
RUN python -m pip install /tmp/dist/*.whl -c /tmp/constraints.txt \
    && rm -rf /tmp/dist /tmp/constraints.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config
COPY src ./src
COPY docker/api-entrypoint.sh ./docker/api-entrypoint.sh

RUN mkdir -p /app/data \
    && chmod 755 /app/docker/api-entrypoint.sh \
    && chown -R helios:helios /app /home/helios

USER helios

EXPOSE 8000

CMD ["uvicorn", "helios.app:app", "--host", "0.0.0.0", "--port", "8000"]
