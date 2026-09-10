# MyAgent 修改说明：新增 Streamlit Web 界面

> 本文档完整记录本次改动的目标、设计与全部代码修改，供评审与后续维护参考。
> 实施原则：**完全复用现有业务层**（`agent/` `rag/` `mcp/`），CLI 行为不变；对既有代码只做向后兼容的小扩展。

---

## 一、改动概述

新增 `web/` 模块，用 Streamlit 把 MyAgent 包装成可视化界面：

- **主区域**：聊天对话；Agent 每次调用的工具（知识库检索 / MCP 工具）以"工具调用轨迹"折叠面板展示。
- **侧边栏**：
  - 会话管理：新建会话、切换历史会话（自动加载该会话全部消息）；
  - 知识库管理：多文件上传入库（pdf/txt/md）、文档列表（含分块数）、逐个删除；
  - 工具一览：等同 CLI 的 `/tools`。

## 二、技术设计要点

1. **同步 Streamlit × 异步业务层**：`Agent.ask` / `RAGService.upload` / `MCPManager` 均基于 asyncio，而 Streamlit 是同步框架且每次交互重跑脚本。方案：新增 `AsyncBridge`——在 daemon 线程中维持一个 `run_forever` 的常驻事件循环，界面线程用 `asyncio.run_coroutine_threadsafe` 提交协程并同步等待结果。
2. **MCP 连接常驻**：MCP 的 stdio 子进程连接必须跨页面刷新存活。整个运行时（bridge + Memory + RAGService + MCPManager + Agent）用 `st.cache_resource` 缓存，进程内只初始化一次。
3. **工具轨迹落库**：新增可选回调 `Agent.ask(..., on_tool=...)` 供界面实时展示；同时把本轮工具轨迹以 JSON 存入 `messages.trace` 列，历史消息回放时也能看到调用过程。
4. **数据库为唯一事实来源**：聊天区每次从 `Memory.load_messages()` 渲染，交互完成后统一 `st.rerun()`。
5. **首启友好**：`config.py` 未填 API Key 时，页面显示引导信息而非堆栈崩溃（当前项目尚未配置 Key，这是首启必经场景）。

## 三、改动文件总览

| 文件 | 类型 | 说明 |
| ---- | ---- | ---- |
| `web/__init__.py` | 新增 | 包标识 |
| `web/bridge.py` | 新增 | `AsyncBridge`：后台线程常驻事件循环桥 |
| `web/app.py` | 新增 | Streamlit 界面入口 |
| `agent/core.py` | 修改 | `ask()` 增加 `on_tool` 回调与 trace 收集入库（向后兼容） |
| `agent/memory.py` | 修改 | `messages` 表增加 `trace` 列（含旧库迁移）、`save_message` 支持可选 trace、新增 `load_messages()` |
| `requirements.txt` | 修改 | 增加 `streamlit>=1.37.0` |
| `README.md` | 修改 | 特性表、架构图、目录树、启动方式补充 Web 界面 |

---

## 四、新增文件（完整代码）

### 4.1 `web/__init__.py`

```python
"""MyAgent Web 界面包（Streamlit）。"""
```

### 4.2 `web/bridge.py`

```python
"""
异步桥：后台线程常驻事件循环
============================
Streamlit 是同步框架，每次页面交互都会重新执行脚本；而 MyAgent 业务层基于
asyncio（Agent.ask / RAGService.upload / MCPManager），且 MCP 的 stdio
子进程连接必须跨页面刷新持续存活。

解决方案：在 daemon 线程中维持一个 run_forever 的事件循环——
  - 界面线程用 asyncio.run_coroutine_threadsafe 提交协程并同步等待结果；
  - 事件循环与其中建立的 MCP 连接常驻，不受页面刷新影响。
"""
import asyncio
import threading


class AsyncBridge:
    """把协程提交到后台常驻事件循环执行，并以同步方式等待结果。"""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="myagent-asyncio",
            daemon=True,  # 随 Streamlit 进程退出，无需显式 join
        )
        self._thread.start()

    def run(self, coro):
        """提交协程到后台事件循环，阻塞至完成并返回结果（异常原样抛出）。"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()
```

### 4.3 `web/app.py`

