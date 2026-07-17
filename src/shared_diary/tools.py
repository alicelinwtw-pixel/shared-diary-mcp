from __future__ import annotations

from typing import Any

from .store import DiaryStore


class DiaryTools:
    """Framework-neutral tool service. MCP decorators live in mcp_server.py."""

    def __init__(self, store: DiaryStore) -> None:
        self.store = store

    def write_entry(
        self,
        actor_id: str,
        body: str,
        occurred_at: str | None = None,
        visibility: str = "shared",
        audience: list[str] | None = None,
        challenge_question: str | None = None,
        challenge_answers: list[str] | None = None,
        challenge_hint: str | None = None,
        preview: str | None = None,
    ) -> dict[str, Any]:
        entry = self.store.write_entry(
            actor_id,
            body,
            occurred_at=occurred_at,
            visibility=visibility,  # type: ignore[arg-type]
            audience=audience or (),
            challenge_question=challenge_question,
            challenge_answers=challenge_answers or (),
            challenge_hint=challenge_hint,
            preview=preview,
        )
        return {
            "saved": True,
            "entry": entry,
            "announce": self.store.notify_on_ai_write(actor_id),
            "instruction": (
                "Briefly tell the user that the diary was saved."
                if self.store.notify_on_ai_write(actor_id)
                else "Surprise mode is enabled. Do not announce or summarize this write."
            ),
        }

    def read_day(self, actor_id: str, day: str) -> dict[str, Any]:
        return {"day": day, "entries": self.store.list_day(actor_id, day)}

    def read_recent(self, actor_id: str, days: int = 3) -> dict[str, Any]:
        return {"days": self.store.list_recent_days(actor_id, days=days)}

    def browse_timeline(
        self,
        actor_id: str,
        limit: int = 20,
        before: str | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        return {
            "entries": self.store.list_timeline(
                actor_id,
                limit=limit,
                before=before,
                after=after,
            )
        }

    def edit_entry(self, actor_id: str, entry_id: str, body: str) -> dict[str, Any]:
        return {
            "saved": True,
            "entry": self.store.edit_entry(entry_id, actor_id, body),
        }

    def delete_entry(self, actor_id: str, entry_id: str) -> dict[str, Any]:
        self.store.delete_entry(entry_id, actor_id)
        return {"deleted": True, "entry_id": entry_id}

    def reply_to_entry(self, actor_id: str, entry_id: str, body: str) -> dict[str, Any]:
        return {"saved": True, "reply": self.store.reply_to_entry(entry_id, actor_id, body)}

    def get_unread_replies(self, actor_id: str) -> dict[str, Any]:
        return {"replies": self.store.get_unread_replies(actor_id)}

    def mark_replies_read(self, actor_id: str, reply_ids: list[str]) -> dict[str, Any]:
        return {"marked": self.store.mark_replies_read(actor_id, reply_ids)}

    def attempt_unlock(self, actor_id: str, entry_id: str, answer: str) -> dict[str, Any]:
        entry = self.store.read_entry(entry_id, actor_id, answer=answer)
        return {"unlocked": not entry["locked"], "entry": entry}
