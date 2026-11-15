.PHONY: test migrate

test:
	uv run pytest -m "not integration" -q

migrate:
	uv run --env-file .env alembic upgrade head
