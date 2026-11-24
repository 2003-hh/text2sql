from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query
from redis.exceptions import RedisError

from t2s.observability.trace import TracePage
from t2s.sessions.service import SessionNotFound, SessionService


def trace_router(service: Callable[[], SessionService]) -> APIRouter:
    router = APIRouter()

    @router.get("/sessions/{id}/trace")
    def read_trace(
        id: str, after: int = Query(0, ge=0, le=10**15),
        limit: int = Query(100, ge=1, le=200),
    ) -> TracePage:
        try:
            return service().read_trace(id, after, limit)
        except SessionNotFound as error:
            raise HTTPException(404, "会话不存在") from error
        except RedisError as error:
            raise HTTPException(503, "轨迹存储暂不可用") from error

    return router
