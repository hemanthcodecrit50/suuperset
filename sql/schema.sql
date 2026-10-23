-- Superset Campus Placement Intelligence System
-- PostgreSQL schema. Mirrors app/models.py.
--
-- Unknown values (deadline, eligibility, compensation) must stay NULL rather
-- than being guessed. Content hashes and external ids drive idempotent upserts.

BEGIN;

-- Configuration for each portal/source being monitored.
CREATE TABLE IF NOT EXISTS sources (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    base_url    TEXT,
    login_url   TEXT,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    config      JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS companies (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    slug          TEXT UNIQUE,
    website       TEXT,
    industry      TEXT,
    headquarters  TEXT,
    description   TEXT,
    metadata      JSONB DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS opportunities (
    id             BIGSERIAL PRIMARY KEY,
    source_id      BIGINT REFERENCES sources(id) ON DELETE SET NULL,
    company_id     BIGINT REFERENCES companies(id) ON DELETE SET NULL,
    external_id    TEXT NOT NULL,
    company_name   TEXT,
    title          TEXT NOT NULL,
    role           TEXT,
    location       TEXT,
    job_function   TEXT,
    work_type      TEXT,
    compensation   TEXT,
    eligibility    TEXT,
    skills         JSONB DEFAULT '[]'::jsonb,
    deadline       TIMESTAMPTZ,
    apply_url      TEXT,
    description    TEXT,
    attachments    JSONB DEFAULT '[]'::jsonb,
    relevance_score      REAL,
    relevance_reasons    JSONB DEFAULT '[]'::jsonb,
    relevance_unknown    JSONB DEFAULT '[]'::jsonb,
    eligible             BOOLEAN,
    deadline_reminded_at TIMESTAMPTZ,
    detail_fetched_at     TIMESTAMPTZ,
    content_hash   TEXT,
    first_seen_at  TIMESTAMPTZ,
    last_seen_at   TIMESTAMPTZ,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    raw            JSONB DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_opportunities_source_external UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_opportunities_deadline ON opportunities (deadline);
CREATE INDEX IF NOT EXISTS idx_opportunities_company ON opportunities (company_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_active ON opportunities (is_active);

CREATE TABLE IF NOT EXISTS opportunity_versions (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    content_hash    TEXT NOT NULL,
    title           TEXT,
    description     TEXT,
    eligibility     TEXT,
    deadline        TIMESTAMPTZ,
    skills          JSONB DEFAULT '[]'::jsonb,
    raw             JSONB DEFAULT '{}'::jsonb,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_opp_versions_opportunity ON opportunity_versions (opportunity_id);

CREATE TABLE IF NOT EXISTS announcements (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT REFERENCES sources(id) ON DELETE SET NULL,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT,
    url           TEXT,
    category      TEXT,
    content_hash  TEXT,
    published_at  TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ,
    last_seen_at  TIMESTAMPTZ,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    raw           JSONB DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_announcements_source_external UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_announcements_published ON announcements (published_at);

CREATE TABLE IF NOT EXISTS announcement_versions (
    id               BIGSERIAL PRIMARY KEY,
    announcement_id  BIGINT NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
    content_hash     TEXT NOT NULL,
    title            TEXT,
    body             TEXT,
    raw              JSONB DEFAULT '{}'::jsonb,
    captured_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ann_versions_announcement ON announcement_versions (announcement_id);

CREATE TABLE IF NOT EXISTS applications (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT NOT NULL UNIQUE REFERENCES opportunities(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'saved',
    priority        INTEGER,
    next_action     TEXT,
    next_action_at  TIMESTAMPTZ,
    notes           TEXT,
    applied_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications (status);

CREATE TABLE IF NOT EXISTS user_preferences (
    id          BIGSERIAL PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    value       JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio_items (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    description   TEXT,
    technologies  JSONB DEFAULT '[]'::jsonb,
    achievements  JSONB DEFAULT '[]'::jsonb,
    evidence_url  TEXT,
    status        TEXT NOT NULL DEFAULT 'implemented',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research_reports (
    id              BIGSERIAL PRIMARY KEY,
    company_id      BIGINT REFERENCES companies(id) ON DELETE SET NULL,
    opportunity_id  BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL DEFAULT 'company',
    summary         TEXT,
    content         JSONB DEFAULT '{}'::jsonb,
    sources         JSONB DEFAULT '[]'::jsonb,
    model           TEXT,
    content_hash    TEXT,
    is_stale        BOOLEAN NOT NULL DEFAULT FALSE,
    generated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resume_suggestions (
    id                 BIGSERIAL PRIMARY KEY,
    opportunity_id     BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,
    portfolio_item_id  BIGINT REFERENCES portfolio_items(id) ON DELETE SET NULL,
    requirement        TEXT,
    bullet             TEXT NOT NULL,
    evidence           TEXT,
    technologies       JSONB DEFAULT '[]'::jsonb,
    approved           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    id               BIGSERIAL PRIMARY KEY,
    opportunity_id   BIGINT REFERENCES opportunities(id) ON DELETE CASCADE,
    announcement_id  BIGINT REFERENCES announcements(id) ON DELETE CASCADE,
    channel          TEXT NOT NULL,
    change_type      TEXT NOT NULL,
    dedup_key        TEXT NOT NULL UNIQUE,
    payload          JSONB DEFAULT '{}'::jsonb,
    status           TEXT NOT NULL DEFAULT 'pending',
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    scheduled_at     TIMESTAMPTZ,
    sent_at          TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_status ON alert_deliveries (status);

CREATE TABLE IF NOT EXISTS poll_runs (
    id             BIGSERIAL PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'running',
    items_seen     INTEGER NOT NULL DEFAULT 0,
    items_new      INTEGER NOT NULL DEFAULT 0,
    items_changed  INTEGER NOT NULL DEFAULT 0,
    alerts_queued  INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    notes          JSONB DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_poll_runs_started ON poll_runs (started_at);

-- Seed the Superset source (idempotent).
INSERT INTO sources (name, base_url, login_url, enabled)
VALUES ('superset', 'https://app.joinsuperset.com', 'https://app.joinsuperset.com/students', TRUE)
ON CONFLICT (name) DO NOTHING;

COMMIT;
