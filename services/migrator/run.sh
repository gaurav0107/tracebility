#!/usr/bin/env bash
set -euo pipefail

PG_DSN="${LANGPROBE_PG_DSN:?LANGPROBE_PG_DSN must be set}"
CH_URL="${LANGPROBE_CLICKHOUSE_URL:?LANGPROBE_CLICKHOUSE_URL must be set}"

echo "==> Postgres: collecting applied migrations"
applied="$(psql -At -c "SELECT version FROM schema_migrations" "$PG_DSN" 2>/dev/null || true)"

count_applied=0
count_skipped=0
for f in /schemas/postgres/*.sql; do
  v="$(basename "$f" .sql)"
  if printf '%s\n' "$applied" | grep -qx "$v"; then
    echo "  skip: $v"
    count_skipped=$((count_skipped + 1))
    continue
  fi
  echo "  apply: $v"
  psql -v ON_ERROR_STOP=1 -f "$f" "$PG_DSN" >/dev/null
  count_applied=$((count_applied + 1))
done
echo "==> Postgres: applied $count_applied, skipped $count_skipped"

# ClickHouse migrations are version-tracked via the `schema_migrations`
# table (created by 0000_schema_migrations.sql, always run first; its
# CREATE IF NOT EXISTS is itself idempotent). Subsequent migration files
# only run if their version isn't already in schema_migrations. Each file
# may use any DDL — CREATE, ALTER, RENAME, DROP — without needing to be
# self-idempotent.
echo "==> ClickHouse: applying migrations with version tracking"

# We talk to ClickHouse over HTTP rather than the native binary protocol,
# because the DSN we get (LANGPROBE_CLICKHOUSE_URL) already targets the
# HTTP endpoint. clickhouse-client's URL form isn't supported in 24.x and
# adding the binary would balloon the image by ~480 MB.
#
# DSN convention in this project is http(s)://USER:PASS@HOST:PORT/DB.
# Over HTTP, ClickHouse expects the database as a query param, not a path
# segment — rewrite "/<db>" -> "/?database=<db>" before sending.
ch_re='^(https?://[^/]+)/([^?]+)(\?.*)?$'
if [[ "$CH_URL" =~ $ch_re ]]; then
  ch_endpoint="${BASH_REMATCH[1]}/?database=${BASH_REMATCH[2]}${BASH_REMATCH[3]:+&${BASH_REMATCH[3]:1}}"
else
  ch_endpoint="$CH_URL"   # already has /?... form, send as-is
fi

# Helper: POST one statement to the CH HTTP endpoint, fail loud on non-200.
# Usage: ch_post "<sql>" "<context-for-error-msg>"
ch_post() {
  local stmt="$1"
  local ctx="$2"
  local http_status
  http_status=$(printf '%s' "$stmt" | curl -sS -o /tmp/ch_response -w "%{http_code}" \
    --data-binary @- "$ch_endpoint" || true)
  if [ "$http_status" != "200" ]; then
    echo "ERROR: ClickHouse returned HTTP $http_status on statement from $ctx"
    cat /tmp/ch_response
    exit 1
  fi
}

# Apply 0000 unconditionally so the tracking table exists.
# ClickHouse HTTP endpoint doesn't accept multi-statement bodies (no
# `multi_statements` setting in 24.x), so split files on `;` followed by
# newline, drop empty/comment-only chunks, and POST each statement.
apply_file() {
  local f="$1"
  while IFS= read -r -d '' stmt; do
    # Skip chunks that carry no SQL (pure comment / blank). Delete full-line
    # `--` comments FIRST, line by line, then collapse whitespace. Order
    # matters: collapsing whitespace first joins every line into one, so a
    # single leading `--` would swallow the rest of the chunk and a real
    # statement sitting under a comment line would look empty and get dropped.
    # That silent drop is exactly what skipped `add column end_user_id` in
    # 0008 (its next statement then failed with NO_SUCH_COLUMN_IN_TABLE).
    case "$(printf '%s' "$stmt" | sed -E '/^[[:space:]]*--/d' | tr -d '[:space:]')" in
      "") continue ;;
    esac
    ch_post "$stmt" "$(basename "$f")"
  done < <(awk 'BEGIN{RS=";\n"; ORS="\0"} { print }' "$f")
}

bootstrap_file="/schemas/clickhouse/0000_schema_migrations.sql"
if [ -f "$bootstrap_file" ]; then
  echo "  bootstrap: $(basename "$bootstrap_file" .sql)"
  apply_file "$bootstrap_file"
fi

# Read which versions are already applied. The query format is TabSeparated;
# one version per line. An empty result is fine (fresh DB, just bootstrapped).
ch_applied="$(printf '%s' "select version from schema_migrations" | curl -sS \
  --data-binary @- "$ch_endpoint" || true)"

ch_applied_count=0
ch_skipped_count=0
for f in /schemas/clickhouse/*.sql; do
  v="$(basename "$f" .sql)"
  # 0000 was bootstrapped above; record-if-missing so subsequent runs skip.
  if [ "$v" = "0000_schema_migrations" ]; then
    if ! printf '%s\n' "$ch_applied" | grep -qx "$v"; then
      ch_post "insert into schema_migrations (version) values ('$v')" "$v (record bootstrap)"
    fi
    continue
  fi
  if printf '%s\n' "$ch_applied" | grep -qx "$v"; then
    echo "  skip: $v"
    ch_skipped_count=$((ch_skipped_count + 1))
    continue
  fi
  echo "  apply: $v"
  apply_file "$f"
  ch_post "insert into schema_migrations (version) values ('$v')" "$v (record apply)"
  ch_applied_count=$((ch_applied_count + 1))
done
echo "==> ClickHouse: applied $ch_applied_count, skipped $ch_skipped_count"

echo "==> migrator: done"
