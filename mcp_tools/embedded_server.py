"""
内置 MCP Server（stdio 本地进程）
=================================
提供 2 个基础工具：
  - file_read(path)    ：读取本地文本文件（带完整路径穿越防护）
  - http_request(...)  ：发起 HTTP 接口请求（带完整异常兜底）

【协议要求（重要）】本进程通过 **stdout** 与 MCP 客户端通信，因此：
  - 禁止任何 print 输出到 stdout；
  - 所有日志 / 调试输出一律走 stderr（下方 logging 已绑定到 stderr）。
否则会污染 MCP 协议消息流，导致连接失败或调用错乱。
"""
import json
import logging
import os
import sys

# stderr 实时刷新，便于定位本 Server 的问题
sys.stderr.reconfigure(line_buffering=True)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[MCP-SERVER %(levelname)s] %(asctime)s %(message)s",
    force=True,
)
logger = logging.getLogger("embedded_server")

# 把项目根目录加入 sys.path，确保能 import 到 config（本文件以脚本方式运行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402  需要在调整 sys.path 之后导入
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("myagent-builtin-tools")

# ---------------- 文件读取：路径穿越防护 ----------------
# 允许读取的目录白名单：项目文档归集目录 + 当前工作目录
_ALLOWED_ROOTS = {
    os.path.abspath(config.DOCUMENTS_DIR),
    os.path.abspath(os.getcwd()),
}


def _resolve_allowed(rel_or_abs: str):
    """
    规范化路径并校验是否在白名单内。
    处理：~ 展开、相对路径转换、.. 折叠（normpath）、统一大小写（normcase，防绕过）。
    不合法时返回 None。
    """
    raw = os.path.abspath(os.path.expanduser(os.path.normpath(rel_or_abs)))
    norm_raw = os.path.normcase(raw)  # Windows 下忽略大小写差异
    for root in _ALLOWED_ROOTS:
        norm_root = os.path.normcase(root)
        # 必须位于白名单目录内部（含等于目录本身），杜绝 ../ 跳出
        if norm_raw == norm_root or norm_raw.startswith(norm_root + os.sep):
            return raw
    return None


# ---------------- 敏感文件黑名单：防止 API Key / 密钥泄露 ----------------
# 精确文件名匹配（不区分大小写）
_SENSITIVE_FILENAMES = {
    "config.py",
    "config.example.py",
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "service_account.json",
}
# 扩展名匹配（不区分大小写）
_SENSITIVE_SUFFIXES = (
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".jks",
    ".keystore",
    ".p8",
    ".der",
)


def _is_sensitive(path: str) -> bool:
    """按文件名 / 扩展名判断是否为敏感文件（大小写不敏感）。"""
    name = os.path.basename(path)
    lower_name = name.lower()
    if lower_name in _SENSITIVE_FILENAMES:
        return True
    return any(lower_name.endswith(suf) for suf in _SENSITIVE_SUFFIXES)


@mcp.tool()
def file_read(path: str) -> str:
    """
    读取本地文本文件内容。仅允许读取 data/documents 目录和项目工作目录内的文件；
    白名单之外的路径（含 ../ 穿越、绝对路径跳转）一律拒绝。
    """
    target = _resolve_allowed(path)
    if target is None:
        return "错误：路径不在允许访问的白名单内（仅可读取 data/documents 目录与项目工作目录中的文件）。"
    if not os.path.isfile(target):
        return f"错误：文件不存在或不是普通文件：{path}"
    if _is_sensitive(target):
        return f"错误：拒绝读取敏感文件（{os.path.basename(target)}），以防止 API Key 等机密信息泄露。"
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            with open(target, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    return f"错误：文件解码失败（已尝试 utf-8/gbk/latin-1）：{path}"


# ---------------- HTTP 请求工具 ----------------
@mcp.tool()
def http_request(
    url: str, method: str = "GET", params: str = "", body: str = "", headers: str = ""
) -> str:
    """
    发起 HTTP(S) 请求并返回响应内容。
    method 支持 GET/POST/PUT/DELETE/PATCH/HEAD；
    params / body / headers 均以 JSON 字符串形式传入（可为空字符串）。
    """
    import httpx

    method = method.strip().upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"):
        return f"错误：不支持的请求方法：{method}（支持 GET/POST/PUT/DELETE/PATCH/HEAD）"
    if not url or not url.startswith(("http://", "https://")):
        return f"错误：URL 无效：{url}"

    # 入参 JSON 解析错误直接返回文本，不让进程崩溃
    try:
        params_dict = json.loads(params) if params else None
        body_dict = json.loads(body) if body else None
        headers_dict = json.loads(headers) if headers else None
    except json.JSONDecodeError as e:
        return f"错误：params/body/headers 必须是合法 JSON 字符串，解析失败：{e}"

    try:
        timeout = httpx.Timeout(config.TOOL_TIMEOUT)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.request(
                method, url, params=params_dict, json=body_dict, headers=headers_dict
            )
            text = resp.text or ""
            return f"状态码:{resp.status_code}\n{text[:4000]}"
    except httpx.TimeoutException:
        return f"错误：请求超时（超过 {config.TOOL_TIMEOUT} 秒），请确认目标服务可达或稍后重试。"
    except httpx.ConnectError as e:
        return f"错误：无法连接目标服务器（网络不通 / SSL 错误 / 地址不存在）：{e}"
    except httpx.HTTPError as e:
        return f"错误：HTTP 请求异常：{type(e).__name__}: {e}"
    except Exception as e:  # 兜底：任何异常都以文本返回给 Agent，不让进程崩溃
        logger.exception("http_request 发生未预期异常")
        return f"错误：HTTP 请求失败 - {type(e).__name__}: {e}"


if __name__ == "__main__":
    logger.info("内置 MCP Server 启动（stdio 模式）")
    mcp.run()  # 默认 stdio 传输