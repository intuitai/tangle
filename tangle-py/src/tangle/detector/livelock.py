# src/tangle/detector/livelock.py

import threading

import xxhash

from tangle.types import AgentID, LivelockPattern


class RingBuffer:
    """Fixed-capacity circular buffer for message digests."""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._buffer: list[bytes] = []
        self._start = 0
        self._count = 0
        self._lock = threading.Lock()

    def append(self, digest: bytes) -> None:
        with self._lock:
            if len(self._buffer) < self._capacity:
                self._buffer.append(digest)
                self._count += 1
            else:
                idx = (self._start + self._count) % self._capacity
                self._buffer[idx] = digest
                self._start = (self._start + 1) % self._capacity

    def last_n(self, n: int) -> list[bytes]:
        with self._lock:
            count = min(n, len(self._buffer))
            if count == 0:
                return []
            result: list[bytes] = []
            total = len(self._buffer)
            for i in range(count):
                idx = (
                    (self._start + total - count + i) % self._capacity
                    if total == self._capacity
                    else total - count + i
                )
                result.append(self._buffer[idx])
            return result

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._start = 0
            self._count = 0


class LivelockDetector:
    """Detects repetitive message patterns between agent pairs and within workflows."""

    def __init__(
        self,
        window: int = 50,
        min_repeats: int = 3,
        min_pattern: int = 2,
        ring_size: int = 200,
    ) -> None:
        self._window = window
        self._min_repeats = min_repeats
        self._min_pattern = min_pattern
        self._ring_size = ring_size
        # Keyed by (workflow_id, from_agent, to_agent) for isolation across workflows
        self._pair_buffers: dict[tuple[str, AgentID, AgentID], RingBuffer] = {}
        self._conversation_buffers: dict[str, RingBuffer] = {}
        self._pair_agents: dict[str, set[tuple[AgentID, AgentID]]] = {}  # workflow -> set of pairs

    def on_message(
        self,
        from_agent: AgentID,
        to_agent: AgentID,
        body: bytes,
        workflow_id: str,
    ) -> LivelockPattern | None:
        content_hash = xxhash.xxh128(body).digest()

        # Conversation hash captures WHO sent WHAT TO WHOM
        conversation_hash = xxhash.xxh128(
            from_agent.encode() + to_agent.encode() + content_hash
        ).digest()

        # Per-pair buffer — keyed by (workflow_id, from_agent, to_agent)
        pair_key = (workflow_id, from_agent, to_agent)
        if pair_key not in self._pair_buffers:
            self._pair_buffers[pair_key] = RingBuffer(self._ring_size)
        self._pair_buffers[pair_key].append(content_hash)

        # Track pairs per workflow (using plain (from, to) for agent list extraction)
        agent_pair = (from_agent, to_agent)
        if workflow_id not in self._pair_agents:
            self._pair_agents[workflow_id] = set()
        self._pair_agents[workflow_id].add(agent_pair)

        # Per-workflow conversation buffer
        if workflow_id not in self._conversation_buffers:
            self._conversation_buffers[workflow_id] = RingBuffer(self._ring_size)
        self._conversation_buffers[workflow_id].append(conversation_hash)

        # Check per-pair buffer
        result = self._check_pattern(
            self._pair_buffers[pair_key], [from_agent, to_agent], workflow_id
        )
        if result:
            return result

        # Check conversation buffer
        agents = self._get_workflow_agents(workflow_id)
        result = self._check_pattern(self._conversation_buffers[workflow_id], agents, workflow_id)
        return result

    def _get_workflow_agents(self, workflow_id: str) -> list[AgentID]:
        pairs = self._pair_agents.get(workflow_id, set())
        agents: set[AgentID] = set()
        for f, t in pairs:
            agents.add(f)
            agents.add(t)
        return sorted(agents)

    def _check_pattern(
        self,
        buffer: RingBuffer,
        agents: list[AgentID],
        workflow_id: str,
    ) -> LivelockPattern | None:
        digests = buffer.last_n(self._window)
        if len(digests) < self._min_pattern * self._min_repeats:
            return None

        for pattern_len in range(self._min_pattern, len(digests) // self._min_repeats + 1):
            # Extract candidate pattern (last pattern_len digests)
            candidate = digests[-pattern_len:]
            # Count consecutive repeats scanning backward
            repeats = 1
            pos = len(digests) - pattern_len
            while pos >= pattern_len:
                segment = digests[pos - pattern_len : pos]
                if segment == candidate:
                    repeats += 1
                    pos -= pattern_len
                else:
                    break

            if repeats >= self._min_repeats:
                return LivelockPattern(
                    agents=agents,
                    pattern_length=pattern_len,
                    repeat_count=repeats,
                    workflow_id=workflow_id,
                )

        return None

    def report_progress(self, workflow_id: str) -> None:
        """Reset buffers for this workflow to suppress false positive after progress."""
        if workflow_id in self._conversation_buffers:
            self._conversation_buffers[workflow_id].clear()
        pairs = self._pair_agents.get(workflow_id, set())
        for agent_pair in pairs:
            pair_key = (workflow_id, agent_pair[0], agent_pair[1])
            if pair_key in self._pair_buffers:
                self._pair_buffers[pair_key].clear()

    def clear_workflow(self, workflow_id: str) -> None:
        """Remove all buffers for a workflow."""
        self._conversation_buffers.pop(workflow_id, None)
        pairs = self._pair_agents.pop(workflow_id, set())
        for agent_pair in pairs:
            pair_key = (workflow_id, agent_pair[0], agent_pair[1])
            self._pair_buffers.pop(pair_key, None)
