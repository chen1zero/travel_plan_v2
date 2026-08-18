"""SQLite persistence for tasks, SSE events, and completed plans."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Tuple
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_event_metadata(value: Any) -> Dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


class RevisionConflictError(ValueError):
    """Raised when an itinerary edit uses an old plan revision."""


class SessionContextError(ValueError):
    """Raised when a follow-up does not target the current session plan."""


class TaskStateConflictError(ValueError):
    """Raised when a terminal task is completed or failed again."""


class SQLitePlanRepository:
    """Small durable repository suitable for a single API deployment."""

    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    current_plan_id TEXT,
                    title TEXT,
                    archived_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    current_stage TEXT,
                    plan_id TEXT,
                    error_message TEXT,
                    error_code TEXT,
                    error_retryable INTEGER,
                    error_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'agent',
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    stage TEXT,
                    message TEXT NOT NULL,
                    plan_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );

                CREATE INDEX IF NOT EXISTS events_task_id_event_id
                ON events(task_id, event_id);
                """
            )
            self._ensure_column(
                connection,
                "sessions",
                "user_id",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "sessions",
                "title",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "sessions",
                "archived_at",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "tasks",
                "session_id",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "tasks",
                "previous_plan_id",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "tasks",
                "error_code",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "tasks",
                "error_retryable",
                "INTEGER",
            )
            self._ensure_column(
                connection,
                "tasks",
                "error_id",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "events",
                "metadata_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                connection,
                "plans",
                "session_id",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "plans",
                "previous_plan_id",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "plans",
                "context_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                connection,
                "plans",
                "source_type",
                "TEXT NOT NULL DEFAULT 'agent'",
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_session_created
                ON tasks(session_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_plans_session_revision
                ON plans(session_id, revision)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_user_updated
                ON sessions(user_id, updated_at DESC, session_id DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_active_updated
                ON sessions(updated_at DESC, session_id DESC)
                WHERE archived_at IS NULL
                """
            )
            self._migrate_legacy_sessions(connection)
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    @staticmethod
    def _migrate_legacy_sessions(
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute(
            """
            SELECT t.task_id, t.request_json, t.created_at, t.updated_at,
                   p.plan_id, p.plan_json, p.generated_at, p.context_json
            FROM tasks t
            LEFT JOIN plans p ON p.task_id = t.task_id
            WHERE t.session_id IS NULL
            ORDER BY t.created_at
            """
        ).fetchall()
        for row in rows:
            session_id = f"session_{uuid4().hex}"
            current_plan_id = row["plan_id"]
            updated_at = row["generated_at"] or row["updated_at"]
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, current_plan_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    current_plan_id,
                    row["created_at"],
                    updated_at,
                ),
            )
            connection.execute(
                "UPDATE tasks SET session_id = ? WHERE task_id = ?",
                (session_id, row["task_id"]),
            )
            if current_plan_id:
                context_json = row["context_json"] or "{}"
                if context_json == "{}":
                    context_json = json.dumps(
                        _derive_context_from_plan(
                            json.loads(row["plan_json"]),
                            json.loads(row["request_json"]),
                        ),
                        ensure_ascii=False,
                    )
                connection.execute(
                    """
                    UPDATE plans
                    SET session_id = ?, context_json = ?
                    WHERE plan_id = ?
                    """,
                    (session_id, context_json, current_plan_id),
                )

    def create_task(
        self,
        request_data: Dict[str, Any],
        idempotency_key: Optional[str],
        user_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        now = utc_now()
        stored_idempotency_key = (
            f"{user_id}:{idempotency_key}"
            if user_id and idempotency_key
            else idempotency_key
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if stored_idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM tasks WHERE idempotency_key = ?",
                    (stored_idempotency_key,),
                ).fetchone()
                if existing is not None:
                    return self._task_from_row(existing), False

            requested_session_id = request_data.get("session_id")
            previous_plan_id = request_data.get("previous_plan_id")
            if previous_plan_id:
                previous = connection.execute(
                    "SELECT * FROM plans WHERE plan_id = ?",
                    (previous_plan_id,),
                ).fetchone()
                if previous is None:
                    raise SessionContextError("上一版旅行计划不存在")
                session_id = str(previous["session_id"] or "")
                if not session_id:
                    raise SessionContextError("上一版计划尚未关联会话")
                if (
                    requested_session_id
                    and requested_session_id != session_id
                ):
                    raise SessionContextError(
                        "previous_plan_id 不属于指定会话"
                    )
                session = connection.execute(
                    "SELECT * FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if session is None:
                    raise SessionContextError("规划会话不存在")
                if user_id is not None and session["user_id"] != user_id:
                    raise SessionContextError("上一版旅行计划不存在")
                if session["current_plan_id"] != previous_plan_id:
                    raise RevisionConflictError(
                        "上一版计划已过期，请基于会话中的最新计划追问"
                    )
                active_task = connection.execute(
                    """
                    SELECT task_id FROM tasks
                    WHERE session_id = ?
                      AND status IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
                if active_task is not None:
                    raise RevisionConflictError(
                        "该会话已有规划任务进行中，请等待完成后再追问"
                    )
            else:
                if requested_session_id:
                    raise SessionContextError(
                        "继续现有会话时必须提供 previous_plan_id"
                    )
                session_id = f"session_{uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO sessions (
                        session_id, user_id, current_plan_id,
                        created_at, updated_at
                    ) VALUES (?, ?, NULL, ?, ?)
                    """,
                    (session_id, user_id, now, now),
                )

            task_id = f"task_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, idempotency_key, status, request_json,
                    session_id, previous_plan_id, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    stored_idempotency_key,
                    json.dumps(request_data, ensure_ascii=False),
                    session_id,
                    previous_plan_id,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return self._task_from_row(row), True

    def get_task(
        self,
        task_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            if user_id is None:
                row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT t.* FROM tasks t
                    JOIN sessions s ON s.session_id = t.session_id
                    WHERE t.task_id = ? AND s.user_id = ?
                    """,
                    (task_id, user_id),
                ).fetchone()
        return None if row is None else self._task_from_row(row)

    def list_incomplete_tasks(self) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tasks
                WHERE status IN ('queued', 'running')
                ORDER BY created_at
                """
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def set_task_running(self, task_id: str) -> bool:
        return self._update_active_task(
            task_id,
            status="running",
            error_message=None,
            error_code=None,
            error_retryable=None,
            error_id=None,
        )

    def set_task_stage(self, task_id: str, stage: str) -> bool:
        return self._update_active_task(task_id, current_stage=stage)

    def fail_task(
        self,
        task_id: str,
        message: str,
        *,
        error_code: Optional[str] = None,
        retryable: Optional[bool] = None,
        error_id: Optional[str] = None,
    ) -> bool:
        return self._update_active_task(
            task_id,
            status="failed",
            error_message=message,
            error_code=error_code,
            error_retryable=(
                None if retryable is None else int(retryable)
            ),
            error_id=error_id,
        )

    def complete_task(
        self,
        task_id: str,
        plan_data: Dict[str, Any],
        planning_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        plan_id = f"plan_{uuid4().hex}"
        generated_at = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            if task["status"] != "running":
                raise TaskStateConflictError(
                    "只有运行中的任务可以保存完成结果"
                )
            session_id = str(task["session_id"] or "")
            previous_plan_id = task["previous_plan_id"]
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise SessionContextError("规划会话不存在")
            if previous_plan_id:
                if session["current_plan_id"] != previous_plan_id:
                    raise RevisionConflictError(
                        "会话已有更新版本，本轮结果不能覆盖最新计划"
                    )
                previous = connection.execute(
                    "SELECT revision FROM plans WHERE plan_id = ?",
                    (previous_plan_id,),
                ).fetchone()
                if previous is None:
                    raise SessionContextError("上一版旅行计划不存在")
                revision = int(previous["revision"]) + 1
            else:
                if session["current_plan_id"] is not None:
                    raise RevisionConflictError(
                        "会话已有规划结果，首次任务不能重复完成"
                    )
                revision = 1
            connection.execute(
                """
                INSERT INTO plans (
                    plan_id, task_id, revision, status,
                    generated_at, plan_json, session_id,
                    previous_plan_id, context_json, source_type
                ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, 'agent')
                """,
                (
                    plan_id,
                    task_id,
                    revision,
                    generated_at,
                    json.dumps(plan_data, ensure_ascii=False),
                    session_id,
                    previous_plan_id,
                    json.dumps(
                        planning_context or {},
                        ensure_ascii=False,
                    ),
                ),
            )
            connection.execute(
                """
                UPDATE tasks
                SET status = 'completed', plan_id = ?,
                    error_message = NULL, error_code = NULL,
                    error_retryable = NULL, error_id = NULL,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (plan_id, generated_at, task_id),
            )
            connection.execute(
                """
                UPDATE sessions
                SET current_plan_id = ?,
                    title = COALESCE(title, ?),
                    updated_at = ?
                WHERE session_id = ?
                """,
                (
                    plan_id,
                    _default_session_title(plan_data),
                    generated_at,
                    session_id,
                ),
            )
        return self.get_plan(plan_id)

    def get_plan(
        self,
        plan_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            if user_id is None:
                row = connection.execute(
                    "SELECT * FROM plans WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT p.* FROM plans p
                    JOIN sessions s ON s.session_id = p.session_id
                    WHERE p.plan_id = ? AND s.user_id = ?
                    """,
                    (plan_id, user_id),
                ).fetchone()
        return None if row is None else self._plan_from_row(row)

    def get_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            if user_id is None:
                session = connection.execute(
                    "SELECT * FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            else:
                session = connection.execute(
                    """
                    SELECT * FROM sessions
                    WHERE session_id = ? AND user_id = ?
                    """,
                    (session_id, user_id),
                ).fetchone()
            if session is None:
                return None
            plans = connection.execute(
                """
                SELECT plan_id, task_id, previous_plan_id, revision,
                       generated_at, source_type
                FROM plans
                WHERE session_id = ?
                ORDER BY revision, generated_at
                """,
                (session_id,),
            ).fetchall()
        return {
            "session_id": session["session_id"],
            "current_plan_id": session["current_plan_id"],
            "title": session["title"],
            "archived_at": session["archived_at"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "plans": [
                {
                    "plan_id": row["plan_id"],
                    "task_id": row["task_id"],
                    "previous_plan_id": row["previous_plan_id"],
                    "revision": int(row["revision"]),
                    "generated_at": row["generated_at"],
                    "source_type": row["source_type"],
                }
                for row in plans
            ],
        }

    def list_sessions(
        self,
        *,
        limit: int = 20,
        cursor: Optional[str] = None,
        query: Optional[str] = None,
        include_archived: bool = False,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return server-backed planning history using stable keyset paging."""
        conditions = ["s.current_plan_id IS NOT NULL"]
        parameters: List[Any] = []
        if user_id is not None:
            conditions.append("s.user_id = ?")
            parameters.append(user_id)
        if not include_archived:
            conditions.append("s.archived_at IS NULL")
        normalized_query = (query or "").strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            conditions.append(
                "(COALESCE(s.title, '') LIKE ? OR p.plan_json LIKE ? "
                "OR t.request_json LIKE ?)"
            )
            parameters.extend([pattern, pattern, pattern])
        if cursor:
            cursor_updated_at, cursor_session_id = _decode_cursor(cursor)
            conditions.append(
                "(s.updated_at < ? OR "
                "(s.updated_at = ? AND s.session_id < ?))"
            )
            parameters.extend(
                [cursor_updated_at, cursor_updated_at, cursor_session_id]
            )
        parameters.append(limit + 1)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT s.*, p.revision, p.plan_json, t.request_json,
                       (SELECT COUNT(*) FROM plans history
                        WHERE history.session_id = s.session_id)
                       AS revision_count
                FROM sessions s
                JOIN plans p ON p.plan_id = s.current_plan_id
                JOIN tasks t ON t.task_id = p.task_id
                WHERE {' AND '.join(conditions)}
                ORDER BY s.updated_at DESC, s.session_id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        items = [self._session_summary_from_row(row) for row in visible_rows]
        next_cursor = None
        if has_more and visible_rows:
            last = visible_rows[-1]
            next_cursor = _encode_cursor(
                str(last["updated_at"]),
                str(last["session_id"]),
            )
        return {"items": items, "next_cursor": next_cursor}

    def get_session_turns(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        with self._connect() as connection:
            if user_id is None:
                exists = connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            else:
                exists = connection.execute(
                    """
                    SELECT 1 FROM sessions
                    WHERE session_id = ? AND user_id = ?
                    """,
                    (session_id, user_id),
                ).fetchone()
            if exists is None:
                return None
            rows = connection.execute(
                """
                SELECT t.*, p.revision, p.source_type
                FROM tasks t
                LEFT JOIN plans p ON p.task_id = t.task_id
                WHERE t.session_id = ?
                ORDER BY t.created_at, t.task_id
                """,
                (session_id,),
            ).fetchall()
            event_rows = connection.execute(
                """
                SELECT e.* FROM events e
                JOIN tasks t ON t.task_id = e.task_id
                WHERE t.session_id = ?
                ORDER BY e.event_id
                """,
                (session_id,),
            ).fetchall()
        events_by_task: Dict[str, List[Dict[str, Any]]] = {}
        for event in event_rows:
            metadata = _load_event_metadata(event["metadata_json"])
            events_by_task.setdefault(event["task_id"], []).append(
                {
                    "event_id": str(event["event_id"]),
                    "type": event["event_type"],
                    "timestamp": event["timestamp"],
                    "stage": event["stage"],
                    "message": event["message"],
                    "plan_id": event["plan_id"],
                    **metadata,
                }
            )
        turns: List[Dict[str, Any]] = []
        for row in rows:
            request_data = json.loads(row["request_json"])
            turns.append(
                {
                    "task_id": row["task_id"],
                    "status": row["status"],
                    "user_text": _turn_user_text(request_data),
                    "request": request_data,
                    "plan_id": row["plan_id"],
                    "revision": (
                        int(row["revision"])
                        if row["revision"] is not None
                        else None
                    ),
                    "source_type": row["source_type"],
                    "error_message": row["error_message"],
                    "error_code": row["error_code"],
                    "retryable": (
                        bool(row["error_retryable"])
                        if row["error_retryable"] is not None
                        else None
                    ),
                    "error_id": row["error_id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "events": events_by_task.get(row["task_id"], []),
                }
            )
        return turns

    def fork_session(
        self,
        session_id: str,
        source_plan_id: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create an independent session from any immutable plan version."""
        now = utc_now()
        new_session_id = f"session_{uuid4().hex}"
        task_id = f"task_fork_{uuid4().hex}"
        plan_id = f"plan_{uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                """
                SELECT p.*, t.request_json, s.title, s.user_id
                FROM plans p
                JOIN tasks t ON t.task_id = p.task_id
                JOIN sessions s ON s.session_id = p.session_id
                WHERE p.plan_id = ? AND p.session_id = ?
                  AND (? IS NULL OR s.user_id = ?)
                """,
                (source_plan_id, session_id, user_id, user_id),
            ).fetchone()
            if source is None:
                raise KeyError(source_plan_id)
            request_data = json.loads(source["request_json"])
            request_data["session_id"] = None
            request_data["previous_plan_id"] = None
            request_data["additional_requirements"] = (
                f"基于历史版本 V{int(source['revision'])} 创建新规划"
            )
            title = f"副本 · {source['title'] or _default_session_title(json.loads(source['plan_json']))}"
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, user_id, current_plan_id, title,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_session_id,
                    source["user_id"],
                    plan_id,
                    title[:80],
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, status, request_json, session_id, plan_id,
                    created_at, updated_at
                ) VALUES (?, 'completed', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    json.dumps(request_data, ensure_ascii=False),
                    new_session_id,
                    plan_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO plans (
                    plan_id, task_id, revision, status, generated_at,
                    plan_json, session_id, context_json, source_type
                ) VALUES (?, ?, 1, 'completed', ?, ?, ?, ?, 'fork')
                """,
                (
                    plan_id,
                    task_id,
                    now,
                    source["plan_json"],
                    new_session_id,
                    source["context_json"] or "{}",
                ),
            )
            connection.execute(
                """
                INSERT INTO events (
                    task_id, event_type, timestamp, message, plan_id
                ) VALUES (?, 'plan.completed', ?, ?, ?)
                """,
                (
                    task_id,
                    now,
                    f"已从历史版本 V{int(source['revision'])} 创建新规划",
                    plan_id,
                ),
            )
        return self.get_plan(plan_id, user_id)

    def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        archived: Optional[bool] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        now = utc_now()
        fields: Dict[str, Any] = {"updated_at": now}
        if title is not None:
            fields["title"] = title
        if archived is not None:
            fields["archived_at"] = now if archived else None
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._connect() as connection:
            ownership_clause = "" if user_id is None else " AND user_id = ?"
            parameters = [*fields.values(), session_id]
            if user_id is not None:
                parameters.append(user_id)
            cursor = connection.execute(
                f"UPDATE sessions SET {assignments} "
                f"WHERE session_id = ?{ownership_clause}",
                parameters,
            )
            if cursor.rowcount == 0:
                return None
        return self.get_session(session_id, user_id)

    def update_plan(
        self,
        plan_id: str,
        base_revision: int,
        plan_data: Dict[str, Any],
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        generated_at = utc_now()
        next_plan_id = f"plan_{uuid4().hex}"
        edit_task_id = f"task_edit_{uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT p.* FROM plans p
                JOIN sessions s ON s.session_id = p.session_id
                WHERE p.plan_id = ? AND (? IS NULL OR s.user_id = ?)
                """,
                (plan_id, user_id, user_id),
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            if int(row["revision"]) != base_revision:
                raise RevisionConflictError(
                    "行程已被更新，请刷新后再编辑"
                )
            session = connection.execute(
                "SELECT current_plan_id FROM sessions WHERE session_id = ?",
                (row["session_id"],),
            ).fetchone()
            if session is None or session["current_plan_id"] != plan_id:
                raise RevisionConflictError(
                    "只能编辑会话中的最新计划"
                )
            next_revision = base_revision + 1
            source_task = connection.execute(
                "SELECT request_json FROM tasks WHERE task_id = ?",
                (row["task_id"],),
            ).fetchone()
            request_data = (
                json.loads(source_task["request_json"])
                if source_task is not None
                else {}
            )
            request_data["session_id"] = row["session_id"]
            request_data["previous_plan_id"] = plan_id
            request_data["additional_requirements"] = "手动调整行程"
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, status, request_json, session_id,
                    previous_plan_id, plan_id, created_at, updated_at
                ) VALUES (?, 'completed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    edit_task_id,
                    json.dumps(request_data, ensure_ascii=False),
                    row["session_id"],
                    plan_id,
                    next_plan_id,
                    generated_at,
                    generated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO plans (
                    plan_id, task_id, revision, status, generated_at,
                    plan_json, session_id, previous_plan_id,
                    context_json, source_type
                ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, 'manual_edit')
                """,
                (
                    next_plan_id,
                    edit_task_id,
                    next_revision,
                    generated_at,
                    json.dumps(plan_data, ensure_ascii=False),
                    row["session_id"],
                    plan_id,
                    row["context_json"] or "{}",
                ),
            )
            connection.execute(
                """
                UPDATE sessions
                SET current_plan_id = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (next_plan_id, generated_at, row["session_id"]),
            )
            connection.execute(
                """
                INSERT INTO events (
                    task_id, event_type, timestamp, message, plan_id
                ) VALUES (?, 'plan.completed', ?, ?, ?)
                """,
                (
                    edit_task_id,
                    generated_at,
                    "已保存手动调整后的新版本",
                    next_plan_id,
                ),
            )
        return self.get_plan(next_plan_id, user_id)

    def append_event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        stage: Optional[str] = None,
        plan_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        timestamp = utc_now()
        normalized_metadata = {
            str(key): value
            for key, value in dict(metadata or {}).items()
            if value is not None
        }
        metadata_json = json.dumps(
            normalized_metadata,
            ensure_ascii=False,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (
                    task_id, event_type, timestamp, stage, message, plan_id,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event_type,
                    timestamp,
                    stage,
                    message,
                    plan_id,
                    metadata_json,
                ),
            )
            event_id = cursor.lastrowid
        return {
            "event_id": str(event_id),
            "type": event_type,
            "timestamp": timestamp,
            "stage": stage,
            "message": message,
            "plan_id": plan_id,
            **normalized_metadata,
        }

    def list_events(
        self,
        task_id: str,
        after_event_id: int = 0,
    ) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE task_id = ? AND event_id > ?
                ORDER BY event_id
                """,
                (task_id, after_event_id),
            ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "type": row["event_type"],
                "timestamp": row["timestamp"],
                "stage": row["stage"],
                "message": row["message"],
                "plan_id": row["plan_id"],
                **_load_event_metadata(row["metadata_json"]),
            }
            for row in rows
        ]

    def _update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values())
        values.append(task_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE tasks SET {assignments} WHERE task_id = ?",
                values,
            )

    def _update_active_task(self, task_id: str, **fields: Any) -> bool:
        """Atomically mutate a queued/running task, never a terminal task."""
        if not fields:
            return False
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [*fields.values(), task_id]
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE tasks SET {assignments}
                WHERE task_id = ? AND status IN ('queued', 'running')
                """,
                values,
            )
        return cursor.rowcount == 1

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "session_id": row["session_id"],
            "previous_plan_id": row["previous_plan_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "current_stage": row["current_stage"],
            "plan_id": row["plan_id"],
            "error_message": row["error_message"],
            "error_code": row["error_code"],
            "retryable": (
                bool(row["error_retryable"])
                if row["error_retryable"] is not None
                else None
            ),
            "error_id": row["error_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _session_summary_from_row(row: sqlite3.Row) -> Dict[str, Any]:
        plan = json.loads(row["plan_json"])
        request_data = json.loads(row["request_json"])
        summary = plan.get("request_summary", {})
        return {
            "session_id": row["session_id"],
            "current_plan_id": row["current_plan_id"],
            "title": row["title"] or _default_session_title(plan),
            "destination_city": summary.get("destination_city"),
            "start_date": summary.get("start_date"),
            "end_date": summary.get("end_date"),
            "budget_cny": summary.get("budget_cny"),
            "accommodation_type": summary.get("accommodation_type")
            or summary.get("hotel_requirement"),
            "latest_requirement": request_data.get(
                "additional_requirements"
            ),
            "current_revision": int(row["revision"]),
            "revision_count": int(row["revision_count"]),
            "archived_at": row["archived_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _plan_from_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "plan_id": row["plan_id"],
            "task_id": row["task_id"],
            "session_id": row["session_id"],
            "previous_plan_id": row["previous_plan_id"],
            "revision": int(row["revision"]),
            "status": row["status"],
            "generated_at": row["generated_at"],
            "source_type": row["source_type"],
            "plan": json.loads(row["plan_json"]),
            "context": json.loads(row["context_json"] or "{}"),
        }


def _encode_cursor(updated_at: str, session_id: str) -> str:
    payload = json.dumps([updated_at, session_id]).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> Tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding)
        values = json.loads(decoded.decode("utf-8"))
        if (
            not isinstance(values, list)
            or len(values) != 2
            or not all(isinstance(value, str) for value in values)
        ):
            raise ValueError
        return values[0], values[1]
    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise ValueError("历史记录分页游标无效") from exc


def _default_session_title(plan: Dict[str, Any]) -> str:
    summary = plan.get("request_summary", {})
    city = str(summary.get("destination_city") or "旅行计划")
    start = summary.get("start_date")
    end = summary.get("end_date")
    if start and end:
        return f"{city} · {start}—{end}"[:80]
    return city[:80]


def _turn_user_text(request_data: Dict[str, Any]) -> str:
    requirement = str(
        request_data.get("additional_requirements") or ""
    ).strip()
    if requirement and requirement != "无":
        return requirement
    city = request_data.get("destination_city") or "目的地"
    start = request_data.get("start_date") or ""
    end = request_data.get("end_date") or ""
    return f"规划 {city} {start} 至 {end} 的旅行行程".strip()


def _derive_context_from_plan(
    plan: Dict[str, Any],
    request: Dict[str, Any],
) -> Dict[str, Any]:
    """Build reusable specialist-shaped context for legacy plans."""
    attractions = []
    seen_names = set()
    for day in plan.get("daily_itinerary", []):
        if not isinstance(day, dict):
            continue
        for item in day.get("schedule", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("place_name") or "")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            attractions.append(
                {
                    "name": name,
                    "address": item.get("address"),
                    "location": item.get("location"),
                }
            )
    attraction_context = {
        "city": plan.get("request_summary", {}).get(
            "destination_city"
        ),
        "preferences": plan.get("request_summary", {}).get(
            "preferences", []
        ),
        "attractions": attractions,
        "data_notes": ["由旧版已保存计划恢复的景点上下文"],
    }
    weather_context = {
        "city": plan.get("request_summary", {}).get(
            "destination_city"
        ),
        "daily_forecasts": plan.get("weather_summary", []),
        "data_notes": ["由旧版已保存计划恢复的天气上下文"],
    }
    hotel_context = {
        "city": plan.get("request_summary", {}).get(
            "destination_city"
        ),
        "hotels": [plan.get("selected_hotel", {})],
        "data_notes": ["由旧版已保存计划恢复的住宿上下文"],
    }
    return {
        "request": request,
        "attractions": json.dumps(
            attraction_context,
            ensure_ascii=False,
        ),
        "weather": json.dumps(weather_context, ensure_ascii=False),
        "hotels": json.dumps(hotel_context, ensure_ascii=False),
        "change_analysis": {},
    }
