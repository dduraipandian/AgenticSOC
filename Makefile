export database_url

.PHONY: test db-init

test:
	uv run pytest -m "not integration" -q

# Apply the schema to any Postgres server using an exported database_url.
# Usage: make db-init (requires the psql client; does not load .env).
db-init:
	@: "$${database_url:?Set database_url to the target Postgres connection URL}"
	@psql --dbname="$$database_url" -X -v ON_ERROR_STOP=1 --single-transaction \
		-f infra/postgres/001_schemas.sql
