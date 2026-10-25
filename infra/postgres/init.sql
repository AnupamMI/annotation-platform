-- Runs once, when the database volume is first created.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- Restricted schema: real identities (e.g. patient names) mapped to pseudonymous IDs.
-- Only the app's admin paths touch this; the LLM role never gets access.
CREATE SCHEMA IF NOT EXISTS restricted;

-- Read-only role for the "Ask" feature (question -> SQL). It is granted SELECT on
-- curated views only, later, once those views exist (phase 4).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ask_readonly') THEN
        CREATE ROLE ask_readonly NOLOGIN;
    END IF;
END
$$;
