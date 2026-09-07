"""
统一日志工具
============
项目约定：所有"系统日志 / 调试输出"一律输出到 **stderr**，stdout 仅供用户可见的
正输出（CLI 的回答、列表等）。

这一点对 MCP 的 stdio Server 至关重要：stdout 承载 MCP 协议消息流，任何
print 到 stdout 都会破坏协议导致连接失败（见 mcp_tools/embedded_server.py）。
"""
import logging
import sys

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO, stream: object = None):
    """初始化根日志器，handler 默认绑定 stderr。"""
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    root.handlers = [handler]
    return root


def get_logger(name: str) -> logging.Logger:
    """获取带名称的子日志器。"""
    return logging.getLogger(name)