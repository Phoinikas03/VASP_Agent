from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEDULER_DIR_NAME = ".scheduler"
STATE_FILE_NAME = "session_state.json"
DB_FILE_NAME = "state.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scheduler_dir(workspace: str | Path) -> Path:
    return Path(workspace).resolve() / SCHEDULER_DIR_NAME


def session_state_path(workspace: str | Path) -> Path:
    return scheduler_dir(workspace) / STATE_FILE_NAME


def load_session_state(workspace: str | Path) -> dict[str, Any]:
    path = session_state_path(workspace)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except json.JSONDecodeError:
        # content is corrupted. Returning {} directly would make the caller generate a new id and overwrite the original file --
        # the possibly recoverable claude_session_id would be lost, and the turns/tasks/events for the old id
        # in sqlite would all become orphans. Keep a copy as evidence first, then proceed.
        _quarantine_corrupt_state(path)
        return {}
    return data if isinstance(data, dict) else {}


def _quarantine_corrupt_state(path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    try:
        path.replace(path.with_suffix(path.suffix + f".corrupt.{stamp}"))
    except OSError:
        return


def load_structured_resume_session_id(workspace: str | Path) -> str | None:
    data = load_session_state(workspace)
    session_id = data.get("claude_session_id")
    return str(session_id) if session_id else None


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    """Open a sqlite connection and set the concurrency parameters.

    A bare ``connect()`` raises ``database is locked`` after 5 seconds when the CLI and WebUI open the same workspace,
    or two WebUIs share the global session index, and callers mostly have no try/except.
    WAL keeps readers and writers from blocking each other; busy_timeout leaves the writer a retry window.
    """
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        pass  # Failure to set this on e.g. a read-only mount should not prevent opening
    return conn


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    # The temp file name includes the pid: a fixed name would let two concurrent writers write the same tmp;
    # rename is atomic, but the content could be an interleaved partial mix.
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())  # Without fsync a power loss could leave an empty file after rename
    tmp.replace(path)


