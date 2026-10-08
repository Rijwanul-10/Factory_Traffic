"""
Phase 3 Tests: Emergency Preemption & Manual Control.

Validates:
- Emergency preemption overrides normal green safely
- Emergency preemption during manual hold cancels manual request and audits it
- Next emergency in same phase served without transition
- Conflicting emergency queued in FIFO
- Emergency cleared by VEHICLE_CLEARED or stale timeout
- Manual green request queue (FIFO, max 3, TTL expiry)
- Manual request same-phase deduplication
- RETURN_TO_AUTOMATIC command
"""

import pytest

from .conftest import (
    make_initialized_engine,
    make_arrival_event,
    make_clearance_event,
    run_ticks_for,
)
from backend.domain.types import (
    Direction,
    Phase,
    JunctionMode,
    VehicleType,
    CommandType,
    CommandStatus,
    AuditEventType,
    TransitionStep,
    SignalColor,
)


class TestEmergencyAndManual:

    def test_emergency_preemption_cancels_active_manual_hold(self):
        engine, clock = make_initialized_engine()

        # Administrator requests WEST manual green
        cmd_id, status, _ = engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)
        assert engine.mode == JunctionMode.MANUAL

        # Advance to manual green
        run_ticks_for(engine, clock, 10)
        assert engine.active_manual is not None

        # Emergency arrives from NORTH (conflicting direction)
        emg = make_arrival_event(
            vehicle_id="AMBULANCE_1",
            direction=Direction.NORTH,
            vehicle_type=VehicleType.EMERGENCY,
            event_id="emg_evt_1",
            server_timestamp=clock.now(),
        )
        engine.process_sensor_event(emg)

        # Mode must immediately switch to EMERGENCY
        assert engine.mode == JunctionMode.EMERGENCY
        # Active manual must be cancelled
        assert engine.active_manual is None

        # Audit should record MANUAL_CANCELLED
        audits = engine.drain_audit_buffer()
        assert any(a.event_type == AuditEventType.MANUAL_CANCELLED for a in audits)

    def test_same_phase_emergency_served_without_transition(self):
        engine, clock = make_initialized_engine()
        # Engine starts with NORTH_SOUTH GREEN

        # Emergency arrives from SOUTH (same phase as NORTH_SOUTH)
        emg = make_arrival_event(
            vehicle_id="FIRE_TRUCK_1",
            direction=Direction.SOUTH,
            vehicle_type=VehicleType.EMERGENCY,
            event_id="emg_south",
            server_timestamp=clock.now(),
        )
        engine.process_sensor_event(emg)

        assert engine.mode == JunctionMode.EMERGENCY
        # Must NOT trigger a transition because NS is already GREEN!
        assert not engine.in_transition
        assert engine.desired_signals.get(Direction.SOUTH) == SignalColor.GREEN

    def test_conflicting_emergencies_served_in_fifo(self):
        engine, clock = make_initialized_engine()

        # EMG 1 from EAST (triggers transition from NS to EW)
        emg1 = make_arrival_event("EMG_EAST", Direction.EAST, VehicleType.EMERGENCY,
                                  event_id="e_e", server_timestamp=clock.now())
        engine.process_sensor_event(emg1)
        assert engine.active_emergency.vehicle_id == "EMG_EAST"

        # EMG 2 from NORTH (conflicting)
        emg2 = make_arrival_event("EMG_NORTH", Direction.NORTH, VehicleType.EMERGENCY,
                                  event_id="e_n", server_timestamp=clock.now())
        engine.process_sensor_event(emg2)

        # EMG 2 sits in emergency_queue behind EMG 1
        assert len(engine.emergency_queue) == 2
        assert engine.emergency_queue[0].vehicle_id == "EMG_EAST"
        assert engine.emergency_queue[1].vehicle_id == "EMG_NORTH"

        # Clear EMG 1
        run_ticks_for(engine, clock, 10)  # Transition complete
        clr1 = make_clearance_event("EMG_EAST", Direction.EAST, event_id="c_e")
        engine.process_sensor_event(clr1)

        # EMG 2 should now become active emergency
        assert engine.active_emergency is not None
        assert engine.active_emergency.vehicle_id == "EMG_NORTH"

    def test_manual_queue_ttl_expiry(self):
        engine, clock = make_initialized_engine()

        # Start manual on WEST
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)

        # Queue a second manual request on NORTH with a short TTL
        cmd2_id, status, _ = engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.NORTH)
        assert len(engine.manual_queue) == 1
        engine.manual_queue[0].ttl = 5.0  # 5 nominal seconds TTL

        # Advance past TTL (6 nominal seconds)
        clock.advance(engine.config.real_seconds(6.0))
        # Complete the transition (7 nominal) + active manual hold (10 nominal)
        run_ticks_for(engine, clock, 25.0)

        # Expired manual request was dropped, returning to automatic
        assert engine.mode == JunctionMode.AUTOMATIC
        assert len(engine.manual_queue) == 0

    def test_manual_same_phase_deduplication(self):
        engine, clock = make_initialized_engine()

        # Request EAST
        _, status1, _ = engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.EAST)
        assert status1 in (CommandStatus.QUEUED, CommandStatus.ACTIVE)

        # Request WEST (same phase EAST_WEST) -> Rejected
        _, status2, _ = engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)
        assert status2 == CommandStatus.REJECTED

    def test_return_to_automatic_clears_manual_queue(self):
        engine, clock = make_initialized_engine()

        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.EAST)
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.NORTH)

        assert engine.mode == JunctionMode.MANUAL
        assert len(engine.manual_queue) > 0

        # Return to automatic
        cmd_id, status, _ = engine.process_manual_request(CommandType.RETURN_TO_AUTOMATIC)
        assert status == CommandStatus.COMPLETED
        assert engine.mode == JunctionMode.AUTOMATIC
        assert len(engine.manual_queue) == 0
        assert engine.active_manual is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
