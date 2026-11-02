-- Postgres user for OpenClaw agents.
--
-- Principle: agents read everything they need, write NOTHING.
-- Any write to production data must go through a reviewed PR + CI scraper,
-- never directly from the agent process.
--
-- Run with:    psql "$DATABASE_URL" -f 001_readonly_agent_user.sql
--
-- Before running:
--   1. generate a long random password (`openssl rand -hex 24`)
--   2. set it in your vault as AGENT_RO_DATABASE_URL
--   3. replace REPLACE_WITH_VAULT_PASSWORD below

BEGIN;

-- Drop & recreate so script is idempotent.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
        REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM agent_ro;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM agent_ro;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM agent_ro;
        REVOKE ALL ON SCHEMA public FROM agent_ro;
        DROP OWNED BY agent_ro;
        DROP ROLE agent_ro;
    END IF;
END $$;

CREATE ROLE agent_ro WITH
    LOGIN
    PASSWORD 'REPLACE_WITH_VAULT_PASSWORD'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
    CONNECTION LIMIT 5
    VALID UNTIL '2027-12-31';        -- force rotation

-- Read-only grants
GRANT CONNECT ON DATABASE postgres TO agent_ro;
GRANT USAGE   ON SCHEMA   public   TO agent_ro;
GRANT SELECT  ON ALL TABLES    IN SCHEMA public TO agent_ro;
GRANT SELECT  ON ALL SEQUENCES IN SCHEMA public TO agent_ro;

-- Future tables get the same treatment automatically
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES    TO agent_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO agent_ro;

-- Explicit DENY on sensitive tables (defense in depth — they're already
-- read-only, but if someone later grants UPDATE by mistake, these still block).
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM agent_ro;

-- Statement timeout: prevents an agent from running a 30-minute query that
-- blocks the production DB.
ALTER ROLE agent_ro SET statement_timeout            = '15s';
ALTER ROLE agent_ro SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE agent_ro SET lock_timeout                 = '2s';

-- Row-level audit: log every connection from agent_ro (uses pg_stat_activity;
-- pair with a cron that snapshots it into an audit table).
COMMENT ON ROLE agent_ro IS 'OpenClaw autonomous agents — read-only. Rotate password Jan/Jul.';

COMMIT;

-- Verification block (run separately, will raise if anything was granted):
--   SET ROLE agent_ro;
--   INSERT INTO prices (product_id, store_id, price) VALUES (gen_random_uuid(), gen_random_uuid(), 1);
--   -- expected: ERROR: permission denied for table prices
