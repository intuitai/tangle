"""
Customer Support Escalation Example — Livelock + Deadlock Detection
====================================================================

Demonstrates both failure modes Tangle detects in a four-agent AI customer
support pipeline:

  1. **Livelock** — drafter and reviewer exchange the same reject/revise
     messages in a loop. Tangle detects the repeated pattern via xxhash
     digest matching and injects a tiebreaker prompt to break the loop.

  2. **Deadlock** — all four agents form a circular wait chain. Tangle
     detects the cycle instantly via incremental DFS.

Architecture::

                    Customer Support Agent Pipeline
                    ===============================

  ┌──────────┐      ┌────────────┐      ┌──────────┐      ┌──────────┐
  │  triage  │─────>│ researcher │─────>│  drafter │─────>│ reviewer │
  │          │      │            │      │          │      │          │
  │ classify │      │ KB lookup  │      │ write    │      │ approve/ │
  │ ticket   │      │ acct hist  │      │ response │      │ reject   │
  └──────────┘      └────────────┘      └─────┬────┘      └────┬─────┘
       ^                                      |  ^              |
       │                                      │  │              │
       │        DEADLOCK: circular wait       │  └──────────────┘
       │        reviewer ──> triage ──>       │    LIVELOCK: repeated
       │        researcher ──> drafter ──>    │    reject/revise loop
       │        reviewer (4-agent cycle)      │    (same messages 3x)
       │                                      │
       └──────────────────────────────────────┘

                         │
                         ▼
              ┌─────────────────────┐
              │    TangleMonitor    │
              │                     │
              │  Cycle Detector ────┼──> DEADLOCK  ──> alert callback
              │  (incremental DFS)  │
              │                     │
              │  Livelock Detector ─┼──> LIVELOCK  ──> tiebreaker prompt
              │  (xxhash ring buf)  │
              └─────────────────────┘

No external dependencies — uses the SDK directly (no LangGraph required):

    cd tangle-py && uv run python examples/customer_support_escalation.py
"""

from __future__ import annotations

from tangle import TangleConfig, TangleMonitor
from tangle.types import Detection, DetectionType

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Use "alert" resolution so the on_detection callback fires for every detection.
# We handle tiebreaker logic manually inside the callback so that only livelock
# detections get a tiebreaker prompt (deadlocks get reported but not tiebroken).
config = TangleConfig(
    cycle_check_interval=999_999.0,  # Disable periodic scans; incremental DFS is enough
    livelock_min_repeats=3,  # Detect after 3 repetitions
    livelock_window=20,  # Messages to keep in the analysis window
    livelock_min_pattern=2,  # Minimum 2-message pattern (drafter + reviewer)
    resolution="alert",  # Alert only; tiebreaker handled in callback
)

TIEBREAKER_PROMPT = (
    "You appear to be in a loop. Please try a different approach or report that you are stuck."
)


# ---------------------------------------------------------------------------
# Detection callback
# ---------------------------------------------------------------------------


def on_detection(detection: Detection) -> None:
    """Pretty-print detection details and inject tiebreaker for livelocks."""
    if detection.type == DetectionType.LIVELOCK and detection.livelock:
        ll = detection.livelock
        agents = ", ".join(sorted(ll.agents))
        print("\n*** [TANGLE] LIVELOCK DETECTED! ***")
        print(f"  Agents:       {agents}")
        print(f"  Pattern:      {ll.pattern_length} messages repeated {ll.repeat_count} times")
        print(f"  Workflow:     {ll.workflow_id}")
        # Inject tiebreaker to the first agent (sorted for stable output)
        target = sorted(ll.agents)[0]
        print(f"  Resolution:   tiebreaker prompt injected to '{target}'")
        print(f'  Prompt:       "{TIEBREAKER_PROMPT}"')

    elif detection.type == DetectionType.DEADLOCK and detection.cycle:
        cycle = detection.cycle
        # Print agents in sorted order — cycle traversal order is not canonical
        agents_sorted = sorted(cycle.agents)
        print("\n*** [TANGLE] DEADLOCK DETECTED! ***")
        print(f"  Cycle:    {' -> '.join(agents_sorted)} (cycle)")
        print(f"  Length:   {len(cycle.agents)} agents")
        print(f"  Workflow: {cycle.workflow_id}")
        print("  Severity: CRITICAL")


# ---------------------------------------------------------------------------
# Build the monitor
# ---------------------------------------------------------------------------

monitor = TangleMonitor(
    config=config,
    on_detection=on_detection,
)


# ---------------------------------------------------------------------------
# Part 1: Livelock — drafter/reviewer reject loop
# ---------------------------------------------------------------------------


