"""
文档加载模块：支持 PDF / TXT / MD 三种格式的文字提取。
"""
import os

from pypdf import PdfReader

# 支持的文件后缀
SUPPORTED_EXTS = {".pdf", ".txt", ".md"}

# 纯文本文件的编码尝试顺序（覆盖常见中文文档）
TEXT_ENCODINGS = ("utf-8", "gbk", "latin-1")


def load_text(file_path: str) -> str:
    """
    读取文档并返回其中全部文本。

    :raises FileNotFoundError: 文件不存在
    :raises ValueError: 格式不支持 / 解码失败 / 内容为空
    """
    path = os.path.abspath(file_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"不支持的格式 {ext}，仅支持 {sorted(SUPPORTED_EXTS)}")

    if ext == ".pdf":
        return _load_pdf(path)
    return _load_text(path)


def _load_pdf(path: str) -> str:
    """使用 pypdf 提取每一页文本，页与页之间以空行分隔。"""
    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        # 注意：扫描版 PDF（内容为图片）无法提取文字
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


def _load_text(path: str) -> str:
    """txt/md 文本读取，自动依次尝试多种编码直到成功。"""
    for encoding in TEXT_ENCODINGS:
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"文件解码失败（已尝试 {TEXT_ENCODINGS}）: {path}")