```python
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
```

---

## 五、既有文件修改明细

### 5.1 `agent/core.py` — `Agent.ask` 增加回调与轨迹入库

**修改点 1**：签名与 docstring（新增可选参数 `on_tool`，不传时行为与原来完全一致）：

```python
    async def ask(self, session_id: int, user_input: str, on_tool=None) -> str:
        """
        处理一轮用户提问，返回最终回答文本。
        流程：保存用户消息 → 拼装历史 + 工具列表 → 与 LLM 多轮循环
        （工具调用 → 回填结果 → 再问），直到 LLM 直接给出最终回答。

        :param on_tool: 可选回调 on_tool(name, args, result)，每次工具执行后触发，
                        供界面层展示调用轨迹；不传则无任何额外行为（CLI 不受影响）。
        """
```

**修改点 2**：主循环前初始化轨迹收集：

```python
        tools = self._build_tools()
        traces: list[dict] = []  # 本轮工具调用轨迹，随最终回答入库供界面回放
```

**修改点 3**：模型直接作答时把轨迹一并入库：

```python
            if not msg.tool_calls:
                # 模型决定直接作答：保存（含工具轨迹）并返回
                answer = (msg.content or "").strip() or "（模型未返回内容）"
                self.memory.save_message(
                    session_id,
                    "assistant",
                    answer,
                    trace=json.dumps(traces, ensure_ascii=False) if traces else None,
                )
                return answer
```

**修改点 4**：每次工具执行后记录轨迹并通知回调：

```python
                logger.info("[tool] %s -> %s", name, str(tool_result)[:100])
                traces.append({"tool": name, "args": args, "result": tool_result[:500]})
                if on_tool:  # 通知界面层；回调自身异常不影响主流程
                    try:
                        on_tool(name, args, tool_result)
                    except Exception:
                        logger.debug("on_tool 回调异常（已忽略）", exc_info=True)
```

### 5.2 `agent/memory.py` — trace 列与消息回放接口

**修改点 1**：头部补充 `import json`。

**修改点 2**：`_init_db` 中 `messages` 表增加可空列 `trace`，并兼容旧库迁移：

```python
                    content     TEXT    NOT NULL,
                    trace       TEXT,
                    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            # 兼容旧库：messages 表缺少 trace 列时补上（trace 存工具调用轨迹 JSON）
            cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
            if "trace" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN trace TEXT")
```

**修改点 3**：`save_message` 支持可选 trace：

```python
    def save_message(
        self, session_id: int, role: str, content: str, trace: str | None = None
    ):
        """保存一条消息（role: user / assistant）；trace 为可选的工具调用轨迹 JSON。"""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, trace) VALUES (?, ?, ?, ?)",
                (session_id, role, content, trace),
            )
```

**修改点 4**：类末尾新增 `load_messages`（`load_history` 保持不变，喂给 LLM 的消息结构不受影响）：

```python
    def load_messages(self, session_id: int) -> list[dict]:
        """
        读取会话全部消息（含工具调用轨迹），供界面回放展示。
        :return: 按时间正序的 [{"role", "content", "trace"}, ...]；
                 trace 为轨迹列表（解析失败或无轨迹时为 None）。
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT role, content, trace FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        result = []
        for r in rows:
            trace = None
            if r["trace"]:
                try:
                    trace = json.loads(r["trace"])
                except json.JSONDecodeError:
                    trace = None
            result.append({"role": r["role"], "content": r["content"], "trace": trace})
        return result
```

### 5.3 `requirements.txt` — 新增依赖

```text
# HTTP 工具底层请求库
httpx>=0.27.0

# Web 界面（streamlit run web/app.py）
streamlit>=1.37.0
```

### 5.4 `README.md` — 四处补充

1. **特性一览**表新增一行：

   ```markdown
   | 🖥️ Web 界面 | Streamlit 可视化界面（`streamlit run web/app.py`）：聊天 + 工具调用轨迹展示、知识库上传/删除、会话新建/切换 |
   ```

2. **架构图** 用户交互层节点新增 `WEB["Web 界面<br/>(web/app.py · Streamlit)"]`，并增加连线 `WEB --> CORE`。

