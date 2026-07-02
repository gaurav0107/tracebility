# dev-deps

Ephemeral, single-replica Postgres / ClickHouse / Redis applied directly with
`kubectl` — **not** managed by the Helm chart.

Purpose: let the first GKE deploy of `langprobe` reach Ready without
requiring managed databases up front. Storage is `emptyDir`; deleting a pod
loses the data.

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

## Why not StatefulSets?

A StatefulSet + PVC is the right answer when you actually want the data to
survive a pod restart. The premise of dev-deps is "you'll replace these
within a week" — adding PVCs adds GCE Persistent Disks that you then have to
remember to delete. `emptyDir` makes the disposable nature explicit.
