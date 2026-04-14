# lol-html-py

Async streaming Python bindings for [lol_html], the **l**ow-**o**utput-**l**atency **HTML**
rewriter from Cloudflare. Built on [PyO3] and [Tokio], integrates with
`asyncio` via [pyo3-async-runtimes].

[lol_html]: https://github.com/cloudflare/lol-html
[PyO3]: https://pyo3.rs
[Tokio]: https://tokio.rs
[pyo3-async-runtimes]: https://github.com/PyO3/pyo3-async-runtimes

## Why

`lol_html` is designed for streaming: it can begin emitting output before
the full input has been received. Exposing that property to Python requires
more than a thin FFI wrapper: per-chunk round trips across the PyO3 boundary
will dominate runtime for small chunks. This package solves that by running
the rewriter on a long-lived Tokio task, coalescing output in Rust, and
crossing the FFI boundary only at channel endpoints with symmetric
backpressure on both input and output. The chunk sizes that control the
properties are all configurable.

## Install

```bash
pip install lol-html-py
```

## Usage

```python
import asyncio
from lol_html import AsyncRewriter

async def main():
    rw = AsyncRewriter()

    async def produce():
        await rw.feed(b"<html><script>bad()</script><p>ok</p></html>")
        rw.close()

    async def consume():
        out = bytearray()
        async for chunk in rw:
            out += chunk
        return bytes(out)

    _, result = await asyncio.gather(produce(), consume())
    print(result)

asyncio.run(main())
```

## Configuration

To control the latency/throughput trade-off we can vary the I/O and flushing parameters.
These have sensible defaults and can be overridden per instance and via environment variables.

| Constructor arg       | Env var                         | Default | Meaning                                                          |
|-----------------------|---------------------------------|---------|------------------------------------------------------------------|
| `input_capacity`      | `LOL_HTML_INPUT_CAPACITY`       | `8`     | Bounded input channel depth (producer backpressure)              |
| `output_capacity`     | `LOL_HTML_OUTPUT_CAPACITY`      | `8`     | Bounded output channel depth (consumer backpressure)             |
| `flush_threshold`     | `LOL_HTML_FLUSH_THRESHOLD`      | `16384` | Bytes accumulated before flushing to the output channel          |
| `flush_every_chunk`   | `LOL_HTML_FLUSH_EVERY_CHUNK`    | `false` | Force a flush after every input chunk, regardless of buffer size |

Note that environment variables are read at library load time via a Rust constructor.
Per-instance constructor arguments always take priority over env vars.

### Operating points

| Goal                            | `flush_threshold` | `flush_every_chunk` |
|---------------------------------|-------------------|---------------------|
| SSE / per-token streaming       | `1`               | `True`              |
| General low-latency streaming   | `4096`–`16384`    | `False`             |
| High-throughput batch           | `65536`+          | `False`             |
| Single output blob at end       | `0`               | `False`             |

`flush_threshold=0` disables size-based flushing entirely; output emits only
at end-of-stream or per-chunk forced flush.

## Architecture

```
Python producer ──feed()──▶ [input channel, bounded] ──▶ parser task
                                                           │
                                                           ├─ HtmlRewriter.write()
                                                           ├─ sink coalesces into Vec
                                                           └─ flush ──▶ [output channel, bounded]
                                                                            │
                                                       Python consumer ◀────┘ (async for)
```

* The parser task runs on a shared multi-threaded Tokio runtime.
* FFI crossings happen only at channel enqueue/dequeue points.
* Backpressure is symmetric: slow consumer → full output channel → parser
  blocks → full input channel → `feed()` awaits.
* Cancellation via `cancel()` or `Drop` triggers a `CancellationToken` that
  unblocks every select arm, ensuring clean shutdown.

## Performance

Benchmarks are split across two harnesses to isolate parse cost from PyO3 FFI + asyncio cost:

```bash
just bench          # Python async pipeline via pytest-benchmark
just bench-native   # Pure Rust baseline — no Python, no FFI
```

On a 110 KB HTML payload (`medium` in the bench suite) we get:

| Scenario                            | Throughput   | vs. native |
|-------------------------------------|--------------|------------|
| `null_copy` — memcpy ceiling        | ~58,000 MB/s | —          |
| `lol_html_native` — Rust only       | ~164 MB/s    | 1.00×      |
| `lol_html_py` — async Python        | ~113 MB/s    | 0.69×      |

The 1.45× gap between native and Python comes from crossing the FFI boundary,
sending data over Tokio channels, asyncio scheduling, and allocating a `PyBytes`
object for each output chunk. On this payload that adds up to roughly 300 μs
per run, most of which is fixed per invocation (not per byte), so will
matter less on larger documents and more on smaller ones.


### How input chunk size affects throughput

In the native Rust bench, feeding the payload in 256-byte writes or 64 KB writes
both measure ~164 MB/s — chunk size doesn't affect native performance.

In the Python bench, 256-byte chunks run at ~5.7 MB/s; 16 KB chunks run at
~113 MB/s. That's a 20× difference, and it's entirely FFI overhead: each
`await rw.feed(chunk)` crosses into Rust, sends on a channel, and returns an
awaitable. At 256 bytes per call you're doing ~430 of those round-trips per
document; at 16 KB you're doing ~7.

If you're feeding the rewriter from an HTTP response or a file read, chunks are
already ≥ 4 KB by default and this isn't something you need to think about.
If you're feeding it from a source that produces tiny writes (a tokenizer,
an SSE stream with small events), buffer them in Python before calling `feed()`.

### How flush policy affects throughput

With `flush_every_chunk=True` and `flush_threshold=1`, the bench runs at
~52 MB/s instead of ~113 MB/s — roughly 2× slower. Every input chunk causes
one output channel send, one `PyBytes` allocation, and one GIL reacquisition
on the Python side.

With the defaults (threshold 16 KB, no per-chunk flush) the rewriter batches
output until it has ~16 KB to send, which is usually one or two sends for a
document this size.

Pick eager flushing when you specifically need to forward each input chunk's
output before the next one arrives — server-sent events, incremental rendering,
anything the downstream consumer treats as a real-time stream. Otherwise the
default will be faster.

### Is 113 MB/s good?

Compared to raw memcpy (~58,000 MB/s), native lol_html is 355× slower. That
reflects the work of tokenizing HTML and matching element handlers —
lol_html isn't memory-bound, it's CPU-bound on parse logic. The Python bindings
achieve ~70% of native throughput, so the async layer is taking a meaningful
but not dominant share of total time.

## Development

Build from source, with uv:

```bash
uv sync
```

or with pip:

```bash
git clone https://github.com/lmmx/lol-html
cd lol-html
pip install maturin
maturin develop --release
```

## License

MIT © Louis Maddox
