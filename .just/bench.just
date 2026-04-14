# Benchmark the package
bench:
    #!/usr/bin/env -S echo-comment --shell-flags="-e" --color bright-green
    # Full suite minus benchmarks and slow tests
    pytest tests/bench --benchmark-only --benchmark-columns=mean,stddev,ops

bench-native:
    #!/usr/bin/env -S echo-comment --shell-flags="-e" --color bright-green
    # Run the native Rust code example to bench speed without Python
    cargo run --release --example bench_native

bench-save-baseline:
    #!/usr/bin/env -S echo-comment --shell-flags="-e" --color bright-green
    # Save a baseline, then compare after changes
    pytest tests/bench --benchmark-only --benchmark-autosave

bench-compare-baseline:
    #!/usr/bin/env -S echo-comment --shell-flags="-e" --color bright-green
    # After making changes, compare to baseline
    pytest tests/bench --benchmark-only --benchmark-compare
