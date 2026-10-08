"""
Phase 5 Tests: SQLite Persistence Repository.

Validates:
- Schema initialization
- Junction config persistence
- Junction state snapshot persistence
- Waiting vehicles CRUD
- Idempotency tracking (processed_events)
- Command storage & status updates
- Emergency and manual queues persistence
- Audit log storage & retrieval
- Device status tracking
"""

import os
import tempfile
import pytest

from backend.adapters.sqlite_repository import SqliteRepository
from backend.domain.types import (
    AuditEntry,
    AuditEventType,
    CommandStatus,
    ControllerStatus,
    Direction,
    EmergencyEntry,
    JunctionConfig,
    ManualRequest,
    PendingCommand,
    Phase,
    SignalColor,
    SignalState,
    VehicleType,
    WaitingVehicle,
)


@pytest.fixture
def repo():
    # Use temporary file to test real SQLite database file operations
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    repository = SqliteRepository(db_path=db_path)
    yield repository
    repository.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            pass


@pytest.mark.asyncio
async def test_junction_config_crud(repo):
    config = JunctionConfig(
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
    await repo.save_junction_config(config)

    loaded = await repo.load_junction_config("A")
    assert loaded is not None
    assert loaded.junction_id == "A"
    assert loaded.green_duration == 30.0
    assert loaded.time_scale == 2.0


@pytest.mark.asyncio
async def test_waiting_vehicles_crud(repo):
    config = JunctionConfig(junction_id="A")
    await repo.save_junction_config(config)

    v1 = WaitingVehicle(
        vehicle_id="V_101",
        junction_id="A",
        direction=Direction.NORTH,
        vehicle_type=VehicleType.TRUCK,
        arrived_at=1000.0,
        sequence_no=1,
    )
    v2 = WaitingVehicle(
        vehicle_id="V_102",
        junction_id="A",
        direction=Direction.EAST,
        vehicle_type=VehicleType.FORKLIFT,
        arrived_at=1005.0,
        sequence_no=2,
    )
    await repo.save_waiting_vehicle(v1)
    await repo.save_waiting_vehicle(v2)

    vehicles = await repo.load_waiting_vehicles("A")
    assert len(vehicles) == 2
    assert vehicles[0].vehicle_id == "V_101"
    assert vehicles[1].vehicle_id == "V_102"

    await repo.remove_waiting_vehicle("V_101")
    remaining = await repo.load_waiting_vehicles("A")
    assert len(remaining) == 1
    assert remaining[0].vehicle_id == "V_102"


@pytest.mark.asyncio
async def test_processed_events_idempotency(repo):
    config = JunctionConfig(junction_id="A")
    await repo.save_junction_config(config)

    assert not await repo.is_event_processed("evt_test_1")
    await repo.mark_event_processed("evt_test_1", "A")
    assert await repo.is_event_processed("evt_test_1")


@pytest.mark.asyncio
async def test_commands_and_status(repo):
    config = JunctionConfig(junction_id="A")
    await repo.save_junction_config(config)

    cmd = PendingCommand(
        command_id="cmd-999",
        junction_id="A",
        desired_signals=SignalState(north=SignalColor.GREEN),
        created_at=2000.0,
    )
    await repo.save_command(cmd, status="PENDING")

    pending = await repo.load_pending_commands("A")
    assert len(pending) == 1
    assert pending[0].command_id == "cmd-999"

    await repo.update_command_status("cmd-999", "ACKED")
    pending_after = await repo.load_pending_commands("A")
    assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_emergency_and_manual_queues(repo):
    config = JunctionConfig(junction_id="A")
    await repo.save_junction_config(config)

    emg = EmergencyEntry(
        vehicle_id="EMG-911",
        direction=Direction.EAST,
        phase=Phase.EAST_WEST,
        arrived_at=3000.0,
        stale_timeout=3060.0,
    )
    await repo.save_emergency_entry(emg, "A")
    emgs = await repo.load_emergency_queue("A")
    assert len(emgs) == 1
    assert emgs[0].vehicle_id == "EMG-911"

    man = ManualRequest(
        command_id="man-101",
        direction=Direction.WEST,
        phase=Phase.EAST_WEST,
        created_at=3000.0,
        ttl=120.0,
    )
    await repo.save_manual_request(man, "A")
    mans = await repo.load_manual_queue("A")
    assert len(mans) == 1
    assert mans[0].command_id == "man-101"


@pytest.mark.asyncio
async def test_audit_log_persistence(repo):
    config = JunctionConfig(junction_id="A")
    await repo.save_junction_config(config)

    entry = AuditEntry(
        event_type=AuditEventType.MODE_CHANGE,
        junction_id="A",
        timestamp=4000.0,
        previous_state="AUTOMATIC",
        new_state="EMERGENCY",
        reason="Ambulance arrived",
    )
    await repo.save_audit_entry(entry)

    logs = await repo.load_audit_log("A", limit=10)
    assert len(logs) == 1
    assert logs[0].event_type == AuditEventType.MODE_CHANGE
    assert logs[0].reason == "Ambulance arrived"


@pytest.mark.asyncio
async def test_device_status_persistence(repo):
    config = JunctionConfig(junction_id="A")
    await repo.save_junction_config(config)

    await repo.save_device_status(
        device_id="sig-A-ctrl",
        junction_id="A",
        device_type="SIGNAL_CONTROLLER",
        status="ONLINE",
        updated_at=5000.0,
    )

    statuses = await repo.load_device_statuses("A")
    assert len(statuses) == 1
    assert statuses[0]["device_id"] == "sig-A-ctrl"
    assert statuses[0]["status"] == "ONLINE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
