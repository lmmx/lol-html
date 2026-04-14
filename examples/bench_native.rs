//! Native Rust throughput benchmark — no Python, no FFI.
//!
//! Runs the same three operating-point scenarios documented in the README
//! (SSE / general streaming / batch) so numbers can be compared directly
//! against the Python bench in tests/bench/.
//!
//! The Rust side mirrors the bindings' flush policy (coalescing buffer with
//! optional per-chunk force flush) so we're comparing like with like — the
//! "Python tax" we isolate is FFI + asyncio only, not the buffering strategy.

use std::env;
use std::time::Instant;

use lol_html::{element, HtmlRewriter, Settings};

// ---- payloads ---------------------------------------------------------------

fn make_payload(size: &str) -> Vec<u8> {
    match size {
        "small" => {
            let mut v = Vec::from(&b"<html><body>"[..]);
            for _ in 0..100 {
                v.extend_from_slice(b"<p>hi</p>");
            }
            v.extend_from_slice(b"</body></html>");
            v
        }
        "medium" => {
            let mut v = Vec::from(&b"<html><body>"[..]);
            for _ in 0..10_000 {
                v.extend_from_slice(b"<p>item</p>");
            }
            v.extend_from_slice(b"</body></html>");
            v
        }
        "large" => {
            let mut v = Vec::from(&b"<html><body>"[..]);
            for _ in 0..20_000 {
                v.extend_from_slice(b"<div><p>nested ");
                for _ in 0..5 {
                    v.extend_from_slice(b"<span>stuff</span>");
                }
                v.extend_from_slice(b"</p></div>");
            }
            v.extend_from_slice(b"</body></html>");
            v
        }
        other => panic!("unknown payload size: {other}"),
    }
}

// ---- scenarios --------------------------------------------------------------

/// Parameters matching the Python `AsyncRewriter` config.
#[derive(Clone, Copy)]
struct Scenario {
    name: &'static str,
    chunk_size: usize,
    flush_threshold: usize,
    flush_every_chunk: bool,
}

const SCENARIOS: &[Scenario] = &[
    // SSE: every input chunk produces an output chunk immediately.
    Scenario {
        name: "SSE (per-chunk flush)",
        chunk_size: 256,
        flush_threshold: 1,
        flush_every_chunk: true,
    },
    // General streaming: default-ish config, moderate chunks.
    Scenario {
        name: "general streaming",
        chunk_size: 4096,
        flush_threshold: 16 * 1024,
        flush_every_chunk: false,
    },
    // Batch: big chunks, big flush threshold — throughput over latency.
    Scenario {
        name: "high-throughput batch",
        chunk_size: 65536,
        flush_threshold: 64 * 1024,
        flush_every_chunk: false,
    },
];

/// Run lol_html with the bindings' coalescing sink behavior emulated.
/// Returns (total_output_bytes, number_of_flushes).
fn run_scenario(payload: &[u8], s: Scenario) -> (usize, usize) {
    // Coalescing buffer, matching src/rewriter.rs behavior.
    let mut coalesce: Vec<u8> = Vec::with_capacity(s.flush_threshold.max(4096));
    // "Channel": in the Python version this would be an mpsc::channel
    // sending Vec<u8>. Here we simulate by counting flushes and total bytes.
    let mut total_out = 0usize;
    let mut flushes = 0usize;

    let mut do_flush = |buf: &mut Vec<u8>, force: bool| {
        let should = !buf.is_empty()
            && (force || (s.flush_threshold > 0 && buf.len() >= s.flush_threshold));
        if should {
            total_out += buf.len();
            flushes += 1;
            buf.clear();
        }
    };

    {
        // Sink writes into the coalescing buffer. This is the exact shape
        // used in rewriter.rs (Rc<RefCell<Vec>> there; bare &mut here since
        // we're single-threaded and don't need the async machinery).
        let coalesce_ptr: *mut Vec<u8> = &mut coalesce;
        let sink = move |c: &[u8]| {
            // Safety: the closure, the rewriter, and the buffer all live
            // within this scope; no aliasing.
            unsafe { (*coalesce_ptr).extend_from_slice(c) };
        };

        let mut rw = HtmlRewriter::new(
            Settings {
                element_content_handlers: vec![
                    element!("script", |el| { el.remove(); Ok(()) }),
                    element!("style",  |el| { el.remove(); Ok(()) }),
                ],
                ..Settings::new()
            },
            sink,
        );

        for chunk in payload.chunks(s.chunk_size) {
            rw.write(chunk).expect("write");
            do_flush(&mut coalesce, s.flush_every_chunk);
        }
        rw.end().expect("end");
    }

    // Final flush of any remainder.
    do_flush(&mut coalesce, true);

    (total_out, flushes)
}

// ---- bench harness ----------------------------------------------------------

struct Result {
    name: String,
    mean_us: f64,
    stddev_us: f64,
    mbps: f64,
    flushes: usize,
}

fn bench<F: FnMut() -> (usize, usize)>(
    name: impl Into<String>,
    repeats: usize,
    payload_len: usize,
    mut f: F,
) -> Result {
    for _ in 0..3 { let _ = f(); } // warmup
    let mut samples = Vec::with_capacity(repeats);
    let mut flushes = 0usize;
    for _ in 0..repeats {
        let t0 = Instant::now();
        let (_, fl) = f();
        samples.push(t0.elapsed().as_secs_f64() * 1e6);
        flushes = fl;
    }
    let mean = samples.iter().sum::<f64>() / samples.len() as f64;
    let var = samples.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / samples.len() as f64;
    let stddev = var.sqrt();
    let mbps = (payload_len as f64) / (mean / 1e6) / 1e6;
    Result { name: name.into(), mean_us: mean, stddev_us: stddev, mbps, flushes }
}

// ---- main -------------------------------------------------------------------

fn main() {
    let mut payload_size = "medium".to_string();
    let mut repeats = 50usize;
    let args: Vec<String> = env::args().collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--payload" => { payload_size = args[i + 1].clone(); i += 2; }
            "--repeats" => { repeats = args[i + 1].parse().expect("int"); i += 2; }
            other => panic!("unknown arg: {other}"),
        }
    }

    let payload = make_payload(&payload_size);
    let payload_len = payload.len();
    println!("payload: {} ({} bytes, {:.1} KB)", payload_size, payload_len, payload_len as f64 / 1024.0);
    println!("repeats: {}\n", repeats);

    let mut results: Vec<Result> = Vec::new();
    for s in SCENARIOS {
        let label = format!(
            "{:<24} [chunk={:>6}, thresh={:>6}, eager={}]",
            s.name, s.chunk_size, s.flush_threshold, s.flush_every_chunk
        );
        results.push(bench(label, repeats, payload_len, || run_scenario(&payload, *s)));
    }

    println!("{:<70} {:>10} {:>10} {:>12} {:>8}",
        "scenario", "mean μs", "stddev μs", "MB/s", "flushes");
    println!("{}", "-".repeat(112));
    for r in &results {
        println!("{:<70} {:>10.1} {:>10.1} {:>12.0} {:>8}",
            r.name, r.mean_us, r.stddev_us, r.mbps, r.flushes);
    }
}
