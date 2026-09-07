"""
RAG 模块共享的数据结构定义。
"""
from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    """一条检索命中的文档块。"""

    text: str      # 块内容
    score: float   # 与查询的相似度（0~1，越大越相关）
    source: str    # 来源文档文件名