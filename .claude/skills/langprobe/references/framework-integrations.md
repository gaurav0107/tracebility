# Framework Integrations — langprobe

langprobe ingests **plain OTLP/HTTP** at `POST <host>/v1/traces`. Every integration
below is the same shape: install an **OpenInference instrumentor** for the framework
+ the stock **`opentelemetry-exporter-otlp-proto-http`** exporter, wire them into a
`TracerProvider`, point the endpoint at the langprobe host, and set the API key
header. There is no langprobe-specific runtime library.

The endpoint accepts **both** `application/x-protobuf` (what the stock exporter
sends) and `application/json`. Protobuf works out of the box — do not force JSON.

---

## 0. The shared bootstrap

Every recipe reuses this exact provider/exporter wiring. Only the **instrumentor**
lines at the bottom change per framework.

```python
# langprobe_tracing.py
import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


def build_provider(service_name: str) -> TracerProvider:
    endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"].rstrip("/") + "/v1/traces"
    headers = {"Authorization": f"Bearer {os.environ['LANGPROBE_API_KEY']}"}
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
    )
    trace.set_tracer_provider(provider)
    return provider
```

Config (host is the bare ingest-api base URL — the bootstrap appends `/v1/traces`):

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:7080"
export LANGPROBE_API_KEY="lt_<public_id>.<secret>"
```

**Scripts** must flush before exit or the last batch is lost:

```python
provider = build_provider("my-agent")
# ... instrument + run ...
provider.force_flush()
provider.shutdown()
```

**Servers**: build the provider once at startup; flush/shutdown once at shutdown
(FastAPI lifespan / atexit) — never per request. See `troubleshooting.md` §Flush.

---

## 1. CrewAI

- **Instrumentor**: `openinference-instrumentation-crewai`
- **Also install the LLM provider's instrumentor.** CrewAI dispatches the actual
  LLM call through LiteLLM/the provider SDK; the CrewAI instrumentor gives you the
  Agent/Task/Crew structure, but the `llm`-kind span (with model + tokens) comes
  from the provider instrumentor. Match it to your `crewai.LLM(model=...)`:
  - OpenAI (`gpt-4o`, ...) → `openinference-instrumentation-openai`
  - Anthropic (`claude-...`) → `openinference-instrumentation-anthropic`
  - LiteLLM-routed (`gemini/...`, `azure/...`) → `openinference-instrumentation-litellm`
- **Install**:
  ```bash
  pip install openinference-instrumentation-crewai openinference-instrumentation-openai \
    opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
  ```

```python
from langprobe_tracing import build_provider
from openinference.instrumentation.crewai import CrewAIInstrumentor
from openinference.instrumentation.openai import OpenAIInstrumentor

provider = build_provider("crew-app")
CrewAIInstrumentor().instrument(tracer_provider=provider)
OpenAIInstrumentor().instrument(tracer_provider=provider)  # provider match!

from crewai import Agent, Task, Crew

analyst = Agent(role="Market Analyst", goal="Analyze trends",
                backstory="10y experience", verbose=False)
task = Task(description="Analyze the AI chip market",
            expected_output="A short report", agent=analyst)
crew = Crew(agents=[analyst], tasks=[task])
print(crew.kickoff())

provider.force_flush()
provider.shutdown()
```

> If you install only the CrewAI instrumentor, the run shows the Agent/Task tree but
> no `llm`-kind child (no model, no tokens). That missing-provider-instrumentor case
> is the #1 CrewAI papercut — see `troubleshooting.md` §"Wrong / missing span kinds".

---

## 2. DSPy

- **Instrumentor**: `openinference-instrumentation-dspy`
- Captures module runs (`chain`), LM calls (`llm`), and retrieval (`retriever`).
- **Install**:
  ```bash
  pip install openinference-instrumentation-dspy \
    opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
  ```

```python
from langprobe_tracing import build_provider
from openinference.instrumentation.dspy import DSPyInstrumentor

provider = build_provider("dspy-app")
DSPyInstrumentor().instrument(tracer_provider=provider)

import dspy

dspy.configure(lm=dspy.LM("openai/gpt-4o"))


class QA(dspy.Module):
    def __init__(self):
        super().__init__()
        self.answer = dspy.Predict("question -> answer")

    def forward(self, question: str):
        return self.answer(question=question)


print(QA()(question="Explain DSPy in one sentence.").answer)

