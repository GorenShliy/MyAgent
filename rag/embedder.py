"""
Embedding 封装：按 config.EMBEDDING_PROVIDER 选择实现：
  - "local"（默认）：sentence-transformers 本地模型，离线可用、无需 API Key
  - "api"：OpenAI 兼容 text-embedding 接口（EMBEDDING_* 配置；留空自动复用 LLM 配置）

对上层统一暴露 async 的 embed_texts / embed_query，RAGService 无需感知后端差异。
"""
import asyncio

import config

# 单次 encode 的最大文本条数（本地与 API 共用，防内存 / 请求体过大）
_BATCH_SIZE = 64


def _new_local_model():
    """构建本地 SentenceTransformer 模型（首次运行自动下载权重）。"""
    from sentence_transformers import SentenceTransformer

    kwargs = {}
    if config.EMBEDDING_CACHE_DIR:
        kwargs["cache_folder"] = config.EMBEDDING_CACHE_DIR
    return SentenceTransformer(config.EMBEDDING_MODEL, **kwargs)


class LocalEmbedder:
    """本地模型后端：推理在后台线程池执行，避免阻塞事件循环。"""

    def __init__(self):
        self._model = _new_local_model()

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vectors]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            out.extend(await asyncio.to_thread(self._encode, batch))
        return out

    async def embed_query(self, query: str) -> list[float]:
        return (await self.embed_texts([query]))[0]


class ApiEmbedder:
    """OpenAI 兼容接口后端。"""

    def __init__(self):
        settings = config.get_embedding_settings()
        if not settings["api_key"]:
            raise ValueError(
                "未配置 API Key：EMBEDDING_PROVIDER=api 需要在 config.py 中"
                "填写 LLM_API_KEY 或 EMBEDDING_API_KEY"
            )
        from openai import AsyncOpenAI

        self.model = settings["model"]
        self._client = AsyncOpenAI(
            api_key=settings["api_key"], base_url=settings["base_url"]
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            resp = await self._client.embeddings.create(
                model=self.model, input=batch
            )
            embeddings.extend(item.embedding for item in resp.data)
        return embeddings

    async def embed_query(self, query: str) -> list[float]:
        return (await self.embed_texts([query]))[0]


def create_embedder():
    """按 config.EMBEDDING_PROVIDER 构建后端。"""
    if config.EMBEDDING_PROVIDER == "api":
        return ApiEmbedder()
    return LocalEmbedder()


class Embedder:
    """统一入口：RAGService 只依赖本类，不感知后端差异。"""

    def __init__(self):
        self._impl = create_embedder()

    @property
    def backend(self) -> str:
        return config.EMBEDDING_PROVIDER

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return await self._impl.embed_texts(texts)

    async def embed_query(self, query: str) -> list[float]:
        return await self._impl.embed_query(query)