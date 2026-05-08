CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS patients (
    id SERIAL PRIMARY KEY,
    patient_id VARCHAR(64) UNIQUE NOT NULL,
    name TEXT,
    birth_date DATE,
    gender VARCHAR(10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS consent_records (
    id SERIAL PRIMARY KEY,
    patient_id VARCHAR(64) REFERENCES patients(patient_id),
    consent_type VARCHAR(32) NOT NULL,
    consented BOOLEAN NOT NULL DEFAULT FALSE,
    consent_date TIMESTAMP,
    withdrawal_date TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS diagnosis_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) UNIQUE NOT NULL,
    patient_id VARCHAR(64) REFERENCES patients(patient_id),
    organ VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    kb_version VARCHAR(32),
    model_version VARCHAR(32),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS diagnosis_results (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) REFERENCES diagnosis_sessions(session_id),
    organ VARCHAR(32) NOT NULL,
    tier VARCHAR(16) NOT NULL,
    confidence FLOAT NOT NULL,
    findings TEXT,
    recommendations TEXT,
    knowledge_ids TEXT[],
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_base (
    id SERIAL PRIMARY KEY,
    kb_id VARCHAR(64) UNIQUE NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768),
    organ VARCHAR(32),
    tier VARCHAR(16),
    version VARCHAR(32),
    confidence FLOAT DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64),
    operation VARCHAR(64) NOT NULL,
    operator VARCHAR(64),
    details JSONB,
    hash_chain VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_knowledge_embedding ON knowledge_base USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_diagnosis_sessions_session_id ON diagnosis_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_consent_patient_id ON consent_records(patient_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_session_id ON audit_logs(session_id);