DO $$
BEGIN
    IF current_setting('server_version_num')::integer < 180000
       OR current_setting('server_version_num')::integer >= 190000 THEN
        RAISE EXCEPTION 'PostgreSQL 18 is required';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS raw_tdnet_history AUTHORIZATION research_owner;
ALTER SCHEMA raw_tdnet_history OWNER TO research_owner;
REVOKE ALL ON SCHEMA raw_tdnet_history FROM PUBLIC;

-- One row per legacy tdnet_report_tags row (17 rows). The slug is already a
-- stable natural key, so there is no separate legacy_id column here.
CREATE TABLE raw_tdnet_history.report_tag (
    report_tag_entry_id text PRIMARY KEY CHECK (report_tag_entry_id ~ '^[0-9a-f]{64}$'),
    slug text NOT NULL UNIQUE,
    label_ja text NOT NULL,
    label_en text NOT NULL,
    description text NOT NULL,
    priority integer NOT NULL,
    active boolean NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    migration_batch_id text NOT NULL CHECK (migration_batch_id ~ '^[0-9a-f]{64}$'),
    migrated_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis)
);

-- One row per legacy tdnet_disclosures row (35,984 rows). The legacy `id`
-- is already a 16-char opaque hex string, not an integer, so it is kept
-- verbatim as disclosure_id rather than renamed to legacy_id.
CREATE TABLE raw_tdnet_history.disclosure (
    disclosure_entry_id text PRIMARY KEY CHECK (disclosure_entry_id ~ '^[0-9a-f]{64}$'),
    disclosure_id text NOT NULL UNIQUE,
    disclosure_date date NOT NULL,
    time text NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    title text NOT NULL,
    pdf_url text NOT NULL UNIQUE,
    xbrl_available boolean NOT NULL,
    xbrl_url text,
    place text NOT NULL,
    history text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    migration_batch_id text NOT NULL CHECK (migration_batch_id ~ '^[0-9a-f]{64}$'),
    migrated_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis)
);

CREATE INDEX disclosure_disclosure_date_idx ON raw_tdnet_history.disclosure (disclosure_date);
CREATE INDEX disclosure_code_idx ON raw_tdnet_history.disclosure (code);

-- One row per legacy disclosure_files row (43,542 rows): path plus hash,
-- never the PDF/XBRL bytes themselves (those stay on the SSD).
CREATE TABLE raw_tdnet_history.file (
    file_entry_id text PRIMARY KEY CHECK (file_entry_id ~ '^[0-9a-f]{64}$'),
    legacy_id integer NOT NULL UNIQUE,
    disclosure_id text NOT NULL REFERENCES raw_tdnet_history.disclosure (disclosure_id),
    file_type text NOT NULL,
    source_url text NOT NULL,
    source_file_id text NOT NULL,
    storage_bucket text NOT NULL,
    storage_path text NOT NULL,
    content_type text,
    file_size_bytes integer,
    sha256 text,
    download_status text NOT NULL,
    download_attempts integer NOT NULL,
    downloaded_at timestamptz,
    last_download_error text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    migration_batch_id text NOT NULL CHECK (migration_batch_id ~ '^[0-9a-f]{64}$'),
    migrated_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis),
    UNIQUE (disclosure_id, file_type)
);

-- One row per legacy document_parse_jobs row (36,940 rows).
CREATE TABLE raw_tdnet_history.parse_job (
    parse_job_entry_id text PRIMARY KEY CHECK (parse_job_entry_id ~ '^[0-9a-f]{64}$'),
    legacy_id integer NOT NULL UNIQUE,
    file_legacy_id integer NOT NULL REFERENCES raw_tdnet_history.file (legacy_id),
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    parse_status text NOT NULL,
    parse_attempts integer NOT NULL,
    text_path text,
    text_sha256 text,
    parsed_at timestamptz,
    last_parse_error text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    migration_batch_id text NOT NULL CHECK (migration_batch_id ~ '^[0-9a-f]{64}$'),
    migrated_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis),
    UNIQUE (file_legacy_id, parser_name, parser_version)
);

