# dev-deps

Single-replica Postgres / ClickHouse / Redis applied directly with `kubectl`
— **not** managed by the Helm chart.

Purpose: let the GKE deploy of `langprobe` reach Ready without requiring
managed databases up front. Postgres and ClickHouse run as **StatefulSets
backed by a `standard-rwo` PersistentVolumeClaim** (`volumeClaimTemplates`)
so their data survives pod restarts and rescheduling. This matters on GKE
Autopilot, where pods are recreated routinely (node auto-repair,
bin-packing, upgrades) — the earlier `emptyDir` setup silently wiped every
table on each pod move, which took down `app.langprobe.com` (all
Postgres-backed features 500'd until the schema was re-migrated). The PVCs
are **not** deleted when the StatefulSet is deleted; remove them explicitly
(`kubectl delete pvc -n langprobe data-postgres-0 data-clickhouse-0`) when
you tear these down.

> **Security:** these are throwaway pods with no NetworkPolicy in front of
> them, so any workload that can reach the `postgres`/`clickhouse` Services
> in the namespace can talk to them. They must NOT hold real customer data.
> Their password is **not** a committed default — Postgres and ClickHouse
> read it from the `langprobe-db-bootstrap` Secret (keys `postgres_password`
> / `clickhouse_password`) via `secretKeyRef`. Replace them with managed
> databases before serving production traffic.

## Apply

Create the credential Secret first (random, idempotent), then apply:

```bash
kubectl -n langprobe get secret langprobe-db-bootstrap >/dev/null 2>&1 \
  || kubectl -n langprobe create secret generic langprobe-db-bootstrap \
       --from-literal=postgres_password="$(openssl rand -hex 24)" \
       --from-literal=clickhouse_password="$(openssl rand -hex 24)"

kubectl apply -n langprobe -f deploy/k8s/dev-deps/
```

## Replace with managed services

Edit the three k8s secrets created during bootstrap to point at the new
endpoints, then `kubectl rollout restart deploy -n langprobe`:

- `langprobe-postgres` (key `dsn`)
- `langprobe-clickhouse` (key `url`)
- `langprobe-redis` (key `url`)

(`langprobe-session` is unrelated — it stays.)

Once switched, delete the dev-deps:

```bash
kubectl delete -n langprobe -f deploy/k8s/dev-deps/
```

## Why StatefulSets + PVCs?

A StatefulSet + PVC is the right answer when you want the data to survive a
pod restart — which, once these back a live deploy, you do. The previous
`emptyDir` setup treated them as throwaway ("you'll replace these within a
week"), but on Autopilot that guarantee is a data-loss bug: any pod
reschedule wipes the database. Postgres and ClickHouse are therefore
StatefulSets with `volumeClaimTemplates`; Redis stays `emptyDir` because it
is a cache with no source-of-truth data to lose.

These are still a stopgap. The real fix is a managed database (Cloud SQL /
ClickHouse Cloud) with backups, a strong rotated password, and a
NetworkPolicy — see below.
