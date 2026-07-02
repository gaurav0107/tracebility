"""Attribute mapping is versioned DATA, not code.

The OTLP/OpenInference ingest shim resolves span kind, model, temperature,
tokens, and IO by consulting an ordered fallback chain of source attribute
keys. Those chains — plus the kind maps — live in a packaged JSON file
(``attribute_mapping.json``) loaded once at import time. Adding a new source
convention (e.g. a new vendor's token key) must require editing ONLY that JSON,
with ZERO Python changes.

These tests assert:
1. The JSON has an integer ``version`` and the expected top-level shape.
2. A representative OTel/OpenInference span still resolves
   model/tokens/kind/io/temperature identically to the pre-extraction
   behavior (byte-identical behavior preservation).
3. The resolution is genuinely data-driven: monkeypatching a new source key
   onto a loaded fallback chain changes what gets resolved, proving the
   Python code reads the data rather than hard-coded literals.
"""

from __future__ import annotations

import json
from pathlib import Path

from langprobe_ingest.routers import otel


def _mapping_path() -> Path:
    return Path(otel.__file__).resolve().parent.parent / "attribute_mapping.json"


def test_mapping_json_has_int_version_and_shape() -> None:
    data = json.loads(_mapping_path().read_text())
    assert isinstance(data["version"], int)
    assert isinstance(data["openinference_kind"], dict)
    assert isinstance(data["gen_ai_operation_kind"], dict)
    assert isinstance(data["model"], list)
    assert isinstance(data["temperature"], list)
    assert isinstance(data["tokens"]["prompt"], list)
    assert isinstance(data["tokens"]["completion"], list)
    assert isinstance(data["tokens"]["total"], list)
    assert isinstance(data["io"]["input"], list)
    assert isinstance(data["io"]["output"], list)


def test_representative_span_resolves_identically() -> None:
    # OpenInference-flavored attributes.
    oi_attrs = {
        "openinference.span.kind": "LLM",
        "llm.model_name": "gpt-4o-mini",
        "llm.invocation_parameters.temperature": 0.7,
        "llm.token_count.prompt": 1000,
        "llm.token_count.completion": 500,
        "input.value": "hello",
        "output.value": "hi there",
    }
    assert otel._resolve_kind(oi_attrs, "chat gpt-4o-mini") == "llm"
    assert otel._resolve_model(oi_attrs) == "gpt-4o-mini"
    assert otel._resolve_temperature(oi_attrs) == 0.7
    assert otel._resolve_tokens(oi_attrs) == (1000, 500, 1500)
    assert otel._resolve_io(oi_attrs) == ("hello", "hi there")

    # OTel GenAI-flavored attributes (second entries in the fallback chains).
    genai_attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "claude-3-5-sonnet",
        "gen_ai.request.temperature": 0.2,
        "gen_ai.usage.input_tokens": 42,
        "gen_ai.usage.output_tokens": 8,
        "gen_ai.prompt": "hi",
        "gen_ai.completion": "yo",
    }
    assert otel._resolve_kind(genai_attrs, "chat") == "llm"
    assert otel._resolve_model(genai_attrs) == "claude-3-5-sonnet"
    assert otel._resolve_temperature(genai_attrs) == 0.2
    assert otel._resolve_tokens(genai_attrs) == (42, 8, 50)
    assert otel._resolve_io(genai_attrs) == ("hi", "yo")


def test_kind_maps_match_data() -> None:
    assert otel._resolve_kind({"openinference.span.kind": "RETRIEVER"}, "x") == "retriever"
    assert otel._resolve_kind({"gen_ai.operation.name": "embeddings"}, "x") == "embedding"


def test_resolution_is_data_driven(monkeypatch) -> None:
    """Appending a new source key to a loaded fallback chain must change
    resolution WITHOUT any code change — proving the resolvers read the data."""
    # A vendor emits its model under a key not in the default chain.
    novel_attrs = {"acme.model": "acme-llm-7b"}
    assert otel._resolve_model(novel_attrs) is None  # not resolvable by default

    patched = list(otel._MODEL_KEYS) + ["acme.model"]
    monkeypatch.setattr(otel, "_MODEL_KEYS", patched)
    assert otel._resolve_model(novel_attrs) == "acme-llm-7b"

    # Same proof for a token fallback chain.
    novel_tokens = {"acme.tokens.in": 7}
    assert otel._resolve_tokens(novel_tokens) == (None, None, None)
    monkeypatch.setattr(
        otel, "_PROMPT_TOKEN_KEYS", list(otel._PROMPT_TOKEN_KEYS) + ["acme.tokens.in"]
    )
    assert otel._resolve_tokens(novel_tokens) == (7, None, 7)
