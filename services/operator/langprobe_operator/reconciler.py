"""Manifest builders + apply loop.

We split this off from `main.py` so the manifest builder can be
unit-tested without touching kopf. ``build_manifests(spec, ...)``
returns the list of Kubernetes objects we want; ``reconcile(...)``
upserts them via server-side apply.

Keeping the manifest layout matched to the Helm chart's templates is
deliberate — every bug fix lands in two places (Helm yaml + this
function), but a langprobe install through the operator MUST land
on the same pod topology as a Helm install. Operators expect
documentation to apply equally.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class ReconcileSummary:
    ok: bool
    message: str
    timestamp: str


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


async def reconcile(*, name: str, namespace: str, spec: dict[str, Any]) -> ReconcileSummary:
    """Apply the manifests built from `spec` into `namespace`.

    Imports the kubernetes client lazily so unit tests can call
    `build_manifests` without a cluster.
    """
    manifests = build_manifests(name=name, namespace=namespace, spec=spec)

    try:
        from kubernetes import client  # type: ignore[import]
        from kubernetes import config as k8s_config
    except ImportError as exc:  # pragma: no cover
        return ReconcileSummary(
            ok=False,
            message=f"kubernetes client not installed: {exc}",
            timestamp=_now(),
        )

    try:
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
    except Exception as exc:  # noqa: BLE001
        return ReconcileSummary(
            ok=False,
            message=f"could not load kubeconfig: {exc}",
            timestamp=_now(),
        )

    api_apps = client.AppsV1Api()
    api_core = client.CoreV1Api()
    api_net = client.NetworkingV1Api()

    errors: list[str] = []
    for m in manifests:
        kind = m.get("kind")
        meta_name = m["metadata"]["name"]
        try:
            if kind == "Deployment":
                _server_side_apply(
                    api_apps.create_namespaced_deployment,
                    api_apps.patch_namespaced_deployment,
                    namespace,
                    m,
                    meta_name,
                )
            elif kind == "Service":
                _server_side_apply(
                    api_core.create_namespaced_service,
                    api_core.patch_namespaced_service,
                    namespace,
                    m,
                    meta_name,
                )
            elif kind == "Ingress":
                _server_side_apply(
                    api_net.create_namespaced_ingress,
                    api_net.patch_namespaced_ingress,
                    namespace,
                    m,
                    meta_name,
                )
            elif kind == "PersistentVolumeClaim":
                _server_side_apply(
                    api_core.create_namespaced_persistent_volume_claim,
                    api_core.patch_namespaced_persistent_volume_claim,
                    namespace,
                    m,
                    meta_name,
                )
            else:
                errors.append(f"unsupported kind {kind}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{kind}/{meta_name}: {exc}")

    if errors:
        return ReconcileSummary(
            ok=False,
            message="; ".join(errors)[:500],
            timestamp=_now(),
        )
    return ReconcileSummary(
        ok=True,
        message=f"applied {len(manifests)} objects",
        timestamp=_now(),
    )


# ---------------------------------------------------------------------------
# Manifest construction
# ---------------------------------------------------------------------------


def build_manifests(*, name: str, namespace: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the ordered list of k8s objects for one Langprobe CR."""
    image_cfg = spec.get("image") or {}
    registry = image_cfg.get("registry") or "ghcr.io/langprobe"
    tag = image_cfg.get("tag") or "latest"

    api = spec.get("api") or {}
    ingest_api = spec.get("ingestApi") or {}
    ingest_worker = spec.get("ingestWorker") or {}
    scheduler = spec.get("scheduler") or {}
    web = spec.get("web") or {}
    secrets = spec.get("secrets") or {}
    ingress = spec.get("ingress") or {}
    logging_cfg = spec.get("logging") or {}
    log_level = logging_cfg.get("level") or "INFO"

    owner_refs = [_owner_ref(name, namespace, spec)]

    api_name = f"{name}-api"
    ingest_api_name = f"{name}-ingest-api"
    ingest_worker_name = f"{name}-ingest-worker"
    scheduler_name = f"{name}-scheduler"
    web_name = f"{name}-web"

    base_labels = {
        "app.kubernetes.io/managed-by": "langprobe-operator",
        "app.kubernetes.io/instance": name,
        "app.kubernetes.io/part-of": "langprobe",
    }

    def labels(component: str) -> dict[str, str]:
        return {**base_labels, "app.kubernetes.io/component": component}

    def env_from_secret(env_name: str, ref: dict[str, Any] | None) -> dict[str, Any] | None:
        if not ref or not ref.get("name"):
            return None
        return {
            "name": env_name,
            "valueFrom": {
                "secretKeyRef": {
                    "name": ref["name"],
                    "key": ref.get("key") or "value",
                }
            },
        }

    def container_image(component_repo: str, override: str | None) -> str:
        return f"{registry}/{override or component_repo}:{tag}"

    api_port = 7081
    ingest_port = 7080
    web_port = 7090

    api_envs: list[dict[str, Any]] = [
        {"name": "LANGPROBE_BIND_HOST", "value": "0.0.0.0"},
        {"name": "LANGPROBE_API_BIND_PORT", "value": str(api_port)},
        {"name": "LANGPROBE_LOG_LEVEL", "value": log_level},
    ]
    for env, ref_key in (
        ("LANGPROBE_PG_DSN", "postgres"),
        ("LANGPROBE_CLICKHOUSE_URL", "clickhouse"),
        ("LANGPROBE_SESSION_SECRET", "session"),
    ):
        e = env_from_secret(env, secrets.get(ref_key))
        if e:
            api_envs.append(e)
    if api.get("publicApiBase"):
        api_envs.append(
            {
                "name": "LANGPROBE_CORS_ALLOW_ORIGIN",
                "value": api["publicApiBase"],
            }
        )

    ingest_envs: list[dict[str, Any]] = [
        {"name": "LANGPROBE_BIND_HOST", "value": "0.0.0.0"},
        {"name": "LANGPROBE_BIND_PORT", "value": str(ingest_port)},
        {"name": "LANGPROBE_LOG_LEVEL", "value": log_level},
        {
            "name": "LANGPROBE_DISK_BUFFER_PATH",
            "value": "/var/lib/langprobe/ingest-buffer",
        },
    ]
    for env, ref_key in (
        ("LANGPROBE_PG_DSN", "postgres"),
        ("LANGPROBE_REDIS_URL", "redis"),
    ):
        e = env_from_secret(env, secrets.get(ref_key))
        if e:
            ingest_envs.append(e)

    worker_envs: list[dict[str, Any]] = [
        {"name": "LANGPROBE_LOG_LEVEL", "value": log_level},
    ]
    for env, ref_key in (
        ("LANGPROBE_REDIS_URL", "redis"),
        ("LANGPROBE_CLICKHOUSE_URL", "clickhouse"),
    ):
        e = env_from_secret(env, secrets.get(ref_key))
        if e:
            worker_envs.append(e)

    scheduler_envs: list[dict[str, Any]] = [
        {"name": "LANGPROBE_LOG_LEVEL", "value": log_level},
    ]
    e = env_from_secret("LANGPROBE_PG_DSN", secrets.get("postgres"))
    if e:
        scheduler_envs.append(e)

    web_envs: list[dict[str, Any]] = [
        {"name": "NODE_ENV", "value": "production"},
        {
            "name": "API_BASE_INTERNAL",
            "value": f"http://{api_name}:{api_port}",
        },
    ]
    if web.get("publicApiBase") or api.get("publicApiBase"):
        web_envs.append(
            {
                "name": "NEXT_PUBLIC_API_BASE",
                "value": web.get("publicApiBase") or api.get("publicApiBase"),
            }
        )

    manifests: list[dict[str, Any]] = []

    # api
    manifests.append(
        _deployment(
            name=api_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("api"),
            replicas=int(api.get("replicas", 2)),
            container={
                "name": "api",
                "image": container_image("langprobe-api", api.get("repository")),
                "ports": [{"name": "http", "containerPort": api_port}],
                "env": api_envs,
                "readinessProbe": _http_probe(api_port, "/healthz", 5, 10),
                "livenessProbe": _http_probe(api_port, "/healthz", 15, 30),
                "resources": _default_resources(),
            },
        )
    )
    manifests.append(
        _service(
            name=api_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("api"),
            port=api_port,
        )
    )

    # ingest-api with disk buffer PVC
    pvc_name = f"{ingest_api_name}-buffer"
    manifests.append(
        _pvc(
            name=pvc_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("ingest-api"),
            size="5Gi",
        )
    )
    manifests.append(
        _deployment(
            name=ingest_api_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("ingest-api"),
            replicas=int(ingest_api.get("replicas", 2)),
            strategy="Recreate",
            container={
                "name": "ingest-api",
                "image": container_image("langprobe-ingest-api", ingest_api.get("repository")),
                "ports": [{"name": "http", "containerPort": ingest_port}],
                "env": ingest_envs,
                "readinessProbe": _http_probe(ingest_port, "/healthz", 5, 10),
                "livenessProbe": _http_probe(ingest_port, "/healthz", 15, 30),
                "resources": _default_resources(),
                "volumeMounts": [
                    {
                        "name": "ingest-buffer",
                        "mountPath": "/var/lib/langprobe/ingest-buffer",
                    }
                ],
            },
            volumes=[
                {
                    "name": "ingest-buffer",
                    "persistentVolumeClaim": {"claimName": pvc_name},
                }
            ],
        )
    )
    manifests.append(
        _service(
            name=ingest_api_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("ingest-api"),
            port=ingest_port,
        )
    )

    # ingest-worker (no service; redis consumer)
    manifests.append(
        _deployment(
            name=ingest_worker_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("ingest-worker"),
            replicas=int(ingest_worker.get("replicas", 2)),
            container={
                "name": "ingest-worker",
                "image": container_image(
                    "langprobe-ingest-worker", ingest_worker.get("repository")
                ),
                "env": worker_envs,
                "resources": _default_resources(),
            },
        )
    )

    # scheduler (no service; durable periodic ticks over the control plane)
    manifests.append(
        _deployment(
            name=scheduler_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("scheduler"),
            replicas=int(scheduler.get("replicas", 1)),
            container={
                "name": "scheduler",
                "image": container_image("langprobe-scheduler", scheduler.get("repository")),
                "env": scheduler_envs,
                "resources": _default_resources(),
            },
        )
    )

    # web
    manifests.append(
        _deployment(
            name=web_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("web"),
            replicas=int(web.get("replicas", 2)),
            container={
                "name": "web",
                "image": container_image("langprobe-web", web.get("repository")),
                "ports": [{"name": "http", "containerPort": web_port}],
                "env": web_envs,
                "readinessProbe": _http_probe(web_port, "/", 5, 10),
                "livenessProbe": _http_probe(web_port, "/", 30, 60),
                "resources": _default_resources(small=True),
            },
        )
    )
    manifests.append(
        _service(
            name=web_name,
            namespace=namespace,
            owner_refs=owner_refs,
            labels=labels("web"),
            port=web_port,
        )
    )

    if ingress.get("enabled"):
        manifests.append(
            _ingress(
                name=name,
                namespace=namespace,
                owner_refs=owner_refs,
                labels=base_labels,
                class_name=ingress.get("className"),
                hosts={
                    "web": (
                        ingress.get("web", {}).get("host"),
                        web_name,
                        web_port,
                    ),
                    "api": (
                        ingress.get("api", {}).get("host"),
                        api_name,
                        api_port,
                    ),
                    "ingest": (
                        ingress.get("ingest", {}).get("host"),
                        ingest_api_name,
                        ingest_port,
                    ),
                },
            )
        )
    return manifests


