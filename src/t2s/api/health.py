from collections.abc import Callable, Mapping
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
Probe = Callable[[], None]


def health_router(probes: Mapping[str, Probe]) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready")
    def ready() -> JSONResponse:
        checks = {}
        for name, probe in probes.items():
            try:
                probe()
            except Exception as error:
                # 驱动错误正文可能包含连接详情，日志只记录异常类型。
                logger.warning("Readiness probe failed: %s (%s)", name, type(error).__name__)
                checks[name] = "unavailable"
            else:
                checks[name] = "ok"
        available = all(status == "ok" for status in checks.values())
        return JSONResponse(
            status_code=200 if available else 503,
            content={"status": "ready" if available else "unavailable", "checks": checks},
        )

    return router
