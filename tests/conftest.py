"""测试夹具：构造 mock 运行时，复用 api.server 的 FastAPI 应用。

策略：把 app 的 lifespan 替换为空操作（避免加载 embedding/cross-encoder 重型模型），
直接向 api.server._runtime 注入 MagicMock，让接口层走纯契约测试。
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import api.server as server


def _build_mock_agent():
    """构造带默认行为的 mock Agent。"""
    agent = MagicMock()

    async def _ask(session_id, message, on_tool=None):
        if on_tool is not None:
            on_tool("knowledge_search", {"query": message}, "知识库检索结果")
        return "这是 mock 回答"

    agent.ask = AsyncMock(side_effect=_ask)
    agent._build_tools.return_value = [
        {"type": "function", "function": {"name": "knowledge_search", "description": "检索知识库"}},
        {"type": "function", "function": {"name": "builtin_tools_file_read", "description": "读取文件（白名单内）"}},
        {"type": "function", "function": {"name": "builtin_tools_http_request", "description": "发起 HTTP 请求"}},
    ]
    return agent


@pytest.fixture
def mock_runtime():
    """注入 mock 运行时到 api.server._runtime，返回 mock 对象供断言。"""
    rag = MagicMock()
    memory = MagicMock()
    agent = _build_mock_agent()
    mcp = MagicMock()

    # RAG 默认行为
    rag.list_sources.return_value = {"doc1.txt": 3, "doc2.md": 2}
    rag.delete = AsyncMock(return_value=3)
    rag.upload = AsyncMock(return_value=5)

    # Memory 默认行为（sqlite3.Row 支持 r["key"]，普通 dict 同样支持）
    memory.list_sessions.return_value = [
        {"id": 1, "title": "会话一", "created_at": "2026-01-01 10:00:00"},
        {"id": 2, "title": "会话二", "created_at": "2026-01-02 10:00:00"},
    ]
    memory.delete_session.return_value = 4
    memory.create_session.return_value = 100

    server._runtime.update(rag=rag, memory=memory, agent=agent, mcp=mcp)
    yield {"rag": rag, "memory": memory, "agent": agent, "mcp": mcp}
    server._runtime.clear()


# 空 lifespan：跳过真实初始化（加载模型、连接 MCP 等）
@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def client(mock_runtime):
    """返回带 mock 运行时的 TestClient（用空 lifespan 跳过真实初始化）。"""
    server.app.router.lifespan_context = _noop_lifespan
    with TestClient(server.app) as c:
        yield c

