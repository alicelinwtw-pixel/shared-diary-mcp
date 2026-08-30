from __future__ import annotations

import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


try:
    import starlette  # noqa: F401
except ImportError:
    # Protocol tests do not need an HTTP server. Supply the tiny import surface used
    # while constructing mcp_server.app so this suite can run without deployment deps.
    starlette = types.ModuleType("starlette")
    applications = types.ModuleType("starlette.applications")
    requests = types.ModuleType("starlette.requests")
    responses = types.ModuleType("starlette.responses")
    routing = types.ModuleType("starlette.routing")

    class _App:
        def __init__(self, routes=None, **kwargs):
            self.routes = routes or []

    class _Request:
        pass

    class _Response:
        pass

    class _Route:
        def __init__(self, path, endpoint, **kwargs):
            self.path = path
            self.endpoint = endpoint

    applications.Starlette = _App
    requests.Request = _Request
    responses.FileResponse = _Response
    responses.JSONResponse = _Response
    responses.PlainTextResponse = _Response
    responses.Response = _Response
    routing.Route = _Route
    sys.modules.update(
        {
            "starlette": starlette,
            "starlette.applications": applications,
            "starlette.requests": requests,
            "starlette.responses": responses,
            "starlette.routing": routing,
        }
    )

from shared_diary import mcp_server
from shared_diary.exporter import build_backup_archive
from shared_diary.store import DiaryStore
from shared_diary.tools import DiaryTools


class StatelessMCPProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_store = mcp_server.store
        self.original_tools = mcp_server.tools
        mcp_server.store = DiaryStore(Path(self.tempdir.name) / "diary.sqlite3")
        mcp_server.tools = DiaryTools(mcp_server.store)
        participant = mcp_server.store.create_participant("Aster", "ai")
        self.access_key = mcp_server.store.issue_access_key(participant["id"])
        self.actor = participant["id"]

    def tearDown(self) -> None:
        mcp_server.store = self.original_store
        mcp_server.tools = self.original_tools
        self.tempdir.cleanup()

    def post(self, request_id: int, method: str, params: dict | None = None):
        body = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        response = mcp_server.handle_mcp_message(self.actor, body)
        self.assertIsNotNone(response)
        return response

    def test_full_initialize_list_write_read_flow(self) -> None:
        initialized = self.post(
            1,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        )
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "Shared Diary")
        self.assertEqual(initialized["result"]["serverInfo"]["version"], "0.2.0")

        notification = mcp_server.handle_mcp_message(
            self.actor,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        self.assertIsNone(notification)

        listed = self.post(2, "tools/list")
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(len(names), 10)
        self.assertIn("write_entry", names)
        self.assertIn("read_recent", names)
        self.assertIn("edit_entry", names)
        self.assertIn("delete_entry", names)

        written = self.post(
            3,
            "tools/call",
            {
                "name": "write_entry",
                "arguments": {"body": "完整 MCP 流程测试", "visibility": "shared"},
            },
        )
        self.assertTrue(written["result"]["structuredContent"]["saved"])

        read_back = self.post(
            4,
            "tools/call",
            {"name": "read_recent", "arguments": {"days": 3}},
        )
        encoded = read_back["result"]["content"][0]["text"]
        self.assertIn("完整 MCP 流程测试", encoded)

        entry_id = written["result"]["structuredContent"]["entry"]["id"]
        edited = self.post(
            5,
            "tools/call",
            {
                "name": "edit_entry",
                "arguments": {"entry_id": entry_id, "body": "已经修改"},
            },
        )
        self.assertEqual(
            edited["result"]["structuredContent"]["entry"]["body"],
            "已经修改",
        )

        deleted = self.post(
            6,
            "tools/call",
            {"name": "delete_entry", "arguments": {"entry_id": entry_id}},
        )
        self.assertTrue(deleted["result"]["structuredContent"]["deleted"])

    def test_backup_archive_contains_json_and_markdown(self) -> None:
        entry = mcp_server.store.write_entry(
            self.actor,
            "备份里的正文",
            occurred_at="2026-07-17T03:21:00+08:00",
        )
        mcp_server.store.reply_to_entry(entry["id"], self.actor, "备份里的回应")

        filename, content = build_backup_archive(mcp_server.store, self.actor)
        self.assertTrue(filename.endswith(".zip"))
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 2)
            json_name = next(name for name in names if name.endswith(".json"))
            markdown_name = next(name for name in names if name.endswith(".md"))
            json_text = archive.read(json_name).decode("utf-8")
            markdown_text = archive.read(markdown_name).decode("utf-8")
        self.assertIn("备份里的正文", json_text)
        self.assertIn("备份里的回应", markdown_text)
        self.assertIn("2026-07-17", markdown_text)

if __name__ == "__main__":
    unittest.main()
