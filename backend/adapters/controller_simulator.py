"""
RestSimulatorController - Hardware signal controller simulator adapter.

Implements ControllerPort interface.
Records commands sent by the backend.
Simulates physical hardware with configurable behaviors:
- Manual ACK via POST /api/controller-events
- Optional auto_ack mode with configurable delay
- Failure injection: offline, degraded, dropped commands
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..domain.types import (
    ControllerStatus,
    PendingCommand,
    SignalState,
)
from ..ports.interfaces import ControllerPort

logger = logging.getLogger(__name__)


@dataclass
class ControllerRecord:
    command: PendingCommand
    timestamp: float
    acked: bool = False
    ack_timestamp: Optional[float] = None
    actual_state: Optional[SignalState] = None


class RestSimulatorController(ControllerPort):
    """
    Controller adapter simulating traffic signal hardware.
    Commands sit in pending history until ACK is received.
    """

    def __init__(
        self,
        junction_id: str,
        auto_ack: bool = False,
        auto_ack_delay: float = 0.05,
        status: ControllerStatus = ControllerStatus.ONLINE,
    ):
        self.junction_id = junction_id
        self.auto_ack = auto_ack
        self.auto_ack_delay = auto_ack_delay
        self.status = status
        self.sent_commands: list[PendingCommand] = []
        self.command_history: dict[str, ControllerRecord] = {}
        self.last_command: Optional[PendingCommand] = None
        self._on_command_callback = None
        self._on_ack_callback = None

    def set_command_callback(self, callback) -> None:
        """Register a callback for when a command is dispatched."""
        self._on_command_callback = callback

    def set_ack_callback(self, callback) -> None:
        """Register a callback for when an ACK is delivered (e.g. in auto_ack mode)."""
        self._on_ack_callback = callback

    async def send_command(self, command: PendingCommand) -> None:
        """
        Record command sent to hardware.
        If controller is OFFLINE, command cannot be received by physical hardware.
        """
        self.sent_commands.append(command)
        self.last_command = command

        record = ControllerRecord(
            command=command,
            timestamp=asyncio.get_event_loop().time(),
        )
        self.command_history[command.command_id] = record
        logger.info(f"Controller {self.junction_id} received command: {command.command_id} (retries={command.retries})")

        if self._on_command_callback:
            try:
                await self._on_command_callback(command)
            except Exception as e:
                logger.error(f"Error in controller callback: {e}")

        # If auto-ack mode is enabled and controller is online, schedule automatic ACK
        if self.auto_ack and self.status == ControllerStatus.ONLINE:
            asyncio.create_task(self._schedule_auto_ack(command.command_id))

    async def _schedule_auto_ack(self, command_id: str) -> None:
        if self.auto_ack_delay > 0:
            await asyncio.sleep(self.auto_ack_delay)
        if self.status == ControllerStatus.ONLINE:
            self.mark_acked(command_id)
            if self._on_ack_callback:
                try:
                    res = self._on_ack_callback(command_id)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.error(f"Error in controller ACK callback: {e}")

    def mark_acked(self, command_id: str, actual_state: Optional[SignalState] = None) -> bool:
        """Mark a command as acknowledged by the controller."""
        if command_id in self.command_history:
            rec = self.command_history[command_id]
            rec.acked = True
            rec.ack_timestamp = asyncio.get_event_loop().time()
            rec.actual_state = actual_state or rec.command.desired_signals
            return True
        return False

    def set_status(self, status: ControllerStatus) -> None:
        """Update simulated controller status (ONLINE, OFFLINE, DEGRADED)."""
        self.status = status
        logger.info(f"Controller {self.junction_id} status changed to {status.value}")

    def clear(self) -> None:
        """Clear recorded command history."""
        self.sent_commands.clear()
        self.command_history.clear()
        self.last_command = None
