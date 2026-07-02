"""FastAPI application factory for the control-plane API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis_async
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langprobe_tenant import AuditWriter

from . import config
from .clickhouse_client import ClickHouseQuery
from .middleware import install as install_middleware
from .routers import (
    admin_audit,
    admin_quotas,
    agent_views,
    alerts,
    annotations,
    api_keys,
    comparisons,
    datasets,
    evals,
    feedback,
    feedback_keys,
    health,
    llm_credentials,
    luna_judges,
    members,
    metrics,
    oauth_signup,
    playground,
    poll_runs,
    projects,
    prompts,
    reliability,
    replay_runs,
    replays,
    run_actions,
    runs_query,
    saved_views,
    scim,
    studio,
    threads_query,
    workspaces_me,
)
from .routers import (
    auth as auth_router,
)
from .routers import (
    setup as setup_router,
)
from .routers import (
    sso as sso_router,
)

log = structlog.get_logger("langprobe.api.app")


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper())
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
    )


def create_app() -> FastAPI:
    settings = config.load()
    _configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.pg = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=2,
            max_size=10,
            command_timeout=10,
        )
        app.state.clickhouse = (
            ClickHouseQuery(settings.clickhouse_url) if settings.clickhouse_url else None
        )
        # Audit writer: every egress event lands here (export, share-link,
        # webhook fan-out, read API inputs/outputs return). Postgres
        # ``audit_log`` is read-only after this lands; new writes go to
        # ClickHouse via this writer (spec §5.8).
        app.state.audit_writer = (
            await AuditWriter.from_url(settings.clickhouse_url) if settings.clickhouse_url else None
        )
        # Redis is needed for the api-key invalidation publish path and the
        # quota / audit reconciler hooks. Optional; if unset, those features
        # are no-ops.
        app.state.redis = (
            redis_async.from_url(settings.redis_url, decode_responses=False)
            if settings.redis_url
            else None
        )
        # Spawn the alert evaluator so threshold rules tick without an
        # external scheduler. Cancelled cleanly on shutdown.
        evaluator_task = asyncio.create_task(
            alerts.evaluator_loop(app.state.pg, app.state.clickhouse),
            name="alert-evaluator",
        )
        log.info(
            "api started",
            bind=f"{settings.bind_host}:{settings.bind_port}",
            clickhouse=bool(settings.clickhouse_url),
        )
        try:
            yield
        finally:
            evaluator_task.cancel()
            try:
                await evaluator_task
            except asyncio.CancelledError:
                pass
            if app.state.clickhouse is not None:
                app.state.clickhouse.close()
            if app.state.redis is not None:
                await app.state.redis.aclose()
            await app.state.pg.close()
            log.info("api stopped")

    app = FastAPI(
        title="langprobe api",
        version="0.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.cors_allow_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_middleware(app)
    app.include_router(health.router)
    app.include_router(setup_router.router)
    app.include_router(auth_router.router)
    app.include_router(projects.router)
    app.include_router(projects.workspaces_router)
    app.include_router(api_keys.router)
    app.include_router(members.router)
    app.include_router(runs_query.router)
    app.include_router(replays.runs_router)
    app.include_router(replays.catalog_router)
    app.include_router(replay_runs.runs_router)
    app.include_router(agent_views.router)
    app.include_router(reliability.router)
    app.include_router(threads_query.router)
    app.include_router(metrics.router)
    app.include_router(datasets.router)
    app.include_router(prompts.router)
    app.include_router(evals.router)
    app.include_router(comparisons.router)
    app.include_router(alerts.router)
    app.include_router(annotations.router)
    app.include_router(feedback_keys.router)
    app.include_router(feedback.router)
    app.include_router(studio.router)
    app.include_router(playground.router)
    app.include_router(poll_runs.router)
    app.include_router(saved_views.router)
    app.include_router(run_actions.router)
    app.include_router(luna_judges.router)
    app.include_router(llm_credentials.router)
    app.include_router(sso_router.router)
    app.include_router(oauth_signup.router)
    app.include_router(scim.router)
    app.include_router(scim.admin_router)
    app.include_router(workspaces_me.router)
    app.include_router(admin_quotas.router)
    app.include_router(admin_audit.router)
    return app


app = create_app()
