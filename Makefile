DOCKER_COMPOSE ?= docker compose
NPM ?= npm

.PHONY: dev preflight install test test-web test-e2e lint lint-web typecheck typecheck-web \
        build-web check migrate down backup desktop desktop-shortcut

dev:
	$(DOCKER_COMPOSE) up --build

# Fails loudly when the virtualenv is missing a declared dependency, instead of letting the
# first symptom be fifteen pytest collection errors.
preflight:
	helios-preflight

install:
	python -m pip install -e ".[dev,desktop]" -c constraints.txt

test: preflight
	pytest

test-web:
	cd web && $(NPM) test

test-e2e:
	cd web && $(NPM) run test:e2e

lint:
	ruff check .

lint-web:
	cd web && $(NPM) run lint

typecheck:
	mypy

typecheck-web:
	cd web && $(NPM) run typecheck

build-web:
	cd web && $(NPM) run build

# Everything CI runs, in one target.
check: preflight lint typecheck test lint-web typecheck-web test-web

migrate:
	helios-migrate

down:
	$(DOCKER_COMPOSE) down --remove-orphans

# Online-backup copy of the live SQLite database (safe under WAL, safe while the stack is up).
# Override the destination or retention with `make backup ARGS="--dest /path --keep 30"`.
backup:
	helios backup $(ARGS)

# Helios without Docker: API, worker and dashboard supervised from the tray, in an app window.
desktop:
	helios-desktop

desktop-shortcut:
	helios-desktop --install-shortcut
