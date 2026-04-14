//! Native Rust throughput benchmark — no Python, no FFI.
//!
//! Mirrors the scenarios in `tests/bench/bench_throughput.py` so you can
//! directly compare the MB/s rate achievable with lol_html alone against
//! the same workload driven through the Python async bindings. The gap
//! between the two is the cost of: FFI boundary, channel hops, asyncio
//! scheduling, PyBytes allocation, and GIL re-acquisition.
//!
//! Run with:
//!     cargo run --release --example bench_native
//!     cargo run --release --example bench_native -- --payload large
//!     cargo run --release --example bench_native -- --repeats 50

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

/// Run lol_html on `payload` in `chunk_size`-sized writes. No Python, no
/// channels, no coalescing layer — just the parser and an output `Vec`.
fn run_native(payload: &[u8], chunk_size: usize) -> usize {
    let mut output: Vec<u8> = Vec::with_capacity(payload.len());
    {
        let sink = |c: &[u8]| output.extend_from_slice(c);
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
        for chunk in payload.chunks(chunk_size) {
            rw.write(chunk).expect("write");
        }
        rw.end().expect("end");
    }
    output.len()
}

/// Null baseline: just copy the payload chunk-by-chunk into an output Vec.
/// Represents the theoretical floor for "do nothing useful".
fn run_null(payload: &[u8], chunk_size: usize) -> usize {
    let mut output: Vec<u8> = Vec::with_capacity(payload.len());
    for chunk in payload.chunks(chunk_size) {
        output.extend_from_slice(chunk);
    }
    output.len()
}

// ---- bench harness ----------------------------------------------------------

struct Result {
    name: String,
    mean_us: f64,
    stddev_us: f64,
    mbps: f64,
    out_bytes: usize,
}

fn bench<F: FnMut() -> usize>(name: impl Into<String>, repeats: usize, payload_len: usize, mut f: F) -> Result {
    // Warmup
    for _ in 0..3 {
        let _ = f();
    }
    let mut samples = Vec::with_capacity(repeats);
    let mut out_bytes = 0;
    for _ in 0..repeats {
        let t0 = Instant::now();
        out_bytes = f();
        samples.push(t0.elapsed().as_secs_f64() * 1e6);
    }
    let mean = samples.iter().sum::<f64>() / samples.len() as f64;
    let var = samples.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / samples.len() as f64;
    let stddev = var.sqrt();
    let mbps = (payload_len as f64) / (mean / 1e6) / 1e6;
    Result {
        name: name.into(),
        mean_us: mean,
        stddev_us: stddev,
        mbps,
        out_bytes,
    }
}

// ---- main -------------------------------------------------------------------

fn main() {
    // Tiny arg parser — keeps the example dep-free.
    let mut payload_size = "medium".to_string();
    let mut repeats = 20usize;
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
    println!("payload: {} ({} bytes, {:.1} KB)\n", payload_size, payload_len, payload_len as f64 / 1024.0);

    let chunk_sizes = [256usize, 4096, 16384, 65536];

    let mut results: Vec<Result> = Vec::new();

    // Null baseline — memcpy ceiling
    for &cs in &chunk_sizes {
        results.push(bench(format!("null_copy          [chunk={cs:>6}]"), repeats, payload_len,
            || run_null(&payload, cs)));
    }

    // Real lol_html work
    for &cs in &chunk_sizes {
        results.push(bench(format!("lol_html_native    [chunk={cs:>6}]"), repeats, payload_len,
            || run_native(&payload, cs)));
    }

    // Print a table that lines up with the pytest-benchmark output
    println!("{:<40} {:>12} {:>12} {:>14} {:>14}",
        "scenario", "mean (us)", "stddev (us)", "throughput", "out bytes");
    println!("{}", "-".repeat(96));
    for r in &results {
        println!("{:<40} {:>12.1} {:>12.1} {:>10.0} MB/s {:>14}",
            r.name, r.mean_us, r.stddev_us, r.mbps, r.out_bytes);
    }

    // Quick summary
    let best_native = results.iter()
        .filter(|r| r.name.starts_with("lol_html_native"))
        .min_by(|a, b| a.mean_us.partial_cmp(&b.mean_us).unwrap())
        .unwrap();
    let best_null = results.iter()
        .filter(|r| r.name.starts_with("null_copy"))
        .min_by(|a, b| a.mean_us.partial_cmp(&b.mean_us).unwrap())
        .unwrap();
    println!("\nbest native lol_html: {:.0} MB/s", best_native.mbps);
    println!("best null (memcpy):   {:.0} MB/s", best_null.mbps);
    println!("native parse overhead vs memcpy: {:.2}x", best_null.mbps / best_native.mbps);
    println!("\nCompare against your pytest-benchmark numbers to get the FFI + asyncio tax.");
}
