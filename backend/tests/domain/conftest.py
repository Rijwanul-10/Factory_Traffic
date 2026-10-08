"""
Shared test fixtures for domain tests.
"""

import pytest
import sys
import os

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.domain.clock import FakeClock
from backend.domain.types import (
    Direction, Phase, JunctionConfig, SensorEvent, EventType, VehicleType,
    SignalState, SignalColor, JunctionMode, TransitionStep,
)
from backend.domain.engine import JunctionEngine


def make_config(**overrides) -> JunctionConfig:
    """Create a JunctionConfig with optional overrides."""
    defaults = dict(
        junction_id="A",
        green_duration=30.0,
        min_green=5.0,
        max_green=60.0,
        yellow_duration=5.0,
        all_red_duration=2.0,
        time_scale=2.0,
        manual_hold=10.0,
        emergency_stale_timeout=60.0,
        max_wait=90.0,
        grace_empty=2.0,
        ack_timeout=3.0,
        ack_retries=2,
    )
    defaults.update(overrides)
    return JunctionConfig(**defaults)


def make_engine(clock: FakeClock = None, **config_overrides) -> tuple[JunctionEngine, FakeClock]:
    """Create a JunctionEngine with a FakeClock and optional config overrides."""
    if clock is None:
        clock = FakeClock()
    config = make_config(**config_overrides)
    engine = JunctionEngine(config=config, clock=clock)
    return engine, clock


def make_initialized_engine(**config_overrides) -> tuple[JunctionEngine, FakeClock]:
    """Create an engine that's already initialized with NS GREEN."""
    engine, clock = make_engine(**config_overrides)
    engine.initialize_green(Phase.NORTH_SOUTH)
    engine.drain_audit_buffer()  # Clear init audit entries
    return engine, clock


def make_arrival_event(
    vehicle_id: str = "VH-001",
    direction: Direction = Direction.NORTH,
    vehicle_type: VehicleType = VehicleType.TRUCK,
    junction_id: str = "A",
    event_id: str = None,
    sequence_no: int = 1,
    timestamp: float = 0.0,
    server_timestamp: float = 0.0,
) -> SensorEvent:
    """Create a VEHICLE_ARRIVED sensor event."""
    if event_id is None:
        event_id = f"evt-{vehicle_id}-arr"
    return SensorEvent(
        event_id=event_id,
        junction_id=junction_id,
        direction=direction,
        event_type=EventType.VEHICLE_ARRIVED,
        vehicle_id=vehicle_id,
        vehicle_type=vehicle_type,
        sequence_no=sequence_no,
        timestamp=timestamp,
        server_timestamp=server_timestamp,
    )


def make_clearance_event(
    vehicle_id: str = "VH-001",
    direction: Direction = Direction.NORTH,
    junction_id: str = "A",
    event_id: str = None,
    sequence_no: int = 2,
) -> SensorEvent:
    """Create a VEHICLE_CLEARED sensor event."""
    if event_id is None:
        event_id = f"evt-{vehicle_id}-clr"
    return SensorEvent(
        event_id=event_id,
        junction_id=junction_id,
        direction=direction,
        event_type=EventType.VEHICLE_CLEARED,
        vehicle_id=vehicle_id,
        sequence_no=sequence_no,
    )


def run_ticks_for(engine: JunctionEngine, clock: FakeClock, nominal_seconds: float, tick_interval: float = 0.5, auto_ack: bool = True):
    """Advance clock and tick the engine for a number of nominal seconds."""
    real_seconds = engine.config.real_seconds(nominal_seconds)
    elapsed = 0.0
    all_commands = []
    while elapsed < real_seconds:
        step = min(tick_interval, real_seconds - elapsed)
        clock.advance(step)
        if auto_ack:
            for cmd_id in list(engine.pending_commands.keys()):
                engine.process_ack(cmd_id)
        cmds = engine.tick()
        all_commands.extend(cmds)
        if auto_ack:
            for cmd_id in list(engine.pending_commands.keys()):
                engine.process_ack(cmd_id)
        elapsed += step
    return all_commands
