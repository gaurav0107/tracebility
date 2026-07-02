# GKE bootstrap runbook

One-time setup before the `deploy` workflow can run. Idempotent: re-running
should be a no-op (commands either succeed or report "already exists").

Replace nothing in this runbook with shell variables — all values are
hard-coded so re-runs are deterministic.

## Prerequisites

- `gcloud` authenticated as a user with project-owner-equivalent perms on
  `project-c4ff4ea3-775a-4e0c-9a3`.
- `kubectl` installed.
- GKE cluster `langprobe-cluster-1` already exists in `asia-southeast2`.

## 1. Enable APIs

```bash
gcloud services enable \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  container.googleapis.com \
  artifactregistry.googleapis.com \
  --project=project-c4ff4ea3-775a-4e0c-9a3
```

## 2. Create the Artifact Registry repo

```bash
gcloud artifacts repositories create langprobe \
  --repository-format=docker \
  --location=asia-southeast2 \
  --description="langprobe container images" \
  --project=project-c4ff4ea3-775a-4e0c-9a3
```

If it already exists, `gcloud` returns `ALREADY_EXISTS`; ignore.

## 3. Create the deploy service account

```bash
gcloud iam service-accounts describe \
  langprobe-deploy@project-c4ff4ea3-775a-4e0c-9a3.iam.gserviceaccount.com \
  --project=project-c4ff4ea3-775a-4e0c-9a3 >/dev/null 2>&1 \
  || gcloud iam service-accounts create langprobe-deploy \
       --display-name="GitHub Actions deploy SA" \
       --project=project-c4ff4ea3-775a-4e0c-9a3
```

Email: `langprobe-deploy@project-c4ff4ea3-775a-4e0c-9a3.iam.gserviceaccount.com`.

## 4. Grant minimum IAM

```bash
SA=langprobe-deploy@project-c4ff4ea3-775a-4e0c-9a3.iam.gserviceaccount.com

gcloud artifacts repositories add-iam-policy-binding langprobe \
  --location=asia-southeast2 \
  --member="serviceAccount:${SA}" \
  --role=roles/artifactregistry.writer \
  --project=project-c4ff4ea3-775a-4e0c-9a3

gcloud projects add-iam-policy-binding project-c4ff4ea3-775a-4e0c-9a3 \
  --member="serviceAccount:${SA}" \
  --role=roles/container.developer

gcloud projects add-iam-policy-binding project-c4ff4ea3-775a-4e0c-9a3 \
  --member="serviceAccount:${SA}" \
  --role=roles/container.clusterViewer
```

The cluster's node pools also need read access to Artifact Registry so
kubelet can pull the images we push. On Autopilot the node identity is
the project's default compute service account:

```bash
PROJECT_NUMBER=$(gcloud projects describe project-c4ff4ea3-775a-4e0c-9a3 --format='value(projectNumber)')
gcloud artifacts repositories add-iam-policy-binding langprobe \
  --location=asia-southeast2 \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/artifactregistry.reader \
  --project=project-c4ff4ea3-775a-4e0c-9a3
```

Without this binding pods land in `ImagePullBackOff` with `403 Forbidden`
even though the deploy SA can push to AR fine. Default GCE compute SA
permissions cover most things but not cross-project / cross-region AR
reads, and they don't include AR reader on a freshly-created repo.

## 5. Create the WIF pool and provider

```bash
gcloud iam workload-identity-pools describe github-actions-pool \
  --location=global \
  --project=project-c4ff4ea3-775a-4e0c-9a3 >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create github-actions-pool \
       --location=global \
       --display-name="GitHub Actions" \
       --project=project-c4ff4ea3-775a-4e0c-9a3

gcloud iam workload-identity-pools providers describe github-actions-provider \
  --workload-identity-pool=github-actions-pool \
  --location=global \
  --project=project-c4ff4ea3-775a-4e0c-9a3 >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools providers create-oidc github-actions-provider \
       --workload-identity-pool=github-actions-pool \
       --location=global \
       --issuer-uri=https://token.actions.githubusercontent.com \
       --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
       --attribute-condition='assertion.repository == "langprobe/langprobe"' \
       --project=project-c4ff4ea3-775a-4e0c-9a3
```

