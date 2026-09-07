"""
LLM 客户端：OpenAI 兼容 chat 补全 + 工具调用（function calling）支持。
"""
import asyncio
from typing import Optional

from openai import AsyncOpenAI

import config


class LLMClient:
    """封装对话补全与工具调用。"""

    def __init__(self):
        if not config.LLM_API_KEY:
            raise ValueError("未配置 API Key：请在 config.py 中填写 LLM_API_KEY")
        self._client = AsyncOpenAI(
            api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL
        )

    async def chat(self, messages: list[dict], tools: Optional[list[dict]] = None):
        """
        调用 LLM（带超时保护，防止请求挂起导致 UI 一直"思考中"）。

        :param messages: OpenAI 消息数组
        :param tools: OpenAI 工具 schema 数组（可为空）
        :return: 响应中的 message 对象——用 .content 读最终文本、
                 .tool_calls 读工具调用列表（未调用工具时为空）
        :raises asyncio.TimeoutError: 超过 config.LLM_TIMEOUT 秒未返回
        """
        kwargs = {"model": config.LLM_MODEL, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = await asyncio.wait_for(
            self._client.chat.completions.create(**kwargs),
            timeout=config.LLM_TIMEOUT,
        )
        return resp.choices[0].message