from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .exporter import build_backup_archive
from .store import DiaryError, DiaryStore, PermissionDenied
from .tools import DiaryTools


DATA_PATH = Path(os.environ.get("DIARY_DB_PATH", "data/shared-diary.sqlite3"))
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)

store = DiaryStore(DATA_PATH)
tools = DiaryTools(store)

MCP_INSTRUCTIONS = (
    "This is a chronological shared diary, not a general memory database. During an "
    "active conversation, you may autonomously call write_entry when a moment feels "
    "personally meaningful and worth preserving. Be selective rather than writing every "
    "turn. Autonomous writes append new entries only. Respect the returned surprise-mode "
    "instruction and never reveal locked or unauthorized content."
)

STRING_OR_NULL = {"type": ["string", "null"]}
STRING_ARRAY_OR_NULL = {"type": ["array", "null"], "items": {"type": "string"}}

MCP_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "write_entry",
        "description": (
            "Append a diary entry. You may call this autonomously during an active "
            "conversation when a moment feels genuinely worth preserving; do not write "
            "every turn. Obey the surprise-mode instruction returned by the tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "minLength": 1},
                "occurred_at": STRING_OR_NULL,
                "visibility": {
                    "type": "string",
                    "enum": ["shared", "private", "selected", "challenge"],
                    "default": "shared",
                },
                "audience": STRING_ARRAY_OR_NULL,
                "challenge_question": STRING_OR_NULL,
                "challenge_answers": STRING_ARRAY_OR_NULL,
                "challenge_hint": STRING_OR_NULL,
                "preview": STRING_OR_NULL,
            },
            "required": ["body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_day",
        "description": "Read one YYYY-MM-DD diary page visible to you.",
        "inputSchema": {
            "type": "object",
            "properties": {"day": {"type": "string", "format": "date"}},
            "required": ["day"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read_recent",
        "description": "Read the newest visible calendar pages to recover continuity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 31, "default": 3}
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "browse_timeline",
        "description": "Browse visible entries using ISO timestamps as cursors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "before": STRING_OR_NULL,
                "after": STRING_OR_NULL,
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "edit_entry",
        "description": (
            "Replace the body of one diary entry you authored. You cannot edit another "
            "participant's entry."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string"},
                "body": {"type": "string", "minLength": 1},
            },
            "required": ["entry_id", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_entry",
        "description": (
            "Permanently delete one diary entry you authored, including its replies. "
            "Only call this when the user explicitly asks to delete it. You cannot delete "
            "another participant's entry."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"entry_id": {"type": "string"}},
            "required": ["entry_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "reply_to_entry",
        "description": "Add a one-level response beneath a visible diary entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string"},
                "body": {"type": "string", "minLength": 1},
            },
            "required": ["entry_id", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_unread_replies",
        "description": "Read new responses beneath entries you authored.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "mark_replies_read",
        "description": "Mark selected responses to your entries as read.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reply_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}
            },
            "required": ["reply_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "attempt_unlock",
        "description": "Try a playful answer to unlock a challenge diary entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string"},
                "answer": {"type": "string"},
            },
            "required": ["entry_id", "answer"],
            "additionalProperties": False,
        },
    },
]


def jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def call_tool(actor: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    dispatch = {
        "write_entry": tools.write_entry,
        "read_day": tools.read_day,
        "read_recent": tools.read_recent,
        "browse_timeline": tools.browse_timeline,
        "edit_entry": tools.edit_entry,
        "delete_entry": tools.delete_entry,
        "reply_to_entry": tools.reply_to_entry,
        "get_unread_replies": tools.get_unread_replies,
        "mark_replies_read": tools.mark_replies_read,
        "attempt_unlock": tools.attempt_unlock,
    }
    fn = dispatch.get(name)
    if fn is None:
        raise KeyError(name)
    return fn(actor, **arguments)


def handle_mcp_message(actor: str, message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return jsonrpc_error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")

    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        return jsonrpc_result(
            request_id,
            {
                "protocolVersion": requested or "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "Shared Diary", "version": "0.1.0-dev"},
                "instructions": MCP_INSTRUCTIONS,
            },
        )
    if method == "ping":
        return jsonrpc_result(request_id, {})
    if method == "tools/list":
        return jsonrpc_result(request_id, {"tools": MCP_TOOL_DEFINITIONS})
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return jsonrpc_error(request_id, -32602, "Invalid tool arguments")
        try:
            payload = call_tool(actor, name, arguments)
            return jsonrpc_result(
                request_id,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                    ],
                    "structuredContent": payload,
                },
            )
        except KeyError:
            return jsonrpc_error(request_id, -32602, f"Unknown tool: {name}")
        except (DiaryError, TypeError, ValueError) as exc:
            payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
            return jsonrpc_result(
                request_id,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                    ],
                    "isError": True,
                },
            )
    return jsonrpc_error(request_id, -32601, "Method not found")


