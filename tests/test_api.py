"""FastAPI 接口单元测试：覆盖 7 个接口的正常路径与错误路径。

全部走 mock 运行时（见 conftest.py），不加载真实模型，执行速度快、确定性强。
"""
import json


def _parse_sse(response_text: str) -> list[dict]:
    """把 SSE 响应文本解析为事件列表。"""
    events = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            payload = line[6:]
            if payload:
                events.append(json.loads(payload))
    return events


# ---------------- 工具 ----------------
def test_list_tools(client, mock_runtime):
    r = client.get("/api/tools")
    assert r.status_code == 200
    data = r.json()
    assert {t["name"] for t in data} == {
        "knowledge_search",
        "builtin_tools_file_read",
        "builtin_tools_http_request",
    }
    assert all("description" in t for t in data)
    mock_runtime["agent"]._build_tools.assert_called_once()


# ---------------- 文档 ----------------
def test_list_documents(client, mock_runtime):
    r = client.get("/api/documents")
    assert r.status_code == 200
    data = r.json()
    # sorted by source
    assert [d["source"] for d in data] == ["doc1.txt", "doc2.md"]
    assert {d["chunks"] for d in data} == {2, 3}
    mock_runtime["rag"].list_sources.assert_called_once()


def test_upload_document(client, mock_runtime):
    files = {"file": ("hello.txt", "测试内容".encode("utf-8"))}
    r = client.post("/api/documents/upload", files=files)
    assert r.status_code == 200
    data = r.json()
    assert data == {"source": "hello.txt", "chunks": 5}
    mock_runtime["rag"].upload.assert_called_once()


def test_upload_empty_filename(client, mock_runtime):
    # 空文件名时 FastAPI 在框架层把字段解析为字符串（非 UploadFile），返回 422
    files = {"file": ("", "内容".encode("utf-8"))}
    r = client.post("/api/documents/upload", files=files)
    assert r.status_code == 422
    mock_runtime["rag"].upload.assert_not_called()


def test_upload_rag_failure(client, mock_runtime):
    from unittest.mock import AsyncMock

    mock_runtime["rag"].upload = AsyncMock(side_effect=ValueError("不支持的格式"))
    files = {"file": ("bad.xyz", "内容".encode("utf-8"))}
    r = client.post("/api/documents/upload", files=files)
    assert r.status_code == 400
    assert "不支持的格式" in r.json()["detail"]


def test_delete_document(client, mock_runtime):
    r = client.delete("/api/documents/doc1.txt")
    assert r.status_code == 200
    assert r.json() == {"source": "doc1.txt", "deleted_chunks": 3}


def test_delete_document_not_found(client, mock_runtime):
    mock_runtime["rag"].delete.return_value = 0
    r = client.delete("/api/documents/nope.txt")
    assert r.status_code == 404
    assert "未找到文档" in r.json()["detail"]


# ---------------- 会话 ----------------
def test_list_sessions(client, mock_runtime):
    r = client.get("/api/sessions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["id"] == 1 and data[0]["title"] == "会话一"
    assert data[1]["id"] == 2


def test_delete_session(client, mock_runtime):
    r = client.delete("/api/sessions/1")
    assert r.status_code == 200
    assert r.json() == {"session_id": 1, "deleted_messages": 4}


def test_delete_session_not_found(client, mock_runtime):
    mock_runtime["memory"].delete_session.return_value = None
    r = client.delete("/api/sessions/999")
    assert r.status_code == 404
    assert "未找到会话" in r.json()["detail"]


# ---------------- 对话（非流式） ----------------
def test_chat_non_stream(client, mock_runtime):
    r = client.post("/api/chat", json={"message": "你好", "stream": False})
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == 100  # create_session 返回值
    assert data["answer"] == "这是 mock 回答"
    mock_runtime["memory"].create_session.assert_called_once()
    mock_runtime["agent"].ask.assert_called_once_with(100, "你好")


def test_chat_non_stream_with_existing_session(client, mock_runtime):
    r = client.post("/api/chat", json={"session_id": 7, "message": "hi", "stream": False})
    assert r.status_code == 200
    assert r.json()["session_id"] == 7
    mock_runtime["memory"].create_session.assert_not_called()
    mock_runtime["agent"].ask.assert_called_once_with(7, "hi")


# ---------------- 对话（SSE 流式） ----------------
def test_chat_stream(client, mock_runtime):
    with client.stream(
        "POST", "/api/chat", json={"message": "你好", "stream": True}
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.read().decode("utf-8")

    events = _parse_sse(body)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert events[0]["session_id"] == 100
    assert "tool" in types
    assert "answer" in types
    assert types[-1] == "done"

    # tool 事件内容
    tool_event = next(e for e in events if e["type"] == "tool")
    assert tool_event["name"] == "knowledge_search"
    # answer 事件内容
    answer_event = next(e for e in events if e["type"] == "answer")
    assert answer_event["content"] == "这是 mock 回答"


def test_chat_stream_with_existing_session(client, mock_runtime):
    with client.stream(
        "POST", "/api/chat", json={"session_id": 5, "message": "hi", "stream": True}
    ) as r:
        assert r.status_code == 200
        body = r.read().decode("utf-8")
    events = _parse_sse(body)
    assert events[0] == {"type": "start", "session_id": 5}
    mock_runtime["memory"].create_session.assert_not_called()


def test_chat_stream_error(client, mock_runtime):
    from unittest.mock import AsyncMock

    async def _boom(session_id, message, on_tool=None):
        raise RuntimeError("LLM 挂了")

    mock_runtime["agent"].ask = AsyncMock(side_effect=_boom)

    with client.stream(
        "POST", "/api/chat", json={"message": "x", "stream": True}
    ) as r:
        assert r.status_code == 200
        body = r.read().decode("utf-8")
    events = _parse_sse(body)
    types = [e["type"] for e in events]
    assert "error" in types
    err = next(e for e in events if e["type"] == "error")
    assert "RuntimeError" in err["message"]
    assert types[-1] == "done"
