"""
Agent 核心：意图判断 → 工具调用 → 多轮循环 → 二次汇总。

可用工具构成：
  - knowledge_search（内部工具，接 RAG 检索）
  - MCP 外部工具（由 MCPManager 动态注册，如 file_read / http_request）

决策规则见 config.SYSTEM_PROMPT：优先查知识库；知识库返回"信息不足"时，
再考虑调用 MCP 外部工具补充信息，最后基于工具结果汇总回答。
"""
import json

import config
from agent.llm import LLMClient
from agent.memory import Memory
from mcp_tools.client import MCPManager
from rag.service import RAGService
from utils.logger import get_logger

logger = get_logger("agent.core")


class Agent:
    """多轮 ReAct 式智能体：与 LLM 交互并调度知识库 / MCP 工具。"""

    def __init__(
        self,
        rag: RAGService,
        mcp: MCPManager,
        memory: Memory,
        llm: LLMClient | None = None,
    ):
        self.rag = rag
        self.mcp = mcp
        self.memory = memory
        self.llm = llm or LLMClient()

    # ---------------- 对外入口 ----------------
    async def ask(self, session_id: int, user_input: str, on_tool=None) -> str:
        """
        处理一轮用户提问，返回最终回答文本。
        流程：保存用户消息 → 拼装历史 + 工具列表 → 与 LLM 多轮循环
        （工具调用 → 回填结果 → 再问），直到 LLM 直接给出最终回答。

        :param on_tool: 可选回调 on_tool(name, args, result)，每次工具执行后触发，
                        供界面层展示调用轨迹；不传则无任何额外行为（CLI 不受影响）。
        """
        self.memory.save_message(session_id, "user", user_input)

        history = self.memory.load_history(session_id, config.MAX_HISTORY_TURNS)
        messages: list[dict] = [{"role": "system", "content": config.SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        tools = self._build_tools()
        traces: list[dict] = []  # 本轮工具调用轨迹，随最终回答入库供界面回放

        for _ in range(config.MAX_AGENT_ITERATIONS):  # 多轮上限，防死循环
            try:
                msg = await self.llm.chat(messages, tools=tools)
            except Exception as e:
                return f"抱歉，调用大模型失败：{type(e).__name__}: {e}"

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

            # 记录 assistant 的工具调用请求，随后逐个执行并回填
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_result = await self._dispatch_tool(name, args)
                # 打印工具调用轨迹，便于用户观察 Agent 的推理过程
                # （日志内容保持 ASCII，避免 Windows 终端 GBK 编码报错）
                logger.info("[tool] %s -> %s", name, str(tool_result)[:100])
                traces.append({"tool": name, "args": args, "result": tool_result[:500]})
                if on_tool:  # 通知界面层；回调自身异常不影响主流程
                    try:
                        on_tool(name, args, tool_result)
                    except Exception:
                        logger.debug("on_tool 回调异常（已忽略）", exc_info=True)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": tool_result}
                )

        return "已达到最大迭代轮数仍未得到结论，请尝试把问题表述得更具体一些。"

    # ---------------- 工具调度 ----------------
    def _build_tools(self) -> list[dict]:
        """内部工具（知识库检索）+ MCP 动态注册的工具。"""
        return [self._knowledge_search_tool(), *self.mcp.list_tools()]

    def _knowledge_search_tool(self) -> dict:
        """知识库检索工具的 OpenAI schema。"""
        return {
            "type": "function",
            "function": {
                "name": "knowledge_search",
                "description": (
                    "在本地私有知识库中检索与问题相关的文档内容。"
                    "回答前应优先调用本工具；若返回【知识库信息不足】，"
                    "可再考虑调用其它 MCP 外部工具补充信息。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "要检索的关键问题或关键词（越具体越好）",
                        }
                    },
                    "required": ["query"],
                },
            },
        }

    async def _dispatch_tool(self, name: str, arguments: dict) -> str:
        """按工具名路由执行：knowledge_search 走 RAG，其余走 MCP。"""
        if name == "knowledge_search":
            return await self._knowledge_search(arguments)
        # MCP 调用内部已做完整异常兜底，异常会以错误文本形式返回
        return await self.mcp.call_tool(name, arguments)

    async def _knowledge_search(self, args: dict) -> str:
        """执行知识库检索，把命中结果（或"信息不足"标记）返回给 LLM。"""
        query = (args.get("query") or "").strip()
        if not query:
            return "错误：knowledge_search 需要提供 query 参数。"
        try:
            result = await self.rag.retrieve(query)
        except Exception as e:
            return f"错误：知识库检索失败：{type(e).__name__}: {e}"
        if not result["found"]:
            return result["note"]  # 含【知识库信息不足】标记
        return self.rag.format_result(result)