async def mcp_endpoint(request: Request) -> Response:
    actor = store.authenticate(str(request.path_params.get("access_key") or ""))
    if actor is None:
        return PlainTextResponse("Not found", status_code=404)
    try:
        message = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(jsonrpc_error(None, -32700, "Parse error"))
    response = handle_mcp_message(actor, message)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)


async def health(request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "shared-diary-mcp", "version": "0.1.0"})


STATIC_DIR = Path(__file__).resolve().parent / "static"
ADMIN_KEY = os.environ.get("DIARY_ADMIN_KEY", "")


def is_admin(request: Request) -> bool:
    supplied = str(request.path_params.get("admin_key") or "")
    return bool(ADMIN_KEY) and hmac.compare_digest(supplied, ADMIN_KEY)


def public_base_url(request: Request) -> str:
    configured = os.environ.get("DIARY_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def web_actor(request: Request) -> str | None:
    return store.authenticate(str(request.path_params.get("access_key") or ""))


def web_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)


async def diary_page(request: Request):
    if web_actor(request) is None:
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


async def static_asset(request: Request):
    if web_actor(request) is None:
        return PlainTextResponse("Not found", status_code=404)
    name = str(request.path_params.get("name") or "")
    if name not in {"app.css", "app.js"}:
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(
        STATIC_DIR / name,
        headers={"Cache-Control": "no-store"},
    )


async def setup_page(request: Request):
    if not is_admin(request):
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(STATIC_DIR / "setup.html")


async def setup_asset(request: Request):
    if not is_admin(request):
        return PlainTextResponse("Not found", status_code=404)
    name = str(request.path_params.get("name") or "")
    if name not in {"setup.js", "app.css"}:
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(STATIC_DIR / name)


async def admin_participants(request: Request):
    if not is_admin(request):
        return web_error("not found", 404)
    if request.method == "GET":
        return JSONResponse({"ok": True, "participants": store.list_participants()})
    try:
        data = await request.json()
        participant = store.create_participant(
            str(data.get("display_name") or ""),
            str(data.get("kind") or "human"),  # type: ignore[arg-type]
        )
        access_key = store.issue_access_key(participant["id"])
        base = public_base_url(request)
        return JSONResponse(
            {
                "ok": True,
                "participant": participant,
                "access_key": access_key,
                "diary_url": f"{base}/diary/{access_key}/",
                "mcp_url": f"{base}/mcp/{access_key}/",
            }
        )
    except (json.JSONDecodeError, DiaryError) as exc:
        return web_error(str(exc))


async def admin_rotate_key(request: Request):
    if not is_admin(request):
        return web_error("not found", 404)
    try:
        participant_id = str(request.path_params.get("participant_id") or "")
        access_key = store.issue_access_key(participant_id)
        base = public_base_url(request)
        return JSONResponse(
            {
                "ok": True,
                "access_key": access_key,
                "diary_url": f"{base}/diary/{access_key}/",
                "mcp_url": f"{base}/mcp/{access_key}/",
            }
        )
    except DiaryError as exc:
        return web_error(str(exc), 404)


