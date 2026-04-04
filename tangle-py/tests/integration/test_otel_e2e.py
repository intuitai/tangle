"""OTel E2E integration test: OTLP spans via gRPC → Tangle detects deadlock."""
import pytest
import time


@pytest.mark.integration
class TestOTelE2E:
    def test_otel_spans_to_deadlock_detection(self):
        """Send OTLP spans via gRPC → Tangle detects deadlock."""
        try:
            import grpc
            from opentelemetry.proto.collector.trace.v1 import (
                trace_service_pb2,
                trace_service_pb2_grpc,
            )
            from opentelemetry.proto.trace.v1 import trace_pb2
            from opentelemetry.proto.common.v1 import common_pb2
        except ImportError:
            pytest.skip("gRPC/OTel dependencies not available")

        try:
            from tangle.integrations.otel import OTelCollector
        except ImportError:
            pytest.skip("OTelCollector not yet implemented")

        from tangle import TangleMonitor, TangleConfig

        port = 14317  # Use non-standard port for testing
        monitor = TangleMonitor(
            config=TangleConfig(
                otel_enabled=True,
                otel_port=port,
                cycle_check_interval=999,
            )
        )

        collector = OTelCollector(monitor, port=port)
        collector.start()

        try:
            # Give server time to start
            time.sleep(0.5)

            # Create gRPC channel and stub
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = trace_service_pb2_grpc.TraceServiceStub(channel)

            def make_span(agent_id, workflow_id, event_type, target="", resource=""):
                attrs = [
                    common_pb2.KeyValue(
                        key="tangle.agent.id",
                        value=common_pb2.AnyValue(string_value=agent_id),
                    ),
                    common_pb2.KeyValue(
                        key="tangle.workflow.id",
                        value=common_pb2.AnyValue(string_value=workflow_id),
                    ),
                    common_pb2.KeyValue(
                        key="tangle.event.type",
                        value=common_pb2.AnyValue(string_value=event_type),
                    ),
                ]
                if target:
                    attrs.append(
                        common_pb2.KeyValue(
                            key="tangle.target.agent",
                            value=common_pb2.AnyValue(string_value=target),
                        )
                    )
                if resource:
                    attrs.append(
                        common_pb2.KeyValue(
                            key="tangle.resource",
                            value=common_pb2.AnyValue(string_value=resource),
                        )
                    )
                return trace_pb2.Span(
                    name=f"tangle.{event_type}",
                    start_time_unix_nano=int(time.time() * 1e9),
                    attributes=attrs,
                )

            # Send spans: register A, register B, A waits for B, B waits for A (deadlock)
            spans = [
                make_span("A", "wf-otel", "register"),
                make_span("B", "wf-otel", "register"),
                make_span("A", "wf-otel", "wait_for", target="B", resource="data"),
                make_span("B", "wf-otel", "wait_for", target="A", resource="result"),
            ]

            request = trace_service_pb2.ExportTraceServiceRequest(
                resource_spans=[
                    trace_pb2.ResourceSpans(
                        scope_spans=[trace_pb2.ScopeSpans(spans=spans)]
                    )
                ]
            )
            stub.Export(request)

            # Verify deadlock detected
            time.sleep(0.2)
            detections = monitor.active_detections()
            assert len(detections) >= 1
            assert detections[0].type.value == "deadlock"

            channel.close()
        finally:
            collector.stop()
