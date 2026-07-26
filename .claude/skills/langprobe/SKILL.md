---
name: langprobe
description: >
  langprobe is a self-hosted LLM/agent observability platform — the real debugger
  for agents. Use this skill to instrument an arbitrary repo to send traces to
  langprobe: detect the framework, add the right OpenInference instrumentor + an
  OTLP exporter, point OTEL_EXPORTER_OTLP_ENDPOINT at the user's langprobe host,
  set the API-key header, and verify a trace lands in /runs. Covers CrewAI, DSPy,
  Pydantic AI, OpenAI Agents, LlamaIndex, and any OTel-instrumented app.
---

# langprobe — Agent Skill

langprobe ingests traces over **plain OTLP/HTTP** at `POST /v1/traces`. There is no
proprietary SDK to adopt: you add a stock OpenInference instrumentor + the stock
`opentelemetry-exporter-otlp-proto-http` exporter, point it at the user's langprobe
host, and traces flow. Runs then render in the product's `/runs` view — the surface
you use to debug an agent when it goes sideways at 2 a.m.

This skill is a **hands-free procedure**: follow it top to bottom to instrument a
repo you've never seen before. Read the reference files when a step needs detail.

---

## The one thing to get right

langprobe speaks OTLP. Every integration is the same three moving parts:

1. **An OpenInference instrumentor** for the framework in the repo (it emits spans
   with the right `openinference.span.kind`, model, token, and I/O attributes).
2. **An OTLP/HTTP exporter** (`opentelemetry-exporter-otlp-proto-http`) wired into a
   `TracerProvider` with a `BatchSpanProcessor`.
3. **Config** pointing the exporter at `<LANGPROBE_HOST>/v1/traces` with the API key
   in a header.

Get those three right and you are done. The rest is framework detection.

> **The endpoint accepts BOTH `application/x-protobuf` AND `application/json`.**
> A stock `OTLPSpanExporter` from `opentelemetry-exporter-otlp-proto-http` sends
> protobuf — that works now, out of the box. You do NOT need to force JSON.

---

## Procedure (follow in order)

### Step 0 — Gather the two facts you need

You need the user's langprobe **host** and an **API key**. Ask if not provided.

- **Host**: the ingest-api base URL, e.g. `http://localhost:7080` for a local
  self-host, or `https://ingest.<their-domain>` in prod. The trace endpoint is
  always `<host>/v1/traces`.
- **API key**: a `lt_<public_id>.<secret>` value minted in the langprobe UI under
  **API keys** (scope `ingest:write`). See `docs/getting-started.md` §3.

### Step 1 — Detect the framework

Grep the repo's dependency manifest (`pyproject.toml`, `requirements.txt`,
`poetry.lock`) and imports. Map to the instrumentor:

