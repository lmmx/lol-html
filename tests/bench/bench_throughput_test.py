"""Throughput benchmarks.

Run with: pytest tests/bench --benchmark-only

These are descriptive, not prescriptive — no assertions, just numbers to
compare across versions. Track regressions via pytest-benchmark's
--benchmark-autosave + --benchmark-compare."""

from __future__ import annotations

import asyncio

import pytest

from lol_html import AsyncRewriter


async def _run_streaming(payload: bytes, chunk_size: int, **kwargs: object) -> int:
    rw = AsyncRewriter(**kwargs)
    total = 0

    async def produce() -> None:
        for i in range(0, len(payload), chunk_size):
            await rw.feed(payload[i : i + chunk_size])
        rw.close()

    async def consume() -> int:
        nonlocal total
        async for c in rw:
            total += len(c)
        return total

    _, consumed = await asyncio.gather(produce(), consume())
    return consumed


@pytest.mark.parametrize("chunk_size", [256, 4096, 16384, 65536])
def test_bench_chunk_size_sweep(benchmark, medium_payload, chunk_size):
    """How chunk size affects throughput at default flush policy."""

    def run():
        return asyncio.run(_run_streaming(medium_payload, chunk_size))

    result = benchmark(run)
    assert result > 0


@pytest.mark.parametrize("flush_threshold", [0, 4096, 16384, 1 << 20])
def test_bench_flush_threshold_sweep(benchmark, medium_payload, flush_threshold):
    """How flush threshold affects throughput at fixed chunk size."""

    def run():
        return asyncio.run(
            _run_streaming(
                medium_payload,
                chunk_size=4096,
                flush_threshold=flush_threshold,
            ),
        )

    result = benchmark(run)
    assert result > 0


def test_bench_eager_vs_batched(benchmark, medium_payload):
    """SSE-style (per-chunk flush) vs. throughput-oriented (large batches)."""

    def run_eager():
        return asyncio.run(
            _run_streaming(
                medium_payload,
                chunk_size=4096,
                flush_every_chunk=True,
                flush_threshold=1,
            ),
        )

    benchmark(run_eager)


def test_bench_large_document(benchmark, large_payload):
    """Realistic-sized document throughput."""

    def run():
        return asyncio.run(
            _run_streaming(
                large_payload,
                chunk_size=16384,
                flush_threshold=64 * 1024,
            ),
        )

    benchmark(run)
