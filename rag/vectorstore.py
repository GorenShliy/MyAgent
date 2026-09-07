"""
ChromaDB 向量库封装：入库 / 检索 / 按源文档删除 / 统计。
使用 cosine 空间 —— 距离越小越相似，相似度 = 1 - 距离。
"""
import chromadb

import config


class VectorStore:
    """ChromaDB 持久化向量库（本地单目录，无需外部数据库服务）。"""

    def __init__(self, persist_dir: str = None, collection_name: str = None):
        self._client = chromadb.PersistentClient(path=persist_dir or config.CHROMA_DIR)
        self._collection = self._client.get_or_create_collection(
            name=collection_name or config.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def collection(self):
        """暴露集合对象，供需要直接访问的场景使用。"""
        return self._collection

    def count(self) -> int:
        """知识库中的分块总数。"""
        return self._collection.count()

    def add_chunks(
        self,
        texts: list[str],
        ids: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]],
    ) -> None:
        """向量入库：texts / ids / metadatas / embeddings 长度必须一致。"""
        self._collection.add(
            ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings
        )

    def query(self, query_embedding: list[float], top_k: int) -> dict:
        """按相似度检索最相关的 top_k 个分块。

        :return: {"ids","documents","metadatas","distances"}，
                 distances 越小越相似（cosine 距离）
        """
        n = min(top_k, self.count())
        if n == 0:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        return self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )

    def get_stats(self) -> dict[str, int]:
        """按源文档统计分块数量（用于 documents 命令）。"""
        data = self._collection.get(include=["metadatas"])
        stats: dict[str, int] = {}
        for meta in data.get("metadatas") or []:
            src = meta.get("source", "未知来源")
            stats[src] = stats.get(src, 0) + 1
        return stats

    def delete_by_source(self, source: str) -> int:
        """删除指定源文档的全部分块，返回删除数量。"""
        before = self.count()
        self._collection.delete(where={"source": source})
        return before - self.count()