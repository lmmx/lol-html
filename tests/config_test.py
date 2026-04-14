"""Environment-variable configuration.

Config is read via #[ctor] at library load, so we can't test it in-process.
We shell out to a subprocess with env vars set and introspect behavior."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def _run_with_env(script: str, env_overrides: dict[str, str]) -> str:
    env = os.environ.copy()
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return result.stdout


def test_env_var_sets_flush_every_chunk() -> None:
    """LOL_HTML_FLUSH_EVERY_CHUNK=1 should cause per-chunk flushing without
    a constructor override."""
    script = """
    import asyncio
    from lol_html import AsyncRewriter

    async def main():
        rw = AsyncRewriter(flush_threshold=1)  # deliberately no flush_every_chunk arg
        inputs = [b"<p>a</p>", b"<p>b</p>", b"<p>c</p>"]

        async def produce():
            for c in inputs:
                await rw.feed(c)
            rw.close()

        async def consume():
            chunks = []
            async for c in rw:
                chunks.append(bytes(c))
            return chunks

        _, chunks = await asyncio.gather(produce(), consume())
        print(len(chunks))

    asyncio.run(main())
    """
    # Without the env var, threshold=1 alone should still coalesce (small output).
    baseline = int(_run_with_env(script, {}).strip())
    eager = int(_run_with_env(script, {"LOL_HTML_FLUSH_EVERY_CHUNK": "1"}).strip())
    assert eager >= baseline
    assert eager >= 3  # one per input chunk, plus possibly trailing


def test_env_var_sets_flush_threshold() -> None:
    """Threshold=0 from env should make the whole output come out as one chunk."""
    script = """
    import asyncio
    from lol_html import AsyncRewriter

    async def main():
        rw = AsyncRewriter()  # no args, purely env-driven
        async def produce():
            for _ in range(100):
                await rw.feed(b"<p>x</p>")
            rw.close()
        async def consume():
            chunks = []
            async for c in rw:
                chunks.append(bytes(c))
            return chunks
        _, chunks = await asyncio.gather(produce(), consume())
        print(len(chunks))

    asyncio.run(main())
    """
    one_chunk = int(_run_with_env(script, {"LOL_HTML_FLUSH_THRESHOLD": "0"}).strip())
    assert one_chunk == 1


def test_constructor_overrides_env() -> None:
    """Explicit constructor arg beats env var."""
    script = """
    import asyncio
    from lol_html import AsyncRewriter

    async def main():
        # Env says "never flush by size"; constructor overrides to eager.
        rw = AsyncRewriter(flush_threshold=1, flush_every_chunk=True)
        async def produce():
            for _ in range(5):
                await rw.feed(b"<p>x</p>")
            rw.close()
        async def consume():
            return [bytes(c) async for c in rw]
        _, chunks = await asyncio.gather(produce(), consume())
        print(len(chunks))

    asyncio.run(main())
    """
    out = int(_run_with_env(script, {"LOL_HTML_FLUSH_THRESHOLD": "0"}).strip())
    assert out >= 5  # per-chunk flush wins despite env threshold=0


def test_invalid_env_var_falls_back_to_default() -> None:
    """Garbage env values should not crash the library."""
    script = """
    import asyncio
    from lol_html import AsyncRewriter

    async def main():
        rw = AsyncRewriter()
        await rw.feed(b"<p>ok</p>")
        rw.close()
        out = bytearray()
        async for c in rw:
            out += c
        print(b"<p>ok</p>" in out)

    asyncio.run(main())
    """
    result = _run_with_env(script, {"LOL_HTML_FLUSH_THRESHOLD": "not-a-number"}).strip()
    assert result == "True"
