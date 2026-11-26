# Daelys. Targets that touch the DB run inside the api container by default (no host Python needed).
# CI sets RUN= (empty) to run on the host against service containers.
SHELL := /bin/bash
ifneq (,$(wildcard .env))
include .env
export
endif

COMPOSE ?= docker compose
RUN ?= $(COMPOSE) run --rm -T api
PY_DIRS := core api agents rules workers connectors evaluation
# The bench writes evaluation/results/ and the README's bench section, so in a container those are mounted from
# the host (as the host user, so the files stay yours). With RUN= (CI), it runs on the host directly.
BENCH_MOUNTS := -v "$(CURDIR)/evaluation/results:/app/evaluation/results" -v "$(CURDIR)/README.md:/app/README.md"
BENCH_RUN ?= $(if $(RUN),$(COMPOSE) run --rm -T --user $$(id -u):$$(id -g) $(BENCH_MOUNTS) api,)

.PHONY: up down build migrate seed test cov lint fmt audit replay logs install web bench

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

migrate:
	$(RUN) alembic upgrade head

seed:
	$(RUN) python -m core.seed

test:
	$(RUN) pytest -q

cov:
	$(RUN) pytest -q --cov --cov-report=term-missing

replay:
	$(RUN) python -m connectors.replay $(or $(SCENARIO),tiffin_box) $(or $(WEEK),week2) --speed $(or $(SPEED),60)

lint:
	ruff check .
	ruff format --check .
	mypy $(PY_DIRS) tests

fmt:
	ruff check --fix .
	ruff format .

audit:
	pip-audit --strict --progress-spinner off -r requirements.lock
	gitleaks detect --no-banner --redact

install:
	python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'

logs:
	$(COMPOSE) logs -f api worker

web:
	@echo "Web app: http://localhost:$(or $(API_HOST_PORT),8000)/  (served by the api service; no build step)"

bench:
	$(BENCH_RUN) python -m evaluation.run --suite $(or $(SUITE),all)
