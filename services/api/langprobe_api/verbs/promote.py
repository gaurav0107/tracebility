"""promote_to_recurring verb (Task 6, D2-A) — turn a ready draft into a
recurring judge.

A ``backtest_draft`` that reached ``ready`` (an operator has reviewed
its backtest results — caught/missed counts) can be promoted into a
real, recurring ``luna_judge`` that later eval runs reference by slug.
Two properties matter here:

APPROVAL GATE (security boundary, layer 2): promoting a draft mutates
production judge config, so it must never happen silently on an
agent's say-so. ``params.approval_token`` being non-blank is checked
here as a defense-in-depth backstop. The PRIMARY enforcement — that
only a human-issued token reaches this verb at all — belongs to the
HTTP router (Task 7), which is expected to mint/validate the token
against an actual approval flow (e.g. a UI click) before ever calling
this function. This verb-level check exists so that even if the
router's guard is ever bypassed or misconfigured, a call with an
empty/blank token still cannot create a judge.

IDEMPOTENCY: an agent driving this verb may retry a call whose response
it never saw (timeout, crash, etc.). To make that safe, the judge's
slug is derived deterministically from a hash of the draft's
``judge_config`` (``proposed-<sha256[:12]>``) rather than a random ID.
The INSERT either succeeds, or collides with a prior promotion of the
same config under ``luna_judge_slug_uniq (project_id, slug)`` — in
which case we look up and return that existing judge's id instead of
erroring. A retry converges on the same judge; it never creates a
duplicate.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import asyncpg
from langprobe_tenant.context import TenantContext

from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.lifecycle import DraftStatus
from langprobe_api.verbs.models import PromoteIn, PromoteOut
from langprobe_api.verbs.scope import ScopeError, require_project_scope

# Model a promoted judge runs under. Matches the proposer model used to
# draft it (verbs/propose.py's PROPOSER_MODEL) so a promoted judge's
# scoring behavior isn't a surprise relative to what backtested it.
PROMOTED_JUDGE_MODEL = "claude-3-5-haiku-latest"

# Default watch rule stamped on promotion. luna scores are higher-is-better,
# so we fire when the judge's windowed average quality drops below this.
_WATCH_THRESHOLD = 0.5
_WATCH_COMPARATOR = "<"


class ApprovalRequiredError(Exception):
    """Raised when ``approval_token`` is empty/blank. No judge is
    created and the draft is left untouched."""


class DraftNotReadyError(Exception):
    """Raised when the draft's status is not ``ready``. Only a draft
    that has been backtested and reviewed can be promoted."""


def _config_hash(judge_config: dict[str, Any]) -> str:
    """Deterministic sha256 hex digest of ``judge_config``, independent
    of key order — the canonical form is ``json.dumps`` with
    ``sort_keys=True`` and no extra whitespace."""
    canonical = json.dumps(judge_config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _slug_for_config(judge_config: dict[str, Any]) -> str:
    """Slug for the promoted judge, derived from the config hash.
    Matches ``luna_judge_slug_format``'s ``^[a-z0-9][a-z0-9_-]*$`` —
    hex digits and hyphens only."""
    return f"proposed-{_config_hash(judge_config)[:12]}"


async def promote_to_recurring(deps: VerbDeps, ctx: TenantContext, params: PromoteIn) -> PromoteOut:
    draft = await deps.pool.fetchrow(
        """
        select id, project_id, org_id, cluster_ref, judge_kind, judge_config,
               status, created_by, created_at, heartbeat_at, error
        from backtest_draft
        where id = $1
        """,
        params.draft_id,
    )
    if draft is None:
        raise ScopeError(f"backtest_draft {params.draft_id} not found")
    require_project_scope(ctx, draft["project_id"])

    if not params.approval_token or not params.approval_token.strip():
        raise ApprovalRequiredError("promote_to_recurring requires a non-blank approval_token")

    if draft["status"] != DraftStatus.READY.value:
        raise DraftNotReadyError(
            f"backtest_draft {draft['id']} is not ready (status={draft['status']!r})"
        )

    judge_config = draft["judge_config"] or {}
    slug = _slug_for_config(judge_config)
    prompt = judge_config.get("prompt", "")

    judge_id = await _insert_or_get_judge(
        deps.pool,
        project_id=draft["project_id"],
        slug=slug,
        prompt=prompt,
        draft_id=draft["id"],
        created_by=ctx.api_key_id,
        schedule_seconds=params.schedule_seconds,
    )

    await _provision_watch_rule(
        deps.pool,
        project_id=draft["project_id"],
        judge_id=judge_id,
        slug=slug,
        schedule_seconds=params.schedule_seconds,
        created_by=ctx.api_key_id,
    )

    await deps.pool.execute(
        """
        update backtest_draft
        set status = $2
        where id = $1
        """,
        draft["id"],
        DraftStatus.PROMOTED.value,
    )

    return PromoteOut(judge_id=judge_id)


async def _insert_or_get_judge(
    pool: Any,
    *,
    project_id: UUID,
    slug: str,
    prompt: str,
    draft_id: UUID,
    created_by: UUID | None,
    schedule_seconds: int,
) -> UUID:
    """Insert a new ``luna_judge`` for (project_id, slug); on a unique
    violation (a prior promotion already created it), fetch and return
    that existing judge's id instead. Idempotent by construction.

    Promotion is what makes a judge *recurring*: it stamps
    ``is_recurring``, the cadence, and ``scored_through = now()`` so the
    scheduler scores forward from promotion (the backtest already covered
    history — see 0031_recurring_judges)."""
    try:
        row = await pool.fetchrow(
            """
            insert into luna_judge (
                project_id, slug, name, description, rubric_prompt,
                output_format, provider, model, created_by,
                is_recurring, schedule_seconds, recurring_enabled, scored_through
            )
            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10, true, now())
            returning id
            """,
            project_id,
            slug,
            f"Proposed judge {slug}",
            f"promoted from draft {draft_id}",
            prompt,
            "score-rationale",
            "anthropic",
            PROMOTED_JUDGE_MODEL,
            created_by,
            schedule_seconds,
        )
        assert row is not None
        return row["id"]
    except asyncpg.UniqueViolationError:
        existing = await pool.fetchrow(
            """
            select id from luna_judge
            where project_id = $1 and slug = $2
            """,
            project_id,
            slug,
        )
        assert existing is not None
        return existing["id"]


async def _provision_watch_rule(
    pool: Any,
    *,
    project_id: UUID,
    judge_id: UUID,
    slug: str,
    schedule_seconds: int,
    created_by: UUID | None,
) -> None:
    """Create the default judge_score_avg alert rule that watches this
    recurring judge. Idempotent via alert_rule_judge_watch_uniq, so a
    promote retry never creates a second rule. The alert window is aligned
    to the scoring cadence, clamped to the column's 60..86400 bound."""
    window_seconds = max(60, min(schedule_seconds, 86400))
    await pool.execute(
        """
        insert into alert_rule (
            project_id, name, metric, comparator, threshold,
            window_seconds, subject_id, enabled, created_by
        )
        values ($1, $2, 'judge_score_avg', $3, $4, $5, $6, true, $7)
        on conflict (subject_id, metric) where subject_id is not null do nothing
        """,
        project_id,
        f"Judge {slug} quality watch",
        _WATCH_COMPARATOR,
        _WATCH_THRESHOLD,
        window_seconds,
        judge_id,
        created_by,
    )
