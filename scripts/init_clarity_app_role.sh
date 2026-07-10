#!/usr/bin/env bash
# Create the least-privilege runtime role on first Postgres initialization.
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${CLARITY_APP_PASSWORD:?CLARITY_APP_PASSWORD is required}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$CLARITY_APP_PASSWORD" --set=db_name="$POSTGRES_DB" <<'SQL'
SELECT format(
  'CREATE ROLE clarity_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
  :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clarity_app') \gexec

ALTER ROLE clarity_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE clarity_app PASSWORD :'app_password';
GRANT CONNECT ON DATABASE :"db_name" TO clarity_app;
GRANT USAGE ON SCHEMA public TO clarity_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO clarity_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO clarity_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO clarity_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO clarity_app;
SQL
