-- Initialize PostgreSQL + pgvector database and seed development baseline data
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Core Projects Table
CREATE TABLE IF NOT EXISTS projects (
    project_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    therapeutic_area VARCHAR(128) NOT NULL,
    dosage_form VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Approved Regulatory & Scientific Sources Table
CREATE TABLE IF NOT EXISTS approved_sources (
    source_id VARCHAR(128) PRIMARY KEY,
    title TEXT NOT NULL,
    publisher VARCHAR(64) NOT NULL,
    publication_date DATE NOT NULL,
    version VARCHAR(64) NOT NULL,
    url_or_identifier TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-Only Audit Events Table (21 CFR Part 11 / GxP baseline)
CREATE TABLE IF NOT EXISTS audit_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id VARCHAR(128) NOT NULL,
    actor_role VARCHAR(64) NOT NULL,
    project_id VARCHAR(64),
    action VARCHAR(128) NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed Development Projects
INSERT INTO projects (project_id, name, therapeutic_area, dosage_form)
VALUES
    ('PRJ-001', 'Metformin ER Nitrosamine Risk Mitigation', 'Metabolic Disorders', 'Extended-Release Tablet'),
    ('PRJ-002', 'BCS Class II Oral Bioavailability Enhancement', 'Oncology', 'Immediate-Release Capsule')
ON CONFLICT (project_id) DO NOTHING;

-- Seed Approved Regulatory Sources
INSERT INTO approved_sources (source_id, title, publisher, publication_date, version, url_or_identifier, content_hash)
VALUES
    (
        'fda-nitrosamines-rev1',
        'Control of Nitrosamine Impurities in Human Drugs: Guidance for Industry (Rev 1)',
        'FDA',
        '2021-02-24',
        'Revision 1',
        'FDA-2020-D-1530',
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    ),
    (
        'ich-q8-r2-pharma-dev',
        'ICH Harmonised Tripartite Guideline: Pharmaceutical Development Q8(R2)',
        'ICH',
        '2009-08-01',
        'Step 4',
        'ICH-Q8-R2',
        '7d865e959b2466918c9863afca942d0fb89d7c9ac0c99bafc3749504ded97730'
    ),
    (
        'ich-q3d-elemental-impurities',
        'ICH Guideline Q3D(R2) on Elemental Impurities',
        'ICH',
        '2022-04-26',
        'Revision 2',
        'ICH-Q3D-R2',
        'b5bb9d8014a0f9b1d61e21e796d78dccdf1352f23cd32812f4850b878ae4944c'
    )
ON CONFLICT (source_id) DO NOTHING;

-- Seed Initial System Audit Event
INSERT INTO audit_events (actor_id, actor_role, project_id, action, resource_type, resource_id, details)
SELECT
    'system-bootstrap',
    'admin',
    'PRJ-001',
    'DATABASE_SEED_INITIALIZED',
    'database',
    'pharma_ai',
    '{"note": "Local development seed data loaded"}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM audit_events WHERE action = 'DATABASE_SEED_INITIALIZED');