3. **目录结构** 新增 `web/` 两个文件（`bridge.py` 异步桥 / `app.py` 界面入口）。

4. **启动方式** 增加「启动 Web 界面」小节：

   ````markdown
   ### 启动 Web 界面

   ```bash
   streamlit run web/app.py
   ```

   浏览器自动打开后：左侧管理会话与知识库（上传 / 删除文档、查看工具列表），
   主区域对话；Agent 每次调用的工具（知识库检索 / MCP 工具）会以"工具调用轨迹"
   折叠面板展示。会话与向量库同样持久化到 `data/`，与 CLI 完全共用。
   ````

---

## 六、启动与验证

1. 安装依赖并做语法自检：

   ```bash
   pip install -r requirements.txt
   python -m py_compile web/bridge.py web/app.py agent/core.py agent/memory.py
   ```

2. 启动界面（未配置 Key 时应看到引导提示而非崩溃）：

   ```bash
   streamlit run web/app.py
   ```

3. 配置 `config.py` 的 `LLM_API_KEY` 后手测完整链路：
   - 上传文档 → 主区域提问 → 回答含来源，"工具调用轨迹"面板显示 `knowledge_search` 调用；
   - 问知识库外问题 → 观察 Agent 降级调用 `builtin_tools_file_read` / `builtin_tools_http_request`；
   - ➕ 新建会话 → 切换历史会话 → 消息与轨迹回放正常；
   - 删除文档 → 列表即时更新；
   - 重启 Streamlit → 会话、向量库、MCP 连接均正常恢复。

## 七、已知边界（本次不改，留作后续）

- ~~同名文件重复上传仍会产生双份分块（内容 hash 去重待做）~~ → 已实现（见条目 12：上传同名文档自动覆盖旧分块）
- ~~会话标题仍为默认"新会话"，未做首轮提问自动命名~~ → 已实现（见条目 12：首轮对话后 LLM 自动生成标题）
- 工具结果在轨迹中截断为前 500 字符（避免消息表膨胀）。

---

## 八、问题记录与修复日志

> 按时间顺序记录每次「问题提出 → 现象 → 根因 → 修复」，便于追溯与持续维护。
> 规则：只要运行中发现/用户反馈问题并修复，就追加一条。

### 2026-09-07

**1. 本地 `mcp/` 目录与官方 mcp SDK 同名冲突**
- 现象：`from mcp import ClientSession` 报 `ImportError: cannot import name 'ClientSession'`
- 根因：本地工具包目录名 `mcp` 与第三方 SDK 包名 `mcp` 相同，Python 优先解析到本地同名包，SDK 无法导入
- 修复：本地目录改名 `mcp_tools/`；同步更新 `config.py`（`MCP_SERVERS_FILE`）、`servers.json`、`agent/core.py`、`main.py`、`web/app.py` 及 README / TEST_GUIDE 中的路径引用

**2. mcp 2.x 移除 FastMCP，API 大规模变更**
- 现象：`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`（提示 FastMCP 已改名为 MCPServer）
- 根因：`requirements.txt` 写 `mcp>=1.2.0`，pip 解析到当时最新的 mcp 2.x
- 修复：`requirements.txt` 锁定 `mcp>=1.2.0,<2` 并降级安装 1.x（2.x 属破坏性升级，本项目按 1.x 编写）

**3. DeepSeek 端点不支持 Embedding，文档无法入库**
- 现象：填好 Key 后 `upload` 失败，`/embeddings` 返回 404
- 根因：仅有 DeepSeek 对话服务（`api.deepseek.com`），其端点不提供 embedding 能力；原配置 EMBEDDING_* 留空自动复用 LLM 地址
- 修复：Embedding 默认切换为**本地模型**（`sentence-transformers` + `BAAI/bge-small-zh-v1.5`，首次自动下载、之后离线可用，无需额外 Key）：
  - `config.py` 新增 `EMBEDDING_PROVIDER = "local" | "api"` 开关、`EMBEDDING_MODEL`、`EMBEDDING_API_MODEL`；
  - `rag/embedder.py` 重写为双后端（`LocalEmbedder` / `ApiEmbedder`），对外接口不变（`Embedder.embed_texts / embed_query`）；
  - `requirements.txt` 增加 `sentence-transformers`；README 配置表同步