## 6. Bind WIF principal → deploy SA

```bash
PROJECT_NUMBER=$(gcloud projects describe project-c4ff4ea3-775a-4e0c-9a3 --format='value(projectNumber)')

gcloud iam service-accounts add-iam-policy-binding \
  langprobe-deploy@project-c4ff4ea3-775a-4e0c-9a3.iam.gserviceaccount.com \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-actions-pool/attribute.repository/langprobe/langprobe" \
  --project=project-c4ff4ea3-775a-4e0c-9a3
```

```bash
# Print the full WIF provider resource name to copy into GitHub Variables:
echo "projects/$(gcloud projects describe project-c4ff4ea3-775a-4e0c-9a3 --format='value(projectNumber)')/locations/global/workloadIdentityPools/github-actions-pool/providers/github-actions-provider"
```

## 7. Get cluster credentials

```bash
gcloud container clusters get-credentials langprobe-cluster-1 \
  --region=asia-southeast2 \
  --project=project-c4ff4ea3-775a-4e0c-9a3
```

> ⚠️ **The `deploy/k8s/dev-deps/` Postgres/ClickHouse/Redis are throwaway
> single-replica pods on `emptyDir` (data is lost on restart) with no
> NetworkPolicy.** They exist only to get a fresh cluster to `Ready`
> before you point the app at managed databases. Do NOT treat them as a
> durable production data plane. For anything serving real customer
> traffic, replace them with managed Postgres/ClickHouse and set the app
> secrets in step 9 to those managed URLs.

## 8. Generate DB credentials, then create namespace + dev-deps

Generate the database password **once** and store it in a Secret. The
dev-deps Postgres/ClickHouse read their password from this Secret via
`secretKeyRef`, so there is no default password baked into the manifests.
The `get ... || create` guard is idempotent: re-running the runbook
reuses the existing credentials instead of regenerating them (which would
lock you out of existing data).

```bash
kubectl create namespace langprobe --dry-run=client -o yaml | kubectl apply -f -

# Random DB credentials, created only if they don't already exist.
kubectl -n langprobe get secret langprobe-db-bootstrap >/dev/null 2>&1 \
  || kubectl -n langprobe create secret generic langprobe-db-bootstrap \
       --from-literal=postgres_password="$(openssl rand -hex 24)" \
       --from-literal=clickhouse_password="$(openssl rand -hex 24)"

kubectl apply -n langprobe -f deploy/k8s/dev-deps/
kubectl rollout status -n langprobe deployment/postgres deployment/clickhouse deployment/redis --timeout=180s
```

## 9. Create k8s secrets

Build the app's DSN secrets from the generated DB password (never a
hardcoded literal). For a managed-database production setup, replace the
values below with your managed connection strings instead.

```bash
PG_PW="$(kubectl -n langprobe get secret langprobe-db-bootstrap -o jsonpath='{.data.postgres_password}' | base64 -d)"
CH_PW="$(kubectl -n langprobe get secret langprobe-db-bootstrap -o jsonpath='{.data.clickhouse_password}' | base64 -d)"

kubectl -n langprobe create secret generic langprobe-postgres \
  --from-literal=dsn="postgres://langprobe:${PG_PW}@postgres:5432/langprobe" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n langprobe create secret generic langprobe-clickhouse \
  --from-literal=url="http://langprobe:${CH_PW}@clickhouse:8123/langprobe" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n langprobe create secret generic langprobe-redis \
  --from-literal=url="redis://redis:6379/0" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n langprobe get secret langprobe-session >/dev/null 2>&1 \
  || kubectl -n langprobe create secret generic langprobe-session \
       --from-literal=secret="$(openssl rand -hex 32)"
```

