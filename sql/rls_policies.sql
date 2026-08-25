-- =====================================================================
-- WorkforceIQ -- row-level security and grants
--
-- RUN THIS AFTER schema.sql AND AFTER the views. It is a separate file, and
-- it is re-run every time, for one specific reason:
--
--   schema.sql begins with DROP TABLE ... CASCADE. Dropping a table takes its
--   RLS policies with it. So any security applied as a one-off migration is
--   silently destroyed the next time anyone rebuilds the database, and the
--   tables come back publicly readable AND writable with nobody noticing.
--   Security that lives outside the build script is security that lasts until
--   the next rebuild.
--
-- POSTURE
--   This is a public demo dataset -- the open IBM HR sample with synthesised
--   employee names -- so anonymous READ is intentional: it is what lets the
--   hosted dashboard query the database with no server-side secret.
--
--   Anonymous WRITE is not intentional anywhere. Supabase grants the anon and
--   authenticated roles table privileges by default, and RLS is what actually
--   constrains them, so both halves are needed: RLS on with SELECT-only
--   policies, and the write privileges revoked outright. Either alone leaves
--   a hole.
--
-- NOTE: this file is PostgreSQL-specific and is deliberately NOT in sql/views/,
-- so the DuckDB local-validation path (which knows nothing about RLS) skips it.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. RLS on every base table, with a read-only policy
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);

        -- idempotent: drop then recreate, so re-running never errors
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'p_' || t || '_read', t);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO anon, authenticated USING (true)',
            'p_' || t || '_read', t);
    END LOOP;
END $$;


-- ---------------------------------------------------------------------
-- 2. Revoke every write privilege from the public-facing roles
--
-- No INSERT/UPDATE/DELETE policy exists above, so RLS already blocks writes.
-- Revoking as well means a future policy added by mistake cannot open a write
-- path on its own. Defence in depth, and it costs nothing.
-- ---------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON ALL TABLES IN SCHEMA public
    FROM anon, authenticated;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES
    FROM anon, authenticated;


-- ---------------------------------------------------------------------
-- 3. Make every view SECURITY INVOKER
--
-- A Postgres view runs with the privileges of its OWNER by default, which
-- means a view over an RLS-protected table bypasses that table's policies
-- entirely -- the RLS above would be decorative. security_invoker makes the
-- view evaluate under the QUERYING user's permissions instead, so policies
-- apply through the view as well.
--
-- This matters most for vw_attrition_risk_watchlist, which names individual
-- employees alongside a flight-risk score. When per-manager RLS is added in
-- production (see the README), that policy has to hold through the view or it
-- achieves nothing.
--
-- Requires PostgreSQL 15+.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v text;
BEGIN
    FOR v IN
        SELECT table_name FROM information_schema.views WHERE table_schema = 'public'
    LOOP
        EXECUTE format('ALTER VIEW public.%I SET (security_invoker = on)', v);
    END LOOP;
END $$;


-- ---------------------------------------------------------------------
-- 4. Views need an explicit read grant once they are security_invoker
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO anon, authenticated;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon, authenticated;
