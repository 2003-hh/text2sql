from redis import Redis

from t2s.observability.trace import TraceEvent, TracePage

# 同一次追加中完成步号、去重、裁剪和过期。去重索引只保留仍在流里的记录。
_APPEND = """
local stream, seen = KEYS[1], KEYS[2]
local dedup, event = ARGV[1], ARGV[2]
local limit, ttl = tonumber(ARGV[3]), tonumber(ARGV[4])
if redis.call('HEXISTS', seen, dedup) == 1 then
    return 0
end
local last = redis.call('XREVRANGE', stream, '+', '-', 'COUNT', 1)
local step = 1
if #last > 0 then
    step = tonumber(string.match(last[1][1], '^%d+')) + 1
end
redis.call('XADD', stream, tostring(step) .. '-0', 'dedup', dedup, 'event', event)
redis.call('HSET', seen, dedup, '1')
local excess = redis.call('XLEN', stream) - limit
if excess > 0 then
    for _, entry in ipairs(redis.call('XRANGE', stream, '-', '+', 'COUNT', excess)) do
        redis.call('HDEL', seen, entry[2][2])
        redis.call('XDEL', stream, entry[1])
    end
end
redis.call('EXPIRE', stream, ttl)
redis.call('EXPIRE', seen, ttl)
return step
"""


class RedisTraceStore:
    def __init__(self, client: Redis, *, max_events: int, ttl_seconds: int):
        self._client = client
        self._append = client.register_script(_APPEND)
        self._max_events = max_events
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _keys(session_id: str) -> tuple[str, str]:
        prefix = f"t2s:trace:v1:{{{session_id}}}"
        return f"{prefix}:events", f"{prefix}:seen"

    def append(self, event: TraceEvent, dedup: str) -> None:
        self._append(
            keys=self._keys(event.session_id),
            args=(dedup, event.model_dump_json(exclude={"step"}),
                  self._max_events, self._ttl_seconds),
        )

    def read(self, session_id: str, after: int, limit: int) -> TracePage:
        stream, _ = self._keys(session_id)
        entries = self._client.xrange(stream, min=f"({after}-0", count=limit + 1)
        events = [
            TraceEvent.model_validate_json(fields[b"event"]).model_copy(
                update={"step": int(stream_id.split(b"-", 1)[0])}
            )
            for stream_id, fields in entries[:limit]
        ]
        return TracePage(
            events=events, next_after=events[-1].step if events else after,
            has_more=len(entries) > limit,
            history_trimmed=bool(events and events[0].step > after + 1),
        )
