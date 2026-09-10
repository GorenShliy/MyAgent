"""
MyAgent 全局配置【模板】config.example.py
==========================================
本文件是发布到 GitHub 供他人使用的【示例配置】，不含任何真实密钥。

使用方式（拉取源代码后）：
    1. 复制本文件为 config.py：  copy config.example.py config.py   （Windows）
                                  cp config.example.py config.py     （macOS / Linux）
    2. 打开 config.py，填入你自己的 LLM_API_KEY 等配置；
    3. 其余路径（data/ 等）无需手动创建，程序会自动生成。

注意：config.py 已被 .gitignore 排除，不会被 git 跟踪，请勿修改后强制提交。
新增或修改配置项时，请同步维护本模板与 README 的配置说明。
"""
import os

# ==========================================================
# 一、LLM（对话大模型）配置 —— 兼容所有 OpenAI 格式的 API
# ==========================================================
# 你的 API Key（必填，填入你自己的 Key；可使用任意 OpenAI 兼容服务）
LLM_API_KEY = ""

# API 服务地址（官方 https://api.openai.com/v1；也可指向中转或本地网关如 Ollama/vLLM）
LLM_BASE_URL = "https://api.openai.com/v1"

# 对话模型名称（按所选服务商填写，例如 gpt-4o-mini / deepseek-chat / qwen-plus）
LLM_MODEL = "gpt-4o-mini"

# ==========================================================
# 二、Embedding（向量化）配置
# ==========================================================
# 提供方式："local"（本地模型，默认，无需 API Key）/ "api"（OpenAI 兼容 text-embedding 接口）
EMBEDDING_PROVIDER = "local"

# 本地模式使用的模型名（中文友好，首次运行自动下载，之后离线可用）
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

# 本地模型缓存目录（None/空字符串 = 使用默认 huggingface 缓存）
EMBEDDING_CACHE_DIR = ""

# ---- 以下仅 API 模式（EMBEDDING_PROVIDER = "api"）使用 ----
# EMBEDDING_API_KEY / EMBEDDING_BASE_URL 两个变量互相独立，
# 均为空字符串("")时自动复用上面 LLM 的 API_KEY 与 BASE_URL。
EMBEDDING_API_KEY = ""
EMBEDDING_BASE_URL = ""
# API 模式下的接口模型名（例如 text-embedding-3-small）
EMBEDDING_API_MODEL = "text-embedding-3-small"


def get_embedding_settings() -> dict:
    """返回 API 模式下实际生效的 Embedding 配置：独立配置为空时回退到 LLM 配置。"""
    return {
        "api_key": EMBEDDING_API_KEY or LLM_API_KEY,
        "base_url": EMBEDDING_BASE_URL or LLM_BASE_URL,
        "model": EMBEDDING_API_MODEL,
    }


# ==========================================================
# 三、RAG 知识库配置
# ==========================================================
# 项目根目录（本文件所在目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 上传的原始文档归集目录（同时也是 file_read 工具的白名单目录之一）
DOCUMENTS_DIR = os.path.join(BASE_DIR, "data", "documents")

# ChromaDB 向量库持久化目录（本地单目录，无需外部数据库服务）
CHROMA_DIR = os.path.join(BASE_DIR, "data", "chroma_db")

# 向量集合名称
COLLECTION_NAME = "myagent_kb"

# 文本分块参数：CHUNK_SIZE 为每块最大字符数（当前按【字符】切分，见 rag/splitter.py），
# CHUNK_OVERLAP 为相邻块之间的重叠字符数（避免跨块上下文断裂）
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# 检索时最多召回多少个候选块（可被后续 Rerank 二次重排）
TOP_K = 5

# 相似度阈值（0~1，越大要求越严格）：检索结果中相似度低于该值的块会被过滤。
# 当没有任何块达到阈值时，RAG 返回"知识库信息不足"，提示 Agent 转向外部工具。
RAG_SIM_THRESHOLD = 0.30

# ---- Rerank 重排序（默认开启，本地 CrossEncoder）----
# 使用 sentence-transformers 的 CrossEncoder（模型 BAAI/bge-reranker-base）对
# 向量召回的候选块做二次精排；模型首次运行自动下载，之后离线可用。
RERANK_ENABLED = True
RERANK_MODEL = "BAAI/bge-reranker-base"

# ---- 查询改写（默认开启）----
# 检索前用 LLM 把用户问题改写为 2-3 个检索关键词，分别召回后合并去重，提升召回率。
QUERY_REWRITE_ENABLED = True

# ==========================================================
# 四、对话记忆（SQLite）配置
# ==========================================================
# SQLite 数据库文件路径（单文件持久化，重启不丢失）
DB_PATH = os.path.join(BASE_DIR, "data", "myagent.db")

# 每次请求携带的最大历史对话轮数（1 轮 = 用户问题 + 助手回答）
MAX_HISTORY_TURNS = 10

# ==========================================================
# 五、MCP 工具配置
# ==========================================================
# MCP Server 注册表文件路径（stdio / SSE 类型均在此配置）
# 注意：本地工具包名为 mcp_tools，避免与官方 MCP SDK（包名 mcp）冲突
MCP_SERVERS_FILE = os.path.join(BASE_DIR, "mcp_tools", "servers.json")

# 单个 MCP 工具调用 / HTTP 请求的超时时间（秒）
TOOL_TIMEOUT = 30

# 单次大模型对话调用的超时时间（秒），防止请求长时间挂起（Web 端会一直"思考中"）
LLM_TIMEOUT = 60

# Agent 多轮工具调用的最大迭代次数（防止死循环）
MAX_AGENT_ITERATIONS = 8

# ==========================================================
# 六、Agent 系统提示词：定义"先查知识库、不足再调外部工具"的决策规则
# ==========================================================
SYSTEM_PROMPT = (
    "你是 MyAgent，一个具备私有知识库检索和外部工具调用能力的智能助手。\n"
    "工作规则：\n"
    "1. 回答前优先调用 knowledge_search 检索私有知识库，基于检索内容回答，并在末尾注明参考来源。\n"
    "2. 当 knowledge_search 返回【知识库信息不足】时，先结合已有信息判断；若确实缺少关键数据，"
    "再决定是否调用 MCP 外部工具（如读取本地文件 file_read、请求外部接口 http_request）来补充信息。\n"
    "3. 工具返回结果后，请对结果进行二次汇总，用清晰、完整的中文回答用户。\n"
    "4. 已有足够信息时直接作答，不要无意义地调用工具。\n"
    "5. 回答简明扼要、条理清晰；不确定的内容要如实说明。"
)