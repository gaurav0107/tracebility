"""Stateless instrumentation-guide recipe engine — "tell your agent to."

Pure guidance: no ClickHouse, no Postgres, no tenant data. Given a framework
slug, return the exact steps to wire it to langprobe over plain OTLP/HTTP — an
OpenInference instrumentor + the stock OTLP exporter, pointed at
``POST <host>/v1/traces``.

The recipes here are encoded from
``.claude/skills/langprobe/references/framework-integrations.md`` (the single
source of truth). There is no langprobe-specific runtime library; every recipe
is the same shape and only the instrumentor line changes.

Both the MCP tool (``instrument_my_repo``) and the HTTP twin
(``GET /v1/agent/instrument-guide``) call ``build_instrumentation_guide`` — one
logic, two transports, no state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The shared provider/exporter wiring every recipe reuses (verbatim in spirit
# from framework-integrations.md §0). Only the instrumentor lines change per
# framework.
BOOTSTRAP_CODE = """# langprobe_tracing.py
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


# Scripts must flush before exit or the last batch is lost:
#     provider = build_provider("my-agent")
#     # ... instrument + run ...
#     provider.force_flush()
#     provider.shutdown()
"""

# Stock exporter + SDK that every recipe needs alongside its instrumentor.
_BASE_INSTALL = (
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
)

ENDPOINT_NOTE = (
    "POST <host>/v1/traces — langprobe ingests plain OTLP/HTTP and accepts both "
    "application/x-protobuf (what the stock exporter sends) and application/json. "
    "Protobuf works out of the box; do not force JSON."
)

ENV = {
    "OTEL_EXPORTER_OTLP_ENDPOINT": (
        "The bare langprobe ingest-api base URL (e.g. http://localhost:7080). "
        "The bootstrap appends /v1/traces."
    ),
    "LANGPROBE_API_KEY": (
        "Your langprobe API key: lt_<public_id>.<secret>. Sent as Authorization: Bearer <key>."
    ),
}

VERIFY_STEP = (
    "Run your instrumented script/app once, then open /runs in the langprobe UI — "
    "the new run appears within a few seconds. To smoke-test the endpoint directly: "
    'curl -sS -o /dev/null -w "%{http_code}\\n" '
    '-H "Authorization: Bearer $LANGPROBE_API_KEY" '
    '"$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces" '
    "(a POST of a valid OTLP payload returns 200/202; a bad key returns 401)."
)


@dataclass(frozen=True)
class Recipe:
    """One framework's instrumentation recipe."""

    slug: str
    instrumentor_package: str
    instrumentor_code: str
    notes: str = ""
    # Extra pip packages beyond the framework instrumentor + base SDK/exporter.
    extra_packages: tuple[str, ...] = ()


RECIPES: dict[str, Recipe] = {
    "crewai": Recipe(
        slug="crewai",
        instrumentor_package="openinference-instrumentation-crewai",
        instrumentor_code="""from langprobe_tracing import build_provider
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
""",
        notes=(
            "Also install the LLM provider's instrumentor to get the llm-kind span "
            "(model + tokens): match crewai.LLM(model=...) — gpt-* -> "
            "openinference-instrumentation-openai, claude-* -> "
            "openinference-instrumentation-anthropic, gemini/azure -> "
            "openinference-instrumentation-litellm. With only the CrewAI "
            "instrumentor you get the Agent/Task tree but no llm child."
        ),
        # Default to the OpenAI provider instrumentor (matches the recipe above).
        extra_packages=("openinference-instrumentation-openai",),
    ),
    "dspy": Recipe(
        slug="dspy",
        instrumentor_package="openinference-instrumentation-dspy",
        instrumentor_code="""from langprobe_tracing import build_provider
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
""",
        notes="Captures module runs (chain), LM calls (llm), and retrieval (retriever).",
    ),
    "pydantic-ai": Recipe(
        slug="pydantic-ai",
        instrumentor_package="openinference-instrumentation-pydantic-ai",
        instrumentor_code="""from langprobe_tracing import build_provider
from openinference.instrumentation.pydantic_ai import PydanticAIInstrumentor

provider = build_provider("pydantic-ai-app")
PydanticAIInstrumentor().instrument(tracer_provider=provider)

from pydantic_ai import Agent

agent = Agent("openai:gpt-4o", system_prompt="You are concise.")
result = agent.run_sync("What is the capital of France?")
print(result.output)

provider.force_flush()
provider.shutdown()
""",
        notes=(
            "Pydantic AI also emits OTel spans natively; the OpenInference package "
            "normalizes them to openinference.span.kind for cleaner kind badges. If "
            "you use Agent(instrument=True) instead, skip this package and just "
            "repoint OTEL_EXPORTER_OTLP_ENDPOINT at langprobe."
        ),
    ),
    "openai-agents": Recipe(
        slug="openai-agents",
        instrumentor_package="openinference-instrumentation-openai-agents",
        instrumentor_code="""from langprobe_tracing import build_provider
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

provider = build_provider("openai-agents-app")
OpenAIAgentsInstrumentor().instrument(tracer_provider=provider)

from agents import Agent, Runner

agent = Agent(name="Assistant", instructions="You are helpful and brief.")
result = Runner.run_sync(agent, "Give me a one-line summary of OTLP.")
print(result.final_output)

provider.force_flush()
provider.shutdown()
""",
        notes="Captures the agent loop, handoffs, tool calls, and the underlying LLM calls.",
    ),
    "llamaindex": Recipe(
        slug="llamaindex",
        instrumentor_package="openinference-instrumentation-llama-index",
        instrumentor_code="""from langprobe_tracing import build_provider
from openinference.instrumentation.llama_index import LlamaIndexInstrumentor

provider = build_provider("llamaindex-app")
LlamaIndexInstrumentor().instrument(tracer_provider=provider)

from llama_index.core import VectorStoreIndex, Document

index = VectorStoreIndex.from_documents([Document(text="langprobe speaks OTLP.")])
resp = index.as_query_engine().query("What protocol does langprobe speak?")
print(resp)

provider.force_flush()
provider.shutdown()
""",
        notes=(
            "Captures queries, retrieval (retriever), reranking (reranker), "
            "embeddings (embedding), and the LLM synthesis step — the full RAG tree."
        ),
    ),
    "openai": Recipe(
        slug="openai",
        instrumentor_package="openinference-instrumentation-openai",
        instrumentor_code="""from langprobe_tracing import build_provider
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
""",
        notes=(
            "Bare provider, no framework: a single instrumented call produces a "
            "complete llm-kind run."
        ),
    ),
    "anthropic": Recipe(
        slug="anthropic",
        instrumentor_package="openinference-instrumentation-anthropic",
        instrumentor_code="""from langprobe_tracing import build_provider
from openinference.instrumentation.anthropic import AnthropicInstrumentor

provider = build_provider("bare-anthropic")
AnthropicInstrumentor().instrument(tracer_provider=provider)

from anthropic import Anthropic

client = Anthropic()
resp = client.messages.create(
    model="claude-3-5-sonnet-latest",
    max_tokens=128,
    messages=[{"role": "user", "content": "One-line definition of a trace."}],
)
print(resp.content[0].text)

provider.force_flush()
provider.shutdown()
""",
        notes=(
            "Bare provider, no framework: a single instrumented call produces a "
            "complete llm-kind run."
        ),
    ),
    "langchain": Recipe(
        slug="langchain",
        instrumentor_package="openinference-instrumentation-langchain",
        instrumentor_code="""from langprobe_tracing import build_provider
from openinference.instrumentation.langchain import LangChainInstrumentor

provider = build_provider("langchain-app")
LangChainInstrumentor().instrument(tracer_provider=provider)

from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o")
print(llm.invoke("One-line definition of a trace.").content)

provider.force_flush()
provider.shutdown()
""",
        notes=(
            "Captures chains, agents, tool calls, retrieval, and the underlying LLM "
            "calls across LangChain / LangGraph."
        ),
    ),
}

