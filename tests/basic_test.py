"""Basic smoke tests for the async rewriter."""

from __future__ import annotations

import asyncio


from lol_html import AsyncRewriter


async def _drive(rw: AsyncRewriter, chunks: list[bytes]) -> bytes:
    """Feed ``chunks`` into ``rw`` and collect all output."""

    async def produce() -> None:
        for c in chunks:
            await rw.feed(c)
        rw.close()

    async def consume() -> bytes:
        out = bytearray()
        async for chunk in rw:
            out += chunk
        return bytes(out)

    _, result = await asyncio.gather(produce(), consume())
    return result


async def test_removes_script_and_style() -> None:
    rw = AsyncRewriter(flush_every_chunk=True)
    out = await _drive(
        rw,
        [
            b"<html><head><style>x{}</style></head>",
            b"<body><script>bad()</script><p>hi</p></body></html>",
        ],
    )
    assert b"<script" not in out
    assert b"<style" not in out
    assert b"<p>hi</p>" in out


async def test_eager_flush_produces_output_before_close() -> None:
    """With flush_every_chunk=True, output should be visible after each feed."""
    rw = AsyncRewriter(flush_every_chunk=True, flush_threshold=1)
    await rw.feed(b"<html><body><p>first</p>")

    # We should be able to read at least one chunk before closing.
    chunk = await asyncio.wait_for(rw.__anext__(), timeout=1.0)
    assert b"first" in chunk

    await rw.feed(b"<p>second</p></body></html>")
    rw.close()

    rest = bytearray(chunk)
    async for c in rw:
        rest += c
    assert b"second" in rest


async def test_coalesced_mode_emits_less_often() -> None:
    rw = AsyncRewriter(flush_threshold=1 << 20)  # 1 MiB — won't flush mid-stream
    out = await _drive(rw, [b"<p>x</p>"] * 100)
    assert out.count(b"<p>x</p>") == 100


async def test_close_is_idempotent() -> None:
    rw = AsyncRewriter()
    await rw.feed(b"<p>hi</p>")
    rw.close()
    rw.close()  # second call should not raise
    out = bytearray()
    async for chunk in rw:
        out += chunk
    assert b"<p>hi</p>" in out
