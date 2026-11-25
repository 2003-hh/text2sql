from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from t2s.api.health import Probe, health_router
from t2s.api.sessions import session_router
from t2s.api.trace_routes import trace_router
from t2s.infrastructure.catalog import check_catalog, load_catalog
from t2s.infrastructure.mysql import check_mysql
from t2s.infrastructure.redis import check_redis, redis_client
from t2s.observability.store import RedisTraceStore
from t2s.observability.trace import TraceRecorder
from t2s.planning.planner import Planner, build_planner
from t2s.query.executor import execute_query
from t2s.sessions.service import SessionService
from t2s.settings import Settings
from t2s.workflow.checkpoint import create_checkpointer


def create_app(
    settings: Settings | None = None,
    *,
    probes: Mapping[str, Probe] | None = None,
    sessions: Callable[[], SessionService] | None = None,
    planner_factory: Callable[[Settings, dict], Planner] = build_planner,
) -> FastAPI:
    if probes is None:
        config = settings if settings is not None else Settings()
        redis = redis_client(config.redis_url.get_secret_value())
        service = None
        trace = TraceRecorder(RedisTraceStore(
            redis, max_events=config.trace_max_events, ttl_seconds=config.trace_ttl_seconds,
        ))
        initialization_lock = Lock()

        def claim(id: str, revision: int) -> bool:
            return bool(redis.set(f"t2s:v1:execution:{id}:{revision}", "claimed", nx=True))

        def get_sessions() -> SessionService:
            nonlocal service
            with initialization_lock:
                if service is None:
                    catalog = load_catalog()
                    service = SessionService(
                        create_checkpointer(redis), catalog,
                        partial(execute_query, settings=config), claim,
                        planner=planner_factory(config, catalog), trace=trace,
                        evidence_diagnostics=config.evidence_diagnostics,
                    )
                return service

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            try:
                yield
            finally:
                redis.close()

        app = FastAPI(title="小哲 Text2SQL", lifespan=lifespan)
        probes = {
            "mysql": partial(check_mysql, config),
            "redis": partial(check_redis, redis),
            "catalog": check_catalog,
        }
        if sessions is None:
            sessions = get_sessions
    else:
        app = FastAPI(title="小哲 Text2SQL")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=400, content={"detail": "请核对输入和操作"})

    app.include_router(health_router(probes))
    if sessions is not None:
        app.include_router(session_router(sessions))
        app.include_router(trace_router(sessions))
    app.mount("/", StaticFiles(directory=Path(__file__).parent / "resources/web", html=True))
    return app
