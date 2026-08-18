"""Minimal username/password authentication backed by SQLite."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Dict, Optional
from uuid import uuid4


_PASSWORD_ITERATIONS = 310_000


class UsernameAlreadyExistsError(ValueError):
    """Raised when registration targets an existing username."""


class SQLiteAuthRepository:
    """Store users and opaque login sessions in the API database."""

    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry
                ON auth_sessions(expires_at);
                """
            )

    def create_user(
        self,
        username: str,
        password: str,
    ) -> Dict[str, Any]:
        normalized_username = username.strip().lower()
        user_id = f"user_{uuid4().hex}"
        created_at = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, username, password_hash, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        normalized_username,
                        _hash_password(password),
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise UsernameAlreadyExistsError(
                "用户名已存在"
            ) from exc
        return {
            "user_id": user_id,
            "username": normalized_username,
            "created_at": created_at,
        }

    def authenticate(
        self,
        username: str,
        password: str,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
        if row is None or not _verify_password(
            password,
            str(row["password_hash"]),
        ):
            return None
        return _user_from_row(row)

    def create_session(
        self,
        user_id: str,
        *,
        ttl_days: int,
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=ttl_days)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            connection.execute(
                """
                INSERT INTO auth_sessions (
                    token_hash, user_id, created_at, expires_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    _hash_token(token),
                    user_id,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return token

    def get_user_for_session(
        self,
        token: str,
    ) -> Optional[Dict[str, Any]]:
        now = _utc_now()
        token_hash = _hash_token(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.*
                FROM auth_sessions auth
                JOIN users u ON u.user_id = auth.user_id
                WHERE auth.token_hash = ? AND auth.expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?",
                    (token_hash,),
                )
                return None
        return _user_from_row(row)

    def delete_session(self, token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (_hash_token(token),),
            )


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_ITERATIONS,
    )
    return f"pbkdf2_sha256${_PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split(
            "$", 3
        )
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = bytes.fromhex(raw_salt)
        expected = bytes.fromhex(raw_digest)
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "created_at": row["created_at"],
    }
