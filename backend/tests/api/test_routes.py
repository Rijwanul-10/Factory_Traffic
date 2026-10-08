"""
Phase 6 Tests: FastAPI REST API Integration Tests.

Validates all HTTP endpoints, status codes, input validations, and error responses.
"""

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from backend.api.main import create_app
from backend.domain.types import JunctionConfig


@pytest.fixture
def client():
    # Use temporary database for API tests
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    os.environ["DB_PATH"] = db_path
    os.environ["AUTO_ACK"] = "true"

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            pass


def test_root_health_check(client):
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "RUNNING"


def test_list_junctions(client):
    res = client.get("/api/junctions")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert any(j["junction_id"] == "A" for j in data)


def test_get_junction_config(client):
    res = client.get("/api/junctions/A")
    assert res.status_code == 200
    data = res.json()
    assert data["junction_id"] == "A"
    assert "green_duration" in data
    assert data["time_scale"] == 2.0


def test_get_junction_not_found(client):
    res = client.get("/api/junctions/NON_EXISTENT")
    assert res.status_code == 404


def test_get_junction_status(client):
    res = client.get("/api/junctions/A/status")
    assert res.status_code == 200
    data = res.json()
    assert data["junction_id"] == "A"
    assert data["mode"] in ("AUTOMATIC", "MANUAL", "EMERGENCY", "FAILURE")
    assert "desired_signals" in data
    assert "actual_signals" in data
    assert "queues" in data
    assert "alerts" in data


def test_submit_sensor_event_arrival_and_duplicate(client):
    # 1. Valid arrival
    event_payload = {
        "event_id": "api-evt-001",
        "junction_id": "A",
        "direction": "NORTH",
        "event_type": "VEHICLE_ARRIVED",
        "vehicle_id": "VH-API-1",
        "vehicle_type": "TRUCK",
        "sequence_no": 1,
        "timestamp": "2026-10-08T10:00:00Z",
    }
    res1 = client.post("/api/sensor-events", json=event_payload)
    assert res1.status_code == 200
    assert res1.json()["status"] == "ACCEPTED"

    # Queue count should increase
    status_res = client.get("/api/junctions/A/status")
    assert status_res.json()["queues"]["NORTH"] >= 1

    # 2. Resubmit exact duplicate event_id
    res2 = client.post("/api/sensor-events", json=event_payload)
    assert res2.status_code == 200
    assert res2.json()["status"] == "DUPLICATE"


def test_submit_sensor_event_clearance(client):
    # Arrival
    client.post("/api/sensor-events", json={
        "event_id": "api-evt-clr-arr",
        "junction_id": "A",
        "direction": "EAST",
        "event_type": "VEHICLE_ARRIVED",
        "vehicle_id": "VH-CLR-1",
        "vehicle_type": "FORKLIFT",
        "sequence_no": 2,
    })

    # Clearance
    res_clr = client.post("/api/sensor-events", json={
        "event_id": "api-evt-clr-done",
        "junction_id": "A",
        "direction": "EAST",
        "event_type": "VEHICLE_CLEARED",
        "vehicle_id": "VH-CLR-1",
        "sequence_no": 3,
    })
    assert res_clr.status_code == 200
    assert res_clr.json()["status"] == "ACCEPTED"


def test_sensor_event_validation_errors(client):
    # Missing vehicle_type for VEHICLE_ARRIVED -> 422
    res1 = client.post("/api/sensor-events", json={
        "event_id": "err-1",
        "junction_id": "A",
        "direction": "NORTH",
        "event_type": "VEHICLE_ARRIVED",
        "vehicle_id": "V-ERR",
    })
    assert res1.status_code == 422

    # Unknown junction -> 404
    res2 = client.post("/api/sensor-events", json={
        "event_id": "err-2",
        "junction_id": "Z",
        "direction": "NORTH",
        "event_type": "VEHICLE_ARRIVED",
        "vehicle_id": "V-ERR",
        "vehicle_type": "TRUCK",
    })
    assert res2.status_code == 404

    # Invalid direction -> 422
    res3 = client.post("/api/sensor-events", json={
        "event_id": "err-3",
        "junction_id": "A",
        "direction": "NORTHEAST",
        "event_type": "VEHICLE_ARRIVED",
        "vehicle_id": "V-ERR",
        "vehicle_type": "TRUCK",
    })
    assert res3.status_code == 422

    # Invalid vehicle_type -> 422
    res4 = client.post("/api/sensor-events", json={
        "event_id": "err-4",
        "junction_id": "A",
        "direction": "NORTH",
        "event_type": "VEHICLE_ARRIVED",
        "vehicle_id": "V-ERR",
        "vehicle_type": "SPACESHIP",
    })
    assert res4.status_code == 422


def test_manual_commands(client):
    # Manual green request -> 202
    res_man = client.post("/api/junctions/A/commands", json={
        "command": "MANUAL_GREEN_REQUEST",
        "direction": "WEST",
    })
    assert res_man.status_code == 202
    data = res_man.json()
    assert "command_id" in data
    assert data["status"] in ("QUEUED", "ACTIVE")

    # Return to automatic -> 202
    res_ret = client.post("/api/junctions/A/commands", json={
        "command": "RETURN_TO_AUTOMATIC",
    })
    assert res_ret.status_code == 202
    assert res_ret.json()["status"] == "COMPLETED"


def test_manual_command_missing_direction(client):
    res = client.post("/api/junctions/A/commands", json={
        "command": "MANUAL_GREEN_REQUEST",
    })
    assert res.status_code == 422


def test_controller_events(client):
    # Controller status OFFLINE
    res_off = client.post("/api/controller-events", json={
        "junction_id": "A",
        "status": "OFFLINE",
        "device_type": "SIGNAL_CONTROLLER",
    })
    assert res_off.status_code == 200

    # Status check should show failure / offline
    res_status = client.get("/api/junctions/A/status")
    assert res_status.json()["controller_status"] == "OFFLINE"

    # Controller status ONLINE
    res_on = client.post("/api/controller-events", json={
        "junction_id": "A",
        "status": "ONLINE",
        "device_type": "SIGNAL_CONTROLLER",
    })
    assert res_on.status_code == 200

    # Submit ACK
    res_ack = client.post("/api/controller-events", json={
        "junction_id": "A",
        "status": "ACK",
        "command_id": "cmd-test-fake",
        "actual_state": {"NORTH": "RED", "SOUTH": "RED", "EAST": "RED", "WEST": "RED"},
    })
    assert res_ack.status_code == 200


def test_get_junction_history(client):
    res = client.get("/api/junctions/A/history?limit=10")
    assert res.status_code == 200
    logs = res.json()
    assert isinstance(logs, list)
    assert len(logs) > 0
    assert "event_type" in logs[0]
    assert "timestamp" in logs[0]


def test_create_junction_b(client):
    res = client.post("/api/junctions", json={
        "junction_id": "B",
        "name": "Junction B Warehouse Entrance",
        "directions": ["NORTH", "SOUTH", "EAST", "WEST"],
        "phases": ["NORTH_SOUTH", "EAST_WEST"],
        "green_duration": 25.0,
        "time_scale": 2.0,
    })
    assert res.status_code == 201
    assert res.json()["junction_id"] == "B"

    # Verify B is listed
    list_res = client.get("/api/junctions")
    assert any(j["junction_id"] == "B" for j in list_res.json())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
