CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS posts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source      VARCHAR(20) NOT NULL,
    external_id VARCHAR(100) UNIQUE NOT NULL,
    title       TEXT,
    content     TEXT NOT NULL,
    author      VARCHAR(100),
    url         TEXT,
    recommended BOOLEAN,
    sentiment   FLOAT,
    issue_tags  VARCHAR(50)[],
    embedding   vector(1024),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    analyzed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_type      VARCHAR(50) NOT NULL,
    severity        VARCHAR(20) NOT NULL,
    trigger_data    JSONB,
    related_post_ids VARCHAR(100)[],
    slack_ts        VARCHAR(50),
    status          VARCHAR(20) DEFAULT 'open',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS drafts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id     UUID REFERENCES alerts(id),
    content      TEXT NOT NULL,
    tone         VARCHAR(20),
    status       VARCHAR(20) DEFAULT 'pending',
    feedback     TEXT,
    eval_scores  JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    status           VARCHAR(20) DEFAULT 'running',
    posts_analyzed   INTEGER DEFAULT 0,
    alerts_triggered INTEGER DEFAULT 0,
    drafts_generated INTEGER DEFAULT 0,
    error_message    TEXT
);

CREATE TABLE IF NOT EXISTS official_responses (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_tag  VARCHAR(50) NOT NULL,
    content    TEXT NOT NULL,
    source     VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_official_responses_tag ON official_responses(issue_tag);

CREATE TABLE IF NOT EXISTS patch_notes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gid          VARCHAR(50) UNIQUE NOT NULL,
    version      VARCHAR(50) NOT NULL,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source);
CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_posts_sentiment ON posts(sentiment);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
