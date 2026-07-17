"""Hugging Face Spaces entrypoint with CPU Basic and ZeroGPU support."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shared_diary.mcp_server import app as diary_app  # noqa: E402


interface = None

try:
    import gradio as gr
    import spaces
except ImportError:
    # Normal local/PyPI installs do not need the Hugging Face presentation shell.
    app = diary_app
else:
    @spaces.GPU
    def zero_gpu_probe():
        """Register the hook required by ZeroGPU; diary work remains CPU-only."""
        return {"ok": True, "message": "ZeroGPU hook registered"}


    with gr.Blocks(title="共同日记") as interface:
        gr.Markdown("# 📖 共同日记")
        gr.Markdown("日记服务正在运行。请从管理员生成的专属链接进入日记或连接 MCP。")
        probe = gr.Button("ZeroGPU 启动检测（平时无需点击）")
        probe_result = gr.JSON(label="检测结果")
        probe.click(fn=zero_gpu_probe, outputs=probe_result)

    # ZeroGPU needs Gradio to remain the primary ASGI app. Put the protected
    # diary routes in front of Gradio's routes, but leave Gradio's home page at /.
    app = interface.app
    diary_routes = [
        route for route in diary_app.routes if getattr(route, "path", None) != "/"
    ]
    app.router.routes[0:0] = diary_routes

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "7860")),
        access_log=False,
    )
