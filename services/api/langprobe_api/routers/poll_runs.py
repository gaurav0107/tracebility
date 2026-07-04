"""Panel-of-LLM-Judges (PoLL) eval runs.

A poll_run scores every dataset item with N judges (built-in
echo / contains / exact in v1; LLM-judges slot in next iteration via
the same dispatch surface as the playground). For each item we write
N eval_score rows tagged with the per-judge name; consensus + agreement
get computed at read time via GROUP BY on the ClickHouse side.

Why a separate router from evals.py?
  - The single-judge eval surface is intentionally one judge per row;
    fanning out to N judges from that endpoint would mean breaking
    the existing semantics of `judge_kind` and `score_avg`.
  - The aggregation strategy (mean/majority/min/max) and the agreement
    metric (pairwise) are properties of the panel, not of any judge.

Boundaries:
  - POST inserts the queued row, kicks off `BackgroundTasks._run_poll`,
    returns 202.
  - The runner fetches items once, scores each on every judge, batches
    one ClickHouse insert with all (item × judge) rows, computes the
    aggregate metrics, updates postgres counters.
  - Audit-fail-closed on every write (ER-10).
  - RBAC: list/get for any role; create for owner/admin/member; no
    destructive ops in v1.
"""

from __future__ import annotations

from datetime import datetime
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
from . import luna_judges
from .evals import _judge  # reuse the built-in deterministic judge bench

log = structlog.get_logger("langprobe.api.poll_runs")

router = APIRouter(prefix="/v1/poll-runs", tags=["poll-runs"])

_JUDGE_KINDS = {"echo", "contains", "exact"}
_AGGREGATIONS = {"mean", "majority", "min", "max"}
_MIN_JUDGES = 2
_MAX_JUDGES = 5


class PollRunOut(BaseModel):
    id: UUID
    project_id: UUID
    dataset_id: UUID
    name: str | None
    judges: list[str]
    aggregation: str
    status: str
    item_total: int
    item_done: int
    consensus_avg: float | None
    agreement: float | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PollRunCreate(BaseModel):
    project_id: UUID
    dataset_id: UUID
    judges: list[str] = Field(min_length=_MIN_JUDGES, max_length=_MAX_JUDGES)
    aggregation: str = Field(default="mean", max_length=16)
    name: str | None = Field(default=None, max_length=255)


class PollItemRow(BaseModel):
    item_id: UUID | None
    consensus: float
    scores: dict[str, float]
    labels: dict[str, str]
    rationales: dict[str, str]
    judged_at: datetime | None


class PollItemList(BaseModel):
    items: list[PollItemRow]


@router.get("", response_model=list[PollRunOut])
async def list_runs(
    request: Request,
    project_id: UUID = Query(...),
    principal: Principal = Depends(require_user),
) -> list[PollRunOut]:
    pool: asyncpg.Pool = request.app.state.pg
    await _assert_project_role(
        pool,
        principal,
        project_id,
        ("owner", "admin", "member", "viewer"),
    )
    rows = await pool.fetch(
        """
        select id, project_id, dataset_id, name, judges, aggregation,
               status, item_total, item_done, consensus_avg, agreement,
               error, started_at, finished_at, created_at, updated_at
          from poll_run
         where project_id = $1
         order by created_at desc
        """,
        project_id,
    )
    return [PollRunOut(**dict(r)) for r in rows]


@router.post(
    "",
    response_model=PollRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    request: Request,
    body: PollRunCreate,
    background: BackgroundTasks,
    principal: Principal = Depends(require_user),
) -> PollRunOut:
    if body.aggregation not in _AGGREGATIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"aggregation must be one of {sorted(_AGGREGATIONS)}",
        )
    unique_judges = sorted(set(body.judges))
    if len(unique_judges) < _MIN_JUDGES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"need ≥{_MIN_JUDGES} distinct judges",
        )
    pool: asyncpg.Pool = request.app.state.pg
    for j in unique_judges:
        base_kind, luna_slug = luna_judges.parse_judge_kind(j)
        if luna_slug is not None:
            judge_row = await luna_judges.resolve_judge(pool, body.project_id, luna_slug)
            if judge_row is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    f"luna judge '{luna_slug}' not found in this project",
                )
        elif base_kind not in _JUDGE_KINDS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown judge_kind '{j}' (allowed: {sorted(_JUDGE_KINDS)} or 'luna:<slug>')",
            )

    workspace_id = await _assert_project_role(
        pool, principal, body.project_id, ("owner", "admin", "member")
    )
    dataset = await pool.fetchrow(
        """select id, project_id, item_count from dataset
             where id = $1 and deleted_at is null""",
        body.dataset_id,
    )
    if dataset is None or dataset["project_id"] != body.project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")

    row = await pool.fetchrow(
        """
        insert into poll_run (
            project_id, dataset_id, name, judges, aggregation,
            status, item_total, created_by
        )
        values ($1, $2, $3, $4, $5, 'queued', $6, $7)
        returning id, project_id, dataset_id, name, judges, aggregation,
                  status, item_total, item_done, consensus_avg, agreement,
                  error, started_at, finished_at, created_at, updated_at
        """,
        body.project_id,
        body.dataset_id,
        body.name,
        unique_judges,
        body.aggregation,
        dataset["item_count"],
        principal.user_id,
    )
    assert row is not None
    poll_row = PollRunOut(**dict(row))

    await audit.record(
        pool,
        principal=principal,
        action="poll_run.create",
        target_kind="poll_run",
        target_id=poll_row.id,
        payload={
            "dataset_id": str(poll_row.dataset_id),
            "judges": unique_judges,
            "aggregation": body.aggregation,
        },
        request=request,
        workspace_id=workspace_id,
        project_id=poll_row.project_id,
    )

    ch = getattr(request.app.state, "clickhouse", None)
    if ch is None:
        await _mark_failed(pool, poll_row.id, "clickhouse not configured")
    else:
        background.add_task(_run_poll, pool, ch, poll_row.id)
    return poll_row


