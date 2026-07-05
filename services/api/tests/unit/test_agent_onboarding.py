"""Unit tests for the stateless instrumentation-guide recipe engine."""

from __future__ import annotations

from langprobe_api.agent.onboarding import build_instrumentation_guide


def test_crewai_supported_guide():
    guide = build_instrumentation_guide("crewai")
    assert guide["supported"] is True
    assert guide["framework"] == "crewai"
    assert guide["language"] == "python"
    # install references the CrewAI OpenInference instrumentor + the OTLP http exporter
    install_blob = " ".join(guide["install"])
    assert "openinference-instrumentation-crewai" in install_blob
    assert "opentelemetry-exporter-otlp-proto-http" in install_blob
    assert "openinference.instrumentation.crewai" in guide["instrumentor_code"]
    assert "CrewAIInstrumentor" in guide["instrumentor_code"]
    # env carries both required variables
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in guide["env"]
    assert "LANGPROBE_API_KEY" in guide["env"]
    # endpoint note points at the ingest path
    assert "/v1/traces" in guide["endpoint_note"]
    # bootstrap + verify are present
    assert "TracerProvider" in guide["bootstrap_code"]
    assert guide["verify_step"]


def test_unknown_framework_falls_back():
    guide = build_instrumentation_guide("bogus")
    assert guide["supported"] is False
    assert guide["supported_frameworks"]
    assert "crewai" in guide["supported_frameworks"]
    # still gives a usable generic OTLP recipe
    assert "opentelemetry-exporter-otlp-proto-http" in " ".join(guide["install"])
    assert "TracerProvider" in guide["bootstrap_code"]


def test_empty_framework_lists_supported():
    guide = build_instrumentation_guide("")
    assert guide["supported"] is False
    assert guide["supported_frameworks"]


def test_pydantic_ai_alias_resolves():
    canonical = build_instrumentation_guide("pydantic-ai")
    alias = build_instrumentation_guide("pydantic_ai")
    assert alias["supported"] is True
    assert alias["framework"] == canonical["framework"]
    assert alias["instrumentor_code"] == canonical["instrumentor_code"]
    assert alias["install"] == canonical["install"]


def test_common_aliases_resolve():
    assert build_instrumentation_guide("openai_agents")["framework"] == "openai-agents"
    assert build_instrumentation_guide("llama-index")["framework"] == "llamaindex"


def test_non_python_language_points_at_otlp():
    guide = build_instrumentation_guide("crewai", language="typescript")
    assert guide["supported"] is False
    assert guide["language"] == "typescript"
    assert guide["supported_frameworks"]
    assert guide["note"]
