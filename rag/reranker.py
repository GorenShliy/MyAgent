"""
结果重排序（Rerank）：本地 CrossEncoder 精排
============================================
默认开启（config.RERANK_ENABLED = True），使用 sentence-transformers 的
CrossEncoder（模型 BAAI/bge-reranker-base）对向量召回的候选块做二次打分，
按相关性降序返回。模型首次运行自动下载，之后离线可用（与 embedder.py 同策略）。

重排器统一接口为 async rerank(query, hits)，推理在后台线程执行避免阻塞事件循环。
若模型加载失败，自动降级为 IdentityReranker（不改顺序）。
"""
import asyncio
import math

import config
from rag.types import RetrievedChunk
from utils.logger import get_logger

logger = get_logger("rag.reranker")


class BaseReranker:
    """重排器抽象接口。"""

    async def rerank(self, query: str, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """输入查询与候选块，返回重排后的候选块。"""
        raise NotImplementedError


class IdentityReranker(BaseReranker):
    """降级实现：不改变候选顺序（即纯向量检索结果）。"""

    async def rerank(self, query: str, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return hits


class LocalCrossEncoderReranker(BaseReranker):
    """本地 CrossEncoder 重排：用 (query, chunk) 对打分，sigmoid 归一化到 0~1。"""

    def __init__(self):
        self._model = self._load_model()

    @staticmethod
    def _load_model():
        """离线优先加载 CrossEncoder：缓存命中则完全跳过联网校验。"""
        from sentence_transformers import CrossEncoder

        try:
            return CrossEncoder(config.RERANK_MODEL, local_files_only=True)
        except Exception:
            logger.info("重排模型缓存未找到，尝试联网下载 %s ...", config.RERANK_MODEL)
            return CrossEncoder(config.RERANK_MODEL)

    def _predict(self, pairs: list[list[str]]) -> list[float]:
        return self._model.predict(pairs)

    async def rerank(self, query: str, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not hits:
            return hits
        pairs = [[query, h.text] for h in hits]
        # CPU 推理放后台线程，避免阻塞事件循环
        scores = await asyncio.to_thread(self._predict, pairs)
        for h, s in zip(hits, scores):
            # CrossEncoder 输出为 logits，经 sigmoid 映射到 (0, 1)，与相似度阈值口径一致
            h.score = round(1.0 / (1.0 + math.exp(-float(s))), 4)
        return sorted(hits, key=lambda h: h.score, reverse=True)


def build_reranker() -> BaseReranker:
    """按配置构建重排器：未启用或模型加载失败时降级为 IdentityReranker。"""
    if not config.RERANK_ENABLED:
        return IdentityReranker()
    try:
        return LocalCrossEncoderReranker()
    except Exception as e:
        logger.warning("本地 CrossEncoder 加载失败（%s），降级为不重排", e)
        return IdentityReranker()
