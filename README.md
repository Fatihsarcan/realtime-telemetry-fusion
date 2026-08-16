<h1 align="center">Telemetry Fusion</h1>

<p align="center">
  A real-time, multi-source telemetry ingestion and fusion platform.<br>
  Collects high-frequency positional data from unreliable external sources, normalises it into a
  single model, deduplicates it, and serves it as both a live stream and a queryable history.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/RabbitMQ-topic%20exchange-FF6600?logo=rabbitmq&logoColor=white" alt="RabbitMQ">
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL 16">
  <img src="https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white" alt="Redis 7">
  <img src="https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Terraform-OCI-7B42BC?logo=terraform&logoColor=white" alt="Terraform">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

<p align="center"><a href="README.tr.md">🇹🇷 Türkçe</a></p>

---

## What it does

The platform currently ingests live **ADS-B aircraft telemetry** from the OpenSky Network: roughly 300
aircraft over Turkish airspace, each reporting position, altitude, velocity and heading.

The aircraft are the payload, not the product. **The product is the pipeline** — the part that keeps
working when the source rate-limits you, when the same observation arrives five times, when packets
arrive out of order, and when a service dies mid-batch. Swap the collector and the same pipeline
carries vessel AIS, UAV telemetry, sensor networks or vehicle tracking without touching anything
downstream.

## Why it exists

Streaming telemetry is deceptively hard. A naive implementation writes each record straight to a
database and falls over the first time reality intervenes. This project is built around the failure
modes rather than the happy path:

| Real-world problem | How the system handles it |
|---|---|
| Source rate-limits or goes down | Collector logs, backs off and retries; pipeline keeps serving cached state |
| The same observation arrives repeatedly | Three-layer deduplication (in-batch → Redis → database constraint) |
| Packets arrive out of order | Upsert refuses to overwrite newer state with an older timestamp |
| Malformed records | Validated at ingest; unparseable messages are dead-lettered, never blocking the queue |
| Consumer crashes mid-batch | Nothing is acknowledged until the batch is committed — at-least-once delivery |
| Producer outruns the database | Queue absorbs the burst; prefetch caps in-memory backlog (backpressure) |
| Source imposes a daily quota | Collector measures real cost from response headers and paces itself |

## Architecture

```
                    ┌──────────────────┐
   OpenSky ADS-B ──►│ collector-opensky│──┐
                    └──────────────────┘  │
                    ┌──────────────────┐  │   ┌──────────┐   ┌───────────┐
   (AIS / others) ─►│ collector-*      │──┼──►│ RabbitMQ │──►│ processor │
                    └──────────────────┘  │   └──────────┘   └─────┬─────┘
                                          │    topic exchange      │
                                          │    + dead-letter       │
                                          │                        ▼
                                          │            ┌───────────────────────┐
                                          │            │ normalise → dedup →   │
                                          │            │ batch write           │
                                          │            └───┬───────────────┬───┘
                                          │                ▼               ▼
                                          │        ┌──────────────┐  ┌──────────┐
                                          │        │ PostgreSQL   │  │  Redis   │
                                          │        │ (history)    │  │ (state + │
                                          │        └──────┬───────┘  │  pub/sub)│
                                          │               │          └────┬─────┘
                                          │               ▼               ▼
                                          │           ┌─────────────────────┐
                                          └──────────►│ api (FastAPI)       │
                                                      │ REST + WebSocket    │
                                                      └──────────┬──────────┘
                                                                 ▼
                                                            live map
```

Each service knows as little as possible: the collector knows the external API but not the database;
the processor knows the data model but not where it came from; the API only reads. **Adding a data
source means writing one collector and publishing to the same exchange** — nothing else changes.

| Service | Responsibility | Stack |
|---|---|---|
| `collector-opensky` | Fetch, normalise, publish; OAuth2 token refresh, quota pacing | httpx, aio-pika |
| `rabbitmq` | Decouple ingest from processing; backpressure; dead-lettering | RabbitMQ topic exchange |
| `processor` | Deduplicate, batch-write, publish live updates, enforce retention | asyncpg, redis |
| `postgres` | Append-only observation time series + latest-state table | PostgreSQL 16 |
| `redis` | Latest-state cache + WebSocket fan-out channel | Redis 7 |
| `api` | REST history queries, WebSocket live feed, pipeline metrics | FastAPI, uvicorn |

## Quick start

```bash
git clone https://github.com/Fatihsarcan/realtime-telemetry-fusion
cd realtime-telemetry-fusion
cp .env.example .env          # optionally add free OpenSky API credentials
docker compose up -d --build
```

| | |
|---|---|
| Live map | <http://localhost:8000> |
| API documentation | <http://localhost:8000/docs> |
| RabbitMQ console | <http://localhost:15672> |

First data lands within a minute. Without OpenSky credentials the collector falls back to anonymous
access, which works but has a much smaller daily quota.

## API

| Endpoint | Description |
|---|---|
| `GET /api/tracks?source=&bbox=&limit=` | Latest known position of every tracked object — served from Redis, never touches the database |
| `GET /api/tracks/{source}/{id}/history?minutes=60` | Historical track of one object, from PostgreSQL |
| `GET /api/stats` | Pipeline metrics: throughput, duplicates dropped, batch latency p50/p95 |
| `GET /health` | Liveness check that actually exercises Redis and PostgreSQL |
| `WS /ws/live` | Pushes every new observation as it is processed |

