"""
Junction Actor - Actor-model concurrency for junction traffic control.

Requirements:
- One asyncio task per junction owns all state.
- API handlers put messages on that junction's asyncio.Queue and await the result.
- A tick loop runs in the same task.
- No sleep() in handlers.
- Two requests can never race.
- Data/config-driven so multiple junctions (A, B, C, D) can be managed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .engine import JunctionEngine
from .types import (
    CommandStatus,
    CommandType,
    ControllerStatus,
    Direction,
    EventProcessingResult,
    JunctionConfig,
    PendingCommand,
    SensorEvent,
    SignalState,
)
from ..ports.interfaces import ControllerPort, Repository

logger = logging.getLogger(__name__)


@dataclass
class ActorMessage:
    """Message sent into the junction actor mailbox."""
    msg_type: str
    payload: dict[str, Any]
    response_future: asyncio.Future[Any]


class JunctionActor:
    """
    Actor owning the state of a single junction.
    All state mutations and the periodic tick happen inside the actor loop.
    """

    def __init__(
        self,
        engine: JunctionEngine,
        repository: Optional[Repository] = None,
        controller_port: Optional[ControllerPort] = None,
        tick_interval: float = 0.05,  # 50ms tick interval in real time
    ):
        self.engine = engine
        self.repository = repository
        self.controller_port = controller_port
        self.tick_interval = tick_interval
        self.mailbox: asyncio.Queue[ActorMessage] = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        if self.controller_port and hasattr(self.controller_port, "set_ack_callback"):
            self.controller_port.set_ack_callback(self.handle_ack)

    @property
    def junction_id(self) -> str:
        return self.engine.config.junction_id

    def start(self) -> asyncio.Task:
        """Start the actor background task."""
        if self._running:
            return self._task  # type: ignore
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"actor-junction-{self.junction_id}")
        return self._task

    async def stop(self) -> None:
        """Gracefully stop the actor task."""
        if not self._running:
            return
        self._running = False
        # Send shutdown sentinel
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        await self.mailbox.put(ActorMessage(msg_type="SHUTDOWN", payload={}, response_future=fut))
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def send_message(self, msg_type: str, payload: dict[str, Any]) -> Any:
        """
        Public client method: put a message on the mailbox and await response.
        Handlers NEVER sleep here — they merely await the actor's response future.
        """
        if not self._running:
            raise RuntimeError(f"Junction actor {self.junction_id} is not running")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        msg = ActorMessage(msg_type=msg_type, payload=payload, response_future=future)
        await self.mailbox.put(msg)
        return await future

    # ── Convenience API callers ──

    async def handle_sensor_event(self, event: SensorEvent) -> EventProcessingResult:
        return await self.send_message("SENSOR_EVENT", {"event": event})

    async def handle_manual_command(
        self, command_type: CommandType, direction: Optional[Direction] = None
    ) -> tuple[str, CommandStatus]:
        return await self.send_message("MANUAL_COMMAND", {"command_type": command_type, "direction": direction})

    async def handle_ack(
        self, command_id: str, actual_state: Optional[SignalState] = None
    ) -> None:
        await self.send_message("ACK", {"command_id": command_id, "actual_state": actual_state})

    async def handle_controller_status(self, status: ControllerStatus) -> None:
        await self.send_message("CONTROLLER_STATUS", {"status": status})

    async def handle_sensor_status(self, direction: Direction, status: ControllerStatus) -> None:
        await self.send_message("SENSOR_STATUS", {"direction": direction, "status": status})

    async def get_status(self) -> dict[str, Any]:
        return await self.send_message("GET_STATUS", {})

    async def recover_restart(self) -> None:
        await self.send_message("RECOVER_RESTART", {})

    # ── Actor Loop ──

    async def _run_loop(self) -> None:
        """
        Single-threaded event loop for this junction.
        Ticks the engine, processes queued messages, and dispatches controller commands.
        """
        logger.info(f"Junction actor {self.junction_id} started")

        while self._running:
            try:
                # 1. Tick the engine
                cmds = self.engine.tick()
                await self._dispatch_commands(cmds)
                await self._persist_audit_log()

                # 2. Wait for incoming messages up to tick_interval
                try:
                    msg = await asyncio.wait_for(self.mailbox.get(), timeout=self.tick_interval)
                    if msg.msg_type == "SHUTDOWN":
                        msg.response_future.set_result(True)
                        break

                    await self._process_message(msg)
                except asyncio.TimeoutError:
                    # No message arrived during tick interval — continue loop to tick again
                    pass

            except Exception as e:
                logger.error(f"Error in junction actor {self.junction_id}: {e}", exc_info=True)

        logger.info(f"Junction actor {self.junction_id} stopped")

    async def _process_message(self, msg: ActorMessage) -> None:
        """Process a single incoming message sequentially."""
        try:
            mtype = msg.msg_type
            payload = msg.payload
            cmds: list[PendingCommand] = []

            if mtype == "SENSOR_EVENT":
                event: SensorEvent = payload["event"]
                result, cmds = self.engine.process_sensor_event(event)
                msg.response_future.set_result(result)

            elif mtype == "MANUAL_COMMAND":
                cmd_type: CommandType = payload["command_type"]
                direction: Optional[Direction] = payload.get("direction")
                cmd_id, status, cmds = self.engine.process_manual_request(cmd_type, direction)
                msg.response_future.set_result((cmd_id, status))

            elif mtype == "ACK":
                cmd_id = payload["command_id"]
                actual_state = payload.get("actual_state")
                cmds = self.engine.process_ack(cmd_id, actual_state)
                msg.response_future.set_result(True)

            elif mtype == "CONTROLLER_STATUS":
                status: ControllerStatus = payload["status"]
                cmds = self.engine.process_controller_status(status)
                msg.response_future.set_result(True)

            elif mtype == "SENSOR_STATUS":
                direction: Direction = payload["direction"]
                status: ControllerStatus = payload["status"]
                cmds = self.engine.process_sensor_status(direction, status)
                msg.response_future.set_result(True)

            elif mtype == "GET_STATUS":
                status_data = self.engine.get_status()
                msg.response_future.set_result(status_data)

            elif mtype == "RECOVER_RESTART":
                cmds = self.engine.recover_from_restart()
                msg.response_future.set_result(True)

            else:
                msg.response_future.set_exception(ValueError(f"Unknown message type: {mtype}"))

            # Dispatch any generated controller commands
            await self._dispatch_commands(cmds)
            await self._persist_audit_log()

        except Exception as e:
            if not msg.response_future.done():
                msg.response_future.set_exception(e)

    async def _dispatch_commands(self, commands: list[PendingCommand]) -> None:
        """Send commands to controller port if configured."""
        if not self.controller_port or not commands:
            return
        for cmd in commands:
            try:
                await self.controller_port.send_command(cmd)
            except Exception as e:
                logger.error(f"Failed to send command {cmd.command_id}: {e}")

    async def _persist_audit_log(self) -> None:
        """Drain audit buffer from engine and persist via repository."""
        if not self.repository:
            return
        entries = self.engine.drain_audit_buffer()
        for entry in entries:
            try:
                await self.repository.save_audit_entry(entry)
            except Exception as e:
                logger.error(f"Failed to persist audit entry {entry.id}: {e}")


class JunctionRegistry:
    """Registry managing multiple junction actors by junction_id."""

    def __init__(self):
        self._actors: dict[str, JunctionActor] = {}

    def register(self, actor: JunctionActor) -> None:
        self._actors[actor.junction_id] = actor

    def get(self, junction_id: str) -> Optional[JunctionActor]:
        return self._actors.get(junction_id)

    def all_junction_ids(self) -> list[str]:
        return list(self._actors.keys())

    async def start_all(self) -> None:
        for actor in self._actors.values():
            actor.start()

    async def stop_all(self) -> None:
        for actor in self._actors.values():
            await actor.stop()
