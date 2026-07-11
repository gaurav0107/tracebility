"""LangSmith API parity shim.

The wedge: an existing LangSmith user points ``LANGSMITH_ENDPOINT`` at our host
and the SDK keeps working. We translate LangSmith's ``RunCreate`` / ``RunUpdate``
shape into our internal ``IngestBatch`` and push to the same queue as native
ingest. The worker is path-agnostic.

LangSmith run_type → our kind:
  llm, chain, tool, retriever, embedding, parser, agent → identical
  prompt → 'parser' (langchain occasionally emits this for output parsing)
  unknown → 'chain' (worker stashes raw run_type in metadata)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import orjson
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from langprobe_tenant import QuotaMeter, TenantContext

from ..enqueue import IngestEnqueue, serialize_batch
from ..limits import INGEST_GATING_METER, enforce_quota
from ..redactor import Redactor
from ..schemas import IngestAck, IngestBatch, RunIngest, RunKind

log = structlog.get_logger("langprobe.ingest.langsmith_shim")

router = APIRouter(tags=["langsmith-shim"])

_KIND_MAP: dict[str, RunKind] = {
    "llm": "llm",
    "chain": "chain",
    "tool": "tool",
    "agent": "agent",
    "retriever": "retriever",
    "embedding": "embedding",
    "parser": "parser",
    "prompt": "parser",
}


def _coerce_kind(run_type: str | None) -> RunKind:
    if run_type is None:
        return "chain"
    return _KIND_MAP.get(run_type.lower(), "chain")


# LangSmith has no dedicated session field: threads are grouped off a metadata
# key. The SDK puts it in ``extra.metadata`` under one of these (in priority
# order). Without reading them, correctly-tagged LangSmith runs never populate
# run.session_id and stay invisible under /runs?view=threads.
_LS_SESSION_META_KEYS = ("session_id", "thread_id", "conversation_id")


def _resolve_session_id(body: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    # A literal top-level session_id wins if a caller sets one explicitly.
    top = body.get("session_id")
    if isinstance(top, str) and top:
        return top
    for key in _LS_SESSION_META_KEYS:
        v = metadata.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return orjson.dumps(value).decode("utf-8")


def _to_run_ingest(body: dict[str, Any], *, partial: bool = False) -> RunIngest:
    extra = body.get("extra") or {}
    metadata = dict(extra.get("metadata") or {})
    tags = list(body.get("tags") or extra.get("tags") or [])
    for key in ("dotted_order", "trace_id", "run_type"):
        if key in body and body[key] is not None:
            metadata[f"langsmith_{key}"] = body[key]

    inputs = body.get("inputs")
    outputs = body.get("outputs")
    err = body.get("error")
    return RunIngest(
        run_id=UUID(str(body["id"])),
        parent_run_id=UUID(str(body["parent_run_id"])) if body.get("parent_run_id") else None,
        name=body.get("name") or ("update" if partial else "run"),
        kind=_coerce_kind(body.get("run_type")),
        status="error" if err else ("running" if partial and not body.get("end_time") else "ok"),
        sdk="langsmith",
        start_time=body.get("start_time") or datetime.now(UTC).isoformat(),
        end_time=body.get("end_time"),
        inputs=None if inputs is None else _stringify(inputs),
        outputs=None if outputs is None else _stringify(outputs),
        session_id=_resolve_session_id(body, metadata),
        tags=tags,
        metadata=metadata,
        error_message=err,
    )


async def _enqueue_runs(request: Request, ctx: TenantContext, runs: list[RunIngest]) -> IngestAck:
    batch = IngestBatch(sdk="langsmith", runs=runs)
    envelope: dict[str, Any] = {
        "org_id": str(ctx.org_id),
        "workspace_id": str(ctx.workspace_id),
        "project_id": str(ctx.project_id),
        "api_key_id": str(ctx.api_key_id),
        "received_at": datetime.now(UTC).isoformat(),
        "source": "langsmith_shim",
        "payload": batch.model_dump(mode="json"),
    }
    enqueue: IngestEnqueue = request.app.state.enqueue
    redactor: Redactor = request.app.state.redactor
    counts = redactor.redact_envelope(envelope)
    if counts:
        log.info(
            "redacted",
            project_id=envelope["project_id"],
            counts=dict(counts),
        )
    serialized = serialize_batch(envelope)
    await enqueue.enqueue(serialized, org_id=ctx.org_id)

    quota_meter: QuotaMeter = request.app.state.quota_meter
    try:
        await quota_meter.record(
            org_id=ctx.org_id, meter=INGEST_GATING_METER, amount=len(runs), limit=-1
        )
        await quota_meter.record(
            org_id=ctx.org_id, meter="span_bytes", amount=len(serialized), limit=-1
        )
    except Exception:  # noqa: BLE001
        log.warning("quota record failed", org_id=str(ctx.org_id))

    return IngestAck(accepted_runs=len(runs), accepted_spans=0)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=IngestAck)
async def langsmith_create_run(
    request: Request,
    body: dict[str, Any],
    ctx: TenantContext = Depends(enforce_quota),
) -> IngestAck:
    if "id" not in body:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "id is required")
    return await _enqueue_runs(request, ctx, [_to_run_ingest(body)])


@router.post("/runs/batch", status_code=status.HTTP_202_ACCEPTED, response_model=IngestAck)
async def langsmith_batch_runs(
    request: Request,
    body: dict[str, Any],
    ctx: TenantContext = Depends(enforce_quota),
) -> IngestAck:
    posts = body.get("post") or []
    patches = body.get("patch") or []
    runs = [_to_run_ingest(p) for p in posts] + [_to_run_ingest(p, partial=True) for p in patches]
    return await _enqueue_runs(request, ctx, runs)


@router.patch("/runs/{run_id}", status_code=status.HTTP_202_ACCEPTED, response_model=IngestAck)
async def langsmith_update_run(
    request: Request,
    run_id: UUID,
    body: dict[str, Any],
    ctx: TenantContext = Depends(enforce_quota),
) -> IngestAck:
    merged = {**body, "id": str(run_id)}
    return await _enqueue_runs(request, ctx, [_to_run_ingest(merged, partial=True)])
