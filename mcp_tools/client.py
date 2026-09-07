"""
MCP 客户端管理器：支持连接多个 MCP Server（stdio 本地进程 / SSE 远程），
统一发现工具、调用工具；单个 Server 启动失败或调用异常不影响整体运行。
"""
import asyncio
import json
import re
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

import config
from utils.logger import get_logger

logger = get_logger("mcp.client")

# OpenAI 规范要求 function.name 只允许 [a-zA-Z0-9_-]：用于清洗 server 名
_INVALID_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize_server(name: str) -> str:
    """把 server 名中的非法字符替换为下划线，保证生成的工具名合法。"""
    return _INVALID_NAME_RE.sub("_", name)


class MCPServerConn:
    """单个 MCP Server 的连接管理。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.name = cfg["name"]
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.tools: dict[str, object] = {}  # tool_name -> MCP 工具定义

    async def start(self):
        """建立连接并拉取工具列表；失败时向上抛出，由 MCPManager 统一兜底。"""
        self._stack = AsyncExitStack()
        server_type = self.cfg.get("type", "stdio")

        if server_type == "stdio":
            # command 为 "python" 时替换为当前解释器，避免多 Python 版本错乱
            command = self.cfg["command"]
            if command == "python":
                command = sys.executable
            params = StdioServerParameters(
                command=command,
                args=self.cfg.get("args", []),
                env=self.cfg.get("env"),
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        elif server_type == "sse":
            read, write = await self._stack.enter_async_context(
                sse_client(self.cfg["url"])
            )
        else:
            raise ValueError(
                f"未知 MCP Server 类型: {server_type}（仅支持 stdio / sse）"
            )

        self.session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        await self.session.initialize()
        result = await self.session.list_tools()
        self.tools = {t.name: t for t in result.tools}
        logger.info("MCP Server [%s] 已连接，工具数: %d", self.name, len(self.tools))

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用工具，把响应内容序列化为文本。"""
        result = await self.session.call_tool(tool_name, arguments or {})
        parts = []
        for item in result.content:
            if getattr(item, "type", "") == "text":
                parts.append(item.text)
            else:
                parts.append(str(item))
        return "\n".join(parts) if parts else "(无返回内容)"

    async def aclose(self):
        """关闭连接并退出子进程。"""
        if self._stack:
            await self._stack.aclose()


class MCPManager:
    """多 MCP Server 的统一管理入口。"""

    def __init__(self, servers_file: str | None = None):
        self.servers_file = servers_file or config.MCP_SERVERS_FILE
        self.conns: dict[str, MCPServerConn] = {}
        # 工具展示名 -> (server_name, tool_name)，用于把 LLM 传回的工具名反查路由
        self._tool_map: dict[str, tuple[str, str]] = {}

    async def start(self):
        """按 servers.json 启动全部启用的 Server；单个失败仅告警，不阻断整体。"""
        try:
            with open(self.servers_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(
                "MCP 配置加载失败（%s），本次没有可用外部工具。文件：%s",
                e, self.servers_file,
            )
            return

        for cfg in data.get("servers", []):
            if not cfg.get("enabled", True):
                continue
            conn = MCPServerConn(cfg)
            try:
                await conn.start()
            except Exception as e:
                # Server 启动失败不崩溃：记录并继续加载其它 Server
                logger.error("MCP Server [%s] 启动失败: %s", cfg.get("name"), e)
                await conn.aclose()
                continue
            self.conns[conn.name] = conn

        if self.conns:
            logger.info("MCP 工具就绪，可用 Server: [%s]", ", ".join(self.conns))
        else:
            logger.warning("没有可用的 MCP Server（仅剩知识库工具）")

    def list_tools(self) -> list[dict]:
        """
        返回全部可用 MCP 工具的 OpenAI tool 格式。

        命名约束：OpenAI 规范要求 function.name 只允许 [a-zA-Z0-9_-]，
        因此工具名采用 "{server名}_{工具名}"（server 名中非法字符替换为 _），
        并通过 _tool_map 记录「展示名 -> (server, tool)」用于调用时反查，
        避免不同 Server 的工具重名冲突。
        """
        for server_name, conn in sorted(self.conns.items()):
            for tool_name, tool in conn.tools.items():
                full_name = f"{_sanitize_server(server_name)}_{tool_name}"
                self._tool_map[full_name] = (server_name, tool_name)
        schemas = []
        for full_name, (server_name, tool_name) in sorted(self._tool_map.items()):
            tool = self.conns[server_name].tools[tool_name]
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": full_name,
                        "description": getattr(tool, "description", "") or "",
                        "parameters": getattr(
                            tool, "inputSchema", {"type": "object"}
                        ),
                    },
                }
            )
        return schemas

    def tool_names(self) -> list[str]:
        """返回形如 "server_tool" 的可用工具名列表（供人阅读）。"""
        return [s["function"]["name"] for s in self.list_tools()]

    async def call_tool(self, full_name: str, arguments: dict) -> str:
        """
        调用工具：full_name 形如 "server名_工具名"（由 list_tools 生成）。
        超时 / 连接 / 运行中的任何异常统一捕获，返回可读错误文本交给 LLM
        处理，保证 Agent 进程不崩溃。
        """
        mapping = self._tool_map.get(full_name)
        if mapping is None:
            return f"错误：工具 [{full_name}] 不在当前可用工具列表中（可用：{', '.join(self.tool_names()) or '无'}）"
        server_name, tool_name = mapping
        conn = self.conns.get(server_name)
        if conn is None:
            return f"错误：找不到 MCP Server [{server_name}]"

        try:
            return await asyncio.wait_for(
                conn.call_tool(tool_name, arguments), timeout=config.TOOL_TIMEOUT
            )
        except asyncio.TimeoutError:
            return (
                f"错误：工具 {full_name} 调用超时"
                f"（超过 {config.TOOL_TIMEOUT} 秒）。"
            )
        except Exception as e:
            logger.exception("工具调用异常 %s", full_name)
            return f"错误：工具 {full_name} 调用失败 - {type(e).__name__}: {e}"

    async def aclose(self):
        """关闭全部 Server 连接。"""
        for conn in self.conns.values():
            try:
                await conn.aclose()
            except Exception:
                pass
        self.conns.clear()