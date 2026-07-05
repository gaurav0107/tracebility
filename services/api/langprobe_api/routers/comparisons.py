"""Side-by-side prompt comparisons (A/B v1).

A comparison pairs a dataset with two prompt versions and a judge, then
scores every dataset item under each variant. Per-item scores live in
ClickHouse `eval_score` — one row per side, with the side carried in
`judge_name` as `cmp:a` / `cmp:b` so the data plane schema stays the
same as evals/feedback. Aggregates (counters + averages per side) live
on postgres `comparison`.

V1 ships built-in judges only (echo / contains / exact). LLM-as-judge
swaps in next iteration; `_render_for_variant` is the single seam where
real generation lands. Storage shape is final: pairing per item is a
FULL OUTER JOIN on `run_id` (which carries `item_id` per the eval
convention) once both sides have at least one row.

Boundaries:
- POST inserts the queued row, kicks off `BackgroundTasks._run_comparison`,
  returns 202.
- The runner fetches dataset items, scores each on both sides, batches
  one ClickHouse insert with all rows, updates postgres counters per
  side. Failures bubble into `comparison.error` with status='failed';
  we never silently drop.
- Audit-fail-closed (ER-10) on every write.
- RBAC: list/get for any role; create for owner/admin/member; no
  destructive ops in v1 (a stuck row is fine).
"""

from __future__ import annotations

import json as _json
import re
import uuid as _uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import audit
from ..auth import Principal, assert_workspace_role, require_user
from ..clickhouse_client import ClickHouseQuery
from ..tenant_scope import resolve_tenant_ids
from . import playground as playground_module

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_DEFAULT_MAX_TOKENS = 1024

log = structlog.get_logger("langprobe.api.comparisons")

router = APIRouter(prefix="/v1/comparisons", tags=["comparisons"])

_JUDGE_KINDS = {"echo", "contains", "exact"}


class ComparisonOut(BaseModel):
    id: UUID
    project_id: UUID
    dataset_id: UUID
    prompt_version_id_a: UUID
    prompt_version_id_b: UUID
    judge_kind: str
    name: str | None
    status: str
    item_total: int
    item_done_a: int
    item_done_b: int
    score_avg_a: float | None
    score_avg_b: float | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ComparisonCreate(BaseModel):
    project_id: UUID
    dataset_id: UUID
    prompt_version_id_a: UUID
    prompt_version_id_b: UUID
    judge_kind: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=255)


class ComparisonItemRow(BaseModel):
    item_id: UUID | None
    score_a: float | None
    score_b: float | None
    label_a: str | None
    label_b: str | None
    rationale_a: str | None
    rationale_b: str | None
    judged_at: datetime | None


class ComparisonItemList(BaseModel):
    items: list[ComparisonItemRow]


