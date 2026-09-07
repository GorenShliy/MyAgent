"""
文本分块模块：递归字符切分 + 重叠窗口
=====================================
【重要说明】当前实现是【按字符数】切分（RecursiveCharacterTextSplitter 的
长度计量单位是字符）。该方式对中英文通用、零额外依赖，但字符数与模型
token 数并不严格一致。

权衡（README.md 亦有说明）：
- 按字符切分：简单、无额外依赖；长英文单词/长代码片段可能导致单块 token 超限。
- 按 token 切分：块大小与模型上下文严格对齐，但需要 tiktoken 依赖且首次使用
  需联网下载 tokenizer 文件。

如需改为按 token 切分，先 `pip install tiktoken`，再把下方 split_text 替换为：
    from langchain_text_splitters import TokenTextSplitter

    splitter = TokenTextSplitter(
        chunk_size=config.CHUNK_SIZE,        # 此时单位为 token 数
        chunk_overlap=config.CHUNK_OVERLAP,
        model_name=config.LLM_MODEL,         # 按所选模型的 tokenizer 计量
    )
    return splitter.split_text(text)
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter

import config


def split_text(text: str) -> list[str]:
    """
    递归切分文本：优先在段落/句子边界断开，过长块再往细切。
    相邻块保留 CHUNK_OVERLAP 个字符的重叠窗口，避免跨块上下文断裂。
    """
    text = text.strip()
    if not text:
        return []

    # separators 按边界优先级从大到小排列，中文优先使用段落与句号切分
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        length_function=len,  # 按字符数计量
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )
    return splitter.split_text(text)