provider.force_flush()
provider.shutdown()
```

---

## 3. Pydantic AI

- **Instrumentor**: `openinference-instrumentation-pydantic-ai` (Pydantic AI also
  emits OTel spans natively via `logfire`-style instrumentation; the OpenInference
  package normalizes them to `openinference.span.kind`).
- **Install**:
  ```bash
  pip install openinference-instrumentation-pydantic-ai \
    opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
  ```

```python
from langprobe_tracing import build_provider
from openinference.instrumentation.pydantic_ai import PydanticAIInstrumentor

provider = build_provider("pydantic-ai-app")
PydanticAIInstrumentor().instrument(tracer_provider=provider)

from pydantic_ai import Agent

agent = Agent("openai:gpt-4o", system_prompt="You are concise.")
result = agent.run_sync("What is the capital of France?")
print(result.output)

provider.force_flush()
provider.shutdown()
```

> If you use Pydantic AI's own `Agent(instrument=True)` OTel wiring instead, you can
> skip the OpenInference package and just repoint `OTEL_EXPORTER_OTLP_ENDPOINT` at
> langprobe — but the OpenInference instrumentor gives cleaner kind badges.

---

## 4. OpenAI Agents SDK

- **Instrumentor**: `openinference-instrumentation-openai-agents`
- Captures the agent loop, handoffs, tool calls, and the underlying LLM calls.
- **Install**:
  ```bash
  pip install openinference-instrumentation-openai-agents \
    opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
  ```

```python
from langprobe_tracing import build_provider
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

provider = build_provider("openai-agents-app")
OpenAIAgentsInstrumentor().instrument(tracer_provider=provider)

from agents import Agent, Runner

agent = Agent(name="Assistant", instructions="You are helpful and brief.")
result = Runner.run_sync(agent, "Give me a one-line summary of OTLP.")
print(result.final_output)

provider.force_flush()
provider.shutdown()
```

---

## 5. LlamaIndex

- **Instrumentor**: `openinference-instrumentation-llama-index`
- Captures queries, retrieval (`retriever`), reranking (`reranker`), embeddings
  (`embedding`), and the LLM synthesis step — the full RAG span tree.
- **Install**:
  ```bash
  pip install openinference-instrumentation-llama-index \
    opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
  ```

```python
from langprobe_tracing import build_provider
from openinference.instrumentation.llama_index import LlamaIndexInstrumentor

provider = build_provider("llamaindex-app")
LlamaIndexInstrumentor().instrument(tracer_provider=provider)

from llama_index.core import VectorStoreIndex, Document

index = VectorStoreIndex.from_documents([Document(text="langprobe speaks OTLP.")])
resp = index.as_query_engine().query("What protocol does langprobe speak?")
print(resp)

provider.force_flush()
provider.shutdown()
```

---

## 6. Bare provider (no framework)

If the repo calls `openai` / `anthropic` directly with no agent framework, use the
provider instrumentor on its own. A single instrumented call produces a complete
`llm`-kind run.

- **Install** (OpenAI shown; swap `-anthropic` as needed):
  ```bash
  pip install openinference-instrumentation-openai \
    opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
  ```

```python
from langprobe_tracing import build_provider
from openinference.instrumentation.openai import OpenAIInstrumentor

provider = build_provider("bare-openai")
OpenAIInstrumentor().instrument(tracer_provider=provider)

from openai import OpenAI

client = OpenAI()
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "One-line definition of a trace."}],
)
print(resp.choices[0].message.content)

provider.force_flush()
provider.shutdown()
```

---

## 7. Already OTel-instrumented app

If the repo already builds a `TracerProvider` with an `OTLPSpanExporter` (or relies
on the OTel env-var autoconfig), do **not** add a second provider. Just repoint it:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://localhost:7080/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer%20lt_<public_id>.<secret>"
```

Note the URL-encoded space (`%20`) in the headers value — the OTel SDK parses
`key=value` pairs and a literal space will corrupt the header. langprobe accepts the
protobuf these exporters send, so no exporter swap is needed.

---

## Provider-key cheat sheet (CrewAI / LiteLLM routing)

| `crewai.LLM(model=...)` / LiteLLM `model=` | Provider instrumentor to add |
|---|---|
| `gpt-4o`, `gpt-4o-mini` | `openinference-instrumentation-openai` |
| `claude-...` | `openinference-instrumentation-anthropic` |
| `gemini/...` | `openinference-instrumentation-litellm` |
| `azure/...` | `openinference-instrumentation-litellm` |

Pick wrong and the LLM call succeeds but produces no `llm`-kind span — the run shows
only the agent parent. See `troubleshooting.md`.
