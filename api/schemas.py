"""API 请求 / 响应数据模型（Pydantic）。"""
from typing import Optional

from pydantic import BaseModel


# ---------------- 聊天 ----------------
class ChatRequest(BaseModel):
    """对话请求体。"""

    session_id: Optional[int] = None  # 不传则新建会话
    message: str
    stream: bool = True  # True=SSE 流式事件，False=直接返回最终答案 JSON


class ChatResponse(BaseModel):
    """非流式对话响应。"""

    session_id: int
    answer: str


# ---------------- 文档 ----------------
class DocumentItem(BaseModel):
    """知识库中的一条文档。"""

    source: str
    chunks: int


class UploadResponse(BaseModel):
    """文档上传响应。"""

    source: str
    chunks: int


# ---------------- 会话 ----------------
class SessionItem(BaseModel):
    """一条历史会话。"""

    id: int
    title: str
    created_at: str


# ---------------- 工具 ----------------
class ToolItem(BaseModel):
    """一个可用工具。"""

    name: str
    description: str
