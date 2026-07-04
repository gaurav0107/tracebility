"""cluster_failures verb (Task 3, D2-A).

Groups failing runs in a project + time window by error kind, tool
name, or status, so an operator (or an agent) can see which failure
mode dominates before drafting an eval. Query-param style mirrors
``routers/threads_query.py`` (clickhouse-connect's ``{name:Type}``
binding) — no raw string interpolation of caller-controlled values
into SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langprobe_tenant.context import TenantContext

from langprobe_api.verbs.deps import VerbDeps
from langprobe_api.verbs.models import Cluster, ClusterFailuresIn, ClusterFailuresOut
from langprobe_api.verbs.scope import require_project_scope

# Window is clamped to this many hours (30 days) regardless of what the
# caller asks for — an unbounded window would let a single verb call
# scan a project's entire history.
MAX_WINDOW_HOURS = 720

# Result size cap: this is a triage view, not a paginated export.
MAX_CLUSTERS = 50

# Fixed map from the Pydantic-constrained `group_by` literal to the
# ClickHouse column it groups by. This is the only place the value is
# allowed to influence SQL — it's never interpolated directly, only
# used as a dict key so an unlisted value can't reach the query string.
GROUP_BY_COLUMNS: dict[str, str] = {
    "error": "error_kind",
    "tool": "name",
    "status": "status",
}

_SQL = """
    select {col} as key, count() as count,
           arraySlice(groupArray(run_id), 1, 5) as sample_run_ids
    from run final
    where project_id = {{project_id:UUID}}
      and status = 'error'
      and start_time >= {{since:DateTime64(9)}}
    group by key
    order by count desc
    limit {{limit:UInt32}}
"""


async def cluster_failures(
    deps: VerbDeps, ctx: TenantContext, params: ClusterFailuresIn
) -> ClusterFailuresOut:
    require_project_scope(ctx, params.project_id)

    clamped_hours = min(params.window_hours, MAX_WINDOW_HOURS)
    since = datetime.now(UTC) - timedelta(hours=clamped_hours)

    col = GROUP_BY_COLUMNS[params.group_by]
    sql = _SQL.format(col=col)
    query_params = {
        "project_id": str(ctx.project_id),
        "since": since,
        "limit": MAX_CLUSTERS,
    }

    rows = await deps.ch.query(sql, parameters=query_params)

    clusters = [
        Cluster(
            key=str(row["key"]),
            count=int(row["count"]),
            sample_run_ids=list(row["sample_run_ids"]),
        )
        for row in rows
    ]
    return ClusterFailuresOut(clusters=clusters)
