//! Async, streaming HTML rewriter exposed to Python.
//!
//! # Architecture
//!
//! Because [`lol_html::HtmlRewriter`] is `!Send` (its handler trait objects
//! aren't `Send`), the parser cannot live on a multi-thread Tokio runtime.
//! We therefore run it on a **dedicated OS thread** with its own
//! current-thread Tokio runtime. Python-facing awaitables live on a separate,
//! shared multi-thread runtime registered with `pyo3-async-runtimes`, and the
//! two sides communicate exclusively via `tokio::sync::mpsc` channels (which
//! *are* `Send`).
//!
//! ```text
//! Python (asyncio)
//!    │  feed()/__anext__  ──────► awaitable on shared MT runtime
//!    │                                       │
//!    │                                  mpsc channels
//!    │                                       │
//!    └──────────────────────────────►  dedicated thread
//!                                            current-thread runtime
//!                                            HtmlRewriter (!Send, !Sync)
//!                                            coalescing sink buffer
//! ```
//!
//! # Flush policy
//!
//! * Size-based: flush when the sink buffer reaches `flush_threshold` bytes
//!   (disabled when `flush_threshold == 0`).
//! * Per-chunk: if `flush_every_chunk`, flush after every input chunk.
//! * End-of-stream: always flushes whatever remains after `rewriter.end()`.

use std::cell::RefCell;
use std::rc::Rc;
use std::sync::Arc;
use std::thread;

use lol_html::{element, HtmlRewriter, Settings};
use std::sync::LazyLock;
use pyo3::exceptions::{PyRuntimeError, PyStopAsyncIteration};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3_async_runtimes::tokio::future_into_py;
use tokio::runtime::{Builder, Runtime};
use tokio::sync::{mpsc, Mutex};
use tokio_util::sync::CancellationToken;

use crate::config;

/// Shared multi-thread Tokio runtime used for Python-facing awaitables.
///
/// Leaked to `'static` so `pyo3-async-runtimes::tokio::init_with_runtime`
/// accepts it. A single runtime per process is the intended model.
static RT: LazyLock<&'static Runtime> = LazyLock::new(|| {
    let rt = Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .thread_name("lol-html-py-io")
        .build()
        .expect("failed to build shared tokio runtime");
    let rt: &'static Runtime = Box::leak(Box::new(rt));
    // Safe to ignore: only fails if already initialized.
    let _ = pyo3_async_runtimes::tokio::init_with_runtime(rt);
    rt
});

fn shared_runtime() -> &'static Runtime {
    &RT
}

/// Asynchronous streaming HTML rewriter.
///
/// See module-level documentation for the architecture. Typical use:
///
/// ```python
/// import asyncio
/// from lol_html import AsyncRewriter
///
/// async def main():
///     rw = AsyncRewriter()
///     async def produce():
///         await rw.feed(b"<html><script>bad()</script><p>ok</p></html>")
///         rw.close()
///     async def consume():
///         out = bytearray()
///         async for chunk in rw:
///             out += chunk
///         return bytes(out)
///     _, result = await asyncio.gather(produce(), consume())
///     return result
///
/// asyncio.run(main())
/// ```
#[pyclass(module = "lol_html._lol_html")]
pub struct AsyncRewriter {
    input_tx: Option<mpsc::Sender<Vec<u8>>>,
    output_rx: Arc<Mutex<mpsc::Receiver<Vec<u8>>>>,
    cancel: CancellationToken,
}

