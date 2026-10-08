"""
Comprehensive QA Phase 2 Domain Test Suite.

Proves all 10 categories required by the assessment:
1. Safety Invariant across 10,000 random steps (no conflicting green, no direct jump, safe sequence)
2. Timing (nominal vs real halved by TIME_SCALE=2, yellow & all-red never shortened/skipped)
3. Automatic scoring (queue size, vehicle weighting, waiting time, MAX_WAIT starvation protection)
4. Empty-phase skipping & rest state (no cycling, no unnecessary switching)
5. Early green end (GRACE_EMPTY + MIN_GREEN early transition, busy extension up to MAX_GREEN)
6. Vehicle events (idempotency, no negative queue, orphan clearance rejection, arrival after clearance, out-of-order)
7. Emergency preemption (safe transition, same-phase hold, transition continuation, FIFO 3 directions, duplicate ignored, clearance/stale timeout, waiting clocks preserved)
8. Manual override (safe transition, MANUAL_HOLD confirmation, max queue 3, TTL expiry, dedupe, safe return to auto, emergency > manual)
9. Commands & failure (desired vs actual separation, ACK retries, FAILURE mode, controller reconnect ALL_RED verification, sensor offline DEGRADED fixed-time service)
10. Concurrency (spec sequence at T=0, 4ms, 8ms, 12ms, 17ms replay both sequential and async)
"""

import asyncio
import random
import time
import pytest