| Detected | OpenInference instrumentor package | Reference |
|---|---|---|
| `crewai` | `openinference-instrumentation-crewai` (+ the LLM provider's instrumentor) | framework-integrations §CrewAI |
| `dspy` | `openinference-instrumentation-dspy` | framework-integrations §DSPy |
| `pydantic_ai` / `pydantic-ai` | `openinference-instrumentation-pydantic-ai` | framework-integrations §Pydantic AI |
| `agents` (OpenAI Agents SDK) | `openinference-instrumentation-openai-agents` | framework-integrations §OpenAI Agents |
| `llama_index` / `llama-index` | `openinference-instrumentation-llama-index` | framework-integrations §LlamaIndex |
| bare `openai` / `anthropic` | `openinference-instrumentation-openai` / `-anthropic` | framework-integrations §Bare provider |
| already has OTLP exporter | nothing — just repoint the endpoint | Step 4 |

If none match but the repo already builds a `TracerProvider` (search for
`OTLPSpanExporter` or `OTEL_EXPORTER_OTLP_ENDPOINT`), skip to **Step 4** — you only
need to repoint the existing exporter.

### Step 2 — Install the instrumentor + exporter

```bash
pip install \
  openinference-instrumentation-<framework> \
  opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http
```

Add the same packages to the repo's dependency manifest so the change persists.

### Step 3 — Add the bootstrap module

Create a single `langprobe_tracing.py` (or fold into the app's startup) that builds
the provider, wires the exporter, and installs the instrumentor. This is the
canonical shape — the only thing that changes per framework is the last two lines:

```python
# langprobe_tracing.py
import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


def setup_tracing(service_name: str = "my-agent") -> None:
    endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"].rstrip("/") + "/v1/traces"
    # langprobe auth: API key travels in the Authorization: Bearer header.
    # (X-Api-Key is also accepted — see references/troubleshooting.md.)
    headers = {"Authorization": f"Bearer {os.environ['LANGPROBE_API_KEY']}"}

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
    )
    trace.set_tracer_provider(provider)

    # --- framework instrumentor (swap for the detected framework) ---
    from openinference.instrumentation.crewai import CrewAIInstrumentor

    CrewAIInstrumentor().instrument(tracer_provider=provider)
```

Call `setup_tracing()` **once at startup, before** the framework runs any work (for
scripts: the first lines of `__main__`; for servers: app startup / lifespan). See
`references/framework-integrations.md` for the exact instrumentor class per
framework, and `references/troubleshooting.md` for the flush-on-exit gotcha.

### Step 4 — Set the config

Export the two variables (or write them into the repo's `.env` / process manager):

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:7080"   # the langprobe HOST, no /v1/traces
export LANGPROBE_API_KEY="lt_<public_id>.<secret>"
```

The bootstrap in Step 3 appends `/v1/traces` itself, so `OTEL_EXPORTER_OTLP_ENDPOINT`
holds the bare host. (If you instead rely on the OTel SDK's own env-var wiring, set
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<host>/v1/traces` and
`OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer%20<key>"` — note the URL-
encoded space.)

### Step 5 — Verify a trace lands in /runs

Run the app once so it makes at least one LLM call, then confirm:

1. The exporter returned **202** (langprobe acks OTLP with `202 Accepted`). A quick
   smoke test without touching the app:

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' \
     -X POST "$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces" \
     -H "authorization: Bearer $LANGPROBE_API_KEY" \
     -H 'content-type: application/json' \
     -d '{"resourceSpans":[]}'
   # expect: 202
   ```

   A `401` means the key/header is wrong (see troubleshooting §Auth). A `400` on a
   real payload means malformed OTLP; an empty `resourceSpans` still 202s.

2. Open the langprobe web UI and go to **/runs**. Your run appears within seconds
   (ingest-api enqueues to Redis; the worker drains to ClickHouse). Click in to see
   the span tree, model, tokens, cost, and I/O.

If nothing shows up, walk `references/troubleshooting.md` §"Missing traces" top to
bottom — it is ordered by likelihood.

---

## What langprobe reads off your spans

langprobe classifies each span by its `openinference.span.kind` (falling back to
OTel `gen_ai.operation.name`, then the span name). The full kind set it understands:

`llm`, `chain`, `tool`, `agent`, `retriever`, `embedding`, `reranker`, `guardrail`,
`evaluator`, `workflow`, `task`.

The first four map to the categorical **kind badges** in the trace view
(llm / tool / retriever→retr / chain). OpenInference instrumentors set these
automatically — you rarely set them by hand. When you DO emit custom spans (a
hand-rolled tool, guardrail, evaluator, reranker), set `openinference.span.kind`
yourself so the run renders with the right kind — see
`references/decorators-and-traces.md`.

**End-user identity.** langprobe stamps a run with the human the agent served when
the trace's **root span** carries an `enduser.id` attribute (fallbacks: `user.id`,
`session.user.id`). Set it on your root span to group and filter runs by end user in
`/runs`. See `references/decorators-and-traces.md` §End-user identity.

**Tokens & cost.** Token counts come from `llm.token_count.*` / `gen_ai.usage.*`
attributes, which OpenInference instrumentors populate. Cost is derived downstream.
If tokens are missing, the instrumentor didn't capture them — see troubleshooting.

---

## Reference docs

- **Framework recipes** (CrewAI, DSPy, Pydantic AI, OpenAI Agents, LlamaIndex, bare
  providers) → [`references/framework-integrations.md`](references/framework-integrations.md)
- **Custom spans, span kinds, end-user identity, naming** →
  [`references/decorators-and-traces.md`](references/decorators-and-traces.md)
- **Troubleshooting** the known papercuts →
  [`references/troubleshooting.md`](references/troubleshooting.md)

## Key facts

| Thing | Value |
|---|---|
| Trace endpoint | `POST <host>/v1/traces` (OTLP/HTTP) |
| Content types accepted | `application/x-protobuf` **and** `application/json` |
| Auth header | `Authorization: Bearer lt_<public_id>.<secret>` (or `X-Api-Key: <key>`) |
| Ack | `202 Accepted` |
| Where runs show up | the web UI's `/runs` view |
| Local host default | `http://localhost:7080` (ingest-api) |
| Full local setup | `docs/getting-started.md` |