**4. Web 端提问一直"思考中"**
- 现象：在 Web 界面问"介绍一下你自己"，界面长时间停留在"思考中"
- 根因（两个叠加）：
  - a) MCP 工具命名违规：工具名形如 `builtin_tools::file_read`，含 `::`，不满足 OpenAI function.name 规范（仅允许 `^[a-zA-Z0-9_-]+$`），DeepSeek 返回 400 `invalid_request_error`，Agent 报"调用大模型失败"且该错误在 Web 端表现不直观；
  - b) 后台 streamlit 进程运行在受限环境，写 `C:\Users\...\.streamlit` 被拒（PermissionError），会话初始化消息无法下发
- 修复：
  - a) `mcp_tools/client.py`：工具展示名改为 `server_tool`（server 名做非法字符清洗），新增 `_tool_map`（展示名 → (server, tool)）供 LLM 回传时反查路由；`list_tools / tool_names / call_tool` 同步改造；
  - b) `agent/llm.py`：LLM 调用包 `asyncio.wait_for` 超时保护（新增 `config.LLM_TIMEOUT = 60`），请求挂起不再无限等待；
  - c) Web 服务启动参数调整：解除沙箱限制运行，并加 `--server.fileWatcherType none --browser.gatherUsageStats false` 降低日志噪音

**5.（操作记录）误删已入库文档如何恢复**
- 说明与结论：Web/CLI 的"删除"只删除 ChromaDB 中的向量块，**不会删除** `data/documents/` 下的原始文件；恢复方式 = 重新执行 `python main.py upload <文件>`（或 Web 端重新上传）。若原始文件也被删除且向量已清空，才需要重建文件内容

**6.（经验教训）同一消息内对同一文件的多个 Edit 会互相覆盖**
- 现象：一次消息内依次提交两个 Edit（补 `import re` + 改正则行），运行时报 `NameError: name 're' is not defined`
- 根因：并行工具调用基于同一份文件旧快照写回，后写覆盖先写
- 规避：对同一文件的修改改为逐个 Edit 串行提交，或改用 Write 全量重写后再验证

**7.（操作记录）git 初始化与 GitHub 发布准备**
- 交付 `.gitignore`（排除 `config.py`/`.venv/`/`data/`/`.trae/`/`.idea/`/`__pycache__/` 等）与 `config.example.py`（无密钥模板，供拉取者复制为 `config.py`）
- README 新增「发布到 GitHub」章节（上传/不上传清单、提交前检查、拉取者快速上手）
- 已 `git init` 并完成首次提交（commit `2e432e4`，30 个文件，2800 行），工作区干净；**尚未 push**（待用户在 GitHub 建远程仓库后 push）
- 注意：后续改动配置项时，需同步维护 `config.example.py` 与 README 配置表

**8.（已修复）用系统 Python 启动报 `ModuleNotFoundError: No module named 'openai'`**
- 现象：在项目目录执行 `python main.py chat` 报缺 openai（后续任何依赖都会同样报错）
- 根因：依赖只装在项目虚拟环境 `.venv` 内；用户当前 shell 用的是系统 Python（未激活虚拟环境）
- 修复（使用层面）：
  - 启动前先执行 `.venv\Scripts\activate`（推荐），或所有命令用 `.venv\Scripts\python.exe main.py ...` / `.venv\Scripts\python.exe -m streamlit run web/app.py` 代替；
  - `README.md`「安装步骤」新增激活提醒、「启动方式」与「启动 Web 界面」统一补充激活步骤
- 经验：任何使用入口（CLI / Web / 上传）都必须经由虚拟环境的 Python 启动

**9.（新增功能）历史对话记录支持删除单个会话**
- 需求：用户提出"历史对话记录不能提供删除吗"，确认删除粒度为【删除单个会话】
- 实现：
  - `agent/memory.py` 新增 `Memory.delete_session(session_id)`：删除会话及其全部消息，返回消息条数；会话不存在返回 `None`；
  - CLI：新增命令 `python main.py delete-session <id>`，对话内新增 `/delete-session <id>`；
  - Web：侧边栏会话列表下方新增「🗑️ 删除所选会话」按钮，删除后自动切换到最近会话或新建
