"""Each DispatchError code records a dispatch_cost row + raises."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from langprobe_api.llm import DispatchError, dispatch
from langprobe_api.llm.types import Message
from litellm import exceptions as litellm_errors

pytestmark = pytest.mark.asyncio


def _patch_workspace(mocker) -> None:
    mocker.patch(
        "langprobe_api.llm.gateway._workspace_id_for_project",
        new=AsyncMock(return_value=uuid.uuid4()),
    )


async def test_no_credential_records_and_raises(fake_pool, mocker) -> None:
    fake_pool.fetchrow = AsyncMock(return_value=None)
    fake_pool.fetchval = AsyncMock(return_value=None)
    _patch_workspace(mocker)

    with pytest.raises(DispatchError) as exc:
        await dispatch(
            fake_pool,
            project_id=uuid.uuid4(),
            surface="playground",
            surface_ref_id=uuid.uuid4(),
            model="openai/gpt-4o",
            messages=[Message(role="user", content="hi")],
        )
    assert exc.value.code == "no_credential"
    insert = [
        c for c in fake_pool.execute.await_args_list if "insert into dispatch_cost" in c.args[0]
    ]
    assert len(insert) == 1
    assert "no_credential" in insert[0].args


async def test_bad_model_raises_before_db(fake_pool, mocker) -> None:
    with pytest.raises(DispatchError) as exc:
        await dispatch(
            fake_pool,
            project_id=uuid.uuid4(),
            surface="playground",
            surface_ref_id=uuid.uuid4(),
            model="bedrock/claude",
            messages=[Message(role="user", content="hi")],
        )
    assert exc.value.code == "bad_model"
    fake_pool.execute.assert_not_called()


async def test_timeout_records_and_raises(fake_pool, mocker) -> None:
    fake_pool.fetchrow = AsyncMock(return_value={"secret_encrypted": "sk"})
    fake_pool.fetchval = AsyncMock(return_value=None)
    _patch_workspace(mocker)
    mocker.patch(
        "langprobe_api.llm.gateway.litellm.acompletion",
        new=AsyncMock(
            side_effect=litellm_errors.Timeout(
                "upstream timed out", model="x", llm_provider="openai"
            )
        ),
    )
    with pytest.raises(DispatchError) as exc:
        await dispatch(
            fake_pool,
            project_id=uuid.uuid4(),
            surface="playground",
            surface_ref_id=uuid.uuid4(),
            model="openai/gpt-4o",
            messages=[Message(role="user", content="hi")],
        )
    assert exc.value.code == "timeout"
    insert = [
        c for c in fake_pool.execute.await_args_list if "insert into dispatch_cost" in c.args[0]
    ]
    assert len(insert) == 1


async def test_provider_error_records_and_raises(fake_pool, mocker) -> None:
    fake_pool.fetchrow = AsyncMock(return_value={"secret_encrypted": "sk"})
    fake_pool.fetchval = AsyncMock(return_value=None)
    _patch_workspace(mocker)
    mocker.patch(
        "langprobe_api.llm.gateway.litellm.acompletion",
        new=AsyncMock(
            side_effect=litellm_errors.RateLimitError(
                "429",
                model="x",
                llm_provider="openai",
            )
        ),
    )
    with pytest.raises(DispatchError) as exc:
        await dispatch(
            fake_pool,
            project_id=uuid.uuid4(),
            surface="playground",
            surface_ref_id=uuid.uuid4(),
            model="openai/gpt-4o",
            messages=[Message(role="user", content="hi")],
        )
    assert exc.value.code == "provider_error"


async def test_ceiling_exceeded_only_on_automated_surfaces(fake_pool, mocker) -> None:
    fake_pool.fetchrow = AsyncMock(
        return_value={
            "ceiling": 50.0,
            "spent": 51.0,
        }
    )
    fake_pool.fetchval = AsyncMock(return_value=None)
    _patch_workspace(mocker)
    with pytest.raises(DispatchError) as exc:
        await dispatch(
            fake_pool,
            project_id=uuid.uuid4(),
            surface="poll",
            surface_ref_id=uuid.uuid4(),
            model="openai/gpt-4o",
            messages=[Message(role="user", content="hi")],
        )
    assert exc.value.code == "ceiling_exceeded"


async def test_ceiling_skipped_on_interactive_surfaces(fake_pool, mocker) -> None:
    """Interactive surfaces skip the ceiling check entirely."""
    fake_pool.fetchrow = AsyncMock(
        side_effect=[
            {"secret_encrypted": "sk"},
        ]
    )
    fake_pool.fetchval = AsyncMock(return_value=None)
    _patch_workspace(mocker)

    fake_resp = mocker.MagicMock()
    fake_resp.choices = [mocker.MagicMock(message=mocker.MagicMock(content="hi"))]
    fake_resp.usage = {"prompt_tokens": 1, "completion_tokens": 1}
    fake_resp.model = "openai/gpt-4o"
    fake_resp.model_dump = mocker.MagicMock(return_value={})
    mocker.patch(
        "langprobe_api.llm.gateway.litellm.acompletion",
        new=AsyncMock(return_value=fake_resp),
    )
    mocker.patch("langprobe_api.llm.gateway.litellm.completion_cost", return_value=0)

    result = await dispatch(
        fake_pool,
        project_id=uuid.uuid4(),
        surface="playground",
        surface_ref_id=uuid.uuid4(),
        model="openai/gpt-4o",
        messages=[Message(role="user", content="hi")],
    )
    assert result.text == "hi"
