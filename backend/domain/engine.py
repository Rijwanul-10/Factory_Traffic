"""
Junction Engine - The core state machine for traffic control.

Pure Python. No imports of FastAPI, DB, HTTP, or MQTT.
This is the single source of truth for signal safety invariants.

Architecture: Each junction runs as a single-threaded actor.
All state mutations go through this engine sequentially.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar, Optional

from .clock import Clock
from .types import (
    AuditEntry,
    AuditEventType,
    CommandStatus,
    CommandType,
    ControllerStatus,
    Direction,
    DIRECTION_PHASE,
    CONFLICTING_PHASE,
    EmergencyEntry,
    EventProcessingResult,
    EventType,
    JunctionConfig,
    JunctionMode,
    ManualRequest,
    PendingCommand,
    Phase,
    PHASE_DIRECTIONS,
    SensorEvent,
    SignalColor,
    SignalState,
    TransitionStep,
    VehicleType,
    VEHICLE_PRIORITY_WEIGHT,
    WaitingVehicle,
)


# ─── Safety assertion ──────────────────────────────────────────────────────

class SafetyViolationError(Exception):
    """Raised when a signal state would violate traffic safety invariants."""
    pass


def assert_signal_safety(signals: SignalState) -> None:
    """
    THE safety invariant. Called before every signal state change.
    Conflicting phases must never be GREEN simultaneously.
    """
    if signals.has_conflict():
        raise SafetyViolationError(
            f"SAFETY VIOLATION: Conflicting phases both GREEN! {signals.as_dict()}"
        )


# ─── Transition planner ────────────────────────────────────────────────────

@dataclass
class TransitionPlan:
    """A FIFO list of steps to execute a safe signal transition."""
    steps: deque[tuple[TransitionStep, Phase, float]] = field(default_factory=deque)
    # Each step: (step_type, target_phase, duration_nominal)
    target_phase: Optional[Phase] = None

    @property
    def is_empty(self) -> bool:
        return len(self.steps) == 0

    @property
    def current_step(self) -> Optional[tuple[TransitionStep, Phase, float]]:
        return self.steps[0] if self.steps else None

    def pop_step(self) -> Optional[tuple[TransitionStep, Phase, float]]:
        return self.steps.popleft() if self.steps else None


def build_transition_plan(
    current_phase: Phase,
    target_phase: Phase,
    config: JunctionConfig,
) -> TransitionPlan:
    """
    Build a safe transition from current_phase to target_phase.
    Sequence: current YELLOW -> ALL_RED -> target GREEN.
    """
    if current_phase == target_phase:
        # Same phase, no transition needed — just start/continue green
        return TransitionPlan(
            steps=deque([(TransitionStep.GREEN, target_phase, config.green_duration)]),
            target_phase=target_phase,
        )

    return TransitionPlan(
        steps=deque([
            (TransitionStep.YELLOW, current_phase, config.yellow_duration),
            (TransitionStep.ALL_RED, target_phase, config.all_red_duration),
            (TransitionStep.GREEN, target_phase, config.green_duration),
        ]),
        target_phase=target_phase,
    )


# ─── Junction Engine (state machine) ───────────────────────────────────────

@dataclass
class JunctionEngine:
    """
    The core junction state machine.

    All methods are synchronous and pure (no I/O).
    External effects (commands to controllers, DB writes) are returned
    as lists of events/commands for the caller to dispatch.

    Safety invariants are enforced here in ONE place.
    """

    config: JunctionConfig
    clock: Clock

    # ── Signal state ──
    mode: JunctionMode = JunctionMode.AUTOMATIC
    current_phase: Phase = Phase.NORTH_SOUTH
    desired_signals: SignalState = field(default_factory=SignalState.all_red)
    actual_signals: SignalState = field(default_factory=SignalState.all_red)

    # ── Timing ──
    green_start_time: float = 0.0        # when current green started (real time)
    step_start_time: float = 0.0         # when current transition step started (real time)
    phase_wait_start: dict[Phase, float] = field(default_factory=dict)  # when each phase started waiting

    # ── Transition ──
    transition_plan: Optional[TransitionPlan] = None
    current_step_type: Optional[TransitionStep] = None
    in_transition: bool = False
    grace_empty_start: Optional[float] = None  # when grace timer started

    # ── Queues ──
    waiting_vehicles: dict[str, WaitingVehicle] = field(default_factory=dict)  # vehicle_id -> vehicle
    processed_events: set[str] = field(default_factory=set)  # event_ids
    vehicle_tombstones: set[str] = field(default_factory=set)  # vehicle_ids that have been CLEARED
    direction_sequence: dict[str, int] = field(default_factory=dict)  # junction_id+direction -> last seq_no

    # ── Emergency ──
    emergency_queue: deque[EmergencyEntry] = field(default_factory=deque)
    active_emergency: Optional[EmergencyEntry] = None

    # ── Manual ──
    manual_queue: deque[ManualRequest] = field(default_factory=deque)
    active_manual: Optional[ManualRequest] = None
    manual_hold_start: Optional[float] = None  # real time

    # ── Controller ──
    controller_status: ControllerStatus = ControllerStatus.ONLINE
    pending_commands: dict[str, PendingCommand] = field(default_factory=dict)
    direction_sensor_status: dict[Direction, ControllerStatus] = field(default_factory=dict)

    # ── Audit log (in-memory buffer, flushed by caller) ──
    audit_buffer: list[AuditEntry] = field(default_factory=list)

    # ── Initialization flag ──
    _initialized: bool = False

    def __post_init__(self):
        if not self._initialized:
            now = self.clock.now()
            for phase in self.config.phases:
                if phase not in self.phase_wait_start:
                    self.phase_wait_start[phase] = now
            for d in self.config.directions:
                if d not in self.direction_sensor_status:
                    self.direction_sensor_status[d] = ControllerStatus.ONLINE
            self._initialized = True

    # ── Audit helper ──

    def _audit(
        self,
        event_type: AuditEventType,
        reason: str = "",
        direction: Optional[Direction] = None,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
        command_id: Optional[str] = None,
        vehicle_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            event_type=event_type,
            junction_id=self.config.junction_id,
            timestamp=self.clock.now(),
            direction=direction,
            previous_state=previous_state,
            new_state=new_state,
            command_id=command_id,
            reason=reason,
            vehicle_id=vehicle_id,
            event_id=event_id,
        )
        self.audit_buffer.append(entry)
        return entry

    # ── Signal helpers (THE safety enforcement point) ──

    def _set_desired_signals(self, signals: SignalState, reason: str = "") -> list[PendingCommand]:
        """
        Set desired signals, enforcing the safety invariant.
        Returns commands to send to the controller.
        """
        assert_signal_safety(signals)

        old = self.desired_signals
        self.desired_signals = signals

        if old != signals:
            self._audit(
                AuditEventType.SIGNAL_CHANGE,
                reason=reason,
                previous_state=str(old.as_dict()),
                new_state=str(signals.as_dict()),
            )

            # Create controller command
            cmd = self._create_command(signals)
            return [cmd]
        return []

    def _create_command(self, signals: SignalState) -> PendingCommand:
        cmd_id = f"cmd-{uuid.uuid4().hex[:8]}"
        cmd = PendingCommand(
            command_id=cmd_id,
            junction_id=self.config.junction_id,
            desired_signals=signals,
            created_at=self.clock.now(),
            ack_timeout=self.config.ack_timeout,
            max_retries=self.config.ack_retries,
        )
        self.pending_commands[cmd_id] = cmd
        self._audit(
            AuditEventType.COMMAND_SENT,
            command_id=cmd_id,
            reason=f"Signal command: {signals.as_dict()}",
        )
        return cmd

    def _signals_for_phase_green(self, phase: Phase) -> SignalState:
        """Return a signal state with the given phase GREEN and everything else RED."""
        signals = SignalState.all_red()
        return signals.with_phase(phase, SignalColor.GREEN)

    def _signals_for_phase_yellow(self, phase: Phase) -> SignalState:
        """Return a signal state with the given phase YELLOW and everything else RED."""
        signals = SignalState.all_red()
        return signals.with_phase(phase, SignalColor.YELLOW)

    # ── Queue helpers ──

    def queue_count(self, direction: Direction) -> int:
        """Count of waiting vehicles in a direction."""
        return sum(
            1 for v in self.waiting_vehicles.values()
            if v.direction == direction
        )

    def phase_queue_count(self, phase: Phase) -> int:
        """Total waiting vehicles in a phase (both directions)."""
        return sum(self.queue_count(d) for d in PHASE_DIRECTIONS[phase])

    def phase_is_empty(self, phase: Phase) -> bool:
        return self.phase_queue_count(phase) == 0

    def all_queues_empty(self) -> bool:
        return len(self.waiting_vehicles) == 0

    def phase_score(self, phase: Phase) -> float:
        """
        Score a phase for scheduling:
        sum(queue_counts) + sum(vehicle_priority_weights) + 0.5 * waiting_seconds
        """
        now = self.clock.now()
        score = 0.0
        for v in self.waiting_vehicles.values():
            if DIRECTION_PHASE[v.direction] == phase:
                score += 1  # queue count
                score += VEHICLE_PRIORITY_WEIGHT.get(v.vehicle_type, 1)
                wait_secs = now - v.arrived_at
                score += 0.5 * wait_secs
        return score

    def phase_max_wait(self, phase: Phase) -> float:
        """Maximum waiting time of any vehicle in this phase (in real seconds)."""
        now = self.clock.now()
        max_wait = 0.0
        for v in self.waiting_vehicles.values():
            if DIRECTION_PHASE[v.direction] == phase:
                max_wait = max(max_wait, now - v.arrived_at)
        return max_wait

    # ── Mode transitions ──

    # Valid mode transitions table
    VALID_MODE_TRANSITIONS: ClassVar[dict[JunctionMode, set[JunctionMode]]] = {
        JunctionMode.AUTOMATIC: {JunctionMode.MANUAL, JunctionMode.EMERGENCY, JunctionMode.FAILURE},
        JunctionMode.MANUAL: {JunctionMode.AUTOMATIC, JunctionMode.EMERGENCY, JunctionMode.FAILURE},
        JunctionMode.EMERGENCY: {JunctionMode.AUTOMATIC, JunctionMode.MANUAL, JunctionMode.FAILURE},
        JunctionMode.FAILURE: {JunctionMode.AUTOMATIC},
    }

    def _can_transition_mode(self, target: JunctionMode) -> bool:
        return target in self.VALID_MODE_TRANSITIONS.get(self.mode, set())

    def _set_mode(self, new_mode: JunctionMode, reason: str = ""):
        if new_mode == self.mode:
            return
        old = self.mode
        if not self._can_transition_mode(new_mode):
            raise ValueError(f"Invalid mode transition: {old} -> {new_mode}")
        self.mode = new_mode
        self._audit(
            AuditEventType.MODE_CHANGE,
            reason=reason,
            previous_state=old.value,
            new_state=new_mode.value,
        )

    # ── Vehicle event processing ──

    def process_sensor_event(self, event: SensorEvent) -> tuple[EventProcessingResult, list[PendingCommand]]:
        """
        Process a sensor event. Returns the processing result and any commands to send.

        Idempotency: duplicate event_ids return DUPLICATE with no state change.
        Orphan: CLEARED for unknown vehicle is rejected and audited.
        """
        commands: list[PendingCommand] = []

        # Duplicate check
        if event.event_id in self.processed_events:
            self._audit(
                AuditEventType.DUPLICATE_EVENT,
                reason=f"Duplicate event_id: {event.event_id}",
                direction=event.direction,
                event_id=event.event_id,
                vehicle_id=event.vehicle_id,
            )
            return EventProcessingResult.DUPLICATE, commands

        # Mark as processed
        self.processed_events.add(event.event_id)

        # Sequence check
        seq_key = f"{event.junction_id}_{event.direction.value}"
        last_seq = self.direction_sequence.get(seq_key, -1)

        if event.event_type == EventType.VEHICLE_ARRIVED:
            # Check if vehicle was already cleared (tombstone)
            if event.vehicle_id in self.vehicle_tombstones:
                self._audit(
                    AuditEventType.STALE_EVENT,
                    reason=f"ARRIVED after CLEARED (tombstone) for {event.vehicle_id}",
                    direction=event.direction,
                    event_id=event.event_id,
                    vehicle_id=event.vehicle_id,
                )
                return EventProcessingResult.STALE, commands

            # Check if already waiting (idempotent by vehicle_id)
            if event.vehicle_id in self.waiting_vehicles:
                self._audit(
                    AuditEventType.DUPLICATE_EVENT,
                    reason=f"Vehicle {event.vehicle_id} already waiting",
                    direction=event.direction,
                    event_id=event.event_id,
                    vehicle_id=event.vehicle_id,
                )
                return EventProcessingResult.DUPLICATE, commands

            # Add to waiting vehicles
            vehicle = WaitingVehicle(
                vehicle_id=event.vehicle_id,
                junction_id=event.junction_id,
                direction=event.direction,
                vehicle_type=event.vehicle_type or VehicleType.EMPLOYEE_VEHICLE,
                arrived_at=event.server_timestamp or self.clock.now(),
                sequence_no=event.sequence_no,
            )
            self.waiting_vehicles[event.vehicle_id] = vehicle

            self._audit(
                AuditEventType.VEHICLE_ARRIVED,
                reason=f"Vehicle {event.vehicle_id} ({event.vehicle_type}) arrived",
                direction=event.direction,
                event_id=event.event_id,
                vehicle_id=event.vehicle_id,
            )

            # Update sequence
            if event.sequence_no > last_seq:
                self.direction_sequence[seq_key] = event.sequence_no

            # Handle emergency vehicle
            if event.vehicle_type == VehicleType.EMERGENCY:
                commands.extend(self._handle_emergency_arrival(event))

            return EventProcessingResult.ACCEPTED, commands

        elif event.event_type == EventType.VEHICLE_CLEARED:
            if event.vehicle_id not in self.waiting_vehicles:
                # Orphan: unknown vehicle cleared
                self._audit(
                    AuditEventType.ORPHAN_CLEARED,
                    reason=f"CLEARED for unknown vehicle {event.vehicle_id}",
                    direction=event.direction,
                    event_id=event.event_id,
                    vehicle_id=event.vehicle_id,
                )
                return EventProcessingResult.ORPHAN, commands

            # Remove from waiting
            del self.waiting_vehicles[event.vehicle_id]
            self.vehicle_tombstones.add(event.vehicle_id)

            self._audit(
                AuditEventType.VEHICLE_CLEARED,
                reason=f"Vehicle {event.vehicle_id} cleared",
                direction=event.direction,
                event_id=event.event_id,
                vehicle_id=event.vehicle_id,
            )

            # Update sequence
            if event.sequence_no > last_seq:
                self.direction_sequence[seq_key] = event.sequence_no

            # Handle emergency vehicle clearance
            commands.extend(self._handle_emergency_clearance(event.vehicle_id))

            return EventProcessingResult.ACCEPTED, commands

        return EventProcessingResult.REJECTED, commands

    # ── Emergency handling ──

    def _handle_emergency_arrival(self, event: SensorEvent) -> list[PendingCommand]:
        """Handle arrival of an emergency vehicle."""
        commands: list[PendingCommand] = []

        # Check if this emergency vehicle is already in the queue
        for e in self.emergency_queue:
            if e.vehicle_id == event.vehicle_id:
                self._audit(
                    AuditEventType.DUPLICATE_EVENT,
                    reason=f"Emergency vehicle {event.vehicle_id} already in queue",
                    direction=event.direction,
                    vehicle_id=event.vehicle_id,
                )
                return commands

        phase = DIRECTION_PHASE[event.direction]
        entry = EmergencyEntry(
            vehicle_id=event.vehicle_id,
            direction=event.direction,
            phase=phase,
            arrived_at=self.clock.now(),
            stale_timeout=self.clock.now() + self.config.real_seconds(self.config.emergency_stale_timeout),
        )
        self.emergency_queue.append(entry)

        self._audit(
            AuditEventType.EMERGENCY_ENTER,
            reason=f"Emergency vehicle {event.vehicle_id} from {event.direction.value}",
            direction=event.direction,
            vehicle_id=event.vehicle_id,
        )

        # If in manual mode, cancel active manual and audit
        if self.mode == JunctionMode.MANUAL:
            if self.active_manual:
                self.active_manual.status = CommandStatus.CANCELLED
                self._audit(
                    AuditEventType.MANUAL_CANCELLED,
                    reason=f"Cancelled by emergency vehicle {event.vehicle_id}",
                    command_id=self.active_manual.command_id,
                )
                self.active_manual = None
                self.manual_hold_start = None
            # Clear manual queue
            for mr in self.manual_queue:
                mr.status = CommandStatus.CANCELLED
            self.manual_queue.clear()

        # Enter emergency mode
        if self.mode != JunctionMode.EMERGENCY:
            self._set_mode(JunctionMode.EMERGENCY, f"Emergency vehicle {event.vehicle_id}")

        # Start serving if no active emergency
        if self.active_emergency is None:
            commands.extend(self._serve_next_emergency())

        return commands

    def _handle_emergency_clearance(self, vehicle_id: str) -> list[PendingCommand]:
        """Handle clearance of an emergency vehicle."""
        commands: list[PendingCommand] = []

        # Remove from emergency queue
        self.emergency_queue = deque(e for e in self.emergency_queue if e.vehicle_id != vehicle_id)

        # Check if the active emergency is cleared
        if self.active_emergency and self.active_emergency.vehicle_id == vehicle_id:
            self._audit(
                AuditEventType.EMERGENCY_CLEAR,
                reason=f"Emergency vehicle {vehicle_id} cleared",
                vehicle_id=vehicle_id,
            )
            self.active_emergency = None

            # Try to serve next emergency or return to auto
            if self.emergency_queue:
                commands.extend(self._serve_next_emergency())
            else:
                self._return_from_emergency()

        return commands

    def _serve_next_emergency(self) -> list[PendingCommand]:
        """Serve the next emergency from the FIFO queue."""
        commands: list[PendingCommand] = []

        if not self.emergency_queue:
            return commands

        entry = self.emergency_queue[0]
        self.active_emergency = entry

        target_phase = entry.phase

        # If the target phase is already GREEN, just hold it
        if self.current_phase == target_phase and self.current_step_type == TransitionStep.GREEN:
            # Already in the right phase — no transition needed
            # Stop the green timer (emergency holds indefinitely until cleared)
            self.green_start_time = self.clock.now()  # reset timer
            return commands

        # If we're in YELLOW or ALL_RED of a transition, continue that sequence
        # but replace the target with the emergency phase
        if self.in_transition and self.transition_plan:
            current = self.transition_plan.current_step
            if current and current[0] in (TransitionStep.YELLOW, TransitionStep.ALL_RED):
                # Continue current YELLOW/ALL_RED, but change target to emergency phase
                # Remove future GREEN step and replace with emergency's target
                new_steps = deque()
                for step in self.transition_plan.steps:
                    if step[0] in (TransitionStep.YELLOW, TransitionStep.ALL_RED):
                        new_steps.append(step)
                    elif step[0] == TransitionStep.GREEN:
                        new_steps.append((TransitionStep.GREEN, target_phase, self.config.green_duration))
                        break
                self.transition_plan.steps = new_steps
                self.transition_plan.target_phase = target_phase
                return commands

        # Start a new safe transition
        plan = build_transition_plan(self.current_phase, target_phase, self.config)
        commands.extend(self._start_transition(plan, f"Emergency preemption for {entry.vehicle_id}"))
        return commands

    def _return_from_emergency(self):
        """Return to appropriate mode after emergency queue is empty."""
        # Preserve waiting times — phases that were waiting keep their timestamps
        self._set_mode(JunctionMode.AUTOMATIC, "Emergency queue empty, returning to automatic")

    # ── Manual handling ──

    def process_manual_request(
        self, command_type: CommandType, direction: Optional[Direction] = None
    ) -> tuple[str, CommandStatus, list[PendingCommand]]:
        """
        Process a manual command request.
        Returns (command_id, status, commands_to_send).
        """
        commands: list[PendingCommand] = []
        cmd_id = f"manual-{uuid.uuid4().hex[:8]}"

        if command_type == CommandType.RETURN_TO_AUTOMATIC:
            self._audit(
                AuditEventType.RETURN_TO_AUTOMATIC,
                reason="Admin requested return to automatic",
                command_id=cmd_id,
            )
            # Clear manual queue
            for mr in self.manual_queue:
                mr.status = CommandStatus.CANCELLED
            self.manual_queue.clear()

            if self.active_manual:
                self.active_manual.status = CommandStatus.CANCELLED
                self.active_manual = None
                self.manual_hold_start = None

            if self.mode == JunctionMode.MANUAL:
                self._set_mode(JunctionMode.AUTOMATIC, "Return to automatic by admin")

            return cmd_id, CommandStatus.COMPLETED, commands

        if command_type == CommandType.MANUAL_GREEN_REQUEST:
            if direction is None:
                return cmd_id, CommandStatus.REJECTED, commands

            # Emergency overrides manual
            if self.mode == JunctionMode.EMERGENCY:
                self._audit(
                    AuditEventType.MANUAL_REQUEST,
                    reason=f"Manual request rejected: emergency mode active",
                    direction=direction,
                    command_id=cmd_id,
                )
                return cmd_id, CommandStatus.REJECTED, commands

            # Failure mode rejects manual
            if self.mode == JunctionMode.FAILURE:
                return cmd_id, CommandStatus.REJECTED, commands

            phase = DIRECTION_PHASE[direction]

            # Deduplicate same-phase request
            for mr in self.manual_queue:
                if mr.phase == phase and mr.status == CommandStatus.QUEUED:
                    return cmd_id, CommandStatus.REJECTED, commands

            if self.active_manual and self.active_manual.phase == phase:
                return cmd_id, CommandStatus.REJECTED, commands

            # Max 3 in queue
            if len(self.manual_queue) >= 3:
                return cmd_id, CommandStatus.REJECTED, commands

            request = ManualRequest(
                command_id=cmd_id,
                direction=direction,
                phase=phase,
                created_at=self.clock.now(),
            )
            self.manual_queue.append(request)

            self._audit(
                AuditEventType.MANUAL_REQUEST,
                reason=f"Manual GREEN request for {direction.value}",
                direction=direction,
                command_id=cmd_id,
            )

            # If not already in manual mode and no active manual, start serving
            if self.mode != JunctionMode.MANUAL and self.mode == JunctionMode.AUTOMATIC:
                self._set_mode(JunctionMode.MANUAL, f"Manual request for {direction.value}")
                commands.extend(self._serve_next_manual())

            elif self.mode == JunctionMode.MANUAL and self.active_manual is None:
                commands.extend(self._serve_next_manual())

            return cmd_id, CommandStatus.QUEUED, commands

        return cmd_id, CommandStatus.REJECTED, commands

    def _serve_next_manual(self) -> list[PendingCommand]:
        """Serve the next manual request from the FIFO queue."""
        commands: list[PendingCommand] = []

        while self.manual_queue:
            request = self.manual_queue[0]

            # Check TTL
            if self.clock.now() > request.expires_at:
                self.manual_queue.popleft()
                request.status = CommandStatus.CANCELLED
                continue

            self.manual_queue.popleft()
            request.status = CommandStatus.ACTIVE
            self.active_manual = request

            target_phase = request.phase

            # If already in the right phase and GREEN
            if self.current_phase == target_phase and self.current_step_type == TransitionStep.GREEN:
                self.manual_hold_start = self.clock.now()
                self._audit(
                    AuditEventType.MANUAL_HOLD_START,
                    reason=f"Manual hold started for {request.direction.value}",
                    direction=request.direction,
                    command_id=request.command_id,
                )
                return commands

            # Build transition to the target phase
            plan = build_transition_plan(self.current_phase, target_phase, self.config)
            # Override green duration with manual hold
            new_steps = deque()
            for step in plan.steps:
                if step[0] == TransitionStep.GREEN:
                    new_steps.append((TransitionStep.GREEN, step[1], self.config.manual_hold))
                else:
                    new_steps.append(step)
            plan.steps = new_steps

            commands.extend(self._start_transition(plan, f"Manual request for {request.direction.value}"))
            return commands

        # No more manual requests
        if self.mode == JunctionMode.MANUAL:
            self._set_mode(JunctionMode.AUTOMATIC, "Manual queue empty")
        return commands

    # ── Transition execution ──

    def _start_transition(self, plan: TransitionPlan, reason: str = "") -> list[PendingCommand]:
        """Start executing a transition plan."""
        commands: list[PendingCommand] = []
        self.transition_plan = plan
        self.in_transition = True
        self.grace_empty_start = None  # Cancel any grace timer

        # Execute the first step
        commands.extend(self._execute_current_step(reason))
        return commands

    def _execute_current_step(self, reason: str = "") -> list[PendingCommand]:
        """Execute the current step in the transition plan."""
        commands: list[PendingCommand] = []

        if not self.transition_plan or self.transition_plan.is_empty:
            self.in_transition = False
            return commands

        step_type, phase, duration = self.transition_plan.current_step
        self.current_step_type = step_type
        self.step_start_time = self.clock.now()

        if step_type == TransitionStep.YELLOW:
            signals = self._signals_for_phase_yellow(self.current_phase)
            commands.extend(self._set_desired_signals(signals, f"YELLOW: {reason}"))
            self._audit(
                AuditEventType.PHASE_CHANGE,
                reason=f"Phase YELLOW: {reason}",
                previous_state=self.current_phase.value,
                new_state=f"YELLOW_{self.current_phase.value}",
            )

        elif step_type == TransitionStep.ALL_RED:
            signals = SignalState.all_red()
            commands.extend(self._set_desired_signals(signals, f"ALL_RED: {reason}"))
            self._audit(
                AuditEventType.PHASE_CHANGE,
                reason=f"ALL_RED: {reason}",
                previous_state=self.current_phase.value,
                new_state="ALL_RED",
            )

        elif step_type == TransitionStep.GREEN:
            signals = self._signals_for_phase_green(phase)
            commands.extend(self._set_desired_signals(signals, f"GREEN {phase.value}: {reason}"))
            self.current_phase = phase
            self.green_start_time = self.clock.now()
            # Reset wait timer for this phase
            self.phase_wait_start[phase] = self.clock.now()
            self._audit(
                AuditEventType.PHASE_CHANGE,
                reason=f"Phase GREEN: {reason}",
                previous_state="transition",
                new_state=phase.value,
            )
            # If manual mode, start hold timer when green is confirmed
            if self.mode == JunctionMode.MANUAL and self.active_manual:
                self.manual_hold_start = self.clock.now()
                self._audit(
                    AuditEventType.MANUAL_HOLD_START,
                    reason=f"Manual hold started",
                    command_id=self.active_manual.command_id,
                )

        return commands

    # ── Tick (called periodically by the actor loop) ──

    def tick(self) -> list[PendingCommand]:
        """
        Called periodically. Advances the state machine.
        Returns commands to send to the controller.
        """
        commands: list[PendingCommand] = []
        now = self.clock.now()

        # In FAILURE mode, don't sequence — just stay ALL_RED
        if self.mode == JunctionMode.FAILURE:
            return commands

        # Check for stale emergencies
        commands.extend(self._check_stale_emergencies())

        # Check ACK timeouts
        commands.extend(self._check_ack_timeouts())

        # If in transition, check if current step is done
        if self.in_transition and self.transition_plan and not self.transition_plan.is_empty:
            step_type, phase, duration = self.transition_plan.current_step
            real_duration = self.config.real_seconds(duration)
            elapsed = now - self.step_start_time

            if elapsed >= real_duration - 1e-5:
                # Step completed
                self.transition_plan.pop_step()

                if self.transition_plan.is_empty:
                    # Transition complete
                    self.in_transition = False
                    self.transition_plan = None
                    # The green is already set — nothing more to do
                else:
                    # Execute next step
                    commands.extend(self._execute_current_step("Transition continues"))

            return commands

        # Not in transition — check mode-specific logic
        if self.mode == JunctionMode.EMERGENCY:
            # In emergency mode, hold green for emergency direction
            # Don't cycle automatically
            return commands

        if self.mode == JunctionMode.MANUAL:
            # Check manual hold timer
            if self.active_manual and self.manual_hold_start:
                hold_real = self.config.real_seconds(self.config.manual_hold)
                elapsed = now - self.manual_hold_start
                if elapsed >= hold_real:
                    # Manual hold expired
                    self._audit(
                        AuditEventType.MANUAL_HOLD_END,
                        reason=f"Manual hold expired for {self.active_manual.direction.value}",
                        command_id=self.active_manual.command_id,
                    )
                    self.active_manual.status = CommandStatus.COMPLETED
                    self.active_manual = None
                    self.manual_hold_start = None

                    # Serve next manual or return to auto
                    commands.extend(self._serve_next_manual())
            return commands

        # AUTOMATIC mode
        commands.extend(self._tick_automatic())
        return commands

    def _tick_automatic(self) -> list[PendingCommand]:
        """Handle automatic mode tick logic."""
        commands: list[PendingCommand] = []
        now = self.clock.now()

        if self.current_step_type != TransitionStep.GREEN:
            return commands

        green_real = self.config.real_seconds(self.config.green_duration)
        min_green_real = self.config.real_seconds(self.config.min_green)
        max_green_real = self.config.real_seconds(self.config.max_green)
        grace_real = self.config.real_seconds(self.config.grace_empty)
        green_elapsed = now - self.green_start_time

        other_phase = CONFLICTING_PHASE[self.current_phase]

        # Grace empty: if current phase empties and other has vehicles
        if self.phase_is_empty(self.current_phase) and not self.phase_is_empty(other_phase):
            if self.grace_empty_start is None:
                self.grace_empty_start = now

            grace_elapsed = now - self.grace_empty_start
            if grace_elapsed >= grace_real and green_elapsed >= min_green_real:
                # Early end: transition to other phase
                self.grace_empty_start = None
                self._audit(
                    AuditEventType.GRACE_EARLY_END,
                    reason=f"Phase {self.current_phase.value} empty, switching to {other_phase.value}",
                )
                plan = build_transition_plan(self.current_phase, other_phase, self.config)
                commands.extend(self._start_transition(plan, "Phase empty early end"))
                return commands
        else:
            self.grace_empty_start = None

        # Check starvation: if other phase has waited too long
        if not self.phase_is_empty(other_phase):
            max_wait_real = self.config.real_seconds(self.config.max_wait)
            other_max_wait = self.phase_max_wait(other_phase)
            if other_max_wait >= max_wait_real and green_elapsed >= min_green_real:
                self._audit(
                    AuditEventType.STARVATION_OVERRIDE,
                    reason=f"Phase {other_phase.value} waited {other_max_wait:.1f}s (max: {max_wait_real:.1f}s)",
                )
                plan = build_transition_plan(self.current_phase, other_phase, self.config)
                commands.extend(self._start_transition(plan, "Starvation protection"))
                return commands

        # Normal green expiry
        if green_elapsed >= green_real:
            # Check if we should extend
            if not self.phase_is_empty(self.current_phase) and self.phase_is_empty(other_phase):
                # Extend up to MAX_GREEN
                if green_elapsed < max_green_real:
                    if not hasattr(self, '_extend_logged') or not self._extend_logged:
                        self._audit(
                            AuditEventType.GREEN_EXTEND,
                            reason=f"Extending green for {self.current_phase.value}: has vehicles, other empty",
                        )
                        self._extend_logged = True
                    return commands
                else:
                    self._extend_logged = False

            # Skip empty phases
            if self.phase_is_empty(other_phase):
                # Other phase empty — don't switch, stay on current (rest state)
                if not self.phase_is_empty(self.current_phase):
                    # Current has vehicles, stay
                    if green_elapsed < max_green_real:
                        return commands
                # All empty — stay on current green (rest state)
                return commands

            # Normal transition to other phase (choose by score)
            self._extend_logged = False
            target = self._choose_next_phase()
            if target and target != self.current_phase:
                plan = build_transition_plan(self.current_phase, target, self.config)
                commands.extend(self._start_transition(plan, "Automatic green expiry"))

        return commands

    def _choose_next_phase(self) -> Optional[Phase]:
        """Choose the next phase based on scoring."""
        best_phase = None
        best_score = -1.0

        for phase in self.config.phases:
            if phase == self.current_phase:
                continue
            if self.phase_is_empty(phase):
                continue  # Skip empty phases

            # Check starvation
            max_wait_real = self.config.real_seconds(self.config.max_wait)
            if self.phase_max_wait(phase) >= max_wait_real:
                return phase  # Starvation override

            score = self.phase_score(phase)
            if score > best_score:
                best_score = score
                best_phase = phase

        return best_phase

    # ── Controller ACK handling ──

    def process_ack(self, command_id: str, actual_state: Optional[SignalState] = None) -> list[PendingCommand]:
        """Process a controller ACK for a command."""
        commands: list[PendingCommand] = []

        if command_id not in self.pending_commands:
            # Duplicate or unknown ACK — ignore
            self._audit(
                AuditEventType.COMMAND_ACK,
                reason=f"ACK for unknown/already-processed command {command_id}",
                command_id=command_id,
            )
            return commands

        cmd = self.pending_commands.pop(command_id)

        # Check if this ACK is for a superseded command
        # A command is superseded if the desired signals have changed since it was sent
        if cmd.desired_signals != self.desired_signals:
            self._audit(
                AuditEventType.COMMAND_SUPERSEDED,
                reason=f"Late ACK for superseded command {command_id}",
                command_id=command_id,
            )
            return commands

        # Apply actual state
        if actual_state:
            self.actual_signals = actual_state
        else:
            self.actual_signals = cmd.desired_signals

        self._audit(
            AuditEventType.COMMAND_ACK,
            reason=f"Command {command_id} acknowledged",
            command_id=command_id,
            new_state=str(self.actual_signals.as_dict()),
        )

        return commands

    def process_controller_status(self, status: ControllerStatus) -> list[PendingCommand]:
        """Process a controller status change (ONLINE/OFFLINE)."""
        commands: list[PendingCommand] = []
        old_status = self.controller_status
        self.controller_status = status

        if status == ControllerStatus.OFFLINE:
            self._audit(
                AuditEventType.CONTROLLER_OFFLINE,
                reason="Controller went offline",
            )
            if self.mode != JunctionMode.FAILURE:
                self._set_mode(JunctionMode.FAILURE, "Controller offline")
                self.desired_signals = SignalState.all_red()

        elif status == ControllerStatus.ONLINE and old_status != ControllerStatus.ONLINE:
            self._audit(
                AuditEventType.CONTROLLER_ONLINE,
                reason="Controller reconnected",
            )
            # Go through ALL_RED first, then resume
            signals = SignalState.all_red()
            commands.extend(self._set_desired_signals(signals, "Controller reconnect: ALL_RED"))

        return commands

    def _check_ack_timeouts(self) -> list[PendingCommand]:
        """Check for pending commands that have timed out."""
        commands: list[PendingCommand] = []
        now = self.clock.now()
        timed_out = []

        for cmd_id, cmd in list(self.pending_commands.items()):
            elapsed = now - cmd.created_at
            if elapsed >= cmd.ack_timeout:
                if cmd.retries < cmd.max_retries:
                    # Retry with same command_id
                    cmd.retries += 1
                    cmd.created_at = now  # Reset timeout
                    self._audit(
                        AuditEventType.COMMAND_RETRY,
                        reason=f"Retry {cmd.retries}/{cmd.max_retries} for {cmd_id}",
                        command_id=cmd_id,
                    )
                    commands.append(cmd)
                else:
                    # Max retries reached — enter failure mode
                    timed_out.append(cmd_id)

        for cmd_id in timed_out:
            cmd = self.pending_commands.pop(cmd_id)
            self._audit(
                AuditEventType.COMMAND_TIMEOUT,
                reason=f"Command {cmd_id} timed out after {cmd.max_retries} retries",
                command_id=cmd_id,
            )
            if self.mode != JunctionMode.FAILURE:
                self._set_mode(JunctionMode.FAILURE, f"Command timeout: {cmd_id}")
                self.desired_signals = SignalState.all_red()
                self._audit(
                    AuditEventType.FAILURE_ENTER,
                    reason=f"Entering failure mode: command {cmd_id} unacknowledged",
                )

        return commands

    def _check_stale_emergencies(self) -> list[PendingCommand]:
        """Check for stale emergency entries that should be removed."""
        commands: list[PendingCommand] = []
        now = self.clock.now()

        # Check queue
        stale = [e for e in self.emergency_queue if now >= e.stale_timeout]
        for entry in stale:
            self.emergency_queue.remove(entry)
            self._audit(
                AuditEventType.EMERGENCY_STALE,
                reason=f"Emergency {entry.vehicle_id} stale (timeout)",
                vehicle_id=entry.vehicle_id,
                direction=entry.direction,
            )

        # Check active
        if self.active_emergency and now >= self.active_emergency.stale_timeout:
            self._audit(
                AuditEventType.EMERGENCY_STALE,
                reason=f"Active emergency {self.active_emergency.vehicle_id} stale",
                vehicle_id=self.active_emergency.vehicle_id,
            )
            self.active_emergency = None

            if self.emergency_queue:
                commands.extend(self._serve_next_emergency())
            else:
                self._return_from_emergency()

        return commands

    # ── Initialization / Recovery ──

    def initialize_green(self, phase: Phase) -> list[PendingCommand]:
        """Initialize the engine to start with a specific phase as GREEN."""
        commands: list[PendingCommand] = []
        self.current_phase = phase
        self.current_step_type = TransitionStep.GREEN
        self.green_start_time = self.clock.now()
        signals = self._signals_for_phase_green(phase)
        commands.extend(self._set_desired_signals(signals, f"Initialize GREEN for {phase.value}"))
        return commands

    def recover_from_restart(self) -> list[PendingCommand]:
        """
        Recovery after a server restart.
        Set actual_signals = UNKNOWN, desired = ALL_RED, re-issue command.
        """
        commands: list[PendingCommand] = []
        self.actual_signals = SignalState.all_unknown()
        signals = SignalState.all_red()
        commands.extend(self._set_desired_signals(signals, "Restart recovery: ALL_RED"))

        # Drop stale emergencies
        now = self.clock.now()
        self.emergency_queue = deque(e for e in self.emergency_queue if now < e.stale_timeout)

        # Cancel any in-flight transition — restart from ALL_RED
        self.transition_plan = None
        self.in_transition = False
        self.current_step_type = None

        self._audit(
            AuditEventType.RESTART_RECOVERY,
            reason="Server restart recovery initiated",
        )

        return commands

    # ── Status / getters ──

    def get_countdown(self) -> Optional[float]:
        """Get the remaining nominal countdown for the current step."""
        if self.mode == JunctionMode.FAILURE:
            return None

        now = self.clock.now()

        if self.in_transition and self.transition_plan and not self.transition_plan.is_empty:
            step_type, phase, duration = self.transition_plan.current_step
            real_duration = self.config.real_seconds(duration)
            elapsed = now - self.step_start_time
            remaining_real = max(0, real_duration - elapsed)
            return remaining_real * self.config.time_scale  # Convert back to nominal

        if self.mode == JunctionMode.EMERGENCY:
            return None  # Emergency holds indefinitely

        if self.current_step_type == TransitionStep.GREEN:
            if self.mode == JunctionMode.MANUAL and self.manual_hold_start:
                hold_real = self.config.real_seconds(self.config.manual_hold)
                elapsed = now - self.manual_hold_start
                remaining_real = max(0, hold_real - elapsed)
                return remaining_real * self.config.time_scale

            green_real = self.config.real_seconds(self.config.green_duration)
            elapsed = now - self.green_start_time
            remaining_real = max(0, green_real - elapsed)
            return remaining_real * self.config.time_scale

        return None

    def get_alerts(self) -> list[str]:
        """Get current alert messages."""
        alerts = []
        if self.mode == JunctionMode.FAILURE:
            alerts.append("FAILURE MODE: Controller unresponsive or offline")
        if self.controller_status == ControllerStatus.OFFLINE:
            alerts.append("Controller OFFLINE")
        if self.controller_status == ControllerStatus.DEGRADED:
            alerts.append("Controller DEGRADED")
        if self.desired_signals != self.actual_signals:
            alerts.append(f"Signal mismatch: desired={self.desired_signals.as_dict()}, actual={self.actual_signals.as_dict()}")
        for d, s in self.direction_sensor_status.items():
            if s != ControllerStatus.ONLINE:
                alerts.append(f"Sensor {d.value}: {s.value}")
        if self.pending_commands:
            alerts.append(f"{len(self.pending_commands)} pending command(s)")
        for color in [self.actual_signals.north, self.actual_signals.south, self.actual_signals.east, self.actual_signals.west]:
            if color == SignalColor.UNKNOWN:
                alerts.append("Unknown actual signal state — awaiting controller report")
                break
        return alerts

    def get_status(self) -> dict:
        """Get the full junction status for API response."""
        return {
            "junction_id": self.config.junction_id,
            "mode": self.mode.value,
            "phase": self.current_phase.value,
            "controller_status": self.controller_status.value,
            "desired_signals": self.desired_signals.as_dict(),
            "actual_signals": self.actual_signals.as_dict(),
            "queues": {d.value: self.queue_count(d) for d in self.config.directions},
            "emergency_queue": [
                {"vehicle_id": e.vehicle_id, "direction": e.direction.value, "phase": e.phase.value}
                for e in self.emergency_queue
            ],
            "active_emergency": {
                "vehicle_id": self.active_emergency.vehicle_id,
                "direction": self.active_emergency.direction.value,
            } if self.active_emergency else None,
            "manual_queue": [
                {"command_id": m.command_id, "direction": m.direction.value, "status": m.status.value}
                for m in self.manual_queue
            ],
            "active_manual": {
                "command_id": self.active_manual.command_id,
                "direction": self.active_manual.direction.value,
            } if self.active_manual else None,
            "pending_commands": [
                {"command_id": c.command_id, "retries": c.retries}
                for c in self.pending_commands.values()
            ],
            "alerts": self.get_alerts(),
            "countdown": self.get_countdown(),
            "in_transition": self.in_transition,
            "current_step": self.current_step_type.value if self.current_step_type else None,
            "time_scale": self.config.time_scale,
        }

    def drain_audit_buffer(self) -> list[AuditEntry]:
        """Drain and return the audit buffer."""
        entries = list(self.audit_buffer)
        self.audit_buffer.clear()
        return entries