@router.get("", response_model=list[ComparisonOut])
async def list_comparisons(
    request: Request,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> list[ComparisonOut]:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        project_id,
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, dataset_id,
               prompt_version_id_a, prompt_version_id_b,
               judge_kind, name, status,
               item_total, item_done_a, item_done_b,
               score_avg_a, score_avg_b,
               error, started_at, finished_at, created_at, updated_at
        from comparison
        where project_id = $1
        order by created_at desc
        """,
        project_id,
    )
    return [ComparisonOut(**dict(r)) for r in rows]


@router.post("", response_model=ComparisonOut, status_code=status.HTTP_202_ACCEPTED)
async def create_comparison(
    request: Request,
    body: ComparisonCreate,
    background: BackgroundTasks,
    principal: Principal = Depends(require_user),
) -> ComparisonOut:
    pool: asyncpg.Pool = request.app.state.pg
    workspace_id = await _assert_project_role(
        pool,
        body.project_id,
        principal,
        allowed=("owner", "admin", "member"),
    )
    if body.judge_kind not in _JUDGE_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"judge_kind must be one of {sorted(_JUDGE_KINDS)}",
        )
    if body.prompt_version_id_a == body.prompt_version_id_b:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "prompt_version_id_a and prompt_version_id_b must differ",
        )

    dataset = await pool.fetchrow(
        """
        select id, project_id, item_count
        from dataset
        where id = $1 and deleted_at is null
        """,
        body.dataset_id,
    )
    if dataset is None or dataset["project_id"] != body.project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")

    versions = await pool.fetch(
        """
        select pv.id, p.project_id
        from prompt_version pv
        join prompt p on p.id = pv.prompt_id
        where pv.id = any($1::uuid[])
          and p.deleted_at is null
        """,
        [body.prompt_version_id_a, body.prompt_version_id_b],
    )
    by_id = {r["id"]: r for r in versions}
    for vid in (body.prompt_version_id_a, body.prompt_version_id_b):
        v = by_id.get(vid)
        if v is None or v["project_id"] != body.project_id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"prompt version not found in project: {vid}",
            )

    row = await pool.fetchrow(
        """
        insert into comparison (
            project_id, dataset_id,
            prompt_version_id_a, prompt_version_id_b,
            judge_kind, name, status, item_total, created_by
        )
        values ($1, $2, $3, $4, $5, $6, 'queued', $7, $8)
        returning id, project_id, dataset_id,
                  prompt_version_id_a, prompt_version_id_b,
                  judge_kind, name, status,
                  item_total, item_done_a, item_done_b,
                  score_avg_a, score_avg_b,
                  error, started_at, finished_at, created_at, updated_at
        """,
        body.project_id,
        body.dataset_id,
        body.prompt_version_id_a,
        body.prompt_version_id_b,
        body.judge_kind,
        body.name,
        dataset["item_count"],
        principal.user_id,
    )
    assert row is not None
    cmp_row = ComparisonOut(**dict(row))
    await audit.record(
        pool,
        principal=principal,
        action="comparison.create",
        target_kind="comparison",
        target_id=cmp_row.id,
        payload={
            "dataset_id": str(cmp_row.dataset_id),
            "judge_kind": cmp_row.judge_kind,
            "prompt_version_id_a": str(cmp_row.prompt_version_id_a),
            "prompt_version_id_b": str(cmp_row.prompt_version_id_b),
        },
        request=request,
        workspace_id=workspace_id,
        project_id=cmp_row.project_id,
    )

    ch = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        await _mark_failed(pool, cmp_row.id, "clickhouse not configured")
    else:
        background.add_task(_run_comparison, pool, ch, cmp_row.id, workspace_id)
    return cmp_row


@router.get("/{comparison_id}", response_model=ComparisonOut)
async def get_comparison(
    request: Request,
    comparison_id: UUID,
    principal: Principal = Depends(require_user),
) -> ComparisonOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_comparison(pool, comparison_id)
    await _assert_project_role(
        pool,
        row["project_id"],
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    return ComparisonOut(**dict(row))


@router.get("/{comparison_id}/items", response_model=ComparisonItemList)
async def list_items(
    request: Request,
    comparison_id: UUID,
    limit: int = Query(default=200, ge=1, le=2000),
    principal: Principal = Depends(require_user),
) -> ComparisonItemList:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_comparison(pool, comparison_id)
    await _assert_project_role(
        pool,
        row["project_id"],
        principal,
        allowed=("owner", "admin", "member", "viewer"),
    )
    ch = _require_clickhouse(request)

    sql = """
        select
            coalesce(a.run_id, b.run_id) as item_id,
            a.score as score_a,
            b.score as score_b,
            a.label as label_a,
            b.label as label_b,
            a.rationale as rationale_a,
            b.rationale as rationale_b,
            greatest(coalesce(a.judged_at, toDateTime64(0,3)),
                     coalesce(b.judged_at, toDateTime64(0,3))) as judged_at
        from (
            select run_id, score, label, rationale, judged_at
            from eval_score
            where project_id = {project_id:UUID}
              and eval_config_id = {comparison_id:UUID}
              and judge_name = 'cmp:a'
        ) a
        full outer join (
            select run_id, score, label, rationale, judged_at
            from eval_score
            where project_id = {project_id:UUID}
              and eval_config_id = {comparison_id:UUID}
              and judge_name = 'cmp:b'
        ) b on a.run_id = b.run_id
        order by judged_at desc
        limit {limit:UInt32}
    """
    params = {
        "project_id": str(row["project_id"]),
        "comparison_id": str(comparison_id),
        "limit": limit,
    }
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("comparison items query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    items = [
        ComparisonItemRow(
            item_id=r.get("item_id"),
            score_a=_opt_float(r.get("score_a")),
            score_b=_opt_float(r.get("score_b")),
            label_a=_opt_str(r.get("label_a")),
            label_b=_opt_str(r.get("label_b")),
            rationale_a=_opt_str(r.get("rationale_a")),
            rationale_b=_opt_str(r.get("rationale_b")),
            judged_at=r.get("judged_at"),
        )
        for r in rows
    ]
    return ComparisonItemList(items=items)


# ----- background runner ---------------------------------------------------


async def _run_comparison(
    pool: asyncpg.Pool,
    ch: ClickHouseQuery,
    comparison_id: UUID,
    workspace_id: UUID,
) -> None:
    """Score every dataset item on both sides, write paired ClickHouse rows.

    Real LLM dispatch: each variant's prompt template is rendered with the
    dataset item's `input` (parsed as JSON, or wrapped as `{"input": "..."}`
    when not JSON), dispatched via the shared `playground._dispatch` against
    the variant's `prompt_version.model_params.model` + temperature +
    max_tokens. The dispatched output text is what the judge scores.

    Each successful dispatch writes a real ClickHouse `run` + `span` row with
    `sdk='comparison'` so both sides are visible at `/runs` and the trace
    can be inspected just like any other run. The eval_score row carries
    the new run_id in the run_id slot (instead of the dataset item_id) so
    the pairing query joins paired runs, not paired item rows. The
    item_id is preserved on `metadata` for cross-reference.
    """
    try:
        cmp_row = await pool.fetchrow(
            """
            select id, project_id, dataset_id, judge_kind,
                   prompt_version_id_a, prompt_version_id_b
            from comparison where id = $1
            """,
            comparison_id,
        )
        if cmp_row is None:
            return

        await pool.execute(
            "update comparison set status='running', started_at=now() where id=$1",
            comparison_id,
        )

        versions = await pool.fetch(
            """
            select id, template, model_params
            from prompt_version
            where id = any($1::uuid[])
            """,
            [cmp_row["prompt_version_id_a"], cmp_row["prompt_version_id_b"]],
        )
        ver_by_id = {r["id"]: r for r in versions}
        variant_a = _build_variant("a", ver_by_id.get(cmp_row["prompt_version_id_a"]))
        variant_b = _build_variant("b", ver_by_id.get(cmp_row["prompt_version_id_b"]))

        items = await _fetch_dataset_items(ch, cmp_row["project_id"], cmp_row["dataset_id"])
        await pool.execute(
            "update comparison set item_total=$2 where id=$1",
            comparison_id,
            len(items),
        )

        rows: list[tuple[Any, ...]] = []
        score_sum_a = 0.0
        score_sum_b = 0.0
        judged_at = datetime.utcnow()
        project_id = cmp_row["project_id"]
        org_id, _ = await resolve_tenant_ids(pool, project_id)
        for item in items:
            output_a, run_id_a, err_a = await _dispatch_variant(
                pool,
                project_id,
                comparison_id,
                variant_a,
                item,
            )
            output_b, run_id_b, err_b = await _dispatch_variant(
                pool,
                project_id,
                comparison_id,
                variant_b,
                item,
            )
            score_a, label_a, rationale_a = _judge(
                cmp_row["judge_kind"], output_a, item["expected"]
            )
            score_b, label_b, rationale_b = _judge(
                cmp_row["judge_kind"], output_b, item["expected"]
            )
            # If dispatch failed, override the score → 0 / fail and prepend
            # the error to the rationale; we never silently drop (ER-23).
            if err_a is not None:
                score_a, label_a = 0.0, "fail"
                rationale_a = f"dispatch failed: {err_a}; {rationale_a}"
            if err_b is not None:
                score_b, label_b = 0.0, "fail"
                rationale_b = f"dispatch failed: {err_b}; {rationale_b}"
            score_sum_a += score_a
            score_sum_b += score_b

            # Write the per-side trace before the eval_score row so the
            # run exists when reviewers click through.
            if run_id_a is not None:
                await _write_comparison_trace(
                    ch,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    project_id=cmp_row["project_id"],
                    run_id=run_id_a,
                    variant=variant_a,
                    rendered=variant_a.last_rendered,
                    output=output_a,
                    item=item,
                    comparison_id=comparison_id,
                    error=err_a,
                )
            if run_id_b is not None:
                await _write_comparison_trace(
                    ch,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    project_id=cmp_row["project_id"],
                    run_id=run_id_b,
                    variant=variant_b,
                    rendered=variant_b.last_rendered,
                    output=output_b,
                    item=item,
                    comparison_id=comparison_id,
                    error=err_b,
                )

            rows.append(
                _score_row(
                    org_id,
                    workspace_id,
                    cmp_row["project_id"],
                    item["item_id"],
                    comparison_id,
                    "cmp:a",
                    score_a,
                    label_a,
                    rationale_a,
                    output_a,
                    judged_at,
                    run_id=run_id_a,
                    ok=err_a is None,
                )
            )
            rows.append(
                _score_row(
                    org_id,
                    workspace_id,
                    cmp_row["project_id"],
                    item["item_id"],
                    comparison_id,
                    "cmp:b",
                    score_b,
                    label_b,
                    rationale_b,
                    output_b,
                    judged_at,
                    run_id=run_id_b,
                    ok=err_b is None,
                )
            )

        if rows:
            try:
                await ch.insert(
                    "eval_score",
                    rows,
                    column_names=[
                        "org_id",
                        "workspace_id",
                        "project_id",
                        "run_id",
                        "span_id",
                        "eval_config_id",
                        "judge_name",
                        "judge_endpoint",
                        "judge_version",
                        "score",
                        "label",
                        "rationale",
                        "raw_output",
                        "outcome",
                        "judged_at",
                        "cost_usd",
                    ],
                )
            except Exception as exc:  # noqa: BLE001
                await _mark_failed(pool, comparison_id, f"clickhouse insert: {exc}")
                return

        n = len(items)
        avg_a = (score_sum_a / n) if n else None
        avg_b = (score_sum_b / n) if n else None
        await pool.execute(
            """
            update comparison
            set status='done',
                item_done_a=$2, item_done_b=$3,
                score_sum_a=$4, score_sum_b=$5,
                score_avg_a=$6, score_avg_b=$7,
                finished_at=now()
            where id=$1
            """,
            comparison_id,
            n,
            n,
            score_sum_a,
            score_sum_b,
            avg_a,
            avg_b,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "comparison run failed",
            comparison_id=str(comparison_id),
            error=str(exc),
        )
        await _mark_failed(pool, comparison_id, str(exc))


def _score_row(
    org_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    item_id: UUID,
    comparison_id: UUID,
    side: str,
    score: float,
    label: str,
    rationale: str,
    raw_output: str,
    judged_at: datetime,
    *,
    run_id: str | None = None,
    ok: bool = True,
) -> tuple[Any, ...]:
    # If a real run was dispatched, carry its id in the run_id slot so
    # the FULL OUTER JOIN in /items pairs the dispatched runs (the
    # paired item_id is preserved on the rationale for cross-ref).
    # When dispatch was skipped (no items / fall-through) we keep the
    # legacy item_id-as-run_id shape.
    return (
        str(org_id),
        str(workspace_id),
        str(project_id),
        run_id if run_id is not None else str(item_id),
        None,  # span_id
        str(comparison_id),  # eval_config_id = our comparison id
        side,  # 'cmp:a' or 'cmp:b'
        "comparison",  # judge_endpoint
        "v2",  # judge_version (v2 = real dispatch)
        float(score),
        label,
        rationale,
        raw_output[:8000],  # cap raw_output
        "ok" if ok else "failed",
        judged_at,
        0,  # cost_usd (model cost is on run row)
    )


# ---- variant + dispatch ---------------------------------------------------


class _Variant:
    """Materialized per-side dispatch config, populated from prompt_version."""

    __slots__ = (
        "side",
        "version_id",
        "template",
        "model",
        "provider",
        "temperature",
        "max_tokens",
        "last_rendered",
    )

    def __init__(
        self,
        *,
        side: str,
        version_id: UUID | None,
        template: str,
        model: str,
        provider: str,
        temperature: float | None,
        max_tokens: int,
    ) -> None:
        self.side = side
        self.version_id = version_id
        self.template = template
        self.model = model
        self.provider = provider
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.last_rendered = ""


def _build_variant(side: str, row: asyncpg.Record | None) -> _Variant:
    """Pull (template, model, temperature, max_tokens) from a prompt_version
    row. Falls back to a stub provider when `model_params.model` is missing
    so a malformed prompt version doesn't crash the run — the dispatch
    still produces an output we can score (echo of the rendered prompt)
    and the operator sees the fallback in the trace metadata.
    """
    template = ""
    model = "stub-comparison"
    temperature: float | None = None
    max_tokens = _DEFAULT_MAX_TOKENS
    version_id: UUID | None = None
    if row is not None:
        version_id = row["id"]
        template = row["template"] or ""
        params = row["model_params"]
        if isinstance(params, str):
            try:
                params = _json.loads(params)
            except (TypeError, ValueError):
                params = None
        if isinstance(params, dict):
            if isinstance(params.get("model"), str) and params["model"]:
                model = params["model"]
            t = params.get("temperature")
            if isinstance(t, (int, float)):
                temperature = float(t)
            mt = params.get("max_tokens")
            if isinstance(mt, int) and mt > 0:
                max_tokens = mt
    try:
        provider = playground_module._resolve_provider(model)
    except HTTPException:
        # Unknown model prefix: fall back to stub so the comparison still
        # finishes. The trace metadata records why.
        model = f"stub-{model}"
        provider = "stub"
    return _Variant(
        side=side,
        version_id=version_id,
        template=template,
        model=model,
        provider=provider,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _render_template(template: str, variables: dict[str, Any]) -> str:
    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            return match.group(0)
        value = variables[key]
        if isinstance(value, str):
            return value
        try:
            return _json.dumps(value)
        except (TypeError, ValueError):
            return str(value)

    return _VAR_RE.sub(_repl, template)


def _item_variables(item: dict[str, Any]) -> dict[str, Any]:
    """Coerce dataset_item.input into a {{var}}-substitutable dict.

    Inputs are stored as opaque JSON strings; we try to parse first so
    `{"question": "..."}` substitutes naturally. Non-JSON or non-dict
    inputs become `{"input": "<raw>"}` so the template can reference
    `{{input}}`. `expected` is exposed too in case the prompt wants to
    reference it (rare but harmless).
    """
    raw = item.get("input") or ""
    parsed: Any = None
    if isinstance(raw, str) and raw:
        try:
            parsed = _json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
    variables: dict[str, Any] = {"input": raw, "expected": item.get("expected") or ""}
    if isinstance(parsed, dict):
        for k, v in parsed.items():
            if isinstance(k, str):
                variables[k] = v
    return variables


async def _dispatch_variant(
    pool: asyncpg.Pool,
    project_id: UUID,
    comparison_id: UUID,
    variant: _Variant,
    item: dict[str, Any],
) -> tuple[str, str, str | None]:
    """Render template + dispatch the LLM via the gateway.

    Returns (output, run_id, error). On dispatch failure we still return
    a `run_id` (the new uuid we generated) and a non-None error so the
    trace + score row record the failure attempt rather than silent-drop
    (ER-23).
    """
    rendered = _render_template(variant.template, _item_variables(item))
    variant.last_rendered = rendered
    run_id = str(_uuid.uuid4())

    if variant.provider == "stub":
        # Stub bypasses LiteLLM entirely. Used as the fallback when the
        # comparison is configured against a model whose provider prefix
        # we don't know.
        output = f"[stub:{variant.model}] {rendered[-512:]}"
        return output, run_id, None

    from ..llm import DispatchError, Message
    from ..llm import dispatch as gateway_dispatch

    bare_model = variant.model
    if not bare_model.startswith(variant.provider + "/"):
        bare_model = f"{variant.provider}/{bare_model}"
    try:
        result = await gateway_dispatch(
            pool,
            project_id=project_id,
            surface="comparisons",
            surface_ref_id=comparison_id,
            model=bare_model,
            messages=[Message(role="user", content=rendered)],
            temperature=variant.temperature,
            max_tokens=variant.max_tokens,
        )
        return result.text, run_id, None
    except DispatchError as exc:
        log.warning(
            "comparison dispatch failed",
            side=variant.side,
            provider=variant.provider,
            model=variant.model,
            code=exc.code,
            detail=exc.detail,
        )
        return "", run_id, f"[{exc.code}] {exc.detail}"


async def _write_comparison_trace(
    ch: ClickHouseQuery,
    *,
    org_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    run_id: str,
    variant: _Variant,
    rendered: str,
    output: str,
    item: dict[str, Any],
    comparison_id: UUID,
    error: str | None,
) -> None:
    """Write a real `run` + `span` row with `sdk='comparison'`.

    Best-effort: a ClickHouse blip shouldn't fail the whole comparison.
    Failures here are logged; the score row still goes in via the
    batched insert downstream.
    """
    now = datetime.now(UTC)
    inputs_json = _json.dumps({"prompt": rendered})
    outputs_json = _json.dumps({"output": output})
    metadata = _json.dumps(
        {
            "comparison": True,
            "comparison_id": str(comparison_id),
            "comparison_side": variant.side,
            "dataset_item_id": str(item.get("item_id")) if item.get("item_id") else None,
            "prompt_version_id": str(variant.version_id) if variant.version_id else None,
            "fallback_provider": variant.provider == "stub" and variant.model.startswith("stub-"),
        }
    )
    status_str = "ok" if error is None else "error"
    error_kind = "" if error is None else "DispatchError"
    error_message = "" if error is None else error[:2000]
    try:
        await ch.insert(
            "run",
            [
                (
                    str(org_id),
                    str(workspace_id),
                    str(project_id),
                    run_id,
                    None,
                    f"comparison:{variant.side}:{variant.model}",
                    "llm",
                    status_str,
                    now,
                    now,
                    now,
                    inputs_json,
                    outputs_json,
                    None,
                    None,
                    0,
                    0,
                    0,
                    0,
                    "comparison",
                    str(comparison_id),
                    "",
                    ["comparison", f"side:{variant.side}"],
                    metadata,
                    error_kind,
                    error_message,
                    1,
                )
            ],
            column_names=[
                "org_id",
                "workspace_id",
                "project_id",
                "run_id",
                "parent_run_id",
                "name",
                "kind",
                "status",
                "start_time",
                "end_time",
                "received_at",
                "inputs",
                "outputs",
                "inputs_obj_ref",
                "outputs_obj_ref",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost_usd",
                "sdk",
                "session_id",
                "user_id",
                "tags",
                "metadata",
                "error_kind",
                "error_message",
                "schema_version",
            ],
        )
        await ch.insert(
            "span",
            [
                (
                    str(org_id),
                    str(workspace_id),
                    str(project_id),
                    run_id,
                    str(_uuid.uuid4()),
                    None,
                    f"comparison:{variant.model}",
                    "llm",
                    status_str,
                    now,
                    now,
                    now,
                    variant.model,
                    variant.temperature,
                    inputs_json,
                    outputs_json,
                    None,
                    None,
                    0,
                    0,
                    0,
                    0,
                    metadata,
                    error_kind,
                    error_message,
                    1,
                )
            ],
            column_names=[
                "org_id",
                "workspace_id",
                "project_id",
                "run_id",
                "span_id",
                "parent_span_id",
                "name",
                "kind",
                "status",
                "start_time",
                "end_time",
                "received_at",
                "model",
                "temperature",
                "inputs",
                "outputs",
                "inputs_obj_ref",
                "outputs_obj_ref",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost_usd",
                "attributes",
                "error_kind",
                "error_message",
                "schema_version",
            ],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "comparison trace write failed",
            run_id=run_id,
            error=str(exc),
        )


async def _fetch_dataset_items(
    ch: ClickHouseQuery, project_id: UUID, dataset_id: UUID
) -> list[dict[str, Any]]:
    sql = """
        select item_id, input, expected
        from dataset_item final
        where project_id = {project_id:UUID}
          and dataset_id = {dataset_id:UUID}
          and deleted_at is null
        order by created_at asc
        limit 5000
    """
    params = {
        "project_id": str(project_id),
        "dataset_id": str(dataset_id),
    }
    return await ch.query(sql, parameters=params)


def _judge(kind: str, output_text: str, expected: str) -> tuple[float, str, str]:
    """Built-in deterministic judges. No LLM call, no API key."""
    if kind == "echo":
        return (1.0, "pass", "echo: smoke-test, always 1.0")
    if kind == "exact":
        ok = output_text == expected
        return (
            1.0 if ok else 0.0,
            "pass" if ok else "fail",
            "exact match" if ok else "output != expected",
        )
    if kind == "contains":
        ok = bool(expected) and (expected in output_text)
        return (
            1.0 if ok else 0.0,
            "pass" if ok else "fail",
            "expected found in output" if ok else "expected not in output",
        )
    return (0.0, "fail", f"unknown judge kind: {kind}")


async def _mark_failed(pool: asyncpg.Pool, comparison_id: UUID, reason: str) -> None:
    await pool.execute(
        """
        update comparison
        set status='failed', error=$2, finished_at=now()
        where id=$1
        """,
        comparison_id,
        reason[:2000],
    )


# ----- helpers -------------------------------------------------------------


async def _fetch_comparison(pool: asyncpg.Pool, comparison_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        select id, project_id, dataset_id,
               prompt_version_id_a, prompt_version_id_b,
               judge_kind, name, status,
               item_total, item_done_a, item_done_b,
               score_avg_a, score_avg_b,
               error, started_at, finished_at, created_at, updated_at
        from comparison
        where id = $1
        """,
        comparison_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "comparison not found")
    return row


async def _assert_project_role(
    pool: asyncpg.Pool,
    project_id: UUID,
    principal: Principal,
    *,
    allowed: tuple[str, ...],
) -> UUID:
    workspace_id = await pool.fetchval(
        "select workspace_id from project where id = $1 and deleted_at is null",
        project_id,
    )
    if workspace_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    await assert_workspace_role(
        pool,
        user_id=principal.user_id,
        workspace_id=workspace_id,
        allowed=allowed,
    )
    return workspace_id


def _require_clickhouse(request: Request) -> ClickHouseQuery:
    ch: ClickHouseQuery | None = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "clickhouse not configured (set LANGPROBE_CLICKHOUSE_URL)",
        )
    return ch


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if s else None