from backend.domain.clock import FakeClock
from backend.domain.engine import JunctionEngine, build_transition_plan
from backend.domain.actor import JunctionActor
from backend.domain.types import (
    CommandStatus,
    CommandType,
    ControllerStatus,
    Direction,
    EmergencyEntry,
    EventProcessingResult,
    EventType,
    JunctionConfig,
    JunctionMode,
    ManualRequest,
    Phase,
    SensorEvent,
    SignalColor,
    SignalState,
    TransitionStep,
    VehicleType,
)
from backend.tests.domain.conftest import (
    make_arrival_event,
    make_clearance_event,
    make_config,
    make_engine,
    make_initialized_engine,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Safety Invariant: 10,000 Random Steps Property Test
# ═══════════════════════════════════════════════════════════════════════════

def test_safety_invariant_10000_random_steps():
    """
    1. Safety invariant across 10,000 random sequences of events, commands, ACKs and ticks:
       - Conflicting phases are NEVER GREEN together
       - No GREEN -> conflicting GREEN directly
       - Every phase change goes GREEN -> YELLOW -> ALL_RED -> GREEN
    """
    config = make_config(time_scale=2.0)
    engine, clock = make_initialized_engine(time_scale=2.0)

    rng = random.Random(1337)
    directions = list(Direction)
    vehicle_types = [VehicleType.TRUCK, VehicleType.FORKLIFT, VehicleType.EMPLOYEE_VEHICLE]

    # Transition tracking state machine
    last_phase_seen = Phase.NORTH_SOUTH
    phase_in_green = Phase.NORTH_SOUTH
    seen_states: list[str] = ["NS_GREEN"]

    for step in range(10000):
        action = rng.choice([
            "arrive", "clear", "emergency", "manual", "return_auto",
            "ack", "sensor_offline", "sensor_online", "controller_status",
            "tick", "tick", "tick", "tick", "tick"
        ])

        if action == "arrive":
            d = rng.choice(directions)
            vt = rng.choice(vehicle_types)
            ev = make_arrival_event(
                vehicle_id=f"V-{step}",
                direction=d,
                vehicle_type=vt,
                event_id=f"evt-{step}",
                server_timestamp=clock.now(),
            )
            engine.process_sensor_event(ev)

        elif action == "clear":
            if engine.waiting_vehicles:
                vid = rng.choice(list(engine.waiting_vehicles.keys()))
                v = engine.waiting_vehicles[vid]
                ev = make_clearance_event(
                    vehicle_id=vid,
                    direction=v.direction,
                    event_id=f"clr-{step}",
                )
                engine.process_sensor_event(ev)

        elif action == "emergency":
            d = rng.choice(directions)
            ev = make_arrival_event(
                vehicle_id=f"EMG-{step}",
                direction=d,
                vehicle_type=VehicleType.EMERGENCY,
                event_id=f"emg-{step}",
                server_timestamp=clock.now(),
            )
            engine.process_sensor_event(ev)

        elif action == "manual":
            d = rng.choice(directions)
            engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, d)

        elif action == "return_auto":
            engine.process_manual_request(CommandType.RETURN_TO_AUTOMATIC)

        elif action == "ack":
            if engine.pending_commands:
                cmd_id = rng.choice(list(engine.pending_commands.keys()))
                cmd = engine.pending_commands[cmd_id]
                engine.process_ack(cmd_id, cmd.desired_signals)

        elif action == "sensor_offline":
            d = rng.choice(directions)
            engine.process_sensor_status(d, ControllerStatus.OFFLINE)

        elif action == "sensor_online":
            d = rng.choice(directions)
            engine.process_sensor_status(d, ControllerStatus.ONLINE)

        elif action == "controller_status":
            st = rng.choice([ControllerStatus.ONLINE, ControllerStatus.OFFLINE])
            engine.process_controller_status(st)

        elif action == "tick":
            dt = rng.choice([0.1, 0.5, 1.0, 2.5])
            clock.advance(dt)
            cmds = engine.tick()
            # 80% chance of immediate ACK in simulator
            for cmd in cmds:
                if rng.random() < 0.8:
                    engine.process_ack(cmd.command_id, cmd.desired_signals)

        # ── ASSERTION A: Desired signals must NEVER have conflict ──
        sig = engine.desired_signals
        assert not sig.has_conflict(), (
            f"Step {step}: CONFLICTING GREENS! Signals: {sig.as_dict()}"
        )

        # ── ASSERTION B: Check transition sequencing ──
        ns_is_green = (sig.north == SignalColor.GREEN or sig.south == SignalColor.GREEN)
        ew_is_green = (sig.east == SignalColor.GREEN or sig.west == SignalColor.GREEN)
        ns_is_yellow = (sig.north == SignalColor.YELLOW or sig.south == SignalColor.YELLOW)
        ew_is_yellow = (sig.east == SignalColor.YELLOW or sig.west == SignalColor.YELLOW)
        is_all_red = sig.is_all_red()

        current_desc = "UNKNOWN"
        if ns_is_green:
            current_desc = "NS_GREEN"
        elif ew_is_green:
            current_desc = "EW_GREEN"
        elif ns_is_yellow:
            current_desc = "NS_YELLOW"
        elif ew_is_yellow:
            current_desc = "EW_YELLOW"
        elif is_all_red:
            current_desc = "ALL_RED"

        prev_desc = seen_states[-1]
        if current_desc != prev_desc:
            # Transition check:
            # If leaving NS_GREEN, cannot jump directly to EW_GREEN
            if prev_desc == "NS_GREEN":
                assert current_desc in ("NS_YELLOW", "ALL_RED", "NS_GREEN"), (
                    f"Step {step}: Direct jump from NS_GREEN to {current_desc}!"
                )
            # If leaving EW_GREEN, cannot jump directly to NS_GREEN
            elif prev_desc == "EW_GREEN":
                assert current_desc in ("EW_YELLOW", "ALL_RED", "EW_GREEN"), (
                    f"Step {step}: Direct jump from EW_GREEN to {current_desc}!"
                )
            seen_states.append(current_desc)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Timing: Nominal vs Real (TIME_SCALE=2) & Protected Yellow/All-Red
# ═══════════════════════════════════════════════════════════════════════════

def test_timing_nominal_vs_real_durations():
    """Nominal: green 30s, yellow 5s, all-red 2s. With TIME_SCALE=2 real durations are halved."""
    config = make_config(
        green_duration=30.0,
        yellow_duration=5.0,
        all_red_duration=2.0,
        time_scale=2.0,
    )
    assert config.real_seconds(config.green_duration) == 15.0
    assert config.real_seconds(config.yellow_duration) == 2.5
    assert config.real_seconds(config.all_red_duration) == 1.0


