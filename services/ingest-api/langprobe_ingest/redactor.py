"""PII redactor (Phase 8 stub).

Healthcare and fin customers won't ingest LLM traces unless something is
scrubbing the obvious stuff before it lands in ClickHouse. LangSmith ships
this; we have to too. Per the CEO plan this is a "Presidio-equivalent"
slot — the real Presidio integration is a follow-up swap behind the same
:class:`Redactor` interface.

What this stub does:
- Walks the inputs/outputs/error_message strings AND the structured
  metadata/attributes/extra fields (recursively, every string leaf) of
  each run/span. This matters because the OTLP shim copies prompt and
  completion content into span ``attributes``; redacting only
  inputs/outputs would leave the same PII in the attributes column.
- Replaces matches of a small built-in regex set with stable tokens
  (``[REDACTED:EMAIL]`` etc.) so downstream eval/replay is still useful.
- Records redaction counts on the envelope so operators can audit signal
  loss before turning it on broadly.

What it explicitly does NOT do (yet):
- Named-entity detection (PERSON, LOCATION, ORG). That's the Presidio swap.
- Per-project policy. Today it's stack-wide on/off via env.
- Reversible tokenization. Once redacted, the original is gone — that's
  the point. If you need the original for replay, redaction must stay off
  for that project.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

# Stable replacement tokens. Keep them grep-able and obviously-not-data.
_TOKENS: dict[str, str] = {
    "EMAIL": "[REDACTED:EMAIL]",
    "PHONE": "[REDACTED:PHONE]",
    "SSN": "[REDACTED:SSN]",
    "CREDIT_CARD": "[REDACTED:CC]",
    "API_KEY": "[REDACTED:KEY]",
    "AWS_KEY": "[REDACTED:AWS_KEY]",
    "JWT": "[REDACTED:JWT]",
}

# Patterns are intentionally conservative — false negatives (missed PII)
# are recoverable by the operator turning on stricter modes; false
# positives (over-redaction) silently destroy eval signal.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "EMAIL",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "SSN",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "CREDIT_CARD",
        # 13-19 digit groups, optionally space/hyphen separated. Loose
        # enough to cover common renderings; tight enough to skip plain
        # version numbers.
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    ),
    (
        "AWS_KEY",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "API_KEY",
        # Generic-looking secret tokens. Match our own lt_ prefix and
        # common third-party shapes (sk-, pk-, ghp_, gho_).
        re.compile(
            r"\b(?:lt_[a-f0-9]{16}\.[A-Za-z0-9_-]{20,}|"
            r"sk-[A-Za-z0-9]{20,}|pk-[A-Za-z0-9]{20,}|"
            r"gh[ps]_[A-Za-z0-9]{20,})\b"
        ),
    ),
    (
        "PHONE",
        # E.164-ish or US-style. Last so SSN/CC patterns win first.
        re.compile(r"(?:(?<!\d)\+?\d{1,3}[ -]?)?(?<!\d)\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}(?!\d)"),
    ),
)


@dataclass
class RedactionResult:
    text: str
    counts: Counter[str]


class Redactor:
    """Apply built-in regex redactions to free-form strings."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def redact_text(self, value: str) -> RedactionResult:
        counts: Counter[str] = Counter()
        if not self.enabled or not value:
            return RedactionResult(text=value, counts=counts)
        out = value
        for label, pattern in _PATTERNS:
            replacement = _TOKENS[label]
            new_out, n = pattern.subn(replacement, out)
            if n:
                counts[label] += n
                out = new_out
        return RedactionResult(text=out, counts=counts)

    def redact_envelope(self, envelope: dict[str, Any]) -> Counter[str]:
        """Walk a serialized envelope and redact in place. Returns aggregate counts."""
        if not self.enabled:
            return Counter()
        agg: Counter[str] = Counter()
        payload = envelope.get("payload") or {}
        for run in payload.get("runs") or []:
            agg.update(self._redact_run(run))
        for span in payload.get("spans") or []:
            agg.update(self._redact_span(span))
        return agg

    # Free-form string fields redacted directly.
    _STRING_FIELDS = ("inputs", "outputs", "error_message")
    # Structured (dict/list) fields whose string leaves also carry PII.
    # `metadata` is persisted on runs and `attributes` on spans; the OTLP
    # shim also copies prompt/completion content into span `attributes`,
    # so scrubbing only inputs/outputs would leave the same PII behind
    # here. `extra` covers the LangSmith shim's `extra.metadata`.
    _STRUCTURED_FIELDS = ("metadata", "attributes", "extra")

    def _redact_run(self, run: dict[str, Any]) -> Counter[str]:
        agg: Counter[str] = Counter()
        for key in self._STRING_FIELDS:
            agg.update(self._redact_field(run, key))
        for key in self._STRUCTURED_FIELDS:
            agg.update(self._redact_structure(run, key))
        for span in run.get("spans") or []:
            agg.update(self._redact_span(span))
        return agg

    def _redact_span(self, span: dict[str, Any]) -> Counter[str]:
        agg: Counter[str] = Counter()
        for key in self._STRING_FIELDS:
            agg.update(self._redact_field(span, key))
        for key in self._STRUCTURED_FIELDS:
            agg.update(self._redact_structure(span, key))
        return agg

    def _redact_field(self, obj: dict[str, Any], key: str) -> Counter[str]:
        v = obj.get(key)
        if not isinstance(v, str) or not v:
            return Counter()
        result = self.redact_text(v)
        if result.counts:
            obj[key] = result.text
        return result.counts

    def _redact_structure(self, obj: dict[str, Any], key: str) -> Counter[str]:
        """Redact string leaves inside a nested dict/list field, in place."""
        if key not in obj:
            return Counter()
        new_value, counts = self._redact_value(obj[key])
        if counts:
            obj[key] = new_value
        return counts

    def _redact_value(self, value: Any) -> tuple[Any, Counter[str]]:
        """Recursively redact string leaves in dicts/lists/strings.

        Returns the (possibly rebuilt) value and the aggregate counts.
        Non-string scalars (int/float/bool/None) pass through untouched.
        """
        if isinstance(value, str):
            result = self.redact_text(value)
            return result.text, result.counts
        counts: Counter[str] = Counter()
        if isinstance(value, dict):
            new_dict: dict[Any, Any] = {}
            for k, item in value.items():
                new_dict[k], item_counts = self._redact_value(item)
                counts.update(item_counts)
            return new_dict, counts
        if isinstance(value, list):
            new_list: list[Any] = []
            for item in value:
                new_item, item_counts = self._redact_value(item)
                new_list.append(new_item)
                counts.update(item_counts)
            return new_list, counts
        return value, counts


def redactor_from_env(enabled_flag: bool) -> Redactor:
    """Factory for app startup. Kept thin so tests can swap the redactor."""
    return Redactor(enabled=enabled_flag)
