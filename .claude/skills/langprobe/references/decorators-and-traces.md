# Custom Spans, Kinds, and Identity — langprobe

langprobe has no proprietary decorator API. You instrument with **plain OpenTelemetry
+ OpenInference semantic conventions**, and langprobe reads those conventions off the
wire at `POST /v1/traces`. This file covers the attributes langprobe actually reads,
so your hand-rolled spans render correctly in `/runs`.

For a framework in the repo, prefer the framework's OpenInference instrumentor (see
`framework-integrations.md`) — it sets all of this for you. Reach for manual spans
only for code the instrumentor doesn't cover (a hand-rolled tool, a custom
retriever/reranker, a guardrail, an evaluator).

---

## 1. The span kinds langprobe understands

langprobe classifies each span in this order:

1. `openinference.span.kind` attribute (primary signal)
2. OTel `gen_ai.operation.name` (`chat`/`completion` → `llm`, `embeddings` →
   `embedding`, `tool_calling` → `tool`)
3. Span-name heuristic (`embed*` → embedding, `retriev*`/`search*` → retriever,
   `tool.*` → tool, `agent.*` → agent, `chat.*`/`llm.*` → llm)

The full canonical kind set:

| `openinference.span.kind` | langprobe kind | Badge in trace view |
|---|---|---|
| `LLM` | `llm` | llm (amber) |
| `TOOL` | `tool` | tool (cyan) |
| `RETRIEVER` | `retriever` | retr (green) |
| `CHAIN` | `chain` | chain (indigo) |
| `AGENT` | `agent` | — |
| `EMBEDDING` | `embedding` | — |
| `RERANKER` | `reranker` | — |
| `GUARDRAIL` | `guardrail` | — |
| `EVALUATOR` | `evaluator` | — |
| `WORKFLOW` | `workflow` | — |
| `TASK` | `task` | — |

The first four (llm / tool / retriever / chain) render as the categorical **kind
badges** in the trace view; the others are tracked and filterable but not badged.

> Set `openinference.span.kind` in UPPERCASE — langprobe uppercases the incoming
> value before matching, but the OpenInference convention is uppercase.

---

## 2. A custom span, done right

```python
from opentelemetry import trace

tracer = trace.get_tracer("my-agent")

with tracer.start_as_current_span("web_search") as span:
    span.set_attribute("openinference.span.kind", "TOOL")
    span.set_attribute("input.value", query)
    results = search_api.search(query)
    span.set_attribute("output.value", results)
```

`start_as_current_span` makes the span a child of whatever span is active, so nesting
is automatic — the run's tree in `/runs` follows your call structure.

---

## 3. Attributes langprobe reads (per kind)

langprobe reads a small, versioned set of attribute keys (see the ingest shim's
`attribute_mapping.json`). Use the OpenInference keys — they're the first in each
fallback chain.

### Model & sampling (llm spans)
| Purpose | Keys (first match wins) |
|---|---|
| Model | `llm.model_name`, `gen_ai.request.model`, `gen_ai.response.model`, `model` |
| Temperature | `llm.invocation_parameters.temperature`, `gen_ai.request.temperature`, `temperature` |

### Tokens (llm spans → aggregated into the run)
| Purpose | Keys |
|---|---|
| Prompt tokens | `llm.token_count.prompt`, `gen_ai.usage.input_tokens` |
| Completion tokens | `llm.token_count.completion`, `gen_ai.usage.output_tokens` |
| Total tokens | `llm.token_count.total` (else prompt+completion) |

Set these on your `llm`-kind spans and they roll up to the run total; cost is derived
downstream. If you don't set them, the run shows no tokens/cost.

### Inputs & outputs (any kind)
| Purpose | Keys |
|---|---|
| Input | `input.value`, `llm.input_messages`, `gen_ai.prompt` |
| Output | `output.value`, `llm.output_messages`, `gen_ai.completion` |

Non-string values are JSON-serialized. For LLM spans, prefer the structured
`llm.input_messages` / `llm.output_messages` shape the OpenInference instrumentors
emit — the trace view renders those as message threads.

### Status
Set span status via the OTel API (`span.set_status(Status(StatusCode.ERROR))`); a
single error span marks the whole run `error` in `/runs`.

---

## 4. End-user identity

langprobe stamps a run with **the human the agent served** — distinct from the
operator / API key. It reads this from the **root span** of the trace only, via the
ordered fallback chain:

| Priority | Attribute |
|---|---|
| 1 | `enduser.id` |
| 2 | `user.id` |
| 3 | `session.user.id` |

Set it on your top-level (root) span so runs can be grouped and filtered by end user
in `/runs`:

```python
with tracer.start_as_current_span("handle_request") as root:
    root.set_attribute("openinference.span.kind", "WORKFLOW")
    root.set_attribute("enduser.id", current_user_id)   # the human, not the API key
    run_agent(...)
```

Because langprobe only reads `enduser.id` off the root span, set it on the outermost
span — not on a nested LLM/tool span, where it will be ignored.

---

## 5. Naming spans so runs are readable

The run's name and kind come from its **root span**. A run named `POST` or
`task` is useless in `/runs`. Give the outermost span a meaningful name:

```python
with tracer.start_as_current_span("checkout.support_agent") as root:
    root.set_attribute("openinference.span.kind", "WORKFLOW")
    ...
```

For framework-instrumented apps you usually get good names for free; wrap the app's
entry point in one named `WORKFLOW` span when you want a single, meaningful run root
grouping several calls. Avoid HTTP-verb-only names — see `troubleshooting.md`
§"Ugly run names".

---

## 6. Async

Plain OTel context propagation works across `async def`. `start_as_current_span`
inside a coroutine still nests under the active span, and `BatchSpanProcessor` exports
off-thread, so it won't block the event loop. Just remember to `force_flush()` /
`shutdown()` the provider at application shutdown (lifespan / atexit), not per request.