@router.get("/{poll_id}", response_model=PollRunOut)
async def get_run(
    request: Request,
    poll_id: UUID,
    principal: Principal = Depends(require_user),
) -> PollRunOut:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_poll(pool, poll_id)
    await _assert_project_role(
        pool,
        principal,
        row["project_id"],
        ("owner", "admin", "member", "viewer"),
    )
    return PollRunOut(**dict(row))


@router.get("/{poll_id}/items", response_model=PollItemList)
async def list_items(
    request: Request,
    poll_id: UUID,
    limit: int = Query(default=500, ge=1, le=5000),
    principal: Principal = Depends(require_user),
) -> PollItemList:
    pool: asyncpg.Pool = request.app.state.pg
    row = await _fetch_poll(pool, poll_id)
    await _assert_project_role(
        pool,
        principal,
        row["project_id"],
        ("owner", "admin", "member", "viewer"),
    )
    ch = _require_clickhouse(request)

    sql = """
        select run_id as item_id,
               judge_name,
               score,
               label,
               rationale,
               judged_at
          from eval_score
         where project_id = {project_id:UUID}
           and eval_config_id = {poll_id:UUID}
         order by judged_at desc
         limit {limit:UInt32}
    """
    params = {
        "project_id": str(row["project_id"]),
        "poll_id": str(poll_id),
        "limit": limit,
    }
    try:
        rows = await ch.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("poll items query failed", error=str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "data plane unavailable") from exc

    aggregation = row["aggregation"]
    judges = list(row["judges"])

    by_item: dict[str, dict[str, Any]] = {}
    for r in rows:
        item_id = str(r["item_id"]) if r["item_id"] is not None else None
        if item_id is None:
            continue
        bucket = by_item.setdefault(
            item_id,
            {
                "item_id": r["item_id"],
                "scores": {},
                "labels": {},
                "rationales": {},
                "judged_at": r.get("judged_at"),
            },
        )
        judge = str(r.get("judge_name") or "")
        if judge:
            bucket["scores"][judge] = float(r.get("score") or 0)
            bucket["labels"][judge] = str(r.get("label") or "")
            bucket["rationales"][judge] = str(r.get("rationale") or "")

    items: list[PollItemRow] = []
    for bucket in by_item.values():
        scores = bucket["scores"]
        consensus = _aggregate(aggregation, [scores[j] for j in judges if j in scores])
        items.append(
            PollItemRow(
                item_id=bucket["item_id"],
                consensus=consensus,
                scores=scores,
                labels=bucket["labels"],
                rationales=bucket["rationales"],
                judged_at=bucket["judged_at"],
            )
        )
    items.sort(key=lambda i: i.consensus)
    return PollItemList(items=items)


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------


