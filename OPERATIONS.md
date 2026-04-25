# Operations & Replay Runbook

This document is for the engineer on the other end of a Tangle alert. It
covers the replay subsystem end-to-end: how to capture an incident, how to
re-run it offline, and how to verify that a detector change still catches
what production already caught.

For client/API integration see [`API.md`](./API.md). For upgrade and
compatibility policy see [`COMPATIBILITY.md`](./COMPATIBILITY.md).

## Why replay exists

A live Tangle monitor makes detection decisions once, in real time, and
then loses the exact graph state that drove them. Replay reconstructs that
state from a recorded event log so you can:

- Re-run an incident against the same detector code to confirm the alert
  was correct (or confirm a false positive).
- Re-run an incident against new detector code to verify a fix would
  trigger and an unrelated change does not regress detection.
- Hand a single `.tgz` bundle to a teammate or to support that contains
  everything needed to reproduce the alert locally.

Replay is deterministic: the recorded `event.timestamp` drives the
monitor's clock, and periodic background scans, OTel export, and metrics
are disabled. Same inputs and same code produce the same detections.

## Quick reference

| Task                              | Command                                             |
|-----------------------------------|-----------------------------------------------------|
| Run sidecar with event logging    | `tangle --event-log /var/log/tangle/events.jsonl`   |
| Build an incident bundle          | `tangle bundle out.tgz --event-log events.jsonl`    |
| Replay an event log               | `tangle replay events.jsonl`                        |
| Replay a bundle                   | `tangle replay incident.tgz`                        |
| Diff replay vs recorded detections| `tangle diff incident.tgz` (exit 2 if regression)   |

The subcommands `replay`, `bundle`, and `diff` are routed by the CLI when
they appear as the first argument. They do **not** show up in
`tangle --help`, which only lists serve flags. Use the table above as the
discovery surface.

## Step 1 — enable event logging

Replay is only as good as the event log. Turn it on in production *before*
you need it.

**Sidecar:**

```bash
tangle --host 0.0.0.0 --port 8090 \
  --event-log /var/log/tangle/events.jsonl
```

**Embedded library:**

```python
from tangle import TangleConfig, TangleMonitor

config = TangleConfig(
    event_log_path="/var/log/tangle/events.jsonl",
    event_log_fsync=True,   # default; flip to False on hot paths if log is replicated
)
monitor = TangleMonitor(config=config)
```

`event_log_fsync=True` (the default) flushes and `fsync()`s after every
append. A crash leaves a valid prefix that `EventLogReader` can still
read. Turning it off is safe only when the log is being replicated
elsewhere (e.g., shipped to S3 by a sidecar).

### What's in the log

The format is line-delimited JSON. The first line is a header pinning the
schema version; every subsequent line is one event with a per-line
truncated SHA-256 hash and a monotonic sequence number:

```json
{"kind":"header","producer":"tangle","schema":1}
{"kind":"event","seq":0,"hash":"<16 hex>","event":{"type":"register",...}}
{"kind":"event","seq":1,"hash":"<16 hex>","event":{"type":"wait_for",...}}
```

`EventLogReader` validates the header schema, the seq sequence, and each
hash on read. Tampering, truncation in the middle of a record, or a
schema downgrade all raise `LogCorruptionError`.

## Step 2 — capture an incident bundle

A bundle is a `.tgz` containing the event log, an optional file of live
detections, and a manifest pinning the Tangle version and config used at
capture time. The manifest is the first thing to look at when a replay
diverges — *did the detector code change, or did the config change?*

```bash
tangle bundle incident-2026-04-25.tgz \
  --event-log /var/log/tangle/events.jsonl \
  --detections detections.jsonl \
  --note "PagerDuty INC-1422 — drafter/reviewer livelock at 14:02 UTC"
```

`--detections` is optional. If you supply it, `tangle diff` can later
compare what fired live against what fires on replay. If you skip it,
`replay` still works, you just don't get a regression check.

### Capturing live detections to JSONL

