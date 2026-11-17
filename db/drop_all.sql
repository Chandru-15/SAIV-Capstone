-- ============================================================================
-- SAIV — destructive reset. Drops every SAIV table and all of its data.
-- Use for local development only:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/drop_all.sql -f db/schema.sql
--
-- Covers more than schema.sql creates. schema.sql bootstraps the eight core
-- tables; the backend's startup creates the staff-assignment, grouping and
-- password-reset tables on top of them. A reset has to remove whatever is
-- actually in the database, so every table the running system produces is
-- listed here. DROP ... IF EXISTS makes the extras harmless on a database
-- that never had them.
-- ============================================================================

BEGIN;

-- Dropped before the tables they reference.
DROP TABLE IF EXISTS browser_key_challenges CASCADE;
DROP TABLE IF EXISTS password_reset_codes CASCADE;
DROP TABLE IF EXISTS session_groups       CASCADE;
DROP TABLE IF EXISTS group_members        CASCADE;
DROP TABLE IF EXISTS course_groups        CASCADE;
DROP TABLE IF EXISTS course_settings      CASCADE;
DROP TABLE IF EXISTS session_tas          CASCADE;
DROP TABLE IF EXISTS course_instructors   CASCADE;

DROP TABLE IF EXISTS audit_logs   CASCADE;
DROP TABLE IF EXISTS risk_signals CASCADE;
DROP TABLE IF EXISTS checkins     CASCADE;
DROP TABLE IF EXISTS devices      CASCADE;
DROP TABLE IF EXISTS sessions     CASCADE;
DROP TABLE IF EXISTS enrollments  CASCADE;
DROP TABLE IF EXISTS courses      CASCADE;
DROP TABLE IF EXISTS users        CASCADE;

DROP FUNCTION IF EXISTS set_updated_at() CASCADE;

COMMIT;