- 验证：CLI 删除（返回消息数 2 → 列表移除 → 再次删除返回 None）、`main.py --help` 包含子命令、Web AppTest 渲染无异常（无 EXC/ERROR）
- 边界：按用户选择仅实现"删除单个会话"；"删除单条消息""清空全部历史"暂不实现

**10.（已修复）Web 端切换会话"第一次切换不成功"**
- 现象：切换对话下拉框时，第一次点击经常回弹/不生效，需要再点一次
- 根因：`st.selectbox` 未设置 `key`，且用 `index` 反推选中项；Streamlit 无 key 时用 index 与用户选择做一致性比较，值不同步时首次点击会被回弹到旧值
- 修复（`web/app.py`）：
  - 下拉框加 `key="session_selector"`，改为受控组件，自身状态驱动，切换立即生效；
  - 选择变化时 `st.session_state.session_id = chosen; st.rerun()` 一次点击立即切换主区；
  - 「新建会话」「删除会话」后 `st.session_state.pop("session_selector", None)` 重置下拉，避免旧选中项残留
- 验证：AppTest 模拟切换下拉 → 一次 run 即切到目标会话、无异常

**11.（已修复）文档与配置同步修正（用户反馈三项）**
- 1）`requirements.txt`：`sentence-transformers` 取消注释，从可选依赖移入正式依赖——默认 `EMBEDDING_PROVIDER=local` 后本地向量化已是必选能力（原注释误标为"Rerank 时才需要"）；`tiktoken` 仍保留可选
- 2）`docs/TEST_GUIDE.md`：工具名 `builtin_tools::file_read` / `builtin_tools::http_request` 改为下划线形式 `builtin_tools_file_read` / `builtin_tools_http_request`（与工具改名后的实际行为一致）
- 3）`README.md` 目录结构：`mcp/` → `mcp_tools/`，补充 `config.example.py`、`upload.md`；`utils/` 已在树中保留；另同步修正「添加外部 MCP Server」节的 `server名_工具名` 前缀说明，以及 `upload.md` 验证章节的工具名
- 说明：`upload.md` 问题日志第 4 条中保留 `builtin_tools::` 字样——那是当时 bug 的根因描述，属历史记录

**12.（新增功能）会话自动命名、上传去重、start.bat 启动脚本**
- 需求：1）首轮对话后 LLM 自动生成会话标题；2）上传同名文档前删除旧分块；3）Windows 一键启动脚本
- 实现：
  - 会话自动命名：`agent/memory.py` 新增 `rename_session()`；`agent/core.py` 在 `ask()` 首轮判定（保存用户消息前会话无历史）后调用 `_maybe_auto_title()`，用 LLM 按用户提问生成 ≤12 字标题（失败回退"新会话"），正常作答与迭代超限两个出口均触发；
  - 上传去重：`rag/service.py` 的 `upload()` 在入库前先 `delete_by_source(source)` 删除同名旧分块；`_copy_to_documents()` 由"重名加序号"改为"同名覆盖"，保证 source 文件名一致从而命中去重；
  - `start.bat`（新文件）：`chcp 65001` + `cd /d %~dp0`，自动用 `.venv` 内 Python 启动 CLI，缺虚拟环境时给出初始化提示
- 验证（HF_HUB_OFFLINE=1 离线加载本地模型）：
  - 同一文档连续 upload 两次 → 知识库统计仅 `{'测试知识库.md': 1}`，去重生效；
  - 新建会话 → 首轮提问"帮我查一下会议室怎么预订" → 标题自动变为"会议室预订方法"；
  - `start.bat` 为批处理脚本，双击运行（待用户在桌面验证）
- 备注：验证过程中发现首次模型加载会做 huggingface 联网校验较慢，属正常现象（已缓存后每次加载仍有少量 HEAD 请求）；未改动 embedding 加载逻辑