Tangle does not currently ship a CLI command to dump live detections in
the format `bundle --detections` expects. Until it does, capture them
with a short script using the same encoder the bundle reader uses:

```python
# scripts/dump_detections.py
import json
import urllib.request

from tangle.replay.log import encode_detection
from tangle.types import Cycle, Detection, DetectionType, LivelockPattern, Severity

# Pull from the live sidecar /v1/detections (resolved=true to include closed ones).
url = "http://localhost:8090/v1/detections?resolved=true&limit=1000"
items = json.loads(urllib.request.urlopen(url).read())["items"]

with open("detections.jsonl", "w") as f:
    for it in items:
        cycle = livelock = None
        if it.get("cycle"):
            cycle = Cycle(agents=it["cycle"]["agents"], workflow_id=it["cycle"]["workflow_id"])
        if it.get("livelock"):
            ll = it["livelock"]
            livelock = LivelockPattern(
                agents=ll["agents"],
                pattern_length=ll["pattern_length"],
                repeat_count=ll["repeat_count"],
                workflow_id=ll["workflow_id"],
            )
        det = Detection(
            type=DetectionType(it["type"]),
            severity=Severity(it["severity"]),
            cycle=cycle,
            livelock=livelock,
        )
        f.write(json.dumps({"detection": encode_detection(det)}) + "\n")
```

Note that `/v1/detections` returns a stripped-down view of each detection
— UUIDs, `detected_at`, and edge lists are not exposed. The diff command
fingerprints on `(type, workflow_id, sorted agents)` only, so this loss
does not affect diffing; it does mean the bundled detections are coarser
than the originals.

### What the bundle contains

```
incident-2026-04-25.tgz
├── manifest.json       # tangle version, config, created_at, note, schema versions
├── events.jsonl        # verbatim copy of the event log
└── detections.jsonl    # one {"detection": {...}} per line (empty file if not provided)
```

Bundles are gzipped and self-contained. Hand one to anyone with `pip
install tangle-detect` and they can reproduce locally.

## Step 3 — replay an event log or bundle

```bash
tangle replay incident-2026-04-25.tgz
```

Output is one line per detection:

```
events_replayed: 24
detections: 1
  deadlock wf=wf-1 agents=['B', 'A'] severity=critical
```

Replay loads the manifest's pinned config (if any), constructs a fresh
monitor with periodic scans / OTel / metrics disabled, sets the clock to
each event's recorded timestamp, and feeds the events in order. Anything
the monitor would have detected the first time, it detects again — and
nothing else.

`tangle replay` also accepts a raw `events.jsonl` (no manifest, no
config). This is useful when you have a live log but haven't packaged it
into a bundle yet.

## Step 4 — diff replay against the live record

```bash
tangle diff incident-2026-04-25.tgz
```

The command unpacks the bundle, replays the events, then compares the
recorded detections against the freshly-produced ones. Output:

```
unchanged: 5
missing  : 1
  - livelock wf=wf-7 agents=['drafter', 'reviewer']
added    : 0
changed  : 0
```

Exit codes:

| Code | Meaning                                                                  |
|------|--------------------------------------------------------------------------|
| 0    | Replay matches recorded detections exactly (and no `added` / `changed`). |
| 2    | Regression — replay missed at least one recorded detection, *or* a   |
|      | matched detection's severity/type changed.                               |

`added` does **not** count as a regression. New detections that didn't
fire live but fire now may be:

- A genuine bug the original detector missed → good catch.
- A new false positive your tuning needs to suppress → tighten the
  thresholds in `TangleConfig` and re-run.

A human still has to triage `added`; the exit code is intentionally
silent on it.

## Common workflows

### "Did the new detector still catch this incident?"

You're about to merge a change to `detector/cycle.py`. You have a
collection of bundles from past incidents in `incidents/`.

```bash
for b in incidents/*.tgz; do
  echo "==> $b"
  tangle diff "$b" || echo "REGRESSION in $b"
done
```

Hook it into CI as a gate on detector PRs. Any nonzero exit fails the
build with a pointer to the incident that broke.

