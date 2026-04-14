"""Verify the core property: output emitted before end-of-input.

This is the single most important behavioral claim of the library. If this
test fails, everything else is academic."""

from __future__ import annotations

import asyncio


from lol_html import AsyncRewriter


async def test_output_available_before_eof() -> None:
    """We should receive output chunks before calling close()."""
    rw = AsyncRewriter(flush_every_chunk=True, flush_threshold=1)

    await rw.feed(b"<html><body><p>first chunk</p>")
    chunk = await asyncio.wait_for(rw.__anext__(), timeout=1.0)
    assert b"first chunk" in chunk

    await rw.feed(b"<p>second chunk</p>")
    chunk2 = await asyncio.wait_for(rw.__anext__(), timeout=1.0)
    assert b"second chunk" in chunk2

    rw.close()
    # Drain any trailer.
    async for _ in rw:
        pass


async def test_first_output_latency_is_low() -> None:
    """Time from first feed() to first output chunk should be small."""
    rw = AsyncRewriter(flush_every_chunk=True, flush_threshold=1)
    loop = asyncio.get_running_loop()

    t0 = loop.time()
    await rw.feed(b"<p>hello</p>")
    _ = await asyncio.wait_for(rw.__anext__(), timeout=1.0)
    elapsed = loop.time() - t0

    # Even generously, this should be well under 100ms on any CI.
    assert elapsed < 0.1, f"first-output latency was {elapsed * 1000:.1f}ms"
    rw.cancel()


async def test_interleaved_produce_consume() -> None:
    """Classic streaming shape: producer and consumer running in parallel,
    output emerging as input is fed."""
    rw = AsyncRewriter(flush_every_chunk=True, flush_threshold=1)

    N = 20
    produce_times: list[float] = []
    consume_times: list[float] = []
    loop = asyncio.get_running_loop()

    async def producer() -> None:
        for i in range(N):
            await rw.feed(f"<p>item {i}</p>".encode())
            produce_times.append(loop.time())
            await asyncio.sleep(0.01)
        rw.close()

    async def consumer() -> None:
        async for _ in rw:
            consume_times.append(loop.time())

    await asyncio.gather(producer(), consumer())

    assert len(produce_times) == N
    assert len(consume_times) >= N

    # First consume should precede last produce — proof of true interleaving.
    assert consume_times[0] < produce_times[-1], (
        "consumer had to wait for all production — not actually streaming"
    )