class SchedulerStateStore:
    """Workspace-local scheduler state.

    The legacy ``log.txt`` remains the append-only human/debug log. This store
    keeps the small amount of structured state needed for resume, interruption,
    and task control without parsing repr text on every startup.
    """

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.state_dir = scheduler_dir(self.workspace)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / STATE_FILE_NAME
        self.db_path = self.state_dir / DB_FILE_NAME
        self.conn = connect_sqlite(self.db_path)
        self._init_schema()
        self.scheduler_session_id = self._ensure_scheduler_session_id()
        self.upsert_session(status="created")

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            create table if not exists sessions (
                scheduler_session_id text primary key,
                workspace text not null,
                claude_session_id text,
                status text not null,
                created_at text not null,
                updated_at text not null,
                metadata_json text not null default '{}'
            );

            create table if not exists turns (
                turn_id text primary key,
                scheduler_session_id text not null,
                status text not null,
                user_text text not null,
                created_at text not null,
                updated_at text not null,
                result_json text not null default '{}'
            );

            create table if not exists tasks (
                task_id text primary key,
                scheduler_session_id text not null,
                turn_id text,
                kind text not null,
                status text not null,
                label text not null,
                owner text not null,
                metadata_json text not null default '{}',
                created_at text not null,
                updated_at text not null
            );

            create table if not exists events (
                event_id text primary key,
                scheduler_session_id text not null,
                turn_id text,
                event_type text not null,
                text text not null,
                payload_json text not null default '{}',
                created_at text not null
            );

            -- The three tables originally had only primary keys, while queries always filter by scheduler_session_id,
            -- so every query was a full table scan; events only grows, degrading linearly with session length.
            create index if not exists idx_turns_session on turns(scheduler_session_id);
            create index if not exists idx_tasks_session on tasks(scheduler_session_id, updated_at);
            create index if not exists idx_events_session on events(scheduler_session_id, created_at);
            create index if not exists idx_events_turn on events(turn_id);
            """
        )
        self.conn.commit()

    def _ensure_scheduler_session_id(self) -> str:
        data = load_session_state(self.workspace)
        scheduler_session_id = data.get("scheduler_session_id")
        if not scheduler_session_id:
            scheduler_session_id = str(uuid.uuid4())
            data.update(
                {
                    "schema_version": 1,
                    "scheduler_session_id": scheduler_session_id,
                    "workspace": str(self.workspace),
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            _write_json_atomic(self.state_file, data)
        return str(scheduler_session_id)

    def load_resume_session_id(self) -> str | None:
        return load_structured_resume_session_id(self.workspace)

    def save_claude_session_id(self, claude_session_id: str, *, source: str) -> None:
        # Called for every SDK message (from two places in the call chain), while session_id is almost
        # constant within a session. Without a short-circuit, one turn with dozens of tool calls would produce hundreds of
        # "read whole JSON + write temp file + rename + sqlite commit" cycles.
        if getattr(self, "_last_saved_claude_session_id", None) == claude_session_id:
            return
        data = load_session_state(self.workspace)
        now = utc_now()
        data.update(
            {
                "schema_version": 1,
                "scheduler_session_id": self.scheduler_session_id,
                "workspace": str(self.workspace),
                "claude_session_id": claude_session_id,
                "claude_session_source": source,
                "updated_at": now,
            }
        )
        data.setdefault("created_at", now)
        _write_json_atomic(self.state_file, data)
        self.upsert_session(
            status="active",
            claude_session_id=claude_session_id,
            metadata={"claude_session_source": source},
        )
        self._last_saved_claude_session_id = claude_session_id

    def clear_claude_session_id(self, *, invalid_session_id: str | None = None, source: str) -> None:
        self._last_saved_claude_session_id = None  # Invalidate the cache, otherwise re-saving after clearing would be short-circuited
        data = load_session_state(self.workspace)
        now = utc_now()
        if invalid_session_id and data.get("claude_session_id") == invalid_session_id:
            data.pop("claude_session_id", None)
        data["claude_session_source"] = source
        data["updated_at"] = now
        invalid = data.get("invalid_claude_session_ids")
        if not isinstance(invalid, list):
            invalid = []
        if invalid_session_id and invalid_session_id not in invalid:
            invalid.append(invalid_session_id)
        data["invalid_claude_session_ids"] = invalid
        _write_json_atomic(self.state_file, data)
        self.conn.execute(
            """
            update sessions
               set claude_session_id = null, updated_at = ?
             where scheduler_session_id = ?
            """,
            (now, self.scheduler_session_id),
        )
        self.conn.commit()

    def upsert_session(
        self,
        *,
        status: str,
        claude_session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        row = self.conn.execute(
            "select created_at, claude_session_id, metadata_json from sessions where scheduler_session_id = ?",
            (self.scheduler_session_id,),
        ).fetchone()
        if row:
            old_meta = json.loads(row["metadata_json"] or "{}")
            if metadata:
                old_meta.update(metadata)
            self.conn.execute(
                """
                update sessions
                   set workspace = ?,
                       claude_session_id = coalesce(?, claude_session_id),
                       status = ?,
                       updated_at = ?,
                       metadata_json = ?
                 where scheduler_session_id = ?
                """,
                (
                    str(self.workspace),
                    claude_session_id,
                    status,
                    now,
                    json.dumps(old_meta, ensure_ascii=False),
                    self.scheduler_session_id,
                ),
            )
        else:
            self.conn.execute(
                """
                insert into sessions
                (scheduler_session_id, workspace, claude_session_id, status, created_at, updated_at, metadata_json)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.scheduler_session_id,
                    str(self.workspace),
                    claude_session_id,
                    status,
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        self.conn.commit()

    def record_event(
        self,
        event_type: str,
        *,
        text: str = "",
        payload: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            insert into events
            (event_id, scheduler_session_id, turn_id, event_type, text, payload_json, created_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                self.scheduler_session_id,
                turn_id,
                event_type,
                text,
                json.dumps(payload or {}, ensure_ascii=False),
                utc_now(),
            ),
        )
        self.conn.commit()

    def start_turn(self, turn_id: str, user_text: str) -> None:
        now = utc_now()
        self.conn.execute(
            """
            insert or replace into turns
            (turn_id, scheduler_session_id, status, user_text, created_at, updated_at, result_json)
            values (?, ?, 'running', ?, coalesce((select created_at from turns where turn_id = ?), ?), ?, '{}')
            """,
            (turn_id, self.scheduler_session_id, user_text, turn_id, now, now),
        )
        self.conn.commit()
        self.upsert_session(status="running")
        self.record_event("turn.started", text=user_text, turn_id=turn_id)

    def finish_turn(self, turn_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """
            update turns
               set status = ?, updated_at = ?, result_json = ?
             where turn_id = ?
            """,
            (status, utc_now(), json.dumps(result or {}, ensure_ascii=False), turn_id),
        )
        self.conn.commit()
        self.upsert_session(status="idle" if status == "completed" else status)
        self.record_event("turn.finished", text=status, payload=result or {}, turn_id=turn_id)

    def upsert_task(
        self,
        *,
        task_id: str,
        kind: str,
        status: str,
        label: str,
        owner: str,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        row = self.conn.execute(
            "select metadata_json from tasks where task_id = ?",
            (task_id,),
        ).fetchone()
        if row:
            old_meta = json.loads(row["metadata_json"] or "{}")
            if metadata:
                old_meta.update(metadata)
            self.conn.execute(
                """
                update tasks
                   set turn_id = coalesce(?, turn_id),
                       kind = ?,
                       status = ?,
                       label = ?,
                       owner = ?,
                       metadata_json = ?,
                       updated_at = ?
                 where task_id = ?
                """,
                (
                    turn_id,
                    kind,
                    status,
                    label,
                    owner,
                    json.dumps(old_meta, ensure_ascii=False),
                    now,
                    task_id,
                ),
            )
        else:
            self.conn.execute(
                """
                insert into tasks
                (task_id, scheduler_session_id, turn_id, kind, status, label, owner, metadata_json, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    self.scheduler_session_id,
                    turn_id,
                    kind,
                    status,
                    label,
                    owner,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        self.conn.commit()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "select * from tasks where task_id = ?",
            (task_id,),
        ).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        sql = "select * from tasks where scheduler_session_id = ?"
        params: list[Any] = [self.scheduler_session_id]
        if not include_terminal:
            sql += " and lower(status) not in ('completed', 'failed', 'stopped', 'cancelled', 'killed')"
        sql += " order by updated_at desc"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._task_from_row(row) for row in rows]

    def mark_task_status(
        self,
        task_id: str,
        status: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if not task:
            return None
        self.upsert_task(
            task_id=task_id,
            kind=task["kind"],
            status=status,
            label=task["label"],
            owner=task["owner"],
            turn_id=task.get("turn_id"),
            metadata=metadata,
        )
        return self.get_task(task_id)

    def _task_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        return data
