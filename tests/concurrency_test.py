"""Multiple rewriters running concurrently.

The selling point of a Rust-backed streaming parser is that N instances can
run in parallel without the GIL serializing them. We can't directly measure
parallelism from Python, but we can assert that N concurrent rewriters
complete in time closer to 1× than N× a single run."""

from __future__ import annotations

import asyncio
import time

import pytest

from lol_html import AsyncRewriter
from tests.conftest import drive


# A non-trivial payload — enough work that parse time is measurable.
PAYLOAD = (b"<html><body>" + b"<p>item</p>" * 5000 + b"<script>bad()</script></body></html>")


async def _single_run() -> bytes:
    rw = AsyncRewriter(flush_threshold=64 * 1024)
    # Feed in moderate chunks to exercise the streaming path.
    chunks = [PAYLOAD[i:i + 4096] for i in range(0, len(PAYLOAD), 4096)]
    return await drive(rw, chunks)


async def test_single_baseline() -> None:
    """Sanity baseline: one rewriter completes and produces expected output."""
    out = await _single_run()
    assert b"<script" not in out
    assert out.count(b"<p>item</p>") == 5000


async def test_many_concurrent_rewriters_complete() -> None:
    """N concurrent rewriters all finish correctly."""
    N = 8
    results = await asyncio.gather(*(_single_run() for _ in range(N)))
    assert len(results) == N
    for r in results:
        assert b"<script" not in r
        assert r.count(b"<p>item</p>") == 5000


@pytest.mark.slow
async def test_concurrent_scales_better_than_serial() -> None:
    """N concurrent rewriters should be meaningfully faster than N serial runs.

    This is a soft check — CI variance is real — so we assert only a modest
    speedup. If the GIL were serializing the parse work, concurrent would be
    roughly == serial; we want to rule that out."""
    N = 4

    t0 = time.perf_counter()
    for _ in range(N):
        await _single_run()
    serial_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    await asyncio.gather(*(_single_run() for _ in range(N)))
    concurrent_time = time.perf_counter() - t0

    speedup = serial_time / concurrent_time
    # Each rewriter has its own OS thread with its own current-thread runtime,
    # so true parallelism is possible. On a multi-core box we expect >1.5x;
    # on a single-core CI box this may fail, hence @pytest.mark.slow.
    assert speedup > 1.3, (
        f"concurrent was {speedup:.2f}x faster than serial "
        f"(serial={serial_time:.2f}s, concurrent={concurrent_time:.2f}s) — "
        f"expected significant parallelism"
    )


async def test_rewriters_are_independent() -> None:
    """Cancelling one rewriter must not affect another running concurrently."""
    rw_doomed = AsyncRewriter()
    rw_healthy = AsyncRewriter()

    async def run_healthy() -> bytes:
        await rw_healthy.feed(b"<p>healthy</p>")
        rw_healthy.close()
        out = bytearray()
        async for c in rw_healthy:
            out += c
        return bytes(out)

    async def run_doomed() -> None:
        await rw_doomed.feed(b"<p>doomed</p>")
        await asyncio.sleep(0.05)
        rw_doomed.cancel()

    healthy_result, _ = await asyncio.gather(run_healthy(), run_doomed())
    assert b"<p>healthy</p>" in healthy_result