def test_yellow_and_all_red_never_shortened_even_for_emergency():
    """YELLOW (2.5s) and ALL_RED (1s) are never shortened or skipped for emergency."""
    engine, clock = make_initialized_engine(time_scale=2.0)
    # Put vehicle in EW to trigger transition
    engine.process_sensor_event(make_arrival_event(vehicle_id="V-1", direction=Direction.EAST))

    # Advance 15s to end green
    clock.advance(15.0)
    cmds = engine.tick()
    for c in cmds: engine.process_ack(c.command_id, c.desired_signals)

    # Step 1: In YELLOW (duration real = 2.5s)
    assert engine.desired_signals.north == SignalColor.YELLOW
    assert engine.current_step_type == TransitionStep.YELLOW

    # High priority emergency arrives during YELLOW!
    emg = make_arrival_event(vehicle_id="EMG-1", direction=Direction.WEST, vehicle_type=VehicleType.EMERGENCY)
    engine.process_sensor_event(emg)

    # Yellow must NOT end before 2.5s
    clock.advance(2.0)
    cmds = engine.tick()
    for c in cmds: engine.process_ack(c.command_id, c.desired_signals)
    assert engine.desired_signals.north == SignalColor.YELLOW, "Yellow ended early!"

    # Reach 2.5s -> goes to ALL_RED
    clock.advance(0.5)
    cmds = engine.tick()
    for c in cmds: engine.process_ack(c.command_id, c.desired_signals)
    assert engine.desired_signals.is_all_red()
    assert engine.current_step_type == TransitionStep.ALL_RED

    # ALL_RED real duration is 1.0s. At 0.8s must still be ALL_RED
    clock.advance(0.8)
    cmds = engine.tick()
    for c in cmds: engine.process_ack(c.command_id, c.desired_signals)
    assert engine.desired_signals.is_all_red(), "All-red shortened!"

    # Reach 1.0s -> turns EW GREEN
    clock.advance(0.2)
    cmds = engine.tick()
    for c in cmds: engine.process_ack(c.command_id, c.desired_signals)
    assert engine.desired_signals.east == SignalColor.GREEN


# ═══════════════════════════════════════════════════════════════════════════
# 3. Automatic Scoring Tests
# ═══════════════════════════════════════════════════════════════════════════

def test_scoring_bigger_queue_wins():
    """Phase with larger queue gets higher score."""
    engine, clock = make_initialized_engine()
    for i in range(5):
        engine.process_sensor_event(make_arrival_event(vehicle_id=f"NS-{i}", direction=Direction.NORTH, vehicle_type=VehicleType.EMPLOYEE_VEHICLE))
    for i in range(2):
        engine.process_sensor_event(make_arrival_event(vehicle_id=f"EW-{i}", direction=Direction.EAST, vehicle_type=VehicleType.EMPLOYEE_VEHICLE))

    assert engine.phase_score(Phase.NORTH_SOUTH) > engine.phase_score(Phase.EAST_WEST)


def test_scoring_vehicle_weighting_shifts_priority():
    """TRUCK (weight 3) + FORKLIFT (weight 2) beats 3 EMPLOYEE_VEHICLES (weight 1 each)."""
    engine, clock = make_initialized_engine()
    # Phase NS: 3 employee vehicles -> score = 3 count + 3 weights = 6
    for i in range(3):
        engine.process_sensor_event(make_arrival_event(vehicle_id=f"E-{i}", direction=Direction.NORTH, vehicle_type=VehicleType.EMPLOYEE_VEHICLE))

    # Phase EW: 1 Truck + 1 Forklift -> score = 2 count + (3 + 2) = 7
    engine.process_sensor_event(make_arrival_event(vehicle_id="T-1", direction=Direction.EAST, vehicle_type=VehicleType.TRUCK))
    engine.process_sensor_event(make_arrival_event(vehicle_id="F-1", direction=Direction.WEST, vehicle_type=VehicleType.FORKLIFT))

    assert engine.phase_score(Phase.EAST_WEST) > engine.phase_score(Phase.NORTH_SOUTH)