**13.（已修复）start.bat 双击乱码报错；本地模型加载被联网校验拖慢**
- 现象 1：双击 `start.bat` 弹 cmd 报一堆 `'xist' 不是内部或外部命令`、`'all' 不是内部或外部命令` 及乱码命令
- 根因 1：批处理文件最初以 UTF-8 写入，cmd 按系统 GBK 代码页逐字节解析 → 中文行乱码、`if/echo` 结构被拆散；改为 GBK 编码后仍乱（与控制台代码页有关）
- 修复 1：`start.bat` 重写为**纯 ASCII + CRLF**（0 个非 ASCII 字节，提示语用英文），任何代码页下稳定；已在本机 cmd 实测可进入 Python 启动流程
- 现象 2：启动 chat / upload 卡 1~2 分钟，日志显示 `huggingface.co ... HEAD ... [WinError 10060] 连接尝试失败` 反复重试
- 根因 2：`SentenceTransformer` 每次加载模型都会联网校验缓存元数据，网络差/无外网时 HEAD 重试（最多 5 次）拖慢启动
- 修复 2（`rag/embedder.py`）：`_new_local_model()` 加载时临时置 `HF_HUB_OFFLINE=1` 强制离线（缓存命中即秒级跳过联网），仅缓存缺失（首次下载）时恢复联网；实测模型加载 55s → 稳定 ~19s（剩余为 torch/模型冷启动固有开销，非网络问题）
- 经验：批处理文件避免用中文内容（编码坑多），或确保按目标机器代码页保存；本地模型环境应默认离线加载

**14.（新增功能）支持用户手动修改会话名称**
- 需求：用户提出"要允许用户手动修改会话名称"
- 实现：
  - `agent/memory.py`：新增 `get_session_title()`（配合已有 `rename_session()`）；
  - `agent/core.py`：`_maybe_auto_title()` 增加保护——仅当当前标题为默认"新会话"时才自动命名，**用户手动重命名过的标题不会被自动命名覆盖**；
  - Web（`web/app.py`）：会话管理区新增「会话新名称」输入框 +「✏️ 重命名当前会话」按钮；
  - CLI（`cli/chat.py`）：新增 `/rename <新名称>`（重命名当前会话）
- 验证：
  - 手动改标题"我的自定义标题"后经历首轮对话（自动命名逻辑触发）→ 标题保持自定义，未被覆盖；
  - 未命名会话首轮提问"帮我查一下报销流程" → 自动生成"报销流程查询"；
  - Web AppTest 渲染含「✏️ 重命名当前会话」按钮与输入框，无异常
- 交互规则：自动命名只在首轮且标题仍为默认值时生效；手动重命名任何时候都优先

**15.（已修复，随后随功能回退）重命名会话后下拉框名称不刷新** ⚠️ 随条目 14 一并回退
- 现象：手动重命名当前会话后，侧边栏下拉框仍显示旧名称，需刷新页面才更新
- 根因：`st.selectbox` 的 options 值是会话 ID（重命名前后 ID 不变），Streamlit 对相同 options 复用旧标签，不重算 format_func 输出
- 修复（`web/app.py`）：重命名成功分支追加 `st.session_state.pop("session_selector", None)` 再 `st.rerun()`——与新建/删除会话同款处理，强制下拉按新标题重建，立即显示新名称
- 验证：AppTest 交互链无异常；真实视觉效果待用户在浏览器确认

**16.（已修复）四项稳定性与安全修复**
- 1）`agent/core.py` — LLM 调用失败 / 达到最大迭代轮数时助手回复未入库，导致历史记录断裂
  - 根因：`ask()` 中 `except Exception` 分支和循环结束后的 `return` 直接返回文本，未调用 `save_message`
  - 修复：两个出口在 `return` 前先 `self.memory.save_message(session_id, "assistant", ...)` 并触发 `_maybe_auto_title`，与正常作答路径保持一致
- 2）`mcp_tools/embedded_server.py` — `file_read` 缺少敏感文件黑名单，可能泄露 API Key
  - 根因：仅做了目录白名单校验，未拦截白名单内的敏感文件（如 `config.py`、`.env`、`*.key`）
  - 修复：新增 `_SENSITIVE_FILENAMES`（`config.py`/`.env`/`credentials.json` 等）与 `_SENSITIVE_SUFFIXES`（`.key`/`.pem`/`.p12`/`.crt`/`.jks` 等），`_is_sensitive()` 大小写不敏感匹配；`file_read` 在读取前拦截并返回拒绝提示
