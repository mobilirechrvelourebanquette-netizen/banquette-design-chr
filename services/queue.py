"""Redis Streams queue producer and short-lived worker consumer."""
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis, from_url
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)
STREAM = os.getenv("REDIS_QUEUE_STREAM", "banquette:jobs")
GROUP = os.getenv("REDIS_QUEUE_GROUP", "banquette-workers")
MAX_ATTEMPTS = max(1, int(os.getenv("QUEUE_MAX_ATTEMPTS", "5")))
PENDING_IDLE_MS = max(1000, int(os.getenv("QUEUE_PENDING_IDLE_MS", "60000")))
_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        redis_url = os.getenv("REDIS_URL", "")
        if not redis_url:
            raise RuntimeError("REDIS_URL is not configured")
        _redis = from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=5,
            health_check_interval=30,
        )
    return _redis


async def enqueue_job(
    job_type: str,
    data: dict[str, Any],
    dedupe_key: str | None = None,
) -> str:
    """Atomically enqueue a job and optionally deduplicate inbound provider events."""
    redis = await get_redis()
    job_id = str(uuid.uuid4())
    encoded_data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if dedupe_key:
        # Lua makes dedupe + enqueue atomic; duplicate webhook deliveries don't create jobs.
        script = """
        if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
            return redis.call('XADD', KEYS[2], '*', 'job_id', ARGV[1], 'type', ARGV[3], 'data', ARGV[4])
        end
        return ''
        """
        result = await redis.eval(script, 2, dedupe_key, STREAM, job_id, 60 * 60 * 24 * 30, job_type, encoded_data)
        return job_id if result else ""
    await redis.xadd(STREAM, {"job_id": job_id, "type": job_type, "data": encoded_data})
    return job_id


async def _ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def process_batch(
    handler: Callable[[str, dict[str, Any]], Awaitable[None]],
    batch_size: int = 5,
) -> dict[str, int]:
    """Process a bounded batch, reclaim stale jobs, retry failures, and dead-letter exhausted jobs."""
    redis = await get_redis()
    await _ensure_group(redis)
    consumer = f"worker-{uuid.uuid4()}"

    reclaimed = await redis.xautoclaim(
        STREAM, GROUP, consumer, min_idle_time=PENDING_IDLE_MS, start_id="0-0", count=batch_size
    )
    entries = reclaimed[1] if isinstance(reclaimed, (tuple, list)) and len(reclaimed) > 1 else []
    if not entries:
        response = await redis.xreadgroup(
            GROUP, consumer, streams={STREAM: ">"}, count=batch_size, block=1000
        )
        entries = response[0][1] if response else []

    succeeded = failed = dead_lettered = 0
    for stream_id, fields in entries:
        try:
            job_type = fields.get("type", "")
            data = json.loads(fields.get("data", "{}"))
            if not isinstance(data, dict):
                raise ValueError("Queue job data must be an object")
            await handler(job_type, data)
            await redis.xack(STREAM, GROUP, stream_id)
            succeeded += 1
        except Exception as exc:
            failed += 1
            logger.error("Queue job failed (%s); delivery remains pending for retry.", type(exc).__name__)
            try:
                pending = await redis.xpending_range(STREAM, GROUP, min=stream_id, max=stream_id, count=1)
                attempts = pending[0].get("times_delivered", 1) if pending else 1
                if attempts >= MAX_ATTEMPTS:
                    await redis.xadd(
                        f"{STREAM}:dead",
                        {
                            "source_id": stream_id,
                            "type": fields.get("type", ""),
                            "data": fields.get("data", "{}"),
                            "error_type": type(exc).__name__,
                        },
                    )
                    await redis.xack(STREAM, GROUP, stream_id)
                    dead_lettered += 1
            except Exception as dlq_exc:
                logger.error("Dead-letter handling failed (%s).", type(dlq_exc).__name__)

    return {
        "processed": len(entries),
        "succeeded": succeeded,
        "failed": failed,
        "dead_lettered": dead_lettered,
    }
