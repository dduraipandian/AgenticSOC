CREATE SCHEMA IF NOT EXISTS pipeline;

CREATE TABLE IF NOT EXISTS pipeline.puller_checkpoints (
    source      text PRIMARY KEY,
    checkpoint  jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pipeline.puller_raw_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          text NOT NULL,
    vendor_event_id text NOT NULL,
    event           jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT unique_source_event UNIQUE (source, vendor_event_id)
);

CREATE TABLE IF NOT EXISTS pipeline.puller_failures (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          text NOT NULL,
    vendor_event_id text,
    event           text NOT NULL,
    error           text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    processed       bool NOT NULL DEFAULT false
);