- 3）`agent/memory.py` — SQLite 并发写入偶发 `database is locked`
  - 根因：`sqlite3.connect` 默认 `timeout=5.0` 且 journal_mode 为 `delete`，多连接写时锁等待不足
  - 修复：`_connect()` 改为 `sqlite3.connect(self.db_path, timeout=10)` 并执行 `PRAGMA journal_mode=WAL`，允许并发读、缩短写锁持有时间
- 4）`agent/core.py` — 自动命名长度校验阈值与 prompt 不一致
  - 根因：`_TITLE_PROMPT` 要求"不超过 12 个字"，但 `_maybe_auto_title` 用 `len(title) > 32` 做截断
  - 修复：改为 `len(title) > 12`，与 prompt 约束一致
- 验证：临时脚本 `_verify_fixes.py`（已删除）用 mock LLM 分别验证——LLM 异常消息入库、最大迭代消息入库、`config.py`/`*.key` 被 `file_read` 拒绝、`journal_mode=wal`、13 字标题回退"新会话"而 11 字标题被采用，全部通过

**17.（新增功能）RAG 模块增强：本地 CrossEncoder 重排 + 查询改写多路召回**
- 需求：1）用本地 CrossEncoder（BAAI/bge-reranker-base）替换空 HttpReranker 骨架，默认开启重排；2）检索前用 LLM 把问题改写为 2-3 个关键词，多路检索后合并去重提升召回率；3）config 增加开关
- 实现：
  - `rag/reranker.py`：重写为 `LocalCrossEncoderReranker`，复用 embedder.py 的离线优先加载策略（`local_files_only=True`，缓存缺失回退联网下载）；`rerank()` 改为 async，推理放 `asyncio.to_thread` 避免阻塞事件循环；CrossEncoder 输出的 logits 经 sigmoid 归一化到 (0,1)，与 `RAG_SIM_THRESHOLD` 口径一致；模型加载失败自动降级 `IdentityReranker`
  - `rag/service.py`：`RAGService.__init__(llm=None)` 新增可选 LLM 注入；`retrieve()` 流程改为「查询改写 → 多路向量检索 → 按文本合并去重（保留最高分）→ CrossEncoder 重排（用原始问题打分）→ 阈值过滤」；新增 `_rewrite_query()`（LLM 生成 2-3 关键词，逐行解析、清洗标点引号、异常回退空列表）、`_retrieve_multi()`、`_vector_search()`（原 `_retrieve_candidates` 拆分）
  - `config.py` / `config.example.py`：`RERANK_ENABLED` 默认 `True`；移除 `RERANK_API_KEY`/`RERANK_BASE_URL`（不再需要远程接口）；新增 `QUERY_REWRITE_ENABLED = True`
  - `main.py` `cmd_chat` / `web/app.py` `get_runtime`：创建 `LLMClient` 后注入 `RAGService(llm=llm)`，使查询改写在对话场景生效（纯文档管理命令不注入 LLM，自动回退单路检索）
- 验证（临时脚本 `_verify_rag.py`，已删除）：
  - 查询改写：mock LLM 返回 3 关键词 → 正确提取；含引号/逗号 → 清洗；LLM 异常 → 回退空列表
  - 多路合并：3 路各返回相同 2 块 → 去重后 2 块，保留最高相似度分数
  - CrossEncoder 重排：候选含"Python 是编程语言"/"Python 支持面向对象"/"香蕉是水果"，查询"Python 是什么" → 重排后 top1 为 Python 相关块（score=0.731），香蕉被排后；分数降序且在 (0,1]
  - `build_reranker()` 返回 `LocalCrossEncoderReranker`，模型 `BAAI/bge-reranker-base` 自动下载缓存成功
- 备注：重排分数为 sigmoid 后的 CrossEncoder 分数（非原始向量相似度），`RAG_SIM_THRESHOLD=0.30` 仍可作为低相关性过滤阈值；如需调整可单独调高

