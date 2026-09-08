"""
MyAgent 命令行入口。

用法：
  python main.py upload <file> [file2...]   上传文档到知识库
  python main.py documents                   查看知识库文档
  python main.py delete <source>             删除知识库文档
  python main.py sessions                    查看历史会话
  python main.py chat [--session <id>]       进入对话（--session 可恢复历史）
"""
import argparse
import asyncio

from agent.core import Agent
from agent.llm import LLMClient
from agent.memory import Memory
from cli.chat import run_chat
from mcp_tools.client import MCPManager
from rag.service import RAGService
from utils.logger import get_logger, setup_logging

logger = get_logger("main")


# ---------------- 子命令实现 ----------------
def cmd_upload(args):
    """上传文档入库。"""

    async def _run():
        rag = RAGService()
        for path in args.file:
            try:
                n = await rag.upload(path)
                print(f"[上传成功] {path} → 共 {n} 个分块")
            except Exception as e:
                print(f"[上传失败] {path}：{type(e).__name__}: {e}")

    asyncio.run(_run())


def cmd_documents(args):
    """查看知识库文档列表。"""

    async def _run():
        rag = RAGService()
        stats = rag.list_sources()
        if not stats:
            print("知识库为空，请先执行 upload 上传文档。")
            return
        print("知识库文档（源文档名 → 分块数）：")
        for src, cnt in sorted(stats.items()):
            print(f"  - {src}（{cnt} 块）")

    asyncio.run(_run())


def cmd_delete(args):
    """按源文档名删除。"""

    async def _run():
        rag = RAGService()
        deleted = await rag.delete(args.source)
        if deleted:
            print(f"已删除《{args.source}》的 {deleted} 个分块")
        else:
            print(f"未找到《{args.source}》")

    asyncio.run(_run())


def cmd_sessions(args):
    """查看历史会话。"""
    memory = Memory()
    rows = memory.list_sessions()
    if not rows:
        print("暂无历史会话。")
        return
    print("历史会话：")
    for r in rows:
        print(f"  ID={r['id']}  {r['title']}  ({r['created_at']})")


def cmd_delete_session(args):
    """删除指定历史会话（含其全部消息）。"""
    memory = Memory()
    deleted = memory.delete_session(args.id)
    if deleted is None:
        print(f"未找到会话 ID={args.id}")
    else:
        print(f"已删除会话 #{args.id}（含 {deleted} 条消息）")


def cmd_chat(args):
    """进入对话交互；指定 --session 可恢复该会话的历史。"""

    async def _run():
        memory = Memory()
        session_id = args.session or memory.create_session()
        mcp = MCPManager()
        await mcp.start()  # 单个 Server 启动失败不影响继续
        rag = RAGService()
        agent = Agent(rag=rag, mcp=mcp, memory=memory, llm=LLMClient())
        try:
            await run_chat(agent, session_id, memory)
        finally:
            await mcp.aclose()

    asyncio.run(_run())


# ---------------- 入口 ----------------
def main():
    setup_logging()  # 统一日志输出到 stderr

    parser = argparse.ArgumentParser(
        prog="myagent",
        description="MyAgent - RAG 私有知识库 + MCP 工具调用的智能 Agent",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_upload = sub.add_parser("upload", help="上传文档到知识库")
    p_upload.add_argument("file", nargs="+", help="一个或多个文档路径（pdf/txt/md）")
    p_upload.set_defaults(handler=cmd_upload)

    p_docs = sub.add_parser("documents", help="查看知识库中的文档")
    p_docs.set_defaults(handler=cmd_documents)

    p_del = sub.add_parser("delete", help="删除知识库中的文档")
    p_del.add_argument("source", help="源文档名（见 documents 输出）")
    p_del.set_defaults(handler=cmd_delete)

    p_ses = sub.add_parser("sessions", help="查看历史会话")
    p_ses.set_defaults(handler=cmd_sessions)

    p_delses = sub.add_parser("delete-session", help="删除历史会话（含其全部消息）")
    p_delses.add_argument("id", type=int, help="会话 ID（见 sessions 输出）")
    p_delses.set_defaults(handler=cmd_delete_session)

    p_chat = sub.add_parser("chat", help="进入对话交互")
    p_chat.add_argument("--session", type=int, default=None, help="恢复指定会话 ID 的历史")
    p_chat.set_defaults(handler=cmd_chat)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()