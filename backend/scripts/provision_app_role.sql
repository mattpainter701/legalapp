-- =============================================================================
-- provision_app_role.sql
-- -----------------------------------------------------------------------------
-- Creates the least-privilege application role `clarity_app` that the runtime
-- connects as so that Postgres Row Level Security (RLS) is ACTUALLY ENFORCED.
--
-- WHY THIS EXISTS
--   Today the app connects as a SUPERUSER, which bypasses RLS entirely; the
--   only isolation is hand-written `.where(tenant_id == ...)` clauses. RLS is
--   only meaningful when the connecting role is NOT a superuser, NOT the table
--   owner, and does NOT have BYPASSRLS. This role satisfies all three, so the
--   `FORCE ROW LEVEL SECURITY` + `tenant_isolation_*` policies in the
--   migrations become real, structural tenant isolation.
--
-- OPERATIONAL MODEL
--   * Run this script ONCE, as the database OWNER / a superuser, against the
--     target database.
--   * MIGRATIONS continue to run as the OWNER (so DDL, policy creation, and
--     FORCE RLS all work; FORCE does not apply to the owner during migrations).
--   * RUNTIME: point the application's DATABASE_URL at `clarity_app` (NOT the
--     owner / superuser). That is what makes FORCE RLS bite at request time.
--   * Replace the placeholders below before running:
--       - 'CHANGE_ME'   -> a strong generated password (and ROTATE it
--                          regularly; store it in your secrets manager).
--       - :db_name       -> your actual database name (or hardcode it).
--   * This script is idempotent: re-running it is safe.
-- =============================================================================

-- 1) Create the role if it does not already exist. NOSUPERUSER + NOBYPASSRLS
--    are the load-bearing attributes: without them RLS would be bypassed.
DO
$$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clarity_app') THEN
        CREATE ROLE clarity_app
            LOGIN
            PASSWORD 'CHANGE_ME'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOBYPASSRLS;
    END IF;
END
$$;

-- Defensive: ensure attributes are correct even if the role pre-existed with
-- different settings. (Does not touch the password.)
ALTER ROLE clarity_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS LOGIN;

-- 2) Connection + schema usage. Replace :db_name with your database name.
GRANT CONNECT ON DATABASE :"db_name" TO clarity_app;
GRANT USAGE ON SCHEMA public TO clarity_app;

-- 3) DML on all current tables/sequences. (No DDL, no TRUNCATE, no ownership.)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO clarity_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO clarity_app;

-- 4) Default privileges so FUTURE objects created by the owner (e.g. tables and
--    sequences added by later migrations) are automatically granted to the app
--    role without re-running step 3. NOTE: default privileges only apply to
--    objects created by the role that runs this ALTER (the owner) -- run this
--    script as the same owner your migrations run as.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO clarity_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO clarity_app;

-- =============================================================================
-- POST-RUN CHECKLIST
--   [ ] Set the runtime DATABASE_URL to connect as clarity_app.
--   [ ] Confirm clarity_app is NOT a member of the owner/superuser role.
--   [ ] Verify: SELECT rolsuper, rolbypassrls FROM pg_roles
--                 WHERE rolname='clarity_app';  -- both must be `f`.
--   [ ] Rotate 'CHANGE_ME' and store the real secret securely.
-- =============================================================================
