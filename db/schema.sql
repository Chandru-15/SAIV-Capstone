-- ============================================================================
-- SAIV — Secure Attendance & Identity Verification
-- PostgreSQL schema (source of truth: docs/recommended_design/DATABASE-SCHEMA.md)
--
-- Target: PostgreSQL 15 (see docker-compose.yml)
-- Safe to run repeatedly: every statement is IF NOT EXISTS / OR REPLACE.
--
-- Run it:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/schema.sql
--   docker compose exec -T postgres psql -U saiv -d saiv < db/schema.sql
--
-- Conventions chosen here (and why they matter to the backend):
--   * Primary keys are VARCHAR(36) holding UUID strings, matching the
--     SQLAlchemy models in DATABASE-SCHEMA.md (String(36)), not native UUID.
--   * Timestamps are TIMESTAMP WITHOUT TIME ZONE and always hold UTC. This
--     keeps naive Python datetimes (datetime.utcnow()) working unchanged.
--   * Enumerated columns are VARCHAR + CHECK rather than native PG ENUM types,
--     so they work whether the ORM column is String or sa.Enum, and so adding
--     a value is a plain ALTER rather than a type migration. Values are
--     lowercase — if you use Python enums, persist the VALUE, not the NAME
--     (sa.Enum(..., values_callable=lambda e: [m.value for m in e])).
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Shared trigger: keep updated_at honest even if a writer forgets to set it.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = (NOW() AT TIME ZONE 'utc');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ===========================================================================
-- users
-- Accounts for students, instructors, TAs and admins.
-- PRIVACY: face_embedding_hash is a SHA-256 hex digest. Never store raw
-- images or embeddings in this table.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS users (
    id                      VARCHAR(36)  PRIMARY KEY,
    email                   VARCHAR(255) NOT NULL UNIQUE,
    full_name               VARCHAR(255) NOT NULL,
    hashed_password         VARCHAR(255) NOT NULL,           -- bcrypt, cost >= 10
    role                    VARCHAR(20)  NOT NULL,
    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Incremented on logout. Every JWT carries the value it was minted with,
    -- so bumping it invalidates that user's tokens everywhere at once --
    -- access and refresh, on every device. Without it a stateless JWT stays
    -- valid until it expires and "log out" means nothing on the server.
    token_version           INTEGER      NOT NULL DEFAULT 0,

    -- Consent tracking (required before camera / location data is used)
    camera_consent          BOOLEAN      NOT NULL DEFAULT FALSE,
    geolocation_consent     BOOLEAN      NOT NULL DEFAULT FALSE,

    -- Face enrolment: hash only, never the embedding itself
    face_embedding_hash     VARCHAR(64),
    face_enrolled           BOOLEAN      NOT NULL DEFAULT FALSE,

    created_at              TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at              TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    last_login_at           TIMESTAMP,
    scheduled_deletion_at   TIMESTAMP,                       -- PII retention: +30 days

    CONSTRAINT ck_users_role
        CHECK (role IN ('student', 'instructor', 'ta', 'admin')),
    CONSTRAINT ck_users_face_hash_len
        CHECK (face_embedding_hash IS NULL OR char_length(face_embedding_hash) = 64)
);

-- CREATE TABLE IF NOT EXISTS is a no-op on a database created before
-- token_version existed, so add the column explicitly as well. Both
-- statements are idempotent: the `migrate` service re-runs this file on every
-- `docker compose up`.
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS browser_verification_required BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_users_email      ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_role       ON users (role);
CREATE INDEX IF NOT EXISTS ix_users_is_active  ON users (is_active);
CREATE INDEX IF NOT EXISTS ix_users_scheduled_deletion_at
    ON users (scheduled_deletion_at) WHERE scheduled_deletion_at IS NOT NULL;

