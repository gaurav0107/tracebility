"""Public end-user feedback ingest.

The browser-side feedback SDK posts here using a `tbf_pub_<32 hex>`
public key (see [feedback_keys.py]). Possession of the key is the
credential — it has no secret half — so authorization is the trio:

  1. key prefix + format must match (`tbf_pub_` + 32 hex chars),
  2. the key row must exist and be unrevoked,
  3. the request Origin must satisfy `allowed_origins` if the key has
     any (server-to-server callers with no `Origin` header are allowed
     through; CORS itself is the browser's job).

Each accepted feedback writes one ClickHouse `eval_score` row with
`judge_name='user'` so dashboards can query feedback alongside
LLM-judge scores from the same store. We deliberately do NOT audit per
submission — volume can be high and the audit value is on key
issuance/revocation, which `feedback_keys.py` already covers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..clickhouse_client import ClickHouseQuery
from ..tenant_scope import resolve_tenant_ids

log = structlog.get_logger("langprobe.api.feedback")

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])

# When no eval config is tied to a feedback row, ClickHouse still wants a UUID.
_NO_EVAL_CONFIG = "00000000-0000-0000-0000-000000000000"


class FeedbackIn(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    run_id: UUID
    score: float = Field(ge=0.0, le=1.0)
    kind: str = Field(default="thumbs", min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=2000)
    end_user_id: str | None = Field(default=None, max_length=128)


class FeedbackAck(BaseModel):
    accepted: bool
    run_id: UUID


@router.post(
    "",
    response_model=FeedbackAck,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_feedback(request: Request, body: FeedbackIn) -> FeedbackAck:
    pool: asyncpg.Pool = request.app.state.pg
    ch: ClickHouseQuery | None = request.app.state.clickhouse

    public_id = _parse_key(body.key)
    key_row = await pool.fetchrow(
        """
        select id, project_id, allowed_origins
        from feedback_public_key
        where public_id = $1 and revoked_at is null
        """,
        public_id,
    )
    if key_row is None:
        # 401 — same shape whether the key never existed or was revoked.
        # ER-20: revocation is immediate; never serve a cached "ok" here.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or revoked feedback key",
        )

    _check_origin(request, list(key_row["allowed_origins"] or []))

    if ch is None:
        # ER-23: never silent-drop. Tell the SDK so it can buffer and retry.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "feedback store unavailable",
        )

    label = body.label or ("pass" if body.score >= 0.5 else "fail")
    raw_output = json.dumps(
        {
            "kind": body.kind,
            "comment": body.comment,
            "end_user_id": body.end_user_id,
        },
        separators=(",", ":"),
    )
    judged_at = datetime.now(UTC)
    org_id, workspace_id = await resolve_tenant_ids(pool, key_row["project_id"])

    try:
        await ch.insert(
            "eval_score",
            [
                (
                    str(org_id),
                    str(workspace_id),
                    str(key_row["project_id"]),
                    str(body.run_id),
                    None,  # span_id
                    _NO_EVAL_CONFIG,  # eval_config_id
                    "user",  # judge_name
                    "browser",  # judge_endpoint
                    "v1",  # judge_version
                    float(body.score),
                    label,
                    body.comment or "",
                    raw_output,
                    "ok",
                    judged_at,
                    0,  # cost_usd
                )
            ],
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
        log.warning(
            "feedback clickhouse insert failed",
            project_id=str(key_row["project_id"]),
            run_id=str(body.run_id),
            error=str(exc),
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "feedback store unavailable",
        ) from exc

    # Best-effort key bookkeeping — never fail the request on this.
    try:
        await pool.execute(
            "update feedback_public_key set last_used_at = now() where id = $1",
            key_row["id"],
        )
    except Exception as exc:  # noqa: BLE001
        log.info("feedback last_used_at touch failed", error=str(exc))

    return FeedbackAck(accepted=True, run_id=body.run_id)


# ----- helpers -------------------------------------------------------------


def _parse_key(raw: str) -> str:
    if not raw.startswith("tbf_pub_"):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid feedback key format",
        )
    public_id = raw[len("tbf_pub_") :]
    if len(public_id) != 32 or not all(c in "0123456789abcdef" for c in public_id):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid feedback key format",
        )
    return public_id


def _check_origin(request: Request, allowed: list[str]) -> None:
    if not allowed:
        # Empty allowlist = any origin (server-to-server included).
        return
    origin = request.headers.get("origin")
    if origin is None:
        # No Origin header = not a browser request; CORS is the browser's
        # gate. Trust the key and let it through.
        return
    if origin not in allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "origin not allowed for this feedback key",
        )
