CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(64) UNIQUE NOT NULL,
    routing_mode VARCHAR(16) DEFAULT 'active',
    similarity_threshold REAL DEFAULT 0.82,
    source_model VARCHAR(128) NOT NULL,
    builder_model VARCHAR(128) NOT NULL,
    target_model VARCHAR(128) NOT NULL,
    tool_callback_url TEXT,
    tool_callback_secret VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cache_entries (
    id SERIAL PRIMARY KEY,
    project_slug VARCHAR(64) REFERENCES projects(slug),
    entry_key VARCHAR(128) UNIQUE NOT NULL,
    intent_description TEXT NOT NULL,
    param_schema JSONB NOT NULL,
    source_path TEXT NOT NULL,
    is_compiled BOOLEAN DEFAULT FALSE,
    hit_count INTEGER DEFAULT 0,
    avg_source_cost_usd NUMERIC(12, 8) DEFAULT 0,
    builder_cost_usd NUMERIC(12, 8) DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS telemetry_ledger (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL,
    project_slug VARCHAR(64) NOT NULL,
    cache_entry_id INTEGER REFERENCES cache_entries(id) ON DELETE SET NULL,
    execution_path VARCHAR(32) NOT NULL,
    role VARCHAR(24) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cost_usd NUMERIC(12, 8) DEFAULT 0,
    counterfactual_cost_usd NUMERIC(12, 8) DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
