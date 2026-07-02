# Getting started with langprobe

Self-host the whole thing locally, then send your first trace. Five minutes,
two terminals.

## 1. Boot the stack

```sh
cp infra/.env.example infra/.env
# generate the session secret (no default — by design)
echo "SESSION_SECRET=$(openssl rand -hex 32)" >> infra/.env

docker compose -f infra/docker-compose.yml up --build
```

Wait for the `web` and `api` containers to settle. You should see:

| service    | url                       |
| ---------- | ------------------------- |
| web        | http://localhost:7090     |
| api        | http://localhost:7081     |
| ingest-api | http://localhost:7080     |

## 2. First-run setup

Open `http://localhost:7090`. The web shell will route an unbootstrapped
instance to the setup wizard. Behind the scenes it calls:

```sh
curl -X POST http://localhost:7081/v1/setup \
  -H 'content-type: application/json' \
  -d '{"email": "you@example.com", "password": "change-me-later", "org_name": "Default"}'
```

That single call creates a root user, a default org, a default workspace, and
a default project — and hands you back a session cookie. Once it's done, the
endpoint locks itself: future POSTs return 409.

## 3. Mint an API key

In the UI, open **API keys**, then **New key** → name it `local-dev`, scope
`ingest:write`. Copy the `lt_...` value once. We hash it on save; lose it and
you mint another.

## 4. Send a trace

```sh
export LANGPROBE_API_KEY="lt_<public_id>.<secret>"

curl -X POST http://localhost:7080/v1/runs \
  -H "authorization: Bearer $LANGPROBE_API_KEY" \
  -H 'content-type: application/json' \
  -d '{
    "sdk": "curl",
    "runs": [{
      "run_id": "00000000-0000-0000-0000-000000000001",
      "name": "hello.world",
      "kind": "agent",
      "status": "ok",
      "start_time": "2026-05-25T00:00:00Z",
      "end_time": "2026-05-25T00:00:00.842Z",
      "inputs": "ping",
      "outputs": "pong"
    }],
    "spans": []
  }'
```

You'll get a `202 Accepted`. The ingest-api enqueues to Redis; the worker
drains and writes to ClickHouse. The Overview page on the web UI will show
the run within seconds.

## 5. Already on LangSmith?

Point your existing client at us:

```sh
export LANGSMITH_ENDPOINT=http://localhost:7080
export LANGSMITH_API_KEY="lt_<public_id>.<secret>"
```

The native `RunCreate` / `RunUpdate` shapes are translated by the shim and
land in the same queue.

## Instrument with your coding agent

Already running an agent framework (CrewAI, DSPy, Pydantic AI, OpenAI Agents,
LlamaIndex) or any OTel-instrumented app? You don't need a langprobe SDK — langprobe
ingests plain OTLP/HTTP at `/v1/traces`.

Point a coding agent (Claude Code, etc.) at the bundled skill and let it do the
wiring: `.claude/skills/langprobe/SKILL.md`. It detects the framework, adds the right
OpenInference instrumentor + OTLP exporter, sets `OTEL_EXPORTER_OTLP_ENDPOINT` to your
langprobe host + `/v1/traces`, sets the API-key header, and verifies a trace appears
in `/runs`. Framework recipes and troubleshooting live in
`.claude/skills/langprobe/references/`.

## What's next

- Datasets + replay (Phase 12+) — re-run a captured trace against a different
  prompt or model and diff the outputs.
- Eval rigor — panel-of-judges with inter-rater agreement, not single-judge
  scores. (See `RESEARCH.md` for what we're building toward and why.)
- Self-host hardening — TLS termination, S3-compatible blob spill for large
  inputs, Postgres replica.

## Where things live

- Redis stream: `langprobe:ingest:v1` (consumer group `ingest`)
- DLQ: `langprobe:ingest:v1:dlq` (only after `max_deliveries` redeliveries)
- ClickHouse tables: `run`, `span`, `eval_score`, `eval_aggregate`,
  `replay_capture`, `replay_run`
- Postgres tables: `app_user`, `org`, `workspace`, `project`, `api_key`,
  `audit_log`

## When something breaks

- `docker compose logs ingest-api`
- `docker compose logs ingest-worker`
- `redis-cli xpending langprobe:ingest:v1 ingest` — see what's stuck
- `psql ... -c "select action, target_kind, created_at from audit_log order by created_at desc limit 20"` — every state-changing call leaves a row
