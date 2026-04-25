# Performance Baselines

This document publishes Tangle's measured performance characteristics so
operators can size deployments and so contributors can spot regressions.
All numbers are reproducible: the suite lives in `tests/benchmarks/` and
runs via `make bench`.

## Headline numbers

| Workload                                        | Median      | Throughput           |
|-------------------------------------------------|-------------|----------------------|
| Incremental cycle detection (10-agent chain)    | 10.8 µs     | ~93 K edges/sec      |
| Incremental cycle detection (100-agent chain)   | 88.2 µs     | ~11 K edges/sec      |
| Incremental cycle detection (1 K agents, depth 20) | 9.8 µs   | ~102 K edges/sec     |
| Kahn's full scan (100 agents)                   | 85.3 µs     | ~12 K scans/sec      |
| Kahn's full scan (1 K agents, depth 20)         | 980 µs      | ~1 K scans/sec       |
| Livelock check (window 50)                      | 10.4 µs     | ~96 K messages/sec   |
| Livelock check (window 100)                     | 16.2 µs     | ~62 K messages/sec   |
| Livelock check (window 200)                     | 29.2 µs     | ~34 K messages/sec   |
| End-to-end `process_event` (SEND, no detection) | 97.8 µs     | ~10 K events/sec     |
| Replay throughput (1 K events)                  | 4.82 ms     | ~207 K events/sec    |
| Replay throughput (10 K events)                 | 49.4 ms     | ~202 K events/sec    |
| RingBuffer.append                               | 0.23 µs     | ~4.3 M ops/sec       |

Read these as **median single-thread cost on the reference hardware below**.
Production numbers depend heavily on workload mix (event types, workflow
fan-out, livelock window) and the host's CPU.

## Reference hardware

| Field      | Value                                  |
|------------|----------------------------------------|
| CPU        | Apple M4                               |
| Memory     | 16 GB                                  |
| OS         | macOS 15.7.4 (Darwin 24.6.0, arm64)    |
| Python     | 3.10.19                                |
| Tangle     | 0.1.0                                  |
| pytest-benchmark | 5.2.3                            |

x86_64 server hardware will see different absolute numbers. The
*relationships* between benchmarks (e.g., window 200 ≈ 3× window 50) are
expected to hold across architectures.

## Methodology

