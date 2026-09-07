"""
MyAgent Streamlit Web 界面
==========================
启动方式（在项目根目录执行）：
    streamlit run web/app.py

与 CLI 共用同一套业务层（agent / rag / mcp），本文件只负责界面与交互：
  - 侧边栏：会话管理（新建/切换）、知识库管理（上传/删除）、工具一览
  - 主区域：聊天对话，展示工具调用轨迹与参考来源

【异步说明】Streamlit 是同步框架且每次交互重跑脚本，而业务层基于 asyncio
（Agent.ask / RAGService.upload），MCP 的 stdio 子进程连接还需要跨刷新常驻。
因此通过 web.bridge.AsyncBridge 在后台线程维持一个常驻事件循环，
界面线程把协程提交过去同步等待结果；整个运行时用 st.cache_resource 缓存，
保证 MCP 连接只建立一次。
"""
import json
import os
import sys
import tempfile

# Streamlit 以脚本方式运行本文件：手动把项目根目录加入 sys.path，
# 才能导入 config 与 agent/rag/mcp_tools 包（与 mcp_tools/embedded_server.py 同一做法）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from agent.core import Agent
from agent.llm import LLMClient
from agent.memory import Memory
from mcp_tools.client import MCPManager
from rag.service import RAGService
from web.bridge import AsyncBridge

st.set_page_config(page_title="MyAgent", page_icon="🤖", layout="centered")


# ---------------- 常驻运行时（整个进程仅初始化一次） ----------------
@st.cache_resource
def get_runtime():
    """构建事件循环桥 + MCP 连接 + Agent 等常驻对象。"""
    bridge = AsyncBridge()
    memory = Memory()
    rag = RAGService()
    mcp = MCPManager()
    bridge.run(mcp.start())  # 单个 Server 失败仅告警，不阻断（与 CLI 行为一致）
    llm = LLMClient()  # 未配置 API Key 时抛 ValueError，由调用方给出友好提示
    agent = Agent(rag=rag, mcp=mcp, memory=memory, llm=llm)
    return bridge, agent, rag, memory, mcp


try:
    bridge, agent, rag, memory, mcp = get_runtime()
except ValueError as e:  # LLM / Embedding 的 API Key 未配置
    st.error(f"初始化失败：{e}")
    st.info(
        "请打开项目根目录的 config.py，填写 LLM_API_KEY"
        "（及服务商对应的 BASE_URL / MODEL）后刷新页面。"
    )
    st.stop()


# ---------------- 侧边栏 ----------------
with st.sidebar:
    st.header("💬 会话管理")

    sessions = memory.list_sessions()
    if (
        "session_id" not in st.session_state
        or st.session_state.session_id not in [r["id"] for r in sessions]
    ):
        # 首次访问或原会话已不存在：取最近一个会话，没有则新建
        st.session_state.session_id = (
            sessions[0]["id"] if sessions else memory.create_session()
        )

    if st.button("➕ 新建会话", use_container_width=True):
        st.session_state.session_id = memory.create_session()
        st.rerun()

    sessions = memory.list_sessions()  # 新建后重取，保证列表包含当前会话
    ids = [r["id"] for r in sessions]
    labels = [f"#{r['id']} · {r['title']} · {r['created_at']}" for r in sessions]
    chosen = st.selectbox(
        "历史会话",
        range(len(labels)),
        index=ids.index(st.session_state.session_id),
        format_func=lambda i: labels[i],
        label_visibility="collapsed",
    )
    st.session_state.session_id = ids[chosen]
    session_id = st.session_state.session_id

    st.divider()
    st.header("📚 知识库管理")

    uploads = st.file_uploader(
        "上传文档（支持 pdf / txt / md，可多选）",
        type=["pdf", "txt", "md"],
        accept_multiple_files=True,
    )
    if uploads and st.button("⬆️ 入库所选文件", use_container_width=True):
        for up in uploads:
            # 先落盘到临时目录，再走 RAGService.upload
            # （它会把原始文件归集到 data/documents 并自动处理重名）
            suffix = os.path.splitext(up.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(up.getvalue())
                tmp_path = tmp.name
            try:
                n = bridge.run(rag.upload(tmp_path))
                st.success(f"《{up.name}》入库成功：{n} 个分块")
            except Exception as e:
                st.error(f"《{up.name}》入库失败：{type(e).__name__}: {e}")
            finally:
                os.unlink(tmp_path)

    stats = rag.list_sources()
    st.markdown("**已入库文档**")
    if not stats:
        st.caption("知识库为空，请先上传文档。")
    for src, cnt in sorted(stats.items()):
        col_doc, col_del = st.columns([4, 1])
        col_doc.write(f"📄 {src}（{cnt} 块）")
        if col_del.button("删除", key=f"del_{src}", use_container_width=True):
            bridge.run(rag.delete(src))
            st.rerun()

    st.divider()
    st.header("🔧 可用工具")
    tool_lines = ["knowledge_search（内部 · 知识库检索）", *mcp.tool_names()]
    st.markdown("\n".join(f"- {name}" for name in tool_lines))


# ---------------- 主聊天区 ----------------
st.title("🤖 MyAgent 智能助手")
st.caption(f"RAG 私有知识库 + MCP 工具调用 · 当前会话 #{session_id}")


def _render_trace(trace: list[dict]):
    """渲染一组工具调用轨迹（工具名 + 参数 + 结果摘要）。"""
    for t in trace:
        st.markdown(f"**🛠 {t['tool']}**")
        st.code(json.dumps(t["args"], ensure_ascii=False, indent=2), language="json")
        st.text(t["result"])


# 历史消息：以数据库为唯一事实来源，每次交互后统一重渲染
for msg in memory.load_messages(session_id):
    with st.chat_message("user" if msg["role"] == "user" else "assistant"):
        if msg["role"] == "assistant" and msg.get("trace"):
            with st.expander("🛠 工具调用轨迹", expanded=False):
                _render_trace(msg["trace"])
        st.markdown(msg["content"])

prompt = st.chat_input("输入你的问题…")
if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)

    traces: list[dict] = []

    def on_tool(name: str, args: dict, result: str):  # 由 Agent 在工具执行后回调
        traces.append({"tool": name, "args": args, "result": result[:500]})

    with st.chat_message("assistant"):
        with st.status("思考中…（先查知识库，信息不足时调用工具）", expanded=True) as status:
            try:
                answer = bridge.run(agent.ask(session_id, prompt, on_tool=on_tool))
            except Exception as e:
                answer = f"处理请求失败：{type(e).__name__}: {e}"
            _render_trace(traces)
            status.update(label="已完成", state="complete", expanded=False)
        st.markdown(answer)
    st.rerun()  # 新消息（含轨迹）已入库，统一重渲染
