"""Prompt response models surface both `template` and `template_messages`.

The new field is authoritative; the legacy `template` is derived for one
release of back-compat. After the release window we drop the legacy field.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

# These imports refer to symbols added in this task. Until the
# implementation lands, the test fails at import time.
from langprobe_api.routers.prompts import (
    Message,
    PromptVersionOut,
    _hydrate_version,
)
from pydantic import ValidationError


def _now():
    return datetime.now(UTC)


def test_response_carries_template_messages_and_legacy_template_derived():
    """A single-human-message version round-trips with both fields.

    `template` is derived from the messages array; clients that only know
    about the legacy field still work.
    """
    msg = Message(role="human", content="hello {{ x }}")
    out = PromptVersionOut(
        id=uuid4(),
        prompt_id=uuid4(),
        version=1,
        template_messages=[msg],
        template="hello {{ x }}",
        input_schema=None,
        model_params=None,
        aliases=[],
        commit_message=None,
        created_at=_now(),
    )
    assert out.template_messages == [msg]
    assert out.template == "hello {{ x }}"


def test_response_with_system_and_human_omits_legacy_template():
    """A multi-message version sets `template` to '' rather than picking one.

    Old clients see an empty template — better than misleading them with
    the human content alone.
    """
    out = PromptVersionOut(
        id=uuid4(),
        prompt_id=uuid4(),
        version=2,
        template_messages=[
            Message(role="system", content="be terse"),
            Message(role="human", content="answer {{ q }}"),
        ],
        template="",
        input_schema=None,
        model_params=None,
        aliases=[],
        commit_message=None,
        created_at=_now(),
    )
    assert len(out.template_messages) == 2
    assert out.template == ""


def test_message_role_is_constrained_to_v1_set():
    """Pydantic enforces role in {system, human}. AI/tool not yet supported."""
    Message(role="system", content="ok")
    Message(role="human", content="ok")
    with pytest.raises(ValidationError):
        Message(role="ai", content="not yet")


# ---------------------------------------------------------------------------
# _hydrate_version derivation rule. The above tests pin the model shape;
# these pin the row -> model conversion logic so a regression in the
# legacy-template derivation actually fails CI.
# ---------------------------------------------------------------------------


def _row(messages):
    """Build a fake asyncpg.Record-shaped dict for _hydrate_version."""
    return {
        "id": uuid4(),
        "prompt_id": uuid4(),
        "version": 1,
        "template_messages": messages,
        "input_schema": None,
        "model_params": None,
        "aliases": [],
        "commit_message": None,
        "created_at": _now(),
    }


def test_hydrate_version_derives_template_from_single_human_message():
    out = _hydrate_version(_row([{"role": "human", "content": "hello {{ x }}"}]))
    assert out.template == "hello {{ x }}"
    assert out.template_messages == [Message(role="human", content="hello {{ x }}")]


def test_hydrate_version_empty_template_for_multi_message():
    out = _hydrate_version(
        _row(
            [
                {"role": "system", "content": "be terse"},
                {"role": "human", "content": "answer {{ q }}"},
            ]
        )
    )
    assert out.template == ""


def test_hydrate_version_empty_template_for_single_system_message():
    """A single system message is the rare-but-real case where the legacy
    field can't be derived. Empty string, not the system content."""
    out = _hydrate_version(_row([{"role": "system", "content": "be terse"}]))
    assert out.template == ""


def test_hydrate_version_handles_jsonb_string_form():
    """Some asyncpg paths return jsonb as a string; _hydrate_version
    coerces defensively."""
    raw = _json.dumps([{"role": "human", "content": "hi"}])
    out = _hydrate_version(_row(raw))
    assert out.template_messages == [Message(role="human", content="hi")]
    assert out.template == "hi"