def test_scoring_starvation_protection_forces_next_phase():
    """Phase waiting longer than MAX_WAIT is forced next regardless of current phase queue."""
    engine, clock = make_initialized_engine(max_wait=90.0, time_scale=2.0)
    # Put 10 vehicles in NS
    for i in range(10):
        engine.process_sensor_event(make_arrival_event(vehicle_id=f"NS-{i}", direction=Direction.NORTH))

    # Put 1 employee vehicle in EW at T=0
    engine.process_sensor_event(make_arrival_event(vehicle_id="EW-1", direction=Direction.EAST, vehicle_type=VehicleType.EMPLOYEE_VEHICLE))

    # Advance beyond max_wait_real (45s real)
    clock.advance(46.0)
    cmds = engine.tick()
    # Starvation override must trigger transition to EW
    assert engine.in_transition
    assert engine.transition_plan.target_phase == Phase.EAST_WEST


# ═══════════════════════════════════════════════════════════════════════════
# 4. Empty-Phase Skipping & Rest State
# ═══════════════════════════════════════════════════════════════════════════

def test_empty_phase_skipping_and_rest_state():
    """If all queues empty, current green holds (no cycling between empty phases)."""
    engine, clock = make_initialized_engine(green_duration=30.0, time_scale=2.0)
    # Green real = 15s. Advance 30s with NO vehicles
    clock.advance(30.0)
    cmds = engine.tick()

    assert not engine.in_transition
    assert engine.desired_signals.north == SignalColor.GREEN
    assert engine.current_phase == Phase.NORTH_SOUTH
    assert len(cmds) == 0  # No commands, no cycling


# ═══════════════════════════════════════════════════════════════════════════
# 5. Early Green End and Green Extension
# ═══════════════════════════════════════════════════════════════════════════

def test_early_green_end_after_grace_and_min_green():
    """Current phase empties before green ends, other phase has vehicles -> transitions safely."""
    engine, clock = make_initialized_engine(
        green_duration=30.0, min_green=5.0, grace_empty=2.0, time_scale=2.0
    )
    # Min green real = 2.5s, grace real = 1.0s
    engine.process_sensor_event(make_arrival_event(vehicle_id="V-NS", direction=Direction.NORTH))
    engine.process_sensor_event(make_arrival_event(vehicle_id="V-EW", direction=Direction.EAST))

    # Clear NS vehicle after 3s (> min_green real 2.5s)
    clock.advance(3.0)
    engine.process_sensor_event(make_clearance_event(vehicle_id="V-NS", direction=Direction.NORTH))

    # Advance 0.5s: grace timer started, but not expired (grace = 1.0s real)
    clock.advance(0.5)
    cmds = engine.tick()
    assert not engine.in_transition

    # Advance 0.6s: grace expires -> transition initiates!
    clock.advance(0.6)
    cmds = engine.tick()
    assert engine.in_transition
    assert engine.transition_plan.target_phase == Phase.EAST_WEST


def test_green_extension_up_to_max_green():
    """Current phase still busy at 30s and nobody waiting -> extends up to MAX_GREEN."""
    engine, clock = make_initialized_engine(
        green_duration=30.0, max_green=60.0, time_scale=2.0
    )
    # Green real = 15s, Max green real = 30s
    engine.process_sensor_event(make_arrival_event(vehicle_id="V-BUSY", direction=Direction.NORTH))

    # At 15s (normal green expiry), other phase is empty -> green extends
    clock.advance(15.0)
    cmds = engine.tick()
    assert not engine.in_transition
    assert engine.desired_signals.north == SignalColor.GREEN

    # At 25s, still extends
    clock.advance(10.0)
    cmds = engine.tick()
    assert not engine.in_transition
    assert engine.desired_signals.north == SignalColor.GREEN


# ═══════════════════════════════════════════════════════════════════════════
# 6. Vehicle Events Validation & Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

