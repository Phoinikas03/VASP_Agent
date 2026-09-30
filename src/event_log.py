"""Structured session log ``log.jsonl``: writing, reading, and lossless conversion from the legacy ``log.txt``.

Replaces the two files that previously coexisted:

- ``log.txt``    -- one ``repr(SDK message)`` per line, read back via ``eval``; messages outside the type whitelist were silently dropped
- ``conversation_turns.jsonl`` -- a derived view of ``log.txt`` (plain user/assistant text + timestamps)

The new format is one JSON object per line:

``{"v":1,"seq":int,"ts":"...","type":"AssistantMessage","session_id":...,
   "turn_id":...,"payload":{...}}``

Key convention: every nested dataclass in ``payload`` carries ``__type__``. The type cannot be
inferred from the field signature -- ``ServerToolUseBlock`` and ``ToolUseBlock`` have identical
fields (``id``/``input``/``name``), so guessing the type is bound to collide.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import claude_agent_sdk.types as _sdk_types

LOG_FILENAME = "log.jsonl"
LEGACY_LOG_FILENAME = "log.txt"
SCHEMA_VERSION = 1

#: All dataclass types in the SDK. Used for tagging on serialization and for restoring from repr,
#: avoiding a hard-coded whitelist -- the original implementation listed only 8 class names, so new
#: SDK message types were silently dropped.
SDK_TYPES: dict[str, type] = {
    name: obj
    for name, obj in vars(_sdk_types).items()
    if inspect.isclass(obj) and dataclasses.is_dataclass(obj)
}

TYPE_KEY = "__type__"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------

def encode(obj: Any) -> Any:
    """Recursively convert to a JSON-serializable structure, tagging every dataclass with ``__type__``."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {TYPE_KEY: type(obj).__name__}
        for f in dataclasses.fields(obj):
            out[f.name] = encode(getattr(obj, f.name))
        return out
    if isinstance(obj, dict):
        return {str(k): encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [encode(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)  # datetime, Path, etc. fall back to strings so a serialization failure never drops a line


def decode(value: Any) -> Any:
    """Inverse of ``encode``. An unknown ``__type__`` is kept as a plain dict so no data is lost."""
    if isinstance(value, dict):
        tname = value.get(TYPE_KEY)
        payload = {k: decode(v) for k, v in value.items() if k != TYPE_KEY}
        cls = SDK_TYPES.get(tname) if tname else None
        if cls is None:
            return payload if tname is None else {TYPE_KEY: tname, **payload}
        known = {f.name for f in dataclasses.fields(cls)}
        try:
            return cls(**{k: v for k, v in payload.items() if k in known})
        except Exception:
            return {TYPE_KEY: tname, **payload}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def _extract_session_id(msg: Any) -> str | None:
    sid = getattr(msg, "session_id", None)
    if not sid:
        data = getattr(msg, "data", None)
        if isinstance(data, dict):
            sid = data.get("session_id")
    return str(sid) if sid else None


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

class EventLogWriter:
    """Append-only writer for ``log.jsonl``. Holds one long-lived open handle and flushes after every record."""

    def __init__(self, path: Path | str, *, start_seq: int = 0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = start_seq
        self._fh = self.path.open("a", encoding="utf-8")

    @classmethod
    def open_for_workspace(cls, workspace: Path | str) -> "EventLogWriter":
        p = Path(workspace) / LOG_FILENAME
        return cls(p, start_seq=_last_seq(p))

    def _write(self, record: dict[str, Any]) -> None:
        self._seq += 1
        record["seq"] = self._seq
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def append_sdk_message(self, msg: Any, *, turn_id: str | None = None) -> None:
        self._write(
            {
                "v": SCHEMA_VERSION,
                "ts": _now(),
                "type": type(msg).__name__,
                "session_id": _extract_session_id(msg),
                "turn_id": turn_id,
                "payload": encode(msg),
            }
        )

    def append_user_turn(self, text: str, *, turn_id: str | None = None) -> None:
        self._write(
            {
                "v": SCHEMA_VERSION,
                "ts": _now(),
                "type": "UserTurn",
                "session_id": None,
                "turn_id": turn_id,
                "payload": {"role": "user", "text": text},
            }
        )

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


def find_workspace_root(start: Path | str) -> Path | None:
    """Walk up from a task directory to the workspace root containing ``log.jsonl``.

    Each VASP case has its own subdirectory (EOS volume points, three-stage adsorption), while the
    log is one per workspace, so an external process must first locate the root.
    """
    current = Path(start).resolve()
    for candidate in (current, *current.parents):
        if (candidate / LOG_FILENAME).is_file():
            return candidate
        if (candidate / "runs").is_dir() and candidate.name != "runs":
            break  # Reached the repository root; stop walking up
    return None


def append_external_record(
    workspace: Path | str,
    *,
    type: str,
    payload: dict[str, Any],
) -> bool:
    """Append a record from a process outside the agent session (e.g. the vasp_runner run lifecycle).

    How it differs from :class:`EventLogWriter`, and why it must exist separately:

    - **No seq.** ``seq`` requires reading the whole file, which is O(n) and gets computed wrongly
      under concurrency; several vasp_runner processes may write the same log at once. It is left
      empty here and ordering relies on ``ts``.
    - **Single open-append-close.** On POSIX, appending a short line in append mode is atomic,
      so records must stay small (do not stuff in a whole OUTCAR).

    Returns whether the write succeeded; a failure must never affect the caller's main flow.
    """
    try:
        path = Path(workspace).resolve() / LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "v": SCHEMA_VERSION,
                "seq": None,
                "ts": _now(),
                "type": type,
                "session_id": None,
                "turn_id": None,
                "payload": encode(payload),
            },
            ensure_ascii=False,
        )
        if len(line.encode("utf-8")) > 3500:
            # Beyond this length append atomicity is no longer guaranteed; truncate rather than risk interleaving
            payload = {"truncated": True, "type": type, "summary": str(payload)[:1500]}
            line = json.dumps(
                {
                    "v": SCHEMA_VERSION, "seq": None, "ts": _now(), "type": type,
                    "session_id": None, "turn_id": None, "payload": payload,
                },
                ensure_ascii=False,
            )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception:
        return False


def append_tool_record(
    workspace: Path | str,
    *,
    tool: str,
    ok: bool,
    duration_ms: int,
    args: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record a structured trace of one MCP tool call, appended to the workspace ``log.jsonl``.

    The SDK only records the tool's **name and natural-language return**. The most valuable
    supervision signals for VASP -- which POTCARs were picked, how the k-mesh was derived, what the
    element order is, how long it took, whether it succeeded -- previously existed only in the prose
    returned to the model, with no machine-readable record.

    Tool calls are infrequent (historically 137 calls across 88 trajectories), so the cost of
    opening and closing the file each time is negligible, and in exchange the writer does not have to
    be threaded through every tool closure. A failure must never affect the tool's own result.
    """
    try:
        path = Path(workspace).resolve() / LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "v": SCHEMA_VERSION,
            "seq": _last_seq(path) + 1,
            "ts": _now(),
            "type": "ToolInvocation",
            "session_id": None,
            "turn_id": None,
            "payload": {
                "tool": tool,
                "ok": ok,
                "duration_ms": duration_ms,
                "args": encode(args or {}),
                "detail": encode(detail or {}),
                "error": error,
            },
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return  # Trace recording must never make the tool call itself fail


def _last_seq(path: Path) -> int:
    """Continue from the existing seq so numbering does not restart after a session is reopened."""
    if not path.is_file():
        return 0
    last = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = int(json.loads(line).get("seq") or last)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
    except OSError:
        return 0
    return last


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def read_records(path: Path | str) -> Iterator[dict[str, Any]]:
    """Yield records one by one. Bad lines are skipped rather than aborting the whole file."""
    p = Path(path)
    if not p.is_file():
        return
    try:
        fh = p.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def read_messages(path: Path | str) -> Iterator[tuple[dict[str, Any], Any]]:
    """Yield ``(record, restored SDK object or None)``. The object is None for a UserTurn."""
    for rec in read_records(path):
        if rec.get("type") == "UserTurn":
            yield rec, None
        else:
            yield rec, decode(rec.get("payload"))


def last_session_id(path: Path | str) -> str | None:
    """Return the last Claude session_id that appears, for ``--resume``.

    Reads the field directly instead of running a regex over the whole log.
    """
    found = None
    for rec in read_records(path):
        sid = rec.get("session_id")
        if sid:
            found = str(sid)
    return found


def resolve_log_path(workspace: Path | str) -> Path:
    """Return the log to read for this workspace: jsonl first, falling back to the legacy log.txt."""
    ws = Path(workspace)
    new = ws / LOG_FILENAME
    if new.is_file():
        return new
    legacy = ws / LEGACY_LOG_FILENAME
    return legacy if legacy.is_file() else new


# --------------------------------------------------------------------------
# Legacy log.txt -> log.jsonl conversion
# --------------------------------------------------------------------------

def parse_legacy_line(line: str) -> dict[str, Any] | None:
    """Convert one line of the legacy ``log.txt`` into a record. User lines are JSON; the rest are SDK ``repr``.

    The ``eval`` namespace is all SDK dataclass types; the original implementation listed only 8, so
    types such as ``TaskStartedMessage`` failed to parse and were silently dropped.
    """
    s = line.strip()
    if not s:
        return None
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return None
        return {
            "v": SCHEMA_VERSION,
            "ts": None,
            "type": "UserTurn",
            "session_id": None,
            "turn_id": None,
            "payload": {"role": "user", "text": obj.get("text", "")},
        }
    try:
        obj = eval(s, {"__builtins__": {}}, SDK_TYPES)  # noqa: S307 - controlled namespace
    except Exception:
        return None
    return {
        "v": SCHEMA_VERSION,
        "ts": None,
        "type": type(obj).__name__,
        "session_id": _extract_session_id(obj),
        "turn_id": None,
        "payload": encode(obj),
    }


def convert_legacy_log(src: Path | str, dst: Path | str) -> dict[str, int]:
    """Convert ``log.txt`` to ``log.jsonl`` and return statistics. The original file is left untouched.

    The conversion only counts as successful if ``failed`` is 0; callers should decide on that basis whether to keep the result.
    """
    src, dst = Path(src), Path(dst)
    stats = {"lines": 0, "written": 0, "failed": 0}
    out: list[str] = []
    for raw in src.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        stats["lines"] += 1
        rec = parse_legacy_line(raw)
        if rec is None:
            stats["failed"] += 1
            continue
        rec["seq"] = stats["lines"]
        try:
            out.append(json.dumps(rec, ensure_ascii=False))
        except (TypeError, ValueError):
            stats["failed"] += 1
            continue
        stats["written"] += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return stats