#[pymethods]
impl AsyncRewriter {
    /// Construct a new rewriter.
    ///
    /// All parameters are optional; `None` falls back to process-wide defaults
    /// derived from environment variables (see [`crate::config`]).
    ///
    /// # Parameters
    ///
    /// * `input_capacity`    — bounded input channel depth.
    /// * `output_capacity`   — bounded output channel depth.
    /// * `flush_threshold`   — bytes accumulated before flushing. `0` disables
    ///   size-based flushing (output emits only at end-of-stream or on a
    ///   per-chunk forced flush).
    /// * `flush_every_chunk` — if `true`, force a flush after every input
    ///   chunk regardless of buffer size. Useful for SSE-style streams.
    #[new]
    #[pyo3(signature = (
        input_capacity = None,
        output_capacity = None,
        flush_threshold = None,
        flush_every_chunk = None,
    ))]
    pub fn new(
        input_capacity: Option<usize>,
        output_capacity: Option<usize>,
        flush_threshold: Option<usize>,
        flush_every_chunk: Option<bool>,
    ) -> PyResult<Self> {
        // Ensure the shared runtime is up so pyo3-async-runtimes can schedule
        // the awaitables returned by feed()/__anext__.
        let _ = shared_runtime();

        let input_capacity = input_capacity.unwrap_or_else(config::input_capacity).max(1);
        let output_capacity = output_capacity.unwrap_or_else(config::output_capacity).max(1);
        let flush_threshold = flush_threshold.unwrap_or_else(config::flush_threshold);
        let flush_every_chunk = flush_every_chunk.unwrap_or_else(config::flush_every_chunk);

        let (input_tx, input_rx) = mpsc::channel::<Vec<u8>>(input_capacity);
        let (output_tx, output_rx) = mpsc::channel::<Vec<u8>>(output_capacity);
        let cancel = CancellationToken::new();
        let cancel_worker = cancel.clone();

        // Dedicated OS thread for the !Send parser. Its current-thread Tokio
        // runtime drives the async mpsc recv/send operations, and the parser
        // itself (being sync) slots in naturally between awaits.
        thread::Builder::new()
            .name("lol-html-py-parser".to_string())
            .spawn(move || {
                let rt = Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("failed to build parser runtime");
                rt.block_on(parser_task(
                    input_rx,
                    output_tx,
                    cancel_worker,
                    flush_threshold,
                    flush_every_chunk,
                ));
            })
            .map_err(|e| PyRuntimeError::new_err(format!("spawn parser thread: {e}")))?;

        Ok(Self {
            input_tx: Some(input_tx),
            output_rx: Arc::new(Mutex::new(output_rx)),
            cancel,
        })
    }

    /// Feed an input chunk to the parser.
    ///
    /// Returns an awaitable. `await` blocks if the input channel is at
    /// capacity (producer-side backpressure). Raises `RuntimeError` if the
    /// parser has exited or the rewriter is closed.
    pub fn feed<'py>(&self, py: Python<'py>, chunk: Vec<u8>) -> PyResult<Bound<'py, PyAny>> {
        let tx = self
            .input_tx
            .clone()
            .ok_or_else(|| PyRuntimeError::new_err("rewriter is closed"))?;
        let cancel = self.cancel.clone();
        future_into_py(py, async move {
            tokio::select! {
                res = tx.send(chunk) => {
                    res.map_err(|_| PyRuntimeError::new_err("parser task has exited"))
                }
                () = cancel.cancelled() => Err(PyRuntimeError::new_err("rewriter was cancelled")),
            }
        })
    }

    /// Signal end-of-input. The parser finalizes, emits any buffered output,
    /// and closes the output channel. Idempotent.
    pub fn close(&mut self) -> PyResult<()> {
        self.input_tx.take();
        Ok(())
    }

    /// Cancel immediately, dropping any buffered output. Unlike `close()`,
    /// does not wait for end-of-stream semantics.
    pub fn cancel(&mut self) -> PyResult<()> {
        self.cancel.cancel();
        self.input_tx.take();
        Ok(())
    }

    fn __aiter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __anext__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let rx = self.output_rx.clone();
        future_into_py(py, async move {
            let mut guard = rx.lock().await;
            match guard.recv().await {
                Some(bytes) => Python::attach(|py| {
                    Ok(PyBytes::new(py, &bytes).unbind())
                }),
                None => Err(PyStopAsyncIteration::new_err(())),
            }
        })
    }
}

impl Drop for AsyncRewriter {
    fn drop(&mut self) {
        self.cancel.cancel();
    }
}

/// Long-lived parser loop. Runs on the dedicated parser thread's
/// current-thread runtime; owns the `!Send` [`HtmlRewriter`] and a reusable
/// sink buffer.
async fn parser_task(
    mut input_rx: mpsc::Receiver<Vec<u8>>,
    output_tx: mpsc::Sender<Vec<u8>>,
    cancel: CancellationToken,
    flush_threshold: usize,
    flush_every_chunk: bool,
) {
    let initial_cap = flush_threshold.max(4096);
    let buf: Rc<RefCell<Vec<u8>>> = Rc::new(RefCell::new(Vec::with_capacity(initial_cap)));
    let sink_buf = buf.clone();

    let sink = move |chunk: &[u8]| {
        sink_buf.borrow_mut().extend_from_slice(chunk);
    };

    let mut rewriter = HtmlRewriter::new(
        Settings {
            element_content_handlers: vec![
                element!("script", |el| {
                    el.remove();
                    Ok(())
                }),
                element!("style", |el| {
                    el.remove();
                    Ok(())
                }),
            ],
            ..Settings::new()
        },
        sink,
    );

    loop {
        tokio::select! {
            biased;
            () = cancel.cancelled() => break,
            maybe_chunk = input_rx.recv() => match maybe_chunk {
                Some(chunk) => {
                    if rewriter.write(&chunk).is_err() {
                        break;
                    }
                    if flush(&buf, &output_tx, &cancel, flush_threshold, flush_every_chunk)
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
                None => break,
            }
        }
    }

    // Always finalize. `end()` consumes the rewriter, emitting any trailing
    // output through the sink closure we still share via `buf`.
    let _ = rewriter.end();
    let _ = flush(&buf, &output_tx, &cancel, flush_threshold, true).await;
    // Dropping output_tx here signals EOF to Python consumers.
}

/// Flush the sink buffer to the output channel if policy dictates.
///
/// Returns `Err(())` if the consumer has gone away or cancellation fired.
async fn flush(
    buf: &Rc<RefCell<Vec<u8>>>,
    tx: &mpsc::Sender<Vec<u8>>,
    cancel: &CancellationToken,
    threshold: usize,
    force: bool,
) -> Result<(), ()> {
    let should_send = {
        let b = buf.borrow();
        !b.is_empty() && (force || (threshold > 0 && b.len() >= threshold))
    };
    if !should_send {
        return Ok(());
    }
    // Move the buffer contents out before awaiting — never hold a RefCell
    // borrow across `.await`.
    let out = std::mem::take(&mut *buf.borrow_mut());
    tokio::select! {
        res = tx.send(out) => res.map_err(|_| ()),
        () = cancel.cancelled() => Err(()),
    }
}