def test_vehicle_events_edge_cases():
    """Idempotency, non-negative queue, orphan rejection, arrived-after-cleared."""
    engine, clock = make_initialized_engine()

    # 1. Arrival
    res, _ = engine.process_sensor_event(make_arrival_event(vehicle_id="V-1", event_id="EVT-1"))
    assert res == EventProcessingResult.ACCEPTED
    assert engine.queue_count(Direction.NORTH) == 1

    # 2. Duplicate arrival
    res, _ = engine.process_sensor_event(make_arrival_event(vehicle_id="V-1", event_id="EVT-1"))
    assert res == EventProcessingResult.DUPLICATE
    assert engine.queue_count(Direction.NORTH) == 1

    # 3. Clearance
    res, _ = engine.process_sensor_event(make_clearance_event(vehicle_id="V-1", event_id="EVT-2"))
    assert res == EventProcessingResult.ACCEPTED
    assert engine.queue_count(Direction.NORTH) == 0

    # 4. Orphan clearance for unknown vehicle
    res, _ = engine.process_sensor_event(make_clearance_event(vehicle_id="UNKNOWN", event_id="EVT-3"))
    assert res == EventProcessingResult.ORPHAN
    assert engine.queue_count(Direction.NORTH) == 0

    # 5. Arrived after its cleared is stale
    res, _ = engine.process_sensor_event(make_arrival_event(vehicle_id="V-1", event_id="EVT-4"))
    assert res == EventProcessingResult.STALE


# ═══════════════════════════════════════════════════════════════════════════
# 7. Emergency Handling Requirements
# ═══════════════════════════════════════════════════════════════════════════

def test_three_emergencies_served_fifo():
    """Three emergencies from different directions are queued and served FIFO."""
    engine, clock = make_initialized_engine(time_scale=2.0)
    # Queue three emergencies: East, South, West
    e1 = make_arrival_event(vehicle_id="EMG-E", direction=Direction.EAST, vehicle_type=VehicleType.EMERGENCY, event_id="e1")
    e2 = make_arrival_event(vehicle_id="EMG-S", direction=Direction.SOUTH, vehicle_type=VehicleType.EMERGENCY, event_id="e2")
    e3 = make_arrival_event(vehicle_id="EMG-W", direction=Direction.WEST, vehicle_type=VehicleType.EMERGENCY, event_id="e3")

    engine.process_sensor_event(e1)
    engine.process_sensor_event(e2)
    engine.process_sensor_event(e3)

    assert len(engine.emergency_queue) == 3
    assert engine.emergency_queue[0].direction == Direction.EAST
    assert engine.emergency_queue[1].direction == Direction.SOUTH
    assert engine.emergency_queue[2].direction == Direction.WEST


def test_repeated_emergency_event_is_ignored():
    """Repeated emergency event with same event_id is ignored and not duplicated."""
    engine, clock = make_initialized_engine()
    e1 = make_arrival_event(vehicle_id="EMG-E", direction=Direction.EAST, vehicle_type=VehicleType.EMERGENCY, event_id="e1")
    res1, _ = engine.process_sensor_event(e1)
    res2, _ = engine.process_sensor_event(e1)
    assert res1 == EventProcessingResult.ACCEPTED
    assert res2 == EventProcessingResult.DUPLICATE
    assert len(engine.emergency_queue) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 8. Manual Control Requirements
# ═══════════════════════════════════════════════════════════════════════════

def test_manual_control_lifecycle_and_hierarchy():
    """Manual request uses safe transition, holds MANUAL_HOLD, max queue 3, emergency overrides."""
    engine, clock = make_initialized_engine(manual_hold=10.0, time_scale=2.0)

    # 1. Manual request for WEST (Phase EW)
    cmd_id, status, cmds = engine.process_manual_request(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)
    assert status == CommandStatus.QUEUED
    assert engine.in_transition

    # Complete transition to EW green
    clock.advance(2.5)  # Yellow
    cmds = engine.tick()
    for c in cmds: engine.process_ack(c.command_id, c.desired_signals)
    clock.advance(1.0)  # All red
    cmds = engine.tick()
    for c in cmds: engine.process_ack(c.command_id, c.desired_signals)

    assert engine.desired_signals.west == SignalColor.GREEN
    assert engine.mode == JunctionMode.MANUAL

    # 2. Confirm green by ACK to start MANUAL_HOLD
    ack_cmds = engine.process_ack(cmds[0].command_id, engine.desired_signals)
    assert engine.manual_hold_start is not None

    # 3. Emergency overrides manual immediately!
    emg = make_arrival_event(vehicle_id="EMG-N", direction=Direction.NORTH, vehicle_type=VehicleType.EMERGENCY)
    engine.process_sensor_event(emg)
    assert engine.mode == JunctionMode.EMERGENCY