async def _run_poll(
    pool: asyncpg.Pool,
    ch: ClickHouseQuery,
    poll_id: UUID,
) -> None:
    """Score every item with every judge, batch one ClickHouse insert."""
    try:
        poll = await pool.fetchrow(
            """select id, project_id, dataset_id, judges, aggregation
                 from poll_run where id = $1""",
            poll_id,
        )
        if poll is None:
            return
        org_id, workspace_id = await resolve_tenant_ids(pool, poll["project_id"])

        judges: list[str] = list(poll["judges"])
        aggregation: str = poll["aggregation"]

        await pool.execute(
            "update poll_run set status='running', started_at=now() where id=$1",
            poll_id,
        )

        # Resolve any luna judges once up-front; abort with an honest
        # error if a slug went missing between create and run.
        luna_rows: dict[str, dict[str, Any]] = {}
        for judge in judges:
            base, slug = luna_judges.parse_judge_kind(judge)
            if slug is not None:
                row = await luna_judges.resolve_judge(pool, poll["project_id"], slug)
                if row is None:
                    await _mark_failed(
                        pool,
                        poll_id,
                        f"luna judge '{slug}' not found at run time",
                    )
                    return
                luna_rows[judge] = row

        items = await _fetch_dataset_items(ch, poll["project_id"], poll["dataset_id"])
        await pool.execute(
            "update poll_run set item_total=$2 where id=$1",
            poll_id,
            len(items),
        )

        rows: list[tuple[Any, ...]] = []
        per_item_scores: dict[str, list[tuple[str, float]]] = {}
        judged_at = datetime.utcnow()
        for item in items:
            scores_for_item: list[tuple[str, float]] = []
            for judge in judges:
                if judge in luna_rows:
                    luna_row = luna_rows[judge]
                    score, label, rationale, raw_output = await luna_judges.apply_luna_judge(
                        luna_row,
                        pool=pool,
                        project_id=poll["project_id"],
                        surface="poll",
                        surface_ref_id=poll_id,
                        input_text=item["input"],
                        expected=item["expected"],
                    )
                    judge_endpoint = luna_row["provider"]
                    outcome = "ok" if label != "error" else "failed"
                else:
                    score, label, rationale = _judge(judge, item["input"], item["expected"])
                    raw_output = ""
                    judge_endpoint = "builtin"
                    outcome = "ok"
                scores_for_item.append((judge, score))
                rows.append(
                    (
                        str(org_id),
                        str(workspace_id),
                        str(poll["project_id"]),
                        str(item["item_id"]),  # carry item_id in run_id slot
                        None,  # span_id
                        str(poll_id),  # eval_config_id = poll_run.id
                        judge,  # judge_name (carries luna:slug)
                        judge_endpoint,  # judge_endpoint
                        "v1",  # judge_version
                        float(score),
                        label,
                        rationale,
                        raw_output,  # raw_output (LLM response, if any)
                        outcome,
                        judged_at,
                        0,  # cost_usd
                    )
                )
            per_item_scores[str(item["item_id"])] = scores_for_item

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
                await _mark_failed(pool, poll_id, f"clickhouse insert: {exc}")
                return

        # Aggregate metrics over the in-memory per-item scores. Avoids
        # a round-trip back to ClickHouse for what we just wrote.
        consensus_avg = _compute_consensus_avg(aggregation, per_item_scores)
        agreement = _compute_pairwise_agreement(per_item_scores)

        await pool.execute(
            """
            update poll_run
               set status='done',
                   item_done=$2,
                   consensus_avg=$3,
                   agreement=$4,
                   finished_at=now()
             where id=$1
            """,
            poll_id,
            len(items),
            consensus_avg,
            agreement,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("poll run failed", poll_id=str(poll_id), error=str(exc))
        await _mark_failed(pool, poll_id, str(exc))


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _aggregate(strategy: str, scores: list[float]) -> float:
    if not scores:
        return 0.0
    if strategy == "mean":
        return sum(scores) / len(scores)
    if strategy == "min":
        return min(scores)
    if strategy == "max":
        return max(scores)
    if strategy == "majority":
        passed = sum(1 for s in scores if s >= 0.5)
        return 1.0 if passed * 2 > len(scores) else 0.0
    return sum(scores) / len(scores)


def _compute_consensus_avg(
    strategy: str,
    per_item_scores: dict[str, list[tuple[str, float]]],
) -> float | None:
    if not per_item_scores:
        return None
    total = 0.0
    for entries in per_item_scores.values():
        total += _aggregate(strategy, [s for _, s in entries])
    return total / len(per_item_scores)


def _compute_pairwise_agreement(
    per_item_scores: dict[str, list[tuple[str, float]]],
) -> float | None:
    """Binary pairwise agreement: across all (item, judge_pair) cells,
    fraction where both judges classified the item the same way (using
    a 0.5 threshold). 1.0 = perfect agreement; 0.5 = chance for binary.
    """
    if not per_item_scores:
        return None
    matches = 0
    pairs = 0
    for entries in per_item_scores.values():
        n = len(entries)
        if n < 2:
            continue
        for i in range(n):
            for j in range(i + 1, n):
                pairs += 1
                a = entries[i][1] >= 0.5
                b = entries[j][1] >= 0.5
                if a == b:
                    matches += 1
    if pairs == 0:
        return None
    return matches / pairs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_poll(pool: asyncpg.Pool, poll_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        select id, project_id, dataset_id, name, judges, aggregation,
               status, item_total, item_done, consensus_avg, agreement,
               error, started_at, finished_at, created_at, updated_at
          from poll_run
         where id = $1
        """,
        poll_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "poll_run not found")
    return row


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


async def _mark_failed(pool: asyncpg.Pool, poll_id: UUID, reason: str) -> None:
    await pool.execute(
        """update poll_run
              set status='failed', error=$2, finished_at=now()
            where id=$1""",
        poll_id,
        reason[:2000],
    )


async def _assert_project_role(
    pool: asyncpg.Pool,
    principal: Principal,
    project_id: UUID,
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
