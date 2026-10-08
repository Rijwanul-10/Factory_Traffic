"""
Domain types for Factory Traffic Management System.
Pure Python enums, dataclasses, and value objects.
No imports of FastAPI, DB, HTTP, or MQTT.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ─── Enumerations ───────────────────────────────────────────────────────────

class Direction(str, Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"


class Phase(str, Enum):
    NORTH_SOUTH = "NORTH_SOUTH"
    EAST_WEST = "EAST_WEST"


class SignalColor(str, Enum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"
    UNKNOWN = "UNKNOWN"


class JunctionMode(str, Enum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"
    EMERGENCY = "EMERGENCY"
    FAILURE = "FAILURE"


class VehicleType(str, Enum):
    FORKLIFT = "FORKLIFT"
    TRUCK = "TRUCK"
    EMPLOYEE_VEHICLE = "EMPLOYEE_VEHICLE"
    EMERGENCY = "EMERGENCY"


class EventType(str, Enum):
    VEHICLE_ARRIVED = "VEHICLE_ARRIVED"
    VEHICLE_CLEARED = "VEHICLE_CLEARED"


class CommandType(str, Enum):
    MANUAL_GREEN_REQUEST = "MANUAL_GREEN_REQUEST"
    RETURN_TO_AUTOMATIC = "RETURN_TO_AUTOMATIC"


class CommandStatus(str, Enum):
    QUEUED = "QUEUED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ControllerStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class TransitionStep(str, Enum):
    """Internal step in a signal transition sequence."""
    YELLOW = "YELLOW"
    ALL_RED = "ALL_RED"
    GREEN = "GREEN"


class EventProcessingResult(str, Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    ORPHAN = "ORPHAN"
    REJECTED = "REJECTED"
    STALE = "STALE"


class AuditEventType(str, Enum):
    VEHICLE_ARRIVED = "VEHICLE_ARRIVED"
    VEHICLE_CLEARED = "VEHICLE_CLEARED"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    ORPHAN_CLEARED = "ORPHAN_CLEARED"
    STALE_EVENT = "STALE_EVENT"
    PHASE_CHANGE = "PHASE_CHANGE"
    MODE_CHANGE = "MODE_CHANGE"
    SIGNAL_CHANGE = "SIGNAL_CHANGE"
    EMERGENCY_ENTER = "EMERGENCY_ENTER"
    EMERGENCY_CLEAR = "EMERGENCY_CLEAR"
    EMERGENCY_STALE = "EMERGENCY_STALE"
    MANUAL_REQUEST = "MANUAL_REQUEST"
    MANUAL_HOLD_START = "MANUAL_HOLD_START"
    MANUAL_HOLD_END = "MANUAL_HOLD_END"
    MANUAL_CANCELLED = "MANUAL_CANCELLED"
    RETURN_TO_AUTOMATIC = "RETURN_TO_AUTOMATIC"
    COMMAND_SENT = "COMMAND_SENT"
    COMMAND_ACK = "COMMAND_ACK"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_RETRY = "COMMAND_RETRY"
    COMMAND_SUPERSEDED = "COMMAND_SUPERSEDED"
    CONTROLLER_ONLINE = "CONTROLLER_ONLINE"
    CONTROLLER_OFFLINE = "CONTROLLER_OFFLINE"
    SENSOR_OFFLINE = "SENSOR_OFFLINE"
    FAILURE_ENTER = "FAILURE_ENTER"
    FAILURE_RECOVER = "FAILURE_RECOVER"
    RESTART_RECOVERY = "RESTART_RECOVERY"
    STARVATION_OVERRIDE = "STARVATION_OVERRIDE"
    PHASE_SKIP_EMPTY = "PHASE_SKIP_EMPTY"
    GRACE_EARLY_END = "GRACE_EARLY_END"
    GREEN_EXTEND = "GREEN_EXTEND"


# ─── Lookup helpers ─────────────────────────────────────────────────────────

PHASE_DIRECTIONS: dict[Phase, tuple[Direction, Direction]] = {
    Phase.NORTH_SOUTH: (Direction.NORTH, Direction.SOUTH),
    Phase.EAST_WEST: (Direction.EAST, Direction.WEST),
}

DIRECTION_PHASE: dict[Direction, Phase] = {
    Direction.NORTH: Phase.NORTH_SOUTH,
    Direction.SOUTH: Phase.NORTH_SOUTH,
    Direction.EAST: Phase.EAST_WEST,
    Direction.WEST: Phase.EAST_WEST,
}

CONFLICTING_PHASE: dict[Phase, Phase] = {
    Phase.NORTH_SOUTH: Phase.EAST_WEST,
    Phase.EAST_WEST: Phase.NORTH_SOUTH,
}

VEHICLE_PRIORITY_WEIGHT: dict[VehicleType, int] = {
    VehicleType.TRUCK: 3,
    VehicleType.FORKLIFT: 2,
    VehicleType.EMPLOYEE_VEHICLE: 1,
    VehicleType.EMERGENCY: 10,  # Emergency gets highest weight
}


# ─── Value objects / data classes ───────────────────────────────────────────

@dataclass(frozen=True)
class SignalState:
    """Signals for all four directions at a junction."""
    north: SignalColor = SignalColor.RED
    south: SignalColor = SignalColor.RED
    east: SignalColor = SignalColor.RED
    west: SignalColor = SignalColor.RED

    def as_dict(self) -> dict[str, str]:
        return {
            "NORTH": self.north.value,
            "SOUTH": self.south.value,
            "EAST": self.east.value,
            "WEST": self.west.value,
        }

    def get(self, direction: Direction) -> SignalColor:
        return getattr(self, direction.value.lower())

    def with_direction(self, direction: Direction, color: SignalColor) -> "SignalState":
        d = {
            "north": self.north, "south": self.south,
            "east": self.east, "west": self.west,
        }
        d[direction.value.lower()] = color
        return SignalState(**d)

    def with_phase(self, phase: Phase, color: SignalColor) -> "SignalState":
        result = self
        for d in PHASE_DIRECTIONS[phase]:
            result = result.with_direction(d, color)
        return result

    @staticmethod
    def all_red() -> "SignalState":
        return SignalState()

    @staticmethod
    def all_unknown() -> "SignalState":
        return SignalState(
            north=SignalColor.UNKNOWN,
            south=SignalColor.UNKNOWN,
            east=SignalColor.UNKNOWN,
            west=SignalColor.UNKNOWN,
        )

    def has_conflict(self) -> bool:
        """Check if conflicting phases are both GREEN."""
        ns_green = self.north == SignalColor.GREEN or self.south == SignalColor.GREEN
        ew_green = self.east == SignalColor.GREEN or self.west == SignalColor.GREEN
        return ns_green and ew_green


@dataclass(frozen=True)
class SensorEvent:
    event_id: str
    junction_id: str
    direction: Direction
    event_type: EventType
    vehicle_id: str
    vehicle_type: Optional[VehicleType] = None  # Required for ARRIVED, optional for CLEARED
    sequence_no: int = 0
    timestamp: float = 0.0  # sensor timestamp (unix)
    server_timestamp: float = 0.0  # server-received timestamp (unix)


@dataclass(frozen=True)
class WaitingVehicle:
    vehicle_id: str
    junction_id: str
    direction: Direction
    vehicle_type: VehicleType
    arrived_at: float  # server timestamp when arrived
    sequence_no: int = 0


@dataclass
class EmergencyEntry:
    vehicle_id: str
    direction: Direction
    phase: Phase
    arrived_at: float
    stale_timeout: float  # absolute time when it becomes stale


@dataclass
class ManualRequest:
    command_id: str
    direction: Direction
    phase: Phase
    created_at: float
    ttl: float = 120.0  # seconds before expiry
    status: CommandStatus = CommandStatus.QUEUED

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl


@dataclass
class PendingCommand:
    command_id: str
    junction_id: str
    desired_signals: SignalState
    created_at: float
    retries: int = 0
    max_retries: int = 2
    ack_timeout: float = 3.0  # real seconds


@dataclass
class AuditEntry:
    event_type: AuditEventType
    junction_id: str
    timestamp: float
    direction: Optional[Direction] = None
    previous_state: Optional[str] = None
    new_state: Optional[str] = None
    command_id: Optional[str] = None
    reason: str = ""
    vehicle_id: Optional[str] = None
    event_id: Optional[str] = None

    def __post_init__(self):
        if not hasattr(self, 'id'):
            self.id = str(uuid.uuid4())


@dataclass(frozen=True)
class JunctionConfig:
    """Configuration for a junction. Data, not code."""
    junction_id: str
    directions: tuple[Direction, ...] = (Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST)
    phases: tuple[Phase, ...] = (Phase.NORTH_SOUTH, Phase.EAST_WEST)
    green_duration: float = 30.0       # nominal seconds
    min_green: float = 5.0             # nominal seconds
    max_green: float = 60.0            # nominal seconds
    yellow_duration: float = 5.0       # nominal seconds
    all_red_duration: float = 2.0      # nominal seconds
    time_scale: float = 2.0            # real = nominal / time_scale
    manual_hold: float = 10.0          # nominal seconds
    emergency_stale_timeout: float = 60.0  # nominal seconds
    max_wait: float = 90.0             # nominal seconds
    grace_empty: float = 2.0           # nominal seconds
    ack_timeout: float = 3.0           # real seconds (not scaled)
    ack_retries: int = 2

    def real_seconds(self, nominal: float) -> float:
        """Convert nominal seconds to real seconds."""
        return nominal / self.time_scale
