.PHONY: test test-integration migrate format run-portal-backend run-cases run-puller run-normalizer

list: ## Show all available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

test: ## Run the test suite
	uv run pytest -m "not integration" -q

# needs TEST_DATABASE_URL: a throwaway Postgres 18 database, the tests TRUNCATE tables
test-integration: ## Run the integration test suite
	uv run pytest -m integration -q

migrate: ## Run database migration scripts
	uv run --env-file .env alembic upgrade head

format: ## Format python scripts with ruff
	uv run ruff check --fix .
	uv run ruff format .

run-portal-backend: ## Run portal backend
	uv run --env-file .env uvicorn api.investigation:app --port 8121 --reload

run-puller: ## Pull events from sentrix-edr
	uv run --env-file .env python -m pipeline.puller.main

run-normalizer: ## Normalizes pulled events
	uv run --env-file .env python -m pipeline.normalizer.main

run-cases: ## Create cases from pulled events
	uv run --env-file .env python -m pipeline.cases.main