async def api_me(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    return JSONResponse(
        {
            "ok": True,
            "me": store.participant(actor),
            "participants": store.list_participants(),
            "notify_on_ai_write": store.notify_on_ai_write(actor),
            "timezone": store.timezone_name,
        }
    )


async def api_timeline(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    try:
        entries = store.list_timeline(
            actor,
            limit=int(request.query_params.get("limit", "50")),
            before=request.query_params.get("before"),
            after=request.query_params.get("after"),
        )
        return JSONResponse({"ok": True, "entries": entries})
    except (ValueError, DiaryError) as exc:
        return web_error(str(exc))


async def api_export(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    try:
        filename, content = build_backup_archive(store, actor)
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except DiaryError as exc:
        return web_error(str(exc))


async def api_write_entry(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    try:
        data = await request.json()
        entry = store.write_entry(
            actor,
            str(data.get("body") or ""),
            occurred_at=data.get("occurred_at"),
            visibility=str(data.get("visibility") or "shared"),  # type: ignore[arg-type]
            audience=data.get("audience") or (),
            challenge_question=data.get("challenge_question"),
            challenge_answers=data.get("challenge_answers") or (),
            challenge_hint=data.get("challenge_hint"),
            preview=data.get("preview"),
        )
        return JSONResponse({"ok": True, "entry": entry})
    except (json.JSONDecodeError, DiaryError) as exc:
        return web_error(str(exc))


async def api_entry(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    entry_id = str(request.path_params.get("entry_id") or "")
    try:
        if request.method == "PATCH":
            data = await request.json()
            entry = store.edit_entry(entry_id, actor, str(data.get("body") or ""))
            return JSONResponse({"ok": True, "entry": entry})
        store.delete_entry(entry_id, actor)
        return JSONResponse({"ok": True, "deleted": True, "entry_id": entry_id})
    except (json.JSONDecodeError, DiaryError) as exc:
        return web_error(str(exc), 403 if isinstance(exc, PermissionDenied) else 400)


async def api_entry_replies(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    entry_id = str(request.path_params.get("entry_id") or "")
    try:
        if request.method == "GET":
            return JSONResponse(
                {"ok": True, "replies": store.list_replies(entry_id, actor)}
            )
        data = await request.json()
        reply = store.reply_to_entry(entry_id, actor, str(data.get("body") or ""))
        return JSONResponse({"ok": True, "reply": reply})
    except (json.JSONDecodeError, DiaryError) as exc:
        return web_error(str(exc), 403 if isinstance(exc, PermissionDenied) else 400)


async def api_unlock(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    entry_id = str(request.path_params.get("entry_id") or "")
    try:
        data = await request.json()
        entry = store.read_entry(entry_id, actor, answer=str(data.get("answer") or ""))
        if not entry["locked"]:
            store.mark_entry_read(entry_id, actor)
            entry = store.read_entry(entry_id, actor)
        return JSONResponse({"ok": True, "unlocked": not entry["locked"], "entry": entry})
    except (json.JSONDecodeError, DiaryError) as exc:
        return web_error(str(exc), 403 if isinstance(exc, PermissionDenied) else 400)


async def api_mark_entry_read(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    entry_id = str(request.path_params.get("entry_id") or "")
    try:
        store.mark_entry_read(entry_id, actor)
        return JSONResponse({"ok": True})
    except DiaryError as exc:
        return web_error(str(exc), 403 if isinstance(exc, PermissionDenied) else 400)


async def api_notification_setting(request: Request):
    actor = web_actor(request)
    if actor is None:
        return web_error("not found", 404)
    try:
        data = await request.json()
        enabled = bool(data.get("enabled"))
        store.set_notify_on_ai_write(actor, enabled)
        return JSONResponse({"ok": True, "enabled": enabled})
    except (json.JSONDecodeError, DiaryError) as exc:
        return web_error(str(exc))


app = Starlette(
    routes=[
        Route("/", health),
        Route("/health", health),
        Route("/setup/{admin_key}/", setup_page),
        Route("/setup/{admin_key}/assets/{name}", setup_asset),
        Route(
            "/admin-api/{admin_key}/participants",
            admin_participants,
            methods=["GET", "POST"],
        ),
        Route(
            "/admin-api/{admin_key}/participants/{participant_id}/rotate-key",
            admin_rotate_key,
            methods=["POST"],
        ),
        Route("/diary/{access_key}/", diary_page),
        Route("/diary/{access_key}/assets/{name}", static_asset),
        Route("/api/{access_key}/me", api_me),
        Route("/api/{access_key}/timeline", api_timeline),
        Route("/api/{access_key}/export", api_export),
        Route("/api/{access_key}/entries", api_write_entry, methods=["POST"]),
        Route(
            "/api/{access_key}/entries/{entry_id}",
            api_entry,
            methods=["PATCH", "DELETE"],
        ),
        Route(
            "/api/{access_key}/entries/{entry_id}/replies",
            api_entry_replies,
            methods=["GET", "POST"],
        ),
        Route(
            "/api/{access_key}/entries/{entry_id}/unlock",
            api_unlock,
            methods=["POST"],
        ),
        Route(
            "/api/{access_key}/entries/{entry_id}/read",
            api_mark_entry_read,
            methods=["POST"],
        ),
        Route(
            "/api/{access_key}/settings/notify",
            api_notification_setting,
            methods=["POST"],
        ),
        Route("/mcp/{access_key}", mcp_endpoint, methods=["POST"]),
        Route("/mcp/{access_key}/", mcp_endpoint, methods=["POST"]),
    ],
)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "shared_diary.mcp_server:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
