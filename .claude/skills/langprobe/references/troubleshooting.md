# Troubleshooting — langprobe instrumentation

Known papercuts when pointing an OTLP-instrumented repo at langprobe. Ordered by how
often each one is the actual cause. This file is meant to be walked top-to-bottom by
an agent debugging "my traces aren't showing up in /runs".

---

## Missing traces — diagnostic order

1. **Is the exporter pointed at `/v1/traces`?** The path is
   `<host>/v1/traces`. A common miss is exporting to the bare host, or to `/v1/runs`
   (that's the native/LangSmith-shim JSON path, not OTLP). If you use the shared
   bootstrap, `OTEL_EXPORTER_OTLP_ENDPOINT` must be the **bare host** and the code
   appends `/v1/traces`. If you use OTel's own env autoconfig, set
   `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<host>/v1/traces` (the full path).

2. **Did the exporter get a 202?** langprobe acks OTLP with **`202 Accepted`**. Smoke
   test the endpoint+auth without touching the app:
   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' \
     -X POST "$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces" \
     -H "authorization: Bearer $LANGPROBE_API_KEY" \
     -H 'content-type: application/json' \
     -d '{"resourceSpans":[]}'
   ```
   `202` = endpoint + auth good. `401` → §Auth. `400` → malformed OTLP (an empty
   `resourceSpans` array still 202s; a real payload missing `resourceSpans` 400s).

3. **Was the instrumentor installed before work ran?** Call `.instrument()` (and
   build the provider) **before** the framework executes any LLM call, at process
   startup. Instrumenting after the first call means early spans are lost.

4. **Did the batch flush?** `BatchSpanProcessor` buffers and exports on an interval.
   A **script that exits** before the interval drops its spans — call
   `provider.force_flush()` then `provider.shutdown()` before exit. See §Flush.

5. **Right framework instrumentor?** If you see the app's own spans but no LLM/tool
   detail, the framework instrumentor isn't installed or doesn't match. For CrewAI /
   LiteLLM-routed apps you also need the **provider** instrumentor — see §Wrong /
   missing span kinds.

If all five pass, the run is in the pipeline: ingest-api enqueues to Redis and the
worker drains to ClickHouse. Give it a few seconds and refresh `/runs`. If it's still
missing, check `docker compose logs ingest-api` / `ingest-worker` per
`docs/getting-started.md`.

---

## Auth — 401 on `/v1/traces`

langprobe API keys have the exact form `lt_<public_id>.<secret>` (a `lt_` prefix,
then `public_id`, a literal `.`, then `secret`). It travels in **either** header:

```
Authorization: Bearer lt_<public_id>.<secret>
```
or
```
X-Api-Key: lt_<public_id>.<secret>
```

Common 401 causes:

- **Missing `lt_` prefix** or **missing the `.`** between public id and secret →
  "invalid api key format". You truncated or mis-copied the key.
- **URL-encoding in `*_HEADERS`.** If you set the key via
  `OTEL_EXPORTER_OTLP_TRACES_HEADERS`, the SDK parses `key=value` pairs — the space
  in `Bearer <key>` must be URL-encoded as `%20`:
  `OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer%20lt_...."`. A literal
  space corrupts the header and yields 401.
- **Wrong scope.** The key must have `ingest:write`. Mint a fresh one in the UI if
  unsure.
- **Revoked key.** Revoked keys 401 immediately, by design.

---

## Protobuf vs JSON — both are fine now

You do **not** need to force JSON. The endpoint accepts **both**
`application/x-protobuf` (what the stock `opentelemetry-exporter-otlp-proto-http`
`OTLPSpanExporter` sends) and `application/json`. If you inherited advice to use a
JSON exporter or a `proto-http` → JSON workaround, drop it — the stock protobuf
exporter is the recommended path and works out of the box.

(If you're on `opentelemetry-exporter-otlp-proto-grpc`, that speaks gRPC on a
different transport — swap it for the **http** exporter. langprobe's `/v1/traces` is
HTTP/1.1.)

---

## Wrong / missing span kinds

Symptom: a run shows only an agent/workflow parent with no `llm`-kind child (no
model, no tokens), or spans render as `chain` when they should be `tool`/`retriever`.

- **CrewAI / LiteLLM-routed apps**: the framework instrumentor gives structure but
  the LLM call goes through the provider SDK. Install the **provider** instrumentor
  that matches `crewai.LLM(model=...)`:
  - `gpt-4o` → `openinference-instrumentation-openai`
  - `claude-...` → `openinference-instrumentation-anthropic`
  - `gemini/...` / `azure/...` → `openinference-instrumentation-litellm`
  Without it, the LLM call is untraced — no `llm` span.
- **Custom (hand-rolled) spans**: langprobe classifies by `openinference.span.kind`.
  If you didn't set it, it falls back to `gen_ai.operation.name`, then the span name,
  and lands on `chain` by default. Set `openinference.span.kind` (UPPERCASE) on your
  span — see `decorators-and-traces.md` §1 for the full kind table.
- **Don't invent new kinds.** Only the canonical set is understood:
  `LLM, CHAIN, TOOL, AGENT, RETRIEVER, EMBEDDING, RERANKER, GUARDRAIL, EVALUATOR,
  WORKFLOW, TASK`. Anything else falls back to the name heuristic.

---

## Missing tokens / cost

Token counts (and therefore derived cost) come from `llm.token_count.*` /
`gen_ai.usage.*` attributes on `llm`-kind spans. If the run shows no tokens:

- The framework/provider instrumentor didn't capture usage — confirm you installed
  the **provider** instrumentor (not just the framework one), and that the provider
  returned usage (some streaming modes omit it unless you request it).
- For **custom** LLM spans you must set the token attributes yourself:
  `llm.token_count.prompt`, `llm.token_count.completion` (langprobe sums them for the
  total if `llm.token_count.total` is absent).
- Cost is **derived downstream** from model + tokens. There is no cost attribute to
  set on the span; get tokens + model right and cost follows.

---

## Ugly run names

The run's name and kind come from its **root span**. If runs show up as `POST`,
`task`, or a UUID:

- Wrap your entry point in one meaningfully-named root span
  (`checkout.support_agent`) with `openinference.span.kind = "WORKFLOW"`, so every
  request/run gets a single readable root grouping its children. See
  `decorators-and-traces.md` §5.
- For auto-instrumented frameworks you usually get good names for free — an ugly name
  means the true root span is an HTTP-server span or similar. Add your own named
  workflow span above it.

---

## Flush / shutdown gotcha

- **Scripts**: call `provider.force_flush()` then `provider.shutdown()` before exit,
  or the last batch never leaves the process.
- **Servers**: build the provider **once** at startup and flush/shutdown **once** at
  shutdown (FastAPI `lifespan`, or an `atexit` hook) — **never per request**. Flushing
  per request sends one HTTP batch per request instead of one per interval, adding
  latency and risking throttling. `BatchSpanProcessor` already batches on an interval.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

provider = build_provider("my-api")  # + instrument() at import/startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    provider.force_flush()
    provider.shutdown()


app = FastAPI(lifespan=lifespan)
```

---

## gRPC endpoint mistake

langprobe's `/v1/traces` is **HTTP**. If you installed
`opentelemetry-exporter-otlp-proto-grpc` (or set `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`),
the exporter tries gRPC and never reaches the HTTP endpoint. Use
`opentelemetry-exporter-otlp-proto-http` and its `OTLPSpanExporter`.