Re-running the runbook will not regenerate the DB or session secrets.

## 10. OAuth signup setup (Google + GitHub)

Skip this section if you don't want third-party login. The chart's OAuth
block defaults to off — login still works for any other auth path the
app provides.

### 10a. Register the Google OAuth app

1. Open <https://console.cloud.google.com/apis/credentials>.
2. **+ CREATE CREDENTIALS → OAuth client ID**.
3. Application type: **Web application**.
4. Name: `langprobe (langprobe)`.
5. Authorized redirect URIs — add exactly:
   ```
   https://langprobe.daz.co.in/api/auth/oauth/google/callback
   ```
6. **Create**. Copy the Client ID and Client Secret somewhere you can paste from in step 10c (a password manager, or just a temporary text file you delete afterwards).

If the OAuth consent screen has never been configured for this project,
Google will prompt you through it before showing the credentials dialog.
Use **External** user type, fill the required app name + support email,
and you can skip the optional scopes / test users for now.

### 10b. Register the GitHub OAuth app

1. Open <https://github.com/settings/developers> → **OAuth Apps** → **New OAuth App**.
2. Application name: `langprobe (langprobe)`.
3. Homepage URL: `https://langprobe.daz.co.in`.
4. Authorization callback URL — exactly:
   ```
   https://langprobe.daz.co.in/api/auth/oauth/github/callback
   ```
5. **Register application**. Copy the Client ID. **Generate a new client secret** and copy that too.

### 10c. Create the k8s secret

The secret holds all four creds in one object. Re-running this block is
how you rotate any of them.

```bash
kubectl -n langprobe create secret generic langprobe-oauth \
  --from-literal=google_client_id="<paste google client id>" \
  --from-literal=google_client_secret="<paste google client secret>" \
  --from-literal=github_client_id="<paste github client id>" \
  --from-literal=github_client_secret="<paste github client secret>" \
  --dry-run=client -o yaml | kubectl apply -f -
```

To enable just one provider, pass only its two keys; the chart's
`secretKeyRef.optional: true` setting means the other provider's button
just stays hidden.

### 10d. Roll the API to pick up the secret

If the chart change is already live (deploy workflow ran), the api
deployment template is already mounting the env vars — but existing
pods cached the (then-empty) values at boot. Rollout-restart so new
pods read the populated secret:

```bash
kubectl rollout restart deployment/langprobe-api -n langprobe
kubectl rollout status deployment/langprobe-api -n langprobe --timeout=120s
```

Expected: `successfully rolled out`. Skip this if you're applying the
chart change for the first time after creating the secret — the regular
deploy workflow handles it.

### 10e. Verify

```bash
curl -sS https://langprobe.daz.co.in/api/auth/oauth/providers
```

Expected: `{"google":true,"github":true}` (or `true` for whichever
provider you configured). The web `/login` page should now render the
matching `Continue with` buttons.

## 11. Set GitHub repo variables

In GitHub: **Settings → Secrets and variables → Actions → Variables**.

| Name | Value |
|---|---|
| `WIF_PROVIDER` | Output of the `echo` command in Step 6. |
| `DEPLOY_SA_EMAIL` | `langprobe-deploy@project-c4ff4ea3-775a-4e0c-9a3.iam.gserviceaccount.com` |

These are repository **variables**, not secrets — neither value is sensitive
on its own (they're discoverable from any successful deploy log).

## 12. Verify

```bash
gcloud artifacts repositories describe langprobe --location=asia-southeast2
gcloud iam service-accounts describe langprobe-deploy@project-c4ff4ea3-775a-4e0c-9a3.iam.gserviceaccount.com
gcloud iam workload-identity-pools providers describe github-actions-provider \
  --workload-identity-pool=github-actions-pool --location=global
kubectl -n langprobe get pods,svc,secret
```

All commands should exit zero. The four secrets `langprobe-postgres`,
`-clickhouse`, `-redis`, `-session` should be present.