-- One row per legacy document_parse_texts row (36,924 rows, 953 MB). Full
-- parsed text is copied in full (ruling T1); the legacy pages_json and
-- content_sha256 travel with it unchanged.
CREATE TABLE raw_tdnet_history.parse_text (
    parse_text_entry_id text PRIMARY KEY CHECK (parse_text_entry_id ~ '^[0-9a-f]{64}$'),
    legacy_id integer NOT NULL UNIQUE,
    parse_job_legacy_id integer NOT NULL UNIQUE REFERENCES raw_tdnet_history.parse_job (legacy_id),
    content_text text NOT NULL,
    pages_json jsonb,
    page_count integer NOT NULL,
    char_count integer NOT NULL,
    content_sha256 text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    migration_batch_id text NOT NULL CHECK (migration_batch_id ~ '^[0-9a-f]{64}$'),
    migrated_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis)
);

-- One row per legacy document_analysis_results row (51,792 rows): the
-- financial_facts analyzer output, 3 versions of one 2026-06-05 batch run.
CREATE TABLE raw_tdnet_history.analysis_result (
    analysis_result_entry_id text PRIMARY KEY CHECK (analysis_result_entry_id ~ '^[0-9a-f]{64}$'),
    legacy_id integer NOT NULL UNIQUE,
    file_legacy_id integer NOT NULL REFERENCES raw_tdnet_history.file (legacy_id),
    parse_job_legacy_id integer REFERENCES raw_tdnet_history.parse_job (legacy_id),
    analysis_type text NOT NULL,
    analyzer_name text NOT NULL,
    analyzer_version text NOT NULL,
    status text NOT NULL,
    result_json jsonb,
    result_text text,
    analyzed_at timestamptz,
    last_analysis_error text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    migration_batch_id text NOT NULL CHECK (migration_batch_id ~ '^[0-9a-f]{64}$'),
    migrated_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis)
);

-- One row per legacy tdnet_report_tag_assignments row (42,511 rows).
CREATE TABLE raw_tdnet_history.report_tag_assignment (
    report_tag_assignment_entry_id text PRIMARY KEY CHECK (report_tag_assignment_entry_id ~ '^[0-9a-f]{64}$'),
    legacy_id integer NOT NULL UNIQUE,
    disclosure_id text NOT NULL REFERENCES raw_tdnet_history.disclosure (disclosure_id),
    tag_slug text NOT NULL REFERENCES raw_tdnet_history.report_tag (slug),
    file_legacy_id integer REFERENCES raw_tdnet_history.file (legacy_id),
    parse_job_legacy_id integer REFERENCES raw_tdnet_history.parse_job (legacy_id),
    is_primary boolean NOT NULL,
    confidence double precision NOT NULL,
    source text NOT NULL,
    evidence_json jsonb,
    tagger_name text NOT NULL,
    tagger_version text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    migration_batch_id text NOT NULL CHECK (migration_batch_id ~ '^[0-9a-f]{64}$'),
    migrated_at timestamptz NOT NULL,
    ready_for_analysis boolean NOT NULL DEFAULT false CHECK (NOT ready_for_analysis),
    UNIQUE (disclosure_id, tag_slug)
);

ALTER TABLE raw_tdnet_history.report_tag OWNER TO research_owner;
ALTER TABLE raw_tdnet_history.disclosure OWNER TO research_owner;
ALTER TABLE raw_tdnet_history.file OWNER TO research_owner;
ALTER TABLE raw_tdnet_history.parse_job OWNER TO research_owner;
ALTER TABLE raw_tdnet_history.parse_text OWNER TO research_owner;
ALTER TABLE raw_tdnet_history.analysis_result OWNER TO research_owner;
ALTER TABLE raw_tdnet_history.report_tag_assignment OWNER TO research_owner;

REVOKE ALL ON ALL TABLES IN SCHEMA raw_tdnet_history FROM PUBLIC;
REVOKE ALL ON SCHEMA raw_tdnet_history FROM tdnet_ingest, research_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA raw_tdnet_history FROM tdnet_ingest, research_reader;

GRANT USAGE ON SCHEMA raw_tdnet_history TO tdnet_ingest, research_reader;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA raw_tdnet_history TO tdnet_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA raw_tdnet_history TO research_reader;