# ---------------------------------------------------------------------------
# Manifest helpers (each returns a plain dict; kubernetes client accepts these)
# ---------------------------------------------------------------------------


def _owner_ref(name: str, namespace: str, spec: dict[str, Any]) -> dict[str, Any]:
    # The actual UID/apiVersion come from the CR; kopf passes the CR
    # in via decorators and we synthesize from spec defaults here.
    # When we apply the manifest the kubernetes client honors the
    # block; cluster-side cascade-delete uses the UID we attach.
    uid = spec.get("__metadata_uid") or ""
    return {
        "apiVersion": "langprobe.io/v1alpha1",
        "kind": "Langprobe",
        "name": name,
        "uid": uid,
        "controller": True,
        "blockOwnerDeletion": True,
    }


def _deployment(
    *,
    name: str,
    namespace: str,
    owner_refs: list[dict[str, Any]],
    labels: dict[str, str],
    replicas: int,
    container: dict[str, Any],
    volumes: list[dict[str, Any]] | None = None,
    strategy: str = "RollingUpdate",
) -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "ownerReferences": owner_refs,
        },
        "spec": {
            "replicas": replicas,
            "strategy": {"type": strategy},
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [container],
                    **({"volumes": volumes} if volumes else {}),
                },
            },
        },
    }


