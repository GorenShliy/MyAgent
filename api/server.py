"""
MyAgent FastAPI 后端服务
========================
复用现有 agent / rag / mcp_tools 业务层，暴露 RESTful + SSE 接口。

启动方式（已激活 .venv）：
    uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload

接口一览（启动后访问 /docs 查看交互式文档）：
    POST   /api/chat                  对话（SSE 流式事件 或 直接返回 JSON）
    POST   /api/documents/upload      上传文档入库（multipart/form-data，字段名 file）
    GET    /api/documents             列出知识库文档
    DELETE /api/documents/{source}    按源文档名删除
    GET    /api/sessions              列出历史会话
    DELETE /api/sessions/{id}         删除指定会话
    GET    /api/tools                 列出可用工具（知识库 + MCP）
"""
import asyncio
import json
import os
import shutil
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent.core import Agent
from agent.llm import LLMClient
from agent.memory import Memory
from api.schemas import (
    ChatRequest,
    ChatResponse,
    DocumentItem,
    SessionItem,
    ToolItem,
    UploadResponse,
)
from mcp_tools.client import MCPManager
from rag.service import RAGService
from utils.logger import get_logger

logger = get_logger("api.server")

# 进程内单例运行时（与 CLI / Web 共用同一套业务对象）
_runtime: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时构建 Agent/RAG/MCP，关闭时释放 MCP 连接。"""
    llm = LLMClient()
    rag = RAGService(llm=llm)
    memory = Memory()
    mcp = MCPManager()
    await mcp.start()  # 单个 Server 失败不影响整体
    agent = Agent(rag=rag, mcp=mcp, memory=memory, llm=llm)
    _runtime.update(llm=llm, rag=rag, memory=memory, mcp=mcp, agent=agent)
    logger.info("FastAPI 运行时初始化完成")
    try:
        yield
    finally:
        await mcp.aclose()
        logger.info("FastAPI 运行时已关闭")


app = FastAPI(title="MyAgent API", version="1.0.0", lifespan=lifespan)

# 允许跨域（便于前端联调；生产环境可收紧 origins）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _rag() -> RAGService:
    return _runtime["rag"]


def _memory() -> Memory:
    return _runtime["memory"]


def _agent() -> Agent:
    return _runtime["agent"]


# ---------------- 对话 ----------------
@app.post("/api/chat")
async def chat(req: ChatRequest):
    """
    对话接口。

    - stream=True（默认）：返回 SSE 事件流，事件类型：
        start       开始处理
        tool        工具调用（name / args / result）
        answer      最终回答文本（content）
        error       异常（message）
        done        流结束
    - stream=False：直接返回 {"session_id": ..., "answer": ...} JSON
    """
    memory = _memory()
    agent = _agent()
    session_id = req.session_id or memory.create_session()

    if not req.stream:
        answer = await agent.ask(session_id, req.message)
        return ChatResponse(session_id=session_id, answer=answer)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        def on_tool(name, args, result):
            """Agent 工具调用回调：把事件推入队列供 SSE 推送。"""
            queue.put_nowait(
                {
                    "type": "tool",
                    "name": name,
                    "args": args,
                    "result": str(result)[:500],
                }
            )

        async def run_ask():
            try:
                answer = await agent.ask(session_id, req.message, on_tool=on_tool)
                queue.put_nowait({"type": "answer", "content": answer})
            except Exception as e:
                logger.exception("对话处理异常")
                queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {e}"})
            finally:
                queue.put_nowait(None)  # 结束标记

        task = asyncio.create_task(run_ask())

        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id}, ensure_ascii=False)}\n\n"

        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        await task

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------- 文档 ----------------
@app.post("/api/documents/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)):
    """上传文档到知识库（支持 pdf/txt/md）。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    # 把上传内容写入临时文件（保留原始文件名），再复用 rag.upload 入库
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, file.filename)
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        chunks = await _rag().upload(tmp_path)
        return UploadResponse(source=file.filename, chunks=chunks)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/api/documents", response_model=list[DocumentItem])
async def list_documents():
    """列出知识库中的文档及分块数。"""
    stats = _rag().list_sources()
    return [DocumentItem(source=src, chunks=cnt) for src, cnt in sorted(stats.items())]


@app.delete("/api/documents/{source}")
async def delete_document(source: str):
    """按源文档名删除其全部分块。"""
    deleted = await _rag().delete(source)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"未找到文档《{source}》")
    return {"source": source, "deleted_chunks": deleted}


# ---------------- 会话 ----------------
@app.get("/api/sessions", response_model=list[SessionItem])
async def list_sessions():
    """列出全部历史会话（按创建时间倒序）。"""
    rows = _memory().list_sessions()
    return [
        SessionItem(id=r["id"], title=r["title"], created_at=r["created_at"])
        for r in rows
    ]


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: int):
    """删除指定会话及其全部消息。"""
    deleted = _memory().delete_session(session_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"未找到会话 ID={session_id}")
    return {"session_id": session_id, "deleted_messages": deleted}


# ---------------- 工具 ----------------
@app.get("/api/tools", response_model=list[ToolItem])
async def list_tools():
    """列出当前可用工具（知识库检索 + MCP 外部工具）。"""
    agent = _agent()
    tools = agent._build_tools()
    result = []
    for t in tools:
        fn = t.get("function", {})
        result.append(
            ToolItem(name=fn.get("name", ""), description=fn.get("description", ""))
        )
    return result
