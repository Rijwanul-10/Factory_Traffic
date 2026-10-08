"""
SqliteRepository - SQLite persistence adapter for Factory Traffic Management System.

Implements Repository interface from backend.ports.interfaces.
Handles:
- Schema initialization
- State snapshots
- Waiting vehicles queue
- Event idempotency tracking
- Command history and states
- Emergency and manual queues
- Audit log persistence and retrieval
- Restart recovery loading
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from typing import Any, Optional

from ..domain.types import (
    AuditEntry,
    AuditEventType,
    CommandStatus,
    ControllerStatus,
    Direction,
    EmergencyEntry,
    JunctionConfig,
    JunctionMode,
    ManualRequest,
    PendingCommand,
    Phase,
    SignalColor,
    SignalState,
    TransitionStep,
    VehicleType,
    WaitingVehicle,
)
from ..ports.interfaces import Repository


class SqliteRepository(Repository):
    """
    SQLite repository implementing asynchronous persistence operations.
    Runs sqlite3 operations in asyncio executor to ensure thread-safety and non-blocking I/O.
    """

    def __init__(self, db_path: str = ":memory:"):
        import threading
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON;")
        return self._conn

    def close(self) -> None:
        """Close database connection and release file locks."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _init_db(self) -> None:
        """Run schema.sql to ensure all tables exist."""
        schema_file = os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")
        if os.path.exists(schema_file):
            with open(schema_file, "r", encoding="utf-8") as f:
                schema_sql = f.read()
        else:
            raise FileNotFoundError(f"schema.sql not found at {schema_file}")

        with self._get_connection() as conn:
            conn.executescript(schema_sql)
            conn.commit()

    async def _run(self, func, *args, **kwargs) -> Any:
        """Run a synchronous sqlite function in the asyncio default thread executor with lock."""
        loop = asyncio.get_running_loop()
        def locked_call():
            with self._lock:
                return func(*args, **kwargs)
        return await loop.run_in_executor(None, locked_call)

    # ── Junction Config ──

    def _save_junction_config_sync(self, config: JunctionConfig) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO junctions (
                    junction_id, name, directions, phases, green_duration,
                    min_green, max_green, yellow_duration, all_red_duration,
                    time_scale, manual_hold, emergency_stale_timeout, max_wait,
                    grace_empty, ack_timeout, ack_retries, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config.junction_id,
                    f"Junction {config.junction_id}",
                    json.dumps([d.value for d in config.directions]),
                    json.dumps([p.value for p in config.phases]),
                    config.green_duration,
                    config.min_green,
                    config.max_green,
                    config.yellow_duration,
                    config.all_red_duration,
                    config.time_scale,
                    config.manual_hold,
                    config.emergency_stale_timeout,
                    config.max_wait,
                    config.grace_empty,
                    config.ack_timeout,
                    config.ack_retries,
                    0.0,
                ),
            )
            conn.commit()

    async def save_junction_config(self, config: JunctionConfig) -> None:
        await self._run(self._save_junction_config_sync, config)

    def _load_junction_config_sync(self, junction_id: str) -> Optional[JunctionConfig]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM junctions WHERE junction_id = ?", (junction_id,))
            row = cur.fetchone()
            if not row:
                return None
            return JunctionConfig(
                junction_id=row["junction_id"],
                directions=tuple(Direction(d) for d in json.loads(row["directions"])),
                phases=tuple(Phase(p) for p in json.loads(row["phases"])),
                green_duration=row["green_duration"],
                min_green=row["min_green"],
                max_green=row["max_green"],
                yellow_duration=row["yellow_duration"],
                all_red_duration=row["all_red_duration"],
                time_scale=row["time_scale"],
                manual_hold=row["manual_hold"],
                emergency_stale_timeout=row["emergency_stale_timeout"],
                max_wait=row["max_wait"],
                grace_empty=row["grace_empty"],
                ack_timeout=row["ack_timeout"],
                ack_retries=row["ack_retries"],
            )

    async def load_junction_config(self, junction_id: str) -> Optional[JunctionConfig]:
        return await self._run(self._load_junction_config_sync, junction_id)

    # ── Junction State ──

    def _save_junction_state_sync(self, junction_id: str, state: dict) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO junction_state (
                    junction_id, mode, phase, desired_signals, actual_signals,
                    controller_status, in_transition, current_step, green_start_time,
                    step_start_time, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    junction_id,
                    state.get("mode", "AUTOMATIC"),
                    state.get("phase", "NORTH_SOUTH"),
                    json.dumps(state.get("desired_signals", {})),
                    json.dumps(state.get("actual_signals", {})),
                    state.get("controller_status", "ONLINE"),
                    1 if state.get("in_transition") else 0,
                    state.get("current_step"),
                    state.get("green_start_time", 0.0),
                    state.get("step_start_time", 0.0),
                    state.get("updated_at", 0.0),
                ),
            )
            conn.commit()

    async def save_junction_state(self, junction_id: str, state: dict) -> None:
        await self._run(self._save_junction_state_sync, junction_id, state)

    def _load_junction_state_sync(self, junction_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM junction_state WHERE junction_id = ?", (junction_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "junction_id": row["junction_id"],
                "mode": row["mode"],
                "phase": row["phase"],
                "desired_signals": json.loads(row["desired_signals"]),
                "actual_signals": json.loads(row["actual_signals"]),
                "controller_status": row["controller_status"],
                "in_transition": bool(row["in_transition"]),
                "current_step": row["current_step"],
                "green_start_time": row["green_start_time"],
                "step_start_time": row["step_start_time"],
                "updated_at": row["updated_at"],
            }

    async def load_junction_state(self, junction_id: str) -> Optional[dict]:
        return await self._run(self._load_junction_state_sync, junction_id)

    # ── Waiting Vehicles ──

    def _save_waiting_vehicle_sync(self, vehicle: WaitingVehicle) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO waiting_vehicles (
                    vehicle_id, junction_id, direction, vehicle_type, arrived_at, sequence_no
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    vehicle.vehicle_id,
                    vehicle.junction_id,
                    vehicle.direction.value,
                    vehicle.vehicle_type.value,
                    vehicle.arrived_at,
                    vehicle.sequence_no,
                ),
            )
            conn.commit()

    async def save_waiting_vehicle(self, vehicle: WaitingVehicle) -> None:
        await self._run(self._save_waiting_vehicle_sync, vehicle)

    def _remove_waiting_vehicle_sync(self, vehicle_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM waiting_vehicles WHERE vehicle_id = ?", (vehicle_id,))
            conn.commit()

    async def remove_waiting_vehicle(self, vehicle_id: str) -> None:
        await self._run(self._remove_waiting_vehicle_sync, vehicle_id)

    def _load_waiting_vehicles_sync(self, junction_id: str) -> list[WaitingVehicle]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM waiting_vehicles WHERE junction_id = ? ORDER BY arrived_at ASC",
                (junction_id,),
            )
            results = []
            for row in cur.fetchall():
                results.append(
                    WaitingVehicle(
                        vehicle_id=row["vehicle_id"],
                        junction_id=row["junction_id"],
                        direction=Direction(row["direction"]),
                        vehicle_type=VehicleType(row["vehicle_type"]),
                        arrived_at=row["arrived_at"],
                        sequence_no=row["sequence_no"],
                    )
                )
            return results

    async def load_waiting_vehicles(self, junction_id: str) -> list[WaitingVehicle]:
        return await self._run(self._load_waiting_vehicles_sync, junction_id)

    # ── Processed Events (Idempotency) ──

    def _mark_event_processed_sync(self, event_id: str, junction_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_events (event_id, junction_id, processed_at) VALUES (?, ?, ?)",
                (event_id, junction_id, 0.0),
            )
            conn.commit()

    async def mark_event_processed(self, event_id: str, junction_id: str) -> None:
        await self._run(self._mark_event_processed_sync, event_id, junction_id)

    def _is_event_processed_sync(self, event_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,))
            return cur.fetchone() is not None

    async def is_event_processed(self, event_id: str) -> bool:
        return await self._run(self._is_event_processed_sync, event_id)

    # ── Commands ──

    def _save_command_sync(self, command: PendingCommand, status: str = "PENDING") -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO commands (
                    command_id, junction_id, desired_signals, status, retries,
                    max_retries, ack_timeout, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.command_id,
                    command.junction_id,
                    json.dumps(command.desired_signals.as_dict()),
                    status,
                    command.retries,
                    command.max_retries,
                    command.ack_timeout,
                    command.created_at,
                    command.created_at,
                ),
            )
            conn.commit()

    async def save_command(self, command: PendingCommand, status: str = "PENDING") -> None:
        await self._run(self._save_command_sync, command, status)

    def _update_command_status_sync(self, command_id: str, status: str) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE commands SET status = ? WHERE command_id = ?", (status, command_id))
            conn.commit()

    async def update_command_status(self, command_id: str, status: str) -> None:
        await self._run(self._update_command_status_sync, command_id, status)

    def _load_pending_commands_sync(self, junction_id: str) -> list[PendingCommand]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM commands WHERE junction_id = ? AND status = 'PENDING'",
                (junction_id,),
            )
            results = []
            for row in cur.fetchall():
                sig_dict = json.loads(row["desired_signals"])
                signals = SignalState(
                    north=SignalColor(sig_dict.get("NORTH", "RED")),
                    south=SignalColor(sig_dict.get("SOUTH", "RED")),
                    east=SignalColor(sig_dict.get("EAST", "RED")),
                    west=SignalColor(sig_dict.get("WEST", "RED")),
                )
                results.append(
                    PendingCommand(
                        command_id=row["command_id"],
                        junction_id=row["junction_id"],
                        desired_signals=signals,
                        created_at=row["created_at"],
                        retries=row["retries"],
                        max_retries=row["max_retries"],
                        ack_timeout=row["ack_timeout"],
                    )
                )
            return results

    async def load_pending_commands(self, junction_id: str) -> list[PendingCommand]:
        return await self._run(self._load_pending_commands_sync, junction_id)

    # ── Emergency Queue ──

    def _save_emergency_entry_sync(self, entry: EmergencyEntry, junction_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO emergency_queue (
                    vehicle_id, junction_id, direction, phase, arrived_at, stale_timeout
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.vehicle_id,
                    junction_id,
                    entry.direction.value,
                    entry.phase.value,
                    entry.arrived_at,
                    entry.stale_timeout,
                ),
            )
            conn.commit()

    async def save_emergency_entry(self, entry: EmergencyEntry, junction_id: str) -> None:
        await self._run(self._save_emergency_entry_sync, entry, junction_id)

    def _remove_emergency_entry_sync(self, vehicle_id: str, junction_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM emergency_queue WHERE vehicle_id = ? AND junction_id = ?",
                (vehicle_id, junction_id),
            )
            conn.commit()

    async def remove_emergency_entry(self, vehicle_id: str, junction_id: str) -> None:
        await self._run(self._remove_emergency_entry_sync, vehicle_id, junction_id)

    def _load_emergency_queue_sync(self, junction_id: str) -> list[EmergencyEntry]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM emergency_queue WHERE junction_id = ? ORDER BY arrived_at ASC",
                (junction_id,),
            )
            results = []
            for row in cur.fetchall():
                results.append(
                    EmergencyEntry(
                        vehicle_id=row["vehicle_id"],
                        direction=Direction(row["direction"]),
                        phase=Phase(row["phase"]),
                        arrived_at=row["arrived_at"],
                        stale_timeout=row["stale_timeout"],
                    )
                )
            return results

    async def load_emergency_queue(self, junction_id: str) -> list[EmergencyEntry]:
        return await self._run(self._load_emergency_queue_sync, junction_id)

    # ── Manual Queue ──

    def _save_manual_request_sync(self, request: ManualRequest, junction_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO manual_queue (
                    command_id, junction_id, direction, phase, status, created_at, ttl
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.command_id,
                    junction_id,
                    request.direction.value,
                    request.phase.value,
                    request.status.value,
                    request.created_at,
                    request.ttl,
                ),
            )
            conn.commit()

    async def save_manual_request(self, request: ManualRequest, junction_id: str) -> None:
        await self._run(self._save_manual_request_sync, request, junction_id)

    def _remove_manual_request_sync(self, command_id: str, junction_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM manual_queue WHERE command_id = ? AND junction_id = ?",
                (command_id, junction_id),
            )
            conn.commit()

    async def remove_manual_request(self, command_id: str, junction_id: str) -> None:
        await self._run(self._remove_manual_request_sync, command_id, junction_id)

    def _load_manual_queue_sync(self, junction_id: str) -> list[ManualRequest]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM manual_queue WHERE junction_id = ? ORDER BY created_at ASC",
                (junction_id,),
            )
            results = []
            for row in cur.fetchall():
                results.append(
                    ManualRequest(
                        command_id=row["command_id"],
                        direction=Direction(row["direction"]),
                        phase=Phase(row["phase"]),
                        created_at=row["created_at"],
                        ttl=row["ttl"],
                        status=CommandStatus(row["status"]),
                    )
                )
            return results

    async def load_manual_queue(self, junction_id: str) -> list[ManualRequest]:
        return await self._run(self._load_manual_queue_sync, junction_id)

    # ── Audit Log ──

    def _save_audit_entry_sync(self, entry: AuditEntry) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO audit_log (
                    id, event_type, junction_id, direction, previous_state,
                    new_state, command_id, reason, vehicle_id, event_id, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    getattr(entry, "id", str(uuid.uuid4())),
                    entry.event_type.value,
                    entry.junction_id,
                    entry.direction.value if entry.direction else None,
                    entry.previous_state,
                    entry.new_state,
                    entry.command_id,
                    entry.reason,
                    entry.vehicle_id,
                    entry.event_id,
                    entry.timestamp,
                ),
            )
            conn.commit()

    async def save_audit_entry(self, entry: AuditEntry) -> None:
        await self._run(self._save_audit_entry_sync, entry)

    def _load_audit_log_sync(self, junction_id: str, limit: int = 100) -> list[AuditEntry]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM audit_log WHERE junction_id = ? ORDER BY timestamp DESC LIMIT ?",
                (junction_id, limit),
            )
            results = []
            for row in cur.fetchall():
                entry = AuditEntry(
                    event_type=AuditEventType(row["event_type"]),
                    junction_id=row["junction_id"],
                    timestamp=row["timestamp"],
                    direction=Direction(row["direction"]) if row["direction"] else None,
                    previous_state=row["previous_state"],
                    new_state=row["new_state"],
                    command_id=row["command_id"],
                    reason=row["reason"] or "",
                    vehicle_id=row["vehicle_id"],
                    event_id=row["event_id"],
                )
                entry.id = row["id"]
                results.append(entry)
            return results

    async def load_audit_log(self, junction_id: str, limit: int = 100) -> list[AuditEntry]:
        return await self._run(self._load_audit_log_sync, junction_id, limit)

    # ── Device Status ──

    def _save_device_status_sync(
        self,
        device_id: str,
        junction_id: str,
        device_type: str,
        status: str,
        direction: Optional[str] = None,
        updated_at: float = 0.0,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO device_status (
                    device_id, junction_id, device_type, direction, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (device_id, junction_id, device_type, direction, status, updated_at),
            )
            conn.commit()

    async def save_device_status(
        self,
        device_id: str,
        junction_id: str,
        device_type: str,
        status: str,
        direction: Optional[str] = None,
        updated_at: float = 0.0,
    ) -> None:
        await self._run(
            self._save_device_status_sync,
            device_id,
            junction_id,
            device_type,
            status,
            direction,
            updated_at,
        )

    def _load_device_statuses_sync(self, junction_id: str) -> list[dict]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM device_status WHERE junction_id = ?",
                (junction_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    async def load_device_statuses(self, junction_id: str) -> list[dict]:
        return await self._run(self._load_device_statuses_sync, junction_id)
