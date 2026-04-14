"""Python-side scenario bench, matching examples/bench_native.rs.

Runs the three documented operating points — SSE, general streaming, batch —
and prints MB/s numbers that can be compared directly against the Rust bench.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

from lol_html import AsyncRewriter


# ---- payloads (identical to bench_native.rs) --------------------------------

def make_payload(size: str) -> bytes:
    if size == "small":
        return b"<html><body>" + b"<p>hi</p>" * 100 + b"</body></html>"
    if size == "medium":
        return b"<html><body>" + b"<p>item</p>" * 10_000 + b"</body></html>"
    if size == "large":
        return (
            b"<html><body>"
            + (b"<div><p>nested " + b"<span>stuff</span>" * 5 + b"</p></div>") * 20_000
            + b"</body></html>"
        )
    raise ValueError(size)


# ---- scenarios --------------------------------------------------------------

SCENARIOS = [
    dict(name="SSE (per-chunk flush)",
         chunk_size=256, flush_threshold=1, flush_every_chunk=True),
    dict(name="general streaming",
         chunk_size=4096, flush_threshold=16 * 1024, flush_every_chunk=False),
    dict(name="high-throughput batch",
         chunk_size=65536, flush_threshold=64 * 1024, flush_every_chunk=False),
]


async def run_scenario(
    payload: bytes,
    chunk_size: int,
    flush_threshold: int,
    flush_every_chunk: bool,
) -> tuple[int, int]:
    """Run one scenario once. Returns (output_bytes, chunk_count)."""
    rw = AsyncRewriter(
        flush_threshold=flush_threshold,
        flush_every_chunk=flush_every_chunk,
    )

    async def produce() -> None:
        for i in range(0, len(payload), chunk_size):
            await rw.feed(payload[i:i + chunk_size])
        rw.close()

    async def consume() -> tuple[int, int]:
        total = 0
        count = 0
        async for c in rw:
            total += len(c)
            count += 1
        return total, count

    _, (out_bytes, count) = await asyncio.gather(produce(), consume())
    return out_bytes, count


# ---- harness ---------------------------------------------------------------

def bench(fn, repeats: int, payload_len: int) -> dict:
    # warmup
    for _ in range(3):
        asyncio.run(fn())
    samples: list[float] = []
    result = (0, 0)
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = asyncio.run(fn())
        samples.append((time.perf_counter() - t0) * 1e6)
    mean = statistics.fmean(samples)
    stddev = statistics.pstdev(samples)
    return {
        "mean_us": mean,
        "stddev_us": stddev,
        "mbps": payload_len / (mean / 1e6) / 1e6,
        "flushes": result[1],
    }


def main() -> None:
    payload_size = "medium"
    repeats = 50
    args = sys.argv[1:]
    while args:
        flag = args.pop(0)
        if flag == "--payload":
            payload_size = args.pop(0)
        elif flag == "--repeats":
            repeats = int(args.pop(0))
        else:
            raise SystemExit(f"unknown arg: {flag}")

    payload = make_payload(payload_size)
    print(f"payload: {payload_size} ({len(payload)} bytes, {len(payload) / 1024:.1f} KB)")
    print(f"repeats: {repeats}\n")

    print(f"{'scenario':<70} {'mean μs':>10} {'stddev μs':>10} {'MB/s':>12} {'flushes':>8}")
    print("-" * 112)
    for s in SCENARIOS:
        label = (
            f"{s['name']:<24} [chunk={s['chunk_size']:>6}, "
            f"thresh={s['flush_threshold']:>6}, eager={s['flush_every_chunk']}]"
        )
        async def run(s=s):
            return await run_scenario(
                payload,
                chunk_size=s["chunk_size"],
                flush_threshold=s["flush_threshold"],
                flush_every_chunk=s["flush_every_chunk"],
            )
        r = bench(run, repeats, len(payload))
        print(f"{label:<70} {r['mean_us']:>10.1f} {r['stddev_us']:>10.1f} "
              f"{r['mbps']:>12.0f} {r['flushes']:>8}")


if __name__ == "__main__":
    main()