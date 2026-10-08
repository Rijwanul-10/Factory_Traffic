"""
Phase 5 Tests: Restart Recovery Integration.

Validates the full restart recovery lifecycle:
- Loading persisted vehicles, emergency entries, and manual requests
- Resetting actual_signals = UNKNOWN
- Resetting desired_signals = ALL_RED
- Generating ALL_RED controller command
- Dropping stale emergencies and expired manual requests
- Clearing in-flight transitions
- Automatic scheduling resumes only after ALL_RED ACK
"""

import os
import tempfile
import pytest

from backend.adapters.sqlite_repository import SqliteRepository
from backend.domain.clock import FakeClock
from backend.domain.engine import JunctionEngine
from backend.domain.recovery import recover_junction_from_storage
from backend.domain.types import (
    Direction,
    EmergencyEntry,
    JunctionConfig,
    JunctionMode,
    ManualRequest,
    Phase,
    SignalColor,
    SignalState,
    VehicleType,
    WaitingVehicle,
)


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    repo = SqliteRepository(db_path=db_path)
    yield repo
    repo.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            pass


@pytest.mark.asyncio
async def test_restart_recovery_lifecycle(temp_db):
    clock = FakeClock(start=1000.0)
    config = JunctionConfig(junction_id="A")
    await temp_db.save_junction_config(config)

    # 1. Populate storage as if the previous server had active state
    # 2 vehicles
    v1 = WaitingVehicle("V1", "A", Direction.NORTH, VehicleType.TRUCK, arrived_at=950.0)
    v2 = WaitingVehicle("V2", "A", Direction.EAST, VehicleType.FORKLIFT, arrived_at=980.0)
    await temp_db.save_waiting_vehicle(v1)
    await temp_db.save_waiting_vehicle(v2)

    # 1 active emergency (stale at 1050 > 1000) and 1 stale emergency (stale at 990 < 1000)
    emg_active = EmergencyEntry("EMG_ACTIVE", Direction.EAST, Phase.EAST_WEST, arrived_at=990.0, stale_timeout=1050.0)
    emg_stale = EmergencyEntry("EMG_STALE", Direction.NORTH, Phase.NORTH_SOUTH, arrived_at=900.0, stale_timeout=960.0)
    await temp_db.save_emergency_entry(emg_active, "A")
    await temp_db.save_emergency_entry(emg_stale, "A")

    # 1 valid manual request and 1 expired manual request
    man_active = ManualRequest("man-valid", Direction.WEST, Phase.EAST_WEST, created_at=990.0, ttl=60.0)
    man_expired = ManualRequest("man-expired", Direction.NORTH, Phase.NORTH_SOUTH, created_at=900.0, ttl=30.0)
    await temp_db.save_manual_request(man_active, "A")
    await temp_db.save_manual_request(man_expired, "A")

    # 2. Boot a fresh engine (simulating process restart)
    new_engine = JunctionEngine(config=config, clock=clock)

    # 3. Perform restart recovery
    commands = await recover_junction_from_storage(new_engine, temp_db)

    # 4. Verify safety state
    assert len(commands) == 1
    init_cmd = commands[0]
    assert init_cmd.desired_signals == SignalState.all_red()

    # actual_signals must be UNKNOWN
    assert new_engine.actual_signals == SignalState.all_unknown()
    # desired_signals must be ALL_RED
    assert new_engine.desired_signals == SignalState.all_red()
    # in_transition must be False (transitions discarded)
    assert not new_engine.in_transition

    # Waiting vehicles restored
    assert len(new_engine.waiting_vehicles) == 2
    assert "V1" in new_engine.waiting_vehicles
    assert "V2" in new_engine.waiting_vehicles

    # Stale emergency dropped, active retained
    assert len(new_engine.emergency_queue) == 1
    assert new_engine.emergency_queue[0].vehicle_id == "EMG_ACTIVE"

    # Expired manual dropped, active retained
    assert len(new_engine.manual_queue) == 1
    assert new_engine.manual_queue[0].command_id == "man-valid"

    # 5. Controller ACKs the initial ALL_RED command
    new_engine.process_ack(init_cmd.command_id)
    assert new_engine.actual_signals == SignalState.all_red()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
