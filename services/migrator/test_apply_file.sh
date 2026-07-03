#!/usr/bin/env bash
# Regression test for apply_file's chunk selection in run.sh.
#
# Bug (fixed 2026-07-03): the "is this chunk empty?" check collapsed all
# whitespace (`tr -d '[:space:]'`) BEFORE stripping `--` comments. With the
# newlines gone, a single leading `--` made `sed 's/--[^\n]*//'` swallow the
# rest of the chunk, so a real SQL statement sitting under a comment line
# looked empty and was silently dropped. That skipped `add column end_user_id`
# in 0008_end_user_id.sql; the next statement (`add end_user_metadata AFTER
# end_user_id`) then failed with NO_SUCH_COLUMN_IN_TABLE and the Helm
# pre-upgrade migrator job died with BackoffLimitExceeded.
#
# Fix: strip full-line comments FIRST (line by line), THEN collapse whitespace.
# Run: bash services/migrator/test_apply_file.sh
set -euo pipefail

RUN_SH="$(cd "$(dirname "$0")" && pwd)/run.sh"

# Mirrors run.sh's predicate. The grep guard below binds this test to run.sh
# so a revert to the buggy order fails here too.
chunk_has_sql() {
  local check
  check="$(printf '%s' "$1" | sed -E '/^[[:space:]]*--/d' | tr -d '[:space:]')"
  [ -n "$check" ]
}

fail=0
assert() { # desc want(keep|skip) stmt
  local got
  if chunk_has_sql "$3"; then got=keep; else got=skip; fi
  if [ "$got" = "$2" ]; then echo "ok: $1"; else echo "FAIL: $1 (want $2, got $got)"; fail=1; fi
}

# The regression: a comment line directly above a real statement must be KEPT.
assert "comment-led statement is kept" keep \
  "$(printf -- '-- child spans inherit it via run_id\n\nalter table run add column if not exists end_user_id Nullable(String)')"
# Pure comment / blank chunks are correctly dropped.
assert "comment-only chunk is skipped" skip \
  "$(printf -- '-- 0008_end_user_id.sql\n-- End-user identity: distinguish operator from end user')"
assert "blank chunk is skipped" skip "$(printf -- '\n\n')"
assert "plain statement is kept" keep "alter table run add index idx_x x type bloom_filter granularity 4"

# Guard: run.sh must strip comments BEFORE collapsing whitespace.
if grep -qF "sed -E '/^[[:space:]]*--/d' | tr -d '[:space:]'" "$RUN_SH"; then
  echo "ok: run.sh strips comments before collapsing whitespace"
else
  echo "FAIL: run.sh does not use the fixed comment-then-whitespace order"; fail=1
fi

if [ "$fail" = 0 ]; then echo "PASS"; else echo "FAILED"; exit 1; fi
