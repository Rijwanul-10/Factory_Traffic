"""
FastAPI route definitions for Factory Traffic Management System.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Query, Request, status

from .schemas import (
    AuditEntryResponse,
    ControllerEventRequest,
    ControllerEventResponse,
    JunctionCreateRequest,
    JunctionStatusResponse,
    ManualCommandRequest,
    ManualCommandResponse,
    SensorEventRequest,
    SensorEventResponse,
)
from ..domain.actor import JunctionActor, JunctionRegistry
from ..domain.clock import RealClock
from ..domain.engine import JunctionEngine
from ..domain.types import (
    CommandStatus,
    CommandType,
    ControllerStatus,
    Direction,
    EventProcessingResult,
    EventType,
    JunctionConfig,
    Phase,
    SensorEvent,
    SignalColor,
    SignalState,
    VehicleType,
)
from ..ports.interfaces import Repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Traffic Management"])


def get_registry(request: Request) -> JunctionRegistry:
    return request.app.state.registry


def get_repository(request: Request) -> Repository:
    return request.app.state.repository


def parse_timestamp(ts: Any) -> float:
    """Parse timestamp into unix epoch seconds."""
    if ts is None:
        return time.time()
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            # Try float string
            return float(ts)
        except ValueError:
            pass
        try:
            # Try ISO string (support 'Z' suffix)
            clean_ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_ts)
            return dt.timestamp()
        except Exception:
            return time.time()
    return time.time()


# ─── 1. Junctions Endpoints ─────────────────────────────────────────────────

@router.get("/junctions", summary="List all junctions")
async def list_junctions(request: Request) -> list[dict]:
    registry = get_registry(request)
    result = []
    for jid in registry.all_junction_ids():
        actor = registry.get(jid)
        if actor:
            cfg = actor.engine.config
            result.append({
                "junction_id": cfg.junction_id,
                "directions": [d.value for d in cfg.directions],
                "phases": [p.value for p in cfg.phases],
                "time_scale": cfg.time_scale,
                "green_duration": cfg.green_duration,
            })
    return result


@router.post("/junctions", status_code=status.HTTP_201_CREATED, summary="Create a new junction")
async def create_junction(payload: JunctionCreateRequest, request: Request) -> dict:
    registry = get_registry(request)
    repo = get_repository(request)

    if registry.get(payload.junction_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Junction '{payload.junction_id}' already exists",
        )

    try:
        dirs = tuple(Direction(d) for d in payload.directions)
        phs = tuple(Phase(p) for p in payload.phases)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    config = JunctionConfig(
        junction_id=payload.junction_id,
        directions=dirs,
        phases=phs,
        green_duration=payload.green_duration,
        min_green=payload.min_green,
        max_green=payload.max_green,
        yellow_duration=payload.yellow_duration,
        all_red_duration=payload.all_red_duration,
        time_scale=payload.time_scale,
        manual_hold=payload.manual_hold,
        emergency_stale_timeout=payload.emergency_stale_timeout,
        max_wait=payload.max_wait,
        grace_empty=payload.grace_empty,
        ack_timeout=payload.ack_timeout,
        ack_retries=payload.ack_retries,
    )

    await repo.save_junction_config(config)

    engine = JunctionEngine(config=config, clock=RealClock())
    engine.initialize_green(Phase.NORTH_SOUTH)
    actor = JunctionActor(engine=engine, repository=repo)
    actor.start()
    registry.register(actor)

    return {"junction_id": config.junction_id, "message": "Junction created and started"}


@router.get("/junctions/{junction_id}", summary="Get junction configuration")
async def get_junction(junction_id: str, request: Request) -> dict:
    registry = get_registry(request)
    actor = registry.get(junction_id)
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Junction '{junction_id}' not found",
        )
    cfg = actor.engine.config
    return {
        "junction_id": cfg.junction_id,
        "directions": [d.value for d in cfg.directions],
        "phases": [p.value for p in cfg.phases],
        "green_duration": cfg.green_duration,
        "min_green": cfg.min_green,
        "max_green": cfg.max_green,
        "yellow_duration": cfg.yellow_duration,
        "all_red_duration": cfg.all_red_duration,
        "time_scale": cfg.time_scale,
        "manual_hold": cfg.manual_hold,
        "emergency_stale_timeout": cfg.emergency_stale_timeout,
        "max_wait": cfg.max_wait,
        "grace_empty": cfg.grace_empty,
        "ack_timeout": cfg.ack_timeout,
        "ack_retries": cfg.ack_retries,
    }


@router.get("/junctions/{junction_id}/status", response_model=JunctionStatusResponse, summary="Get full live junction status")
async def get_junction_status(junction_id: str, request: Request) -> JunctionStatusResponse:
    registry = get_registry(request)
    actor = registry.get(junction_id)
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Junction '{junction_id}' not found",
        )
    data = await actor.get_status()
    return JunctionStatusResponse(**data)


# ─── 2. Sensor Events ───────────────────────────────────────────────────────

@router.post("/sensor-events", response_model=SensorEventResponse, summary="Submit vehicle detection sensor event")
async def submit_sensor_event(payload: SensorEventRequest, request: Request) -> SensorEventResponse:
    registry = get_registry(request)
    actor = registry.get(payload.junction_id)
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Junction '{payload.junction_id}' not found",
        )

    # Validate that ARRIVED must specify vehicle_type
    if payload.event_type == "VEHICLE_ARRIVED" and not payload.vehicle_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="vehicle_type is required for VEHICLE_ARRIVED events",
        )

    sensor_ts = parse_timestamp(payload.timestamp)
    server_ts = time.time()

    event = SensorEvent(
        event_id=payload.event_id,
        junction_id=payload.junction_id,
        direction=Direction(payload.direction),
        event_type=EventType(payload.event_type),
        vehicle_id=payload.vehicle_id,
        vehicle_type=VehicleType(payload.vehicle_type) if payload.vehicle_type else None,
        sequence_no=payload.sequence_no,
        timestamp=sensor_ts,
        server_timestamp=server_ts,
    )

    result: EventProcessingResult = await actor.handle_sensor_event(event)

    return SensorEventResponse(
        event_id=payload.event_id,
        status=result.value,
        message=f"Event processed with result: {result.value}",
    )


# ─── 3. Manual / Control Commands ──────────────────────────────────────────

@router.post(
    "/junctions/{junction_id}/commands",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ManualCommandResponse,
    summary="Submit manual traffic control command",
)
async def submit_manual_command(
    junction_id: str, payload: ManualCommandRequest, request: Request
) -> ManualCommandResponse:
    registry = get_registry(request)
    actor = registry.get(junction_id)
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Junction '{junction_id}' not found",
        )

    cmd_type = CommandType(payload.command)
    direction = Direction(payload.direction) if payload.direction else None

    if cmd_type == CommandType.MANUAL_GREEN_REQUEST and not direction:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="direction is required for MANUAL_GREEN_REQUEST",
        )

    cmd_id, cmd_status = await actor.handle_manual_command(cmd_type, direction)

    return ManualCommandResponse(
        command_id=cmd_id,
        status=cmd_status.value,
        message=f"Command {cmd_type.value} result: {cmd_status.value}",
    )


# ─── 4. Controller Events / Acknowledgements ───────────────────────────────

@router.post(
    "/controller-events",
    response_model=ControllerEventResponse,
    summary="Submit controller event, acknowledgement, or status change",
)
async def submit_controller_event(
    payload: ControllerEventRequest, request: Request
) -> ControllerEventResponse:
    registry = get_registry(request)
    repo = get_repository(request)
    actor = registry.get(payload.junction_id)
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Junction '{payload.junction_id}' not found",
        )

    st_upper = payload.status.upper()

    if st_upper == "ACK":
        if not payload.command_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="command_id is required for ACK events",
            )
        actual_signals = None
        if payload.actual_state:
            actual_signals = SignalState(
                north=SignalColor(payload.actual_state.get("NORTH", "RED")),
                south=SignalColor(payload.actual_state.get("SOUTH", "RED")),
                east=SignalColor(payload.actual_state.get("EAST", "RED")),
                west=SignalColor(payload.actual_state.get("WEST", "RED")),
            )
        await actor.handle_ack(payload.command_id, actual_signals)

        # Update controller simulator if attached
        if actor.controller_port and hasattr(actor.controller_port, "mark_acked"):
            actor.controller_port.mark_acked(payload.command_id, actual_signals)

        return ControllerEventResponse(status="ACKED", message=f"Command {payload.command_id} acknowledged")

    elif st_upper in ("ONLINE", "OFFLINE", "DEGRADED"):
        c_status = ControllerStatus(st_upper)

        # Update controller simulator first so commands generated during status handling see new status
        if actor.controller_port and hasattr(actor.controller_port, "set_status"):
            actor.controller_port.set_status(c_status)

        await actor.handle_controller_status(c_status)

        # Persist device status
        dev_id = f"ctrl-{payload.junction_id}"
        await repo.save_device_status(
            device_id=dev_id,
            junction_id=payload.junction_id,
            device_type="SIGNAL_CONTROLLER",
            status=st_upper,
            updated_at=time.time(),
        )

        return ControllerEventResponse(status=st_upper, message=f"Controller status updated to {st_upper}")

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported status '{payload.status}'. Must be ACK, ONLINE, OFFLINE, or DEGRADED",
    )


# ─── 5. History / Audit Log ─────────────────────────────────────────────────

@router.get(
    "/junctions/{junction_id}/history",
    response_model=list[AuditEntryResponse],
    summary="Get recent audit log history for a junction",
)
async def get_junction_history(
    junction_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="Max entries to return"),
) -> list[AuditEntryResponse]:
    registry = get_registry(request)
    repo = get_repository(request)

    if not registry.get(junction_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Junction '{junction_id}' not found",
        )

    entries = await repo.load_audit_log(junction_id, limit=limit)
    result = []
    for e in entries:
        result.append(
            AuditEntryResponse(
                id=getattr(e, "id", ""),
                event_type=e.event_type.value,
                junction_id=e.junction_id,
                timestamp=e.timestamp,
                direction=e.direction.value if e.direction else None,
                previous_state=e.previous_state,
                new_state=e.new_state,
                command_id=e.command_id,
                reason=e.reason,
                vehicle_id=e.vehicle_id,
                event_id=e.event_id,
            )
        )
    return result
