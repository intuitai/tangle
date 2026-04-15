"""
LangGraph Deadlock Detection Example
=====================================
Demonstrates how Tangle detects deadlocks in a multi-agent LangGraph workflow.

Scenario: A research pipeline with four agents — researcher, writer, reviewer,
and editor — where the review/edit cycle creates a circular wait dependency:

    researcher -> writer -> reviewer -> editor -> researcher  (dependency loop)

When each agent is waiting on the next and the last agent waits back on the
first, no agent can make progress: a deadlock.

Run with:
    uv run python examples/langgraph_deadlock_detection.py
"""

from __future__ import annotations

import threading
import time
from typing import TypedDict

from langgraph.graph import END, StateGraph

from tangle import TangleConfig, TangleMonitor
from tangle.integrations.langgraph import tangle_conditional_edge, tangle_node
from tangle.types import Detection, DetectionType


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

class WorkflowState(TypedDict):
    tangle_workflow_id: str   # Required by Tangle to correlate events
    research: str
    draft: str
    review_notes: str
    edited_draft: str
    iteration: int
    status: str


# ---------------------------------------------------------------------------
# Detection callback — called when a deadlock or livelock is found
# ---------------------------------------------------------------------------

def on_detection(detection: Detection) -> None:
    if detection.type == DetectionType.DEADLOCK and detection.cycle:
        cycle = detection.cycle
        agents = " -> ".join(cycle.agents)
        print(f"\n[TANGLE] DEADLOCK DETECTED!")
        print(f"  Cycle:    {agents}")
        print(f"  Workflow: {cycle.workflow_id}")
        print(f"  Agents in deadlock: {len(cycle.agents)}")
    elif detection.type == DetectionType.LIVELOCK and detection.livelock:
        livelock = detection.livelock
        print(f"\n[TANGLE] LIVELOCK DETECTED!")
        print(f"  Agents:       {livelock.agents}")
        print(f"  Repeat count: {livelock.repeat_count}")


# ---------------------------------------------------------------------------
# Cancel callback — called by the CancelResolver to break the deadlock
# ---------------------------------------------------------------------------

def cancel_agent(agent_id: str, workflow_id: str) -> None:
    print(f"[TANGLE] Cancelling agent '{agent_id}' in workflow '{workflow_id}' to resolve deadlock")


# ---------------------------------------------------------------------------
# Build the Tangle monitor
# ---------------------------------------------------------------------------

config = TangleConfig(
    # How often the background thread does a full Kahn's-algorithm scan (seconds)
    cycle_check_interval=2.0,
    # Maximum cycle length to search for
    max_cycle_length=10,
    # Resolution strategy: cancel the youngest agent in the cycle
    resolution="cancel_youngest",
    # Livelock settings (not the focus here, but good to configure explicitly)
    livelock_min_repeats=4,
    livelock_window=30,
)

monitor = TangleMonitor(
    config=config,
    on_detection=on_detection,
    cancel_fn=cancel_agent,
)


# ---------------------------------------------------------------------------
# LangGraph nodes — each decorated with @tangle_node to auto-emit
# REGISTER and SEND events when the node executes
# ---------------------------------------------------------------------------

@tangle_node(monitor, agent_id="researcher")
def researcher_node(state: WorkflowState) -> dict:
    """Research agent: gathers information on the topic."""
    print("[researcher] Gathering research...")
    time.sleep(0.05)
    return {"research": "Findings: multi-agent coordination patterns in LLMs."}


@tangle_node(monitor, agent_id="writer")
def writer_node(state: WorkflowState) -> dict:
    """Writer agent: drafts content based on research."""
    print("[writer]     Drafting content...")
    time.sleep(0.05)
    return {"draft": "Draft: LLM agents coordinate via message passing..."}


@tangle_node(monitor, agent_id="reviewer")
def reviewer_node(state: WorkflowState) -> dict:
    """Reviewer agent: checks the draft for quality."""
    print("[reviewer]   Reviewing draft...")
    time.sleep(0.05)
    iteration = state.get("iteration", 0) + 1
    return {
        "review_notes": "Needs more examples and clearer structure.",
        "iteration": iteration,
    }


@tangle_node(monitor, agent_id="editor")
def editor_node(state: WorkflowState) -> dict:
    """Editor agent: applies reviewer feedback to the draft."""
    print("[editor]     Editing draft based on review notes...")
    time.sleep(0.05)
    return {"edited_draft": "Edited: " + state.get("draft", "")}


# ---------------------------------------------------------------------------
# Conditional edge functions — decorated with @tangle_conditional_edge to
# auto-emit WAIT_FOR events showing which agent the current agent depends on
# ---------------------------------------------------------------------------

@tangle_conditional_edge(monitor, from_agent="reviewer")
def reviewer_route(state: WorkflowState) -> str:
    """After reviewing, either send to editor or end if done."""
    if state.get("iteration", 0) >= 3:
        return END
    return "editor"


