"""
Port interfaces for the Factory Traffic Management System.
These define the boundaries between the domain and the outside world.
"""

from __future__ import annotations

from typing import Optional, Protocol

from ..domain.types import (
    AuditEntry,
    PendingCommand,
    SignalState,
    WaitingVehicle,
    EmergencyEntry,
    ManualRequest,
    JunctionMode,
    Phase,
    ControllerStatus,
)


class ControllerPort(Protocol):
    """Interface for sending commands to a physical controller."""

    async def send_command(self, command: PendingCommand) -> None:
        """Send a signal command to the controller. ACK comes asynchronously."""
        ...


class Repository(Protocol):
    """Interface for persistence."""

    # Junction state
    async def save_junction_state(self, junction_id: str, state: dict) -> None:
        ...

    async def load_junction_state(self, junction_id: str) -> Optional[dict]:
        ...

    # Waiting vehicles
    async def save_waiting_vehicle(self, vehicle: WaitingVehicle) -> None:
        ...

    async def remove_waiting_vehicle(self, vehicle_id: str) -> None:
        ...

    async def load_waiting_vehicles(self, junction_id: str) -> list[WaitingVehicle]:
        ...

    # Processed events
    async def mark_event_processed(self, event_id: str, junction_id: str) -> None:
        ...

    async def is_event_processed(self, event_id: str) -> bool:
        ...

    # Commands
    async def save_command(self, command: PendingCommand) -> None:
        ...

    async def update_command_status(self, command_id: str, status: str) -> None:
        ...

    async def load_pending_commands(self, junction_id: str) -> list[PendingCommand]:
        ...

    # Emergency queue
    async def save_emergency_entry(self, entry: EmergencyEntry, junction_id: str) -> None:
        ...

    async def remove_emergency_entry(self, vehicle_id: str, junction_id: str) -> None:
        ...

    async def load_emergency_queue(self, junction_id: str) -> list[EmergencyEntry]:
        ...

    # Manual queue
    async def save_manual_request(self, request: ManualRequest, junction_id: str) -> None:
        ...

    async def remove_manual_request(self, command_id: str, junction_id: str) -> None:
        ...

    async def load_manual_queue(self, junction_id: str) -> list[ManualRequest]:
        ...

    # Audit log
    async def save_audit_entry(self, entry: AuditEntry) -> None:
        ...

    async def load_audit_log(self, junction_id: str, limit: int = 100) -> list[AuditEntry]:
        ...
