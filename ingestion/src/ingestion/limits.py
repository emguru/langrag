from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """Simple in-memory sliding-window rate limiter (per-process)."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        events = self._events[key]
        cutoff = now - window_seconds
        while events and events[0] < cutoff:
            events.popleft()
        if len(events) >= limit:
            raise RateLimitExceeded(
                f"Rate limit exceeded: {limit} requests per {window_seconds}s"
            )
        events.append(now)


class RateLimitExceeded(Exception):
    pass


async def read_upload_limited(file, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(
                f"Upload exceeds maximum size of {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


class UploadTooLargeError(Exception):
    pass


class FetchTooLargeError(Exception):
    pass


async def read_response_limited(resp, max_bytes: int) -> bytes:
    content_length = resp.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        raise FetchTooLargeError(
            f"Response exceeds maximum size of {max_bytes} bytes"
        )
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes(65536):
        total += len(chunk)
        if total > max_bytes:
            raise FetchTooLargeError(
                f"Response exceeds maximum size of {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)
