"""
RAG 服务：把【文档加载 → 切分 → 向量化 → 入库】与
【检索 → 相似度阈值过滤 → 上下文组装】组合成 Agent 可直接使用的接口。
"""
import os
import shutil
import uuid

import config
from rag.embedder import Embedder
from rag.loader import load_text
from rag.reranker import build_reranker
from rag.splitter import split_text
from rag.types import RetrievedChunk
from rag.vectorstore import VectorStore
from utils.logger import get_logger

logger = get_logger("rag.service")


class RAGService:
    """知识库核心业务：文档管理与检索。"""

    def __init__(self, llm=None):
        """
        :param llm: 可选的 LLM 客户端（agent.llm.LLMClient），用于查询改写。
                    对话场景必须注入；纯文档管理（upload/documents/delete）无需注入。
        """
        self.llm = llm
        self.embedder = Embedder()
        self.store = VectorStore()
        self.reranker = build_reranker()  # 默认 Identity（未启用重排）

    # ---------------- 文档管理 ----------------
    async def upload(self, file_path: str) -> int:
        """
        上传文档到知识库：读取文本 → 切分 → 向量化 → 入库。
        原始文件会复制到 data/documents 保留（重名自动加序号）。
        :return: 入库的分块数量
        """
        text = load_text(file_path)
        chunks = split_text(text)
        if not chunks:
            raise ValueError("文档内容为空，无法入库")

        source = self._copy_to_documents(file_path)  # 归集后的文件名，同时作为删除依据
        # 去重：同名文档重新入库前，先删除该 source 的旧分块（覆盖式更新，避免重复块）
        removed = self.store.delete_by_source(source)
        if removed:
            logger.info("检测到同名文档《%s》，已删除旧分块 %d 条后重新入库", source, removed)

        ids, metadatas = [], []
        for i, chunk in enumerate(chunks):
            ids.append(f"{source}::{i}::{uuid.uuid4().hex[:8]}")
            metadatas.append({"source": source, "chunk_index": i})

        embeddings = await self.embedder.embed_texts(chunks)
        self.store.add_chunks(chunks, ids, metadatas, embeddings)
        return len(chunks)

    def _copy_to_documents(self, src_path: str) -> str:
        """把上传的原始文档归集到 DOCUMENTS_DIR（同名直接覆盖，保持 source 一致以便去重），返回目标文件名。"""
        os.makedirs(config.DOCUMENTS_DIR, exist_ok=True)
        src = os.path.abspath(src_path)
        base = os.path.basename(src)
        dst = os.path.join(config.DOCUMENTS_DIR, base)
        if os.path.abspath(dst) != src:  # 文件已在归集目录内则跳过
            shutil.copy2(src, dst)  # 同名覆盖
        return base

    def list_sources(self) -> dict[str, int]:
        """返回 {源文档名: 分块数}。"""
        return self.store.get_stats()

    async def delete(self, source: str) -> int:
        """按源文档名删除其全部分块。"""
        return self.store.delete_by_source(source)

    # ---------------- 检索 ----------------
    async def retrieve(self, query: str, top_k: int | None = None) -> dict:
        """
        检索知识库并做相似度阈值过滤。

        流程：查询改写（可选）→ 多路向量检索 → 合并去重 → CrossEncoder 重排 → 阈值过滤。

        :return: {
            "found": bool,                   # 是否有达标结果
            "note": str,                     # 给 LLM 的说明（含"知识库信息不足"标记）
            "chunks": [RetrievedChunk, ...], # 达标块，已按重排分数降序
        }
        """
        top_k = top_k or config.TOP_K
        if self.store.count() == 0:
            return {"found": False, "note": "知识库为空：请先使用 upload 命令上传文档。", "chunks": []}

        # 1) 查询改写：把原问题拆成 2-3 个检索关键词，多路召回提升覆盖率
        queries = [query]
        if config.QUERY_REWRITE_ENABLED and self.llm is not None:
            variants = await self._rewrite_query(query)
            if variants:
                queries.extend(variants)
                logger.debug("查询改写：%s → %s", query, variants)

        # 2) 多路检索 + 合并去重（同一块取最高向量相似度分数）
        all_hits = await self._retrieve_multi(queries, top_k)

        # 3) CrossEncoder 重排（用原始用户问题打分，最能反映真实意图）
        reranked = await self.reranker.rerank(query, all_hits)
        hits = sorted(reranked, key=lambda h: h.score, reverse=True)

        # 4) 过滤低于相似度阈值的块 —— 没有任何块达标时视为"知识库信息不足"
        qualified = [h for h in hits if h.score >= config.RAG_SIM_THRESHOLD]
        if not qualified:
            return {
                "found": False,
                "note": (
                    f"知识库信息不足：未检索到相似度达到阈值 "
                    f"({config.RAG_SIM_THRESHOLD}) 的文档块。"
                ),
                "chunks": [],
            }
        return {"found": True, "note": f"知识库命中 {len(qualified)} 条相关文档块", "chunks": qualified}

    async def _rewrite_query(self, query: str) -> list[str]:
        """用 LLM 把用户问题改写为 2-3 个检索关键词，失败时返回空列表（回退原问题）。"""
        prompt = (
            "请把下面的用户问题改写为 2-3 个用于知识库检索的关键词或短语，"
            "每个关键词占一行。只输出关键词本身，不要编号、不要引号、不要任何解释。\n"
            f"用户问题：{query}"
        )
        try:
            msg = await self.llm.chat([{"role": "user", "content": prompt}])
            text = (msg.content or "").strip()
            seen, variants = set(), []
            for line in text.splitlines():
                v = line.strip().strip('"，,。.、；;')
                if v and v not in seen and v != query:
                    seen.add(v)
                    variants.append(v)
            return variants[:3]
        except Exception:
            logger.debug("查询改写失败，使用原问题", exc_info=True)
            return []

    async def _retrieve_multi(self, queries: list[str], top_k: int) -> list[RetrievedChunk]:
        """对多个查询分别做向量检索，按块文本合并去重（保留最高分）。"""
        merged: dict[str, RetrievedChunk] = {}
        for q in queries:
            for hit in await self._vector_search(q, top_k):
                if hit.text not in merged or hit.score > merged[hit.text].score:
                    merged[hit.text] = hit
        return list(merged.values())

    async def _vector_search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        """单次向量检索，返回带相似度分数的候选块（不做重排）。"""
        query_embedding = await self.embedder.embed_query(query)
        resp = self.store.query(query_embedding, top_k)

        docs = resp["documents"][0]
        metas = resp["metadatas"][0]
        dists = resp["distances"][0]  # cosine 距离，越小越相似
        return [
            RetrievedChunk(
                text=docs[i],
                score=round(1.0 - dists[i], 4),  # 相似度 = 1 - 距离
                source=(metas[i] or {}).get("source", "未知来源"),
            )
            for i in range(len(docs))
        ]

    def format_result(self, result: dict) -> str:
        """把检索结果组装成给 LLM 的上下文文本。"""
        lines = ["【知识库检索结果】", result["note"]]
        for i, chunk in enumerate(result["chunks"], 1):
            lines.append(
                f"{i}. [相似度 {chunk.score:.3f}] 来自《{chunk.source}》：\n{chunk.text[:600]}"
            )
        return "\n\n".join(lines)