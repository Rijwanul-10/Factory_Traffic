"""
Phase 2 Tests: Event Handling & Queues.

Validates:
- Event idempotency via event_id
- Future timestamp rejection (> 60s)
- Delayed sensor event flagging (> 120s)
- Out-of-order sequence detection
- Queue derived consistency (no negative count)
- Orphan CLEARED handling
- Tombstone handling (ARRIVED after CLEARED)
"""

import pytest

from .conftest import (
    make_engine,
    make_initialized_engine,
    make_arrival_event,
    make_clearance_event,
)
from backend.domain.types import (
    Direction,
    EventType,
    EventProcessingResult,
    AuditEventType,
    VehicleType,
)


class TestEventHandling:

    def test_future_timestamp_rejected(self):
        engine, clock = make_initialized_engine()
        future_ts = clock.now() + 100.0  # > 60s in future

        event = make_arrival_event(
            vehicle_id="V_FUTURE",
            direction=Direction.NORTH,
            event_id="evt_future",
            timestamp=future_ts,
            server_timestamp=clock.now(),
        )

        result, _ = engine.process_sensor_event(event)
        assert result == EventProcessingResult.FUTURE_TIMESTAMP
        assert engine.queue_count(Direction.NORTH) == 0

        # Verify audit entry
        audits = engine.drain_audit_buffer()
        assert any(a.event_type == AuditEventType.FUTURE_TIMESTAMP_REJECTED for a in audits)

    def test_delayed_sensor_event_flagged_and_accepted(self):
        engine, clock = make_initialized_engine()
        delayed_ts = clock.now() - 200.0  # > 120s past

        event = make_arrival_event(
            vehicle_id="V_DELAYED",
            direction=Direction.NORTH,
            event_id="evt_delayed",
            timestamp=delayed_ts,
            server_timestamp=clock.now(),
        )

        result, _ = engine.process_sensor_event(event)
        # Delayed events are flagged but still accepted if valid
        assert result == EventProcessingResult.ACCEPTED
        assert engine.queue_count(Direction.NORTH) == 1

        audits = engine.drain_audit_buffer()
        assert any(a.event_type == AuditEventType.DELAYED_EVENT for a in audits)

    def test_out_of_order_sequence_detected_and_audited(self):
        engine, clock = make_initialized_engine()

        # First event with sequence 100
        event1 = make_arrival_event(
            vehicle_id="V_100",
            direction=Direction.NORTH,
            sequence_no=100,
            event_id="evt_100",
            server_timestamp=clock.now(),
        )
        engine.process_sensor_event(event1)

        # Second event with sequence 50 (out of order!)
        event2 = make_arrival_event(
            vehicle_id="V_50",
            direction=Direction.NORTH,
            sequence_no=50,
            event_id="evt_50",
            server_timestamp=clock.now(),
        )
        result, _ = engine.process_sensor_event(event2)
        assert result == EventProcessingResult.ACCEPTED

        audits = engine.drain_audit_buffer()
        assert any(a.event_type == AuditEventType.OUT_OF_ORDER_EVENT for a in audits)

    def test_duplicate_event_id_idempotent_no_state_change(self):
        engine, clock = make_initialized_engine()

        event = make_arrival_event(
            vehicle_id="V_DUP",
            direction=Direction.SOUTH,
            event_id="evt_dup_1",
            server_timestamp=clock.now(),
        )
        res1, _ = engine.process_sensor_event(event)
        assert res1 == EventProcessingResult.ACCEPTED
        assert engine.queue_count(Direction.SOUTH) == 1

        # Resubmit exact same event
        res2, _ = engine.process_sensor_event(event)
        assert res2 == EventProcessingResult.DUPLICATE
        assert engine.queue_count(Direction.SOUTH) == 1

    def test_orphan_cleared_rejected_and_audited(self):
        engine, clock = make_initialized_engine()

        clr = make_clearance_event(
            vehicle_id="UNKNOWN_VEHICLE",
            direction=Direction.EAST,
            event_id="clr_orphan",
        )
        res, _ = engine.process_sensor_event(clr)
        assert res == EventProcessingResult.ORPHAN
        assert engine.queue_count(Direction.EAST) == 0

        audits = engine.drain_audit_buffer()
        assert any(a.event_type == AuditEventType.ORPHAN_CLEARED for a in audits)

    def test_tombstone_prevents_rearrival_after_cleared(self):
        engine, clock = make_initialized_engine()

        # Arrival
        arr = make_arrival_event("V_TOMB", Direction.WEST, event_id="arr_1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)
        assert engine.queue_count(Direction.WEST) == 1

        # Cleared
        clr = make_clearance_event("V_TOMB", Direction.WEST, event_id="clr_1")
        engine.process_sensor_event(clr)
        assert engine.queue_count(Direction.WEST) == 0

        # Late arrival event for the same vehicle
        late_arr = make_arrival_event("V_TOMB", Direction.WEST, event_id="arr_late", server_timestamp=clock.now())
        res, _ = engine.process_sensor_event(late_arr)
        assert res == EventProcessingResult.STALE
        assert engine.queue_count(Direction.WEST) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
