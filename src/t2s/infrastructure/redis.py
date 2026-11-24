from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry


def check_redis(client: Redis) -> None:
    """验证连接，以及 LangGraph 保存器用到的 JSON 和搜索命令。"""
    if not client.ping():
        raise RuntimeError("Redis PING failed")
    client.execute_command("JSON.GET", "t2s:readiness:unused")
    client.execute_command("FT._LIST")


def redis_client(url: str) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=False,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
    )
