-- Telemetry Fusion sema
-- observations: append-only zaman serisi (her gozlem bir satir)
-- tracks      : her nesnenin son bilinen durumu (hizli anlik goruntu)

CREATE TABLE IF NOT EXISTS observations (
    id                BIGSERIAL PRIMARY KEY,
    source            TEXT        NOT NULL,
    source_id         TEXT        NOT NULL,
    ts                TIMESTAMPTZ NOT NULL,
    lat               DOUBLE PRECISION NOT NULL,
    lon               DOUBLE PRECISION NOT NULL,
    altitude_m        DOUBLE PRECISION,
    speed_mps         DOUBLE PRECISION,
    heading_deg       DOUBLE PRECISION,
    vertical_rate_mps DOUBLE PRECISION,
    label             TEXT,
    country           TEXT,
    on_ground         BOOLEAN     NOT NULL DEFAULT FALSE,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Ayni kaynaktan ayni nesne icin ayni zaman damgasi tekrar gelirse yazilmaz.
    -- Dedup'in son savunma hatti: uygulama katmani kacirirsa veritabani tutar.
    CONSTRAINT observations_unique_sample UNIQUE (source, source_id, ts)
);

CREATE INDEX IF NOT EXISTS observations_track_ts_idx ON observations (source, source_id, ts DESC);
CREATE INDEX IF NOT EXISTS observations_ts_idx ON observations (ts DESC);

CREATE TABLE IF NOT EXISTS tracks (
    source        TEXT        NOT NULL,
    source_id     TEXT        NOT NULL,
    last_ts       TIMESTAMPTZ NOT NULL,
    lat           DOUBLE PRECISION NOT NULL,
    lon           DOUBLE PRECISION NOT NULL,
    altitude_m    DOUBLE PRECISION,
    speed_mps     DOUBLE PRECISION,
    heading_deg   DOUBLE PRECISION,
    label         TEXT,
    country       TEXT,
    on_ground     BOOLEAN     NOT NULL DEFAULT FALSE,
    sample_count  BIGINT      NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, source_id)
);

CREATE INDEX IF NOT EXISTS tracks_last_ts_idx ON tracks (last_ts DESC);