```bash
curl "http://localhost:8000/api/tracks?bbox=39,41,28,30&limit=10" | jq
curl "http://localhost:8000/api/stats" | jq
```

## Design decisions

**Why a queue at all.** The collector can emit hundreds of records per second; PostgreSQL write
throughput is unrelated to that. Putting a queue between them means a burst grows the queue instead
of killing the consumer. `prefetch_count` is capped at twice the batch size so the processor never
accumulates unbounded messages in memory.

**Deduplication is three layers deep.** ADS-B repeatedly re-reports the same aircraft with the same
timestamp. Left unhandled, storage grows without bound and metrics like "observations in the last
minute" become meaningless. So: (1) within a batch the newest record per object wins; (2) each
candidate is compared against the last timestamp in Redis — because Redis is shared state, this stays
correct when the processor is scaled to multiple replicas; (3) a `UNIQUE (source, source_id, ts)`
constraint is the last line of defence if the application layer ever misses one.

**Batch writes.** Committing 500 records in one transaction rather than 500 individual inserts raises
sustained throughput by roughly an order of magnitude. When traffic is sparse a 1-second timer flushes
the partial batch, so data never sits waiting.

**No data loss on crash.** No message is acknowledged until its batch is committed. If the processor
dies mid-flight the messages stay queued and are redelivered — at-least-once. Redelivered records hit
the dedup layer, so the end state is still exactly-once in effect. Messages that cannot be parsed are
dead-lettered rather than retried forever.

**Out-of-order packets.** The latest-state upsert carries `WHERE EXCLUDED.last_ts > tracks.last_ts`,
so a delayed older packet can never overwrite newer state.

**Why both Redis and PostgreSQL.** "Where is it now?" and "where has it been for two hours?" have
completely different access patterns — one is key-based, hot and freshness-critical; the other is a
range scan, cold and durability-critical. Splitting them means the live map places zero load on the
database.

**WebSocket fan-out.** One Redis subscription feeds all clients through in-memory queues, so cost
stays flat as clients scale. A slow client's queue fills and its messages are dropped rather than
stalling the broadcast for everyone.

**Self-pacing against source quotas.** OpenSky charges 1–4 credits per query depending on the area
covered, against a daily allowance. Hard-coding a poll interval is brittle. Instead the collector
measures actual cost from the `X-Rate-Limit-Remaining` header and recomputes its interval every round
as *time until reset ÷ affordable calls*. It slows down as the quota depletes and speeds up when it
resets.

## Resource limits

Nothing in the system can grow unbounded, which is what keeps it safe to run on a fixed, small host.

| Limit | Value | Rationale |
|---|---|---|
| Data retention | 7 days | Chunked deletion keeps the table from filling the disk |
| WebSocket clients | 50 | Each client consumes ~11 GB/month of egress |
| Connections per IP | 5 | A misbehaving reconnect loop cannot monopolise the server |
| Client queue depth | 1000 | Slow consumers drop messages instead of stalling the broadcast |
| Queue prefetch | batch × 2 | Bounds processor memory |
| Container memory | 256 MB – 1 GB | Enforced in the production overlay |
| Request body | 64 KB | There is no write endpoint; large bodies are rejected at the edge |

Containers run as a non-root user. In production only Caddy is exposed (80/443) with automatic
Let's Encrypt certificates, HSTS and CSP; the API and RabbitMQ console are not reachable from outside.

## Measured performance

Measured on a development laptop against live traffic — indicative, not a benchmark:

| Metric | Value |
|---|---|
| Aircraft per poll | ~300 |
| Observations stored | 38,000+ |
| Duplicates rejected | 11,600+ |
| Batch write latency (p50 / p95) | 31 ms / 56 ms |
| Query cost measured from source | 3 credits per call |

## Deployment

`docker-compose.prod.yml` adds Caddy for automatic HTTPS and applies container memory limits:

```bash
DOMAIN=your.domain docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

`infra/` provisions the whole stack on Oracle Cloud's always-free tier with Terraform — VCN, subnet,
security list and an Ampere A1 instance, with cloud-init installing Docker on first boot.

The infrastructure code is written to make accidental spending impossible: variable validations reject
anything above the free quota, `preflight.ps1` audits the plan against an allow-list of free resource
types and blocks `apply` if anything else appears, a zero-spend budget alert is provisioned, and
`destroy.ps1` tears everything down in one command.

## Project layout

```
├── services/
│   ├── collector_opensky/   # external source → common model → queue
│   ├── processor/           # dedup, batch persistence, live publish, retention
│   └── api/                 # REST + WebSocket + metrics
├── shared/telemetry_common/ # Observation model, config, logging, queue topology
├── db/init.sql              # schema, indexes, uniqueness constraint
├── web/                     # live map (Leaflet, no build step)
├── infra/                   # Terraform for Oracle Cloud + cost guardrails
├── scripts/                 # smoke tests and diagnostics
└── docker-compose*.yml
```

## Roadmap

- [ ] Second source (vessel AIS) and cross-source correlation
- [ ] Elasticsearch for full-text and geospatial search
- [ ] Load testing with published latency distributions
- [ ] Prometheus metrics endpoint

## License

MIT — see [LICENSE](LICENSE).