DROP TRIGGER IF EXISTS tg_users_updated_at ON users;
CREATE TRIGGER tg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ===========================================================================
-- courses
-- Course definition plus the default verification policy for its sessions.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS courses (
    id                          VARCHAR(36)  PRIMARY KEY,
    code                        VARCHAR(20)  NOT NULL UNIQUE,   -- e.g. 'CS6101'
    name                        VARCHAR(255) NOT NULL,
    description                 TEXT,
    semester                    VARCHAR(20)  NOT NULL,          -- e.g. 'AY2024-25 Sem 1'
    is_active                   BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Default venue / geofence, overridable per session
    venue_latitude              DOUBLE PRECISION,
    venue_longitude             DOUBLE PRECISION,
    venue_name                  VARCHAR(255),
    geofence_radius_meters      DOUBLE PRECISION NOT NULL DEFAULT 100.0,

    -- Default verification policy
    require_face_recognition    BOOLEAN      NOT NULL DEFAULT FALSE,
    require_device_binding      BOOLEAN      NOT NULL DEFAULT TRUE,
    risk_threshold              DOUBLE PRECISION NOT NULL DEFAULT 0.5,

    created_at                  TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at                  TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),

    CONSTRAINT ck_courses_latitude
        CHECK (venue_latitude IS NULL OR venue_latitude BETWEEN -90 AND 90),
    CONSTRAINT ck_courses_longitude
        CHECK (venue_longitude IS NULL OR venue_longitude BETWEEN -180 AND 180),
    CONSTRAINT ck_courses_geofence_radius
        CHECK (geofence_radius_meters > 0),
    CONSTRAINT ck_courses_risk_threshold
        CHECK (risk_threshold BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS ix_courses_code      ON courses (code);
CREATE INDEX IF NOT EXISTS ix_courses_semester  ON courses (semester);
CREATE INDEX IF NOT EXISTS ix_courses_is_active ON courses (is_active);

DROP TRIGGER IF EXISTS tg_courses_updated_at ON courses;
CREATE TRIGGER tg_courses_updated_at
    BEFORE UPDATE ON courses
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ===========================================================================
-- enrollments
-- Student <-> course membership. One row per pair, reactivated on re-enrol.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS enrollments (
    id            VARCHAR(36) PRIMARY KEY,
    student_id    VARCHAR(36) NOT NULL REFERENCES users (id)   ON DELETE CASCADE,
    course_id     VARCHAR(36) NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    enrolled_at   TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    dropped_at    TIMESTAMP,

    CONSTRAINT uq_enrollments_student_course UNIQUE (student_id, course_id)
);

CREATE INDEX IF NOT EXISTS ix_enrollments_student_id ON enrollments (student_id);
CREATE INDEX IF NOT EXISTS ix_enrollments_course_id  ON enrollments (course_id);
CREATE INDEX IF NOT EXISTS ix_enrollments_is_active  ON enrollments (is_active);


-- ===========================================================================
-- sessions
-- A single attendance occasion (lecture, tutorial, lab, exam).
-- Venue / radius / threshold columns are NULL-able overrides: fall back to the
-- parent course when NULL.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS sessions (
    id                      VARCHAR(36)  PRIMARY KEY,
    course_id               VARCHAR(36)  NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
    instructor_id           VARCHAR(36)  REFERENCES users (id) ON DELETE SET NULL,
    name                    VARCHAR(255) NOT NULL,                     -- e.g. 'Lecture 5'
    session_type            VARCHAR(50)  NOT NULL DEFAULT 'lecture',
    description             TEXT,

    scheduled_start         TIMESTAMP    NOT NULL,
    scheduled_end           TIMESTAMP    NOT NULL,
    checkin_opens_at        TIMESTAMP    NOT NULL,
    checkin_closes_at       TIMESTAMP    NOT NULL,

    status                  VARCHAR(20)  NOT NULL DEFAULT 'scheduled',
    actual_start            TIMESTAMP,
    actual_end              TIMESTAMP,

    -- Per-session venue override
    venue_latitude          DOUBLE PRECISION,
    venue_longitude         DOUBLE PRECISION,
    venue_name              VARCHAR(255),
    geofence_radius_meters  DOUBLE PRECISION,

    -- Per-session verification override
    require_liveness_check  BOOLEAN      NOT NULL DEFAULT TRUE,
    require_face_match      BOOLEAN      NOT NULL DEFAULT FALSE,
    risk_threshold          DOUBLE PRECISION,

    -- Rotating QR check-in code
    qr_code_secret          VARCHAR(64),
    qr_code_expires_at      TIMESTAMP,

    created_at              TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at              TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),

    CONSTRAINT ck_sessions_status
        CHECK (status IN ('scheduled', 'active', 'closed', 'cancelled')),
    CONSTRAINT ck_sessions_type
        CHECK (session_type IN ('lecture', 'tutorial', 'lab', 'exam')),
    CONSTRAINT ck_sessions_schedule_order
        CHECK (scheduled_end > scheduled_start),
    CONSTRAINT ck_sessions_checkin_window_order
        CHECK (checkin_closes_at > checkin_opens_at),
    CONSTRAINT ck_sessions_latitude
        CHECK (venue_latitude IS NULL OR venue_latitude BETWEEN -90 AND 90),
    CONSTRAINT ck_sessions_longitude
        CHECK (venue_longitude IS NULL OR venue_longitude BETWEEN -180 AND 180),
    CONSTRAINT ck_sessions_geofence_radius
        CHECK (geofence_radius_meters IS NULL OR geofence_radius_meters > 0),
    CONSTRAINT ck_sessions_risk_threshold
        CHECK (risk_threshold IS NULL OR risk_threshold BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS ix_sessions_course_id       ON sessions (course_id);
CREATE INDEX IF NOT EXISTS ix_sessions_instructor_id   ON sessions (instructor_id);
CREATE INDEX IF NOT EXISTS ix_sessions_status          ON sessions (status);
CREATE INDEX IF NOT EXISTS ix_sessions_scheduled_start ON sessions (scheduled_start);
CREATE INDEX IF NOT EXISTS ix_sessions_checkin_window  ON sessions (checkin_opens_at, checkin_closes_at);

DROP TRIGGER IF EXISTS tg_sessions_updated_at ON sessions;
CREATE TRIGGER tg_sessions_updated_at
    BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ===========================================================================
-- devices
-- Registered client devices, keyed by a hash of the device identifier.
-- public_key is used to verify signed check-in payloads (device binding).
-- ===========================================================================
CREATE TABLE IF NOT EXISTS devices (
    id                      VARCHAR(36)  PRIMARY KEY,
    user_id                 VARCHAR(36)  NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    device_fingerprint      VARCHAR(64)  NOT NULL UNIQUE,   -- SHA-256 of device id
    device_name             VARCHAR(255),
    platform                VARCHAR(50),
    browser                 VARCHAR(100),
    os_version              VARCHAR(50),
    app_version             VARCHAR(50),

    -- Device-bound signing key
    public_key              TEXT         NOT NULL,          -- verified browser keys use base64url DER SPKI
    public_key_created_at   TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    public_key_expires_at   TIMESTAMP,                      -- for key rotation

    -- Platform attestation (SafetyNet / Play Integrity / App Attest)
    attestation_passed      BOOLEAN      NOT NULL DEFAULT FALSE,
    last_attestation_at     TIMESTAMP,
    attestation_token       TEXT,                           -- store encrypted

    -- Trust posture
    is_trusted              BOOLEAN      NOT NULL DEFAULT FALSE,
    trust_score             VARCHAR(20)  NOT NULL DEFAULT 'low',
    is_emulator             BOOLEAN      NOT NULL DEFAULT FALSE,
    is_rooted_jailbroken    BOOLEAN      NOT NULL DEFAULT FALSE,

    first_seen_at           TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    last_seen_at            TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    total_checkins          INTEGER      NOT NULL DEFAULT 0,

    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,
    revoked_at              TIMESTAMP,
    revocation_reason       TEXT,

    CONSTRAINT ck_devices_platform
        CHECK (platform IS NULL OR platform IN ('ios', 'android', 'web', 'desktop')),
    CONSTRAINT ck_devices_trust_score
        CHECK (trust_score IN ('low', 'medium', 'high')),
    CONSTRAINT ck_devices_total_checkins
        CHECK (total_checkins >= 0)
);

CREATE INDEX IF NOT EXISTS ix_devices_user_id     ON devices (user_id);
CREATE INDEX IF NOT EXISTS ix_devices_fingerprint ON devices (device_fingerprint);
CREATE INDEX IF NOT EXISTS ix_devices_is_active   ON devices (is_active);
CREATE INDEX IF NOT EXISTS ix_devices_is_trusted  ON devices (is_trusted);


-- ===========================================================================
-- checkins
-- One row per student per session. Holds the verification evidence and the
-- resulting risk decision.
-- PRIVACY: face_embedding_hash only; no images, no embeddings, no BLOBs.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS checkins (
    id                          VARCHAR(36) PRIMARY KEY,
    session_id                  VARCHAR(36) NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    student_id                  VARCHAR(36) NOT NULL REFERENCES users (id)    ON DELETE CASCADE,
    device_id                   VARCHAR(36) REFERENCES devices (id) ON DELETE SET NULL,

    status                      VARCHAR(20) NOT NULL DEFAULT 'pending',
    checked_in_at               TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    verified_at                 TIMESTAMP,

    -- Geolocation (only populated with geolocation_consent)
    latitude                    DOUBLE PRECISION,
    longitude                   DOUBLE PRECISION,
    location_accuracy_meters    DOUBLE PRECISION,
    distance_from_venue_meters  DOUBLE PRECISION,

    -- Liveness (only populated with camera_consent)
    liveness_passed             BOOLEAN,
    liveness_score              DOUBLE PRECISION,
    liveness_challenge_type     VARCHAR(50),

    -- Face match
    face_match_passed           BOOLEAN,
    face_match_score            DOUBLE PRECISION,
    face_embedding_hash         VARCHAR(64),

    -- Risk decision
    risk_score                  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    risk_factors                TEXT,                  -- JSON array of strings
    qr_code_verified            BOOLEAN NOT NULL DEFAULT FALSE,

    -- Manual review
    reviewed_by_id              VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    reviewed_at                 TIMESTAMP,
    review_notes                TEXT,

    -- Student appeal
    appeal_reason               TEXT,
    appealed_at                 TIMESTAMP,

    scheduled_deletion_at       TIMESTAMP,             -- PII retention: +30 days

    CONSTRAINT uq_checkins_session_student UNIQUE (session_id, student_id),
    CONSTRAINT ck_checkins_status
        CHECK (status IN ('pending', 'approved', 'flagged', 'rejected', 'appealed')),
    CONSTRAINT ck_checkins_latitude
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CONSTRAINT ck_checkins_longitude
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    CONSTRAINT ck_checkins_liveness_score
        CHECK (liveness_score IS NULL OR liveness_score BETWEEN 0 AND 1),
    CONSTRAINT ck_checkins_face_match_score
        CHECK (face_match_score IS NULL OR face_match_score BETWEEN 0 AND 1),
    CONSTRAINT ck_checkins_risk_score
        CHECK (risk_score BETWEEN 0 AND 1),
    CONSTRAINT ck_checkins_face_hash_len
        CHECK (face_embedding_hash IS NULL OR char_length(face_embedding_hash) = 64),
    CONSTRAINT ck_checkins_liveness_challenge_type
        CHECK (liveness_challenge_type IS NULL
               OR liveness_challenge_type IN ('blink', 'head_turn', 'speak',
                                              'passive', 'colour_reflection'))
);

CREATE INDEX IF NOT EXISTS ix_checkins_session_id    ON checkins (session_id);
CREATE INDEX IF NOT EXISTS ix_checkins_student_id    ON checkins (student_id);
CREATE INDEX IF NOT EXISTS ix_checkins_device_id     ON checkins (device_id);
CREATE INDEX IF NOT EXISTS ix_checkins_status        ON checkins (status);
CREATE INDEX IF NOT EXISTS ix_checkins_checked_in_at ON checkins (checked_in_at);
CREATE INDEX IF NOT EXISTS ix_checkins_risk_score    ON checkins (risk_score);
CREATE INDEX IF NOT EXISTS ix_checkins_reviewed_by   ON checkins (reviewed_by_id);
-- Instructor review queue: flagged / appealed check-ins, newest first.
CREATE INDEX IF NOT EXISTS ix_checkins_review_queue
    ON checkins (status, checked_in_at DESC)
    WHERE status IN ('flagged', 'appealed');
CREATE INDEX IF NOT EXISTS ix_checkins_scheduled_deletion_at
    ON checkins (scheduled_deletion_at) WHERE scheduled_deletion_at IS NOT NULL;

-- ck_checkins_liveness_challenge_type, restated for databases that already
-- exist. Same reasoning as ck_risk_signals_type further down: CREATE TABLE IF
-- NOT EXISTS is a no-op once the table is there, so 'colour_reflection' would
-- never reach a running deployment and every check-in using that challenge
-- would fail its INSERT on the old CHECK -- as an opaque 500, after the
-- student had already done the capture.
--
-- Re-runnable, and safe while the list only ever GROWS: the new constraint is
-- a superset of the old one, so no existing row can fail validation. Removing
-- a value would not be safe this way.
ALTER TABLE checkins DROP CONSTRAINT IF EXISTS ck_checkins_liveness_challenge_type;
ALTER TABLE checkins ADD CONSTRAINT ck_checkins_liveness_challenge_type
    CHECK (liveness_challenge_type IS NULL
           OR liveness_challenge_type IN ('blink', 'head_turn', 'speak',
                                          'passive', 'colour_reflection'));


-- ===========================================================================
-- checkin_attempts
-- One immutable row per submission, including the first.
--
-- checkins keeps ONE row per student per session (uq_checkins_session_student
-- above), so a retry updates that row in place and the superseded attempt's
-- evidence would otherwise be overwritten and lost. This table is the history
-- checkins cannot hold. Rows are appended, never updated.
--
-- PRIVACY: duplicates each attempt's location and biometric SCORES, so it
-- carries its own scheduled_deletion_at and is swept on the same +30 day
-- retention as checkins. Never an image, never an embedding.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS checkin_attempts (
    id                          VARCHAR(36) PRIMARY KEY,
    checkin_id                  VARCHAR(36) NOT NULL REFERENCES checkins (id) ON DELETE CASCADE,

    -- 1 for the first submission, then 2, 3, ...
    attempt_number              INTEGER     NOT NULL,

    -- The decision THIS attempt reached, not the check-in's current status.
    status                      VARCHAR(20) NOT NULL,
    risk_score                  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    risk_factors                TEXT,                  -- JSON array of strings

    latitude                    DOUBLE PRECISION,
    longitude                   DOUBLE PRECISION,
    location_accuracy_meters    DOUBLE PRECISION,
    distance_from_venue_meters  DOUBLE PRECISION,

    liveness_passed             BOOLEAN,
    liveness_score              DOUBLE PRECISION,
    face_match_passed           BOOLEAN,
    face_match_score            DOUBLE PRECISION,

    -- Per attempt: switching device between attempts is itself worth seeing.
    device_id                   VARCHAR(36) REFERENCES devices (id) ON DELETE SET NULL,

    attempted_at                TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    scheduled_deletion_at       TIMESTAMP,             -- PII retention: +30 days

    CONSTRAINT uq_checkin_attempts_checkin_number UNIQUE (checkin_id, attempt_number),
    CONSTRAINT ck_checkin_attempts_status
        CHECK (status IN ('pending', 'approved', 'flagged', 'rejected', 'appealed')),
    CONSTRAINT ck_checkin_attempts_attempt_number
        CHECK (attempt_number >= 1),
    CONSTRAINT ck_checkin_attempts_latitude
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CONSTRAINT ck_checkin_attempts_longitude
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    CONSTRAINT ck_checkin_attempts_liveness_score
        CHECK (liveness_score IS NULL OR liveness_score BETWEEN 0 AND 1),
    CONSTRAINT ck_checkin_attempts_face_match_score
        CHECK (face_match_score IS NULL OR face_match_score BETWEEN 0 AND 1),
    CONSTRAINT ck_checkin_attempts_risk_score
        CHECK (risk_score BETWEEN 0 AND 1)
);

-- Additive browser-key rollout. Legacy public keys are unverified until the
-- browser proves possession and the account confirms enrollment.
ALTER TABLE devices ADD COLUMN IF NOT EXISTS browser_key_id VARCHAR(43);
CREATE UNIQUE INDEX IF NOT EXISTS uq_devices_browser_key_id ON devices (browser_key_id);
ALTER TABLE checkins ADD COLUMN IF NOT EXISTS browser_key_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE checkin_attempts ADD COLUMN IF NOT EXISTS browser_key_verified BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS browser_key_challenges (
    id                   VARCHAR(43) PRIMARY KEY,
    purpose              VARCHAR(10) NOT NULL CHECK (purpose IN ('enroll', 'checkin')),
    user_id              VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_version        INTEGER NOT NULL,
    device_id            VARCHAR(36) NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    key_id               VARCHAR(43) NOT NULL,
    session_id           VARCHAR(36) REFERENCES sessions (id) ON DELETE CASCADE,
    recovery_device_id   VARCHAR(36) REFERENCES devices (id) ON DELETE CASCADE,
    recovery_key_id      VARCHAR(43),
    expires_at           TIMESTAMP NOT NULL,
    used_at              TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_browser_key_challenges_expires_at ON browser_key_challenges (expires_at);
CREATE INDEX IF NOT EXISTS ix_browser_key_challenges_enrollment_attempts
    ON browser_key_challenges (user_id, used_at) WHERE used_at IS NOT NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'browser_key_challenges'::regclass AND conname = 'ck_browser_key_challenges_scope'
    ) THEN
        ALTER TABLE browser_key_challenges ADD CONSTRAINT ck_browser_key_challenges_scope CHECK (
            (purpose = 'checkin' AND session_id IS NOT NULL AND recovery_device_id IS NULL
                AND recovery_key_id IS NULL AND used_at IS NULL)
            OR (purpose = 'enroll' AND session_id IS NULL
                AND (recovery_device_id IS NULL) = (recovery_key_id IS NULL))
        );
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS ix_checkin_attempts_checkin_id
    ON checkin_attempts (checkin_id);
CREATE INDEX IF NOT EXISTS ix_checkin_attempts_attempted_at
    ON checkin_attempts (attempted_at);
CREATE INDEX IF NOT EXISTS ix_checkin_attempts_scheduled_deletion_at
    ON checkin_attempts (scheduled_deletion_at) WHERE scheduled_deletion_at IS NOT NULL;


-- ===========================================================================
-- risk_signals
-- The individual indicators that add up to checkins.risk_score.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS risk_signals (
    id           VARCHAR(36) PRIMARY KEY,
    checkin_id   VARCHAR(36) NOT NULL REFERENCES checkins (id) ON DELETE CASCADE,
    signal_type  VARCHAR(50) NOT NULL,
    severity     VARCHAR(20) NOT NULL,
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    details      TEXT,                        -- JSON metadata
    weight       DOUBLE PRECISION NOT NULL DEFAULT 0.1,
    detected_at  TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),

    CONSTRAINT ck_risk_signals_severity
        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_risk_signals_confidence
        CHECK (confidence BETWEEN 0 AND 1),
    CONSTRAINT ck_risk_signals_weight
        CHECK (weight BETWEEN 0 AND 1),
    CONSTRAINT ck_risk_signals_type CHECK (signal_type IN (
        -- Geolocation
        'geo_out_of_bounds', 'impossible_travel', 'geo_accuracy_low',
        'geo_ip_mismatch',
        -- Network
        'vpn_detected', 'proxy_detected', 'tor_detected', 'suspicious_ip',
        -- Device
        'device_unknown', 'device_emulator', 'device_rooted', 'attestation_failed',
        -- Behavioural
        'rapid_succession', 'unusual_time', 'pattern_anomaly',
        -- Liveness
        'liveness_failed', 'liveness_low_confidence', 'deepfake_suspected', 'replay_suspected',
        -- Face match
        'face_match_failed', 'face_match_low_confidence'
    ))
);

CREATE INDEX IF NOT EXISTS ix_risk_signals_checkin_id  ON risk_signals (checkin_id);
CREATE INDEX IF NOT EXISTS ix_risk_signals_signal_type ON risk_signals (signal_type);
CREATE INDEX IF NOT EXISTS ix_risk_signals_severity    ON risk_signals (severity);
CREATE INDEX IF NOT EXISTS ix_risk_signals_detected_at ON risk_signals (detected_at);

-- ck_risk_signals_type, restated for databases that already exist.
--
-- CREATE TABLE IF NOT EXISTS above is a no-op once the table is there, so a
-- signal type added to the list inside it never reaches a running deployment
-- and every INSERT of the new type fails the old CHECK. Dropping and re-adding
-- is re-runnable, and safe while the list only ever GROWS: the new constraint
-- is a superset of the old one, so no existing row can fail validation.
--
-- Removing a value from this list is NOT safe this way: rows holding it would
-- have to be migrated or deleted first.
ALTER TABLE risk_signals DROP CONSTRAINT IF EXISTS ck_risk_signals_type;
ALTER TABLE risk_signals ADD CONSTRAINT ck_risk_signals_type CHECK (signal_type IN (
    -- Geolocation
    'geo_out_of_bounds', 'impossible_travel', 'geo_accuracy_low',
    'geo_ip_mismatch',
    -- Network
    'vpn_detected', 'proxy_detected', 'tor_detected', 'suspicious_ip',
    -- Device
    'device_unknown', 'device_emulator', 'device_rooted', 'attestation_failed',
    -- Behavioural
    'rapid_succession', 'unusual_time', 'pattern_anomaly',
    -- Liveness
    'liveness_failed', 'liveness_low_confidence', 'deepfake_suspected', 'replay_suspected',
    -- Face match
    'face_match_failed', 'face_match_low_confidence'
));


-- ===========================================================================
-- audit_logs
-- Append-only trail. There is deliberately no updated_at column: rows are
-- never modified. The rules below enforce that at the database level.
--
-- `action` is intentionally left without a CHECK constraint so new action
-- types can be logged without a migration. Known values:
--   login_success, login_failed, logout, user_created, user_updated,
--   checkin_attempted, checkin_approved, checkin_flagged, checkin_rejected,
--   checkin_appealed, checkin_reviewed, session_created, session_updated,
--   session_deleted, enrollment_added, enrollment_removed, device_registered,
--   face_enrolled, data_exported, security_violation
-- ===========================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id             VARCHAR(36)  PRIMARY KEY,
    user_id        VARCHAR(36)  REFERENCES users (id) ON DELETE SET NULL,  -- keep the log
    action         VARCHAR(50)  NOT NULL,
    resource_type  VARCHAR(50),
    resource_id    VARCHAR(36),
    ip_address     VARCHAR(45),                    -- IPv4 or IPv6
    user_agent     VARCHAR(500),
    device_id      VARCHAR(36),                    -- not an FK: survives device deletion
    details        TEXT,                           -- JSON
    success        BOOLEAN      NOT NULL DEFAULT TRUE,
    timestamp      TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id    ON audit_logs (user_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action     ON audit_logs (action);
CREATE INDEX IF NOT EXISTS ix_audit_logs_timestamp  ON audit_logs (timestamp);
CREATE INDEX IF NOT EXISTS ix_audit_logs_resource   ON audit_logs (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_ip_address ON audit_logs (ip_address);

-- Immutability is enforced in the application layer, not by a trigger: a
-- BEFORE UPDATE OR DELETE trigger here would also fire on the ON DELETE SET
-- NULL above (user deletion) and on test-fixture teardown. The backend must
-- only ever INSERT into this table.
--
-- To enforce it in the database for a production deployment, revoke the write
-- privileges from the application role instead:
--   REVOKE UPDATE, DELETE ON audit_logs FROM saiv;

COMMIT;
