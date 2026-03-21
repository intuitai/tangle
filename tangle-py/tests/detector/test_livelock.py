# tests/detector/test_livelock.py

from __future__ import annotations

from tangle.detector.livelock import LivelockDetector, RingBuffer

# ===========================================================================
# RingBuffer tests
# ===========================================================================


class TestRingBuffer:

    def test_ring_buffer_append(self) -> None:
        """Items are stored in insertion order before capacity is reached."""
        buf = RingBuffer(capacity=5)
        for i in range(5):
            buf.append(bytes([i]))
        assert len(buf) == 5
        result = buf.last_n(5)
        assert result == [bytes([i]) for i in range(5)]

    def test_ring_buffer_wrap(self) -> None:
        """After capacity is reached, oldest items are overwritten."""
        buf = RingBuffer(capacity=3)
        for i in range(5):
            buf.append(bytes([i]))
        assert len(buf) == 3
        # Should contain [2, 3, 4] (the last 3)
        result = buf.last_n(3)
        assert result == [bytes([2]), bytes([3]), bytes([4])]

    def test_ring_buffer_last_n(self) -> None:
        """last_n returns the correct most-recent slice."""
        buf = RingBuffer(capacity=10)
        for i in range(7):
            buf.append(bytes([i]))
        # Ask for last 3 -> [4, 5, 6]
        result = buf.last_n(3)
        assert result == [bytes([4]), bytes([5]), bytes([6])]

    def test_ring_buffer_last_n_after_wrap(self) -> None:
        """last_n works correctly after the buffer has wrapped."""
        buf = RingBuffer(capacity=4)
        for i in range(10):
            buf.append(bytes([i]))
        # Buffer contains [6, 7, 8, 9]
        result = buf.last_n(3)
        assert result == [bytes([7]), bytes([8]), bytes([9])]

    def test_ring_buffer_empty(self) -> None:
        """last_n on an empty buffer returns an empty list."""
        buf = RingBuffer(capacity=10)
        assert buf.last_n(5) == []
        assert buf.last_n(0) == []
        assert len(buf) == 0


# ===========================================================================
# LivelockDetector tests
# ===========================================================================


class TestLivelockSingleDirection:

    def test_livelock_exact_match_single_direction(self) -> None:
        """Same message A->B repeated min_repeats times is detected as livelock."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=1)
        result = None
        # Send the same message 9 times (3 repeats of a pattern of length 1)
        for _ in range(9):
            result = det.on_message("A", "B", b"same-msg", "wf-1")
        assert result is not None
        assert result.workflow_id == "wf-1"
        assert result.repeat_count >= 3


class TestLivelockPingPong:

    def test_livelock_pingpong_detected(self) -> None:
        """A->B 'req' + B->A 'rej' repeated 3+ times detected via conversation buffer."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=2)
        result = None
        # Send ping-pong pattern enough times
        for _ in range(10):
            result = det.on_message("A", "B", b"request", "wf-1")
            if result is not None:
                break
            result = det.on_message("B", "A", b"reject", "wf-1")
            if result is not None:
                break

        assert result is not None
        assert result.pattern_length >= 1
        assert result.repeat_count >= 3


class TestLivelockThreshold:

    def test_livelock_below_threshold(self) -> None:
        """Only 2 repetitions with min_repeats=3 is not detected."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=1)
        result = None
        # Send only 2 copies of the same message -- not enough for 3 repeats
        for _ in range(2):
            result = det.on_message("A", "B", b"msg", "wf-1")
        assert result is None


class TestLivelockPatternLengths:

    def test_livelock_pattern_length_2(self) -> None:
        """A 2-message pattern repeating 3+ times is detected."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=2)
        result = None
        for i in range(15):
            msg = b"alpha" if i % 2 == 0 else b"beta"
            result = det.on_message("A", "B", msg, "wf-1")
            if result is not None:
                break
        assert result is not None
        assert result.repeat_count >= 3

    def test_livelock_pattern_length_5(self) -> None:
        """A 5-message pattern repeating 3+ times is detected."""
        det = LivelockDetector(window=100, min_repeats=3, min_pattern=2)
        messages = [b"m0", b"m1", b"m2", b"m3", b"m4"]
        result = None
        # 5 messages * 4 repeats = 20 messages -> should get 3+ repeats
        for _cycle in range(4):
            for msg in messages:
                result = det.on_message("A", "B", msg, "wf-1")
                if result is not None:
                    break
            if result is not None:
                break
        assert result is not None
        assert result.repeat_count >= 3


