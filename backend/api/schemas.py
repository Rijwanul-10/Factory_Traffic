"""
Pydantic schemas for the FastAPI REST API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


class JunctionCreateRequest(BaseModel):
    junction_id: str = Field(..., description="Unique junction identifier, e.g. 'A', 'B'")
    name: Optional[str] = Field(None, description="Human readable name")
    directions: list[str] = Field(
        default=["NORTH", "SOUTH", "EAST", "WEST"],
        description="Supported traffic directions",
    )
    phases: list[str] = Field(
        default=["NORTH_SOUTH", "EAST_WEST"],
        description="Supported traffic phases",
    )
    green_duration: float = Field(30.0, ge=5.0, description="Nominal green duration in seconds")
    min_green: float = Field(5.0, ge=1.0, description="Nominal minimum green duration")
    max_green: float = Field(60.0, ge=10.0, description="Nominal maximum green duration")
    yellow_duration: float = Field(5.0, ge=1.0, description="Nominal yellow duration")
    all_red_duration: float = Field(2.0, ge=0.5, description="Nominal all-red clearance duration")
    time_scale: float = Field(2.0, gt=0.0, description="Time scale factor (e.g. 2.0 = 2x speed)")
    manual_hold: float = Field(10.0, ge=2.0, description="Nominal manual green hold duration")
    emergency_stale_timeout: float = Field(60.0, ge=10.0, description="Nominal emergency stale timeout")
    max_wait: float = Field(90.0, ge=10.0, description="Nominal maximum waiting time before starvation override")
    grace_empty: float = Field(2.0, ge=0.5, description="Nominal grace empty wait duration")
    ack_timeout: float = Field(3.0, ge=0.5, description="Controller ACK timeout in real seconds")
    ack_retries: int = Field(2, ge=0, description="Max retries before controller marked DEGRADED/OFFLINE")


class SensorEventRequest(BaseModel):
    event_id: str = Field(..., description="Unique event identifier")
    junction_id: str = Field(..., description="Target junction ID, e.g. 'A'")
    direction: str = Field(..., description="Direction: NORTH, SOUTH, EAST, WEST")
    event_type: str = Field(..., description="Event type: VEHICLE_ARRIVED, VEHICLE_CLEARED")
    vehicle_id: str = Field(..., description="Unique vehicle identifier")
    vehicle_type: Optional[str] = Field(
        None,
        description="Vehicle type (required for ARRIVED): FORKLIFT, TRUCK, EMPLOYEE_VEHICLE, EMERGENCY",
    )
    sequence_no: int = Field(0, ge=0, description="Sequence number per direction for ordering")
    timestamp: Optional[Any] = Field(None, description="Sensor timestamp (ISO string or unix timestamp)")

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        valid = {"NORTH", "SOUTH", "EAST", "WEST"}
        u = v.upper()
        if u not in valid:
            raise ValueError(f"Invalid direction '{v}'. Must be one of {sorted(valid)}")
        return u

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        valid = {"VEHICLE_ARRIVED", "VEHICLE_CLEARED"}
        u = v.upper()
        if u not in valid:
            raise ValueError(f"Invalid event_type '{v}'. Must be one of {sorted(valid)}")
        return u

    @field_validator("vehicle_type")
    @classmethod
    def validate_vehicle_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        valid = {"FORKLIFT", "TRUCK", "EMPLOYEE_VEHICLE", "EMERGENCY"}
        u = v.upper()
        if u not in valid:
            raise ValueError(f"Invalid vehicle_type '{v}'. Must be one of {sorted(valid)}")
        return u


class SensorEventResponse(BaseModel):
    event_id: str
    status: str
    message: str


class ManualCommandRequest(BaseModel):
    command: str = Field(
        ...,
        description="Command type: MANUAL_GREEN_REQUEST or RETURN_TO_AUTOMATIC",
    )
    direction: Optional[str] = Field(
        None,
        description="Direction for MANUAL_GREEN_REQUEST: NORTH, SOUTH, EAST, WEST",
    )

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: str) -> str:
        valid = {"MANUAL_GREEN_REQUEST", "RETURN_TO_AUTOMATIC"}
        u = v.upper()
        if u not in valid:
            raise ValueError(f"Invalid command '{v}'. Must be one of {sorted(valid)}")
        return u

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        valid = {"NORTH", "SOUTH", "EAST", "WEST"}
        u = v.upper()
        if u not in valid:
            raise ValueError(f"Invalid direction '{v}'. Must be one of {sorted(valid)}")
        return u


class ManualCommandResponse(BaseModel):
    command_id: str
    status: str
    message: str


class ControllerEventRequest(BaseModel):
    command_id: Optional[str] = Field(None, description="Command ID being acknowledged")
    junction_id: str = Field(..., description="Target junction ID")
    status: str = Field(
        ...,
        description="Status: ACK, ONLINE, OFFLINE, DEGRADED",
    )
    actual_state: Optional[dict[str, str]] = Field(
        None,
        description="Actual signal state confirmation, e.g. {'NORTH':'GREEN', ...}",
    )
    device_type: Optional[str] = Field(
        None,
        description="Device type: SIGNAL_CONTROLLER or SENSOR",
    )
    direction: Optional[str] = Field(
        None,
        description="Direction if device is a sensor: NORTH, SOUTH, EAST, WEST",
    )


class ControllerEventResponse(BaseModel):
    status: str
    message: str


class JunctionStatusResponse(BaseModel):
    junction_id: str
    mode: str
    phase: str
    controller_status: str
    desired_signals: dict[str, str]
    actual_signals: dict[str, str]
    queues: dict[str, int]
    emergency_queue: list[dict[str, Any]]
    active_emergency: Optional[dict[str, Any]]
    manual_queue: list[dict[str, Any]]
    active_manual: Optional[dict[str, Any]]
    pending_commands: list[dict[str, Any]]
    alerts: list[str]
    countdown: Optional[float]
    in_transition: bool
    current_step: Optional[str]
    time_scale: float


class AuditEntryResponse(BaseModel):
    id: str
    event_type: str
    junction_id: str
    timestamp: float
    direction: Optional[str] = None
    previous_state: Optional[str] = None
    new_state: Optional[str] = None
    command_id: Optional[str] = None
    reason: str = ""
    vehicle_id: Optional[str] = None
    event_id: Optional[str] = None
