"""
Persist conversation turns in the workspace (JSONL) to inject history into the system prompt when Claude Code ``resume`` is unavailable, so that a "new session can pick up the chat".

File: ``<workspace>/conversation_turns.jsonl``, one JSON object per line: ``role`` (user|assistant), ``text``, ``ts`` (ISO8601 UTC).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PERSIST_FILENAME = "conversation_turns.jsonl"
# Limit when injecting into the system prompt; truncate from the front, keeping the tail.
#
# Must be counted in **bytes**, not characters: the system prompt is passed to the Claude Code CLI as a single
# command-line argument, and Linux has a hard per-argument limit of MAX_ARG_STRLEN = 32 * PAGE_SIZE = 131072 bytes.
# Chinese takes 3 bytes/char in UTF-8; truncating at 120000 *characters* previously meant up to 360 KB in the worst case,
# far above the limit -- resuming any long session with Chinese history failed at startup with execve (`[Errno 7]
# Argument list too long`), and the error is raised inside the SDK, making the root cause hard to see.
# Leave ample margin here: the base system prompt is ~28 KB and the injection adds up to 64 KB, still within the limit.
MAX_INJECT_BYTES = 64_000


def persist_path(workspace: Path | str) -> Path:
    return Path(workspace).resolve() / PERSIST_FILENAME


def append_turn(workspace: Path | str, role: str, text: str) -> None:
    text = (text or "").strip()
    if not text or role not in ("user", "assistant"):
        return
    p = persist_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "role": role,
        "text": text,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _blocks_from_event_log(workspace: Path | str) -> list[str]:
    """Derive conversation text from log.jsonl.

    Conversation history is no longer stored in a separate file -- it is a view of log.jsonl. Previously log.txt and
    conversation_turns.jsonl were written in parallel; failed writes to the latter were silently swallowed and the two drifted apart.
    """
    from src.event_log import LOG_FILENAME, read_messages

    path = Path(workspace).resolve() / LOG_FILENAME
    if not path.is_file():
        return []

    blocks: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        buf.clear()
        if text:
            blocks.append(f"### Assistant\n{text}")

    for rec, msg in read_messages(path):
        rtype = rec.get("type")
        if rtype == "UserTurn":
            flush()
            text = ((rec.get("payload") or {}).get("text") or "").strip()
            if text:
                blocks.append(f"### User\n{text}")
        elif rtype == "AssistantMessage" and msg is not None:
            for block in getattr(msg, "content", []) or []:
                if type(block).__name__ == "TextBlock":
                    chunk = (getattr(block, "text", "") or "").strip()
                    if chunk:
                        buf.append(chunk)
        elif rtype == "ResultMessage":
            flush()
    flush()
    return blocks


def _blocks_from_legacy_store(workspace: Path | str) -> list[str]:
    """Read the legacy conversation_turns.jsonl (only for historical workspaces that have no log.jsonl yet)."""
    p = persist_path(workspace)
    if not p.is_file():
        return []
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    blocks: list[str] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(o, dict):
            continue
        role = o.get("role")
        text = (o.get("text") or "").strip()
        if not text:
            continue
        if role == "user":
            blocks.append(f"### User\n{text}")
        elif role == "assistant":
            blocks.append(f"### Assistant\n{text}")
    return blocks


def load_persist_context_for_prompt(workspace: Path | str) -> str | None:
    """Format as plain text injectable into the system prompt; keep the tail when too long."""
    blocks = _blocks_from_event_log(workspace) or _blocks_from_legacy_store(workspace)
    if not blocks:
        return None
    out = "\n\n".join(blocks)
    encoded = out.encode("utf-8")
    if len(encoded) > MAX_INJECT_BYTES:
        # Take from the tail, then decode as UTF-8; the cut point may land in the middle of a multibyte character, so errors="ignore"
        # simply drops the broken leading character.
        out = encoded[-MAX_INJECT_BYTES:].decode("utf-8", errors="ignore")
        out = "…[earlier content truncated]…\n\n" + out
    return out


def persist_on_sdk_message(workspace: Path | str, msg: Any, state: dict[str, Any]) -> None:
    """Kept as a compatibility placeholder: conversation history is now derived from ``log.jsonl`` and no longer written separately.

    Previously this accumulated assistant text and wrote it to ``conversation_turns.jsonl``, in parallel with ``log.txt``.
    The two could drift, and assistant text was only persisted per turn at ``ResultMessage`` -- a mid-turn crash lost the whole turn.
    ``log.jsonl`` is written immediately per entry and is more complete; see ``_blocks_from_event_log`` for the derived view.
    """
    return