class TestLivelockNoPattern:

    def test_livelock_no_pattern(self) -> None:
        """Random distinct messages produce no pattern detection."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=2)
        result = None
        for i in range(30):
            msg = f"unique-message-{i}".encode()
            result = det.on_message("A", "B", msg, "wf-1")
        assert result is None


class TestLivelockProgress:

    def test_livelock_progress_resets(self) -> None:
        """Reporting progress resets buffers, preventing false positive detection."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=1)

        # Build up a repeating pattern
        for _ in range(5):
            det.on_message("A", "B", b"repeat", "wf-1")

        # Report progress -> resets buffers
        det.report_progress("wf-1")

        # Now send only 2 more copies -- not enough for min_repeats=3
        result = None
        for _ in range(2):
            result = det.on_message("A", "B", b"repeat", "wf-1")

        assert result is None


class TestLivelockMultiPair:

    def test_livelock_multi_pair(self) -> None:
        """A-B livelock detection doesn't affect C-D pair."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=1)

        # Trigger livelock on A-B
        for _ in range(9):
            det.on_message("A", "B", b"looping", "wf-1")

        # C-D has unique messages -- should not be flagged
        result = None
        for i in range(5):
            result = det.on_message("C", "D", f"unique-{i}".encode(), "wf-2")
        assert result is None


class TestLivelockWindowBoundary:

    def test_livelock_window_boundary(self) -> None:
        """Pattern spanning the window boundary edge: messages outside the window are ignored."""
        # Small window of 6, min_repeats=3, min_pattern=1
        det = LivelockDetector(window=6, min_repeats=3, min_pattern=1)

        # Send 4 unique messages to push the window
        for i in range(4):
            det.on_message("A", "B", f"noise-{i}".encode(), "wf-1")

        # Now send the repeating message only 3 times -- just enough
        result = None
        for _ in range(3):
            result = det.on_message("A", "B", b"loop", "wf-1")

        assert result is not None
        assert result.repeat_count >= 3


class TestLivelockConversationVsPair:

    def test_livelock_conversation_vs_pair(self) -> None:
        """Conversation buffer catches ping-pong that pair buffer misses.

        Per-pair buffer sees A->B messages and B->A messages separately.
        The conversation buffer sees the interleaved pattern as a whole.
        """
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=2)
        result = None

        # Ping-pong: A->B "ping", B->A "pong" -- pair buffer has only one
        # direction per key, conversation buffer has both interleaved.
        for _ in range(10):
            result = det.on_message("A", "B", b"ping", "wf-1")
            if result is not None:
                break
            result = det.on_message("B", "A", b"pong", "wf-1")
            if result is not None:
                break

        assert result is not None
        assert result.pattern_length >= 1
        assert result.repeat_count >= 3


class TestLivelockClearWorkflow:

    def test_livelock_clear_workflow(self) -> None:
        """clear_workflow removes all buffers for that workflow."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=1)

        # Build up data for wf-1
        for _ in range(5):
            det.on_message("A", "B", b"msg", "wf-1")

        det.clear_workflow("wf-1")

        # After clearing, sending just 2 messages should not trigger detection
        result = None
        for _ in range(2):
            result = det.on_message("A", "B", b"msg", "wf-1")

        assert result is None

        # Internal state should have been cleaned
        # Verify by checking that new buffers were created (implicit via no detection)

    def test_clear_workflow_unknown(self) -> None:
        """clear_workflow on unknown workflow is a no-op."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=1)
        det.clear_workflow("never-seen")  # Should not raise


class TestLivelockProgressUnknown:

    def test_report_progress_unknown_workflow(self) -> None:
        """report_progress on unknown workflow is a no-op."""
        det = LivelockDetector(window=50, min_repeats=3, min_pattern=1)
        det.report_progress("never-seen")  # Should not raise


class TestRingBufferEdgeCases:

    def test_last_n_greater_than_count(self) -> None:
        """last_n with n > number of items returns all items."""
        buf = RingBuffer(capacity=10)
        buf.append(b"a")
        buf.append(b"b")
        buf.append(b"c")
        result = buf.last_n(10)
        assert result == [b"a", b"b", b"c"]

    def test_len_correct_after_wrap(self) -> None:
        """__len__ returns capacity after buffer wraps."""
        buf = RingBuffer(capacity=3)
        for i in range(10):
            buf.append(bytes([i]))
        assert len(buf) == 3

    def test_clear_resets_completely(self) -> None:
        """clear() resets all internal state."""
        buf = RingBuffer(capacity=5)
        for i in range(7):
            buf.append(bytes([i]))
        buf.clear()
        assert len(buf) == 0
        assert buf.last_n(5) == []
