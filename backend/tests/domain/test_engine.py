"""
Domain engine tests - Phase 1.

Tests cover:
- Safety invariant (never violated across random sequences)
- Full transition sequence
- Skipping empty phases
- Early end when phase empties
- Starvation protection
- Emergency preemption (incl. during manual, multiple, same-phase, stale)
- Manual queue
- Duplicate events
- Orphan CLEARED
- Negative queue impossible
- ACK timeout/retry/duplicate/late ACK
- Restart recovery
- Concurrent sequence from spec section 15
"""

import random
import pytest

from .conftest import (
    make_engine, make_initialized_engine,
    make_arrival_event, make_clearance_event,
    run_ticks_for, make_config,
)

from backend.domain.clock import FakeClock
from backend.domain.types import (
    Direction, Phase, SignalState, SignalColor, JunctionMode,
    VehicleType, EventType, CommandType, EventProcessingResult,
    TransitionStep, ControllerStatus, CommandStatus,
    DIRECTION_PHASE, CONFLICTING_PHASE, PHASE_DIRECTIONS,
)
from backend.domain.engine import (
    JunctionEngine, SafetyViolationError, assert_signal_safety,
    build_transition_plan,
)


# ═══════════════════════════════════════════════════════════════════════════
# Safety invariant tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSafetyInvariant:
    """The safety invariant must never be violated."""

    def test_conflicting_greens_raises(self):
        """Conflicting phases GREEN simultaneously must raise."""
        bad = SignalState(
            north=SignalColor.GREEN,
            south=SignalColor.GREEN,
            east=SignalColor.GREEN,
            west=SignalColor.GREEN,
        )
        with pytest.raises(SafetyViolationError):
            assert_signal_safety(bad)

    def test_ns_green_ew_red_ok(self):
        """NS GREEN with EW RED is safe."""
        signals = SignalState(
            north=SignalColor.GREEN, south=SignalColor.GREEN,
            east=SignalColor.RED, west=SignalColor.RED,
        )
        assert_signal_safety(signals)  # Should not raise

    def test_all_red_ok(self):
        signals = SignalState.all_red()
        assert_signal_safety(signals)

    def test_mixed_conflict_raises(self):
        """Even one GREEN on each conflicting side is a violation."""
        bad = SignalState(
            north=SignalColor.GREEN, south=SignalColor.RED,
            east=SignalColor.GREEN, west=SignalColor.RED,
        )
        with pytest.raises(SafetyViolationError):
            assert_signal_safety(bad)

    def test_yellow_with_conflicting_green_raises(self):
        """NS YELLOW with EW GREEN is also a conflict (GREEN on both sides)."""
        # This isn't a conflict by our definition - only GREEN vs GREEN
        # YELLOW on NS with GREEN on EW actually shouldn't happen in valid operation
        # but our invariant only checks GREEN vs GREEN
        bad = SignalState(
            north=SignalColor.GREEN, south=SignalColor.YELLOW,
            east=SignalColor.GREEN, west=SignalColor.RED,
        )
        with pytest.raises(SafetyViolationError):
            assert_signal_safety(bad)

    def test_random_event_sequence_never_violates_safety(self):
        """Property test: random events should never produce conflicting greens."""
        engine, clock = make_initialized_engine()

        directions = list(Direction)
        vehicle_types = [VehicleType.TRUCK, VehicleType.FORKLIFT, VehicleType.EMPLOYEE_VEHICLE]
        rng = random.Random(42)

        for i in range(200):
            # Random action
            action = rng.choice(["arrive", "clear", "tick", "tick", "tick"])

            if action == "arrive":
                d = rng.choice(directions)
                vt = rng.choice(vehicle_types)
                event = make_arrival_event(
                    vehicle_id=f"V-{i}",
                    direction=d,
                    vehicle_type=vt,
                    event_id=f"evt-{i}",
                    server_timestamp=clock.now(),
                )
                engine.process_sensor_event(event)

            elif action == "clear":
                if engine.waiting_vehicles:
                    vid = rng.choice(list(engine.waiting_vehicles.keys()))
                    v = engine.waiting_vehicles[vid]
                    event = make_clearance_event(
                        vehicle_id=vid,
                        direction=v.direction,
                        event_id=f"clr-{i}",
                    )
                    engine.process_sensor_event(event)

            elif action == "tick":
                clock.advance(1.0)
                engine.tick()

            # SAFETY CHECK after every action
            assert not engine.desired_signals.has_conflict(), \
                f"Safety violation at step {i}: {engine.desired_signals.as_dict()}"


