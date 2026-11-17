.PHONY: test test-integration migrate

test:
	uv run pytest -m "not integration" -q

# needs TEST_DATABASE_URL: a throwaway Postgres 18 database, the tests TRUNCATE tables
test-integration:
	uv run pytest -m integration -q

migrate:
	uv run --env-file .env alembic upgrade head

format:
	uv run ruff check --fix .
	uv run ruff format .
