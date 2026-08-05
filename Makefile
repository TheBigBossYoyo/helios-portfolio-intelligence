DOCKER_COMPOSE ?= docker compose

.PHONY: dev test lint typecheck migrate down

dev:
	$(DOCKER_COMPOSE) up --build

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy

migrate:
	helios-migrate

down:
	$(DOCKER_COMPOSE) down --remove-orphans