@tangle_conditional_edge(monitor, from_agent="editor")
def editor_route(state: WorkflowState) -> str:
    """After editing, loop back to researcher if more iterations needed."""
    if state.get("iteration", 0) >= 3:
        return END
    return "researcher"


# ---------------------------------------------------------------------------
# Build the LangGraph workflow
# ---------------------------------------------------------------------------

def build_workflow() -> StateGraph:
    graph = StateGraph(WorkflowState)

    graph.add_node("researcher", researcher_node)
    graph.add_node("writer", writer_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("editor", editor_node)

    # Linear pipeline: researcher -> writer -> reviewer
    graph.set_entry_point("researcher")
    graph.add_edge("researcher", "writer")
    graph.add_edge("writer", "reviewer")

    # Conditional back-edges create the review/edit loop:
    #   reviewer -> editor (or END)
    #   editor -> researcher (or END)
    graph.add_conditional_edges("reviewer", reviewer_route)
    graph.add_conditional_edges("editor", editor_route)

    return graph


# ---------------------------------------------------------------------------
# Simulate a deadlock via SDK hooks
#
# The @tangle_conditional_edge decorator emits WAIT_FOR + RELEASE immediately
# in sequence (it emits both in the same call), so it cannot create a *persistent*
# wait-for edge. To demonstrate a true deadlock — where agents are blocked waiting
# on each other simultaneously — we inject wait edges directly using the SDK.
#
# This represents the real scenario: agent A is mid-execution waiting for B's
# result, B is waiting for C, and C is waiting for A. None can proceed.
# ---------------------------------------------------------------------------

def simulate_deadlock(wf_id: str) -> None:
    """Inject wait-for edges that form a cycle, triggering deadlock detection."""
    print("\n--- Simulating deadlock: agents waiting on each other ---")

    # Register agents in this workflow
    monitor.register(workflow_id=wf_id, agent_id="researcher")
    monitor.register(workflow_id=wf_id, agent_id="writer")
    monitor.register(workflow_id=wf_id, agent_id="reviewer")
    monitor.register(workflow_id=wf_id, agent_id="editor")

    # Researcher is waiting for writer to finish its section
    monitor.wait_for(wf_id, from_agent="researcher", to_agent="writer", resource="section_data")
    print("[deadlock-sim] researcher is waiting for writer")

    # Writer is waiting for reviewer's approval
    monitor.wait_for(wf_id, from_agent="writer", to_agent="reviewer", resource="approval")
    print("[deadlock-sim] writer is waiting for reviewer")

    # Reviewer is waiting for editor's revised draft
    monitor.wait_for(wf_id, from_agent="reviewer", to_agent="editor", resource="revised_draft")
    print("[deadlock-sim] reviewer is waiting for editor")

    # Editor is waiting for researcher's updated findings — CLOSES THE CYCLE
    # This triggers incremental cycle detection immediately
    print("[deadlock-sim] editor is waiting for researcher  (cycle closes here!)")
    detection = monitor.wait_for(wf_id, from_agent="editor", to_agent="researcher", resource="updated_findings")

    # wait_for() returns None (it calls process_event but doesn't surface the detection directly)
    # The detection is fired via on_detection callback and stored in monitor._detections
    time.sleep(0.1)  # Allow the callback to print


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Tangle — LangGraph Deadlock Detection Example")
    print("=" * 60)

    # Use the context manager to start/stop the background scan thread
    with monitor:
        # --- Part 1: Run the normal LangGraph workflow (no deadlock) ---
        print("\n[1] Running normal LangGraph workflow (researcher -> writer -> reviewer -> editor)...")
        app = build_workflow().compile()
        final_state = app.invoke({
            "tangle_workflow_id": "wf-normal-run",
            "research": "",
            "draft": "",
            "review_notes": "",
            "edited_draft": "",
            "iteration": 0,
            "status": "running",
        })
        print(f"\n[1] Workflow completed after {final_state['iteration']} review iteration(s).")
        print(f"    Final draft: {final_state.get('edited_draft', '')[:60]}...")

        # Print monitor stats after the normal run
        stats = monitor.stats()
        print(f"\n[1] Monitor stats: {stats['events_processed']} events processed, "
              f"{stats['graph_nodes']} nodes tracked")

        # --- Part 2: Simulate a deadlock to demonstrate detection ---
        print("\n[2] Now simulating a deadlock scenario...")
        simulate_deadlock("wf-deadlock-demo")

        # Give the on_detection callback a moment to fire if not already
        time.sleep(0.2)

        # Report detections
        detections = monitor.active_detections()
        print(f"\n[2] Active detections: {len(detections)}")
        for d in detections:
            if d.cycle:
                print(f"    Type: DEADLOCK | Cycle length: {len(d.cycle.agents)} agents | "
                      f"Resolved: {d.cycle.resolved}")

    print("\n" + "=" * 60)
    print("Monitor stopped. Example complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