# Common aliases -> canonical slug. Underscore/hyphen and spelling variants
# callers (and their agents) are likely to pass.
_ALIASES: dict[str, str] = {
    "crew-ai": "crewai",
    "crew_ai": "crewai",
    "pydantic_ai": "pydantic-ai",
    "pydanticai": "pydantic-ai",
    "openai_agents": "openai-agents",
    "openai-agents-sdk": "openai-agents",
    "openai_agents_sdk": "openai-agents",
    "agents": "openai-agents",
    "llama-index": "llamaindex",
    "llama_index": "llamaindex",
    "openai-python": "openai",
    "lang-chain": "langchain",
    "lang_chain": "langchain",
    "langgraph": "langchain",
}


def _normalize(framework: str) -> str:
    """Fold a framework string to a canonical registry slug (best effort)."""
    key = framework.strip().lower().replace(" ", "-")
    if key in RECIPES:
        return key
    return _ALIASES.get(key, key)


def _supported_frameworks() -> list[str]:
    return sorted(RECIPES)


def _generic_guide(*, framework: str, language: str, extra_note: str = "") -> dict[str, Any]:
    """The fallback recipe: plain OTLP, no framework instrumentor."""
    note = (
        "No framework-specific recipe matched. Use the shared OTLP bootstrap below "
        "and, if the repo already builds a TracerProvider with an OTLPSpanExporter, "
        "just repoint it at langprobe instead of adding a second provider."
    )
    if extra_note:
        note = f"{extra_note} {note}"
    return {
        "framework": framework,
        "supported": False,
        "language": language,
        "install": list(_BASE_INSTALL),
        "bootstrap_code": BOOTSTRAP_CODE,
        "instrumentor_code": "",
        "env": dict(ENV),
        "endpoint_note": ENDPOINT_NOTE,
        "verify_step": VERIFY_STEP,
        "notes": "",
        "note": note,
        "supported_frameworks": _supported_frameworks(),
    }


def build_instrumentation_guide(framework: str = "", language: str = "python") -> dict[str, Any]:
    """Return the exact steps to wire ``framework`` to langprobe over OTLP.

    Pure and stateless. For an empty or unknown framework, returns a
    generic OTLP recipe with ``supported=False`` and the list of supported
    frameworks (never raises). Only Python is supported for now; other
    languages get a note pointing at plain OTLP + the supported list.
    """
    lang = (language or "python").strip().lower()

    if lang != "python":
        return _generic_guide(
            framework=framework,
            language=lang,
            extra_note=(
                f"Only Python recipes are available for now (requested "
                f"language={lang!r}). Any OTel SDK in {lang} can export to "
                "langprobe via plain OTLP/HTTP."
            ),
        )

    if not framework.strip():
        return _generic_guide(framework="", language=lang)

    slug = _normalize(framework)
    recipe = RECIPES.get(slug)
    if recipe is None:
        return _generic_guide(framework=framework, language=lang)

    install = [recipe.instrumentor_package, *recipe.extra_packages, *_BASE_INSTALL]
    return {
        "framework": recipe.slug,
        "supported": True,
        "language": lang,
        "install": install,
        "bootstrap_code": BOOTSTRAP_CODE,
        "instrumentor_code": recipe.instrumentor_code,
        "env": dict(ENV),
        "endpoint_note": ENDPOINT_NOTE,
        "verify_step": VERIFY_STEP,
        "notes": recipe.notes,
        "note": "",
        "supported_frameworks": _supported_frameworks(),
    }
