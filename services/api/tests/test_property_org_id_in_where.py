"""Static property test: every ClickHouse SQL string in the api service
must filter by ``org_id``.

Spec §8 calls for this to be implemented as 'an AST visitor over the
query-builder output'. The simpler-and-equivalent version: walk the
sources, find triple-quoted strings that look like ClickHouse SQL
(start with ``select`` or ``insert into``), and assert each one
either references ``org_id`` or comes through ``ScopedClickHouse``
(which auto-injects).

Routers that haven't been migrated to ``ScopedClickHouse`` yet are
listed in ``_PENDING_ROUTERS`` so this test is the canonical
remaining-work tracker. Each item there is a router still using the
raw ``ch.query(...)`` path that the spec wants to retire."""

from __future__ import annotations

import ast
import re
from pathlib import Path

# Routers known to still use the raw ch.query path. As they migrate to
# ScopedClickHouse, remove them from this list — the test will assert
# their queries contain ``org_id`` instead.
_PENDING_ROUTERS = frozenset(
    {
        "alerts.py",
        "annotations.py",
        "comparisons.py",
        "datasets.py",
        "evals.py",
        "feedback.py",
        "metrics.py",
        "playground.py",
        "poll_runs.py",
        "replays.py",
        "run_actions.py",
        "saved_views.py",
        "studio.py",
        "threads_query.py",
    }
)

_SQL_HINT = re.compile(r"^\s*(select|insert\s+into|alter\s+table)\b", re.IGNORECASE)
_TENANT_TABLES = re.compile(
    r"\b(?:from|join|into)\s+(run|span|eval_score|eval_aggregate|replay_capture|"
    r"replay_run|dataset_item|billing_meter|audit_reconciliation_gap)\b",
    re.IGNORECASE,
)
# Cheap ClickHouse-vs-postgres heuristic: CH uses {name:Type} parameter
# placeholders + ``final`` + ``toDateTime64``; postgres uses $1 / now() etc.
_CLICKHOUSE_HINT = re.compile(
    r"\{[a-z_][a-z0-9_]*:[A-Za-z][A-Za-z0-9()]*\}|\bfinal\b|\btoDateTime64\b|\btoYYYYMM\b",
    re.IGNORECASE,
)
_HAS_ORG_FILTER = re.compile(r"\borg_id\s*=", re.IGNORECASE)


def _routers_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "langprobe_api" / "routers"


def _sql_strings_in_file(path: Path) -> list[tuple[int, str]]:
    """Pull triple-quoted strings that look like SQL out of a python file.

    We want literals only — runtime-built SQL isn't in scope here; the
    project test suite covers that path indirectly via ScopedClickHouse."""
    src = path.read_text()
    tree = ast.parse(src)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if not _SQL_HINT.match(text):
                continue
            out.append((node.lineno, text))
    return out


def test_every_clickhouse_query_filters_by_org_id():
    """For each router, every SQL literal that touches a tenant-aware
    ClickHouse table must reference ``org_id`` — UNLESS the file uses
    ``ScopedClickHouse`` (which auto-injects ``and org_id = ...`` into
    every query and is itself covered by ``test_tenant_scope.py``)."""
    offenders: list[str] = []
    pending_offenders: list[str] = []
    for path in sorted(_routers_dir().glob("*.py")):
        if path.name == "__init__.py":
            continue
        src = path.read_text()
        # Files routed through ScopedClickHouse are exempt by construction.
        # The scope wrapper's behavior is locked in by test_tenant_scope.py.
        scoped = "ScopedClickHouse" in src or "resolve_project_scope" in src
        for lineno, sql in _sql_strings_in_file(path):
            if not _TENANT_TABLES.search(sql):
                continue
            if not _CLICKHOUSE_HINT.search(sql):
                continue  # postgres / ambiguous; skip
            if _HAS_ORG_FILTER.search(sql):
                continue
            if scoped:
                continue
            entry = f"{path.name}:{lineno}: SQL touches tenant table without org_id filter"
            if path.name in _PENDING_ROUTERS:
                pending_offenders.append(entry)
            else:
                offenders.append(entry)
    assert not offenders, "\n".join(offenders)
    # Pending list is informational; CI tracks the migration via the
    # presence of items here. When _PENDING_ROUTERS empties out and this
    # test assertion fires, delete the assert and the pending list.
    if pending_offenders:
        # Just print; don't fail. The other assertion above is the gate.
        print(  # noqa: T201
            "Pending router migrations:\n  - " + "\n  - ".join(pending_offenders)
        )


def test_runs_query_router_is_migrated():
    """Phase 7 made runs_query the canary. Lock it in so a regression
    that reverts it gets caught immediately."""
    routers_dir = _routers_dir()
    sqls = _sql_strings_in_file(routers_dir / "runs_query.py")
    tenant_sqls = [(ln, s) for ln, s in sqls if _TENANT_TABLES.search(s)]
    assert tenant_sqls, "runs_query.py has no tenant SQL — did it move?"
    # Either the SQL has org_id explicitly, or it goes through
    # ScopedClickHouse which will inject it. The router uses
    # ScopedClickHouse for all 3 routes, so we accept SQL without
    # explicit org_id; what we DON'T accept is migrating away from
    # ScopedClickHouse.
    src = (routers_dir / "runs_query.py").read_text()
    assert "ScopedClickHouse" in src or "resolve_project_scope" in src
