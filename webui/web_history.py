"""Restore the web display event stream from log.txt, and record user input lines so the full conversation history is kept."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# Distinguish from other log lines: single-line JSON
USER_LOG_KEY = "_vasp_agent_user"

# TodoWrite activeForm/content can be very long; truncate in the status bar so it does not break the layout
TODO_STATUS_MAX_LEN = 120


def todo_write_in_progress_label(tool_input: Any) -> str | None:
    """
    Extract a short description of the current in_progress item from the TodoWrite input, for the Web status bar and log replay.
    Fixes: in a long turn the model first writes "step one" text and then starts surface/absorbed, but if the text is not updated the UI looks stuck on step one.
    """
    if not isinstance(tool_input, dict):
        return None
    todos = tool_input.get("todos")
    if not isinstance(todos, list):
        return None
    for t in todos:
        if not isinstance(t, dict):
            continue
        if t.get("status") != "in_progress":
            continue
        label = (t.get("activeForm") or t.get("content") or "").strip()
        if not label:
            continue
        if len(label) > TODO_STATUS_MAX_LEN:
            label = label[: TODO_STATUS_MAX_LEN - 1] + "…"
        return label
    return None


_VALID_TODO_STATUSES = frozenset({"completed", "in_progress", "pending"})
TODO_LABEL_MAX_LEN = 200


def todo_write_items_for_ui(tool_input: Any) -> list[dict[str, str]]:
    """
    Convert the TodoWrite input into the structured list used by the right-hand Todo panel.
    Each item: {"label": str, "status": "completed"|"in_progress"|"pending"}
    """
    out: list[dict[str, str]] = []
    if not isinstance(tool_input, dict):
        return out
    todos = tool_input.get("todos")
    if not isinstance(todos, list):
        return out
    for t in todos:
        if not isinstance(t, dict):
            continue
        raw = t.get("status")
        status = raw if raw in _VALID_TODO_STATUSES else "pending"
        # The right-hand Todo panel should show the task name itself, not status phrases such as "completed/pending/in progress".
        # activeForm can still be used for the status bar, but panel labels prefer content.
        label = (t.get("content") or t.get("activeForm") or "").strip()
        if not label:
            continue
        if len(label) > TODO_LABEL_MAX_LEN:
            label = label[: TODO_LABEL_MAX_LEN - 1] + "…"
        out.append({"label": label, "status": status})
    return out


#: Markers identifying an injected Skill body. If the CLI wording changes these will stop matching, and the whole SKILL.md
#: would be dumped as plain text into the chat stream (exactly the part distillation most wants to identify), so several
#: candidates are listed with a structural fallback instead of relying on a single prefix.
_SKILL_INJECTION_PREFIXES = (
    "Base directory for this skill:",
    "Base directory for skill:",
)


def is_skill_injection_context_text(text: str) -> bool:
    """Decide whether a UserMessage/TextBlock is the SKILL.md body injected by the Skill tool.

    Besides the ToolResultBlock, the Skill tool also injects the entire SKILL.md separately.
    """
    if not text or not isinstance(text, str):
        return False
    head = text.lstrip()
    if head.startswith(_SKILL_INJECTION_PREFIXES):
        return True
    # Structural fallback: the first line mentions a skill directory/path and the body carries YAML frontmatter.
    first_line, _, rest = head.partition("\n")
    if len(first_line) < 200 and "skill" in first_line.lower():
        if ":" in first_line and rest.lstrip().startswith("---"):
            return True
    return False


def write_user_turn_log(log_writer, text: str, *, turn_id: str | None = None) -> None:
    """Write one line before issuing a query, so that a page reload can restore what the user said.

    Historically this wrote to both log.txt and conversation_turns.jsonl, which could drift (failed writes to the latter
    were silently swallowed). Now only log.jsonl is written, and assistant/user text is derived from it.
    """
    log_writer.append_user_turn(text, turn_id=turn_id)


def _format_tool_result_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    try:
                        parts.append(json.dumps(item, ensure_ascii=False, indent=2))
                    except Exception:
                        parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _eval_sdk_message(line: str) -> Any | None:
    """Restore a single-line repr from the old log.txt to an SDK message object.

    The namespace is all SDK dataclass types. Previously 8 class names were hardcoded, so
    ``TaskStartedMessage`` / ``TaskNotificationMessage`` etc. failed to parse and were silently dropped --
    in real history logs 502 background-task messages vanished from the replay because of this.
    """
    from src.event_log import SDK_TYPES

    line = line.strip()
    if not line:
        return None
    if line.startswith("StreamEvent"):
        return None
    try:
                return eval(line, {"__builtins__": {}}, SDK_TYPES)  # noqa: S307 - controlled namespace
    except Exception:
        return None


def sdk_message_to_ui_events(
    msg: Any,
    *,
    format_tool_result: Callable[[Any], str],
    result_failed: Callable[[Any], bool],
) -> list[dict[str, Any]]:
    """Like web_agent_loop, convert a single SDK message into a list of frontend events (excluding status/done)."""
    events: list[dict[str, Any]] = []
    msg_type = type(msg).__name__

    # Background-task messages: replay must match the live display, otherwise after a page refresh the
    # start/stop records of long VASP tasks vanish entirely (see _dispatch_message_to_web in src/main.py).
    subtype = getattr(msg, "subtype", "") or ""
    if subtype in {"task_notification", "task_started", "task_updated"}:
        data = getattr(msg, "data", None)
        data = data if isinstance(data, dict) else {}
        if subtype == "task_notification":
            summary = data.get("summary") or ""
            if summary:
                events.append({"type": "agent_text", "text": f"[Background task] {summary}"})
        elif subtype == "task_started":
            desc = data.get("description") or ""
            if desc:
                events.append({"type": "agent_text", "text": f"[Background task] started: {desc}"})
        else:
            task_id = data.get("task_id") or ""
            patch = data.get("patch") if isinstance(data.get("patch"), dict) else {}
            status = patch.get("status") or data.get("status") or "updated"
            events.append({"type": "agent_text", "text": f"[Background task] {task_id} {status}"})
        return events
    if msg_type == "SystemMessage":
        return events

    if msg_type == "AssistantMessage" or isinstance(msg, AssistantMessage):
        for block in getattr(msg, "content", []):
            bt = type(block).__name__
            if bt == "TextBlock" or isinstance(block, TextBlock):
                events.append({"type": "agent_text", "text": block.text})
            elif bt == "ThinkingBlock" or isinstance(block, ThinkingBlock):
                events.append(
                    {"type": "agent_text", "text": "[Thinking]\n" + getattr(block, "thinking", "")}
                )
            elif bt == "ToolUseBlock" or getattr(block, "type", None) == "tool_use":
                tname = getattr(block, "name", "?")
                tid = getattr(block, "id", "") or ""
                try:
                    input_str = json.dumps(block.input, indent=2, ensure_ascii=False)
                except Exception:
                    input_str = str(getattr(block, "input", ""))
                events.append(
                    {
                        "type": "tool_use",
                        "name": tname,
                        "input_str": input_str,
                        "tool_use_id": tid,
                    }
                )
                if tname == "TodoWrite":
                    tw_in = getattr(block, "input", None) or {}
                    events.append(
                        {
                            "type": "todo_update",
                            "todos": todo_write_items_for_ui(tw_in),
                        }
                    )
                    label = todo_write_in_progress_label(tw_in)
                    if label:
                        events.append(
                            {
                                "type": "status",
                                "text": f"In progress: {label}",
                                "thinking": True,
                            }
                        )

    elif msg_type == "UserMessage" or isinstance(msg, UserMessage):
        raw = getattr(msg, "content", None)
        blocks = raw if isinstance(raw, list) else []
        for block in blocks:
            if isinstance(block, ToolResultBlock):
                tid = getattr(block, "tool_use_id", "") or ""
                err = getattr(block, "is_error", None)
                is_err = bool(err) if err is not None else False
                body = format_tool_result(getattr(block, "content", None))
                events.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "is_error": is_err,
                        "content_str": body,
                    }
                )
            elif isinstance(block, TextBlock):
                if is_skill_injection_context_text(block.text):
                    events.append(
                        {
                            "type": "agent_text",
                            "text": block.text,
                            "collapsed": True,
                            "collapsed_label": "Skill body (click to expand)",
                        }
                    )
                else:
                    events.append({"type": "agent_text", "text": "[Context]\n" + block.text})

    elif msg_type == "ResultMessage" or isinstance(msg, ResultMessage):
        failed = result_failed(msg)
        events.append(
            {
                "type": "result",
                "turns": getattr(msg, "num_turns", 0),
                "error": failed,
                "subtype": getattr(msg, "subtype", None) or "",
                "summary": getattr(msg, "result", None) or "",
            }
        )

    return events


def _prepend_replay_notice_if_needed(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """If the log has no user lines, or the first half lacks them, prepend a notice to the replay list so it is not mistaken for a broken page."""
    if not events:
        return events
    has_user = any(e.get("type") == "user_message" for e in events)
    if not has_user:
        notice = {
            "type": "agent_text",
            "text": (
                "⚠️ **Note**: this log.txt has **no** recorded user input lines (`_vasp_agent_user`), "
                "so the \"You\" bubbles cannot be shown. The current version writes that line before every turn; input produced **after continuing this session** will appear in the record and replay."
            ),
        }
        return [notice] + events
    if events[0].get("type") != "user_message":
        notice = {
            "type": "agent_text",
            "text": (
                "⚠️ **Note**: the **first half** of this record lacks user input lines (older version or not written), "
                "so the first few turns will not show \"You\"; any user bubbles that appear later come from turns after recording was enabled."
            ),
        }
        return [notice] + events
    return events


def parse_log_file_to_ui_events(
    log_path: Path,
    *,
    format_tool_result: Callable[[Any], str] | None = None,
    result_failed: Callable[[Any], bool] | None = None,
) -> list[dict[str, Any]]:
    """Parse the session log and produce an event list consistent with the WebSocket protocol.

    ``log.jsonl`` takes the structured path; the legacy ``log.txt`` that has not been migrated is still parsed as
    "user-line JSON + SDK repr lines", so historical sessions remain replayable.
    """
    fmt = format_tool_result or _format_tool_result_content
    if result_failed is None:
        from src.result_message import result_message_indicates_failure

        result_failed = result_message_indicates_failure

    if not log_path.is_file():
        return []

    events: list[dict[str, Any]] = []

    if log_path.name.endswith(".jsonl"):
        from src.event_log import read_messages

        for rec, msg in read_messages(log_path):
            if rec.get("type") == "UserTurn":
                text = (rec.get("payload") or {}).get("text", "")
                events.append({"type": "user_message", "text": str(text)})
                continue
            if msg is None:
                continue
            events.extend(
                sdk_message_to_ui_events(msg, format_tool_result=fmt, result_failed=result_failed)
            )
        return _prepend_replay_notice_if_needed(events)

    try:
        f = log_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []

    with f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                obj = None
            # Consistent with write_user_turn_log; relaxed so that "contains _vasp_agent_user and text" is enough to parse
            if isinstance(obj, dict) and USER_LOG_KEY in obj and "text" in obj:
                events.append({"type": "user_message", "text": str(obj.get("text", ""))})
                continue

            msg = _eval_sdk_message(line)
            if msg is None:
                continue
            events.extend(
                sdk_message_to_ui_events(
                    msg,
                    format_tool_result=fmt,
                    result_failed=result_failed,
                )
            )

    return _prepend_replay_notice_if_needed(events)