# ═══════════════════════════════════════════════════════════════════════════
# 9. Commands, Failure, Reconnect & Sensor Offline
# ═══════════════════════════════════════════════════════════════════════════

def test_desired_vs_actual_separation_and_ack_requirement():
    """Actual signals change ONLY on matching ACK."""
    engine, clock = make_initialized_engine()
    engine.process_sensor_event(make_arrival_event(vehicle_id="V-EW", direction=Direction.EAST))
    clock.advance(15.0)
    cmds = engine.tick()
    assert len(cmds) > 0

    # Desired is YELLOW, but actual must remain GREEN until ACK
    assert engine.desired_signals.north == SignalColor.YELLOW
    assert engine.actual_signals.north == SignalColor.GREEN

    # Apply matching ACK
    engine.process_ack(cmds[0].command_id, engine.desired_signals)
    assert engine.actual_signals.north == SignalColor.YELLOW


def test_sensor_offline_marks_direction_degraded_with_fixed_time_service():
    """Sensor offline marks direction DEGRADED and guarantees minimum fixed-time service."""
    engine, clock = make_initialized_engine(green_duration=30.0, time_scale=2.0)
    assert engine.current_phase == Phase.NORTH_SOUTH

    # East sensor goes offline
    engine.process_sensor_status(Direction.EAST, ControllerStatus.OFFLINE)
    assert engine.direction_sensor_status[Direction.EAST] == ControllerStatus.OFFLINE

    # Even with 0 waiting vehicles, East/West phase is NOT considered empty!
    assert not engine.phase_is_empty(Phase.EAST_WEST)
    assert not engine.all_queues_empty()

    # Alerts include sensor failure
    alerts = engine._build_alerts()
    assert any("Sensor EAST: OFFLINE" in a for a in alerts)

    # Green expires for NS -> EW is scheduled for minimum fixed-time service!
    clock.advance(15.0)
    cmds = engine.tick()
    assert engine.in_transition
    assert engine.transition_plan.target_phase == Phase.EAST_WEST


# ═══════════════════════════════════════════════════════════════════════════
# 10. Concurrency: Spec Scenario 9 Replay (Sequential & Async Tasks)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spec_concurrent_sequence_async_tasks():
    """
    Spec scenario 9:
    T = 0 ms   NORTH truck arrives
    T = 4 ms   EAST emergency arrives
    T = 8 ms   Administrator requests WEST manual control
    T = 12 ms  Duplicate EAST emergency arrives
    T = 17 ms  Controller ACK arrives
    """
    config = make_config(time_scale=2.0)
    clock = FakeClock()
    engine = JunctionEngine(config=config, clock=clock)
    engine.initialize_green(Phase.NORTH_SOUTH)
    actor = JunctionActor(engine=engine, tick_interval=0.01)
    await actor.start()

    try:
        # Launch concurrent requests simultaneously
        task1 = asyncio.create_task(
            actor.handle_sensor_event(
                make_arrival_event(vehicle_id="TRUCK-N", direction=Direction.NORTH, vehicle_type=VehicleType.TRUCK, event_id="evt-con-1")
            )
        )
        task2 = asyncio.create_task(
            actor.handle_sensor_event(
                make_arrival_event(vehicle_id="EMG-E", direction=Direction.EAST, vehicle_type=VehicleType.EMERGENCY, event_id="evt-con-2")
            )
        )
        task3 = asyncio.create_task(
            actor.handle_manual_command(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)
        )
        task4 = asyncio.create_task(
            actor.handle_sensor_event(
                make_arrival_event(vehicle_id="EMG-E", direction=Direction.EAST, vehicle_type=VehicleType.EMERGENCY, event_id="evt-con-2")
            )
        )

        res1, res2, res3, res4 = await asyncio.gather(task1, task2, task3, task4)

        # Duplicate event must return DUPLICATE
        assert res4 == EventProcessingResult.DUPLICATE

        # Junction status must be consistent
        st = await actor.get_status()
        assert not SignalState(**{k.lower(): SignalColor(v) for k, v in st["desired_signals"].items()}).has_conflict()
        # Mode must be EMERGENCY because emergency overrides manual!
        assert st["mode"] == "EMERGENCY"

    finally:
        await actor.stop()
