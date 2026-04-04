"""Benchmarks for cycle detection and related operations."""
from tangle.config import TangleConfig
from tangle.detector.cycle import CycleDetector
from tangle.detector.livelock import LivelockDetector, RingBuffer
from tangle.graph.wfg import WaitForGraph
from tangle.monitor import TangleMonitor
from tangle.types import Edge, Event, EventType


def _build_chain(n: int, max_depth: int | None = None) -> tuple[WaitForGraph, CycleDetector, Edge]:
    """Build a chain of n agents: A0->A1->...->A(n-1). Returns (graph, detector, closing_edge)."""
    graph = WaitForGraph()
    detector = CycleDetector(graph, max_depth=max_depth if max_depth is not None else n + 1)
    for i in range(n):
        graph.register_agent(f"A{i}", "wf", float(i))
    # Add chain edges A0->A1, A1->A2, ..., A(n-2)->A(n-1)
    for i in range(n - 1):
        edge = Edge(
            from_agent=f"A{i}",
            to_agent=f"A{i+1}",
            resource="r",
            created_at=float(i),
            workflow_id="wf",
        )
        graph.add_edge(edge)
    # The closing edge completes the cycle: A(n-1)->A0
    closing = Edge(
        from_agent=f"A{n-1}",
        to_agent="A0",
        resource="r",
        created_at=float(n),
        workflow_id="wf",
    )
    return graph, detector, closing


def test_bench_incremental_10_agents(benchmark):
    graph, detector, closing = _build_chain(10)
    graph.add_edge(closing)
    benchmark(detector.on_edge_added, closing)


def test_bench_incremental_100_agents(benchmark):
    graph, detector, closing = _build_chain(100)
    graph.add_edge(closing)
    benchmark(detector.on_edge_added, closing)


def test_bench_incremental_1000_agents(benchmark):
    # max_depth=20 matches the default config; DFS bails early on deep chains.
    # This benchmarks incremental detection overhead on a large graph.
    graph, detector, closing = _build_chain(1000, max_depth=20)
    graph.add_edge(closing)
    benchmark(detector.on_edge_added, closing)


def test_bench_kahns_100_agents(benchmark):
    graph, detector, _ = _build_chain(100)
    # Add closing edge to create actual cycle
    closing = Edge(
        from_agent="A99",
        to_agent="A0",
        resource="r",
        created_at=100.0,
        workflow_id="wf",
    )
    graph.add_edge(closing)
    benchmark(detector.full_scan)


def test_bench_kahns_1000_agents(benchmark):
    import sys

    graph, detector, _ = _build_chain(1000, max_depth=20)
    closing = Edge(
        from_agent="A999",
        to_agent="A0",
        resource="r",
        created_at=1000.0,
        workflow_id="wf",
    )
    graph.add_edge(closing)
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(5000)
    try:
        benchmark(detector.full_scan)
    finally:
        sys.setrecursionlimit(old_limit)


def test_bench_livelock_window_50(benchmark):
    detector = LivelockDetector(window=50, min_repeats=3, min_pattern=2, ring_size=200)
    # Pre-fill with some messages
    for i in range(40):
        detector.on_message("A", "B", f"msg-{i % 5}".encode(), "wf")
    benchmark(detector.on_message, "A", "B", b"repeated-msg", "wf")


def test_bench_livelock_window_200(benchmark):
    detector = LivelockDetector(window=200, min_repeats=3, min_pattern=2, ring_size=400)
    for i in range(180):
        detector.on_message("A", "B", f"msg-{i % 5}".encode(), "wf")
    benchmark(detector.on_message, "A", "B", b"repeated-msg", "wf")


def test_bench_process_event(benchmark):
    monitor = TangleMonitor(config=TangleConfig(cycle_check_interval=999))
    monitor.register(workflow_id="wf-bench", agent_id="A")
    monitor.register(workflow_id="wf-bench", agent_id="B")
    event = Event(
        type=EventType.SEND,
        timestamp=1.0,
        workflow_id="wf-bench",
        from_agent="A",
        to_agent="B",
        message_body=b"benchmark-payload",
    )
    benchmark(monitor.process_event, event)


def test_bench_ring_buffer_append(benchmark):
    buf = RingBuffer(capacity=200)
    benchmark(buf.append, b"0123456789abcdef")
