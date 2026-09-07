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


class RAGService:
    """知识库核心业务：文档管理与检索。"""

    def __init__(self):
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
        ids, metadatas = [], []
        for i, chunk in enumerate(chunks):
            ids.append(f"{source}::{i}::{uuid.uuid4().hex[:8]}")
            metadatas.append({"source": source, "chunk_index": i})

        embeddings = await self.embedder.embed_texts(chunks)
        self.store.add_chunks(chunks, ids, metadatas, embeddings)
        return len(chunks)

    def _copy_to_documents(self, src_path: str) -> str:
        """把上传的原始文档归集到 DOCUMENTS_DIR（重名自动加序号），返回目标文件名。"""
        os.makedirs(config.DOCUMENTS_DIR, exist_ok=True)
        src = os.path.abspath(src_path)
        base = os.path.basename(src)
        dst = os.path.join(config.DOCUMENTS_DIR, base)
        if os.path.abspath(dst) == src:  # 文件已在归集目录内，无需复制
            return base
        if os.path.exists(dst):  # 重名：追加 _1/_2...
            name, ext = os.path.splitext(base)
            i = 1
            while os.path.exists(dst := os.path.join(config.DOCUMENTS_DIR, f"{name}_{i}{ext}")):
                i += 1
        shutil.copy2(src, dst)
        return os.path.basename(dst)

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

        :return: {
            "found": bool,                   # 是否有达标结果
            "note": str,                     # 给 LLM 的说明（含"知识库信息不足"标记）
            "chunks": [RetrievedChunk, ...], # 达标块，已按相似度降序
        }
        """
        top_k = top_k or config.TOP_K
        if self.store.count() == 0:
            return {"found": False, "note": "知识库为空：请先使用 upload 命令上传文档。", "chunks": []}

        hits = await self._retrieve_candidates(query, top_k)

        # 过滤低于相似度阈值的块 —— 没有任何块达标时视为"知识库信息不足"
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

    async def _retrieve_candidates(self, query: str, top_k: int) -> list[RetrievedChunk]:
        """召回候选块：向量检索 → 可选 Rerank → 按相似度降序。"""
        query_embedding = await self.embedder.embed_query(query)
        resp = self.store.query(query_embedding, top_k)

        docs = resp["documents"][0]
        metas = resp["metadatas"][0]
        dists = resp["distances"][0]  # cosine 距离，越小越相似
        hits = [
            RetrievedChunk(
                text=docs[i],
                score=round(1.0 - dists[i], 4),  # 相似度 = 1 - 距离
                source=(metas[i] or {}).get("source", "未知来源"),
            )
            for i in range(len(docs))
        ]
        reranked = self.reranker.rerank(query, hits)  # 预留重排扩展点
        return sorted(reranked, key=lambda h: h.score, reverse=True)

    def format_result(self, result: dict) -> str:
        """把检索结果组装成给 LLM 的上下文文本。"""
        lines = ["【知识库检索结果】", result["note"]]
        for i, chunk in enumerate(result["chunks"], 1):
            lines.append(
                f"{i}. [相似度 {chunk.score:.3f}] 来自《{chunk.source}》：\n{chunk.text[:600]}"
            )
        return "\n\n".join(lines)