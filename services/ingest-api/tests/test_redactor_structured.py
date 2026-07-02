"""Redactor scrubs structured fields, not just inputs/outputs.

Regression guard for the OTLP redactor-bypass: the OTel shim copies
prompt/completion content into span ``attributes`` (and runs carry
``metadata``), so redacting only the inputs/outputs strings left the
same PII in the persisted ``attributes``/``metadata`` columns.
"""

from __future__ import annotations

from langprobe_ingest.redactor import Redactor

_SSN = "123-45-6789"
_EMAIL = "patient@hospital.org"


def test_span_attributes_are_redacted() -> None:
    r = Redactor(enabled=True)
    span = {
        "inputs": f"hello {_SSN}",
        "attributes": {
            "gen_ai.prompt": f"SSN {_SSN}",
            "nested": [{"note": f"email {_EMAIL}"}],
            "n_tokens": 42,
        },
    }
    counts = r._redact_span(span)

    assert _SSN not in span["inputs"]
    # The bypass: attributes must also be scrubbed.
    assert _SSN not in span["attributes"]["gen_ai.prompt"]
    assert _EMAIL not in span["attributes"]["nested"][0]["note"]
    # Non-string scalars pass through untouched.
    assert span["attributes"]["n_tokens"] == 42
    assert counts["SSN"] >= 2
    assert counts["EMAIL"] >= 1


def test_run_metadata_and_extra_are_redacted() -> None:
    r = Redactor(enabled=True)
    run = {
        "metadata": {"customer_ssn": _SSN},
        "extra": {"metadata": {"contact": _EMAIL}},
    }
    r._redact_run(run)

    assert _SSN not in run["metadata"]["customer_ssn"]
    assert _EMAIL not in run["extra"]["metadata"]["contact"]


def test_disabled_redactor_leaves_attributes_untouched() -> None:
    r = Redactor(enabled=False)
    span = {"attributes": {"gen_ai.prompt": f"SSN {_SSN}"}}
    r._redact_span(span)
    assert span["attributes"]["gen_ai.prompt"] == f"SSN {_SSN}"