### "Is the live alert real?"

A page just woke you up. You have the bundle in front of you.

```bash
tangle replay /tmp/incident.tgz
```

If replay produces the same detection, the alert reflects what the
detector actually saw — focus on the workflow, not on Tangle. If replay
produces nothing, the live monitor has additional state from background
scans or events that aren't in the log; check the periodic scan cadence
and whether the event log was rotated mid-incident.

### "Promote a tuning change with confidence"

You want to drop `livelock_min_repeats` from 3 to 2. Build a representative
bundle from a recent quiet day and a recent incident day:

```bash
tangle bundle quiet.tgz --event-log quiet-events.jsonl --detections quiet-dets.jsonl
tangle bundle noisy.tgz --event-log noisy-events.jsonl --detections noisy-dets.jsonl
```

Edit the manifest's `config.livelock_min_repeats`, then replay both:

```bash
tangle replay quiet.tgz   # expect: detections == 0; any added is new false positive
tangle replay noisy.tgz   # expect: still catches the original; ideally fewer added
```

## Troubleshooting

### `LogCorruptionError: hash mismatch at line N`

The log has been edited or partially written. The reader fails closed in
strict mode, which is what you want — you can't trust replay results from
a tampered log. Truncate the log to the last good record (everything
before line N) and replay that prefix; the rest of the events are gone.

### `LogCorruptionError: unsupported schema X (expected 1)`

The log was written by a future Tangle version with a newer schema, or
a previous version that emitted a different schema number. Pin your
replay environment to the same Tangle version as the producer. Bundle
manifests record `tangle_version`; check it.

### Replay produces zero detections but live had alerts

Most common causes:

1. **Log started after the incident began.** Events that established the
   wait-for graph were not captured. Replay sees a `wait_for` from an
   already-waiting agent and accepts it, but never closes the cycle.
2. **Background scans caught it, not incremental detection.** Replay
   disables periodic scans (otherwise replay timing would diverge from
   recorded timing). If your live alert came from `_periodic_scan`, the
   event log alone won't reproduce it. Lower `cycle_check_interval` in
   production so the same cycles get caught incrementally.
3. **Config drift.** Manifest pinned `cycle_check_interval=5.0` but you
   replayed without the manifest config (raw `.jsonl`). Use the bundle
   form so the config rides along.

### Replay produces extra detections (`added` count > 0)

Either the detector got more sensitive (tuning change, code change) or
the live monitor was missing them. To distinguish, replay with the
manifest's pinned config — if `added` drops to 0, the divergence is
config; if not, it's code.

### `tangle bundle` rejects the event log

`bundle` raises `FileNotFoundError` if the path is wrong. It does not
sanity-check the log's schema; the validation happens later when
`unpack_bundle` is called (during `replay` or `diff`). If `replay`
fails immediately on a fresh bundle, the source log was already corrupt.

## Known limitations

- **No built-in detection-dump CLI.** `tangle bundle --detections` accepts
  a JSONL in `replay/log.py` schema, but no shipped tool produces that
  file from a live monitor. Use the script in
  [Capturing live detections to JSONL](#capturing-live-detections-to-jsonl)
  above; a future release should add `tangle dump-detections`.
- **Periodic scans don't replay.** Detections that only fired because the
  background `full_scan` saw them are not reproducible from the event log
  alone. Tighten `cycle_check_interval` if this matters for your
  workload.
- **SQLite events are not bundled.** The bundle command takes the JSONL
  event log, not the SQLite store. If you only have the SQLite store,
  there is currently no `to-jsonl` converter — capture the JSONL log in
  parallel from day one.
- **Manifest config is informational, not enforced.** `tangle replay
  events.jsonl` (raw log, no bundle) does not consult any manifest.
  `tangle replay bundle.tgz` and `tangle diff bundle.tgz` do, applying
  the config recorded at capture time. If you want to replay a bundle
  with different config, unpack it manually and call `replay_events()`
  from Python with your desired `TangleConfig`.
