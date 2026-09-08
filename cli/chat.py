"""
CLI 交互界面（异步 REPL）。

内置命令：
  /help   /upload <路径...>   /documents   /delete <源文档名>
  /sessions   /new   /tools   /exit
其余输入直接作为问题发送给 Agent。
"""
import asyncio

from agent.core import Agent
from agent.memory import Memory
from utils.logger import get_logger

logger = get_logger("cli.chat")

HELP_TEXT = """可用命令：
  /upload <文件路径>     上传文档到知识库（支持 pdf/txt/md，可一次多个）
  /documents             查看知识库中的文档及分块数
  /delete <源文档名>     从知识库删除某文档（源文档名见 /documents 输出）
  /sessions              查看历史会话
  /new                   开启新会话
  /tools                 查看当前可用的工具（知识库 + MCP）
  /exit 或 /quit         退出程序
  其它输入               作为问题直接发送给 Agent"""


async def run_chat(agent: Agent, session_id: int, memory: Memory):
    """对话主循环。"""
    print("=" * 58)
    print("  MyAgent - RAG 私有知识库 + MCP 工具智能助手")
    print(f"  当前会话 ID: {session_id}（输入 /help 查看帮助，/exit 退出）")
    print("=" * 58)

    while True:
        try:
            raw = await asyncio.to_thread(input, "\n你 > ")
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        text = raw.strip()
        if not text:
            continue

        if text in ("/exit", "/quit"):
            print("再见！")
            break
        if text == "/help":
            print(HELP_TEXT)
            continue
        if text == "/new":
            session_id = memory.create_session()
            print(f"已开启新会话，会话 ID: {session_id}")
            continue
        if text == "/documents":
            await _cmd_documents(agent)
            continue
        if text == "/tools":
            await _cmd_tools(agent)
            continue
        if text == "/sessions":
            _cmd_sessions(memory)
            continue
        if text.startswith("/delete-session"):
            parts = text.split()
            if len(parts) < 2:
                print("用法：/delete-session <会话ID>（ID 见 /sessions 输出）")
                continue
            try:
                target = int(parts[1])
            except ValueError:
                print("会话 ID 必须是数字。")
                continue
            deleted = memory.delete_session(target)
            if deleted is None:
                print(f"未找到会话 #{target}")
            else:
                print(f"已删除会话 #{target}（含 {deleted} 条消息）")
                if target == session_id:
                    print("提示：当前会话已被删除，输入 /new 开启新会话")
            continue
        if text.startswith("/upload"):
            await _cmd_upload(agent, text)
            continue
        if text.startswith("/delete"):
            await _cmd_delete(agent, text)
            continue
        if text.startswith("/"):
            print(f"未知命令：{text}（输入 /help 查看帮助）")
            continue

        # 正常提问：交给 Agent 处理
        answer = await agent.ask(session_id, text)
        print(f"\nAgent > {answer}")


# ---------------- 内置命令实现 ----------------
async def _cmd_upload(agent: Agent, text: str):
    paths = text.split()[1:]
    if not paths:
        print("用法：/upload <文件路径> [更多路径...]")
        return
    for p in paths:
        try:
            chunks = await agent.rag.upload(p)
            print(f"[上传成功] {p} → {chunks} 个分块")
            logger.info("uploaded %s (%d chunks)", p, chunks)
        except Exception as e:
            print(f"[上传失败] {p}：{type(e).__name__}: {e}")


async def _cmd_documents(agent: Agent):
    stats = agent.rag.list_sources()
    if not stats:
        print("知识库为空，请先用 /upload 或 upload 命令上传文档。")
        return
    print("知识库文档（源文档名 → 分块数）：")
    for src, cnt in sorted(stats.items()):
        print(f"  - {src}（{cnt} 块）")


async def _cmd_delete(agent: Agent, text: str):
    parts = text.split()
    if len(parts) < 2:
        print("用法：/delete <源文档名>")
        return
    source = parts[1]
    deleted = await agent.rag.delete(source)
    print(f"已删除《{source}》的 {deleted} 个分块" if deleted else f"未找到《{source}》")


def _cmd_sessions(memory: Memory):
    rows = memory.list_sessions()
    if not rows:
        print("暂无历史会话。")
        return
    print("历史会话：")
    for r in rows:
        print(f"  ID={r['id']}  {r['title']}  ({r['created_at']})")


async def _cmd_tools(agent: Agent):
    print("当前可用工具：")
    print("  - knowledge_search（知识库检索，Agent 内部工具）")
    for name in agent.mcp.tool_names():
        print(f"  - {name}（MCP 外部工具）")
    if not agent.mcp.tool_names():
        print("  （无可用 MCP 工具：请检查 mcp_tools/servers.json 配置与 Server 启动日志）")