def simulate_livelock(workflow_id: str) -> None:
    """Simulate a drafter/reviewer reject loop that triggers livelock detection."""
    print("-" * 60)
    print("[1] Simulating livelock: drafter/reviewer reject loop...")
    print("-" * 60)

    # Register agents
    monitor.register(workflow_id=workflow_id, agent_id="drafter")
    monitor.register(workflow_id=workflow_id, agent_id="reviewer")

    draft_body = b"We apologize for the billing issue..."
    reject_body = b"Reject: too vague, needs account specifics"

    # Repeat the same two messages 3 times — triggers livelock detection on the
    # 3rd repetition when the xxhash digest ring buffer matches the pattern.
    for _ in range(3):
        print(f'\n[drafter  -> reviewer] "{draft_body.decode()}"')
        monitor.send(workflow_id, from_agent="drafter", to_agent="reviewer", body=draft_body)

        print(f'[reviewer -> drafter ] "{reject_body.decode()}"')
        monitor.send(workflow_id, from_agent="reviewer", to_agent="drafter", body=reject_body)

    # report_progress() resets the livelock detector's ring buffer for this
    # workflow, preventing an immediate re-trigger. Note: it does NOT mark the
    # prior detection as resolved — active_detections() will still include it.
    print("\n[progress] Drafter changes approach after tiebreaker...")
    monitor.report_progress(workflow_id, description="drafter changed approach")

    # Send a new, different message to show normal operation resumes
    new_draft = b"Per account #12345, the charge on 03/15 was..."
    print(f'[drafter  -> reviewer] "{new_draft.decode()}"')
    monitor.send(workflow_id, from_agent="drafter", to_agent="reviewer", body=new_draft)
    print("[1] Livelock remediation complete. Normal operation resumed.")


# ---------------------------------------------------------------------------
# Part 2: Deadlock — 4-agent circular wait
# ---------------------------------------------------------------------------


def simulate_deadlock(workflow_id: str) -> None:
    """Simulate a 4-agent circular wait that triggers deadlock detection."""
    print("\n" + "-" * 60)
    print("[2] Simulating deadlock: 4-agent circular wait...")
    print("-" * 60)

    agents = ["triage", "researcher", "drafter", "reviewer"]
    for agent in agents:
        monitor.register(workflow_id=workflow_id, agent_id=agent)

    waits = [
        ("triage", "researcher", "account_lookup"),
        ("researcher", "drafter", "draft_context"),
        ("drafter", "reviewer", "approval"),
    ]
    for from_agent, to_agent, resource in waits:
        print(f"[{from_agent:<12}] waiting for {to_agent:<12} (resource: {resource})")
        monitor.wait_for(workflow_id, from_agent=from_agent, to_agent=to_agent, resource=resource)

    # This closing edge triggers incremental DFS cycle detection immediately
    print(f"[{'reviewer':<12}] waiting for {'triage':<12} (resource: reclassification)")
    print("                                       ^^^ cycle closes here!")
    monitor.wait_for(
        workflow_id,
        from_agent="reviewer",
        to_agent="triage",
        resource="reclassification",
    )


# ---------------------------------------------------------------------------
# Part 3: Recovery and inspection
# ---------------------------------------------------------------------------


def show_recovery() -> None:
    """Print monitor stats, workflow snapshot, and demonstrate reset_workflow."""
    print("\n" + "-" * 60)
    print("[3] Recovery and inspection")
    print("-" * 60)

    s = monitor.stats()
    print("\nMonitor stats:")
    print(f"  Events processed:   {s['events_processed']}")
    print(f"  Active detections:  {s['active_detections']}")
    print(f"  Graph nodes:        {s['graph_nodes']}")
    print(f"  Graph edges:        {s['graph_edges']}")

    # Snapshot the deadlock workflow — sort agents for stable output
    snap = monitor.snapshot("wf-support-ticket-99")
    print("\nWorkflow snapshot (wf-support-ticket-99):")
    for agent in sorted(snap.nodes):
        state = snap.states.get(agent, "unknown")
        state_str = state.value if hasattr(state, "value") else str(state)
        print(f"  {agent:<12}— {state_str.upper()}")

    # Reset the deadlock workflow — clears its agents, edges, and detections
    print("\nResetting workflow wf-support-ticket-99...")
    monitor.reset_workflow("wf-support-ticket-99")

    s2 = monitor.stats()
    det_count = s2["active_detections"]
    print(f"  Active detections after reset: {det_count}  (livelock from ticket-42 remains)")
    print(f"  Graph edges after reset:       {s2['graph_edges']}")


# ---------------------------------------------------------------------------
# Failure mode summary
# ---------------------------------------------------------------------------


def print_failure_modes() -> None:
    """Print what failure modes Tangle prevented in this example."""
    print(f"\n{'=' * 60}")
    print("Failure Modes Prevented")
    print("=" * 60)
    print("""
Failure mode 1 — Silent livelock (drafter/reviewer reject loop):
  Without Tangle, the drafter and reviewer would exchange the same
  reject/revise messages indefinitely. The workflow appears "running"
  but produces no useful output. In production this burns LLM tokens
  and blocks the customer from getting a response. Tangle detected
  the repeated 2-message pattern after 3 cycles and a tiebreaker
  prompt was injected, breaking the loop.

Failure mode 2 — Silent deadlock (4-agent circular wait):
  Without Tangle, all four agents would hang forever — each waiting
  on the next in a circle. The workflow shows no error, no timeout,
  no log. The customer ticket sits unresolved. Tangle detected the
  cycle the instant the closing edge was added (incremental DFS, not
  polling) and flagged it as CRITICAL severity.

In both cases, the failure is silent. Standard logging and health
checks would not surface these conditions without dedicated
coordination-failure detection like Tangle provides.
""")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60)
    print("Tangle — Customer Support Escalation Example")
    print("=" * 60)
    print()
    print("Architecture:")
    print()
    print("  triage -> researcher -> drafter <-> reviewer (reject/revise loop)")
    print("                ^                        |")
    print("                └────────────────────────┘  (circular wait)")

    with monitor:
        simulate_livelock("wf-support-ticket-42")
        simulate_deadlock("wf-support-ticket-99")
        show_recovery()

    print_failure_modes()
    print("=" * 60)
    print("Monitor stopped. Example complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
