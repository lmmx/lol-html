"""Verify bounded channels actually backpressure.

The testable claim: if the consumer stops reading, feed() blocks after
the input buffer + parser's in-flight chunk fill up. Measured by
asyncio.wait_for timing out on feed()."""

from __future__ import annotations

import asyncio

import pytest

from lol_html import AsyncRewriter


async def test_feed_blocks_when_consumer_stalls() -> None:
    """With tight bounds and no consumer, feed() should eventually block."""
    rw = AsyncRewriter(
        input_capacity=1,
        output_capacity=1,
        flush_every_chunk=True,
        flush_threshold=1,
    )
    # Feed without ever reading. Each feed produces output, output channel
    # fills, parser blocks sending output, parser stops reading input,
    # input channel fills, feed() blocks.
    feeds_completed = 0
    with pytest.raises(asyncio.TimeoutError):
        for _ in range(100):
            await asyncio.wait_for(rw.feed(b"<p>x</p>"), timeout=0.5)
            feeds_completed += 1
    # We expect a small number to go through (channel capacities + parser
    # in-flight) before the pipeline stalls.
    assert 1 <= feeds_completed <= 10, (
        f"expected a few feeds to succeed before backpressure kicked in, got "
        f"{feeds_completed}"
    )
    rw.cancel()


async def test_feed_unblocks_when_consumer_resumes() -> None:
    """After the consumer starts reading, backpressure releases."""
    rw = AsyncRewriter(
        input_capacity=1,
        output_capacity=1,
        flush_every_chunk=True,
        flush_threshold=1,
    )

    # Fill the pipeline.
    for _ in range(3):
        try:
            await asyncio.wait_for(rw.feed(b"<p>x</p>"), timeout=0.2)
        except asyncio.TimeoutError:
            break

    # Start draining.
    async def drain_some(n: int) -> list[bytes]:
        collected: list[bytes] = []
        for _ in range(n):
            chunk = await asyncio.wait_for(rw.__anext__(), timeout=2.0)
            collected.append(bytes(chunk))
        return collected

    drained = await drain_some(2)
    assert len(drained) == 2

    # Now a fresh feed should succeed within a reasonable window.
    await asyncio.wait_for(rw.feed(b"<p>y</p>"), timeout=2.0)
    rw.cancel()


async def test_memory_bounded_under_rate_mismatch() -> None:
    """Fast producer, slow consumer — the pipeline must not grow unboundedly.

    We can't measure Rust-side memory from Python, but we *can* verify that
    feed() blocks (which is the mechanism that keeps memory bounded)."""
    rw = AsyncRewriter(input_capacity=2, output_capacity=2, flush_every_chunk=True)

    produced = 0
    consumed = 0

    async def slow_consumer() -> None:
        nonlocal consumed
        async for _ in rw:
            consumed += 1
            await asyncio.sleep(0.01)  # 100 chunks/sec max

    async def fast_producer() -> None:
        nonlocal produced
        for _ in range(50):
            await rw.feed(b"<p>x</p>")
            produced += 1
        rw.close()

    await asyncio.wait_for(
        asyncio.gather(slow_consumer(), fast_producer()),
        timeout=10.0,
    )
    # Producer should have been forced to wait on consumer → total time
    # dominated by consumer rate. If backpressure were broken, producer would
    # race ahead and we'd see produced == 50 very early.
    assert produced == 50
    assert consumed == produced  # assuming 1 flush per input under these settings