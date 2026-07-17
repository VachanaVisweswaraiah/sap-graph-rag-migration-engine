.PHONY: setup up down load db-check test test-unit test-integration eval api lint fmt clean

setup:            ## install deps + git hooks
	uv sync --all-extras --dev
	uv run pre-commit install

up:               ## start FalkorDB (Browser at http://localhost:3000)
	docker compose up -d

down:             ## stop FalkorDB
	docker compose down

load:             ## build the graph via the Python loader
	uv run kgme-load

db-check:         ## readiness check against the running FalkorDB
	uv run kgme db check --deep

test-unit:        ## fast tests, no Docker
	uv run pytest tests/unit

test-integration: ## testcontainers tests (needs Docker)
	uv run pytest tests/integration -m integration

test:             ## full suite with coverage gate (CI-equivalent)
	uv run pytest tests/unit tests/integration --cov=kgme --cov-fail-under=70

eval:             ## golden-question regression net (manual only: real API key + loaded graph)
	uv run pytest tests/eval -m eval

api:              ## run the FastAPI server (needs FalkorDB up + graph loaded)
	uv run uvicorn kgme.api.main:app --reload --port 8000

lint:             ## ruff (lint + format check) + mypy(strict) -- matches CI exactly
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt:              ## auto-format
	uv run ruff format .
	uv run ruff check --fix .

clean:            ## remove tooling caches
	rm -rf .pytest_cache .ruff_cache .mypy_cache
