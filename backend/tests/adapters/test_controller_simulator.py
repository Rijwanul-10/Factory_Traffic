"""
Phase 4 Tests: Controller Simulator Adapter.

Validates:
- Recording sent commands
- Manual ACK updates
- Auto-ack mode
- Controller offline simulation
- Retry commands tracking
"""

import asyncio
import pytest

from backend.adapters.controller_simulator import RestSimulatorController
from backend.domain.types import (
    ControllerStatus,
    PendingCommand,
    SignalColor,
    SignalState,
)


@pytest.mark.asyncio
async def test_controller_records_commands():
    controller = RestSimulatorController(junction_id="A", auto_ack=False)
    assert len(controller.sent_commands) == 0

    cmd = PendingCommand(
        command_id="cmd-101",
        junction_id="A",
        desired_signals=SignalState(north=SignalColor.GREEN, south=SignalColor.GREEN),
        created_at=100.0,
    )
    await controller.send_command(cmd)

    assert len(controller.sent_commands) == 1
    assert controller.last_command.command_id == "cmd-101"
    assert "cmd-101" in controller.command_history
    assert not controller.command_history["cmd-101"].acked


@pytest.mark.asyncio
async def test_controller_manual_ack():
    controller = RestSimulatorController(junction_id="A", auto_ack=False)
    cmd = PendingCommand(
        command_id="cmd-102",
        junction_id="A",
        desired_signals=SignalState.all_red(),
        created_at=100.0,
    )
    await controller.send_command(cmd)

    success = controller.mark_acked("cmd-102")
    assert success is True
    assert controller.command_history["cmd-102"].acked is True


@pytest.mark.asyncio
async def test_controller_auto_ack_mode():
    controller = RestSimulatorController(junction_id="A", auto_ack=True, auto_ack_delay=0.01)
    cmd = PendingCommand(
        command_id="cmd-103",
        junction_id="A",
        desired_signals=SignalState(east=SignalColor.GREEN, west=SignalColor.GREEN),
        created_at=100.0,
    )
    await controller.send_command(cmd)
    assert not controller.command_history["cmd-103"].acked

    # Wait for auto-ack delay
    await asyncio.sleep(0.05)
    assert controller.command_history["cmd-103"].acked is True


@pytest.mark.asyncio
async def test_controller_offline_status():
    controller = RestSimulatorController(junction_id="A", status=ControllerStatus.ONLINE)
    controller.set_status(ControllerStatus.OFFLINE)
    assert controller.status == ControllerStatus.OFFLINE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
