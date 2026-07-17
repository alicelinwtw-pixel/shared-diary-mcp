from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Iterable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


Visibility = Literal["shared", "private", "selected", "challenge"]
ParticipantKind = Literal["human", "ai"]
DEFAULT_TIMEZONE_NAME = "Asia/Shanghai"


def synchronized(method):
    """Serialize one SQLite connection across web and MCP worker threads."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class DiaryError(Exception):
    """Base error for expected diary failures."""


class NotFound(DiaryError):
    """Requested diary object does not exist."""


class PermissionDenied(DiaryError):
    """The participant cannot perform the requested action."""


def utc_now() -> str:
    # Replies can be written back-to-back in one model turn. Microseconds keep
    # the visible conversation order deterministic without relying on UUIDs.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_answer(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def hash_answer(value: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalize_answer(value).encode("utf-8"),
        salt,
        120_000,
    )
    return digest.hex()


class DiaryStore:
    """SQLite-backed diary core with no MCP or web framework dependency."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        timezone_name: str | None = None,
    ) -> None:
        self.path = str(path)
        self.timezone_name = (
            timezone_name or os.environ.get("DIARY_TIMEZONE") or DEFAULT_TIMEZONE_NAME
        ).strip()
        try:
            self.local_timezone = ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise DiaryError(f"unknown diary timezone: {self.timezone_name}") from exc
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self._create_schema()

    @synchronized
    def close(self) -> None:
        self.db.close()

    @synchronized
    def _create_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS participants (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('human', 'ai')),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                author_id TEXT NOT NULL REFERENCES participants(id),
                body TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                visibility TEXT NOT NULL CHECK (
                    visibility IN ('shared', 'private', 'selected', 'challenge')
                ),
                audience_json TEXT NOT NULL DEFAULT '[]',
                challenge_question TEXT,
                challenge_hint TEXT,
                preview TEXT,
                answer_salt TEXT,
                answer_hashes_json TEXT NOT NULL DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_entries_occurred
                ON entries(occurred_at, created_at);

            CREATE TABLE IF NOT EXISTS entry_unlocks (
                entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                participant_id TEXT NOT NULL REFERENCES participants(id),
                unlocked_at TEXT NOT NULL,
                PRIMARY KEY (entry_id, participant_id)
            );

            CREATE TABLE IF NOT EXISTS entry_reads (
                entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                participant_id TEXT NOT NULL REFERENCES participants(id),
                read_at TEXT NOT NULL,
                PRIMARY KEY (entry_id, participant_id)
            );

            CREATE TABLE IF NOT EXISTS replies (
                id TEXT PRIMARY KEY,
                entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                author_id TEXT NOT NULL REFERENCES participants(id),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_replies_entry
                ON replies(entry_id, created_at);

            CREATE TABLE IF NOT EXISTS reply_reads (
                reply_id TEXT NOT NULL REFERENCES replies(id) ON DELETE CASCADE,
                participant_id TEXT NOT NULL REFERENCES participants(id),
                read_at TEXT NOT NULL,
                PRIMARY KEY (reply_id, participant_id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                participant_id TEXT PRIMARY KEY REFERENCES participants(id),
                notify_on_ai_write INTEGER NOT NULL DEFAULT 0
                    CHECK (notify_on_ai_write IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS access_keys (
                participant_id TEXT PRIMARY KEY REFERENCES participants(id) ON DELETE CASCADE,
                key_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            """
        )
        self._normalize_existing_occurred_at()
        self.db.commit()

    def _normalize_timestamp(self, value: str) -> str:
        """Store all event times in one comparable UTC representation."""
        text_value = value.strip()
        if not text_value:
            raise DiaryError("occurred_at cannot be empty")
        if text_value[-1:] in {"Z", "z"}:
            text_value = f"{text_value[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text_value)
        except ValueError as exc:
            raise DiaryError("occurred_at must be a valid ISO 8601 timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=self.local_timezone)
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")

    def _normalize_existing_occurred_at(self) -> None:
        """Repair mixed Z/+08:00 timestamps written by earlier releases."""
        rows = self.db.execute("SELECT id, occurred_at FROM entries").fetchall()
        updates: list[tuple[str, str]] = []
        for row in rows:
            try:
                normalized = self._normalize_timestamp(str(row["occurred_at"]))
            except DiaryError:
                # Preserve an old malformed row rather than preventing the diary from
                # starting. New writes are validated and cannot create another one.
                continue
            if normalized != row["occurred_at"]:
                updates.append((normalized, str(row["id"])))
        if updates:
            self.db.executemany(
                "UPDATE entries SET occurred_at = ? WHERE id = ?",
                updates,
            )

    def local_day(self, timestamp: str) -> str:
        normalized = self._normalize_timestamp(timestamp)
        return datetime.fromisoformat(normalized).astimezone(
            self.local_timezone
        ).date().isoformat()

    def _local_day_bounds(self, day: str) -> tuple[str, str]:
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError as exc:
            raise DiaryError("day must use YYYY-MM-DD") from exc
        if parsed_day.isoformat() != day:
            raise DiaryError("day must use YYYY-MM-DD")
        start_local = datetime.combine(parsed_day, time.min, tzinfo=self.local_timezone)
        end_local = start_local + timedelta(days=1)
        return (
            start_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
            end_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        )

    @synchronized
    def create_participant(
        self,
        display_name: str,
        kind: ParticipantKind,
        *,
        participant_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"human", "ai"}:
            raise DiaryError("kind must be 'human' or 'ai'")
        name = display_name.strip()
        if not name:
            raise DiaryError("display_name cannot be empty")
        participant_id = participant_id or uuid.uuid4().hex
        created_at = utc_now()
        with self.db:
            self.db.execute(
                "INSERT INTO participants(id, display_name, kind, created_at) VALUES (?, ?, ?, ?)",
                (participant_id, name, kind, created_at),
            )
            self.db.execute(
                "INSERT INTO settings(participant_id, notify_on_ai_write) VALUES (?, 0)",
                (participant_id,),
            )
        return {
            "id": participant_id,
            "display_name": name,
            "kind": kind,
            "created_at": created_at,
        }

    @synchronized
    def issue_access_key(self, participant_id: str) -> str:
        """Create or rotate the opaque key used by one MCP participant."""
        self._require_participant(participant_id)
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        with self.db:
            self.db.execute(
                """
                INSERT OR REPLACE INTO access_keys(participant_id, key_hash, created_at)
                VALUES (?, ?, ?)
                """,
                (participant_id, key_hash, utc_now()),
            )
        return raw_key

    @synchronized
    def authenticate(self, access_key: str) -> str | None:
        if not access_key:
            return None
        key_hash = hashlib.sha256(access_key.encode("utf-8")).hexdigest()
        row = self.db.execute(
            "SELECT participant_id, key_hash FROM access_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if row is None or not hmac.compare_digest(row["key_hash"], key_hash):
            return None
        return str(row["participant_id"])

    @synchronized
    def participant(self, participant_id: str) -> dict[str, Any]:
        return dict(self._require_participant(participant_id))

    @synchronized
    def list_participants(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, display_name, kind, created_at FROM participants ORDER BY created_at, id"
        ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def set_notify_on_ai_write(self, participant_id: str, enabled: bool) -> None:
        self._require_participant(participant_id)
        with self.db:
            self.db.execute(
                "UPDATE settings SET notify_on_ai_write = ? WHERE participant_id = ?",
                (int(enabled), participant_id),
            )

    @synchronized
    def notify_on_ai_write(self, participant_id: str) -> bool:
        self._require_participant(participant_id)
        row = self.db.execute(
            "SELECT notify_on_ai_write FROM settings WHERE participant_id = ?",
            (participant_id,),
        ).fetchone()
        return bool(row["notify_on_ai_write"])

    @synchronized
    def write_entry(
        self,
        author_id: str,
        body: str,
        *,
        occurred_at: str | None = None,
        visibility: Visibility = "shared",
        audience: Iterable[str] = (),
        challenge_question: str | None = None,
        challenge_answers: Iterable[str] = (),
        challenge_hint: str | None = None,
        preview: str | None = None,
    ) -> dict[str, Any]:
        self._require_participant(author_id)
        text = body.strip()
        if not text:
            raise DiaryError("body cannot be empty")
        if visibility not in {"shared", "private", "selected", "challenge"}:
            raise DiaryError("invalid visibility")

        audience_list = list(dict.fromkeys(x for x in audience if x != author_id))
        for participant_id in audience_list:
            self._require_participant(participant_id)

        question = (challenge_question or "").strip() or None
        answers = [normalize_answer(x) for x in challenge_answers if normalize_answer(x)]
        if visibility == "selected" and not audience_list:
            raise DiaryError("selected entries need at least one audience member")
        if visibility == "challenge":
            if not audience_list:
                raise DiaryError("challenge entries need at least one audience member")
            if not question or not answers:
                raise DiaryError("challenge entries need a question and at least one answer")

        salt = secrets.token_bytes(16) if visibility == "challenge" else b""
        answer_hashes = [hash_answer(answer, salt) for answer in answers]
        entry_id = uuid.uuid4().hex
        created_at = utc_now()
        occurred_at = self._normalize_timestamp(occurred_at or created_at)
        with self.db:
            self.db.execute(
                """
                INSERT INTO entries(
                    id, author_id, body, occurred_at, created_at, updated_at,
                    visibility, audience_json, challenge_question, challenge_hint,
                    preview, answer_salt, answer_hashes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    author_id,
                    text,
                    occurred_at,
                    created_at,
                    created_at,
                    visibility,
                    json.dumps(audience_list, ensure_ascii=False),
                    question,
                    (challenge_hint or "").strip() or None,
                    (preview or "").strip() or None,
                    salt.hex() or None,
                    json.dumps(answer_hashes),
                ),
            )
        return self.read_entry(entry_id, author_id)

    @synchronized
    def edit_entry(
        self,
        entry_id: str,
        participant_id: str,
        body: str,
    ) -> dict[str, Any]:
        """Replace an entry body when the current participant is its author."""
        self._require_participant(participant_id)
        row = self.db.execute(
            "SELECT author_id FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            raise NotFound("entry not found")
        if row["author_id"] != participant_id:
            raise PermissionDenied("only the entry author can edit this entry")
        text = body.strip()
        if not text:
            raise DiaryError("body cannot be empty")
        with self.db:
            self.db.execute(
                "UPDATE entries SET body = ?, updated_at = ? WHERE id = ?",
                (text, utc_now(), entry_id),
            )
        return self.read_entry(entry_id, participant_id)

    @synchronized
    def delete_entry(self, entry_id: str, participant_id: str) -> None:
        """Delete an entry when the current participant is its author."""
        self._require_participant(participant_id)
        row = self.db.execute(
            "SELECT author_id FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            raise NotFound("entry not found")
        if row["author_id"] != participant_id:
            raise PermissionDenied("only the entry author can delete this entry")
        with self.db:
            self.db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    @synchronized
    def read_entry(
        self,
        entry_id: str,
        participant_id: str,
        *,
        answer: str | None = None,
    ) -> dict[str, Any]:
        self._require_participant(participant_id)
        row = self.db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise NotFound("entry not found")

        unlocked = self._can_read_body(row, participant_id)
        just_unlocked = False
        if not unlocked and row["visibility"] == "challenge" and answer is not None:
            self._require_in_audience(row, participant_id)
            salt = bytes.fromhex(row["answer_salt"] or "")
            expected = json.loads(row["answer_hashes_json"])
            actual = hash_answer(answer, salt)
            if any(hmac.compare_digest(actual, item) for item in expected):
                with self.db:
                    self.db.execute(
                        """
                        INSERT OR REPLACE INTO entry_unlocks(entry_id, participant_id, unlocked_at)
                        VALUES (?, ?, ?)
                        """,
                        (entry_id, participant_id, utc_now()),
                    )
                unlocked = True
                just_unlocked = True

        data = self._entry_metadata(row)
        data["locked"] = not unlocked
        data["just_unlocked"] = just_unlocked
        data["body"] = row["body"] if unlocked else None
        data["unread"] = self._is_entry_unread(row, participant_id)
        if not unlocked:
            if row["visibility"] == "private":
                raise PermissionDenied("entry is not visible to this participant")
            if row["visibility"] in {"selected", "challenge"}:
                self._require_in_audience(row, participant_id)
        return data

    @synchronized
    def mark_entry_read(self, entry_id: str, participant_id: str) -> None:
        entry = self.read_entry(entry_id, participant_id)
        if entry["locked"]:
            raise PermissionDenied("unlock the entry before marking it read")
        if entry["author_id"] == participant_id:
            return
        with self.db:
            self.db.execute(
                """
                INSERT OR REPLACE INTO entry_reads(entry_id, participant_id, read_at)
                VALUES (?, ?, ?)
                """,
                (entry_id, participant_id, utc_now()),
            )

    @synchronized
    def list_timeline(
        self,
        participant_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_participant(participant_id)
        limit = max(1, min(200, int(limit)))
        clauses: list[str] = []
        params: list[Any] = []
        if before:
            clauses.append("occurred_at < ?")
            params.append(self._normalize_timestamp(before))
        if after:
            clauses.append("occurred_at > ?")
            params.append(self._normalize_timestamp(after))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.execute(
            f"SELECT * FROM entries {where} ORDER BY occurred_at DESC, created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        visible: list[dict[str, Any]] = []
        for row in rows:
            try:
                visible.append(self.read_entry(row["id"], participant_id))
            except PermissionDenied:
                continue
        return visible

    @synchronized
    def list_day(self, participant_id: str, day: str) -> list[dict[str, Any]]:
        """Read one YYYY-MM-DD page in chronological order."""
        self._require_participant(participant_id)
        start, end = self._local_day_bounds(day)
        rows = self.db.execute(
            """
            SELECT * FROM entries
            WHERE occurred_at >= ? AND occurred_at < ?
            ORDER BY occurred_at, created_at
            """,
            (start, end),
        ).fetchall()
        visible: list[dict[str, Any]] = []
        for row in rows:
            try:
                visible.append(self.read_entry(row["id"], participant_id))
            except PermissionDenied:
                continue
        return visible

    @synchronized
    def list_recent_days(
        self,
        participant_id: str,
        *,
        days: int = 3,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return the newest visible calendar pages, preserving page order."""
        days = max(1, min(30, int(days)))
        candidates = self.list_timeline(participant_id, limit=200)
        selected_days: list[str] = []
        for item in candidates:
            day = item["local_day"]
            if day not in selected_days:
                selected_days.append(day)
            if len(selected_days) >= days:
                break
        return {day: self.list_day(participant_id, day) for day in reversed(selected_days)}

    @synchronized
    def export_visible_diary(self, participant_id: str) -> dict[str, Any]:
        """Return every diary entry this participant may see, in reading order."""
        viewer = dict(self._require_participant(participant_id))
        participants = self.list_participants()
        rows = self.db.execute(
            "SELECT * FROM entries ORDER BY occurred_at, created_at, id"
        ).fetchall()
        entries: list[dict[str, Any]] = []
        for row in rows:
            try:
                entry = self.read_entry(row["id"], participant_id)
            except PermissionDenied:
                continue
            exported = dict(entry)
            exported.pop("just_unlocked", None)
            exported["replies"] = (
                self.list_replies(row["id"], participant_id)
                if not entry["locked"]
                else []
            )
            entries.append(exported)
        return {
            "format": "shared-diary-backup",
            "format_version": 1,
            "exported_at": utc_now(),
            "timezone": self.timezone_name,
            "viewer": viewer,
            "participants": participants,
            "entries": entries,
        }

    @synchronized
    def reply_to_entry(self, entry_id: str, author_id: str, body: str) -> dict[str, Any]:
        entry = self.read_entry(entry_id, author_id)
        if entry["locked"]:
            raise PermissionDenied("unlock the entry before replying")
        text = body.strip()
        if not text:
            raise DiaryError("reply body cannot be empty")
        reply_id = uuid.uuid4().hex
        created_at = utc_now()
        with self.db:
            self.db.execute(
                "INSERT INTO replies(id, entry_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (reply_id, entry_id, author_id, text, created_at),
            )
        return {
            "id": reply_id,
            "entry_id": entry_id,
            "author_id": author_id,
            "body": text,
            "created_at": created_at,
        }

    @synchronized
    def list_replies(self, entry_id: str, participant_id: str) -> list[dict[str, Any]]:
        entry = self.read_entry(entry_id, participant_id)
        if entry["locked"]:
            raise PermissionDenied("unlock the entry before reading replies")
        rows = self.db.execute(
            "SELECT * FROM replies WHERE entry_id = ? ORDER BY created_at, id",
            (entry_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def get_unread_replies(self, participant_id: str) -> list[dict[str, Any]]:
        self._require_participant(participant_id)
        rows = self.db.execute(
            """
            SELECT r.*, e.author_id AS entry_author_id
            FROM replies r
            JOIN entries e ON e.id = r.entry_id
            LEFT JOIN reply_reads rr
              ON rr.reply_id = r.id AND rr.participant_id = ?
            WHERE e.author_id = ? AND r.author_id != ? AND rr.reply_id IS NULL
            ORDER BY r.created_at, r.id
            """,
            (participant_id, participant_id, participant_id),
        ).fetchall()
        visible: list[dict[str, Any]] = []
        for row in rows:
            try:
                self.read_entry(row["entry_id"], participant_id)
            except PermissionDenied:
                continue
            visible.append(dict(row))
        return visible

    @synchronized
    def mark_replies_read(self, participant_id: str, reply_ids: Iterable[str]) -> int:
        self._require_participant(participant_id)
        marked = 0
        with self.db:
            for reply_id in dict.fromkeys(reply_ids):
                reply = self.db.execute(
                    "SELECT r.*, e.author_id AS entry_author_id FROM replies r "
                    "JOIN entries e ON e.id = r.entry_id WHERE r.id = ?",
                    (reply_id,),
                ).fetchone()
                if reply is None:
                    continue
                if reply["entry_author_id"] != participant_id:
                    raise PermissionDenied("only the entry author can mark this reply read")
                self.db.execute(
                    "INSERT OR REPLACE INTO reply_reads(reply_id, participant_id, read_at) VALUES (?, ?, ?)",
                    (reply_id, participant_id, utc_now()),
                )
                marked += 1
        return marked

    def _require_participant(self, participant_id: str) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM participants WHERE id = ?",
            (participant_id,),
        ).fetchone()
        if row is None:
            raise NotFound("participant not found")
        return row

    @staticmethod
    def _audience(row: sqlite3.Row) -> list[str]:
        return json.loads(row["audience_json"] or "[]")

    def _require_in_audience(self, row: sqlite3.Row, participant_id: str) -> None:
        if participant_id not in self._audience(row):
            raise PermissionDenied("participant is not in the entry audience")

    def _can_read_body(self, row: sqlite3.Row, participant_id: str) -> bool:
        if row["author_id"] == participant_id:
            return True
        visibility = row["visibility"]
        if visibility == "shared":
            return True
        if visibility == "private":
            return False
        if participant_id not in self._audience(row):
            return False
        if visibility == "selected":
            return True
        unlocked = self.db.execute(
            "SELECT 1 FROM entry_unlocks WHERE entry_id = ? AND participant_id = ?",
            (row["id"], participant_id),
        ).fetchone()
        return unlocked is not None

    def _is_entry_unread(self, row: sqlite3.Row, participant_id: str) -> bool:
        if row["author_id"] == participant_id:
            return False
        seen = self.db.execute(
            "SELECT 1 FROM entry_reads WHERE entry_id = ? AND participant_id = ?",
            (row["id"], participant_id),
        ).fetchone()
        return seen is None

    def _entry_metadata(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "author_id": row["author_id"],
            "occurred_at": row["occurred_at"],
            "local_day": self.local_day(row["occurred_at"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "visibility": row["visibility"],
            "audience": self._audience(row),
            "challenge_question": row["challenge_question"],
            "challenge_hint": row["challenge_hint"],
            "preview": row["preview"],
        }
