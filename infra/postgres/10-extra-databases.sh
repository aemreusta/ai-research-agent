#!/bin/sh
# Extra databases on the shared server. Langfuse gets its own, so its migrations can never
# touch the application schema (architecture v0.6 §2). Runs once, on first initialisation.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
SELECT 'CREATE DATABASE langfuse OWNER "$POSTGRES_USER"'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec
SQL

# A throwaway database for `docker compose --profile test run --rm tests`.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
SELECT 'CREATE DATABASE research_test OWNER "$POSTGRES_USER"'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'research_test')\gexec
SQL
