-- Telemetry Fusion sema
--
-- observations : append-only zaman serisi (her gozlem bir satir)
-- tracks       : her nesnenin son bilinen durumu (hizli anlik goruntu)
-- correlations : kaynaklar arasi eslesmeler (fuzyon ciktisi)
--
-- Not: Bu dosya yalnizca bos bir veritabaninda calisir. Mevcut kurulumlarin
-- guncellenmesi icin processor acilista ayni tanimlari idempotent olarak
-- tekrar uygular (bkz. store.ensure_schema).

CREATE TABLE IF NOT EXISTS observations (
    id                BIGSERIAL PRIMARY KEY,
    source            TEXT        NOT NULL,
    source_id         TEXT        NOT NULL,
    object_type       TEXT        NOT NULL DEFAULT 'unknown',
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
    object_type   TEXT        NOT NULL DEFAULT 'unknown',
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
CREATE INDEX IF NOT EXISTS tracks_type_idx ON tracks (object_type, last_ts DESC);

-- Fuzyon ciktisi: iki farkli kaynaktaki nesnelerin uzamsal eslesmesi.
-- Ornek: bir uydunun yer izdusumu, bir ucagin konumuna 250 km'den yakin.
CREATE TABLE IF NOT EXISTS correlations (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    a_source       TEXT NOT NULL,
    a_source_id    TEXT NOT NULL,
    a_object_type  TEXT NOT NULL,
    a_label        TEXT,
    b_source       TEXT NOT NULL,
    b_source_id    TEXT NOT NULL,
    b_object_type  TEXT NOT NULL,
    b_label        TEXT,
    distance_km    DOUBLE PRECISION NOT NULL,
    -- Ayni cift, ayni saniyede iki kez yazilmasin
    CONSTRAINT correlations_unique_pair UNIQUE (ts, a_source, a_source_id, b_source, b_source_id)
);

CREATE INDEX IF NOT EXISTS correlations_ts_idx ON correlations (ts DESC);
CREATE INDEX IF NOT EXISTS correlations_a_idx ON correlations (a_source, a_source_id, ts DESC);
