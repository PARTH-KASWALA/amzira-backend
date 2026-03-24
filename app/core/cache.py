from __future__ import annotations

import json
import logging
from typing import Any, Iterable

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception as exc:
        logger.warning("redis_unavailable", exc_info=exc)
        _redis_client = None
        return None


def cache_get_json(key: str) -> Any | None:
    client = get_redis()
    if not client:
        return None
    try:
        payload = client.get(key)
    except Exception as exc:
        logger.warning("redis_get_failed", exc_info=exc)
        return None
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    client = get_redis()
    if not client:
        return
    try:
        payload = json.dumps(value, default=str)
        client.setex(key, ttl_seconds, payload)
    except Exception as exc:
        logger.warning("redis_set_failed", exc_info=exc)


def cache_delete(key: str) -> None:
    client = get_redis()
    if not client:
        return
    try:
        client.delete(key)
    except Exception as exc:
        logger.warning("redis_delete_failed", exc_info=exc)


def cache_invalidate_prefix(prefix: str) -> None:
    client = get_redis()
    if not client:
        return
    try:
        for key in client.scan_iter(match=f"{prefix}*"):
            client.delete(key)
    except Exception as exc:
        logger.warning("redis_scan_failed", exc_info=exc)


def invalidate_product_cache(slugs: Iterable[str] | None = None) -> None:
    cache_invalidate_prefix("cache:products:list:")
    if not slugs:
        return
    for slug in slugs:
        if slug:
            cache_delete(f"cache:products:detail:{slug}")
