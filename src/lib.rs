//! Python bindings for `lol_html`, exposing an asynchronous streaming HTML
//! rewriter that cooperates with Python's `asyncio`.
//!
//! See [`rewriter::AsyncRewriter`] for the main entry point, and [`config`]
//! for environment-variable-driven defaults.

#![warn(clippy::pedantic, clippy::nursery)]
#![allow(
    clippy::module_name_repetitions,
    clippy::missing_errors_doc,
    clippy::missing_panics_doc,
    clippy::needless_pass_by_value
)]

pub mod config;
pub mod rewriter;

use pyo3::prelude::*;

use crate::rewriter::AsyncRewriter;

/// PyO3 module entry point.
#[pymodule]
fn _lol_html(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<AsyncRewriter>()?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
