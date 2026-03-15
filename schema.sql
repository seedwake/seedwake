-- Seedwake database schema
-- Runs automatically on first PostgreSQL container startup.

CREATE EXTENSION IF NOT EXISTS vector;

-- Long-term memory with vector search
CREATE TABLE long_term_memory (
    id              BIGSERIAL PRIMARY KEY,
    content         TEXT NOT NULL,
    memory_type     TEXT NOT NULL,          -- episodic / semantic / action_result
    embedding       vector(4096),           -- must match qwen3-embedding output dimension
    entity_tags     TEXT[] DEFAULT '{}',
    source_cycle_id INTEGER,
    emotion_context JSONB,
    importance      FLOAT DEFAULT 0.5,
    access_count    INTEGER DEFAULT 0,
    last_accessed   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE
);

-- Vector index omitted: qwen3-embedding outputs 4096 dimensions,
-- exceeding pgvector's 2000-dim limit for ivfflat/hnsw indexes.
-- Full-table scan is acceptable at current data scale.
-- Options when data grows: truncate dimensions via Ollama API, or use PCA.
CREATE INDEX idx_ltm_entity_tags ON long_term_memory USING GIN (entity_tags);
CREATE INDEX idx_ltm_type ON long_term_memory (memory_type);
CREATE INDEX idx_ltm_created ON long_term_memory (created_at);

-- Identity document
CREATE TABLE identity (
    id         SERIAL PRIMARY KEY,
    section    TEXT NOT NULL UNIQUE,    -- self_description / core_goals / self_understanding
    content    TEXT NOT NULL,
    version    INTEGER DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Habit seeds (Phase 4, table created now for schema completeness)
CREATE TABLE habit_seeds (
    id               BIGSERIAL PRIMARY KEY,
    pattern          TEXT NOT NULL,
    category         TEXT,              -- cognitive / behavioral / emotional
    strength         FLOAT DEFAULT 0.1,
    activation_count INTEGER DEFAULT 0,
    last_activated   TIMESTAMPTZ,
    source_memories  BIGINT[] DEFAULT '{}',
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Audit log (append-only)
CREATE TABLE audit_log (
    id             BIGSERIAL PRIMARY KEY,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cycle_id       INTEGER,
    event_type     TEXT NOT NULL,
    content        JSONB NOT NULL,
    prompt_version TEXT,
    full_prompt    TEXT,
    raw_output     TEXT,
    metadata       JSONB
);

CREATE INDEX idx_audit_cycle ON audit_log (cycle_id);
CREATE INDEX idx_audit_type ON audit_log (event_type);
CREATE INDEX idx_audit_time ON audit_log (timestamp);
