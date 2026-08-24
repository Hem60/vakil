-- Vakil schema. Postgres holds three things: cases, the rulebook index, and
-- the audit ledger. The ledger is append-only by convention here and enforced
-- by the hash chain in vakil.ledger.chain - the database is not the authority
-- on integrity, the chain is.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS cases (
    case_id       TEXT PRIMARY KEY,
    dispute_id    TEXT NOT NULL UNIQUE,
    payment_id    TEXT NOT NULL,
    reason_code   TEXT NOT NULL,
    amount        BIGINT NOT NULL,
    respond_by    TIMESTAMPTZ NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    verdict       TEXT,
    p_win         DOUBLE PRECISION,
    net_ev        BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cases_respond_by_idx ON cases (respond_by)
    WHERE status = 'open';

-- Network rulebook chunks. Every requirement Vakil cites must resolve to a row
-- here, so a citation can always be followed back to source text.
CREATE TABLE IF NOT EXISTS rulebook_chunks (
    id          BIGSERIAL PRIMARY KEY,
    network     TEXT NOT NULL,
    document    TEXT NOT NULL,
    section     TEXT NOT NULL,
    reason_code TEXT,
    body        TEXT NOT NULL,
    embedding   vector(1024)
);

CREATE INDEX IF NOT EXISTS rulebook_reason_idx ON rulebook_chunks (reason_code);
CREATE INDEX IF NOT EXISTS rulebook_fts_idx
    ON rulebook_chunks USING gin (to_tsvector('english', body));

CREATE TABLE IF NOT EXISTS ledger (
    seq        BIGSERIAL PRIMARY KEY,
    hash       TEXT NOT NULL UNIQUE,
    prev_hash  TEXT NOT NULL,
    dispute_id TEXT NOT NULL,
    stage      TEXT NOT NULL,
    at         TIMESTAMPTZ NOT NULL,
    payload    JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ledger_dispute_idx ON ledger (dispute_id, seq);
