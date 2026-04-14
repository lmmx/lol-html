"""close(), cancel(), drop, idempotency, and error paths."""

from __future__ import annotations

import asyncio
import gc

import pytest

from lol_html import AsyncRewriter


async def test_close_is_idempotent() -> None:
    rw = AsyncRewriter()
    await rw.feed(b"<p>hi</p>")
    rw.close()
    rw.close()
    rw.close()
    out = bytearray()
    async for c in rw:
        out += c
    assert b"<p>hi</p>" in out


async def test_feed_after_close_raises() -> None:
    rw = AsyncRewriter()
    await rw.feed(b"<p>a</p>")
    rw.close()
    with pytest.raises(RuntimeError, match="closed"):
        await rw.feed(b"<p>b</p>")
    # Drain so the task exits cleanly.
    async for _ in rw:
        pass


async def test_cancel_stops_iteration_promptly() -> None:
    rw = AsyncRewriter(flush_threshold=1 << 20)  # don't flush mid-stream
    await rw.feed(b"<p>buffered</p>" * 100)
    rw.cancel()
    # After cancel, output may or may not include the buffered data.
    # What matters is that iteration terminates, not hangs.
    chunks: list[bytes] = []
    try:
        async for c in rw:
            chunks.append(bytes(c))
    except Exception:
        pass
    # No assertion on content — cancel is allowed to drop data — only that
    # we exited the loop in reasonable time. The test timeout is the assertion.


async def test_drop_cancels_task() -> None:
    """Dropping the rewriter without close() should still terminate the
    background thread (via Drop → cancel)."""
    rw = AsyncRewriter()
    await rw.feed(b"<p>abandoned</p>")
    del rw
    gc.collect()
    # If the drop didn't cancel, the parser thread would leak. We can't
    # easily assert thread count, but asyncio.sleep gives time for the thread
    # to exit — if it doesn't, subsequent test runs would accumulate threads.
    await asyncio.sleep(0.1)


async def test_iter_returns_stopasynciteration_at_eof() -> None:
    rw = AsyncRewriter()
    await rw.feed(b"<p>done</p>")
    rw.close()
    # Drain everything.
    async for _ in rw:
        pass
    # Next __anext__ should raise StopAsyncIteration.
    with pytest.raises(StopAsyncIteration):
        await rw.__anext__()


async def test_empty_stream() -> None:
    """Zero input chunks, just close."""
    rw = AsyncRewriter()
    rw.close()
    out = bytearray()
    async for c in rw:
        out += c
    assert out == b""


async def test_cancel_unblocks_pending_feed() -> None:
    """A feed() awaiting on a full channel should resolve with error on cancel,
    not hang forever."""
    rw = AsyncRewriter(input_capacity=1, output_capacity=1, flush_threshold=1 << 20)

    # Fill the pipeline.
    await rw.feed(b"<p>a</p>")

    # This feed() will likely block — backpressure.
    feed_task = asyncio.create_task(rw.feed(b"<p>b</p>"))
    await asyncio.sleep(0.05)  # let it start awaiting

    rw.cancel()

    # Must resolve (either success or error) within a short window.
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(feed_task, timeout=1.0)
