//! Runtime configuration for the async HTML rewriter.
//!
//! Configuration is populated from environment variables at library load time
//! via a `ctor` function, so values are available by the time any Python code
//! imports the module. All fields can be overridden per-instance via the
//! `AsyncRewriter` constructor; the environment-derived values act as
//! process-wide defaults.
//!
//! # Environment variables
//!
//! | Variable                         | Type    | Default |
//! |----------------------------------|---------|---------|
//! | `LOL_HTML_FLUSH_THRESHOLD`       | `usize` | `16384` |
//! | `LOL_HTML_FLUSH_EVERY_CHUNK`     | `bool`  | `false` |
//! | `LOL_HTML_INPUT_CAPACITY`        | `usize` | `8`     |
//! | `LOL_HTML_OUTPUT_CAPACITY`       | `usize` | `8`     |
//!
//! Boolean values accept `1`/`true`/`yes`/`on` (case-insensitive) as truthy;
//! anything else is falsy.

use std::env;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

/// Default buffer size (bytes) at which the sink flushes to the output channel.
pub const DEFAULT_FLUSH_THRESHOLD: usize = 16 * 1024;
/// Default bounded capacity for the input channel.
pub const DEFAULT_INPUT_CAPACITY: usize = 8;
/// Default bounded capacity for the output channel.
pub const DEFAULT_OUTPUT_CAPACITY: usize = 8;

static FLUSH_THRESHOLD: AtomicUsize = AtomicUsize::new(DEFAULT_FLUSH_THRESHOLD);
static FLUSH_EVERY_CHUNK: AtomicBool = AtomicBool::new(false);
static INPUT_CAPACITY: AtomicUsize = AtomicUsize::new(DEFAULT_INPUT_CAPACITY);
static OUTPUT_CAPACITY: AtomicUsize = AtomicUsize::new(DEFAULT_OUTPUT_CAPACITY);

/// Parse an environment variable as a `usize`, falling back to `default` if
/// unset or unparseable.
fn env_usize(key: &str, default: usize) -> usize {
    env::var(key)
        .ok()
        .and_then(|v| v.trim().parse::<usize>().ok())
        .unwrap_or(default)
}

/// Parse an environment variable as a boolean. Truthy values are
/// `1`, `true`, `yes`, `on` (case-insensitive). Unset variables fall back to
/// `default`.
fn env_bool(key: &str, default: bool) -> bool {
    match env::var(key) {
        Ok(v) => {
            let v = v.trim();
            matches!(
                v.to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            ) || (!matches!(
                v.to_ascii_lowercase().as_str(),
                "0" | "false" | "no" | "off" | ""
            ) && default)
        }
        Err(_) => default,
    }
}

/// Populate configuration atomics from environment variables.
///
/// Called automatically at library load via `#[ctor::ctor]`. Safe to call
/// again manually (e.g. in tests) to re-read the environment.
pub fn init_from_env() {
    FLUSH_THRESHOLD.store(
        env_usize("LOL_HTML_FLUSH_THRESHOLD", DEFAULT_FLUSH_THRESHOLD),
        Ordering::Relaxed,
    );
    FLUSH_EVERY_CHUNK.store(env_bool("LOL_HTML_FLUSH_EVERY_CHUNK", false), Ordering::Relaxed);
    INPUT_CAPACITY.store(
        env_usize("LOL_HTML_INPUT_CAPACITY", DEFAULT_INPUT_CAPACITY),
        Ordering::Relaxed,
    );
    OUTPUT_CAPACITY.store(
        env_usize("LOL_HTML_OUTPUT_CAPACITY", DEFAULT_OUTPUT_CAPACITY),
        Ordering::Relaxed,
    );
}

/// Current default flush threshold (bytes).
#[must_use]
pub fn flush_threshold() -> usize {
    FLUSH_THRESHOLD.load(Ordering::Relaxed)
}

/// Whether to force a flush after every input chunk, regardless of buffer size.
#[must_use]
pub fn flush_every_chunk() -> bool {
    FLUSH_EVERY_CHUNK.load(Ordering::Relaxed)
}

/// Current default bounded capacity for the input channel.
#[must_use]
pub fn input_capacity() -> usize {
    INPUT_CAPACITY.load(Ordering::Relaxed)
}

/// Current default bounded capacity for the output channel.
#[must_use]
pub fn output_capacity() -> usize {
    OUTPUT_CAPACITY.load(Ordering::Relaxed)
}

/// Library-load constructor. Reads environment variables into the config
/// atomics before any Python code runs.
#[ctor::ctor]
fn ctor_init() {
    init_from_env();
}
