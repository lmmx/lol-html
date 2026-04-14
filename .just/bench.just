# Benchmark the package
bench-native:
    #!/usr/bin/env -S echo-comment --shell-flags="-e" --color bright-green
    # Run the native Rust code example to bench speed without Python
    cargo run --release --example bench_native

# Run the scenario bench side of the Python bindings
bench-python:
    #!/usr/bin/env -S echo-comment --shell-flags="-e" --color bright-green
    # Run Python scenario bench matching the Rust example
    python examples/bench_scenarios.py

# Run both side-by-side, on the same payload
bench-compare:
    #!/usr/bin/env -S echo-comment --shell-flags="-e" --color bright-green
    # Run Rust and Python scenario benches back-to-back for direct comparison
    # === Rust native ===
    cargo run --release --example bench_native
    # 
    # === Python (lol-html-py) ===
    python examples/bench_scenarios.py