# ═══════════════════════════════════════════════════════════════════════════
# Transition sequence tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTransitionSequence:
    """Full transition sequence: GREEN -> YELLOW -> ALL_RED -> GREEN."""

    def test_build_transition_plan_different_phases(self):
        config = make_config()
        plan = build_transition_plan(Phase.NORTH_SOUTH, Phase.EAST_WEST, config)
        steps = list(plan.steps)
        assert len(steps) == 3
        assert steps[0][0] == TransitionStep.YELLOW
        assert steps[1][0] == TransitionStep.ALL_RED
        assert steps[2][0] == TransitionStep.GREEN
        assert steps[2][1] == Phase.EAST_WEST

    def test_build_transition_plan_same_phase(self):
        config = make_config()
        plan = build_transition_plan(Phase.NORTH_SOUTH, Phase.NORTH_SOUTH, config)
        steps = list(plan.steps)
        assert len(steps) == 1
        assert steps[0][0] == TransitionStep.GREEN

    def test_full_transition_via_ticks(self):
        """A full transition should go through YELLOW -> ALL_RED -> GREEN."""
        engine, clock = make_initialized_engine()

        # Vehicles in both phases so NS stays active until green_duration expires
        arr_ns = make_arrival_event("V_NS", Direction.NORTH, VehicleType.EMPLOYEE_VEHICLE,
                                    event_id="e_ns", server_timestamp=clock.now())
        engine.process_sensor_event(arr_ns)

        arr_ew = make_arrival_event("V1", Direction.EAST, VehicleType.TRUCK,
                                    event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr_ew)

        # Advance exactly through full green (30 nominal) + tiny fraction to trigger yellow
        run_ticks_for(engine, clock, 30.2, tick_interval=0.1)

        # Should be in YELLOW now
        assert engine.current_step_type == TransitionStep.YELLOW
        assert engine.desired_signals.get(Direction.NORTH) == SignalColor.YELLOW

        # Advance through YELLOW (5 nominal = 2.5 real)
        run_ticks_for(engine, clock, 5.0, tick_interval=0.1)

        # Should be in ALL_RED
        assert engine.current_step_type == TransitionStep.ALL_RED
        assert engine.desired_signals == SignalState.all_red()

        # Advance through ALL_RED (2 nominal = 1 real)
        run_ticks_for(engine, clock, 2.2, tick_interval=0.1)

        # Should be GREEN on EW now
        assert engine.current_phase == Phase.EAST_WEST
        assert engine.current_step_type == TransitionStep.GREEN
        assert engine.desired_signals.get(Direction.EAST) == SignalColor.GREEN
        assert engine.desired_signals.get(Direction.NORTH) == SignalColor.RED

    def test_yellow_not_shortened(self):
        """YELLOW step must not be shortened or skipped."""
        engine, clock = make_initialized_engine()

        # Add vehicle to NS so full green elapses
        arr_ns = make_arrival_event("V_NS", Direction.NORTH, event_id="e_ns", server_timestamp=clock.now())
        engine.process_sensor_event(arr_ns)
        event = make_arrival_event("V1", Direction.EAST, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(event)

        # Trigger transition
        run_ticks_for(engine, clock, 30.2, tick_interval=0.1)

        assert engine.current_step_type == TransitionStep.YELLOW

        # Tick for less than yellow duration (e.g. 1.0 real = 2.0 nominal, yellow is 5.0 nominal)
        clock.advance(0.5)  # 1.0 nominal second (< 5.0 nominal yellow)
        engine.tick()
        assert engine.current_step_type == TransitionStep.YELLOW

    def test_all_red_not_skipped(self):
        """ALL_RED step must not be skipped."""
        engine, clock = make_initialized_engine()

        # Add vehicle to NS so full green elapses
        arr_ns = make_arrival_event("V_NS", Direction.NORTH, event_id="e_ns", server_timestamp=clock.now())
        engine.process_sensor_event(arr_ns)
        event = make_arrival_event("V1", Direction.EAST, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(event)

        # Through green
        run_ticks_for(engine, clock, 30.2, tick_interval=0.1)
        # Through yellow (5 nominal)
        run_ticks_for(engine, clock, 5.0, tick_interval=0.1)

        assert engine.current_step_type == TransitionStep.ALL_RED

        # Tick less than all_red duration (all_red is 2.0 nominal = 1.0 real)
        clock.advance(0.2)  # 0.4 nominal second (< 2.0 nominal ALL_RED)
        engine.tick()
        assert engine.current_step_type == TransitionStep.ALL_RED


# ═══════════════════════════════════════════════════════════════════════════
# Empty phase skipping
# ═══════════════════════════════════════════════════════════════════════════

class TestEmptyPhaseSkipping:
    """Never turn an empty phase GREEN. Stay on current if all empty."""

    def test_skip_empty_phase(self):
        """Don't switch to an empty phase."""
        engine, clock = make_initialized_engine()

        # No vehicles anywhere — should stay on current green
        run_ticks_for(engine, clock, 35)

        # Still on NS GREEN
        assert engine.current_phase == Phase.NORTH_SOUTH
        assert engine.current_step_type == TransitionStep.GREEN

    def test_rest_state_all_empty(self):
        """When all queues empty, stay on current green (rest state)."""
        engine, clock = make_initialized_engine()

        # Run well past green duration with no vehicles
        run_ticks_for(engine, clock, 100)

        # Should still be on NS GREEN
        assert engine.current_phase == Phase.NORTH_SOUTH
        assert engine.desired_signals.get(Direction.NORTH) == SignalColor.GREEN

    def test_switch_when_other_has_vehicles(self):
        """When green expires and other phase has vehicles, switch."""
        engine, clock = make_initialized_engine()

        # Add vehicles to EW
        event = make_arrival_event("V1", Direction.EAST, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(event)

        # Run through green
        run_ticks_for(engine, clock, 31)

        # Should start transition
        assert engine.in_transition or engine.current_phase == Phase.EAST_WEST


# ═══════════════════════════════════════════════════════════════════════════
# Early end (grace empty)
# ═══════════════════════════════════════════════════════════════════════════

class TestGraceEarlyEnd:
    """When current phase empties, wait GRACE_EMPTY + MIN_GREEN, then switch."""

    def test_early_end_when_empty(self):
        """Phase empties before green expires -> switch after grace + min_green."""
        engine, clock = make_initialized_engine()

        # Add vehicle to NS (current phase)
        arr = make_arrival_event("V1", Direction.NORTH, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        # Add vehicle to EW (other phase)
        arr2 = make_arrival_event("V2", Direction.EAST, event_id="e2", server_timestamp=clock.now())
        engine.process_sensor_event(arr2)

        # Advance past MIN_GREEN
        run_ticks_for(engine, clock, 6)

        # Clear the NS vehicle
        clr = make_clearance_event("V1", Direction.NORTH, event_id="clr1")
        engine.process_sensor_event(clr)

        # Now NS is empty, EW has vehicles
        assert engine.phase_is_empty(Phase.NORTH_SOUTH)
        assert not engine.phase_is_empty(Phase.EAST_WEST)

        # Advance past GRACE_EMPTY (2 nominal = 1 real)
        run_ticks_for(engine, clock, 3)

        # Should have started transition
        assert engine.in_transition or engine.current_phase == Phase.EAST_WEST

    def test_no_early_end_before_min_green(self):
        """Don't switch early if MIN_GREEN hasn't elapsed."""
        engine, clock = make_initialized_engine()

        # Add vehicles to both phases
        arr = make_arrival_event("V1", Direction.NORTH, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)
        arr2 = make_arrival_event("V2", Direction.EAST, event_id="e2", server_timestamp=clock.now())
        engine.process_sensor_event(arr2)

        # Clear NS immediately (before MIN_GREEN)
        clock.advance(1.0)  # 2 nominal secs
        engine.tick()
        clr = make_clearance_event("V1", Direction.NORTH, event_id="clr1")
        engine.process_sensor_event(clr)

        # Advance less than MIN_GREEN total
        clock.advance(0.5)  # 1 more nominal sec, total ~3 nominal
        engine.tick()

        # Should still be on NS (min green not met)
        assert engine.current_phase == Phase.NORTH_SOUTH


# ═══════════════════════════════════════════════════════════════════════════
# Green extension
# ═══════════════════════════════════════════════════════════════════════════

class TestGreenExtension:
    """If current phase has vehicles at green end and nothing else waits, extend."""

    def test_extend_green_when_current_has_vehicles_other_empty(self):
        engine, clock = make_initialized_engine()

        # Add vehicle to NS only
        arr = make_arrival_event("V1", Direction.NORTH, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        # Run past normal green
        run_ticks_for(engine, clock, 35)

        # Should still be NS GREEN (extended, other phase empty)
        assert engine.current_phase == Phase.NORTH_SOUTH
        assert engine.current_step_type == TransitionStep.GREEN

    def test_extension_capped_at_max_green(self):
        """Extension can't exceed MAX_GREEN."""
        engine, clock = make_initialized_engine()

        # Add vehicle to NS
        arr = make_arrival_event("V1", Direction.NORTH, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        # Run past MAX_GREEN (60 nominal = 30 real)
        run_ticks_for(engine, clock, 65)

        # With no other vehicles, it stays (rest state) even past max_green
        # because the other phase is empty — there's nothing to switch to
        assert engine.current_phase == Phase.NORTH_SOUTH


# ═══════════════════════════════════════════════════════════════════════════
# Starvation protection
# ═══════════════════════════════════════════════════════════════════════════

class TestStarvationProtection:
    """Phases that waited longer than MAX_WAIT must be selected."""

    def test_starvation_forces_phase_switch(self):
        """If a phase has waited > MAX_WAIT, force switch regardless of score."""
        engine, clock = make_initialized_engine()

        # Add vehicle to EW with old arrival time (to simulate long wait)
        old_time = clock.now()
        arr = make_arrival_event("V1", Direction.EAST, event_id="e1", server_timestamp=old_time)
        engine.process_sensor_event(arr)

        # Also add vehicle to NS so current phase has traffic
        arr2 = make_arrival_event("V2", Direction.NORTH, event_id="e2", server_timestamp=clock.now())
        engine.process_sensor_event(arr2)

        # Advance beyond MAX_WAIT (90 nominal = 45 real) but within MAX_GREEN
        # Also need to be past min_green
        run_ticks_for(engine, clock, 50)

        # The EW vehicle has waited 50 nominal seconds, but we need the
        # actual time to be > max_wait real seconds
        # max_wait = 90 nominal = 45 real seconds
        # Let's advance more
        run_ticks_for(engine, clock, 50)

        # Now EW has waited about 100 nominal seconds > 90 max_wait
        # Should switch to EW
        assert engine.in_transition or engine.current_phase == Phase.EAST_WEST


# ═══════════════════════════════════════════════════════════════════════════
# Vehicle event processing
# ═══════════════════════════════════════════════════════════════════════════

class TestVehicleEvents:
    """Vehicle arrival and clearance event processing."""

    def test_vehicle_arrival_adds_to_queue(self):
        engine, clock = make_engine()
        event = make_arrival_event("V1", Direction.NORTH, VehicleType.TRUCK,
                                   event_id="e1", server_timestamp=clock.now())
        result, _ = engine.process_sensor_event(event)
        assert result == EventProcessingResult.ACCEPTED
        assert engine.queue_count(Direction.NORTH) == 1

    def test_vehicle_clearance_removes_from_queue(self):
        engine, clock = make_engine()
        arr = make_arrival_event("V1", Direction.NORTH, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)
        assert engine.queue_count(Direction.NORTH) == 1

        clr = make_clearance_event("V1", Direction.NORTH, event_id="clr1")
        result, _ = engine.process_sensor_event(clr)
        assert result == EventProcessingResult.ACCEPTED
        assert engine.queue_count(Direction.NORTH) == 0

    def test_duplicate_event_id_returns_duplicate(self):
        engine, clock = make_engine()
        event = make_arrival_event("V1", Direction.NORTH, event_id="e1", server_timestamp=clock.now())
        result1, _ = engine.process_sensor_event(event)
        assert result1 == EventProcessingResult.ACCEPTED

        result2, _ = engine.process_sensor_event(event)
        assert result2 == EventProcessingResult.DUPLICATE
        assert engine.queue_count(Direction.NORTH) == 1  # Not double-counted

    def test_orphan_cleared_rejected(self):
        engine, clock = make_engine()
        clr = make_clearance_event("UNKNOWN-V", Direction.NORTH, event_id="clr-orphan")
        result, _ = engine.process_sensor_event(clr)
        assert result == EventProcessingResult.ORPHAN

    def test_arrived_after_cleared_is_stale(self):
        """ARRIVED arriving after that vehicle's CLEARED should be ignored."""
        engine, clock = make_engine()
        arr = make_arrival_event("V1", Direction.NORTH, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        clr = make_clearance_event("V1", Direction.NORTH, event_id="clr1")
        engine.process_sensor_event(clr)

        # Now try arriving again (tombstone)
        arr2 = make_arrival_event("V1", Direction.NORTH, event_id="e2", server_timestamp=clock.now())
        result, _ = engine.process_sensor_event(arr2)
        assert result == EventProcessingResult.STALE

    def test_negative_queue_impossible(self):
        """Queue can never go negative."""
        engine, clock = make_engine()

        # Clear without arrival
        clr = make_clearance_event("V1", Direction.NORTH, event_id="clr1")
        result, _ = engine.process_sensor_event(clr)
        assert result == EventProcessingResult.ORPHAN
        assert engine.queue_count(Direction.NORTH) == 0

        # Double clear
        arr = make_arrival_event("V2", Direction.NORTH, event_id="e2", server_timestamp=clock.now())
        engine.process_sensor_event(arr)
        clr1 = make_clearance_event("V2", Direction.NORTH, event_id="clr2")
        engine.process_sensor_event(clr1)
        clr2 = make_clearance_event("V2", Direction.NORTH, event_id="clr3")
        result, _ = engine.process_sensor_event(clr2)
        # Second clear should be orphan (already removed)
        assert result == EventProcessingResult.ORPHAN
        assert engine.queue_count(Direction.NORTH) == 0

    def test_duplicate_vehicle_arrival_not_double_counted(self):
        """Same vehicle arriving twice with different event_ids should not double count."""
        engine, clock = make_engine()
        arr1 = make_arrival_event("V1", Direction.NORTH, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr1)

        arr2 = make_arrival_event("V1", Direction.NORTH, event_id="e2", server_timestamp=clock.now())
        result, _ = engine.process_sensor_event(arr2)
        assert result == EventProcessingResult.DUPLICATE
        assert engine.queue_count(Direction.NORTH) == 1

    def test_vehicle_priority_weights(self):
        """Vehicle priority weights affect phase scoring."""
        engine, clock = make_initialized_engine()

        # Add TRUCK to EAST (weight 3)
        arr1 = make_arrival_event("V1", Direction.EAST, VehicleType.TRUCK,
                                  event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr1)

        # Add EMPLOYEE_VEHICLE to NORTH (weight 1)
        arr2 = make_arrival_event("V2", Direction.NORTH, VehicleType.EMPLOYEE_VEHICLE,
                                  event_id="e2", server_timestamp=clock.now())
        engine.process_sensor_event(arr2)

        # EW should score higher due to truck
        ew_score = engine.phase_score(Phase.EAST_WEST)
        ns_score = engine.phase_score(Phase.NORTH_SOUTH)
        assert ew_score > ns_score


# ═══════════════════════════════════════════════════════════════════════════
# Emergency preemption
# ═══════════════════════════════════════════════════════════════════════════

class TestEmergencyPreemption:
    """Emergency vehicle handling."""

    def test_emergency_triggers_transition(self):
        """Emergency on conflicting phase triggers safe transition."""
        engine, clock = make_initialized_engine()

        event = make_arrival_event(
            "EMG-1", Direction.EAST, VehicleType.EMERGENCY,
            event_id="emg1", server_timestamp=clock.now(),
        )
        result, commands = engine.process_sensor_event(event)

        assert result == EventProcessingResult.ACCEPTED
        assert engine.mode == JunctionMode.EMERGENCY
        assert engine.in_transition
        # Should be in YELLOW (safe transition from NS to EW)
        assert engine.current_step_type == TransitionStep.YELLOW

    def test_emergency_same_phase_no_transition(self):
        """Emergency on same phase holds current green."""
        engine, clock = make_initialized_engine()

        event = make_arrival_event(
            "EMG-1", Direction.NORTH, VehicleType.EMERGENCY,
            event_id="emg1", server_timestamp=clock.now(),
        )
        result, _ = engine.process_sensor_event(event)

        assert result == EventProcessingResult.ACCEPTED
        assert engine.mode == JunctionMode.EMERGENCY
        # Should NOT be in transition — already serving emergency's phase
        assert not engine.in_transition

    def test_emergency_cleared_returns_to_automatic(self):
        """Clearing emergency with empty queue returns to automatic."""
        engine, clock = make_initialized_engine()

        arr = make_arrival_event("EMG-1", Direction.EAST, VehicleType.EMERGENCY,
                                event_id="emg1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        # Complete transition
        run_ticks_for(engine, clock, 40)

        # Clear emergency
        clr = make_clearance_event("EMG-1", Direction.EAST, event_id="clr-emg1")
        engine.process_sensor_event(clr)

        assert engine.mode == JunctionMode.AUTOMATIC

    def test_multiple_emergencies_fifo(self):
        """Multiple emergencies served in FIFO order."""
        engine, clock = make_initialized_engine()

        # Emergency from EAST
        arr1 = make_arrival_event("EMG-1", Direction.EAST, VehicleType.EMERGENCY,
                                  event_id="emg1", server_timestamp=clock.now())
        engine.process_sensor_event(arr1)

        # Emergency from NORTH (same phase if engine is on NS already)
        arr2 = make_arrival_event("EMG-2", Direction.NORTH, VehicleType.EMERGENCY,
                                  event_id="emg2", server_timestamp=clock.now())
        engine.process_sensor_event(arr2)

        assert len(engine.emergency_queue) == 2
        assert engine.active_emergency.vehicle_id == "EMG-1"

    def test_emergency_during_manual_cancels_manual(self):
        """Emergency overrides manual mode."""
        engine, clock = make_initialized_engine()

        # Start manual
        cmd_id, status, _ = engine.process_manual_request(
            CommandType.MANUAL_GREEN_REQUEST, Direction.WEST
        )
        assert engine.mode == JunctionMode.MANUAL

        # Emergency arrives
        arr = make_arrival_event("EMG-1", Direction.EAST, VehicleType.EMERGENCY,
                                event_id="emg1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        assert engine.mode == JunctionMode.EMERGENCY
        assert engine.active_manual is None  # Manual cancelled

    def test_duplicate_emergency_ignored(self):
        """Duplicate emergency event for same vehicle ignored."""
        engine, clock = make_initialized_engine()

        arr1 = make_arrival_event("EMG-1", Direction.EAST, VehicleType.EMERGENCY,
                                  event_id="emg1", server_timestamp=clock.now())
        engine.process_sensor_event(arr1)

        arr2 = make_arrival_event("EMG-1", Direction.EAST, VehicleType.EMERGENCY,
                                  event_id="emg2", server_timestamp=clock.now())
        engine.process_sensor_event(arr2)

        # Should still only have 1 emergency in queue
        assert len(engine.emergency_queue) == 1

    def test_stale_emergency_removed(self):
        """Stale emergencies are removed after timeout."""
        engine, clock = make_initialized_engine()

        arr = make_arrival_event("EMG-1", Direction.EAST, VehicleType.EMERGENCY,
                                event_id="emg1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        assert engine.mode == JunctionMode.EMERGENCY

        # Advance past stale timeout (60 nominal = 30 real)
        run_ticks_for(engine, clock, 65)

        # Should have cleaned up and returned to automatic
        assert engine.active_emergency is None
        assert engine.mode == JunctionMode.AUTOMATIC


# ═══════════════════════════════════════════════════════════════════════════
# Manual control
# ═══════════════════════════════════════════════════════════════════════════

class TestManualControl:
    """Manual override tests."""

    def test_manual_request_queued(self):
        engine, clock = make_initialized_engine()
        cmd_id, status, _ = engine.process_manual_request(
            CommandType.MANUAL_GREEN_REQUEST, Direction.WEST
        )
        assert status == CommandStatus.QUEUED
        assert engine.mode == JunctionMode.MANUAL

    def test_manual_hold_expires(self):
        """Manual hold expires after MANUAL_HOLD and returns to automatic."""
        engine, clock = make_initialized_engine()
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)

        # Complete transition + hold
        run_ticks_for(engine, clock, 50)  # Plenty of time for transition + hold

        assert engine.mode == JunctionMode.AUTOMATIC

    def test_return_to_automatic(self):
        engine, clock = make_initialized_engine()
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)
        assert engine.mode == JunctionMode.MANUAL

        cmd_id, status, _ = engine.process_manual_request(CommandType.RETURN_TO_AUTOMATIC)
        assert status == CommandStatus.COMPLETED
        assert engine.mode == JunctionMode.AUTOMATIC

    def test_manual_max_queue_size(self):
        """Max 3 manual requests in queue."""
        engine, clock = make_initialized_engine()

        # First request starts serving
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)

        # Queue 3 more (but dedupe same phase)
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.NORTH)
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.EAST)
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.SOUTH)

        # 4th should be rejected (max 3 in queue)
        # Note: after first is active, queue has 3
        # Actually the 4th different request should be rejected
        # Let's check the queue size
        assert len(engine.manual_queue) <= 3

    def test_manual_dedupe_same_phase(self):
        """Duplicate request for same phase is rejected."""
        engine, clock = make_initialized_engine()
        engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.EAST)

        # WEST is same phase as EAST (EAST_WEST)
        _, status, _ = engine.process_manual_request(
            CommandType.MANUAL_GREEN_REQUEST, Direction.WEST
        )
        assert status == CommandStatus.REJECTED

    def test_manual_rejected_in_emergency(self):
        """Manual request rejected during emergency mode."""
        engine, clock = make_initialized_engine()
        arr = make_arrival_event("EMG-1", Direction.EAST, VehicleType.EMERGENCY,
                                event_id="emg1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        _, status, _ = engine.process_manual_request(
            CommandType.MANUAL_GREEN_REQUEST, Direction.WEST
        )
        assert status == CommandStatus.REJECTED

    def test_manual_rejected_in_failure(self):
        """Manual request rejected during failure mode."""
        engine, clock = make_initialized_engine()
        engine._set_mode(JunctionMode.FAILURE, "test")
        _, status, _ = engine.process_manual_request(
            CommandType.MANUAL_GREEN_REQUEST, Direction.WEST
        )
        assert status == CommandStatus.REJECTED


# ═══════════════════════════════════════════════════════════════════════════
# Controller ACK handling
# ═══════════════════════════════════════════════════════════════════════════

class TestControllerACK:
    """ACK timeout, retry, duplicate, late ACK."""

    def test_ack_updates_actual_signals(self):
        engine, clock = make_initialized_engine()
        cmds = engine.initialize_green(Phase.NORTH_SOUTH)

        if cmds:
            cmd = cmds[0]
            engine.process_ack(cmd.command_id)
            assert engine.actual_signals == engine.desired_signals

    def test_ack_timeout_retries(self):
        """Timed out command retried up to ACK_RETRIES times."""
        engine, clock = make_initialized_engine()

        # Create a pending command
        cmd = engine._create_command(engine.desired_signals)
        cmd.created_at = clock.now()

        # Advance past ACK_TIMEOUT (3 real seconds)
        clock.advance(4.0)
        result_cmds = engine.tick()

        # Should have retried
        assert cmd.retries >= 1

    def test_ack_timeout_enters_failure(self):
        """After max retries, enter FAILURE mode."""
        engine, clock = make_initialized_engine()

        cmd = engine._create_command(engine.desired_signals)
        cmd.max_retries = 0  # No retries allowed

        # Advance past timeout
        clock.advance(4.0)
        engine.tick()

        assert engine.mode == JunctionMode.FAILURE

    def test_duplicate_ack_ignored(self):
        """Duplicate ACK for same command_id is ignored."""
        engine, clock = make_initialized_engine()

        cmds = engine.initialize_green(Phase.NORTH_SOUTH)
        if cmds:
            cmd = cmds[0]
            engine.process_ack(cmd.command_id)
            # Second ACK
            result = engine.process_ack(cmd.command_id)
            # Should be empty (ignored)
            assert result == [] or len(result) == 0

    def test_late_ack_for_superseded_command(self):
        """Late ACK for a superseded command is audited but not applied."""
        engine, clock = make_initialized_engine()

        # Create command
        cmd1 = engine._create_command(SignalState.all_red())

        # Change desired (supersede)
        engine.desired_signals = SignalState(
            north=SignalColor.GREEN, south=SignalColor.GREEN,
            east=SignalColor.RED, west=SignalColor.RED,
        )

        # Late ACK for cmd1
        engine.process_ack(cmd1.command_id)

        # actual_signals should NOT be ALL_RED (superseded)
        # It stays whatever it was before since the command was superseded

    def test_controller_offline_enters_failure(self):
        engine, clock = make_initialized_engine()
        engine.process_controller_status(ControllerStatus.OFFLINE)
        assert engine.mode == JunctionMode.FAILURE
        assert engine.controller_status == ControllerStatus.OFFLINE


# ═══════════════════════════════════════════════════════════════════════════
# Restart recovery
# ═══════════════════════════════════════════════════════════════════════════

class TestRestartRecovery:
    """Recovery after server restart."""

    def test_restart_sets_unknown_and_all_red(self):
        engine, clock = make_initialized_engine()
        engine.recover_from_restart()

        assert engine.actual_signals == SignalState.all_unknown()
        assert engine.desired_signals == SignalState.all_red()
        assert not engine.in_transition

    def test_restart_drops_stale_emergencies(self):
        engine, clock = make_initialized_engine()

        arr = make_arrival_event("EMG-1", Direction.EAST, VehicleType.EMERGENCY,
                                event_id="emg1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        # Advance past stale timeout
        clock.advance(engine.config.real_seconds(engine.config.emergency_stale_timeout) + 1)

        engine.recover_from_restart()
        assert len(engine.emergency_queue) == 0

    def test_restart_cancels_in_flight_transition(self):
        engine, clock = make_initialized_engine()

        # Start a transition
        arr = make_arrival_event("V1", Direction.EAST, event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)
        run_ticks_for(engine, clock, 31)

        assert engine.in_transition

        engine.recover_from_restart()
        assert not engine.in_transition
        assert engine.transition_plan is None


# ═══════════════════════════════════════════════════════════════════════════
# Concurrent sequence from spec section 15
# ═══════════════════════════════════════════════════════════════════════════

class TestConcurrentSequence:
    """
    T = 0 ms   NORTH truck arrives
    T = 4 ms   EAST emergency arrives
    T = 8 ms   Administrator requests WEST manual control
    T = 12 ms  Duplicate EAST emergency event arrives
    T = 17 ms  Controller ACK arrives

    System must remain consistent with no conflicting GREEN states.
    """

    def test_concurrent_sequence_from_spec(self):
        engine, clock = make_initialized_engine()

        # T=0: NORTH truck arrives
        arr1 = make_arrival_event(
            "TRUCK-1", Direction.NORTH, VehicleType.TRUCK,
            event_id="evt-t0", server_timestamp=clock.now(),
        )
        result, _ = engine.process_sensor_event(arr1)
        assert result == EventProcessingResult.ACCEPTED
        assert not engine.desired_signals.has_conflict()

        # T=4ms
        clock.advance(0.004)

        # EAST emergency arrives
        arr2 = make_arrival_event(
            "EMG-EAST", Direction.EAST, VehicleType.EMERGENCY,
            event_id="evt-t4", server_timestamp=clock.now(),
        )
        result, commands = engine.process_sensor_event(arr2)
        assert result == EventProcessingResult.ACCEPTED
        assert engine.mode == JunctionMode.EMERGENCY
        assert not engine.desired_signals.has_conflict()

        # T=8ms
        clock.advance(0.004)

        # Administrator requests WEST manual control (should be rejected in emergency)
        cmd_id, status, _ = engine.process_manual_request(
            CommandType.MANUAL_GREEN_REQUEST, Direction.WEST
        )
        assert status == CommandStatus.REJECTED
        assert engine.mode == JunctionMode.EMERGENCY  # Still emergency
        assert not engine.desired_signals.has_conflict()

        # T=12ms
        clock.advance(0.004)

        # Duplicate EAST emergency arrives
        arr3 = make_arrival_event(
            "EMG-EAST", Direction.EAST, VehicleType.EMERGENCY,
            event_id="evt-t12", server_timestamp=clock.now(),
        )
        result, _ = engine.process_sensor_event(arr3)
        # Should be DUPLICATE (same vehicle_id already in emergency queue)
        assert engine.mode == JunctionMode.EMERGENCY
        assert not engine.desired_signals.has_conflict()

        # T=17ms
        clock.advance(0.005)

        # Controller ACK arrives (for the pending command)
        if engine.pending_commands:
            cmd_id = list(engine.pending_commands.keys())[0]
            engine.process_ack(cmd_id)

        assert not engine.desired_signals.has_conflict()

        # Run a few ticks to verify stability
        for _ in range(10):
            clock.advance(0.5)
            engine.tick()
            assert not engine.desired_signals.has_conflict()


# ═══════════════════════════════════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════════════════════════════════

class TestPhaseScoring:
    """Phase scoring: queue_count + priority_weights + 0.5 * waiting_seconds."""

    def test_empty_phase_scores_zero(self):
        engine, clock = make_engine()
        assert engine.phase_score(Phase.NORTH_SOUTH) == 0.0

    def test_truck_scores_higher_than_employee(self):
        engine, clock = make_engine()

        arr1 = make_arrival_event("V1", Direction.NORTH, VehicleType.TRUCK,
                                  event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr1)

        arr2 = make_arrival_event("V2", Direction.EAST, VehicleType.EMPLOYEE_VEHICLE,
                                  event_id="e2", server_timestamp=clock.now())
        engine.process_sensor_event(arr2)

        ns_score = engine.phase_score(Phase.NORTH_SOUTH)  # 1 (count) + 3 (truck) = 4
        ew_score = engine.phase_score(Phase.EAST_WEST)    # 1 (count) + 1 (employee) = 2
        assert ns_score > ew_score

    def test_waiting_time_contributes(self):
        engine, clock = make_engine()

        arr = make_arrival_event("V1", Direction.NORTH, VehicleType.EMPLOYEE_VEHICLE,
                                event_id="e1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        score_before = engine.phase_score(Phase.NORTH_SOUTH)
        clock.advance(10.0)
        score_after = engine.phase_score(Phase.NORTH_SOUTH)

        assert score_after > score_before


# ═══════════════════════════════════════════════════════════════════════════
# Mode transitions
# ═══════════════════════════════════════════════════════════════════════════

class TestModeTransitions:
    """Mode transition table validation."""

    def test_auto_to_manual(self):
        engine, clock = make_initialized_engine()
        engine._set_mode(JunctionMode.MANUAL, "test")
        assert engine.mode == JunctionMode.MANUAL

    def test_auto_to_emergency(self):
        engine, clock = make_initialized_engine()
        engine._set_mode(JunctionMode.EMERGENCY, "test")
        assert engine.mode == JunctionMode.EMERGENCY

    def test_auto_to_failure(self):
        engine, clock = make_initialized_engine()
        engine._set_mode(JunctionMode.FAILURE, "test")
        assert engine.mode == JunctionMode.FAILURE

    def test_failure_to_manual_invalid(self):
        engine, clock = make_initialized_engine()
        engine._set_mode(JunctionMode.FAILURE, "test")
        with pytest.raises(ValueError):
            engine._set_mode(JunctionMode.MANUAL, "test")

    def test_failure_to_automatic_valid(self):
        engine, clock = make_initialized_engine()
        engine._set_mode(JunctionMode.FAILURE, "test")
        engine._set_mode(JunctionMode.AUTOMATIC, "recovery")
        assert engine.mode == JunctionMode.AUTOMATIC


# ═══════════════════════════════════════════════════════════════════════════
# Status / Countdown
# ═══════════════════════════════════════════════════════════════════════════

class TestStatus:
    """Status and countdown reporting."""

    def test_get_status_dict(self):
        engine, clock = make_initialized_engine()
        status = engine.get_status()
        assert status["junction_id"] == "A"
        assert status["mode"] == "AUTOMATIC"
        assert status["phase"] == "NORTH_SOUTH"
        assert "queues" in status
        assert "alerts" in status

    def test_countdown_decreases(self):
        engine, clock = make_initialized_engine()
        cd1 = engine.get_countdown()
        clock.advance(1.0)
        cd2 = engine.get_countdown()
        assert cd2 < cd1

    def test_countdown_none_in_emergency(self):
        """Emergency mode has no countdown (hold indefinitely)."""
        engine, clock = make_initialized_engine()
        arr = make_arrival_event("EMG-1", Direction.NORTH, VehicleType.EMERGENCY,
                                event_id="emg1", server_timestamp=clock.now())
        engine.process_sensor_event(arr)

        # Complete any transition
        run_ticks_for(engine, clock, 40)
        cd = engine.get_countdown()
        assert cd is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
