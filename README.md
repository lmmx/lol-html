# lol-html-py

Async streaming Python bindings for [lol_html], the low-output-latency HTML
rewriter from Cloudflare. Built on [PyO3] and [Tokio], integrates with
`asyncio` via [pyo3-async-runtimes].

[lol_html]: https://github.com/cloudflare/lol-html
[PyO3]: https://pyo3.rs
[Tokio]: https://tokio.rs
[pyo3-async-runtimes]: https://github.com/PyO3/pyo3-async-runtimes

## Why

`lol_html` is designed for streaming — it can begin emitting output before
the full input has been received. Exposing that property to Python requires
more than a thin FFI wrapper: per-chunk round trips across the PyO3 boundary
will dominate runtime for small chunks. This package solves that by running
the rewriter on a long-lived Tokio task, coalescing output in Rust, and
crossing the FFI boundary only at channel endpoints with symmetric
backpressure on both input and output.

## Install

```bash
pip install lol-html-py
```

Or from source:

```bash
git clone https://github.com/lmmx/lol-html
cd lol-html
pip install maturin
maturin develop --release
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

Four knobs control the latency/throughput trade-off. All have sensible
defaults; all are overridable per instance and via environment variables.

| Constructor arg       | Env var                         | Default | Meaning                                                          |
|-----------------------|---------------------------------|---------|------------------------------------------------------------------|
| `input_capacity`      | `LOL_HTML_INPUT_CAPACITY`       | `8`     | Bounded input channel depth (producer backpressure)              |
| `output_capacity`     | `LOL_HTML_OUTPUT_CAPACITY`      | `8`     | Bounded output channel depth (consumer backpressure)             |
| `flush_threshold`     | `LOL_HTML_FLUSH_THRESHOLD`      | `16384` | Bytes accumulated before flushing to the output channel          |
| `flush_every_chunk`   | `LOL_HTML_FLUSH_EVERY_CHUNK`    | `false` | Force a flush after every input chunk, regardless of buffer size |

Environment variables are read at library load time via a Rust constructor
(`ctor`). Per-instance constructor arguments always win over env defaults.

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

## License

MIT © Louis Maddox