**18.（新增功能）FastAPI 后端服务层（RESTful + SSE）**
- 需求：新增 `api/` 目录，暴露 7 个 RESTful 接口（含 SSE 流式对话），复用现有 agent/rag/mcp_tools 业务层
- 实现：
  - `api/__init__.py` + `api/schemas.py`：Pydantic 模型（ChatRequest/ChatResponse/DocumentItem/SessionItem/ToolItem/UploadResponse）
  - `api/server.py`：FastAPI 应用，`lifespan` 启动时构建 Agent/RAG/MCP/Memory 单例，关闭时释放 MCP 连接；CORS 全开便于前端联调
    - `POST /api/chat`：`stream=true`（默认）返回 SSE 事件流（start/tool/answer/error/done），通过 `agent.ask(on_tool=...)` 回调把工具调用推入 `asyncio.Queue` 推送；`stream=false` 直接返回 `{session_id, answer}` JSON
    - `POST /api/documents/upload`：`UploadFile` 写入临时目录后复用 `rag.upload()` 入库
    - `GET /api/documents`、`DELETE /api/documents/{source}`：复用 `rag.list_sources()` / `rag.delete()`
    - `GET /api/sessions`、`DELETE /api/sessions/{session_id}`：复用 `memory.list_sessions()` / `memory.delete_session()`
    - `GET /api/tools`：复用 `agent._build_tools()` 返回知识库 + MCP 工具列表
  - `requirements.txt`：新增 `fastapi>=0.115.0`、`uvicorn>=0.30.0`
  - `README.md`：特性表新增 REST API 行、目录结构新增 `api/`、新增「启动 API 服务」章节（含接口一览表与 SSE 事件说明）、拉取者上手补充 API 启动命令
- 验证（启动 `uvicorn api.server:app`，临时脚本 `_verify_api.py` 已删除）：
  - 全部 7 个接口返回 200：tools（3 个工具含 knowledge_search）、documents 列表、sessions 列表、upload（1 分块）、chat 非流式（462 字回答）、chat SSE（事件序列 start→answer→done）、delete session（4 条消息）、delete document（1 分块）
  - 注意：本机 httpx 请求 localhost 被系统代理拦截返回 502，验证脚本用 `trust_env=False` 绕过；真实前端/浏览器不受此影响
- 备注：API 默认无鉴权，生产环境需自行增加 API Key / OAuth；会话与向量库与 CLI/Web 共用 `data/`

**19.（新增）FastAPI 接口单元测试**
- 需求：为 FastAPI 服务补充单元测试
- 实现：
  - `tests/conftest.py`：`mock_runtime` fixture 向 `api.server._runtime` 注入 MagicMock（rag/memory/agent/mcp），预设各方法返回值；`client` fixture 把 `app.router.lifespan_context` 替换为空操作，避免加载 embedding/cross-encoder 重型模型与连接 MCP，`TestClient` 走纯契约测试
  - `tests/test_api.py`：15 个用例覆盖全部 7 个接口
    - 工具：`GET /api/tools` 返回 3 个工具（含 knowledge_search）
    - 文档：上传成功、空文件名（FastAPI 框架层返回 422）、rag 异常返回 400、列表、删除成功、删除不存在返回 404
    - 会话：列表、删除成功、删除不存在返回 404
    - 对话：非流式（新建会话/已有会话）、SSE 流式（start→tool→answer→done 事件序列、断言 tool 事件内容）、SSE 异常（error 事件 + done）
  - `requirements.txt`：新增 `pytest>=8.0.0`
- 踩坑：
  1. `TestClient(app, lifespan="off")` 在当前 Starlette 版本不支持该参数 → 改为替换 `app.router.lifespan_context` 为空 asynccontextmanager
  2. `rag.delete` 用普通 `MagicMock` 返回 int，但服务端 `await` 它报 `TypeError: object int can't be used in 'await' expression` → 改为 `AsyncMock`
  3. 空文件名上传：原断言期望服务端 `if not file.filename` 返回 400，实际 FastAPI 在框架层把空文件名字段解析为字符串而非 UploadFile，返回 422 → 修正断言为 422
- 验证：`pytest tests/ -v` → 15 passed in 0.32s

---

## 九、继续维护约定

- 每次运行报错 / 用户反馈问题并修复后，在「八、问题记录与修复日志」新加一条（时间、现象、根因、修复）。
- 涉及配置项变化时，同步更新 `config.py` 注释、README 配置说明表与测试文档。
