"""PromptVersionCreate xor validator + to_messages() helper.

The model validator ensures exactly one of template, template_messages
is set. to_messages() resolves both shapes to the canonical Message
list for the storage path."""

from __future__ import annotations

import pytest
from langprobe_api.routers.prompts import Message, PromptVersionCreate
from pydantic import ValidationError


def test_accepts_template_messages():
    body = PromptVersionCreate(
        template_messages=[Message(role="human", content="hi")],
    )
    assert body.template_messages is not None
    assert body.template is None


def test_accepts_legacy_template():
    body = PromptVersionCreate(template="hi")
    assert body.template == "hi"
    assert body.template_messages is None


def test_rejects_neither():
    with pytest.raises(ValidationError) as exc:
        PromptVersionCreate()
    assert "required" in str(exc.value).lower()


def test_rejects_both():
    with pytest.raises(ValidationError) as exc:
        PromptVersionCreate(
            template="hi",
            template_messages=[Message(role="human", content="hi")],
        )
    assert "mutually exclusive" in str(exc.value).lower()


def test_rejects_empty_template_messages():
    """min_length=1 on the field; pydantic rejects [] before the
    model validator runs."""
    with pytest.raises(ValidationError):
        PromptVersionCreate(template_messages=[])


def test_to_messages_returns_template_messages_as_is():
    msgs = [
        Message(role="system", content="be terse"),
        Message(role="human", content="hi"),
    ]
    body = PromptVersionCreate(template_messages=msgs)
    assert body.to_messages() == msgs


def test_to_messages_wraps_legacy_template_as_single_human():
    body = PromptVersionCreate(template="hi {{ x }}")
    assert body.to_messages() == [Message(role="human", content="hi {{ x }}")]


def test_to_messages_returns_fresh_list_for_template_messages():
    """Ensure to_messages() doesn't strip pydantic Message identity."""
    msgs = [Message(role="human", content="hi")]
    body = PromptVersionCreate(template_messages=msgs)
    out = body.to_messages()
    assert all(isinstance(m, Message) for m in out)


def test_to_messages_round_trips_via_model_dump():
    """The no-op short-circuit compares [m.model_dump() for m in messages]
    against the deserialized template_messages from the latest version
    row. Pin the round-trip: the same messages on both sides must compare
    equal as list-of-dicts.

    This is the riskiest comparison in the handler (jsonb-vs-list, dict
    key order, str-decoding) and a regression here would silently create
    duplicate versions instead of short-circuiting."""
    msgs = [
        Message(role="system", content="be terse"),
        Message(role="human", content="echo {{ x }}"),
    ]
    body = PromptVersionCreate(template_messages=msgs)
    # Outgoing form (what the handler builds before the INSERT compare).
    outgoing = [m.model_dump() for m in body.to_messages()]
    # Incoming form simulating what asyncpg returns for the jsonb column
    # after `select template_messages from prompt_version` on a row that
    # was previously inserted via this same model_dump path.
    incoming_from_db = [
        {"role": "system", "content": "be terse"},
        {"role": "human", "content": "echo {{ x }}"},
    ]
    assert outgoing == incoming_from_db


def test_legacy_template_round_trips_via_model_dump():
    """The legacy single-string body wraps to the same shape as a
    structured raw_messages with a single human turn — confirming a
    user can re-save a legacy prompt as the structured form without
    accidentally creating v2."""
    legacy_body = PromptVersionCreate(template="echo {{ x }}")
    structured_body = PromptVersionCreate(
        template_messages=[Message(role="human", content="echo {{ x }}")]
    )
    legacy_messages = [m.model_dump() for m in legacy_body.to_messages()]
    structured_messages = [m.model_dump() for m in structured_body.to_messages()]
    assert legacy_messages == structured_messages


def test_derive_legacy_template_single_human():
    """One bare human message -> its content is the legacy template."""
    from langprobe_api.routers.prompts import _derive_legacy_template

    out = _derive_legacy_template([Message(role="human", content="hi")])
    assert out == "hi"


def test_derive_legacy_template_multi_message_returns_empty():
    """Multi-message versions can't be honestly represented as a single
    string -> empty rather than misleading."""
    from langprobe_api.routers.prompts import _derive_legacy_template

    out = _derive_legacy_template(
        [
            Message(role="system", content="be terse"),
            Message(role="human", content="hi"),
        ]
    )
    assert out == ""


def test_derive_legacy_template_single_system_returns_empty():
    """Single system-only message: not a 'normal' prompt; return empty."""
    from langprobe_api.routers.prompts import _derive_legacy_template

    out = _derive_legacy_template([Message(role="system", content="be terse")])
    assert out == ""
