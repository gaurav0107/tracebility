"""Static property test: every ClickHouse INSERT into a tenant-aware table
must carry ``org_id`` and ``workspace_id`` in its ``column_names``.

Companion to ``test_property_org_id_in_where.py`` (which guards the read
path). That test only inspects SQL string literals, so it can't see the
``ch.insert("run", rows, column_names=[...])`` write path used by the api
service — inserts go through the clickhouse-connect client, not raw SQL.

Migration 0006 made ``org_id``/``workspace_id`` part of the ORDER BY on
run/span/eval_score/dataset_item/etc. Those columns are non-nullable UUIDs
with no DEFAULT, so an insert that omits them lands the row under the
zero-UUID tenant — silently breaking isolation. This test walks the api
package, finds every ``*.insert(<tenant_table>, ...)`` call, and asserts the
``column_names`` list names both tenant columns.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TENANT_TABLES = frozenset(
    {
        "run",
        "span",
        "eval_score",
        "eval_aggregate",
        "replay_capture",
        "replay_run",
        "dataset_item",
        "billing_meter",
    }
)
_REQUIRED_COLUMNS = ("org_id", "workspace_id")


def _api_package_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "langprobe_api"


def _string_items(node: ast.AST) -> list[str] | None:
    """Return the constant-string items of a list/tuple literal, or None if
    the node isn't a literal sequence of strings we can read statically."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    items: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            items.append(elt.value)
    return items


def _insert_offenders_in_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "insert"):
            continue
        # First positional arg is the table name.
        if not node.args:
            continue
        table_arg = node.args[0]
        if not (isinstance(table_arg, ast.Constant) and isinstance(table_arg.value, str)):
            continue
        if table_arg.value not in _TENANT_TABLES:
            continue
        # Find the column_names keyword. A name reference to a module constant
        # can't be read statically here (skipped — asserted where the constant
        # is defined); literal lists that omit tenant columns are flagged.
        col_kw = next((kw for kw in node.keywords if kw.arg == "column_names"), None)
        if col_kw is None:
            offenders.append(
                f"{path.name}:{node.lineno}: insert into '{table_arg.value}' "
                f"has no column_names= (relies on column order)"
            )
            continue
        cols = _string_items(col_kw.value)
        if cols is None:
            # e.g. column_names=list(SOME_CONSTANT) — validated where the
            # constant is defined; the replay_run columns are asserted below.
            continue
        missing = [c for c in _REQUIRED_COLUMNS if c not in cols]
        if missing:
            offenders.append(
                f"{path.name}:{node.lineno}: insert into '{table_arg.value}' "
                f"omits {missing} from column_names"
            )
    return offenders


def test_every_insert_carries_tenant_columns():
    offenders: list[str] = []
    for path in sorted(_api_package_dir().rglob("*.py")):
        offenders.extend(_insert_offenders_in_file(path))
    assert not offenders, "tenant columns missing on ClickHouse insert:\n  - " + "\n  - ".join(
        offenders
    )


def test_replay_run_columns_constant_carries_tenant_columns():
    """replay/service.py inserts via ``column_names=list(REPLAY_RUN_COLUMNS)``;
    assert the constant (defined in replay/record.py) names the tenant columns."""
    src = (_api_package_dir() / "replay" / "record.py").read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        # Handle both plain and annotated (``NAME: tuple = (...)``) assignments.
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        else:
            continue
        if "REPLAY_RUN_COLUMNS" not in names or value is None:
            continue
        cols = _string_items(value)
        assert cols is not None, "REPLAY_RUN_COLUMNS is not a literal sequence"
        missing = [c for c in _REQUIRED_COLUMNS if c not in cols]
        assert not missing, f"REPLAY_RUN_COLUMNS omits {missing}"
        found = True
    assert found, "REPLAY_RUN_COLUMNS assignment not found — did it move?"
