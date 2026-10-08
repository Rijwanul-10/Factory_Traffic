"""
Concurrency tests for JunctionActor (Phase 2 & 3 concurrency model).

Validates:
- Actor runs in a dedicated asyncio task
- API handlers put messages on queue and await futures (no sleep)
- Multiple concurrent requests serialized without race conditions
- Conflicting green states never produced even during high-concurrency requests
"""

import asyncio
import pytest

from .conftest import make_engine, make_arrival_event, make_clearance_event
from backend.domain.actor import JunctionActor, JunctionRegistry
from backend.domain.clock import FakeClock
from backend.domain.types import (
    Direction,
    Phase,
    VehicleType,
    CommandType,
    CommandStatus,
    EventProcessingResult,
    SignalState,
)


@pytest.mark.asyncio
async def test_actor_basic_lifecycle():
    engine, clock = make_engine()
    engine.initialize_green(Phase.NORTH_SOUTH)
    actor = JunctionActor(engine=engine, tick_interval=0.01)

    task = actor.start()
    assert task is not None
    assert not task.done()

    # Query status through actor
    status = await actor.get_status()
    assert status["junction_id"] == "A"
    assert status["mode"] == "AUTOMATIC"
    assert status["phase"] == "NORTH_SOUTH"

    await actor.stop()
    assert task.done()


@pytest.mark.asyncio
async def test_actor_serializes_concurrent_requests():
    """Submit 30 concurrent sensor arrivals from different directions simultaneously."""
    engine, clock = make_engine()
    engine.initialize_green(Phase.NORTH_SOUTH)
    actor = JunctionActor(engine=engine, tick_interval=0.01)
    actor.start()

    async def submit_arrival(i: int, direction: Direction):
        event = make_arrival_event(
            vehicle_id=f"V_CONC_{i}",
            direction=direction,
            vehicle_type=VehicleType.FORKLIFT,
            event_id=f"evt_conc_{i}",
            server_timestamp=clock.now(),
        )
        return await actor.handle_sensor_event(event)

    directions = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
    tasks = [
        submit_arrival(i, directions[i % len(directions)])
        for i in range(30)
    ]

    results = await asyncio.gather(*tasks)
    assert all(r == EventProcessingResult.ACCEPTED for r in results)

    status = await actor.get_status()
    total_queued = sum(status["queues"].values())
    assert total_queued == 30

    # Safety check: no conflicting greens
    sig = SignalState(**{d.lower(): s for d, s in status["desired_signals"].items()})
    assert not sig.has_conflict()

    await actor.stop()


@pytest.mark.asyncio
async def test_actor_concurrent_emergency_and_manual():
    """Emergency and manual requests submitted at the exact same moment."""
    engine, clock = make_engine()
    engine.initialize_green(Phase.NORTH_SOUTH)
    actor = JunctionActor(engine=engine, tick_interval=0.01)
    actor.start()

    # Launch manual request and emergency event simultaneously
    manual_fut = asyncio.create_task(
        actor.handle_manual_command(CommandType.MANUAL_GREEN_REQUEST, Direction.WEST)
    )
    emg_event = make_arrival_event(
        vehicle_id="EMG_RACE_1",
        direction=Direction.EAST,
        vehicle_type=VehicleType.EMERGENCY,
        event_id="evt_emg_race",
        server_timestamp=clock.now(),
    )
    emg_fut = asyncio.create_task(actor.handle_sensor_event(emg_event))

    m_res, e_res = await asyncio.gather(manual_fut, emg_fut)

    status = await actor.get_status()
    # State must be consistent: Emergency takes precedence or transitions safely
    assert status["mode"] in ("EMERGENCY", "MANUAL")
    sig = SignalState(**{d.lower(): s for d, s in status["desired_signals"].items()})
    assert not sig.has_conflict()

    await actor.stop()


@pytest.mark.asyncio
async def test_junction_registry():
    registry = JunctionRegistry()
    engine_a, _ = make_engine(junction_id="A")
    engine_b, _ = make_engine(junction_id="B")

    actor_a = JunctionActor(engine=engine_a, tick_interval=0.01)
    actor_b = JunctionActor(engine=engine_b, tick_interval=0.01)

    registry.register(actor_a)
    registry.register(actor_b)

    assert registry.get("A") is actor_a
    assert registry.get("B") is actor_b
    assert registry.get("C") is None
    assert set(registry.all_junction_ids()) == {"A", "B"}

    await registry.start_all()
    status_a = await actor_a.get_status()
    status_b = await actor_b.get_status()
    assert status_a["junction_id"] == "A"
    assert status_b["junction_id"] == "B"
    await registry.stop_all()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
