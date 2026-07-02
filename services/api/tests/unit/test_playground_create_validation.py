"""PlaygroundCreate xor: exactly one of prompt_version_id /
raw_template / raw_messages is required per request. Zero or more than
one is a 422 (pydantic ValidationError)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from langprobe_api.routers.playground import Message, PlaygroundCreate
from pydantic import ValidationError

_MODEL = "anthropic/claude-sonnet-4-6"


def test_accepts_raw_messages():
    body = PlaygroundCreate(
        project_id=uuid4(),
        raw_messages=[Message(role="human", content="hi {{ x }}")],
        variables={"x": "y"},
        model=_MODEL,
    )
    assert body.raw_messages is not None
    assert body.raw_template is None
    assert body.prompt_version_id is None


def test_accepts_raw_template():
    body = PlaygroundCreate(
        project_id=uuid4(),
        raw_template="hi {{ x }}",
        model=_MODEL,
    )
    assert body.raw_template == "hi {{ x }}"
    assert body.raw_messages is None


def test_accepts_prompt_version_id():
    body = PlaygroundCreate(
        project_id=uuid4(),
        prompt_version_id=uuid4(),
        model=_MODEL,
    )
    assert body.prompt_version_id is not None
    assert body.raw_template is None
    assert body.raw_messages is None


def test_rejects_zero_template_sources():
    with pytest.raises(ValidationError) as exc:
        PlaygroundCreate(project_id=uuid4(), model=_MODEL)
    assert "required" in str(exc.value).lower()


def test_rejects_template_and_messages_together():
    with pytest.raises(ValidationError):
        PlaygroundCreate(
            project_id=uuid4(),
            raw_template="hi",
            raw_messages=[Message(role="human", content="hi")],
            model=_MODEL,
        )


def test_rejects_prompt_id_and_raw_template_together():
    with pytest.raises(ValidationError):
        PlaygroundCreate(
            project_id=uuid4(),
            prompt_version_id=uuid4(),
            raw_template="hi",
            model=_MODEL,
        )


def test_rejects_prompt_id_and_raw_messages_together():
    with pytest.raises(ValidationError):
        PlaygroundCreate(
            project_id=uuid4(),
            prompt_version_id=uuid4(),
            raw_messages=[Message(role="human", content="hi")],
            model=_MODEL,
        )


def test_rejects_all_three_together():
    with pytest.raises(ValidationError) as exc:
        PlaygroundCreate(
            project_id=uuid4(),
            prompt_version_id=uuid4(),
            raw_template="hi",
            raw_messages=[Message(role="human", content="hi")],
            model=_MODEL,
        )
    assert "mutually exclusive" in str(exc.value).lower()


def test_rejects_empty_raw_messages_list():
    """An empty messages list is not a valid template source - at least
    one message is required. (The check constraint on the prompt_version
    table enforces this on the storage side; the request-side validator
    closes the gap so we don't even attempt a render with zero messages.)
    """
    with pytest.raises(ValidationError):
        PlaygroundCreate(
            project_id=uuid4(),
            raw_messages=[],
            model=_MODEL,
        )


def test_rejects_all_three_explicit_none():
    """Same as the zero-source case, but with explicit Nones in the body
    (e.g. a JSON client that sends nulls instead of omitting fields).
    Pydantic should treat None and omitted identically."""
    with pytest.raises(ValidationError):
        PlaygroundCreate(
            project_id=uuid4(),
            prompt_version_id=None,
            raw_template=None,
            raw_messages=None,
            model=_MODEL,
        )


def test_rejects_raw_messages_with_invalid_role():
    """Pydantic enforces role in {system, human} via the Message Literal;
    sending an out-of-range role rejects the request before our validator
    even runs."""
    with pytest.raises(ValidationError):
        PlaygroundCreate(
            project_id=uuid4(),
            raw_messages=[{"role": "assistant", "content": "hi"}],
            model=_MODEL,
        )
