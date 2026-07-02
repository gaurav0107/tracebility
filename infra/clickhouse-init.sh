#!/bin/sh
# ClickHouse schema bootstrap for the local docker-compose stack.
#
# Why this exists: the official clickhouse-server entrypoint runs
# /docker-entrypoint-initdb.d/*.sql with `clickhouse-client` but WITHOUT
# `--database`, so unqualified DDL (our schema files are database-agnostic
# by design — the migrator selects the DB via the DSN) lands in `default`.
# The api / ingest-worker / ingest-api all connect to the `langprobe`
# database (CLICKHOUSE_DB), which would then be empty, and every data-plane
# query 503s with UNKNOWN_TABLE. Applying each migration explicitly into
# $CLICKHOUSE_DB keeps the compose stack consistent with the migrator and
# the api's connection string.
#
# Runs once, on first boot only (empty data dir), same as the *.sql init it
# replaces. Schema files are mounted read-only at /schemas/clickhouse.
set -e

for f in /schemas/clickhouse/*.sql; do
  echo "clickhouse-init: applying $(basename "$f") -> ${CLICKHOUSE_DB}"
  clickhouse-client \
    --host 127.0.0.1 \
    -u "$CLICKHOUSE_USER" \
    --password "$CLICKHOUSE_PASSWORD" \
    --database "$CLICKHOUSE_DB" \
    --multiquery < "$f"
done
