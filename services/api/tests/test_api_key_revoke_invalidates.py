"""Regression guard: revoking an api key must publish the resolver's
cache-invalidation event.

``resolver.py`` documents the contract: "any code path that mutates an
api_key row (revoke, scope edit) MUST publish the public_id on Redis pubsub
channel ``apikey:invalidate``". Without it, ``require_ingest_key`` keeps
authenticating a revoked key from the resolver's positive cache for up to the
TTL (60s), because it only re-verifies ``secret_hash`` on a cache hit — never
``revoked_at``.

This is a static check (no app deps / no live redis) so it runs in unit CI.
An end-to-end test that asserts the ingest side actually stops accepting the
key belongs in ``tests/integration`` where a real Redis is available.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _revoke_fn() -> ast.AsyncFunctionDef:
    src = (
        Path(__file__).resolve().parents[1] / "langprobe_api" / "routers" / "api_keys.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "revoke_api_key":
            return node
    raise AssertionError("revoke_api_key not found — did it move or get renamed?")


def _calls(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_revoke_publishes_cache_invalidation():
    fn = _revoke_fn()
    calls = _calls(fn)
    assert "announce_invalidation" in calls, (
        "revoke_api_key no longer publishes announce_invalidation — a revoked "
        "key can keep authenticating ingest from the resolver cache for up to "
        "the positive-cache TTL"
    )


def test_revoke_selects_public_id():
    """announce_invalidation is keyed by public_id, so the revoke query must
    still fetch it."""
    fn = _revoke_fn()
    sql_literals = [
        node.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert any("public_id" in s for s in sql_literals), (
        "revoke_api_key must select api_key.public_id to publish the invalidation event"
    )
