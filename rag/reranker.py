"""
结果重排序（Rerank）预留接口 —— 【默认关闭】
=============================================
现阶段仅依靠"向量相似度检索"（TOP_K 个候选），本模块提供统一的重排扩展点。
开启方式（config.py）：
    RERANK_ENABLED = True，并填写 RERANK_API_KEY / RERANK_BASE_URL。

接入真实远程 Rerank API 后，在 HttpReranker.rerank 中把 (query, hits) 发给
服务的 rerank 接口，按返回分数对 hits 重排并更新 score 字段即可；
也可以改为加载本地模型（sentence-transformers 的 cross-encoder，
pip install sentence-transformers）。
"""
import logging

import config
from rag.types import RetrievedChunk

logger = logging.getLogger(__name__)


class BaseReranker:
    """重排器抽象接口。"""

    def rerank(self, query: str, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """输入查询与候选块，返回重排后的候选块。"""
        raise NotImplementedError


class IdentityReranker(BaseReranker):
    """默认实现：不改变候选顺序（即纯向量检索结果）。"""

    def rerank(self, query: str, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return hits


class HttpReranker(BaseReranker):
    """远程 Rerank API 实现骨架（预留，含配置参数）。"""

    def __init__(self):
        self.base_url = config.RERANK_BASE_URL or config.LLM_BASE_URL
        self.api_key = config.RERANK_API_KEY
        self.model = config.RERANK_MODEL

    def rerank(self, query: str, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
        # TODO(接入指引)：调用远程 rerank 接口（如 Cohere / Jina / 各厂商兼容端点），
        #   请求体一般形如 {"model": ..., "query": query, "documents": [文本列表]}，
        #   拿到每个文档的 relevance score 后按下表重排 hits 并更新 score：
        #   hits[i].score = response 中的对应分数
        #   return sorted(hits, key=lambda h: h.score, reverse=True)
        logger.warning("Rerank API 尚未接入，当前返回原顺序。")
        return hits


def build_reranker() -> BaseReranker:
    """按配置构建重排器：未启用或缺少 API Key 时使用默认（不改顺序）。"""
    if config.RERANK_ENABLED and config.RERANK_API_KEY:
        return HttpReranker()
    return IdentityReranker()