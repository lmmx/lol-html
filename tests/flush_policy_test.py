"""Verify the flush threshold / flush_every_chunk knobs do what they advertise.

Strategy: we can't directly observe the sink buffer, but we *can* observe
output chunk boundaries by preserving them in ``collect_chunked``. Each flush
corresponds to exactly one channel send, and each channel recv on the Python
side is one iteration of ``async for``. So ``len(chunks)`` is a faithful
proxy for "number of flushes"."""

from __future__ import annotations

import pytest

from lol_html import AsyncRewriter
from tests.conftest import collect_chunked, feed_all


async def _run(rw: AsyncRewriter, chunks: list[bytes]) -> list[bytes]:
    import asyncio
    _, out = await asyncio.gather(feed_all(rw, chunks), collect_chunked(rw))
    return out


async def test_flush_every_chunk_emits_per_input() -> None:
    """With flush_every_chunk=True, output chunk count ~= input chunk count
    (plus one possible trailing flush at end())."""
    rw = AsyncRewriter(flush_every_chunk=True, flush_threshold=1)
    inputs = [b"<p>one</p>", b"<p>two</p>", b"<p>three</p>"]
    out_chunks = await _run(rw, inputs)
    assert 3 <= len(out_chunks) <= 4  # +1 for trailing end() flush if anything remains
    combined = b"".join(out_chunks)
    assert combined.count(b"<p>") == 3


async def test_large_threshold_coalesces_to_few_chunks() -> None:
    """With a threshold bigger than the total output, we expect ~1 chunk (the
    end-of-stream flush)."""
    rw = AsyncRewriter(flush_threshold=1 << 20, flush_every_chunk=False)  # 1 MiB
    inputs = [b"<p>x</p>"] * 50  # ~400 bytes total, well under threshold
    out_chunks = await _run(rw, inputs)
    assert len(out_chunks) == 1
    assert out_chunks[0].count(b"<p>x</p>") == 50


async def test_threshold_zero_emits_only_at_end() -> None:
    """flush_threshold=0 disables size-based flushing entirely."""
    rw = AsyncRewriter(flush_threshold=0, flush_every_chunk=False)
    inputs = [b"<p>x</p>"] * 100
    out_chunks = await _run(rw, inputs)
    assert len(out_chunks) == 1
    assert out_chunks[0].count(b"<p>x</p>") == 100


async def test_small_threshold_flushes_mid_stream() -> None:
    """A threshold smaller than total output forces multiple flushes."""
    rw = AsyncRewriter(flush_threshold=64, flush_every_chunk=False)
    # Each input is ~16 bytes of output, so we expect flushes after ~every 4.
    inputs = [b"<p>abcdefgh</p>"] * 32  # ~480 bytes total
    out_chunks = await _run(rw, inputs)
    # Not exact (depends on lol_html emission granularity) but should be many.
    assert len(out_chunks) >= 3
    combined = b"".join(out_chunks)
    assert combined.count(b"<p>abcdefgh</p>") == 32


async def test_script_and_style_removed_across_policies() -> None:
    """Sanity: the actual rewriting works under all flush policies."""
    for cfg in [
        {"flush_every_chunk": True, "flush_threshold": 1},
        {"flush_threshold": 0},
        {"flush_threshold": 1 << 20},
    ]:
        rw = AsyncRewriter(**cfg)
        out_chunks = await _run(
            rw,
            [b"<html><script>bad()</script><style>x{}</style><p>ok</p></html>"],
        )
        combined = b"".join(out_chunks)
        assert b"<script" not in combined, f"failed with {cfg}"
        assert b"<style" not in combined, f"failed with {cfg}"
        assert b"<p>ok</p>" in combined, f"failed with {cfg}"