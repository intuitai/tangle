# src/tangle/cli.py

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from tangle.config import TangleConfig
from tangle.monitor import TangleMonitor

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tangle.types import Detection

_REPLAY_SUBCOMMANDS = {"replay", "bundle", "diff"}


def _serve(args: argparse.Namespace) -> int:
    from tangle.server.app import create_app

    event_log = getattr(args, "event_log", "")
    if not isinstance(event_log, str):
        event_log = ""
    config = TangleConfig(
        server_host=args.host,
        server_port=args.port,
        event_log_path=event_log,
    )
    monitor = TangleMonitor(config=config)
    app = create_app(monitor)

    monitor.start_background()
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        monitor.stop()
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    from tangle.replay import replay_events, unpack_bundle

    source = Path(args.source)
    if source.suffix in {".tgz", ".gz"} or source.name.endswith(".tar.gz"):
        bundle = unpack_bundle(source)
        config = TangleConfig(**bundle.manifest.config) if bundle.manifest.config else None
        result = replay_events(bundle.events, config=config)
    else:
        result = replay_events(source)

    print(f"events_replayed: {result.events_replayed}")
    print(f"detections: {len(result.detections)}")
    for d in result.detections:
        if d.cycle is not None:
            print(
                f"  deadlock wf={d.cycle.workflow_id} agents={d.cycle.agents}"
                f" severity={d.severity.value}"
            )
        elif d.livelock is not None:
            print(
                f"  livelock wf={d.livelock.workflow_id} agents={d.livelock.agents}"
                f" severity={d.severity.value}"
            )
    return 0


def _cmd_bundle(args: argparse.Namespace) -> int:
    from tangle.replay import pack_bundle
    from tangle.replay.log import decode_detection

    detections: list[Detection] = []
    if args.detections:
        for raw in Path(args.detections).read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            rec = json.loads(raw)
            detections.append(decode_detection(rec.get("detection", rec)))

    path = pack_bundle(
        args.output,
        events_log=args.event_log,
        detections=detections,
        note=args.note or "",
    )
    print(f"wrote bundle: {path}")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    from tangle.replay import diff_detections, replay_events, unpack_bundle

    bundle = unpack_bundle(args.bundle)
    config = TangleConfig(**bundle.manifest.config) if bundle.manifest.config else None
    result = replay_events(bundle.events, config=config)
    diff = diff_detections(bundle.detections, result.detections)
    print(diff.format())
    return 2 if diff.is_regression else 0


def _build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tangle sidecar server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--event-log",
        default="",
        help="Path to write an append-only event log for later replay",
    )
    return parser


def _build_replay_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tangle", description="Tangle replay toolkit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    replay = sub.add_parser("replay", help="Replay an event log or bundle locally")
    replay.add_argument("source", help="Path to events.jsonl or a .tgz bundle")
    replay.set_defaults(func=_cmd_replay)

    bundle = sub.add_parser("bundle", help="Pack an event log + detections into a support bundle")
    bundle.add_argument("output", help="Output path (e.g. incident-42.tgz)")
    bundle.add_argument("--event-log", required=True, help="Path to events.jsonl")
    bundle.add_argument(
        "--detections",
        default="",
        help="Optional JSONL of recorded detections to include",
    )
    bundle.add_argument("--note", default="", help="Free-form note stored in manifest")
    bundle.set_defaults(func=_cmd_bundle)

    diff = sub.add_parser(
        "diff",
        help="Replay a bundle and diff new detections against the recorded ones",
    )
    diff.add_argument("bundle", help="Path to a .tgz bundle")
    diff.set_defaults(func=_cmd_diff)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]

    if raw and raw[0] in _REPLAY_SUBCOMMANDS:
        parser = _build_replay_parser()
        args = parser.parse_args(raw)
        return int(args.func(args) or 0)

    parser = _build_serve_parser()
    args = parser.parse_args(raw)
    return int(_serve(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
