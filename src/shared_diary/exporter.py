from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Any

from .store import DiaryStore


VISIBILITY_LABELS = {
    "shared": "共同可见",
    "private": "仅自己可见",
    "selected": "指定可见",
    "challenge": "趣味锁",
}


def _local_datetime(store: DiaryStore, value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        store.local_timezone
    ).strftime("%Y-%m-%d %H:%M:%S")


def render_markdown(store: DiaryStore, backup: dict[str, Any]) -> str:
    participants = {
        item["id"]: item["display_name"] for item in backup["participants"]
    }
    viewer_name = backup["viewer"]["display_name"]
    lines = [
        "# 共同日记备份",
        "",
        f"> 导出身份：{viewer_name}",
        f"> 导出时间：{_local_datetime(store, backup['exported_at'])}",
        f"> 日记时区：{backup['timezone']}",
        "",
    ]
    current_day = None
    for entry in backup["entries"]:
        day = entry["local_day"]
        if day != current_day:
            current_day = day
            lines.extend([f"## {day}", ""])

        author = participants.get(entry["author_id"], entry["author_id"])
        occurred_at = _local_datetime(store, entry["occurred_at"])
        visibility = VISIBILITY_LABELS.get(entry["visibility"], entry["visibility"])
        lines.extend(
            [
                f"### {author} · {occurred_at}",
                "",
                f"*{visibility}*",
                "",
            ]
        )

        if entry["locked"]:
            lines.append("> 🔒 正文尚未解锁，备份仅保留当前可见信息。")
            if entry.get("challenge_question"):
                lines.append(f"> 问题：{entry['challenge_question']}")
            if entry.get("challenge_hint"):
                lines.append(f"> 提示：{entry['challenge_hint']}")
            if entry.get("preview"):
                lines.append(f"> 门缝预告：{entry['preview']}")
            lines.append("")
        else:
            lines.extend([entry.get("body") or "", ""])

        replies = entry.get("replies") or []
        if replies:
            lines.extend(["#### 回应", ""])
            for reply in replies:
                reply_author = participants.get(reply["author_id"], reply["author_id"])
                reply_time = _local_datetime(store, reply["created_at"])
                body = str(reply["body"]).replace("\n", "\n  ")
                lines.append(f"- **{reply_author} · {reply_time}**  \n  {body}")
            lines.append("")

        lines.extend(["---", ""])

    if not backup["entries"]:
        lines.extend(["这里还是一页空白。", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_backup_archive(store: DiaryStore, participant_id: str) -> tuple[str, bytes]:
    backup = store.export_visible_diary(participant_id)
    local_day = datetime.now(store.local_timezone).date().isoformat()
    base_name = f"shared-diary-backup-{local_day}"
    json_bytes = json.dumps(
        backup,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    markdown_bytes = render_markdown(store, backup).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{base_name}.json", json_bytes)
        archive.writestr(f"{base_name}.md", markdown_bytes)
    return f"{base_name}.zip", buffer.getvalue()
