"""Shared fixtures and helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable

from lol_html import AsyncRewriter


async def feed_all(rw: AsyncRewriter, chunks: Iterable[bytes]) -> None:
    """Feed chunks sequentially, then close. Raises if the rewriter rejects."""
    for c in chunks:
        await rw.feed(c)
    rw.close()


async def collect(rw: AsyncRewriter) -> bytes:
    """Drain the output iterator to bytes."""
    out = bytearray()
    async for c in rw:
        out += c
    return bytes(out)


async def drive(rw: AsyncRewriter, chunks: Iterable[bytes]) -> bytes:
    """Feed + collect concurrently, the canonical driver."""
    _, result = await asyncio.gather(feed_all(rw, chunks), collect(rw))
    return result


async def collect_chunked(rw: AsyncRewriter) -> list[bytes]:
    """Collect output preserving chunk boundaries — needed for flush tests."""
    chunks: list[bytes] = []
    async for c in rw:
        chunks.append(bytes(c))
    return chunks


async def iter_with_feeding(
    rw: AsyncRewriter,
    feeder: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Feed from an async source while yielding output chunks. Useful for
    timing-sensitive tests: lets you observe output *during* input, not after."""
    async def pump() -> None:
        async for c in feeder:
            await rw.feed(c)
        rw.close()

    pump_task = asyncio.create_task(pump())
    try:
        async for out in rw:
            yield out
    finally:
        await pump_task