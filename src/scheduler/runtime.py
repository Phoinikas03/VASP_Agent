from __future__ import annotations

import inspect
import shlex
import uuid
from dataclasses import dataclass
from typing import Any

from .state_store import SchedulerStateStore
from .task_registry import TERMINAL_TASK_STATUSES, TaskRegistry, format_task_list


@dataclass
class ControlResponse:
    handled: bool
    text: str = ""
    thinking: bool = False
    done: bool = True


class AgentScheduler:
    """Thin runtime controller around ClaudeSDKClient.

    It does not replace Claude Code. It keeps host-side state and routes
    user-level controls through one place so CLI and WebUI behave consistently.
    """

    def __init__(self, client: Any, store: SchedulerStateStore, tasks: TaskRegistry) -> None:
        self.client = client
        self.store = store
        self.tasks = tasks
        self.busy = False
        self.interrupt_in_flight = False
        self.current_turn_id: str | None = None
        self.pending_after_interrupt: str | None = None

    async def submit(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.busy:
            await self.interrupt(pending_text=text)
            return
        turn_id = str(uuid.uuid4())
        self.current_turn_id = turn_id
        self.pending_after_interrupt = None
        self.busy = True
        self.store.start_turn(turn_id, text)
        try:
            await self.client.query(text)
        except Exception as exc:
            # After a failed query no ResultMessage will arrive to reset the state. Without rolling back here,
            # busy stays True forever, all later input is treated as "busy" and turned into an interrupt,
            # and the session hangs completely without leaving any record.
            self.busy = False
            self.current_turn_id = None
            self.store.finish_turn(turn_id, "failed", {"error": f"query failed: {exc}"})
            self.store.record_event(
                "control.submit_failed",
                text=str(exc),
                payload={"turn_id": turn_id},
                turn_id=turn_id,
            )
            raise

    async def interrupt(self, pending_text: str | None = None) -> None:
        pending = (pending_text or "").strip()
        if pending:
            # Two consecutive submissions while busy would overwrite the previous pending one, which was already echoed to the user.
            # Queue it instead of dropping it; complete_result takes them in order.
            if self.pending_after_interrupt:
                self.pending_after_interrupt = f"{self.pending_after_interrupt}\n\n{pending}"
            else:
                self.pending_after_interrupt = pending
        if not self.busy:
            if pending:
                await self.submit(pending)
            return
        if self.interrupt_in_flight:
            return
        self.interrupt_in_flight = True
        self.store.record_event(
            "control.interrupt",
            text="interrupt current Claude turn",
            payload={"has_pending_text": bool(pending)},
            turn_id=self.current_turn_id,
        )
        try:
            await self.client.interrupt()
        except Exception:
            # A failed interrupt also yields no ResultMessage, so reset here; otherwise later interrupts
            # would be blocked by interrupt_in_flight and could never interrupt again.
            self.interrupt_in_flight = False
            raise

    def observe_message(self, msg: Any) -> None:
        session_id = self._extract_session_id(msg)
        if session_id:
            self.store.save_claude_session_id(session_id, source=f"message:{type(msg).__name__}")
        self.tasks.observe_message(msg, turn_id=self.current_turn_id)

    def complete_result(self, msg: Any, *, failed: bool) -> str:
        session_id = self._extract_session_id(msg)
        if session_id:
            self.store.save_claude_session_id(session_id, source="result")
        if not self.busy and not self.current_turn_id:
            # A late result from a previous turn (e.g. delivered after an interrupt) must not clear the current turn state.
            return ""
        if self.current_turn_id:
            # ResultMessage already carries duration/cost/token usage, all of which used to be discarded.
            # These are the only source for trajectory distillation to judge which step is expensive or stuck.
            payload = {
                "subtype": getattr(msg, "subtype", None),
                "num_turns": getattr(msg, "num_turns", None),
                "session_id": session_id,
                "result": getattr(msg, "result", None),
                "duration_ms": getattr(msg, "duration_ms", None),
                "duration_api_ms": getattr(msg, "duration_api_ms", None),
                "total_cost_usd": getattr(msg, "total_cost_usd", None),
                "usage": getattr(msg, "usage", None),
                "model_usage": getattr(msg, "model_usage", None),
                "stop_reason": getattr(msg, "stop_reason", None),
                "permission_denials": getattr(msg, "permission_denials", None),
                "api_error_status": getattr(msg, "api_error_status", None),
            }
            self.store.finish_turn(
                self.current_turn_id,
                "failed" if failed else "completed",
                payload,
            )
        self.busy = False
        self.interrupt_in_flight = False
        self.current_turn_id = None
        pending = (self.pending_after_interrupt or "").strip()
        self.pending_after_interrupt = None
        return pending

    async def handle_control_command(self, text: str, *, allow_interrupt: bool = False) -> ControlResponse:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return ControlResponse(False)
        try:
            parts = shlex.split(stripped)
        except ValueError as exc:
            return ControlResponse(True, f"Failed to parse scheduler command: {exc}")
        if not parts:
            return ControlResponse(False)

        command = parts[0].lower()
        if command in {"/scheduler-help", "/scheduler"}:
            return ControlResponse(True, self._help_text())
        if command == "/tasks":
            return ControlResponse(True, format_task_list(self.store.list_tasks(include_terminal=True)))
        if command == "/running-tasks":
            return ControlResponse(True, format_task_list(self.store.list_tasks(include_terminal=False)))
        if command == "/interrupt":
            if not allow_interrupt:
                return ControlResponse(False)
            await self.interrupt()
            return ControlResponse(True, "Interrupt sent to Claude Code.", thinking=self.busy, done=not self.busy)
        if command == "/stop-claude-task":
            if len(parts) != 2:
                return ControlResponse(True, "Usage: /stop-claude-task <task_id>")
            return await self._stop_claude_task(parts[1])
        if command == "/stop-vasp-task":
            if len(parts) != 2:
                return ControlResponse(True, "Usage: /stop-vasp-task <task_id>")
            return self._stop_vasp_task(parts[1])
        return ControlResponse(False)

    async def _stop_claude_task(self, task_id: str) -> ControlResponse:
        task = self.store.get_task(task_id)
        if not task:
            return ControlResponse(True, f"Claude task not found: {task_id}")
        if task.get("kind") != "claude":
            return ControlResponse(True, f"Task `{task_id}` has kind `{task.get('kind')}`, not a Claude task.")
        if str(task.get("status", "")).lower() in TERMINAL_TASK_STATUSES:
            return ControlResponse(True, f"Task `{task_id}` is already in `{task.get('status')}` state.")

        stop_task = getattr(self.client, "stop_task", None)
        if not callable(stop_task):
            self.store.record_event(
                "control.stop_claude_task.unsupported",
                text="ClaudeSDKClient has no stop_task method",
                payload={"task_id": task_id},
                turn_id=self.current_turn_id,
            )
            return ControlResponse(
                True,
                (
                    "The installed claude-agent-sdk only exposes `interrupt()`, "
                    "and has no `stop_task()` the host can call directly, so subtasks cannot be stopped in real time "
                    f"like the native Claude Code UI (task `{task_id}`). You can first `/interrupt` the current turn, "
                    "then have the agent handle the task with Claude Code's `TaskStop` tool."
                ),
            )

        result = stop_task(task_id)
        if inspect.isawaitable(result):
            await result
        self.store.mark_task_status(task_id, "stopped", metadata={"stop_reason": "scheduler command"})
        self.store.record_event(
            "control.stop_claude_task",
            text=f"stopped Claude task {task_id}",
            payload={"task_id": task_id},
            turn_id=self.current_turn_id,
        )
        return ControlResponse(True, f"Requested stop of Claude task `{task_id}`.")

    def _stop_vasp_task(self, task_id: str) -> ControlResponse:
        task = self.store.get_task(task_id)
        if not task:
            return ControlResponse(True, f"VASP task not found: {task_id}")
        if task.get("kind") != "vasp":
            return ControlResponse(True, f"Task `{task_id}` has kind `{task.get('kind')}`, not a VASP task.")
        self.store.record_event(
            "control.stop_vasp_task.refused",
            text="VASP termination requires state-backed runner metadata",
            payload={"task_id": task_id},
            turn_id=self.current_turn_id,
        )
        return ControlResponse(
            True,
            (
                "Terminating a VASP task must go through `.claude/skills/run_vasp/scripts/terminate.py` "
                "with exact work-dir/job-id evidence; the scheduler has not yet received "
                f"state-backed metadata for task `{task_id}` from vasp_runner, so the stop is refused."
            ),
        )

    def _extract_session_id(self, msg: Any) -> str | None:
        session_id = getattr(msg, "session_id", None)
        data = getattr(msg, "data", None)
        if not session_id and isinstance(data, dict):
            session_id = data.get("session_id")
        return str(session_id) if session_id else None

    def _help_text(self) -> str:
        return (
            "Scheduler commands:\n"
            "- `/tasks` list recorded tasks\n"
            "- `/running-tasks` list only non-terminal tasks\n"
            "- `/interrupt` interrupt the current Claude turn (works in the CLI; in the Web UI the stop button is more natural)\n"
            "- `/stop-claude-task <task_id>` try to stop a Claude Code background task (depends on whether the SDK exposes stop_task)\n"
            "- `/stop-vasp-task <task_id>` reserved for the state-backed VASP runner; refuses to terminate without sufficient evidence"
        )
