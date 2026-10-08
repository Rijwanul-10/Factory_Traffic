"""
Restart Recovery Orchestration.

Implements the spec restart recovery rules:
1. Load persisted state from repository.
2. Set actual_signals = UNKNOWN, desired = ALL_RED.
3. Re-issue the command to hardware controller.
4. Resume automatic sequencing only after an ACK is received.
5. Restore emergency/manual queues, dropping stale entries.
6. In-flight transitions are restarted from ALL_RED, never resumed mid-way.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from .engine import JunctionEngine
from .types import (
    AuditEventType,
    JunctionMode,
    PendingCommand,
    SignalColor,
    SignalState,
)
from ..ports.interfaces import Repository

logger = logging.getLogger(__name__)


async def recover_junction_from_storage(
    engine: JunctionEngine,
    repository: Repository,
) -> list[PendingCommand]:
    """
    Restore engine state from repository and apply restart recovery protocol.
    Returns the initial ALL_RED command that must be sent to the controller.
    """
    junction_id = engine.config.junction_id
    now = engine.clock.now()

    logger.info(f"Initiating restart recovery for junction {junction_id}...")

    # 1. Restore waiting vehicles
    waiting = await repository.load_waiting_vehicles(junction_id)
    engine.waiting_vehicles.clear()
    for v in waiting:
        engine.waiting_vehicles[v.vehicle_id] = v

    # 2. Restore emergency queue (drop stale emergencies)
    emergencies = await repository.load_emergency_queue(junction_id)
    active_emergencies = [e for e in emergencies if now < e.stale_timeout]
    engine.emergency_queue = deque(active_emergencies)
    engine.active_emergency = None

    # 3. Restore manual queue (drop expired manual requests)
    manuals = await repository.load_manual_queue(junction_id)
    active_manuals = [m for m in manuals if now < (m.created_at + m.ttl)]
    engine.manual_queue = deque(active_manuals)
    engine.active_manual = None
    engine.manual_hold_start = None

    # 4. In-flight transitions are discarded: start cleanly from ALL_RED
    engine.transition_plan = None
    engine.in_transition = False
    engine.current_step_type = None

    # 5. Set actual_signals = UNKNOWN, desired = ALL_RED
    engine.actual_signals = SignalState.all_unknown()
    engine.desired_signals = SignalState.all_red()

    # 6. Re-issue command for ALL_RED
    cmd = engine._create_command(SignalState.all_red())
    commands = [cmd]

    # 7. Audit restart recovery
    engine._audit(
        AuditEventType.RESTART_RECOVERY,
        reason=(
            f"Restart recovery complete: restored {len(engine.waiting_vehicles)} vehicles, "
            f"{len(engine.emergency_queue)} emergencies, {len(engine.manual_queue)} manual requests. "
            "Awaiting controller ACK on ALL_RED before resuming sequencing."
        ),
        command_id=cmd.command_id,
        new_state=str(SignalState.all_red().as_dict()),
    )

    logger.info(f"Restart recovery ready for junction {junction_id}. Command {cmd.command_id} generated.")
    return commands