def _service(
    *,
    name: str,
    namespace: str,
    owner_refs: list[dict[str, Any]],
    labels: dict[str, str],
    port: int,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "ownerReferences": owner_refs,
        },
        "spec": {
            "type": "ClusterIP",
            "selector": labels,
            "ports": [
                {
                    "name": "http",
                    "port": port,
                    "targetPort": "http",
                }
            ],
        },
    }


def _pvc(
    *,
    name: str,
    namespace: str,
    owner_refs: list[dict[str, Any]],
    labels: dict[str, str],
    size: str,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "ownerReferences": owner_refs,
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": size}},
        },
    }


def _ingress(
    *,
    name: str,
    namespace: str,
    owner_refs: list[dict[str, Any]],
    labels: dict[str, str],
    class_name: str | None,
    hosts: dict[str, tuple[str | None, str, int]],
) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []
    for _key, (host, svc_name, svc_port) in hosts.items():
        if not host:
            continue
        rules.append(
            {
                "host": host,
                "http": {
                    "paths": [
                        {
                            "path": "/",
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": svc_name,
                                    "port": {"number": svc_port},
                                }
                            },
                        }
                    ]
                },
            }
        )
    body: dict[str, Any] = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "ownerReferences": owner_refs,
        },
        "spec": {"rules": rules},
    }
    if class_name:
        body["spec"]["ingressClassName"] = class_name
    return body


def _http_probe(port: int, path: str, initial: int, period: int) -> dict[str, Any]:
    return {
        "httpGet": {"path": path, "port": port},
        "initialDelaySeconds": initial,
        "periodSeconds": period,
    }


def _default_resources(*, small: bool = False) -> dict[str, Any]:
    if small:
        return {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        }
    return {
        "requests": {"cpu": "200m", "memory": "256Mi"},
        "limits": {"cpu": "1000m", "memory": "1Gi"},
    }


def _server_side_apply(
    create_fn: Any,
    patch_fn: Any,
    namespace: str,
    body: dict[str, Any],
    name: str,
) -> None:
    """Try create; on 409, fall back to patch.

    The kubernetes-client doesn't expose server-side apply ergonomically
    yet; this is the standard 'create-or-patch' upsert.
    """
    try:
        create_fn(namespace=namespace, body=body)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "AlreadyExists" in msg or "409" in msg:
            patch_fn(name=name, namespace=namespace, body=body)
        else:
            raise


def _now() -> str:
    return datetime.now(UTC).isoformat()