All benchmarks use [pytest-benchmark](https://pytest-benchmark.readthedocs.io/)
with these settings:

- 10 000 warmup iterations to populate the CPU branch predictor and
  Python's dispatch caches before timing starts.
- Minimum 20 timed rounds; pytest-benchmark adds more if variance is high.
- Median + standard deviation reported. **Median is the honest summary
  statistic** — `Max` is dominated by GC pauses and OS scheduling jitter
  on a non-realtime kernel and is not a useful upper bound for capacity
  planning.

To reproduce locally:

```bash
make bench
# or, with custom flags:
uv run pytest tests/benchmarks/ \
  --benchmark-only \
  --benchmark-warmup=on \
  --benchmark-warmup-iterations=10000 \
  --benchmark-min-rounds=20
```

## Cycle detection

The cycle detector has two paths: incremental DFS triggered on every
`wait_for` edge, and a periodic full-graph scan via Kahn's algorithm.
`max_depth` (config: `max_cycle_length`, default 20) bounds the DFS
recursion.

| Benchmark                              | Median   | Stddev   | Notes |
|----------------------------------------|----------|----------|-------|
| `incremental_10_agents`                | 10.8 µs  | 4.0 µs   | 10-agent chain, depth 11 (above max_depth) |
| `incremental_100_agents`               | 88.2 µs  | 13.4 µs  | 100-agent chain, depth 101 (above max_depth) |
| `incremental_1000_agents` (depth=20)   | 9.8 µs   | 34.8 µs  | 1 K-agent chain, capped at depth 20 |
| `kahns_100_agents`                     | 85.3 µs  | 137.3 µs | Full-graph scan |
| `kahns_1000_agents` (depth=20)         | 980 µs   | 1.96 ms  | Full-graph scan |

**Why 1 000 agents looks faster than 100**: the 1 000-agent benchmark
sets `max_depth=20`, matching the production default. DFS bails out as
soon as it exceeds the bound, so the cost is dominated by the depth
limit, not the chain length. The 100-agent benchmark uses
`max_depth=101` so it walks the full chain. Production deployments at
default config get the smaller number.

**Operational implication**: at default settings the incremental detector
costs ~10 µs per `wait_for` event — well below the cost of a
`process_event` call (~98 µs), which itself is dominated by store
recording and lock acquisition rather than detection. The detector is
not your bottleneck.

## Livelock detection

The livelock detector hashes each message body (xxh128) and pattern-matches
over a per-pair ring buffer. Cost scales roughly linearly with `livelock_window`.

| Benchmark                        | Median   | Stddev  | Window |
|----------------------------------|----------|---------|--------|
| `livelock_window_50`             | 10.4 µs  | 0.9 µs  | 50     |
| `livelock_window_100`            | 16.2 µs  | 1.2 µs  | 100    |
| `livelock_window_200`            | 29.2 µs  | 1.6 µs  | 200    |

**Tuning guidance**: doubling `livelock_window` roughly doubles per-message
cost (window 200 is ~2.8× window 50). If your sidecar is CPU-bound on
livelock checks, halving the window to 25 typically halves cost — but see
[Detection Tuning Guide](README.md#detection-tuning-guide) before changing
detection parameters.

## End-to-end event ingest

`process_event` is the single function every event flows through, including
store recording, lock acquisition, retention bookkeeping, metrics, and
detector dispatch.

| Benchmark            | Median   | Stddev   | Workload |
|----------------------|----------|----------|----------|
| `process_event`      | 97.8 µs  | 329.8 µs | SEND event with 16-byte body, no detection fires |

The wide stddev (and the `Max` of ~24 ms not shown here) reflects GC
pauses caught during long benchmark runs. Median is stable across runs.

**Sizing rule of thumb**: a single-threaded sidecar processes
~10 K events/sec sustained. The `RLock` in `TangleMonitor` serializes
all ingest, so adding cores does not increase per-instance throughput.
Run multiple sidecars partitioned by `workflow_id` if you need more.

## Replay throughput

Replay is the operationally interesting case: how long does it take to
re-run an hour of production events offline?

| Benchmark              | Events  | Median   | Throughput          |
|------------------------|---------|----------|---------------------|
| `replay_1k_events`     | 1 000   | 4.82 ms  | ~207 K events/sec   |
| `replay_10k_events`    | 10 000  | 49.4 ms  | ~202 K events/sec   |

Throughput is **linear** at this scale. Extrapolating: a 1 M-event log
(roughly an hour at 280 events/sec sustained) replays in ~5 seconds.
A 100 M-event log replays in ~8 minutes.

**Why replay is ~20× faster than live ingest**: replay uses
`ExplicitClock`, skips the periodic scan thread, disables OTel and
metrics, and runs without a resolver chain firing webhooks. Replay
measures pure detection cost; live ingest measures the full
production hot path.

**Operational implication**: don't shy away from large replays during
incident response. A bundle from a multi-hour incident is still
seconds-to-minutes to diff against the current detector code. See
[`OPERATIONS.md`](OPERATIONS.md) for the replay workflow.

## Spotting regressions

If a PR changes detector code or hot paths, compare benchmark medians
against the table above. Anything more than **20 % slower at the median**
on this hardware is worth investigating — anything less is in the noise
band of a multi-tasking OS.

`pytest-benchmark` can save and compare runs:

```bash
# Save a baseline before your change
uv run pytest tests/benchmarks/ --benchmark-only \
  --benchmark-save=before

# Make your change, then save again
uv run pytest tests/benchmarks/ --benchmark-only \
  --benchmark-save=after

# Compare
uv run pytest-benchmark compare before after
```

Saved runs land in `.benchmarks/` and include the full distribution, so
the comparison reflects more than just the median.
