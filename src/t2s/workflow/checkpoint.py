from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.redis import RedisSaver
from redis import Redis

from t2s.infrastructure.redis import redis_client
from t2s.settings import Settings


def create_checkpointer(client: Redis) -> RedisSaver:
    saver = RedisSaver(redis_client=client)
    saver.setup()
    return saver


@contextmanager
def open_checkpointer(settings: Settings) -> Iterator[RedisSaver]:
    with redis_client(settings.redis_url.get_secret_value()) as client:
        yield create_checkpointer(client)
