# langprobe

**The real debugger for agents.**

Self-hosted LLM observability + eval-rigor + agent-replay, in your VPC. Most tools in this space are dashboards for humans. The langprobe wedge is two things they don't do:

- **Replay.** Open a broken run, edit a prompt / model / tool, re-run it, and diff what changed — span by span, with a determinism verdict. The debugger you reach for at 2am.
- **Agent-first.** The same surface is built for agents, not just people. Token-budgeted, LLM-legible run views over REST and MCP, so an agent can debug an agent: find the failed run, read its salient slice, replay an edit, read the diff.

Plus eval-rigor that tells you whether your judges are trustworthy (schema-adherence, test-retest stability, inter-judge agreement), and LangSmith-compatible ingestion so migrating is import-and-go.

> Status: pre-1.0, actively built. Tracing, evals, prompts, playground, replay (span-level), and the agent surface are working; the client-side replay harness (true control-flow re-execution) and a public SaaS gate are on the roadmap. Self-host it today with `docker compose`.

## What's in here

```
services/
  ingest-api/         # OTel GenAI + LangSmith shim ingest, FastAPI
  api/                # auth, RBAC, project/dataset/prompt CRUD, FastAPI
  ingest-worker/      # Redis -> ClickHouse batch writer
  eval-orchestrator/  # judge fan-out, sampling, RCA
web/                  # Next.js + TypeScript product UI
schemas/
  postgres/           # control-plane schema (orgs, users, audit log, ...)
  clickhouse/         # data-plane schema (runs, spans, evals, replays)
packages/
  sdk-python/         # langprobe Python SDK
  sdk-typescript/     # langprobe TypeScript SDK
infra/                # docker-compose, k8s manifests
designs/              # UI mockups
DESIGN.md             # design system source of truth
CLAUDE.md             # project guide for agents
TODOS.md              # phased build list
```

## Storage stack

- **Postgres** — control plane (orgs, users, projects, api keys, audit log)
- **ClickHouse** — data plane (runs, spans, eval scores, replay captures)
- **Redis** — ingest queue, rate-limit token buckets, cache
- **Object storage** — large attachments (S3/MinIO)

## Quick start (when scaffold is wired)

```bash
docker compose -f infra/docker-compose.yml up -d
open http://localhost:3000
```

The setup wizard will walk you through creating the first org, root user, and API key.

## License

Apache 2.0. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). DCO sign-off is required.
