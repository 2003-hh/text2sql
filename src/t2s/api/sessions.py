from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from redis.exceptions import RedisError

from t2s.sessions.models import CreateSession, Reply, Session
from t2s.sessions.service import SessionConflict, SessionNotFound, SessionService


def session_router(service: Callable[[], SessionService]) -> APIRouter:
    router = APIRouter()

    @router.get("/choices")
    def choices() -> dict[str, str]:
        try:
            return service().choices()
        except RedisError as error:
            raise HTTPException(503, "会话存储暂不可用") from error

    @router.post("/sessions")
    def create(body: CreateSession) -> Session:
        try:
            return service().create(body.plan, question=body.question)
        except ValueError as error:
            raise HTTPException(400, "查询计划或问题不符合要求") from error
        except RedisError as error:
            raise HTTPException(503, "会话存储暂不可用") from error

    @router.get("/sessions/{id}")
    def get(id: str) -> Session:
        try:
            return service().get(id)
        except SessionNotFound as error:
            raise HTTPException(404, "会话不存在") from error
        except RedisError as error:
            raise HTTPException(503, "会话存储暂不可用") from error

    @router.post("/sessions/{id}/respond")
    def respond(id: str, reply: Reply) -> Session:
        try:
            return service().respond(id, reply)
        except SessionNotFound as error:
            raise HTTPException(404, "会话不存在") from error
        except SessionConflict as error:
            raise HTTPException(409, "确认卡已失效，请刷新会话") from error
        except ValueError as error:
            raise HTTPException(400, "当前会话无法执行这个操作") from error
        except RedisError as error:
            raise HTTPException(503, "会话存储暂不可用") from error

    return router
